"""H.264 (AVC): from Annex B byte streams to MP4 samples and the ``avcC`` configuration."""

import re
import struct
from dataclasses import dataclass

from rayohx.errors import OutputFormatError
from rayohx.mux.bits import BitReader, unescape_nal
from rayohx.mux.boxes import box, u16
from rayohx.mux.model import TrackConfig

_START = re.compile(rb"\x00\x00\x01")
_HIGH_PROFILES = frozenset({100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135})
_SUB_SIZES = {1: (2, 2), 2: (2, 1), 3: (1, 1)}

NAL_SLICE = 1
NAL_IDR = 5
NAL_SEI = 6
NAL_SPS = 7
NAL_PPS = 8
NAL_AUD = 9
_DROPPED = frozenset({9, 10, 11, 12})  # delimiters and filler


def split_nal_units(data: bytes) -> list[bytes]:
    """Split an Annex B byte stream (start codes ``00 00 01``) into NAL units."""
    starts = [match.end() for match in _START.finditer(data)]
    units = []
    for index, start in enumerate(starts):
        end = starts[index + 1] - 3 if index + 1 < len(starts) else len(data)
        unit = data[start:end].rstrip(b"\x00")
        if unit:
            units.append(unit)
    return units


@dataclass(frozen=True, slots=True)
class SpsInfo:
    """What the sequence parameter set says about the picture."""

    profile: int
    compatibility: int
    level: int
    chroma_format: int
    bit_depth_luma: int
    bit_depth_chroma: int
    width: int
    height: int


def parse_sps(nal: bytes) -> SpsInfo:
    """Read the size and profile from a sequence parameter set NAL unit."""
    reader = BitReader(unescape_nal(nal[1:]))
    profile = reader.read(8)
    compatibility = reader.read(8)
    level = reader.read(8)
    reader.ue()  # sps id
    chroma, luma_depth, chroma_depth = 1, 8, 8
    if profile in _HIGH_PROFILES:
        chroma = reader.ue()
        if chroma == 3:
            reader.skip(1)
        luma_depth = reader.ue() + 8
        chroma_depth = reader.ue() + 8
        reader.skip(1)
        if reader.flag():
            for index in range(8 if chroma != 3 else 12):
                if reader.flag():
                    _skip_scaling_list(reader, 16 if index < 6 else 64)
    reader.ue()  # log2_max_frame_num_minus4
    poc_type = reader.ue()
    if poc_type == 0:
        reader.ue()
    elif poc_type == 1:
        reader.skip(1)
        reader.se()
        reader.se()
        for _ in range(reader.ue()):
            reader.se()
    reader.ue()  # max_num_ref_frames
    reader.skip(1)
    width_mbs = reader.ue() + 1
    height_units = reader.ue() + 1
    frame_mbs_only = reader.flag()
    if not frame_mbs_only:
        reader.skip(1)
    reader.skip(1)  # direct_8x8_inference
    left = right = top = bottom = 0
    if reader.flag():
        left, right, top, bottom = reader.ue(), reader.ue(), reader.ue(), reader.ue()
    sub_x, sub_y = _SUB_SIZES.get(chroma, (1, 1)) if chroma else (1, 1)
    unit_x = sub_x
    unit_y = sub_y * (1 if frame_mbs_only else 2)
    if chroma == 0:
        unit_x, unit_y = 1, 1 if frame_mbs_only else 2
    width = width_mbs * 16 - unit_x * (left + right)
    height = (1 if frame_mbs_only else 2) * height_units * 16 - unit_y * (top + bottom)
    if width <= 0 or height <= 0:
        raise OutputFormatError("The video's size could not be read from its headers.")
    return SpsInfo(profile, compatibility, level, chroma, luma_depth, chroma_depth, width, height)


def _skip_scaling_list(reader: BitReader, size: int) -> None:
    last = next_value = 8
    for _ in range(size):
        if next_value != 0:
            next_value = (last + reader.se() + 256) % 256
        if next_value != 0:
            last = next_value


class H264Parser:
    """Turns PES payloads (one access unit each) into MP4 samples.

    Parameter sets go into the sample entry and are removed from the samples. Everything before
    the first keyframe is dropped, since it cannot be decoded.
    """

    timescale = 90000

    def __init__(self) -> None:
        self.config: TrackConfig | None = None
        self._sps: list[bytes] = []
        self._pps: list[bytes] = []
        self._info: SpsInfo | None = None

    def access_units(self, payload: bytes) -> list[tuple[bytes, bool] | None]:
        """Split a PES payload into samples: ``(avcc data, is_keyframe)``, or ``None`` for an
        access unit that has to be skipped for now."""
        units = split_nal_units(payload)
        groups: list[list[bytes]] = [[]]
        for unit in units:
            if unit[0] & 0x1F == NAL_AUD and groups[-1]:
                groups.append([])
            groups[-1].append(unit)
        return [self._sample(group) for group in groups if group]

    def _sample(self, units: list[bytes]) -> tuple[bytes, bool] | None:
        kept: list[bytes] = []
        keyframe = False
        for unit in units:
            kind = unit[0] & 0x1F
            if kind == NAL_SPS:
                self._add(self._sps, unit)
            elif kind == NAL_PPS:
                self._add(self._pps, unit)
            elif kind in _DROPPED:
                continue
            else:
                kept.append(unit)
                keyframe = keyframe or kind == NAL_IDR
        if self.config is None and self._sps and self._pps:
            self._build()
        if self.config is None or not kept:
            return None
        return b"".join(struct.pack(">I", len(unit)) + unit for unit in kept), keyframe

    def _add(self, store: list[bytes], unit: bytes) -> None:
        if unit not in store:
            store.append(unit)

    def _build(self) -> None:
        info = parse_sps(self._sps[0])
        self._info = info
        record = bytearray([1, info.profile, info.compatibility, info.level, 0xFF])
        record.append(0xE0 | len(self._sps))
        for sps in self._sps:
            record += u16(len(sps)) + sps
        record.append(len(self._pps))
        for pps in self._pps:
            record += u16(len(pps)) + pps
        if info.profile in _HIGH_PROFILES:
            record += bytes(
                [
                    0xFC | info.chroma_format,
                    0xF8 | (info.bit_depth_luma - 8),
                    0xF8 | (info.bit_depth_chroma - 8),
                    0,
                ]
            )
        entry = visual_sample_entry(b"avc1", info.width, info.height, box(b"avcC", bytes(record)))
        codec = f"avc1.{info.profile:02x}{info.compatibility:02x}{info.level:02x}"
        self.config = TrackConfig(
            "video", self.timescale, entry, codec, width=info.width, height=info.height
        )


def visual_sample_entry(kind: bytes, width: int, height: int, *children: bytes) -> bytes:
    """A video sample entry (``avc1``, ``hvc1``) with its codec configuration boxes."""
    name = bytes(32)
    return box(
        kind,
        bytes(6),
        u16(1),  # data reference index
        bytes(16),  # pre-defined and reserved
        u16(width),
        u16(height),
        struct.pack(">II", 0x00480000, 0x00480000),  # 72 dpi
        bytes(4),
        u16(1),  # frame count
        name,
        u16(0x0018),  # depth
        struct.pack(">h", -1),
        *children,
    )
