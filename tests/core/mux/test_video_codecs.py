import struct

import pytest

from rayohx import OutputFormatError
from rayohx.mux import h264, hevc
from rayohx.mux.mp4read import find_box
from rayohx.mux.muxer import Mp4Muxer
from rayohx.mux.source import TsSource
from rayohx.mux.ts import TsDemuxer
from tests.core.mux.helpers import BitWriter, escape_nal, media

HIGH = 100


def sps_nal(
    profile: int = 66,
    width_mbs: int = 20,
    height_units: int = 15,
    *,
    frame_mbs_only: bool = True,
    crop: tuple[int, int, int, int] = (0, 0, 0, 0),
    chroma: int = 1,
    scaling: bool = False,
) -> bytes:
    writer = BitWriter().bits(profile, 8).bits(0, 8).bits(30, 8).ue(0)
    if profile == HIGH:
        writer.ue(chroma)
        if chroma == 3:
            writer.flag(False)
        writer.ue(0).ue(0).flag(False).flag(scaling)
        if scaling:
            for index in range(8 if chroma != 3 else 12):
                writer.flag(index == 0)
                if index == 0:
                    writer.se(-8)  # "use the default list": ends the list at once
    writer.ue(0).ue(0).ue(0).ue(1).flag(False)
    writer.ue(width_mbs - 1).ue(height_units - 1).flag(frame_mbs_only)
    if not frame_mbs_only:
        writer.flag(False)
    writer.flag(True).flag(any(crop))
    if any(crop):
        for value in crop:
            writer.ue(value)
    writer.flag(False)
    return bytes([0x67]) + escape_nal(writer.to_bytes())


PPS = bytes([0x68, 0xCE, 0x38, 0x80])
IDR = bytes([0x65, 0x88, 0x84, 0x00, 0x10])
SLICE = bytes([0x41, 0x9A, 0x24, 0x6C])
AUD = bytes([0x09, 0xF0])
SEI = bytes([0x06, 0x05, 0x01, 0x80])


def annexb(*units: bytes, long: bool = False) -> bytes:
    start = b"\x00\x00\x00\x01" if long else b"\x00\x00\x01"
    return b"".join(start + unit for unit in units)


# --- H.264 -------------------------------------------------------------------------------------


def test_nal_units_are_split_on_either_start_code() -> None:
    data = b"\x00\x00\x00\x01" + SLICE + b"\x00\x00\x01" + IDR + b"\x00\x00\x00\x00\x01" + AUD
    assert h264.split_nal_units(data) == [SLICE, IDR, AUD]
    assert h264.split_nal_units(b"no start codes") == []
    assert h264.split_nal_units(b"") == []


@pytest.mark.parametrize(
    ("kwargs", "size"),
    [
        ({}, (320, 240)),
        ({"width_mbs": 120, "height_units": 68, "crop": (0, 0, 0, 4)}, (1920, 1080)),
        ({"width_mbs": 80, "height_units": 45, "profile": HIGH}, (1280, 720)),
        (
            {"width_mbs": 80, "height_units": 23, "frame_mbs_only": False, "crop": (0, 0, 0, 4)},
            (1280, 720),
        ),
        ({"width_mbs": 45, "height_units": 80, "crop": (2, 2, 0, 0)}, (712, 1280)),
        ({"profile": HIGH, "scaling": True, "width_mbs": 40, "height_units": 30}, (640, 480)),
        ({"profile": HIGH, "chroma": 3, "scaling": True, "crop": (2, 0, 0, 0)}, (318, 240)),
        ({"profile": HIGH, "chroma": 0}, (320, 240)),
    ],
)
def test_the_picture_size_comes_from_the_sequence_parameters(
    kwargs: dict[str, object], size: tuple[int, int]
) -> None:
    info = h264.parse_sps(sps_nal(**kwargs))  # type: ignore[arg-type]
    assert (info.width, info.height) == size


def test_the_real_files_sequence_parameters_are_read() -> None:
    _, packets = _packets("h264_aac.ts", 0x1B)
    unit = next(u for u in h264.split_nal_units(packets[0]) if u[0] & 0x1F == 7)
    info = h264.parse_sps(unit)
    assert (info.width, info.height, info.profile) == (320, 240, 100)
    assert (info.chroma_format, info.bit_depth_luma) == (1, 8)


def test_a_cut_off_parameter_set_is_an_error() -> None:
    with pytest.raises(OutputFormatError):
        h264.parse_sps(sps_nal()[:4])


def _packets(name: str, stream_type: int) -> tuple[list[object], list[bytes]]:
    payloads: list[bytes] = []
    demuxer = TsDemuxer(
        lambda p, s: payloads.append(p.payload) if s.stream_type == stream_type else None
    )
    demuxer.feed(media(name))
    demuxer.flush()
    return [], payloads


def test_parameter_sets_move_to_the_sample_entry_and_out_of_the_samples() -> None:
    parser = h264.H264Parser()
    sps = sps_nal()
    units = parser.access_units(annexb(AUD, sps, PPS, SEI, IDR))
    assert len(units) == 1
    data, keyframe = units[0]  # type: ignore[misc]
    assert keyframe
    assert data == struct.pack(">I", len(SEI)) + SEI + struct.pack(">I", len(IDR)) + IDR
    assert parser.config is not None
    assert (parser.config.width, parser.config.height) == (320, 240)
    assert parser.config.codec == "avc1.42001e"
    avc_c = find_box(parser.config.sample_entry, (b"avcC",), 86)
    assert avc_c is not None
    record = parser.config.sample_entry[avc_c[0] + 8 : avc_c[0] + avc_c[1]]
    assert record[:5] == bytes([1, 66, 0, 30, 0xFF])
    assert record[5] == 0xE1
    assert record[6:8] == struct.pack(">H", len(sps))
    assert sps in record
    assert PPS in record


def test_samples_before_the_first_parameter_sets_are_skipped() -> None:
    parser = h264.H264Parser()
    assert parser.access_units(annexb(AUD, SLICE)) == [None]
    assert parser.config is None
    units = parser.access_units(annexb(AUD, sps_nal(), PPS, IDR))
    assert units[0] is not None
    follow = parser.access_units(annexb(AUD, SLICE))
    assert follow == [(struct.pack(">I", len(SLICE)) + SLICE, False)]


def test_several_access_units_in_one_payload_are_separated() -> None:
    parser = h264.H264Parser()
    units = parser.access_units(annexb(AUD, sps_nal(), PPS, IDR, AUD, SLICE, AUD, SLICE))
    assert [u[1] for u in units if u is not None] == [True, False, False]


def test_repeated_parameter_sets_are_stored_once() -> None:
    parser = h264.H264Parser()
    sps = sps_nal()
    parser.access_units(annexb(AUD, sps, PPS, IDR))
    parser.access_units(annexb(AUD, sps, PPS, SLICE))
    assert parser.config is not None
    assert parser.config.sample_entry.count(sps) == 1


def test_high_profiles_get_the_extra_avcc_fields() -> None:
    parser = h264.H264Parser()
    parser.access_units(annexb(AUD, sps_nal(profile=HIGH), PPS, IDR))
    assert parser.config is not None
    avc_c = find_box(parser.config.sample_entry, (b"avcC",), 86)
    assert avc_c is not None
    record = parser.config.sample_entry[avc_c[0] + 8 : avc_c[0] + avc_c[1]]
    assert record[-4:] == bytes([0xFD, 0xF8, 0xF8, 0])


def test_filler_and_delimiters_are_dropped() -> None:
    parser = h264.H264Parser()
    filler = bytes([0x0C, 0xFF, 0xFF])
    end_of_sequence = bytes([0x0A])
    units = parser.access_units(annexb(AUD, sps_nal(), PPS, IDR, filler, end_of_sequence))
    assert units[0] == (struct.pack(">I", len(IDR)) + IDR, True)


# --- HEVC --------------------------------------------------------------------------------------


def test_the_real_hevc_headers_are_read() -> None:
    _, packets = _packets("hevc_aac.ts", 0x24)
    units = hevc.HevcParser().access_units(packets[0])
    assert units[0] is not None
    parser = hevc.HevcParser()
    parser.access_units(packets[0])
    assert parser.config is not None
    assert (parser.config.width, parser.config.height) == (320, 240)
    assert parser.config.codec.startswith("hvc1.")
    box = find_box(parser.config.sample_entry, (b"hvcC",), 86)
    assert box is not None
    record = parser.config.sample_entry[box[0] + 8 : box[0] + box[1]]
    assert record[0] == 1
    assert record[21] & 3 == 3  # four-byte NAL lengths
    arrays = record[22]
    assert arrays == 3  # video, sequence and picture parameter sets


def test_hevc_keyframes_are_detected_and_parameter_sets_removed() -> None:
    _, packets = _packets("hevc_aac.ts", 0x24)
    parser = hevc.HevcParser()
    flags = []
    for payload in packets[:12]:
        flags += [unit[1] for unit in parser.access_units(payload) if unit is not None]
    assert flags[0] is True
    assert flags.count(True) == 1
    sample = hevc.HevcParser().access_units(packets[0])[0]
    assert sample is not None
    data = sample[0]
    position = 0
    kinds = []
    while position < len(data):
        (length,) = struct.unpack_from(">I", data, position)
        kinds.append((data[position + 4] >> 1) & 0x3F)
        position += 4 + length
    assert not set(kinds) & {32, 33, 34, 35}


def test_a_stream_without_parameter_sets_never_gets_a_config() -> None:
    parser = hevc.HevcParser()
    assert parser.access_units(annexb(bytes([0x02, 0x01, 0xAA, 0xBB]))) == [None]
    assert parser.config is None


# --- through the whole source ------------------------------------------------------------------


def test_leading_frames_that_cannot_be_decoded_are_dropped() -> None:
    packets = _packets("h264_only.ts", 0x1B)[1]
    start = next(
        i for i, p in enumerate(packets) if h264.H264Parser().access_units(p)[0] is not None
    )
    assert start == 0
    out = bytearray()
    muxer = Mp4Muxer(out.extend)
    source = TsSource(muxer)
    source.feed(media("h264_only.ts"))
    source.finish()
    muxer.close()
    assert out[4:8] == b"ftyp"
