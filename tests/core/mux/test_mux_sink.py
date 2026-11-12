from pathlib import Path

import pytest

from rayohx import (
    ContainerFormat,
    FinalizationError,
    OutputError,
    OutputExistsError,
    SegmentData,
    UnsupportedCodecError,
)
from rayohx.containers import validate_fmp4_file
from rayohx.mux.mp4read import find_box
from rayohx.mux.sink import MuxSink, TrackFile
from tests.core.mux.helpers import MEDIA, media, read_fragments

TS = ContainerFormat.MPEG_TS
MP4 = ContainerFormat.FMP4


def feed(sink: MuxSink, data: bytes, size: int = 40_000) -> None:
    for index, start in enumerate(range(0, len(data), size)):
        sink.write(SegmentData(index=index, media=data[start : start + size]))


def tracks(path: Path) -> dict[int, int]:
    counts: dict[int, int] = {}
    for fragment in read_fragments(path.read_bytes()):
        counts[int(str(fragment["track"]))] = counts.get(int(str(fragment["track"])), 0) + 1
    return counts


def test_a_transport_stream_becomes_an_mp4(tmp_path: Path) -> None:
    sink = MuxSink(tmp_path / "out.mp4", TS)
    assert not sink.supports_resume
    assert sink.writes_file
    assert sink.open(resume_offset=10) == 0
    feed(sink, media("h264_aac.ts"))
    assert not (tmp_path / "out.mp4").exists()
    assert sink.part_path.exists()
    assert sink.commit(validate_fmp4_file) == tmp_path / "out.mp4"
    assert not sink.part_path.exists()
    assert set(tracks(tmp_path / "out.mp4")) == {1, 2}
    assert sink.origin == pytest.approx(1.4, abs=0.001)


def test_write_reports_output_bytes_not_input_bytes(tmp_path: Path) -> None:
    sink = MuxSink(tmp_path / "out.mp4", TS)
    sink.open()
    data = media("h264_aac.ts")
    written = [
        sink.write(SegmentData(index=0, media=data[i : i + 30_000]))
        for i in range(0, len(data), 30_000)
    ]
    sink.commit()
    assert sum(written) <= (tmp_path / "out.mp4").stat().st_size
    assert sink.checkpoint() == (tmp_path / "out.mp4").stat().st_size


def test_a_fragmented_mp4_stream_is_passed_through(tmp_path: Path) -> None:
    sink = MuxSink(tmp_path / "out.mp4", MP4)
    sink.open()
    folder = MEDIA / "hls_fmp4"
    sink.write(
        SegmentData(
            index=0,
            init=(folder / "init_0.mp4").read_bytes(),
            media=(folder / "v0_0.m4s").read_bytes(),
        )
    )
    sink.write(SegmentData(index=1, media=(folder / "v0_1.m4s").read_bytes()))
    sink.commit(validate_fmp4_file)
    assert tracks(tmp_path / "out.mp4") == {1: 2}


def test_a_separate_audio_file_is_read_as_it_is_needed(tmp_path: Path) -> None:
    audio = tmp_path / "audio.ts"
    audio.write_bytes(media("aac_only.ts"))
    sink = MuxSink(
        tmp_path / "out.mp4", TS, extras=[TrackFile(audio, TS, language="en", name="English")]
    )
    sink.open()
    feed(sink, media("h264_only.ts"))
    sink.commit(validate_fmp4_file)
    counts = tracks(tmp_path / "out.mp4")
    assert set(counts) == {1, 2}
    assert counts[2] >= 2
    data = (tmp_path / "out.mp4").read_bytes()
    assert data.index(b"vide") < data.index(b"soun")
    assert b"English" in data


def test_a_separate_fmp4_audio_file_can_be_joined_to_a_ts_video(tmp_path: Path) -> None:
    folder = MEDIA / "hls_fmp4"
    audio = tmp_path / "audio.mp4"
    audio.write_bytes(
        (folder / "init_1.mp4").read_bytes()
        + b"".join((folder / f"v1_{i}.m4s").read_bytes() for i in range(3))
    )
    sink = MuxSink(tmp_path / "out.mp4", TS, extras=[TrackFile(audio, MP4, language="fr")])
    sink.open()
    feed(sink, media("h264_only.ts"))
    sink.commit(validate_fmp4_file)
    assert set(tracks(tmp_path / "out.mp4")) == {1, 2}


def test_nothing_is_written_until_the_main_stream_has_named_its_tracks(tmp_path: Path) -> None:
    audio = tmp_path / "audio.ts"
    audio.write_bytes(media("aac_only.ts"))
    sink = MuxSink(tmp_path / "out.mp4", TS, extras=[TrackFile(audio, TS)])
    sink.open()
    assert sink.checkpoint() == 0  # the audio alone has not been written out
    feed(sink, media("h264_only.ts"))
    sink.commit()
    moov = find_box((tmp_path / "out.mp4").read_bytes(), (b"moov",))
    assert moov is not None
    assert (tmp_path / "out.mp4").read_bytes().count(b"trak") >= 2


def test_existing_output_is_protected(tmp_path: Path) -> None:
    (tmp_path / "out.mp4").write_bytes(b"precious")
    with pytest.raises(OutputExistsError):
        MuxSink(tmp_path / "out.mp4", TS).open()
    sink = MuxSink(tmp_path / "out.mp4", TS, overwrite=True)
    sink.open()
    feed(sink, media("h264_aac.ts"))
    sink.commit()
    assert (tmp_path / "out.mp4").read_bytes() != b"precious"


def test_a_folder_is_not_a_valid_output(tmp_path: Path) -> None:
    with pytest.raises(OutputError, match="folder"):
        MuxSink(tmp_path, TS).preflight()


def test_abort_removes_the_partial_file(tmp_path: Path) -> None:
    sink = MuxSink(tmp_path / "out.mp4", TS)
    sink.open()
    feed(sink, media("h264_aac.ts"))
    sink.abort(keep_partial=True)  # a muxed file cannot be resumed, so it is never kept
    assert not sink.part_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_the_final_check_can_refuse_the_file(tmp_path: Path) -> None:
    sink = MuxSink(tmp_path / "out.mp4", TS)
    sink.open()
    feed(sink, media("h264_aac.ts"))

    def refuse(_path: Path) -> None:
        raise FinalizationError("not good enough")

    with pytest.raises(FinalizationError, match="not good enough"):
        sink.commit(refuse)
    assert not (tmp_path / "out.mp4").exists()


def test_a_file_that_appears_meanwhile_is_not_clobbered(tmp_path: Path) -> None:
    sink = MuxSink(tmp_path / "out.mp4", TS)
    sink.open()
    feed(sink, media("h264_aac.ts"))
    (tmp_path / "out.mp4").write_bytes(b"someone else's")
    with pytest.raises(OutputExistsError):
        sink.commit()
    assert (tmp_path / "out.mp4").read_bytes() == b"someone else's"


def test_committing_without_any_media_is_an_error(tmp_path: Path) -> None:
    sink = MuxSink(tmp_path / "out.mp4", TS)
    sink.open()
    sink.write(SegmentData(index=0, media=b"\x47" + bytes(187) * 4))
    with pytest.raises(FinalizationError, match="no media"):
        sink.commit()


def test_an_unsupported_codec_stops_the_download(tmp_path: Path) -> None:
    from tests.core.mux.helpers import pat, pmt

    sink = MuxSink(tmp_path / "out.mp4", TS)
    sink.open()
    with pytest.raises(UnsupportedCodecError, match="MPEG-2 video"):
        sink.write(SegmentData(index=0, media=pat() + pmt([(0x02, 0x101, b"")])))
