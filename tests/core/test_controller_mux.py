"""Whole downloads that need re-wrapping: separate audio, subtitles, TS to MP4 and FFmpeg."""

import subprocess
from pathlib import Path

import httpx
import pytest

from rayohx import (
    CancellationToken,
    DownloadConfig,
    Downloader,
    DownloadEvent,
    DownloadState,
    EventType,
    NetworkConfig,
    OutputConfig,
    OutputExistsError,
    OutputFormat,
    OutputFormatError,
    PlaylistNotFoundError,
    RemuxBackend,
    SegmentDownloadError,
    SelectionConfig,
    UnsupportedCodecError,
)
from rayohx.containers import validate_fmp4_file, validate_ts_file
from tests.core.mux.helpers import (
    MEDIA,
    Site,
    decoded,
    decodes_cleanly,
    hls_site,
    media,
    needs_ffmpeg,
    pat,
    pes,
    pmt,
    probe,
    single_file_site,
    ts_packets,
)

BASE = "https://media.example.com/"
NETWORK = NetworkConfig(retries=0, backoff_factor=0.001)


def run(
    site: Site,
    url: str,
    output: Path,
    *,
    selection: SelectionConfig | None = None,
    backend: RemuxBackend = RemuxBackend.AUTO,
    ffmpeg_path: Path | None = None,
    overwrite: bool = False,
    fmt: OutputFormat = OutputFormat.AUTO,
    token: CancellationToken | None = None,
) -> tuple[Downloader, list[DownloadEvent]]:
    config = DownloadConfig(
        network=NETWORK,
        selection=selection or SelectionConfig(),
        output=OutputConfig(
            remux_backend=backend, ffmpeg_path=ffmpeg_path, overwrite=overwrite, format=fmt
        ),
    )
    downloader = Downloader(
        BASE + url,
        output=output,
        config=config,
        cancel=token,
        transport=httpx.MockTransport(site),
    )
    events: list[DownloadEvent] = []
    downloader.events.subscribe(events.append)
    return downloader, events


def hidden(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("."))


# --- separate audio ----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["ts", "fmp4"])
def test_separate_audio_is_joined_into_one_mp4(tmp_path: Path, kind: str) -> None:
    site = hls_site(kind, kind)
    downloader, events = run(site, f"{kind}/master.m3u8", tmp_path / "movie.mp4")
    result = downloader.run()
    assert result.success, result.error
    assert result.output is not None
    assert result.output.path == tmp_path / "movie.mp4"
    assert result.output.container.value == "fmp4"
    assert result.output.size_bytes == (tmp_path / "movie.mp4").stat().st_size
    validate_fmp4_file(tmp_path / "movie.mp4")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["movie.mp4"]  # temporary files are gone
    loaded = [e for e in events if e.type is EventType.PLAYLIST_LOADED]
    assert any(e.data.get("audio") for e in loaded)
    assert (tmp_path / "movie.mp4").read_bytes().count(b"trak") >= 2


@needs_ffmpeg
@pytest.mark.parametrize("kind", ["ts", "fmp4"])
def test_the_joined_file_plays_and_matches_the_original_streams(tmp_path: Path, kind: str) -> None:
    site = hls_site(kind, kind)
    run(site, f"{kind}/master.m3u8", tmp_path / "movie.mp4")[0].run()
    output = tmp_path / "movie.mp4"
    streams = probe(output)
    assert [s["codec_name"] for s in streams["video"]] == ["h264"]
    assert [s["codec_name"] for s in streams["audio"]] == ["aac"]
    assert streams["audio"][0]["tags"]["language"] == "eng"  # type: ignore[index]
    assert decodes_cleanly(output)

    folder = MEDIA / f"hls_{kind}"
    video_source = subprocess.run(
        ["ffmpeg", "-v", "error", "-allowed_extensions", "ALL", "-i", str(folder / "v0.m3u8"),  # noqa: S607
         "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    lines = [x.split(",")[-1].strip() for x in video_source.splitlines() if not x.startswith("#")]
    assert decoded(output, "video") == (len(lines), "".join(lines))


def test_a_ts_name_cannot_hold_a_joined_stream(tmp_path: Path) -> None:
    site = hls_site("ts", "ts")
    downloader, _ = run(site, "ts/master.m3u8", tmp_path / "movie.ts")
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "needs an MP4" in (result.error or "")
    assert not any(name.endswith((".ts", ".m4s")) and "v0_" in name for name in site.log)
    assert list(tmp_path.iterdir()) == []


def test_an_unnamed_extension_gets_mp4_when_audio_must_be_joined(tmp_path: Path) -> None:
    site = hls_site("ts", "ts")
    result = run(site, "ts/master.m3u8", tmp_path / "movie")[0].run()
    assert result.output is not None
    assert result.output.path == tmp_path / "movie.mp4"


def test_separate_segments_cannot_hold_separate_tracks(tmp_path: Path) -> None:
    site = hls_site("ts", "ts")
    downloader, _ = run(site, "ts/master.m3u8", tmp_path / "parts", fmt=OutputFormat.SEGMENTS)
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "video_only" in (result.error or "")


def test_video_only_leaves_the_audio_out_and_needs_no_remuxing(tmp_path: Path) -> None:
    site = hls_site("ts", "ts")
    downloader, _ = run(
        site, "ts/master.m3u8", tmp_path / "video", selection=SelectionConfig(video_only=True)
    )
    result = downloader.run()
    assert result.success, result.error
    assert result.output is not None
    assert result.output.path == tmp_path / "video.ts"
    validate_ts_file(result.output.path)
    assert not any("v1_" in name for name in site.log)


def test_a_missing_audio_playlist_fails_before_any_video_is_downloaded(tmp_path: Path) -> None:
    site = hls_site("ts", "ts")
    del site.files["ts/v1.m3u8"]
    downloader, _ = run(site, "ts/master.m3u8", tmp_path / "movie.mp4")
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert isinstance(downloader.exception, PlaylistNotFoundError)
    assert not any("v0_" in name for name in site.log)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("broken", ["ts/v1_1.ts", "ts/v0_1.ts"])
def test_a_failed_segment_leaves_nothing_behind(tmp_path: Path, broken: str) -> None:
    site = hls_site("ts", "ts")
    site.fail.add(broken)
    downloader, _ = run(site, "ts/master.m3u8", tmp_path / "movie.mp4")
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert isinstance(downloader.exception, SegmentDownloadError)
    assert list(tmp_path.iterdir()) == []


def test_cancelling_a_joined_download_cleans_up(tmp_path: Path) -> None:
    site = hls_site("ts", "ts")
    token = CancellationToken()
    downloader, events = run(site, "ts/master.m3u8", tmp_path / "movie.mp4", token=token)

    def listener(event: DownloadEvent) -> None:
        if event.type is EventType.SEGMENT_COMPLETED and event.segment_index == 0:
            token.cancel()

    downloader.events.subscribe(listener)
    result = downloader.run()
    assert result.cancelled
    assert list(tmp_path.iterdir()) == []
    assert EventType.DOWNLOAD_CANCELLED in [e.type for e in events]


def test_an_existing_mp4_is_protected_before_anything_is_downloaded(tmp_path: Path) -> None:
    (tmp_path / "movie.mp4").write_bytes(b"precious")
    site = hls_site("ts", "ts")
    downloader, _ = run(site, "ts/master.m3u8", tmp_path / "movie.mp4")
    assert downloader.run().state is DownloadState.FAILED
    assert isinstance(downloader.exception, OutputExistsError)
    assert not any("v0_" in name for name in site.log)
    assert (tmp_path / "movie.mp4").read_bytes() == b"precious"
    again, _ = run(hls_site("ts", "ts"), "ts/master.m3u8", tmp_path / "movie.mp4", overwrite=True)
    assert again.run().success


# --- transport stream into MP4 -----------------------------------------------------------------


def test_a_transport_stream_is_saved_as_ts_unless_an_mp4_is_asked_for(tmp_path: Path) -> None:
    site = single_file_site("h264_aac.ts")
    result = run(site, "show/index.m3u8", tmp_path / "clip")[0].run()
    assert result.output is not None
    assert result.output.path == tmp_path / "clip.ts"
    assert (tmp_path / "clip.ts").read_bytes() == media("h264_aac.ts")


def test_a_transport_stream_can_be_saved_as_mp4(tmp_path: Path) -> None:
    site = single_file_site("h264_aac.ts")
    result = run(site, "show/index.m3u8", tmp_path / "clip.mp4")[0].run()
    assert result.success, result.error
    assert result.output is not None
    assert result.output.container.value == "fmp4"
    validate_fmp4_file(tmp_path / "clip.mp4")
    assert (tmp_path / "clip.mp4").read_bytes().count(b"trak") == 2


@needs_ffmpeg
def test_the_mp4_made_from_a_transport_stream_holds_the_same_pictures_and_sound(
    tmp_path: Path,
) -> None:
    site = single_file_site("hevc_aac.ts")
    run(site, "show/index.m3u8", tmp_path / "clip.mp4")[0].run()
    original = MEDIA / "hevc_aac.ts"
    assert decoded(tmp_path / "clip.mp4", "video") == decoded(original, "video")
    assert decoded(tmp_path / "clip.mp4", "audio") == decoded(original, "audio")


def test_fragmented_mp4_cannot_be_saved_as_ts(tmp_path: Path) -> None:
    site = hls_site("fmp4", "fmp4")
    downloader, _ = run(site, "fmp4/v0.m3u8", tmp_path / "video.ts")
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "cannot be saved as TS" in (result.error or "")


# --- subtitles ---------------------------------------------------------------------------------

SUBTITLE_MASTER = """#EXTM3U
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",DEFAULT=YES,URI="en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Deutsch",LANGUAGE="de",URI="de.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=320x240,SUBTITLES="subs"
index.m3u8
"""


def subtitle_playlist(prefix: str) -> bytes:
    return (
        "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-PLAYLIST-TYPE:VOD\n"
        f"#EXTINF:2,\n{prefix}0.vtt\n#EXTINF:2,\n{prefix}1.vtt\n#EXT-X-ENDLIST\n"
    ).encode()


def vtt(*cues: str) -> bytes:
    # The video's clock reads 1.4 s at the start, as in the fixture (126000 ticks).
    header = "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:126000,LOCAL:00:00:00.000\n\n"
    return (header + "\n\n".join(cues) + "\n").encode()


def subtitle_site() -> Site:
    site = single_file_site("h264_aac.ts")
    site.files["show/master.m3u8"] = SUBTITLE_MASTER.encode()
    site.files["show/en.m3u8"] = subtitle_playlist("en")
    site.files["show/de.m3u8"] = subtitle_playlist("de")
    shared = "00:00:01.500 --> 00:00:02.500\nspans the boundary"
    site.files["show/en0.vtt"] = vtt("00:00:00.500 --> 00:00:01.000\nHello", shared)
    site.files["show/en1.vtt"] = vtt(shared, "00:00:03.000 --> 00:00:03.500\nGoodbye")
    site.files["show/de0.vtt"] = vtt("00:00:00.500 --> 00:00:01.000\nHallo")
    site.files["show/de1.vtt"] = vtt("00:00:03.000 --> 00:00:03.500\nTschüss")
    return site


def test_subtitles_are_saved_beside_the_video_and_line_up_with_it(tmp_path: Path) -> None:
    site = subtitle_site()
    downloader, events = run(
        site,
        "show/master.m3u8",
        tmp_path / "clip.mp4",
        selection=SelectionConfig(subtitle_languages=("en", "de")),
    )
    result = downloader.run()
    assert result.success, result.error
    english = (tmp_path / "clip.en.vtt").read_text(encoding="utf-8")
    german = (tmp_path / "clip.de.vtt").read_text(encoding="utf-8")
    # The file starts at the stream's first timestamp (1.4 s), so a cue at media time 1.9 s
    # (1.4 s + 0.5 s) appears at 0.5 s.
    assert "00:00:00.500 --> 00:00:01.000\nHello" in english
    assert english.count("spans the boundary") == 1
    assert "00:00:01.500 --> 00:00:02.500\nspans the boundary" in english
    assert "Tschüss" in german
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip.de.vtt", "clip.en.vtt", "clip.mp4"]
    loaded = [e for e in events if e.type is EventType.PLAYLIST_LOADED]
    assert [e.data["subtitles"] for e in loaded if "subtitles" in e.data] == ["English", "Deutsch"]


def test_subtitles_next_to_a_plain_ts_keep_the_streams_own_clock(tmp_path: Path) -> None:
    site = subtitle_site()
    result = run(
        site,
        "show/master.m3u8",
        tmp_path / "clip.ts",
        selection=SelectionConfig(subtitle_languages=("en",)),
    )[0].run()
    assert result.success, result.error
    english = (tmp_path / "clip.en.vtt").read_text(encoding="utf-8")
    assert "00:00:01.900 --> 00:00:02.400\nHello" in english  # 1.4 s + 0.5 s, no shift


def test_an_existing_subtitle_file_is_protected_before_anything_is_downloaded(
    tmp_path: Path,
) -> None:
    (tmp_path / "clip.en.vtt").write_text("mine")
    site = subtitle_site()
    downloader, _ = run(
        site,
        "show/master.m3u8",
        tmp_path / "clip.mp4",
        selection=SelectionConfig(subtitle_languages=("en",)),
    )
    assert downloader.run().state is DownloadState.FAILED
    assert isinstance(downloader.exception, OutputExistsError)
    assert not any(name.startswith("show/seg") for name in site.log)
    assert (tmp_path / "clip.en.vtt").read_text() == "mine"


def test_subtitles_that_are_not_webvtt_are_explained(tmp_path: Path) -> None:
    site = subtitle_site()
    site.files["show/en0.vtt"] = b"not subtitles at all"
    downloader, _ = run(
        site,
        "show/master.m3u8",
        tmp_path / "clip.mp4",
        selection=SelectionConfig(subtitle_languages=("en",)),
    )
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "not in a supported form" in (result.error or "")
    assert not (tmp_path / "clip.en.vtt").exists()


def test_two_tracks_in_one_language_get_different_files(tmp_path: Path) -> None:
    site = subtitle_site()
    master = SUBTITLE_MASTER + (
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English SDH",LANGUAGE="en",'
        'URI="en.m3u8"\n'
    )
    site.files["show/master.m3u8"] = master.replace(
        'SUBTITLES="subs"\nindex.m3u8', 'SUBTITLES="subs"\nindex.m3u8'
    ).encode()
    result = run(
        site,
        "show/master.m3u8",
        tmp_path / "clip.mp4",
        selection=SelectionConfig(subtitle_languages=("en",), subtitle_names=("English SDH",)),
    )[0].run()
    assert result.success, result.error
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".vtt") == [
        "clip.en.vtt",
        "clip.en2.vtt",
    ]


# --- the choice of tool ------------------------------------------------------------------------


def mpeg2_site() -> Site:
    """A stream with MPEG-2 video, which the built-in remuxer cannot copy."""
    data = pat() + pmt([(0x02, 0x101, b"")]) + ts_packets(0x101, pes(0xE0, b"x" * 300, 1, 1))
    site = Site({"show/seg0.ts": data})
    site.files["show/index.m3u8"] = (
        b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:1,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    return site


def test_an_unsupported_codec_without_ffmpeg_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rayohx.controller.find_ffmpeg", lambda _path=None: None)
    downloader, _ = run(mpeg2_site(), "show/index.m3u8", tmp_path / "old.mp4")
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert isinstance(downloader.exception, UnsupportedCodecError)
    assert "MPEG-2 video" in (result.error or "")
    assert "prerequisite" in (result.error or "")
    assert list(tmp_path.iterdir()) == []


def test_the_native_only_setting_never_uses_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rayohx.controller.find_ffmpeg", lambda _path=None: Path("/usr/bin/ffmpeg"))
    downloader, _ = run(
        mpeg2_site(), "show/index.m3u8", tmp_path / "old.mp4", backend=RemuxBackend.NATIVE
    )
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "remux backend" in (result.error or "")


def test_ffmpeg_is_used_for_a_codec_the_built_in_remuxer_cannot_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rayohx.controller.find_ffmpeg", lambda _path=None: Path("/fake/ffmpeg"))
    calls: list[tuple[Path, list[Path], Path]] = []

    def fake_remux(
        executable: Path, inputs: list[object], output: Path, **_options: object
    ) -> None:
        paths = [item.path for item in inputs]  # type: ignore[attr-defined]
        calls.append((executable, paths, output))
        assert all(path.exists() for path in paths)
        output.write_bytes((MEDIA / "h264_only_frag.mp4").read_bytes())

    monkeypatch.setattr("rayohx.delivery.ffmpeg.remux", fake_remux)
    downloader, events = run(mpeg2_site(), "show/index.m3u8", tmp_path / "old.mp4")
    result = downloader.run()
    assert result.success, result.error
    ((executable, inputs, output),) = calls
    assert executable == Path("/fake/ffmpeg")
    assert len(inputs) == 1
    assert output.name == "old.mp4.part"
    assert (tmp_path / "old.mp4").read_bytes() == (MEDIA / "h264_only_frag.mp4").read_bytes()
    assert hidden(tmp_path) == []  # the temporary downloads are removed
    assert EventType.FINALIZATION_STARTED in [e.type for e in events]


def test_asking_for_ffmpeg_when_it_is_missing_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rayohx.controller.find_ffmpeg", lambda _path=None: None)
    downloader, _ = run(
        single_file_site("h264_aac.ts"),
        "show/index.m3u8",
        tmp_path / "clip.mp4",
        backend=RemuxBackend.FFMPEG,
    )
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert isinstance(downloader.exception, OutputFormatError)
    assert "optional prerequisite" in (result.error or "")


def test_a_failure_inside_ffmpeg_removes_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rayohx import FinalizationError

    monkeypatch.setattr("rayohx.controller.find_ffmpeg", lambda _path=None: Path("/fake/ffmpeg"))

    def broken(executable: Path, inputs: list[object], output: Path, **_options: object) -> None:
        output.write_bytes(b"half a file")
        raise FinalizationError("FFmpeg could not join the stream: boom")

    monkeypatch.setattr("rayohx.delivery.ffmpeg.remux", broken)
    downloader, _ = run(
        single_file_site("h264_aac.ts"),
        "show/index.m3u8",
        tmp_path / "clip.mp4",
        backend=RemuxBackend.FFMPEG,
    )
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "boom" in (result.error or "")
    assert list(tmp_path.iterdir()) == []


@needs_ffmpeg
@pytest.mark.parametrize("kind", ["ts", "fmp4"])
def test_real_ffmpeg_can_do_the_whole_job(tmp_path: Path, kind: str) -> None:
    site = hls_site(kind, kind)
    result = run(site, f"{kind}/master.m3u8", tmp_path / "movie.mp4", backend=RemuxBackend.FFMPEG)[
        0
    ].run()
    assert result.success, result.error
    assert decodes_cleanly(tmp_path / "movie.mp4")
    streams = probe(tmp_path / "movie.mp4")
    assert len(streams["video"]) == 1
    assert len(streams["audio"]) == 1
    assert hidden(tmp_path) == []


@needs_ffmpeg
def test_a_real_mpeg2_stream_falls_back_to_the_installed_ffmpeg(tmp_path: Path) -> None:
    source = tmp_path / "old.ts"
    video = "testsrc=duration=1:size=160x120:rate=25"
    subprocess.run(
        [  # noqa: S607
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video,
            "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "mpeg2video", "-c:a", "mp2",
            "-shortest", "-f", "mpegts", str(source),
        ],
        check=True,
    )  # fmt: skip
    data = source.read_bytes()
    packets = len(data) // 188
    half = (packets // 2) * 188
    site = Site(
        {
            "old/seg0.ts": data[:half],
            "old/seg1.ts": data[half : packets * 188],
            "old/index.m3u8": (
                b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nseg0.ts\n#EXTINF:1,\nseg1.ts\n"
                b"#EXT-X-ENDLIST\n"
            ),
        }
    )
    output = tmp_path / "converted.mp4"
    result = run(site, "old/index.m3u8", output)[0].run()
    assert result.success, result.error
    streams = probe(output)
    assert [s["codec_name"] for s in streams["video"]] == ["mpeg2video"]
    assert [s["codec_name"] for s in streams["audio"]] in (
        ["mp2"],
        ["mp3"],
    )  # layer 2 and 3 share a tag
    assert decodes_cleanly(output)
