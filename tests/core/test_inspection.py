import pytest

from rayohx import ConfigurationError, InvalidPlaylistError, MasterPlaylist, MediaPlaylist
from rayohx.inspection import inspect_playlist
from tests.core.hls import fixture_text


class FakeFetcher:
    """Returns canned text, as if the server had redirected to ``final_url``."""

    def __init__(self, text: str, final_url: str | None = None) -> None:
        self.text = text
        self.final_url = final_url
        self.requested: list[str] = []

    def fetch_text(self, url: str) -> tuple[str, str]:
        self.requested.append(url)
        return self.text, self.final_url or url


def test_inspect_returns_a_master_playlist() -> None:
    fetcher = FakeFetcher(fixture_text("master_basic.m3u8"))
    playlist = inspect_playlist("  https://example.com/video/master.m3u8 ", fetcher)
    assert isinstance(playlist, MasterPlaylist)
    assert fetcher.requested == ["https://example.com/video/master.m3u8"]
    assert playlist.variants[0].uri == "https://example.com/video/360p/index.m3u8"


def test_inspect_returns_a_media_playlist() -> None:
    fetcher = FakeFetcher(fixture_text("media_simple.m3u8"))
    playlist = inspect_playlist("https://example.com/a.m3u8", fetcher)
    assert isinstance(playlist, MediaPlaylist)


def test_inspect_does_not_rely_on_the_file_name() -> None:
    fetcher = FakeFetcher(fixture_text("media_simple.m3u8"))
    playlist = inspect_playlist("https://example.com/stream?id=7", fetcher)
    assert isinstance(playlist, MediaPlaylist)


def test_relative_urls_follow_redirects() -> None:
    fetcher = FakeFetcher(
        fixture_text("master_basic.m3u8"), "https://cdn.example.net/x/y/master.m3u8"
    )
    playlist = inspect_playlist("https://example.com/redirecting", fetcher)
    assert isinstance(playlist, MasterPlaylist)
    assert playlist.uri == "https://cdn.example.net/x/y/master.m3u8"
    assert playlist.variants[0].uri == "https://cdn.example.net/x/y/360p/index.m3u8"


def test_a_web_page_is_not_a_playlist() -> None:
    with pytest.raises(InvalidPlaylistError) as info:
        inspect_playlist("https://example.com/a", FakeFetcher(fixture_text("web_page.html")))
    assert info.value.hint


def test_bad_addresses_never_reach_the_fetcher() -> None:
    fetcher = FakeFetcher("")
    with pytest.raises(ConfigurationError):
        inspect_playlist("file:///etc/passwd", fetcher)
    assert fetcher.requested == []
