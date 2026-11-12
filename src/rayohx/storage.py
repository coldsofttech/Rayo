"""Storage: how much space a download needs, and where finished segments wait for their turn.

Segments finish out of order but must be written in order. A *store* holds the ones that finished
early. The strategies differ in where the store lives:

* ``DIRECT`` and ``STREAMING``: memory, limited to a few segments past the one being written.
* ``BUFFERED``: memory, limited by bytes (``buffer_bytes``).
* ``TEMPORARY``: files in the working folder, so every finished segment survives a crash.
* ``HYBRID``: memory first, then files once the memory limit is reached.

In every case a segment is removed from the store as soon as it is written, so working storage
stays small instead of growing to the size of the stream.
"""

import errno
import os
import shutil
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rayohx.config import StorageConfig
from rayohx.enums import StorageStrategy
from rayohx.errors import ConfigurationError, InsufficientStorageError, OutputError
from rayohx.models import MediaPlaylist, SegmentData, StorageMetrics, Variant

#: How far past the segment being written the DIRECT and STREAMING strategies let workers run,
#: as a multiple of the number of workers.
WINDOW_FACTOR = 2
#: Disk spill limit of HYBRID when ``max_temp_bytes`` is not set.
DEFAULT_SPILL_BYTES = 1024 * 1024 * 1024
#: Allowance for container overhead on top of the stream's own bitrate.
ESTIMATE_MARGIN = 1.05
_HEADER = struct.Struct(">Q")


# --- strategy ----------------------------------------------------------------------------------


def choose_strategy(requested: StorageStrategy, *, sink_supports_resume: bool) -> StorageStrategy:
    """Resolve ``AUTO``: write to a resumable file directly, or stream if the sink cannot resume."""
    if requested is not StorageStrategy.AUTO:
        return requested
    return StorageStrategy.DIRECT if sink_supports_resume else StorageStrategy.STREAMING


def validate_strategy(strategy: StorageStrategy, *, sink_supports_resume: bool) -> None:
    """Reject a combination that cannot work.

    Raises:
        ConfigurationError: If the strategy needs a resumable file but the sink is a stream.
    """
    if strategy is StorageStrategy.DIRECT and not sink_supports_resume:
        raise ConfigurationError(
            "The DIRECT strategy writes a file, but this output is a stream.",
            hint="Use the STREAMING, BUFFERED, HYBRID or TEMPORARY strategy, or leave it on AUTO.",
        )


# --- how much space ----------------------------------------------------------------------------


class SizeEstimator:
    """Estimates the total download size without asking the server for every segment.

    It starts from the stream's bitrate and duration, then switches to the sizes actually seen,
    so the figure improves as the download goes on.
    """

    def __init__(self, durations: list[float], bits_per_second: int | None) -> None:
        self._total_duration = sum(durations)
        self._count = len(durations)
        self._initial_rate = bits_per_second / 8 if bits_per_second else None
        self._seen_bytes = 0
        self._seen_duration = 0.0
        self._seen_count = 0
        self._lock = threading.Lock()

    @classmethod
    def for_playlist(cls, playlist: MediaPlaylist, variant: Variant | None) -> "SizeEstimator":
        """Build an estimator from a playlist and, when known, the variant it belongs to."""
        rate = None
        if variant is not None:
            rate = variant.average_bandwidth or variant.bandwidth
        return cls([segment.duration for segment in playlist.segments], rate)

    @property
    def samples(self) -> int:
        """How many segments have been measured."""
        return self._seen_count

    def observe(self, duration: float, size: int) -> None:
        """Record the real size of a finished segment."""
        with self._lock:
            self._seen_bytes += size
            self._seen_duration += duration
            self._seen_count += 1

    def bytes_per_second(self) -> float | None:
        """The best known data rate."""
        with self._lock:
            if self._seen_duration > 0:
                return self._seen_bytes / self._seen_duration
            return self._initial_rate

    def total_bytes(self) -> int | None:
        """Estimated size of the whole download, or ``None`` if nothing is known yet."""
        rate = self.bytes_per_second()
        if rate is None:
            return None
        return int(rate * self._total_duration)

    def average_segment_bytes(self) -> int:
        """Typical size of one segment, or 0 if unknown."""
        with self._lock:
            if self._seen_count:
                return self._seen_bytes // self._seen_count
        total = self.total_bytes()
        return total // self._count if total and self._count else 0


@dataclass(frozen=True, slots=True)
class SpaceNeed:
    """Disk space a strategy needs, split by where it goes."""

    output_bytes: int
    workspace_bytes: int


def space_needed(
    strategy: StorageStrategy, estimate: int, config: StorageConfig, *, writes_file: bool = True
) -> SpaceNeed:
    """What ``strategy`` needs for a download of about ``estimate`` bytes.

    The old rule of "twice the output" is wrong for most strategies: a direct or streaming
    download needs the output only, and a hybrid one needs a bounded amount of working space.
    """
    estimate = int(estimate * ESTIMATE_MARGIN)
    output = estimate if writes_file else 0
    limit = config.max_temp_bytes
    if strategy is StorageStrategy.TEMPORARY:
        workspace = min(estimate, limit) if limit else estimate
    elif strategy is StorageStrategy.HYBRID:
        workspace = min(estimate, limit or DEFAULT_SPILL_BYTES)
    else:
        workspace = 0
    return SpaceNeed(output_bytes=output, workspace_bytes=workspace)


def nearest_existing(path: Path) -> Path:
    """The closest parent of ``path`` that exists, so free space can be read before creating it."""
    current = Path(path).absolute()
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def free_bytes(path: Path) -> int:
    """Free space on the disk holding ``path``."""
    return shutil.disk_usage(nearest_existing(path)).free


def same_volume(first: Path, second: Path) -> bool:
    """Whether two paths are on the same disk."""
    a, b = nearest_existing(first), nearest_existing(second)
    try:
        dev_a, dev_b = a.stat().st_dev, b.stat().st_dev
    except OSError:
        return a.anchor == b.anchor
    if dev_a and dev_b:  # Windows reports 0 on older Python versions
        return dev_a == dev_b
    return a.anchor == b.anchor


def check_disk_space(
    need: SpaceNeed,
    *,
    output_dir: Path,
    workspace_dir: Path,
    reserve_bytes: int,
    free: Callable[[Path], int] = free_bytes,
) -> None:
    """Make sure the disk(s) can hold ``need`` plus ``reserve_bytes``.

    When the output and the working folder share a disk their needs add up; otherwise each disk is
    checked on its own.

    Raises:
        InsufficientStorageError: If there is not enough space.
    """
    if same_volume(output_dir, workspace_dir):
        _require(need.output_bytes + need.workspace_bytes + reserve_bytes, output_dir, free)
        return
    if need.output_bytes:
        _require(need.output_bytes + reserve_bytes, output_dir, free)
    if need.workspace_bytes:
        _require(need.workspace_bytes + reserve_bytes, workspace_dir, free)


def _require(required: int, location: Path, free: Callable[[Path], int]) -> None:
    available = free(location)
    if required > available:
        raise InsufficientStorageError(required, available, location=str(location))


def translate_os_error(exc: OSError, what: str) -> Exception:
    """Turn a failed write into a clear error, recognising a full disk."""
    if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) == 112:
        return InsufficientStorageError(0, 0, location=what)
    return OutputError(f"Could not write {what}: {exc.strerror or exc}")


# --- metrics -----------------------------------------------------------------------------------


class StorageTracker:
    """Counts bytes moved and kept. Safe to update from many threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._memory = 0
        self._disk = 0
        self._peak_disk = 0
        self._downloaded = 0
        self._written = 0

    def downloaded(self, size: int) -> None:
        """Bytes that came off the network."""
        with self._lock:
            self._downloaded += size

    def written(self, size: int) -> None:
        """Bytes written to the output."""
        with self._lock:
            self._written += size

    def memory(self, delta: int) -> None:
        """Bytes added to (or removed from) the in-memory buffer."""
        with self._lock:
            self._memory += delta

    def disk(self, delta: int) -> None:
        """Bytes added to (or removed from) temporary files."""
        with self._lock:
            self._disk += delta
            self._peak_disk = max(self._peak_disk, self._disk)

    @property
    def memory_bytes(self) -> int:
        """Bytes currently buffered in memory."""
        with self._lock:
            return self._memory

    @property
    def disk_bytes(self) -> int:
        """Bytes currently in temporary files."""
        with self._lock:
            return self._disk

    def snapshot(self) -> StorageMetrics:
        """The current figures."""
        with self._lock:
            return StorageMetrics(
                output_bytes=self._written,
                temporary_bytes=self._disk,
                working_bytes=self._disk + self._memory,
                peak_temporary_bytes=self._peak_disk,
                downloaded_bytes=self._downloaded,
                written_bytes=self._written,
            )


# --- stores ------------------------------------------------------------------------------------


class SegmentStore(Protocol):
    """Holds segments that finished before their turn to be written."""

    def put(self, data: SegmentData) -> None: ...

    def has(self, index: int) -> bool: ...

    def take(self, index: int) -> SegmentData: ...

    def existing(self) -> set[int]:
        """Indexes already present before the download started (left by an earlier run)."""
        ...

    def clear(self) -> None: ...


class MemoryStore:
    """Keeps segments in memory."""

    def __init__(self, tracker: StorageTracker) -> None:
        self._items: dict[int, SegmentData] = {}
        self._tracker = tracker
        self._lock = threading.Lock()

    def put(self, data: SegmentData) -> None:
        with self._lock:
            self._items[data.index] = data
        self._tracker.memory(data.size)

    def has(self, index: int) -> bool:
        with self._lock:
            return index in self._items

    def take(self, index: int) -> SegmentData:
        with self._lock:
            data = self._items.pop(index)
        self._tracker.memory(-data.size)
        return data

    def existing(self) -> set[int]:
        return set()

    def clear(self) -> None:
        with self._lock:
            freed = sum(item.size for item in self._items.values())
            self._items.clear()
        self._tracker.memory(-freed)


class DiskStore:
    """Keeps segments as files in a folder. A file appears only once it is completely written."""

    def __init__(self, folder: Path, tracker: StorageTracker) -> None:
        self._folder = folder
        self._tracker = tracker
        self._sizes: dict[int, int] = {}
        self._lock = threading.Lock()
        self._folder.mkdir(parents=True, exist_ok=True)
        self._found = self._scan()

    def _path(self, index: int) -> Path:
        return self._folder / f"{index:08d}.seg"

    def _scan(self) -> set[int]:
        """Find complete files from an earlier run and count them as temporary storage."""
        found: set[int] = set()
        if not self._folder.is_dir():
            return found
        for path in self._folder.iterdir():
            if path.suffix == ".tmp":
                path.unlink(missing_ok=True)  # a write that never finished
            elif path.suffix == ".seg" and path.stem.isdigit() and self._valid(path):
                index = int(path.stem)
                size = path.stat().st_size
                self._sizes[index] = size
                self._tracker.disk(size)
                found.add(index)
            elif path.suffix == ".seg":
                path.unlink(missing_ok=True)
        return found

    @staticmethod
    def _valid(path: Path) -> bool:
        try:
            size = path.stat().st_size
            if size < _HEADER.size:
                return False
            with path.open("rb") as handle:
                init_length: int = _HEADER.unpack(handle.read(_HEADER.size))[0]
            return bool(_HEADER.size + init_length <= size)
        except OSError:
            return False

    def put(self, data: SegmentData) -> None:
        final = self._path(data.index)
        temporary = final.with_suffix(".tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(_HEADER.pack(len(data.init)))
                handle.write(data.init)
                handle.write(data.media)
            os.replace(temporary, final)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise translate_os_error(exc, str(final)) from exc
        size = _HEADER.size + data.size
        with self._lock:
            self._sizes[data.index] = size
        self._tracker.disk(size)

    def has(self, index: int) -> bool:
        with self._lock:
            return index in self._sizes

    def take(self, index: int) -> SegmentData:
        path = self._path(index)
        raw = path.read_bytes()
        init_length: int = _HEADER.unpack_from(raw)[0]
        start = _HEADER.size
        data = SegmentData(
            index=index,
            init=raw[start : start + init_length],
            media=raw[start + init_length :],
        )
        path.unlink(missing_ok=True)
        with self._lock:
            size = self._sizes.pop(index, 0)
        self._tracker.disk(-size)
        return data

    def existing(self) -> set[int]:
        return set(self._found)

    def clear(self) -> None:
        with self._lock:
            freed = sum(self._sizes.values())
            self._sizes.clear()
        shutil.rmtree(self._folder, ignore_errors=True)
        self._tracker.disk(-freed)


class SpillStore:
    """Memory first, and files once ``memory_limit`` bytes are held."""

    def __init__(self, disk: DiskStore, tracker: StorageTracker, memory_limit: int) -> None:
        self._memory = MemoryStore(tracker)
        self._disk = disk
        self._tracker = tracker
        self._limit = memory_limit

    def put(self, data: SegmentData) -> None:
        if self._tracker.memory_bytes + data.size <= self._limit:
            self._memory.put(data)
        else:
            self._disk.put(data)

    def has(self, index: int) -> bool:
        return self._memory.has(index) or self._disk.has(index)

    def take(self, index: int) -> SegmentData:
        return self._memory.take(index) if self._memory.has(index) else self._disk.take(index)

    def existing(self) -> set[int]:
        return self._disk.existing()

    def clear(self) -> None:
        self._memory.clear()
        self._disk.clear()


def make_store(
    strategy: StorageStrategy,
    config: StorageConfig,
    tracker: StorageTracker,
    segments_dir: Path,
) -> SegmentStore:
    """Build the store a strategy uses."""
    if strategy is StorageStrategy.TEMPORARY:
        return DiskStore(segments_dir, tracker)
    if strategy is StorageStrategy.HYBRID:
        return SpillStore(DiskStore(segments_dir, tracker), tracker, config.buffer_bytes)
    return MemoryStore(tracker)
