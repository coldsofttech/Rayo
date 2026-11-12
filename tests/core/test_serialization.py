import json
from pathlib import Path

from rayohx import (
    ContainerFormat,
    DownloadProgress,
    DownloadResult,
    DownloadState,
    MasterPlaylist,
    OutputInfo,
    SelectionConfig,
    StorageMetrics,
    select_stream,
)
from rayohx.playlist import parse_playlist
from rayohx.serialization import (
    playlist_to_dict,
    progress_to_dict,
    result_to_dict,
    track_to_dict,
)
from tests.core.hls import BASE, fixture_text


def test_a_master_playlist_is_described_with_its_tracks_and_the_choice() -> None:
    master = parse_playlist(fixture_text("master_audio_subs.m3u8"), BASE)
    assert isinstance(master, MasterPlaylist)
    chosen = select_stream(master, SelectionConfig(audio_languages=("fr",)))
    data = playlist_to_dict(master, chosen)
    assert data["type"] == "master"
    assert [v["resolution"] for v in data["variants"]] == ["1280x720", "1920x1080", None]
    assert data["variants"][1]["codecs"] == ["avc1.640028", "mp4a.40.2"]
    assert data["variants"][1]["index"] == 1
    assert data["audio_tracks"][2]["language"] == "fr"
    assert data["audio_tracks"][0]["channels"] == "2"
    assert data["audio_tracks"][0]["separate"] is True
    assert data["subtitle_tracks"][1]["forced"] is True
    assert data["selection"] == {
        "variant": 1,
        "audio": ["Français"],
        "subtitles": [],
        "notes": [],
    }
    json.dumps(data)  # everything is plain data


def test_a_media_playlist_is_described_by_its_facts() -> None:
    media = parse_playlist(fixture_text("media_byterange.m3u8"), BASE)
    data = playlist_to_dict(media)
    assert data["type"] == "media"
    assert data["segments"] == 3
    assert data["duration"] == 30.0
    assert data["container"] == "fmp4"
    assert data["complete"] is True
    assert data["byte_ranges"] is True
    assert data["initialization_section"] is True
    assert data["encryption"] == []
    encrypted = playlist_to_dict(parse_playlist(fixture_text("media_encrypted.m3u8"), BASE))
    assert encrypted["encryption"] == ["AES-128"]
    discontinuous = playlist_to_dict(parse_playlist(fixture_text("media_discontinuity.m3u8"), BASE))
    assert discontinuous["discontinuities"] == 1


def test_addresses_never_carry_secrets() -> None:
    text = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nv.m3u8?token=hunter2\n"
    master = parse_playlist(text, "https://user:pw@h.example/m.m3u8?sig=abc")
    dump = json.dumps(playlist_to_dict(master))
    for secret in ("hunter2", "pw@", "abc"):
        assert secret not in dump


def test_a_track_inside_the_video_is_not_separate() -> None:
    master = parse_playlist(
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Main",DEFAULT=YES\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="a"\nv.m3u8\n',
        BASE,
    )
    assert isinstance(master, MasterPlaylist)
    track = track_to_dict(master.audio_tracks[0])
    assert track["separate"] is False
    assert track["url"] is None


def test_progress_and_results_are_plain_data(tmp_path: Path) -> None:
    progress = DownloadProgress(
        state=DownloadState.DOWNLOADING,
        downloaded_bytes=50,
        total_bytes=200,
        completed_segments=1,
        total_segments=4,
        speed=12.345,
        eta_seconds=3.21,
        storage=StorageMetrics(peak_temporary_bytes=7),
    )
    data = progress_to_dict(progress)
    assert data["fraction"] == 0.25
    assert data["speed"] == 12.3
    assert data["eta_seconds"] == 3.2
    assert data["peak_temporary_bytes"] == 7

    done = DownloadResult(
        download_id="abc",
        url="https://h/a.m3u8?token=zzz",
        state=DownloadState.COMPLETED,
        elapsed_seconds=1.23456,
        progress=progress,
        output=OutputInfo(path=tmp_path / "v.mp4", container=ContainerFormat.FMP4, size_bytes=9),
    )
    result = result_to_dict(done)
    assert result["success"] is True
    assert result["output"] == {
        "path": str(tmp_path / "v.mp4"),
        "size_bytes": 9,
        "container": "fmp4",
    }
    assert "zzz" not in json.dumps(result)
    failed = DownloadResult(
        download_id="abc",
        url="https://h/",
        state=DownloadState.FAILED,
        elapsed_seconds=0,
        progress=progress,
        error="boom",
    )
    assert result_to_dict(failed)["output"] is None
    assert result_to_dict(failed)["error"] == "boom"
