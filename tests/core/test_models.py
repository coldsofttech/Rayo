from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from rayohx import (
    AudioTrack,
    ConfigurationError,
    ContainerFormat,
    DownloadProgress,
    DownloadRequest,
    DownloadResult,
    DownloadState,
    EncryptionInfo,
    EncryptionMethod,
    InvalidPlaylistError,
    MasterPlaylist,
    MediaPlaylist,
    OutputInfo,
    Resolution,
    Segment,
    SegmentRange,
    SubtitleTrack,
    Variant,
)


def test_resolution_parse_and_compare() -> None:
    assert Resolution.parse("1920x1080") == Resolution(1920, 1080)
    assert str(Resolution(1280, 720)) == "1280x720"
    assert Resolution(1920, 1080).pixels == 2_073_600
    assert Resolution(1280, 720) < Resolution(1920, 1080)
    for bad in ("1080p", "0x720", "axb", ""):
        with pytest.raises(InvalidPlaylistError):
            Resolution.parse(bad)
    with pytest.raises(ValueError, match="positive"):
        Resolution(0, 10)


def test_segment_range_parse_and_header() -> None:
    assert SegmentRange.parse("1000@200") == SegmentRange(1000, 200)
    assert SegmentRange.parse("500") == SegmentRange(500, None)
    assert SegmentRange(1000, 200).http_header() == "bytes=200-1199"
    with pytest.raises(ValueError, match="offset"):
        SegmentRange(10).http_header()
    for bad in ("0", "a@b", "-1"):
        with pytest.raises(InvalidPlaylistError):
            SegmentRange.parse(bad)
    with pytest.raises(ValueError, match="length"):
        SegmentRange(0)
    with pytest.raises(ValueError, match="offset"):
        SegmentRange(1, -1)


def test_encryption_info() -> None:
    iv = EncryptionInfo.parse_iv("0x00112233445566778899aabbccddeeff")
    assert len(iv) == 16
    assert EncryptionInfo.parse_iv("0x1") == b"\x00" * 15 + b"\x01"
    with pytest.raises(InvalidPlaylistError):
        EncryptionInfo.parse_iv("12345")
    aes = EncryptionInfo(EncryptionMethod.AES_128, "https://k/key", iv)
    assert aes.is_supported
    assert EncryptionInfo(EncryptionMethod.NONE).is_supported
    assert not EncryptionInfo(EncryptionMethod.SAMPLE_AES, "https://k").is_supported
    drm = EncryptionInfo(
        EncryptionMethod.AES_128, "skd://x", key_format="com.apple.streamingkeydelivery"
    )
    assert not drm.is_supported
    with pytest.raises(ValueError, match="key address"):
        EncryptionInfo(EncryptionMethod.AES_128)
    with pytest.raises(ValueError, match="16 bytes"):
        EncryptionInfo(EncryptionMethod.AES_128, "u", b"short")


def test_segment_validation() -> None:
    segment = Segment(index=0, uri="a.ts", duration=6.0, sequence_number=10)
    assert not segment.discontinuity
    with pytest.raises(FrozenInstanceError):
        segment.index = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="index"):
        Segment(index=-1, uri="a", duration=1, sequence_number=0)
    with pytest.raises(ValueError, match="duration"):
        Segment(index=0, uri="a", duration=-1, sequence_number=0)


def test_media_playlist_derived_values() -> None:
    segments = [Segment(index=i, uri=f"{i}.ts", duration=4.0, sequence_number=i) for i in range(3)]
    playlist = MediaPlaylist(uri="https://h/p.m3u8", segments=tuple(segments), target_duration=4)
    assert isinstance(playlist.segments, tuple)
    assert playlist.segment_count == 3
    assert playlist.total_duration == 12.0
    assert not playlist.is_vod
    assert MediaPlaylist(uri="u", segments=(), target_duration=4, end_list=True).is_vod
    assert MediaPlaylist(uri="u", segments=(), target_duration=4, playlist_type="VOD").is_vod
    assert playlist.container is ContainerFormat.UNKNOWN
    with pytest.raises(ValueError, match="target_duration"):
        MediaPlaylist(uri="u", segments=(), target_duration=-1)


def test_master_playlist_groups() -> None:
    variant = Variant(uri="v", bandwidth=1000, resolution=Resolution(1280, 720), codecs=("avc1",))
    master = MasterPlaylist(
        uri="m",
        variants=(variant,),
        audio_tracks=(AudioTrack(group_id="a", name="English", language="en"),),
        subtitle_tracks=(
            SubtitleTrack(group_id="s", name="English", language="en"),
            SubtitleTrack(group_id="t", name="French", language="fr"),
        ),
    )
    assert master.variants[0].height == 720
    assert master.variants[0].codecs == ("avc1",)
    assert master.audio_group("a")[0].language == "en"
    assert master.audio_group("zz") == ()
    assert [track.name for track in master.subtitle_group("t")] == ["French"]
    assert Variant(uri="v", bandwidth=0).height is None
    with pytest.raises(ValueError, match="bandwidth"):
        Variant(uri="v", bandwidth=-1)


def test_download_request_validates_and_hides_secrets() -> None:
    request = DownloadRequest(url=" https://h/a.m3u8?token=sekret ")
    assert request.url == "https://h/a.m3u8?token=sekret"
    assert "sekret" not in repr(request)
    assert "sekret" not in request.redacted_url
    with pytest.raises(ConfigurationError):
        DownloadRequest(url="not a url")


def test_progress_fraction() -> None:
    assert DownloadProgress().fraction is None
    assert DownloadProgress(total_bytes=200, downloaded_bytes=50).fraction == 0.25
    assert DownloadProgress(total_segments=10, completed_segments=5).fraction == 0.5
    assert DownloadProgress(total_bytes=10, downloaded_bytes=99).fraction == 1.0
    assert DownloadProgress(total_segments=2, completed_segments=3).fraction == 1.0


def test_result_states() -> None:
    output = OutputInfo(path=Path("v.mp4"), container=ContainerFormat.FMP4, size_bytes=10)
    done = DownloadResult(
        download_id="d",
        url="https://u:p@h/a?token=t",
        state=DownloadState.COMPLETED,
        elapsed_seconds=1.0,
        progress=DownloadProgress(),
        output=output,
    )
    assert done.success
    assert not done.cancelled
    assert "p@" not in done.url
    cancelled = DownloadResult(
        download_id="d",
        url="https://h",
        state=DownloadState.CANCELLED,
        elapsed_seconds=1.0,
        progress=DownloadProgress(),
    )
    assert cancelled.cancelled
    assert not cancelled.success
    with pytest.raises(ValueError, match="finished"):
        DownloadResult(
            download_id="d",
            url="https://h",
            state=DownloadState.DOWNLOADING,
            elapsed_seconds=0,
            progress=DownloadProgress(),
        )
