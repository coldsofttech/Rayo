"""Putting the tracks' fragments into one file, in time order.

Every track hands the muxer finished fragments. The muxer holds them until it can be sure no
earlier fragment is still coming from another track, writes the initialization section the first
time it writes anything, then the fragments oldest first. Memory stays small because a fragment
is written as soon as every other track has a later one waiting (or has ended).

Tracks may be *lazy*: the muxer asks a function for their next fragment only when it needs it.
That lets a separately downloaded audio track wait on disk until the video catches up.
"""

import logging
import struct
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from rayohx.errors import FinalizationError
from rayohx.mux.fmp4 import RawTrack, build_init_ordered
from rayohx.mux.model import TrackConfig

logger = logging.getLogger(__name__)

#: Waiting fragments beyond this many bytes force a write even if a track is silent.
MAX_PENDING_BYTES = 64 * 1024 * 1024


@dataclass(slots=True)
class _Track:
    track_id: int
    timescale: int
    config: TrackConfig | None = None
    raw: RawTrack | None = None
    queue: deque[tuple[float, bytes]] = field(default_factory=deque)
    ended: bool = False
    puller: Callable[[], bool] | None = None


class Mp4Muxer:
    """Interleaves fragments of several tracks into one fragmented MP4 stream."""

    def __init__(self, write: Callable[[bytes], object]) -> None:
        self._write = write
        self._tracks: dict[int, _Track] = {}
        self._next_id = 1
        self._expected = 0
        self._holds = 0
        self._draining = False
        self._started = False
        self._sequence = 0
        self._pending_bytes = 0
        #: Seconds subtracted from every timestamp so the file starts near zero.
        self.origin: float | None = None
        self.bytes_written = 0

    # --- setting up ------------------------------------------------------------------------

    def expect(self, count: int) -> None:
        """Announce that ``count`` more tracks will be added, so writing waits for them."""
        self._expected += count

    def hold(self) -> None:
        """Do not start writing until ``release`` is called, because more tracks may be coming."""
        self._holds += 1

    def release(self) -> None:
        """Undo one ``hold``; writing can start once no hold is left."""
        self._holds = max(0, self._holds - 1)
        self._drain()

    def add_track(self, config: TrackConfig) -> int:
        """Add a track whose samples are written by this program; returns its track id."""
        track = self._new_track(config.timescale)
        track.config = config
        return track.track_id

    def add_raw_track(self, make: Callable[[int], RawTrack], timescale: int) -> int:
        """Add a track copied from another MP4. ``make(track_id)`` builds its patched boxes."""
        track = self._new_track(timescale)
        track.raw = make(track.track_id)
        return track.track_id

    def set_puller(self, track_id: int, puller: Callable[[], bool]) -> None:
        """Make a track lazy. ``puller`` adds the track's next fragment (or ends the track) and
        returns whether it did anything."""
        self._tracks[track_id].puller = puller

    def _new_track(self, timescale: int) -> _Track:
        if self._started:
            raise FinalizationError(
                "A track appeared after the file had been started.",
                hint="The stream changed its tracks part-way through.",
            )
        track = _Track(self._next_id, timescale)
        self._tracks[track.track_id] = track
        self._next_id += 1
        return track

    # --- input -----------------------------------------------------------------------------

    def add_fragment(self, track_id: int, fragment: bytes, start_ticks: int) -> None:
        """Queue a finished fragment (``moof`` and ``mdat``) that begins at ``start_ticks``."""
        track = self._tracks[track_id]
        track.queue.append((start_ticks / track.timescale, fragment))
        self._pending_bytes += len(fragment)
        self._drain()

    def end_track(self, track_id: int) -> None:
        """No more fragments will come from this track."""
        self._tracks[track_id].ended = True
        self._drain()

    def close(self) -> None:
        """Finish: write everything that is waiting.

        Raises:
            FinalizationError: If no fragment was ever written.
        """
        for track in self._tracks.values():
            track.ended = track.ended or track.puller is None
        self._expected = len(self._tracks)
        self._holds = 0
        self._drain(final=True)
        if not self._started:
            raise FinalizationError(
                "There was no media to write.",
                hint="The stream may be empty, or its codecs may not be supported.",
            )

    # --- output ----------------------------------------------------------------------------

    def _ready(self) -> bool:
        if self._pending_bytes > MAX_PENDING_BYTES:
            return True
        return self._holds == 0 and len(self._tracks) >= self._expected

    def _drain(self, *, final: bool = False) -> None:
        if self._draining:  # a puller added a fragment while we were already writing
            return
        self._draining = True
        try:
            self._write_ready(final=final)
        finally:
            self._draining = False

    def _write_ready(self, *, final: bool) -> None:
        while self._tracks and (final or self._ready()):
            heads: list[tuple[float, int]] = []
            blocked = False
            for track in self._tracks.values():
                self._refill(track)
                if track.queue:
                    heads.append((track.queue[0][0], track.track_id))
                elif not track.ended:
                    blocked = True
            if not heads or (blocked and not self._must_write(final)):
                return
            first = min(heads)
            self._emit_head(self._tracks[first[1]], heads)

    def _must_write(self, final: bool) -> bool:
        return final or self._pending_bytes > MAX_PENDING_BYTES

    def _refill(self, track: _Track) -> None:
        while not track.queue and not track.ended and track.puller is not None:
            if not track.puller():
                track.ended = True

    def _emit_head(self, track: _Track, heads: list[tuple[float, int]]) -> None:
        _, fragment = track.queue.popleft()
        self._pending_bytes -= len(fragment)
        if not self._started:
            self.origin = min(start for start, _ in heads)
            self._started = True
            self._send(self._init())
        assert self.origin is not None  # noqa: S101 - set when the file was started
        self._sequence += 1
        shift = round(self.origin * track.timescale)
        self._send(patch_fragment(fragment, self._sequence, track.track_id, shift))

    def _init(self) -> bytes:
        # Video first, then audio; otherwise in the order the tracks were added.
        ordered = sorted(self._tracks.values(), key=lambda t: (_kind(t) != "video", t.track_id))
        return build_init_ordered(ordered)

    def _send(self, data: bytes) -> None:
        self._write(data)
        self.bytes_written += len(data)


def patch_fragment(fragment: bytes, sequence: int, track_id: int, shift: int) -> bytes:
    """Set a fragment's sequence number and track id, and move its decode time back by ``shift``.

    Raises:
        FinalizationError: If the fragment uses absolute data offsets, which cannot be moved.
    """
    data = bytearray(fragment)
    moof_end = struct.unpack_from(">I", data, 0)[0]
    position = 8
    while position < moof_end:
        size, kind = struct.unpack_from(">I4s", data, position)
        if kind == b"mfhd":
            struct.pack_into(">I", data, position + 12, sequence)
        elif kind == b"traf":
            _patch_traf(data, position + 8, position + size, track_id, shift)
        position += size
    return bytes(data)


def _patch_traf(data: bytearray, start: int, end: int, track_id: int, shift: int) -> None:
    position = start
    while position < end:
        size, kind = struct.unpack_from(">I4s", data, position)
        if kind == b"tfhd":
            flags = struct.unpack_from(">I", data, position + 8)[0] & 0xFFFFFF
            if flags & 0x1:
                raise FinalizationError(
                    "A fragment uses absolute data offsets, which cannot be re-wrapped.",
                    hint="FFmpeg can join this stream.",
                )
            struct.pack_into(">I", data, position + 12, track_id)
        elif kind == b"tfdt":
            version = data[position + 8]
            if version == 1:
                (value,) = struct.unpack_from(">Q", data, position + 12)
                struct.pack_into(">Q", data, position + 12, max(0, value - shift))
            else:
                (value32,) = struct.unpack_from(">I", data, position + 12)
                struct.pack_into(">I", data, position + 12, max(0, value32 - shift))
        position += size


def _kind(track: _Track) -> str:
    if track.config is not None:
        return track.config.kind
    return track.raw.kind if track.raw is not None else "audio"
