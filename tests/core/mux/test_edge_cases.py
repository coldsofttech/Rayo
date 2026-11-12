"""Less common inputs: unusual box layouts, header variants and timing."""

import struct

import pytest

from rayohx import FinalizationError, OutputFormatError
from rayohx.mux import ac3, hevc, mp3
from rayohx.mux.boxes import box
from rayohx.mux.fmp4_source import (
    Fmp4Source,
    _fragment_times,
    _rescale_edit_list,
    _set_tfdt,
    _trex_defaults,
)
from rayohx.mux.mp4read import find_box, iter_boxes
from rayohx.mux.muxer import Mp4Muxer
from tests.core.mux.helpers import BitWriter, escape_nal, media

# --- reading boxes -----------------------------------------------------------------------------


def test_boxes_with_large_and_open_ended_sizes() -> None:
    large = struct.pack(">I4sQ", 1, b"big ", 16 + 5) + b"hello"
    open_ended = struct.pack(">I4s", 0, b"last") + b"tail"
    data = box(b"aaaa", b"xx") + large + open_ended
    found = [(kind, size, header) for kind, _, size, header in iter_boxes(data)]
    assert found == [(b"aaaa", 10, 8), (b"big ", 21, 16), (b"last", 12, 8)]


def test_a_box_that_does_not_fit_stops_the_walk() -> None:
    data = box(b"good", b"x") + struct.pack(">I4s", 100, b"bad ") + b"short"
    assert [kind for kind, *_ in iter_boxes(data)] == [b"good"]
    assert list(iter_boxes(b"\x00\x00\x00")) == []
    truncated_large = struct.pack(">I4s", 1, b"big ") + b"\x00\x00"
    assert list(iter_boxes(truncated_large)) == []
    assert list(iter_boxes(struct.pack(">I4s", 4, b"tiny"))) == []  # smaller than its own header


def test_finding_nested_boxes() -> None:
    data = box(b"outr", box(b"mid ", box(b"leaf", b"!")))
    found = find_box(data, (b"outr", b"mid ", b"leaf"))
    assert found is not None
    assert data[found[0] + found[2] : found[0] + found[1]] == b"!"
    assert find_box(data, (b"outr", b"nope")) is None
    assert find_box(data, (b"nope",)) is None


# --- fragments from other packagers ------------------------------------------------------------


def trun(count: int, *, flags: int, per_sample: bytes = b"", extra: bytes = b"") -> bytes:
    body = struct.pack(">I", (0 << 24) | flags) + struct.pack(">I", count) + extra + per_sample
    return box(b"trun", body)


def fragment_with(*traf_children: bytes) -> bytes:
    return box(b"moof", box(b"mfhd", bytes(8)), box(b"traf", *traf_children)) + box(
        b"mdat", b"data"
    )


def test_sample_durations_come_from_the_trun_when_it_lists_them() -> None:
    entries = b"".join(struct.pack(">II", duration, 10) for duration in (40, 40, 50))
    tfdt = box(b"tfdt", struct.pack(">II", 0, 1234))
    fragment = fragment_with(tfdt, trun(3, flags=0x300, per_sample=entries))
    assert _fragment_times(fragment, 99) == (1234, 130)


def test_sample_durations_fall_back_to_the_track_defaults() -> None:
    tfdt = box(b"tfdt", struct.pack(">II", 0, 0))
    plain = fragment_with(tfdt, trun(4, flags=0x200, per_sample=b"\x00" * 16))
    assert _fragment_times(plain, 25) == (0, 100)
    # tfhd with its own default duration (flag 0x8) wins over the track default.
    tfhd = box(b"tfhd", struct.pack(">II", 0x8, 1) + struct.pack(">I", 40))
    own = fragment_with(tfhd, tfdt, trun(2, flags=0x200, per_sample=bytes(8)))
    assert _fragment_times(own, 25) == (0, 80)
    # tfhd flags that insert other fields before the duration are skipped over correctly.
    skewed = box(
        b"tfhd",
        struct.pack(">II", 0x1 | 0x2 | 0x8, 1) + struct.pack(">QII", 0, 1, 60),
    )
    other = fragment_with(skewed, tfdt, trun(2, flags=0x200, per_sample=bytes(8)))
    assert _fragment_times(other, 25) == (0, 120)


def test_a_data_offset_and_first_sample_flags_do_not_confuse_the_duration() -> None:
    extra = struct.pack(">iI", 100, 0)
    entries = struct.pack(">II", 33, 10) + struct.pack(">II", 34, 10)
    fragment = fragment_with(trun(2, flags=0x305, per_sample=entries, extra=extra))
    assert _fragment_times(fragment, 0) == (None, 67)


def test_a_fragment_without_a_decode_time_or_traf_is_tolerated() -> None:
    assert _fragment_times(box(b"moof", box(b"mfhd", bytes(8))) + box(b"mdat", b""), 0) == (None, 0)
    assert _fragment_times(b"not a fragment", 0) == (None, 0)
    assert _set_tfdt(b"not a fragment", 5) == b"not a fragment"
    no_tfdt = fragment_with(trun(1, flags=0x200, per_sample=bytes(4)))
    assert _set_tfdt(no_tfdt, 5) == no_tfdt


def test_a_version_zero_decode_time_is_rewritten() -> None:
    fragment = fragment_with(box(b"tfdt", struct.pack(">II", 0, 1000)))
    patched = _set_tfdt(fragment, 70)
    tfdt = find_box(patched, (b"moof", b"traf", b"tfdt"))
    assert tfdt is not None
    assert struct.unpack_from(">I", patched, tfdt[0] + 12)[0] == 70


def test_track_defaults_are_read_from_the_trex_box() -> None:
    trex = box(b"trex", struct.pack(">IIIIII", 0, 7, 1, 1024, 0, 0x10000))
    moov = box(b"moov", box(b"mvex", trex))
    assert _trex_defaults(moov, 7) == (1, 1024, 0, 0x10000)
    assert _trex_defaults(moov, 8) == (1, 0, 0, 0)
    assert _trex_defaults(box(b"moov"), 1) == (1, 0, 0, 0)


@pytest.mark.parametrize("version", [0, 1])
def test_edit_lists_are_rescaled_to_the_output_movie_clock(version: int) -> None:
    if version == 1:
        entry = struct.pack(">QqHH", 90000, 0, 1, 0)
    else:
        entry = struct.pack(">IiHH", 90000, 0, 1, 0)
    elst = box(b"elst", struct.pack(">I", version << 24) + struct.pack(">I", 1) + entry)
    trak = bytearray(box(b"trak", box(b"edts", elst)))
    _rescale_edit_list(trak, 90000)
    found = find_box(trak, (b"edts", b"elst"), 8)
    assert found is not None
    fmt = ">Q" if version else ">I"
    assert struct.unpack_from(fmt, trak, found[0] + 16)[0] == 1000
    unchanged = bytearray(box(b"trak", box(b"edts", elst)))
    _rescale_edit_list(unchanged, 1000)
    assert unchanged == bytearray(box(b"trak", box(b"edts", elst)))
    _rescale_edit_list(bytearray(box(b"trak")), 90000)  # no edit list: nothing happens


def test_a_stream_without_track_data_cannot_be_copied() -> None:
    with pytest.raises(FinalizationError, match="incomplete"):
        Fmp4Source(Mp4Muxer(lambda data: None)).feed(
            box(b"moov", box(b"mvhd", bytes(100)), box(b"trak", box(b"free")))
        )


# --- HEVC headers ------------------------------------------------------------------------------


def hevc_sps(*, sub_layers: int = 0, crop: bool = False, chroma: int = 1) -> bytes:
    writer = BitWriter().bits(0, 4).bits(sub_layers, 3).flag(True)
    writer.bits(0, 2).bits(0, 1).bits(1, 5).bits(0x60000000, 32).bits(0, 48).bits(93, 8)
    present = [(index % 2 == 0, index % 2 == 1) for index in range(sub_layers)]
    for profile, level in present:
        writer.flag(profile).flag(level)
    if sub_layers:
        writer.bits(0, 2 * (8 - sub_layers))
    for profile, level in present:
        if profile:
            writer.bits(0, 88)
        if level:
            writer.bits(0, 8)
    writer.ue(0).ue(chroma)
    if chroma == 3:
        writer.flag(False)
    writer.ue(1920).ue(1088).flag(crop)
    if crop:
        writer.ue(0).ue(0).ue(0).ue(4)
    writer.ue(2).ue(2)
    return bytes([0x42, 0x01]) + escape_nal(writer.to_bytes())


@pytest.mark.parametrize(
    ("kwargs", "size", "layers"),
    [
        ({}, (1920, 1088), 1),
        ({"crop": True}, (1920, 1080), 1),
        ({"sub_layers": 2}, (1920, 1088), 3),
        ({"sub_layers": 3, "crop": True, "chroma": 3}, (1920, 1084), 4),
    ],
)
def test_hevc_sequence_parameters_with_sub_layers_and_cropping(
    kwargs: dict[str, object], size: tuple[int, int], layers: int
) -> None:
    info = hevc.parse_sps(hevc_sps(**kwargs))  # type: ignore[arg-type]
    assert (info.width, info.height) == size
    assert info.temporal_layers == layers
    assert (info.bit_depth_luma, info.bit_depth_chroma) == (10, 10)
    assert info.profile == 1
    assert info.level == 93


def test_hevc_headers_that_leave_no_picture_are_refused() -> None:
    writer = BitWriter().bits(0, 4).bits(0, 3).flag(True).bits(0, 2).bits(0, 1).bits(1, 5)
    writer.bits(0, 32).bits(0, 48).bits(93, 8).ue(0).ue(1).ue(0).ue(0).flag(False).ue(0).ue(0)
    with pytest.raises(OutputFormatError, match="size could not be read"):
        hevc.parse_sps(bytes([0x42, 0x01]) + writer.to_bytes())


# --- Dolby -------------------------------------------------------------------------------------


def eac3_frame(
    *, fscod: int = 1, blocks_code: int = 3, strmtyp: int = 0, size_words: int = 100
) -> bytes:
    writer = BitWriter().bits(strmtyp, 2).bits(0, 3).bits(size_words - 1, 11).bits(fscod, 2)
    if fscod == 3:
        writer.bits(1, 2)
    else:
        writer.bits(blocks_code, 2)
    writer.bits(7, 3).flag(True).bits(16, 5)
    body = writer.to_bytes(trailing=False)
    return ac3.SYNC + body + bytes(size_words * 2 - 2 - len(body))


def test_dolby_digital_plus_frames_are_measured_by_their_header() -> None:
    frame = ac3.parse_frame(eac3_frame(blocks_code=3), 0)
    assert frame is not None
    assert frame.enhanced
    assert (frame.sample_rate, frame.samples, frame.channels) == (44100, 1536, 6)
    assert len(frame.data) == 200
    short = ac3.parse_frame(eac3_frame(blocks_code=0), 0)
    assert short is not None
    assert short.samples == 256


def test_reduced_sample_rates_are_read_for_dolby_digital_plus() -> None:
    frame = ac3.parse_frame(eac3_frame(fscod=3), 0)
    assert frame is not None
    assert frame.sample_rate == 22050


def test_dependent_substreams_are_attached_to_their_access_unit() -> None:
    main, dependent = eac3_frame(), eac3_frame(strmtyp=1, size_words=40)
    parser = ac3.Ac3Parser()
    units = parser.frames(main + dependent + main + dependent + main)
    tail = parser.flush()
    assert [len(unit) for unit in units + tail] == [280, 280, 200]
    assert parser.config is not None
    assert parser.enhanced
    dec3 = find_box(parser.config.sample_entry, (b"dec3",), 36)
    assert dec3 is not None
    body = parser.config.sample_entry[dec3[0] + 8 : dec3[0] + dec3[1]]
    assert len(body) == 6  # a dependent substream adds the channel location field
    assert (body[4] >> 1) & 0xF == 1  # one dependent substream


def test_a_plain_dec3_has_no_dependent_substreams() -> None:
    parser = ac3.Ac3Parser()
    parser.frames(eac3_frame() + eac3_frame())
    assert parser.config is not None
    dec3 = find_box(parser.config.sample_entry, (b"dec3",), 36)
    assert dec3 is not None
    assert dec3[1] - 8 == 5  # no channel location field without a dependent substream


def test_dolby_frames_with_bad_headers_are_refused() -> None:
    data = ac3.SYNC + bytes(2) + bytes([0x3F, 0x40]) + bytes(40)  # frame size code 63
    with pytest.raises(OutputFormatError, match="invalid header"):
        ac3.parse_frame(data, 0)
    reserved_rate = ac3.SYNC + bytes(2) + bytes([0xC0, 0x40]) + bytes(40)  # fscod 3
    with pytest.raises(OutputFormatError, match="invalid header"):
        ac3.parse_frame(reserved_rate, 0)


def test_data_before_the_first_dolby_frame_is_skipped() -> None:
    parser = ac3.Ac3Parser()
    frames = [media("h264_ac3.ts")[0:0]]
    assert frames == [b""]
    assert parser.frames(b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09") == []


# --- MPEG audio --------------------------------------------------------------------------------


def test_mpeg_audio_layers_one_and_two() -> None:
    layer2 = 0xFFFD9000  # MPEG-1 layer 2, bitrate index 9, 44.1 kHz
    parser = mp3.Mp3Parser()
    size = 144 * 160000 // 44100
    assert len(parser.frames(layer2.to_bytes(4, "big") + bytes(size - 4))) == 1
    assert parser.samples_per_frame == 1152
    layer1 = 0xFFFF9000
    first = mp3.Mp3Parser()
    expected = (12 * 288000 // 44100) * 4
    assert len(first.frames(layer1.to_bytes(4, "big") + bytes(expected - 4))) == 1
    assert first.samples_per_frame == 384


def test_invalid_mpeg_audio_headers_are_not_frames() -> None:
    for header in (0xFFE00000, 0xFFFB0000, 0xFFFBF000, 0xFFFB9C00, 0xFFEB9000):
        assert mp3._parse_header(header) is None
