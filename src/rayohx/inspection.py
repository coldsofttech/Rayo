"""Looking at a stream before downloading it."""

from typing import Protocol

from rayohx.config import NetworkConfig, validate_url
from rayohx.errors import InvalidPlaylistError
from rayohx.http_client import HttpClient
from rayohx.models import Playlist
from rayohx.playlist import is_hls_playlist, parse_playlist


class PlaylistFetcher(Protocol):
    """Anything that can fetch a playlist's text. The HTTP layer provides the real one."""

    def fetch_text(self, url: str) -> tuple[str, str]:
        """Return the text at ``url`` and the address it finally came from (after redirects)."""
        ...


def inspect_playlist(url: str, fetcher: PlaylistFetcher) -> Playlist:
    """Fetch and parse the playlist at ``url`` without downloading any media.

    Raises:
        ConfigurationError: If ``url`` is not an http(s) address.
        InvalidPlaylistError: If what comes back is not an HLS playlist.
    """
    text, final_url = fetcher.fetch_text(validate_url(url))
    if not is_hls_playlist(text):
        raise InvalidPlaylistError(
            "The address did not return an HLS playlist.",
            hint="It may be a web page or need sign-in. Check the address, headers and cookies.",
        )
    return parse_playlist(text, final_url)


def inspect(url: str, network: NetworkConfig | None = None) -> Playlist:
    """Look at the stream at ``url`` without downloading it: its variants, tracks or segments.

    ``network`` sets headers, cookies, proxy, timeouts and TLS for the request.

    Raises:
        ConfigurationError: If ``url`` is not an http(s) address.
        PlaylistNotFoundError: If the playlist does not exist.
        InvalidPlaylistError: If what comes back is not an HLS playlist.
        NetworkError: If the request fails.
    """
    address = validate_url(url)
    with HttpClient(network or NetworkConfig(), origin_url=address) as client:
        return inspect_playlist(address, client)
