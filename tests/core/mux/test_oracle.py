"""Rayo's output checked against FFmpeg, which did not write it and decodes it independently.

These tests need ``ffmpeg`` and ``ffprobe``; without them they are skipped. The source files
come from FFmpeg too (see ``scripts/make_media_fixtures.py``), so a match means the remuxed file
holds exactly the same compressed pictures and sound.
"""

from pathlib import Path

import pytest

from rayohx.containers import validate_fmp4_file
from tests.core.mux.helpers import (
    MEDIA,
    decoded,
    decodes_cleanly,
    media,
    mux_ts,
    needs_ffmpeg,
    probe,
)

FILES = {
    "h264_aac": ("h264", "aac"),
    "hevc_aac": ("hevc", "aac"),
    "h264_ac3": ("h264", "ac3"),
    "h264_eac3": ("h264", "eac3"),
    "h264_mp3": ("h264", "mp3"),
}


def write(tmp_path: Path, name: str, chunk: int = 50_000) -> Path:
    data, _ = mux_ts(media(f"{name}.ts"), chunk)
    path = tmp_path / f"{name}.mp4"
    path.write_bytes(data)
    return path


@needs_ffmpeg
@pytest.mark.parametrize("name", list(FILES))
def test_the_remuxed_file_is_a_valid_mp4_with_the_same_codecs(tmp_path: Path, name: str) -> None:
    path = write(tmp_path, name)
    validate_fmp4_file(path)
    streams = probe(path)
    video_codec, audio_codec = FILES[name]
    assert [s["codec_name"] for s in streams["video"]] == [video_codec]
    assert [s["codec_name"] for s in streams["audio"]] == [audio_codec]
    video = streams["video"][0]
    assert (int(video["width"]), int(video["height"])) == (320, 240)
    assert streams["audio"][0]["sample_rate"] == "44100"
    assert decodes_cleanly(path)


@needs_ffmpeg
@pytest.mark.parametrize("name", [*FILES, "h264_only"])
def test_the_pictures_are_identical_to_the_original(tmp_path: Path, name: str) -> None:
    path = write(tmp_path, name)
    assert decoded(path, "video") == decoded(MEDIA / f"{name}.ts", "video")
    assert decoded(path, "video")[0] == 100


@needs_ffmpeg
@pytest.mark.parametrize("name", list(FILES))
def test_the_sound_is_identical_to_the_original(tmp_path: Path, name: str) -> None:
    path = write(tmp_path, name)
    assert decoded(path, "audio") == decoded(MEDIA / f"{name}.ts", "audio")


@needs_ffmpeg
def test_audio_only_streams_work(tmp_path: Path) -> None:
    path = write(tmp_path, "aac_only")
    assert decodes_cleanly(path)
    assert probe(path)["video"] == []
    assert decoded(path, "audio") == decoded(MEDIA / "aac_only.ts", "audio")


@needs_ffmpeg
def test_audio_and_video_stay_in_step(tmp_path: Path) -> None:
    path = write(tmp_path, "h264_aac")
    original = probe(MEDIA / "h264_aac.ts")
    remuxed = probe(path)

    def offset(streams: dict[str, list[dict[str, str]]]) -> float:
        return float(streams["audio"][0]["start_time"]) - float(streams["video"][0]["start_time"])

    assert offset(remuxed) == pytest.approx(offset(original), abs=0.002)
    assert float(remuxed["video"][0]["start_time"]) < 0.2  # starts near zero, not at 1.4 s


@needs_ffmpeg
def test_the_video_lasts_as_long_as_the_original(tmp_path: Path) -> None:
    path = write(tmp_path, "h264_aac")
    original, remuxed = probe(MEDIA / "h264_aac.ts"), probe(path)
    assert float(remuxed["video"][0]["duration"]) == pytest.approx(
        float(original["video"][0]["duration"]), abs=0.05
    )
    assert float(remuxed["video"][0]["duration"]) == pytest.approx(4.0, abs=0.05)


@pytest.mark.parametrize("name", list(FILES))
def test_the_output_does_not_depend_on_how_the_input_is_cut(name: str) -> None:
    data = media(f"{name}.ts")
    whole, _ = mux_ts(data, len(data))
    for chunk in (188, 188 * 7 + 3, 4096, 33_333):
        cut, _ = mux_ts(data, chunk)
        assert cut == whole


def test_remuxing_is_deterministic() -> None:
    data = media("h264_aac.ts")
    assert mux_ts(data)[0] == mux_ts(data)[0]
