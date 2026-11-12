import struct
from dataclasses import dataclass

import pytest

from rayohx import FinalizationError
from rayohx.mux import fmp4
from rayohx.mux.fmp4 import RawTrack, build_fragment, build_init_ordered, language_code
from rayohx.mux.model import Sample, TrackConfig
from rayohx.mux.mp4read import find_box, iter_boxes
from rayohx.mux.muxer import patch_fragment
from tests.core.mux.helpers import config, read_fragments, top_level


@dataclass
class Track:
    track_id: int
    config: TrackConfig | None = None
    raw: RawTrack | None = None


def samples(count: int = 3, *, start: int = 1000) -> list[Sample]:
    return [
        Sample(data=bytes([index]) * (10 + index), dts=start + index * 40, duration=40,
               cts_offset=index * 3 - 3, sync=index == 0)
        for index in range(count)
    ]  # fmt: skip


# --- the initialization section ----------------------------------------------------------------


def test_the_init_section_lists_every_track() -> None:
    video = TrackConfig("video", 90000, b"\x00\x00\x00\x08vide", "x", width=640, height=360)
    audio = TrackConfig(
        "audio", 44100, b"\x00\x00\x00\x08soun", "x", channels=2, sample_rate=44100, language="fr"
    )
    data = build_init_ordered([Track(1, video), Track(2, audio)])
    assert top_level(data) == [b"ftyp", b"moov"]
    moov = find_box(data, (b"moov",))
    assert moov is not None
    children = [k for k, *_ in iter_boxes(data, moov[0] + 8, moov[0] + moov[1])]
    assert children == [b"mvhd", b"trak", b"trak", b"mvex"]
    mvex = find_box(data, (b"moov", b"mvex"))
    assert mvex is not None
    assert [k for k, *_ in iter_boxes(data, mvex[0] + 8, mvex[0] + mvex[1])] == [b"trex", b"trex"]

    handlers = []
    for kind, offset, size, _ in iter_boxes(data, moov[0] + 8, moov[0] + moov[1]):
        if kind != b"trak":
            continue
        trak = data[offset : offset + size]
        tkhd = find_box(trak, (b"tkhd",), 8)
        hdlr = find_box(trak, (b"mdia", b"hdlr"), 8)
        mdhd = find_box(trak, (b"mdia", b"mdhd"), 8)
        assert tkhd is not None
        assert hdlr is not None
        assert mdhd is not None
        handlers.append(
            (
                struct.unpack_from(">I", trak, tkhd[0] + 20)[0],
                trak[hdlr[0] + 16 : hdlr[0] + 20],
                struct.unpack_from(">I", trak, mdhd[0] + 20)[0],
            )
        )
        assert find_box(trak, (b"mdia", b"minf", b"stbl", b"stsd"), 8) is not None
    assert handlers == [(1, b"vide", 90000), (2, b"soun", 44100)]
    video_tkhd = find_box(data, (b"moov", b"trak", b"tkhd"), 0)
    assert video_tkhd is not None
    assert struct.unpack_from(">II", data, video_tkhd[0] + video_tkhd[1] - 8) == (
        640 << 16,
        360 << 16,
    )


def test_the_track_language_is_stored_in_three_letters() -> None:
    audio = TrackConfig("audio", 48000, b"\x00\x00\x00\x08soun", "x", language="de")
    data = build_init_ordered([Track(1, audio)])
    mdhd = find_box(data, (b"moov", b"trak", b"mdia", b"mdhd"), 0)
    assert mdhd is not None
    packed = struct.unpack_from(">H", data, mdhd[0] + 28)[0]
    assert "".join(chr(((packed >> shift) & 0x1F) + 0x60) for shift in (10, 5, 0)) == "deu"


def test_copied_tracks_are_included_as_they_are() -> None:
    raw = RawTrack(trak=b"\x00\x00\x00\x0atrakAB", trex=b"\x00\x00\x00\x0atrexCD")
    data = build_init_ordered([Track(1, config(), None), Track(2, None, raw)])
    assert b"trakAB" in data
    assert b"trexCD" in data


@pytest.mark.parametrize(
    ("tag", "code"),
    [("en", "eng"), ("EN-us", "eng"), ("pt_BR", "por"), ("eng", "eng"), ("zh", "zho"),
     ("xx", "und"), ("", "und"), (None, "und"), ("e", "und")],
)  # fmt: skip
def test_language_codes(tag: str | None, code: str) -> None:
    assert language_code(tag) == code


# --- fragments ---------------------------------------------------------------------------------


def test_a_fragment_is_a_moof_and_an_mdat() -> None:
    data = build_fragment(7, samples(), 123456)
    assert top_level(data) == [b"moof", b"mdat"]
    (fragment,) = read_fragments(data)
    assert fragment["track"] == 7
    assert fragment["start"] == 123456
    assert fragment["count"] == 3
    assert fragment["durations"] == [40, 40, 40]
    moof_size = struct.unpack_from(">I", data, 0)[0]
    assert fragment["data_offset"] == moof_size + 8
    mdat = data[moof_size:]
    assert mdat[4:8] == b"mdat"
    assert mdat[8:] == b"".join(s.data for s in samples())


def test_each_samples_size_flags_and_offset_are_recorded() -> None:
    data = build_fragment(1, samples(), 0)
    trun = find_box(data, (b"moof", b"traf", b"trun"), 0)
    assert trun is not None
    entries = trun[0] + 20
    rows = [struct.unpack_from(">IIIi", data, entries + i * 16) for i in range(3)]
    assert [row[1] for row in rows] == [10, 11, 12]
    assert [row[2] for row in rows] == [fmp4.SYNC_FLAGS, fmp4.NON_SYNC_FLAGS, fmp4.NON_SYNC_FLAGS]
    assert [row[3] for row in rows] == [-3, 0, 3]  # composition offsets can be negative


def test_the_decode_time_can_exceed_32_bits() -> None:
    data = build_fragment(1, samples(1), 2**40 + 5)
    assert read_fragments(data)[0]["start"] == 2**40 + 5


# --- patching a fragment for the output file ---------------------------------------------------


def test_the_sequence_track_and_decode_time_are_rewritten() -> None:
    patched = patch_fragment(build_fragment(7, samples(), 123456), 42, 3, 1000)
    (fragment,) = read_fragments(patched)
    assert (fragment["sequence"], fragment["track"], fragment["start"]) == (42, 3, 122456)
    assert fragment["count"] == 3


def test_a_decode_time_never_goes_below_zero() -> None:
    patched = patch_fragment(build_fragment(1, samples(), 500), 1, 1, 9000)
    assert read_fragments(patched)[0]["start"] == 0


def test_version_zero_decode_times_are_handled() -> None:
    data = bytearray(build_fragment(1, samples(), 5000))
    tfdt = find_box(data, (b"moof", b"traf", b"tfdt"), 0)
    assert tfdt is not None
    # Rewrite as a version-0 box: same size, 32-bit time, the other four bytes unused.
    data[tfdt[0] + 8] = 0
    struct.pack_into(">I", data, tfdt[0] + 12, 5000)
    patched = patch_fragment(bytes(data), 1, 1, 1000)
    assert struct.unpack_from(">I", patched, tfdt[0] + 12)[0] == 4000


def test_fragments_with_absolute_data_offsets_are_refused() -> None:
    data = bytearray(build_fragment(1, samples(), 0))
    tfhd = find_box(data, (b"moof", b"traf", b"tfhd"), 0)
    assert tfhd is not None
    data[tfhd[0] + 11] |= 0x01  # base-data-offset-present
    with pytest.raises(FinalizationError, match="absolute data offsets"):
        patch_fragment(bytes(data), 1, 1, 0)
