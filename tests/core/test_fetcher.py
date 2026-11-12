import httpx
import pytest

from rayohx import (
    CancellationError,
    CancellationToken,
    HlsSegmentFetcher,
    MediaPlaylist,
    SegmentDownloadError,
    UnsupportedPlaylistError,
)
from rayohx.playlist import parse_playlist
from tests.core.media import BASE, Origin, client_for, payload


def fetcher_for(origin: Origin) -> HlsSegmentFetcher:
    return HlsSegmentFetcher(client_for(origin), origin.playlist().segments)


def test_a_plain_segment_is_returned_as_downloaded() -> None:
    origin = Origin(3)
    data = fetcher_for(origin).fetch(1)
    assert data.index == 1
    assert data.media == payload(1)
    assert data.init == b""
    assert data.size == 500


def test_the_init_section_goes_with_the_first_segment_only() -> None:
    origin = Origin(3, ext="m4s", init=b"INIT")
    fetcher = fetcher_for(origin)
    assert fetcher.needs_init(0)
    assert not fetcher.needs_init(1)
    first, second = fetcher.fetch(0), fetcher.fetch(1)
    assert first.init == b"INIT"
    assert second.init == b""
    assert first.size == 504


def test_a_changed_init_section_is_written_again() -> None:
    text = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        '#EXT-X-MAP:URI="one.mp4"\n#EXTINF:4,\na.m4s\n#EXTINF:4,\nb.m4s\n'
        '#EXT-X-DISCONTINUITY\n#EXT-X-MAP:URI="two.mp4"\n#EXTINF:4,\nc.m4s\n#EXT-X-ENDLIST\n'
    )
    playlist = parse_playlist(text, BASE + "index.m3u8")
    assert isinstance(playlist, MediaPlaylist)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=request.url.path.encode())

    from rayohx import HttpClient, NetworkConfig

    client = HttpClient(NetworkConfig(), origin_url=BASE, transport=httpx.MockTransport(handler))
    fetcher = HlsSegmentFetcher(client, playlist.segments)
    assert [fetcher.needs_init(i) for i in range(3)] == [True, False, True]
    assert fetcher.fetch(2).init == b"/v/two.mp4"


def test_each_init_section_is_downloaded_once() -> None:
    origin = Origin(4, ext="m4s", init=b"INIT")
    fetcher = fetcher_for(origin)
    fetcher.fetch(0)
    fetcher.fetch(0)
    assert origin.log.count("init.mp4") == 1


def test_byte_ranges_are_requested() -> None:
    origin = Origin(3, byte_ranges=True)
    assert fetcher_for(origin).fetch(2).media == payload(2)


@pytest.mark.parametrize("explicit_iv", [True, False])
def test_encrypted_segments_are_decrypted(explicit_iv: bool) -> None:
    origin = Origin(3, encrypted=True, explicit_iv=explicit_iv)
    fetcher = fetcher_for(origin)
    assert [fetcher.fetch(i).media for i in range(3)] == [payload(i) for i in range(3)]
    assert origin.key_requests == 1


def test_progress_bytes_are_reported() -> None:
    seen: list[int] = []
    fetcher_for(Origin(2, size=1000)).fetch(0, seen.append)
    assert sum(seen) == 1000


def test_download_failures_name_the_segment() -> None:
    origin = Origin(3)
    origin.fail[2] = 404
    with pytest.raises(SegmentDownloadError) as info:
        fetcher_for(origin).fetch(2)
    assert info.value.index == 2
    assert "HTTP 404" in str(info.value)


def test_a_wrong_key_fails_the_segment() -> None:
    origin = Origin(2, encrypted=True)
    origin.plain[1] = b"changed"  # the blob is already encrypted; corrupt the wire data
    origin._blobs[1] = origin._blobs[1][:-5]
    with pytest.raises(SegmentDownloadError, match="blocks"):
        fetcher_for(origin).fetch(1)


def test_cancellation_is_not_turned_into_a_download_error() -> None:
    token = CancellationToken()
    token.cancel()
    origin = Origin(2)
    playlist = origin.playlist()
    fetcher = HlsSegmentFetcher(client_for(origin, cancel=token), playlist.segments)
    with pytest.raises(CancellationError):
        fetcher.fetch(0)


def test_unsupported_encryption_is_refused_not_attempted() -> None:
    text = (
        '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://k"\n'
        "#EXTINF:4,\na.ts\n#EXT-X-ENDLIST\n"
    )
    playlist = parse_playlist(text, BASE + "index.m3u8")
    assert isinstance(playlist, MediaPlaylist)
    origin = Origin(1)
    fetcher = HlsSegmentFetcher(client_for(origin), playlist.segments)
    with pytest.raises(UnsupportedPlaylistError):
        fetcher.fetch(0)
