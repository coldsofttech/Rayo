"""HEVC (H.265): from Annex B byte streams to MP4 samples and the ``hvcC`` configuration."""

import struct
from dataclasses import dataclass

from rayohx.errors import OutputFormatError
from rayohx.mux.bits import BitReader, unescape_nal
from rayohx.mux.boxes import box, u16
from rayohx.mux.h264 import split_nal_units, visual_sample_entry
from rayohx.mux.model import TrackConfig

NAL_VPS = 32
NAL_SPS = 33
NAL_PPS = 34
NAL_AUD = 35
_FIRST_IRAP, _LAST_IRAP = 16, 23
_DROPPED = frozenset({35, 36, 37, 38})  # delimiters, end of sequence and stream, filler
_SUB_SIZES = {1: (2, 2), 2: (2, 1), 3: (1, 1)}


@dataclass(frozen=True, slots=True)
class HevcSps:
    """What the sequence parameter set says."""

    profile_space: int
    tier: int
    profile: int
    compatibility: int
    constraints: int
    level: int
    chroma_format: int
    bit_depth_luma: int
    bit_depth_chroma: int
    width: int
    height: int
    temporal_layers: int
    temporal_nesting: bool


def nal_type(unit: bytes) -> int:
    """The NAL unit type of an HEVC NAL unit."""
    return (unit[0] >> 1) & 0x3F


def parse_sps(nal: bytes) -> HevcSps:
    """Read the profile, level and picture size from a sequence parameter set NAL unit."""
    reader = BitReader(unescape_nal(nal[2:]))
    reader.skip(4)  # video parameter set id
    sub_layers = reader.read(3)
    nesting = reader.flag()
    space = reader.read(2)
    tier = reader.read(1)
    profile = reader.read(5)
    compatibility = reader.read(32)
    constraints = reader.read(48)
    level = reader.read(8)
    profile_present = []
    level_present = []
    for _ in range(sub_layers):
        profile_present.append(reader.flag())
        level_present.append(reader.flag())
    if sub_layers > 0:
        reader.skip(2 * (8 - sub_layers))
    for index in range(sub_layers):
        if profile_present[index]:
            reader.skip(88)
        if level_present[index]:
            reader.skip(8)
    reader.ue()  # sps id
    chroma = reader.ue()
    if chroma == 3:
        reader.skip(1)
    width = reader.ue()
    height = reader.ue()
    if reader.flag():
        left, right, top, bottom = reader.ue(), reader.ue(), reader.ue(), reader.ue()
        sub_x, sub_y = _SUB_SIZES.get(chroma, (1, 1))
        width -= sub_x * (left + right)
        height -= sub_y * (top + bottom)
    luma_depth = reader.ue() + 8
    chroma_depth = reader.ue() + 8
    if width <= 0 or height <= 0:
        raise OutputFormatError("The video's size could not be read from its headers.")
    return HevcSps(
        space, tier, profile, compatibility, constraints, level, chroma,
        luma_depth, chroma_depth, width, height, sub_layers + 1, nesting,
    )  # fmt: skip


class HevcParser:
    """Turns PES payloads (one access unit each) into MP4 samples, like ``H264Parser``."""

    timescale = 90000

    def __init__(self) -> None:
        self.config: TrackConfig | None = None
        self._sets: dict[int, list[bytes]] = {NAL_VPS: [], NAL_SPS: [], NAL_PPS: []}

    def access_units(self, payload: bytes) -> list[tuple[bytes, bool] | None]:
        """Split a PES payload into ``(hvcc data, is_keyframe)`` samples."""
        groups: list[list[bytes]] = [[]]
        for unit in split_nal_units(payload):
            if len(unit) > 2 and nal_type(unit) == NAL_AUD and groups[-1]:
                groups.append([])
            groups[-1].append(unit)
        return [self._sample(group) for group in groups if group]

    def _sample(self, units: list[bytes]) -> tuple[bytes, bool] | None:
        kept: list[bytes] = []
        keyframe = False
        for unit in units:
            if len(unit) < 3:
                continue
            kind = nal_type(unit)
            if kind in self._sets:
                if unit not in self._sets[kind]:
                    self._sets[kind].append(unit)
            elif kind not in _DROPPED:
                kept.append(unit)
                keyframe = keyframe or _FIRST_IRAP <= kind <= _LAST_IRAP
        if self.config is None and all(self._sets.values()):
            self._build()
        if self.config is None or not kept:
            return None
        return b"".join(struct.pack(">I", len(unit)) + unit for unit in kept), keyframe

    def _build(self) -> None:
        info = parse_sps(self._sets[NAL_SPS][0])
        record = bytearray([1])
        record.append((info.profile_space << 6) | (info.tier << 5) | info.profile)
        record += struct.pack(">I", info.compatibility)
        record += info.constraints.to_bytes(6, "big")
        record.append(info.level)
        record += u16(0xF000)  # min spatial segmentation: unknown
        record.append(0xFC)  # parallelism: unknown
        record.append(0xFC | info.chroma_format)
        record.append(0xF8 | (info.bit_depth_luma - 8))
        record.append(0xF8 | (info.bit_depth_chroma - 8))
        record += u16(0)  # average frame rate: unknown
        record.append((info.temporal_layers << 3) | (int(info.temporal_nesting) << 2) | 3)
        record.append(3)
        for kind in (NAL_VPS, NAL_SPS, NAL_PPS):
            units = self._sets[kind]
            record += bytes([0x80 | kind]) + u16(len(units))
            for unit in units:
                record += u16(len(unit)) + unit
        entry = visual_sample_entry(b"hvc1", info.width, info.height, box(b"hvcC", bytes(record)))
        codec = f"hvc1.{info.profile}.{info.compatibility:x}.L{info.level}"
        self.config = TrackConfig(
            "video", self.timescale, entry, codec, width=info.width, height=info.height
        )
