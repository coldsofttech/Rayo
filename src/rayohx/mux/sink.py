"""The output sink that remuxes into a fragmented MP4 file.

It receives a stream's segments in order (MPEG-TS or fragmented MP4) and writes one MP4. Other
tracks that were downloaded earlier, such as a separate audio rendition, are read from disk as the
muxer needs them, so they never have to fit in memory.
"""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from rayohx.enums import ContainerFormat
from rayohx.errors import OutputError, OutputExistsError
from rayohx.models import SegmentData
from rayohx.mux.fmp4_source import Fmp4Source
from rayohx.mux.muxer import Mp4Muxer
from rayohx.mux.source import TsSource
from rayohx.storage import translate_os_error

PART_SUFFIX = ".part"
CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class TrackFile:
    """A complete rendition saved in a file, to be read when the muxer needs it."""

    path: Path
    container: ContainerFormat
    language: str | None = None
    name: str | None = None


class _FileReader:
    """Reads a track file in chunks and feeds them to a source, as the muxer asks."""

    def __init__(self, item: TrackFile, muxer: Mp4Muxer) -> None:
        self._file: IO[bytes] | None = item.path.open("rb")
        self._source: TsSource | Fmp4Source = _make_source(
            item.container, muxer, item.language, item.name
        )
        self._muxer = muxer
        self._done = False

    def prime(self) -> None:
        """Read until every track of the file has been found, and hook them up to be pulled."""
        while not self._found_all() and self.pull():
            pass
        for track_id in self._source.track_ids:
            self._muxer.set_puller(track_id, self.pull)

    def _found_all(self) -> bool:
        expected = self._source.expected_tracks
        return expected is not None and len(self._source.track_ids) >= expected

    def pull(self) -> bool:
        """Feed the next chunk. Returns ``False`` once the file is used up."""
        if self._done or self._file is None:
            return False
        chunk = self._file.read(CHUNK)
        if not chunk:
            self._done = True
            self._source.finish()
            self.close()
            return False
        self._source.feed(chunk)
        return True

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _make_source(
    container: ContainerFormat, muxer: Mp4Muxer, language: str | None, name: str | None
) -> TsSource | Fmp4Source:
    if container is ContainerFormat.MPEG_TS:
        return TsSource(muxer, language=language, name=name)
    return Fmp4Source(muxer, language=language, name=name)


class MuxSink:
    """Writes the stream (plus any extra track files) as a fragmented MP4. It cannot resume."""

    supports_resume = False
    writes_file = True

    def __init__(
        self,
        path: Path | str,
        container: ContainerFormat,
        *,
        extras: Sequence[TrackFile] = (),
        overwrite: bool = False,
    ) -> None:
        self.target: Path | None = Path(path).absolute()
        self._container = container
        self._extras = tuple(extras)
        self._overwrite = overwrite
        self._handle: IO[bytes] | None = None
        self._muxer: Mp4Muxer | None = None
        self._primary: TsSource | Fmp4Source | None = None
        self._readers: list[_FileReader] = []
        self._length = 0
        self._holding = False

    @property
    def part_path(self) -> Path:
        """The file being written until the download completes."""
        assert self.target is not None  # noqa: S101 - set in __init__
        return self.target.with_name(self.target.name + PART_SUFFIX)

    @property
    def origin(self) -> float | None:
        """Seconds taken off every timestamp, once writing has started. Subtitles need it."""
        return self._muxer.origin if self._muxer is not None else None

    def preflight(self) -> None:
        target = self._target()
        if target.is_dir():
            raise OutputError(f"The output path {target} is a folder.", hint="Give a file name.")
        if target.exists() and not self._overwrite:
            raise OutputExistsError(
                f"The output file already exists: {target}",
                hint="Choose another name or allow overwriting.",
            )

    def open(self, *, resume_offset: int | None = None) -> int:
        self.preflight()
        target = self._target()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.part_path.open("wb")
        except OSError as exc:
            raise translate_os_error(exc, str(target)) from exc
        self._muxer = Mp4Muxer(self._write)
        self._muxer.hold()  # until the stream itself has said which tracks it has
        self._holding = True
        for item in self._extras:
            reader = _FileReader(item, self._muxer)
            self._readers.append(reader)
            reader.prime()
        self._primary = _make_source(self._container, self._muxer, None, None)
        return 0

    def write(self, data: SegmentData) -> int:
        assert self._muxer is not None  # noqa: S101 - opened before writing
        before = self._muxer.bytes_written
        if data.init:
            self._feed(data.init)
        self._feed(data.media)
        self._release_when_known()
        return self._muxer.bytes_written - before

    def _release_when_known(self) -> None:
        """Start writing once the stream has announced all of its tracks."""
        primary, muxer = self._primary, self._muxer
        if not self._holding or primary is None or muxer is None:
            return
        expected = primary.expected_tracks
        if expected is not None and len(primary.track_ids) >= expected:
            self._holding = False
            muxer.release()

    def checkpoint(self) -> int:
        if self._handle is not None:
            try:
                self._handle.flush()
                os.fsync(self._handle.fileno())
            except OSError as exc:
                raise translate_os_error(exc, str(self.target)) from exc
        return self._length

    def commit(self, validate: Callable[[Path], None] | None = None) -> Path:
        assert self._muxer is not None  # noqa: S101 - opened before committing
        assert self._primary is not None  # noqa: S101 - opened before committing
        self._primary.finish()
        self._holding = False
        self._muxer.close()
        self.checkpoint()
        self._close()
        if validate is not None:
            validate(self.part_path)
        target = self._target()
        if target.exists() and not self._overwrite:
            raise OutputExistsError(f"The output file appeared while downloading: {target}")
        try:
            os.replace(self.part_path, target)
        except OSError as exc:
            raise translate_os_error(exc, str(target)) from exc
        return target

    def abort(self, *, keep_partial: bool) -> None:
        self._close()
        self.part_path.unlink(missing_ok=True)

    # --- internals -------------------------------------------------------------------------

    def _feed(self, data: bytes) -> None:
        assert self._primary is not None  # noqa: S101 - opened before writing
        try:
            self._primary.feed(data)
        except OSError as exc:
            raise translate_os_error(exc, str(self.target)) from exc

    def _write(self, data: bytes) -> int:
        assert self._handle is not None  # noqa: S101 - opened before writing
        try:
            written = self._handle.write(data)
        except OSError as exc:
            raise translate_os_error(exc, str(self.target)) from exc
        self._length += len(data)
        return written

    def _target(self) -> Path:
        assert self.target is not None  # noqa: S101 - set in __init__
        return self.target

    def _close(self) -> None:
        for reader in self._readers:
            reader.close()
        if self._handle is not None:
            self._handle.close()
            self._handle = None
