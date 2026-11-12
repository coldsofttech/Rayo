from datetime import UTC, datetime

import pytest

from rayohx import (
    ContainerFormat,
    EncryptionMethod,
    InvalidPlaylistError,
    MasterPlaylist,
    MediaPlaylist,
    Resolution,
    SegmentRange,
    UnsupportedPlaylistError,
)
from rayohx.playlist import is_hls_playlist, parse_attribute_list, parse_playlist
from tests.core.hls import BASE, fixture_text

DIR = "https://media.example.com/vod/title/"


def media(name: str) -> MediaPlaylist:
    playlist = parse_playlist(fixture_text(name), BASE)
    assert isinstance(playlist, MediaPlaylist)
    return playlist


def master(name: str) -> MasterPlaylist:
    playlist = parse_playlist(fixture_text(name), BASE)
    assert isinstance(playlist, MasterPlaylist)
    return playlist


# --- detection and attribute lists -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#EXTM3U\n#EXTINF:1,\na.ts", True),
        ("﻿#EXTM3U\n", True),
        ("\n  #EXTM3U\n", True),
        ("<html></html>", False),
        ("", False),
        ("#EXTINF:1,\n", False),
    ],
)
def test_is_hls_playlist_looks_at_content(text: str, expected: bool) -> None:
    assert is_hls_playlist(text) is expected


def test_attribute_list_handles_quotes_spaces_and_order() -> None:
    parsed = parse_attribute_list('B=2, A="x, y=z" ,C=3')
    assert parsed == {"B": "2", "A": "x, y=z", "C": "3"}
    assert parse_attribute_list("") == {}
    assert parse_attribute_list('URI=""') == {"URI": ""}


@pytest.mark.parametrize("text", ["NOVALUE", 'A="open', "a=1", 'A="x"B=2', "=1"])
def test_attribute_list_rejects_malformed_text(text: str) -> None:
    with pytest.raises(InvalidPlaylistError):
        parse_attribute_list(text, line=3)


# --- media playlists ---------------------------------------------------------------------------


def test_simple_media_playlist() -> None:
    playlist = media("media_simple.m3u8")
    assert playlist.uri == BASE
    assert playlist.version == 3
    assert playlist.target_duration == 10
    assert playlist.is_vod
    assert playlist.playlist_type == "VOD"
    assert playlist.container is ContainerFormat.MPEG_TS
    assert [segment.uri for segment in playlist.segments] == [
        f"{DIR}segment0.ts",
        f"{DIR}segment1.ts",
        f"{DIR}segment2.ts",
    ]
    assert [segment.duration for segment in playlist.segments] == [9.009, 9.009, 3.003]
    assert [segment.title for segment in playlist.segments] == ["First", "", ""]
    assert [segment.sequence_number for segment in playlist.segments] == [0, 1, 2]
    assert [segment.index for segment in playlist.segments] == [0, 1, 2]
    assert playlist.total_duration == pytest.approx(21.021)


def test_absolute_urls_are_kept() -> None:
    uris = [segment.uri for segment in media("media_absolute.m3u8").segments]
    assert uris == [
        "https://cdn.example.net/video/seg-1.ts",
        "http://cdn2.example.net:8080/video/seg-2.ts?auth=abc",
    ]


def test_relative_urls_follow_rfc_3986() -> None:
    uris = [segment.uri for segment in media("media_paths.m3u8").segments]
    assert uris == [
        "https://media.example.com/vod/shared/a.ts",
        "https://media.example.com/root/b.ts",
        "https://other.example.org/c.ts",
        f"{BASE}?part=4",
        f"{DIR}dir/d.ts?x=1&y=2",
    ]


def test_fmp4_with_initialization_map_and_media_sequence() -> None:
    playlist = media("media_fmp4.m3u8")
    assert playlist.container is ContainerFormat.FMP4
    assert playlist.media_sequence == 100
    assert [segment.sequence_number for segment in playlist.segments] == [100, 101]
    for segment in playlist.segments:
        assert segment.init_map is not None
        assert segment.init_map.uri == f"{DIR}init.mp4"
        assert segment.init_map.byte_range is None


def test_discontinuity_and_program_date_time_apply_to_the_next_segment_only() -> None:
    playlist = media("media_discontinuity.m3u8")
    assert [segment.discontinuity for segment in playlist.segments] == [False, True, False]
    assert playlist.segments[0].program_date_time is None
    assert playlist.segments[1].program_date_time == datetime(
        2026, 1, 2, 3, 4, 5, 250000, tzinfo=UTC
    )
    assert playlist.segments[2].program_date_time is None
    assert playlist.segments[0].sequence_number == 7


def test_byte_ranges_resolve_missing_offsets() -> None:
    playlist = media("media_byterange.m3u8")
    assert [segment.byte_range for segment in playlist.segments] == [
        SegmentRange(1000, 720),
        SegmentRange(2000, 1720),
        SegmentRange(500, 9000),
    ]
    init = playlist.segments[0].init_map
    assert init is not None
    assert init.byte_range == SegmentRange(720, 0)
    assert playlist.container is ContainerFormat.FMP4


def test_aes_128_keys_apply_until_replaced() -> None:
    segments = media("media_encrypted.m3u8").segments
    first, second, third, clear = (segments[0], segments[1], segments[2], segments[3])
    assert first.encryption is not None
    assert first.encryption.method is EncryptionMethod.AES_128
    assert first.encryption.uri == f"{DIR}keys/one.key"
    assert first.encryption.iv == bytes(range(16))
    assert second.encryption == first.encryption
    assert third.encryption is not None
    assert third.encryption.uri == "https://keys.example.com/two.key?token=hidden"
    assert third.encryption.iv is None
    assert clear.encryption is None
    assert first.encryption.is_supported


def test_sample_aes_is_read_but_flagged_unsupported() -> None:
    encryption = media("media_sample_aes.m3u8").segments[0].encryption
    assert encryption is not None
    assert encryption.method is EncryptionMethod.SAMPLE_AES
    assert encryption.uri == "skd://key"
    assert encryption.key_format == "com.apple.streamingkeydelivery"
    assert encryption.key_format_versions == "1"
    assert not encryption.is_supported


def test_webvtt_segments() -> None:
    assert media("media_subtitles.m3u8").container is ContainerFormat.WEBVTT


@pytest.mark.parametrize(
    ("uri", "container"),
    [
        ("a.ts", ContainerFormat.MPEG_TS),
        ("a.TS?x=1", ContainerFormat.MPEG_TS),
        ("a.m4s", ContainerFormat.FMP4),
        ("a.mp4", ContainerFormat.FMP4),
        ("a.cmfv", ContainerFormat.FMP4),
        ("segment", ContainerFormat.UNKNOWN),
        ("a.aac", ContainerFormat.UNKNOWN),
    ],
)
def test_container_detection(uri: str, container: ContainerFormat) -> None:
    text = f"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\n{uri}\n#EXT-X-ENDLIST\n"
    playlist = parse_playlist(text, BASE)
    assert isinstance(playlist, MediaPlaylist)
    assert playlist.container is container


def test_line_endings_bom_and_empty_playlists() -> None:
    text = "﻿#EXTM3U\r\n#EXT-X-TARGETDURATION:4\r#EXTINF:4,\r\na.ts\r\n#EXT-X-ENDLIST\r\n"
    playlist = parse_playlist(text, BASE)
    assert isinstance(playlist, MediaPlaylist)
    assert playlist.segment_count == 1
    empty = parse_playlist("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-ENDLIST\n", BASE)
    assert isinstance(empty, MediaPlaylist)
    assert empty.segments == ()
    assert empty.container is ContainerFormat.UNKNOWN


def test_extinf_without_a_comma_is_accepted() -> None:
    playlist = parse_playlist("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4\na.ts\n", BASE)
    assert isinstance(playlist, MediaPlaylist)
    assert playlist.segments[0].duration == 4.0
    assert not playlist.is_vod


def test_unicode_line_separators_do_not_split_lines() -> None:
    separator = chr(0x2028)
    title = f"Title{separator}with separator"
    text = f"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,{title}\na.ts\n"
    playlist = parse_playlist(text, BASE)
    assert isinstance(playlist, MediaPlaylist)
    assert playlist.segments[0].title == title


# --- master playlists --------------------------------------------------------------------------


def test_basic_master_playlist() -> None:
    playlist = master("master_basic.m3u8")
    assert playlist.version == 6
    assert len(playlist.variants) == 4
    first = playlist.variants[0]
    assert first.uri == f"{DIR}360p/index.m3u8"
    assert first.bandwidth == 800_000
    assert first.average_bandwidth == 700_000
    assert first.resolution == Resolution(640, 360)
    assert first.frame_rate == 25.0
    assert first.codecs == ("avc1.4d401e", "mp4a.40.2")
    last = playlist.variants[3]
    assert last.video_range == "PQ"
    assert last.average_bandwidth is None
    assert last.codecs[0].startswith("hvc1")


def test_absolute_variant_urls_and_names() -> None:
    playlist = master("master_absolute.m3u8")
    assert playlist.variants[0].uri == "https://cdn.example.net/hls/720.m3u8"
    assert playlist.variants[1].uri == "http://cdn2.example.net/hls/1080.m3u8?sig=zz"
    assert playlist.variants[1].name == "Full HD"


def test_audio_and_subtitle_groups() -> None:
    playlist = master("master_audio_subs.m3u8")
    assert [variant.bandwidth for variant in playlist.variants] == [2_000_000, 4_500_000, 64_000]
    assert playlist.variants[0].audio_group == "aud"
    assert playlist.variants[0].subtitle_group == "subs"
    assert [track.name for track in playlist.audio_group("aud")] == [
        "English",
        "English Commentary",
        "Français",
        "Deutsch",
    ]
    english = playlist.audio_tracks[0]
    assert english.default
    assert english.autoselect
    assert english.channels == "2"
    assert english.uri == f"{DIR}audio/en.m3u8"
    assert playlist.audio_tracks[1].characteristics == ("public.accessibility.describes-video",)
    assert [track.language for track in playlist.subtitle_tracks] == ["en", "en", "es"]
    assert playlist.subtitle_tracks[1].forced
    assert playlist.video_tracks == ()


def test_unusual_but_valid_attribute_order_and_spacing() -> None:
    playlist = master("master_attribute_order.m3u8")
    middle, low = playlist.variants
    assert middle.bandwidth == 1_200_000
    assert middle.resolution == Resolution(854, 480)
    assert middle.frame_rate == 29.97
    assert middle.codecs == ("avc1.42e01e", "mp4a.40.2")
    assert middle.name == "a, b = c"
    assert low.bandwidth == 300_000
    assert low.resolution == Resolution(426, 240)
    assert playlist.subtitle_tracks[0].uri == f"{DIR}sub.m3u8"


def test_unknown_tags_comments_and_blank_lines_are_skipped() -> None:
    playlist = master("master_unknown_tags.m3u8")
    assert [variant.uri for variant in playlist.variants] == [f"{DIR}video.m3u8"]


def test_video_renditions() -> None:
    text = (
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="v",NAME="Angle 2",DEFAULT=YES,URI="a2.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1000,VIDEO="v"\nmain.m3u8\n'
    )
    playlist = parse_playlist(text, BASE)
    assert isinstance(playlist, MasterPlaylist)
    assert playlist.video_tracks[0].name == "Angle 2"
    assert playlist.video_tracks[0].default
    assert playlist.variants[0].video_group == "v"


# --- bad input ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("malformed_no_header.m3u8", "#EXTM3U"),
        ("web_page.html", "#EXTM3U"),
        ("malformed_stream_without_uri.m3u8", "address"),
        ("malformed_stream_at_end.m3u8", "address"),
        ("malformed_extinf_without_uri.m3u8", "no segment address"),
        ("malformed_uri_without_extinf.m3u8", "#EXTINF"),
        ("malformed_no_targetduration.m3u8", "TARGETDURATION"),
        ("malformed_unterminated_quote.m3u8", "Unterminated"),
        ("malformed_missing_bandwidth.m3u8", "BANDWIDTH"),
        ("malformed_mixed.m3u8", "mixes"),
        ("malformed_empty.m3u8", "neither"),
        ("malformed_relative_range.m3u8", "offset"),
        ("malformed_key_without_uri.m3u8", "URI"),
        ("malformed_iv.m3u8", "initialization vector"),
    ],
)
def test_malformed_playlists_are_rejected_clearly(name: str, message: str) -> None:
    with pytest.raises(InvalidPlaylistError, match=message):
        parse_playlist(fixture_text(name), BASE)


def test_errors_report_the_line() -> None:
    with pytest.raises(InvalidPlaylistError) as info:
        parse_playlist(fixture_text("malformed_iv.m3u8"), BASE)
    assert info.value.line == 3
    with pytest.raises(InvalidPlaylistError) as info:
        parse_playlist(fixture_text("malformed_stream_without_uri.m3u8"), BASE)
    assert info.value.line == 2


def test_not_a_playlist_error_has_a_hint() -> None:
    with pytest.raises(InvalidPlaylistError) as info:
        parse_playlist(fixture_text("web_page.html"), BASE)
    assert info.value.hint


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("unsupported_define.m3u8", "variables"),
        ("unsupported_iframes.m3u8", "I-frame"),
        ("unsupported_scheme.m3u8", "http"),
    ],
)
def test_unsupported_features_are_refused(name: str, message: str) -> None:
    with pytest.raises(UnsupportedPlaylistError, match=message):
        parse_playlist(fixture_text(name), BASE)


@pytest.mark.parametrize(
    "body",
    [
        "#EXT-X-PLAYLIST-TYPE:LIVE\n#EXTINF:4,\na.ts",
        "#EXT-X-PROGRAM-DATE-TIME:yesterday\n#EXTINF:4,\na.ts",
        "#EXT-X-MEDIA-SEQUENCE:abc\n#EXTINF:4,\na.ts",
        "#EXT-X-BYTERANGE:0\n#EXTINF:4,\na.ts",
        "#EXTINF:soon,\na.ts",
        '#EXT-X-MAP:BYTERANGE="1@0"\n#EXTINF:4,\na.ts',
        '#EXT-X-KEY:URI="k"\n#EXTINF:4,\na.ts',
        "#EXTINF:4,\na\x01b.ts",
    ],
)
def test_bad_tag_values_are_invalid(body: str) -> None:
    with pytest.raises(InvalidPlaylistError):
        parse_playlist(f"#EXTM3U\n#EXT-X-TARGETDURATION:4\n{body}\n", BASE)


def test_unknown_encryption_method_is_unsupported() -> None:
    text = '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=ROT13,URI="k"\n#EXTINF:4,\na.ts\n'
    with pytest.raises(UnsupportedPlaylistError, match="ROT13"):
        parse_playlist(text, BASE)


@pytest.mark.parametrize(
    "tag",
    [
        "#EXT-X-STREAM-INF:BANDWIDTH=abc",
        "#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=big",
        "#EXT-X-STREAM-INF:BANDWIDTH=1,FRAME-RATE=fast",
        "#EXT-X-STREAM-INF:BANDWIDTH=1,FRAME-RATE=-1",
    ],
)
def test_bad_variant_attributes_are_invalid(tag: str) -> None:
    with pytest.raises(InvalidPlaylistError):
        parse_playlist(f"#EXTM3U\n{tag}\none.m3u8\n", BASE)


@pytest.mark.parametrize(
    "tag",
    [
        '#EXT-X-MEDIA:GROUP-ID="a",NAME="x"',
        '#EXT-X-MEDIA:TYPE=AUDIO,NAME="x"',
        '#EXT-X-MEDIA:TYPE=DATA,GROUP-ID="a",NAME="x"',
    ],
)
def test_bad_media_tags_are_invalid(tag: str) -> None:
    text = f"#EXTM3U\n{tag}\n#EXT-X-STREAM-INF:BANDWIDTH=1\none.m3u8\n"
    with pytest.raises(InvalidPlaylistError):
        parse_playlist(text, BASE)
