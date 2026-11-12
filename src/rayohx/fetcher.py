"""Turning a playlist entry into downloaded, decrypted bytes."""

import threading
from collections.abc import Callable, Sequence

from rayohx.containers import check_segment
from rayohx.crypto import KeyProvider, decrypt_segment, require_supported_encryption
from rayohx.enums import ContainerFormat
from rayohx.errors import CancellationError, DownloadError, NetworkError, SegmentDownloadError
from rayohx.http_client import HttpClient
from rayohx.models import InitMap, Segment, SegmentData


class HlsSegmentFetcher:
    """Downloads segments of one media playlist. Safe to call from many threads."""

    def __init__(
        self,
        client: HttpClient,
        segments: Sequence[Segment],
        keys: KeyProvider | None = None,
        container: ContainerFormat | None = None,
    ) -> None:
        self._container = container
        self._client = client
        self._segments = segments
        self._keys = keys if keys is not None else KeyProvider(client)
        self._inits: dict[InitMap, bytes] = {}
        self._init_lock = threading.Lock()

    def needs_init(self, index: int) -> bool:
        """Whether the initialization section must be written before this segment."""
        segment = self._segments[index]
        if segment.init_map is None:
            return False
        return index == 0 or self._segments[index - 1].init_map != segment.init_map

    def fetch(self, index: int, on_bytes: Callable[[int], None] | None = None) -> SegmentData:
        """Download segment ``index`` and decrypt it.

        Raises:
            SegmentDownloadError: If it cannot be downloaded or decrypted.
            CancellationError: If the download was cancelled.
        """
        segment = self._segments[index]
        if segment.encryption is not None:
            require_supported_encryption([segment])
        try:
            init = self._init_for(segment.init_map, on_bytes) if self.needs_init(index) else b""
            media = self._client.fetch_bytes(
                segment.uri, byte_range=segment.byte_range, on_bytes=on_bytes
            ).content
            if segment.encryption is not None:
                key = self._keys.key_for(segment.encryption)
                media = decrypt_segment(media, segment, key)
        except CancellationError:
            raise
        except (NetworkError, DownloadError) as exc:
            raise SegmentDownloadError(index, segment.uri, exc.message, hint=exc.hint) from exc
        if self._container is not None:
            problem = check_segment(self._container, media)
            if problem is None and init:
                problem = check_segment(self._container, init, is_init=True)
            if problem is not None:
                raise SegmentDownloadError(
                    index,
                    segment.uri,
                    problem,
                    hint="The server may have sent an error page, or the key may be wrong.",
                )
        return SegmentData(index=index, media=media, init=init)

    def _init_for(self, init_map: InitMap | None, on_bytes: Callable[[int], None] | None) -> bytes:
        if init_map is None:
            return b""
        with self._init_lock:
            cached = self._inits.get(init_map)
            if cached is None:
                cached = self._client.fetch_bytes(
                    init_map.uri, byte_range=init_map.byte_range, on_bytes=on_bytes
                ).content
                self._inits[init_map] = cached
            return cached
