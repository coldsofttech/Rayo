"""HLS AES-128 segment decryption and key retrieval.

Only ``METHOD=AES-128`` with the standard key format is handled. SAMPLE-AES and DRM key formats
are refused up front by ``require_supported_encryption``: Rayo never claims to support DRM.
Keys are fetched through the HTTP layer, so the configured headers and cookies apply (to the key
server's host only if it is trusted for credentials).
"""

import threading
from collections.abc import Iterable

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from rayohx.errors import (
    AuthenticationError,
    DecryptionError,
    InvalidResponseError,
    UnsupportedPlaylistError,
)
from rayohx.http_client import HttpClient
from rayohx.models import EncryptionInfo, Segment

KEY_SIZE = 16
BLOCK_SIZE = 16
MAX_CACHED_KEYS = 64


def require_supported_encryption(segments: Iterable[Segment]) -> None:
    """Refuse playlists whose encryption the engine cannot undo.

    Raises:
        UnsupportedPlaylistError: For SAMPLE-AES or any non-standard key format.
    """
    for segment in segments:
        encryption = segment.encryption
        if encryption is not None and not encryption.is_supported:
            raise UnsupportedPlaylistError(
                f"This stream uses {encryption.method.value} with the key format "
                f"{encryption.key_format!r}, which is not supported.",
                hint="Only plain AES-128 encryption is supported. Protected (DRM) streams "
                "cannot be downloaded.",
            )


def derive_iv(segment: Segment) -> bytes:
    """The IV for ``segment``: the playlist's, or the segment's sequence number (RFC 8216)."""
    encryption = segment.encryption
    if encryption is not None and encryption.iv is not None:
        return encryption.iv
    return segment.sequence_number.to_bytes(16, "big")


def decrypt_aes128(data: bytes, key: bytes, iv: bytes) -> bytes:
    """Decrypt AES-128-CBC data with PKCS#7 padding.

    Raises:
        DecryptionError: If the data is not whole blocks or the padding is wrong.
    """
    if len(key) != KEY_SIZE or len(iv) != BLOCK_SIZE:
        raise DecryptionError("The key and the initialization vector must be 16 bytes each.")
    if not data or len(data) % BLOCK_SIZE:
        raise DecryptionError(
            "The encrypted data is not a whole number of blocks.",
            hint="The segment may be cut short or not encrypted as the playlist says.",
        )
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        raise DecryptionError(
            "The segment could not be decrypted.",
            hint="The key is probably wrong or has expired. Check the key server's access.",
        ) from None


def decrypt_segment(data: bytes, segment: Segment, key: bytes) -> bytes:
    """Decrypt one segment's media bytes with ``key`` and the segment's IV."""
    return decrypt_aes128(data, key, derive_iv(segment))


class KeyProvider:
    """Fetches decryption keys once each and keeps them for the rest of the download."""

    def __init__(self, client: HttpClient, *, max_keys: int = MAX_CACHED_KEYS) -> None:
        self._client = client
        self._max_keys = max_keys
        self._keys: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def key_for(self, encryption: EncryptionInfo) -> bytes:
        """Return the 16-byte key for ``encryption``, fetching it on first use."""
        uri = encryption.uri
        if not uri:
            raise DecryptionError("The playlist gives no address for the key.")
        with self._lock:
            cached = self._keys.get(uri)
            if cached is not None:
                return cached
            try:
                response = self._client.fetch_bytes(uri, max_bytes=1024)
            except AuthenticationError as exc:
                raise AuthenticationError(
                    uri,
                    exc.status_code,
                    hint="The key server refused the request. If it needs your Authorization "
                    "header or cookies, add its host to credential_hosts.",
                ) from exc
            key = response.content
            if len(key) != KEY_SIZE:
                raise InvalidResponseError(
                    f"The key must be {KEY_SIZE} bytes but {len(key)} were received.",
                    hint="The key server may have sent an error page or a protected (DRM) key.",
                )
            if len(self._keys) >= self._max_keys:
                self._keys.pop(next(iter(self._keys)))
            self._keys[uri] = key
            return key
