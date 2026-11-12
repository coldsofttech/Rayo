"""Feeding a fragmented MP4 stream (an initialization section, then fragments) to the muxer.

The compressed samples are never touched. Each track's ``trak`` box is copied (with a new track id
and language), and each ``moof``/``mdat`` pair is passed on with its track id and decode time
adjusted. This is what lets a stream whose audio is stored apart from its video be joined, in any
codec.
"""

import hashlib
import logging
import struct

from rayohx.errors import FinalizationError, UnsupportedCodecError
from rayohx.mux.fmp4 import MOVIE_TIMESCALE, RawTrack, language_code
from rayohx.mux.fragmenter import MAX_JUMP_SECONDS
from rayohx.mux.mp4read import find_box, iter_boxes
from rayohx.mux.muxer import Mp4Muxer

logger = logging.getLogger(__name__)

_IGNORED = frozenset(
    {b"ftyp", b"styp", b"sidx", b"ssix", b"emsg", b"prft", b"free", b"skip", b"mfra"}
)


class Fmp4Source:
    """Takes fragmented MP4 data in pieces of any size and gives the muxer its fragments."""

    def __init__(
        self, muxer: Mp4Muxer, *, language: str | None = None, name: str | None = None
    ) -> None:
        self._muxer = muxer
        self._language = language
        self._name = name
        self._buffer = bytearray()
        self._moof: bytes | None = None
        self._init_hash: bytes | None = None
        self._source_track: int | None = None
        self._track: int | None = None
        self._timescale = 1
        self._kind = "audio"
        self._defaults: tuple[int, int, int, int] = (1, 0, 0, 0)
        self._expected_start: int | None = None
        self._offset = 0
        self.expected_tracks: int | None = None
        self.track_ids: list[int] = []

    def feed(self, data: bytes) -> None:
        """Process more data."""
        self._buffer += data
        position = 0
        for kind, offset, size, header in iter_boxes(self._buffer):
            box_bytes = bytes(self._buffer[offset : offset + size])
            position = offset + size
            if kind == b"moov":
                self._init(box_bytes)
            elif kind == b"moof":
                self._moof = box_bytes
            elif kind == b"mdat":
                if self._moof is not None:
                    self._fragment(self._moof + box_bytes)
                    self._moof = None
            elif kind not in _IGNORED:
                logger.debug("Ignoring MP4 box %r", kind)
            del header
        del self._buffer[:position]

    def finish(self) -> None:
        """No more data is coming."""
        if self._track is not None:
            self._muxer.end_track(self._track)

    # --- the initialization section --------------------------------------------------------

    def _init(self, moov: bytes) -> None:
        digest = hashlib.sha256(moov).digest()
        if self._init_hash is not None:
            if digest != self._init_hash:
                raise FinalizationError(
                    "The stream changed its tracks part-way through.",
                    hint="FFmpeg can join streams like this.",
                )
            return
        self._init_hash = digest
        traks = [item for item in iter_boxes(moov, 8) if item[0] == b"trak"]
        if len(traks) != 1:
            raise UnsupportedCodecError(
                "fragmented MP4 with several tracks in one stream",
                hint="FFmpeg, if installed, can join streams like this.",
            )
        movie_scale = _movie_timescale(moov)
        _, offset, size, _ = traks[0]
        trak = moov[offset : offset + size]
        self._source_track = _u32(trak, find_box(trak, (b"tkhd",), 8), 20, 28)
        self._timescale = _u32(trak, find_box(trak, (b"mdia", b"mdhd"), 8), 20, 28)
        self._defaults = _trex_defaults(moov, self._source_track)
        handler = find_box(trak, (b"mdia", b"hdlr"), 8)
        if handler is not None and trak[handler[0] + 16 : handler[0] + 20] == b"vide":
            self._kind = "video"
        self.expected_tracks = 1
        self._muxer.expect(1)
        self._track = self._muxer.add_raw_track(
            lambda new_id: self._make(trak, moov, movie_scale, new_id), self._timescale
        )
        self.track_ids = [self._track]

    def _make(self, trak: bytes, moov: bytes, movie_scale: int, new_id: int) -> RawTrack:
        data = bytearray(trak)
        tkhd = find_box(data, (b"tkhd",), 8)
        mdhd = find_box(data, (b"mdia", b"mdhd"), 8)
        if tkhd is None or mdhd is None:
            raise FinalizationError("The MP4 track header is missing.")
        struct.pack_into(">I", data, tkhd[0] + (28 if data[tkhd[0] + 8] == 1 else 20), new_id)
        if self._language:
            letters = [ord(char) - 0x60 for char in language_code(self._language)]
            packed = (letters[0] << 10) | (letters[1] << 5) | letters[2]
            struct.pack_into(">H", data, mdhd[0] + (40 if data[mdhd[0] + 8] == 1 else 28), packed)
        _rescale_edit_list(data, movie_scale)
        trex = b"\x00\x00\x00\x20trex" + bytes(4) + struct.pack(">IIIII", new_id, *self._defaults)
        return RawTrack(bytes(data), trex, self._kind)

    # --- fragments -------------------------------------------------------------------------

    def _fragment(self, fragment: bytes) -> None:
        if self._track is None:
            return  # a fragment before any initialization section cannot be placed
        start, duration = _fragment_times(fragment, self._defaults[1])
        if start is None:
            start = self._expected_start if self._expected_start is not None else 0
        else:
            start -= self._offset
            if self._expected_start is not None:
                gap = start - self._expected_start
                if abs(gap) > MAX_JUMP_SECONDS * self._timescale:
                    self._offset += gap
                    start = self._expected_start
        self._expected_start = start + duration
        shifted = _set_tfdt(fragment, start)
        self._muxer.add_fragment(self._track, shifted, start)


def _u32(data: bytes | bytearray, found: tuple[int, int, int] | None, v0: int, v1: int) -> int:
    if found is None:
        raise FinalizationError("The MP4 initialization section is incomplete.")
    offset = found[0] + (v1 if data[found[0] + 8] == 1 else v0)
    return int(struct.unpack_from(">I", data, offset)[0])


def _movie_timescale(moov: bytes) -> int:
    found = find_box(moov, (b"mvhd",), 8)
    return _u32(moov, found, 20, 28) if found else MOVIE_TIMESCALE


def _trex_defaults(moov: bytes, track_id: int) -> tuple[int, int, int, int]:
    mvex = find_box(moov, (b"mvex",), 8)
    if mvex:
        for kind, offset, _, _ in iter_boxes(moov, mvex[0] + mvex[2], mvex[0] + mvex[1]):
            if kind == b"trex" and struct.unpack_from(">I", moov, offset + 12)[0] == track_id:
                first, second, third, fourth = struct.unpack_from(">IIII", moov, offset + 16)
                return int(first), int(second), int(third), int(fourth)
    return (1, 0, 0, 0)


def _rescale_edit_list(trak: bytearray, movie_scale: int) -> None:
    """Edit list durations are in movie time; this file's movie timescale is 1000."""
    found = find_box(trak, (b"edts", b"elst"), 8)
    if found is None or movie_scale == MOVIE_TIMESCALE:
        return
    offset = found[0]
    version = trak[offset + 8]
    count = struct.unpack_from(">I", trak, offset + 12)[0]
    position = offset + 16
    for _ in range(count):
        if version == 1:
            (duration,) = struct.unpack_from(">Q", trak, position)
            struct.pack_into(">Q", trak, position, duration * MOVIE_TIMESCALE // movie_scale)
            position += 20
        else:
            (duration32,) = struct.unpack_from(">I", trak, position)
            struct.pack_into(">I", trak, position, duration32 * MOVIE_TIMESCALE // movie_scale)
            position += 12


def _traf(fragment: bytes) -> tuple[int, int] | None:
    moof = find_box(fragment, (b"moof",))
    if moof is None:
        return None
    traf = find_box(fragment, (b"traf",), moof[0] + moof[2], moof[0] + moof[1])
    return None if traf is None else (traf[0] + traf[2], traf[0] + traf[1])


def _fragment_times(fragment: bytes, default_duration: int) -> tuple[int | None, int]:
    """The decode time stored in a fragment (if any) and the total duration of its samples."""
    span = _traf(fragment)
    if span is None:
        return None, 0
    start, duration = None, 0
    tfhd_default = default_duration
    for kind, offset, _, _ in iter_boxes(fragment, *span):
        if kind == b"tfdt":
            version = fragment[offset + 8]
            start = int(struct.unpack_from(">Q" if version else ">I", fragment, offset + 12)[0])
        elif kind == b"tfhd":
            flags = struct.unpack_from(">I", fragment, offset + 8)[0] & 0xFFFFFF
            position = offset + 16
            if flags & 0x1:
                position += 8
            if flags & 0x2:
                position += 4
            if flags & 0x8:
                tfhd_default = struct.unpack_from(">I", fragment, position)[0]
        elif kind == b"trun":
            duration += _trun_duration(fragment, offset, tfhd_default)
    return start, duration


def _trun_duration(fragment: bytes, offset: int, default_duration: int) -> int:
    flags = struct.unpack_from(">I", fragment, offset + 8)[0] & 0xFFFFFF
    count = struct.unpack_from(">I", fragment, offset + 12)[0]
    if not flags & 0x100:
        return int(count * default_duration)
    position = offset + 16 + (4 if flags & 0x1 else 0) + (4 if flags & 0x4 else 0)
    stride = 4 * sum(bool(flags & bit) for bit in (0x100, 0x200, 0x400, 0x800))
    return int(
        sum(
            struct.unpack_from(">I", fragment, position + index * stride)[0]
            for index in range(count)
        )
    )


def _set_tfdt(fragment: bytes, start: int) -> bytes:
    """Write ``start`` as the decode time. A fragment without one is left as it is."""
    span = _traf(fragment)
    if span is None:
        return fragment
    data = bytearray(fragment)
    for kind, offset, _, _ in iter_boxes(data, *span):
        if kind == b"tfdt":
            if data[offset + 8] == 1:
                struct.pack_into(">Q", data, offset + 12, max(0, start))
            else:
                struct.pack_into(">I", data, offset + 12, max(0, start) & 0xFFFFFFFF)
    return bytes(data)
