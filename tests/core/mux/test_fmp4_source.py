import struct
from pathlib import Path

import pytest

from rayohx import FinalizationError, UnsupportedCodecError
from rayohx.mux.fmp4 import build_init_ordered
from rayohx.mux.fmp4_source import Fmp4Source
from rayohx.mux.mp4read import find_box, iter_boxes
from rayohx.mux.muxer import Mp4Muxer
from tests.core.mux.helpers import MEDIA, config, read_fragments, top_level

HLS = MEDIA / "hls_fmp4"


def piece(name: str) -> bytes:
    return (HLS / name).read_bytes()


VIDEO = (piece("init_0.mp4"), [piece("v0_0.m4s"), piece("v0_1.m4s")])
AUDIO = (piece("init_1.mp4"), [piece("v1_0.m4s"), piece("v1_1.m4s"), piece("v1_2.m4s")])


def join(
    *sources: tuple[bytes, list[bytes]], chunk: int | None = None, language: str | None = None
) -> bytes:
    out = bytearray()
    muxer = Mp4Muxer(out.extend)
    feeders = [Fmp4Source(muxer, language=language if i else None) for i, _ in enumerate(sources)]

    def push(feeder: Fmp4Source, data: bytes) -> None:
        if chunk is None:
            feeder.feed(data)
            return
        for start in range(0, len(data), chunk):
            feeder.feed(data[start : start + chunk])

    for feeder, (init, _) in zip(feeders, sources, strict=True):
        push(feeder, init)  # every stream names its tracks before any media is written
    for feeder, (_, segments) in zip(feeders, sources, strict=True):
        for segment in segments:
            push(feeder, segment)
    for feeder in feeders:
        feeder.finish()
    muxer.close()
    return bytes(out)


def moofs(data: bytes) -> int:
    return top_level(data).count(b"moof")


def test_one_track_is_passed_through_fragment_for_fragment() -> None:
    data = join(VIDEO)
    assert top_level(data)[:2] == [b"ftyp", b"moov"]
    assert moofs(data) == sum(top_level(seg).count(b"moof") for seg in VIDEO[1])
    fragments = read_fragments(data)
    assert [f["sequence"] for f in fragments] == list(range(1, len(fragments) + 1))
    assert {f["track"] for f in fragments} == {1}
    assert fragments[0]["start"] == 0  # the output starts at zero


def test_the_compressed_data_is_not_touched() -> None:
    data = join(VIDEO)
    source_mdats = [
        bytes(seg[o : o + s]) for seg in VIDEO[1] for k, o, s, _ in iter_boxes(seg) if k == b"mdat"
    ]
    output_mdats = [bytes(data[o : o + s]) for k, o, s, _ in iter_boxes(data) if k == b"mdat"]
    assert output_mdats == source_mdats


def test_the_result_does_not_depend_on_how_the_data_is_cut() -> None:
    assert join(VIDEO, chunk=100) == join(VIDEO)
    assert join(VIDEO, AUDIO, chunk=777) == join(VIDEO, AUDIO)


def test_two_renditions_become_two_tracks_in_time_order() -> None:
    data = join(VIDEO, AUDIO, language="fr")
    fragments = read_fragments(data)
    assert {f["track"] for f in fragments} == {1, 2}
    # Fragments come out in time order, each track's own order unchanged.
    seconds = [int(str(f["start"])) / (90000 if f["track"] == 1 else 44100) for f in fragments]
    assert seconds == sorted(seconds)
    moov = find_box(data, (b"moov",))
    assert moov is not None
    kinds = []
    for kind, offset, size, _ in iter_boxes(data, moov[0] + 8, moov[0] + moov[1]):
        if kind == b"trak":
            hdlr = find_box(data[offset : offset + size], (b"mdia", b"hdlr"), 8)
            assert hdlr is not None
            kinds.append(data[offset + hdlr[0] + 16 : offset + hdlr[0] + 20])
    assert kinds == [b"vide", b"soun"]  # video first, though audio was added first below


def test_video_is_listed_first_even_when_the_audio_was_added_first() -> None:
    out = bytearray()
    muxer = Mp4Muxer(out.extend)
    audio, video = Fmp4Source(muxer, language="en"), Fmp4Source(muxer)
    audio.feed(AUDIO[0])
    video.feed(VIDEO[0])
    for segment in VIDEO[1]:
        video.feed(segment)
    for segment in AUDIO[1]:
        audio.feed(segment)
    audio.finish()
    video.finish()
    muxer.close()
    first_track = find_box(bytes(out), (b"moov", b"trak", b"mdia", b"hdlr"), 0)
    assert first_track is not None
    assert bytes(out[first_track[0] + 16 : first_track[0] + 20]) == b"vide"


def test_the_language_is_written_into_the_copied_track() -> None:
    data = join(VIDEO, AUDIO, language="fr")
    mdhd_boxes = []
    moov = find_box(data, (b"moov",))
    assert moov is not None
    for kind, offset, size, _ in iter_boxes(data, moov[0] + 8, moov[0] + moov[1]):
        if kind == b"trak":
            mdhd = find_box(data[offset : offset + size], (b"mdia", b"mdhd"), 8)
            assert mdhd is not None
            mdhd_boxes.append(struct.unpack_from(">H", data, offset + mdhd[0] + 28)[0])
    packed = mdhd_boxes[1]
    assert "".join(chr(((packed >> shift) & 0x1F) + 0x60) for shift in (10, 5, 0)) == "fra"


def test_a_repeated_initialization_section_is_ignored() -> None:
    out = bytearray()
    muxer = Mp4Muxer(out.extend)
    source = Fmp4Source(muxer)
    source.feed(VIDEO[0])
    source.feed(VIDEO[0] + VIDEO[1][0])
    source.feed(VIDEO[0] + VIDEO[1][1])
    source.finish()
    muxer.close()
    assert out.count(b"moov") == 1


def test_a_different_initialization_section_part_way_through_is_refused() -> None:
    muxer = Mp4Muxer(lambda data: None)
    source = Fmp4Source(muxer)
    source.feed(VIDEO[0])
    with pytest.raises(FinalizationError, match="changed its tracks"):
        source.feed(AUDIO[0])


def test_several_tracks_in_one_stream_are_not_supported() -> None:
    init = build_init_ordered(
        [type("T", (), {"track_id": 1, "config": config("video"), "raw": None})(),
         type("T", (), {"track_id": 2, "config": config("audio"), "raw": None})()]
    )  # fmt: skip
    source = Fmp4Source(Mp4Muxer(lambda data: None))
    with pytest.raises(UnsupportedCodecError, match="several tracks"):
        source.feed(init)


def test_fragments_before_the_initialization_section_are_dropped() -> None:
    out = bytearray()
    muxer = Mp4Muxer(out.extend)
    source = Fmp4Source(muxer)
    source.feed(VIDEO[1][0])
    source.feed(VIDEO[0])
    source.feed(VIDEO[1][1])
    source.finish()
    muxer.close()
    assert moofs(bytes(out)) == top_level(VIDEO[1][1]).count(b"moof")


def set_decode_time(segment: bytes, value: int) -> bytes:
    data = bytearray(segment)
    tfdt = find_box(data, (b"moof", b"traf", b"tfdt"), 0)
    assert tfdt is not None
    assert data[tfdt[0] + 8] == 1
    struct.pack_into(">Q", data, tfdt[0] + 12, value)
    return bytes(data)


def test_a_jump_in_decode_time_is_closed_up() -> None:
    expected = read_fragments(join(VIDEO))[1]["start"]
    jumped = (VIDEO[0], [VIDEO[1][0], set_decode_time(VIDEO[1][1], 500 * 90000)])
    fragments = read_fragments(join(jumped))
    assert fragments[1]["start"] == expected  # continues where the first fragment ended


def test_boxes_that_are_not_media_are_skipped() -> None:
    from tests.core.media import box

    padded = box(b"emsg", b"x" * 12) + VIDEO[1][0] + box(b"free", b"y" * 5) + box(b"prft", bytes(8))
    out = join((VIDEO[0], [padded]))
    assert moofs(out) == top_level(VIDEO[1][0]).count(b"moof")


def test_files_can_be_used_without_a_directory_listing(tmp_path: Path) -> None:
    (tmp_path / "x.mp4").write_bytes(VIDEO[0])
    assert (tmp_path / "x.mp4").stat().st_size == len(VIDEO[0])
