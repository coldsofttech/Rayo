import threading

import httpx
import pytest

from rayohx import (
    AuthenticationError,
    DecryptionError,
    EncryptionInfo,
    EncryptionMethod,
    HttpClient,
    InvalidResponseError,
    NetworkConfig,
    Segment,
    UnsupportedPlaylistError,
)
from rayohx.crypto import (
    KeyProvider,
    decrypt_aes128,
    decrypt_segment,
    derive_iv,
    require_supported_encryption,
)
from tests.core.media import BASE, IV, KEY, encrypt

DATA = b"The quick brown fox jumps over the lazy dog" * 10
ENCRYPTION = EncryptionInfo(EncryptionMethod.AES_128, BASE + "key.bin", IV)


def segment(sequence: int = 5, encryption: EncryptionInfo | None = ENCRYPTION) -> Segment:
    return Segment(
        index=0, uri=BASE + "a.ts", duration=4, sequence_number=sequence, encryption=encryption
    )


def test_decrypt_round_trip() -> None:
    assert decrypt_aes128(encrypt(DATA, KEY, IV), KEY, IV) == DATA


def test_a_wrong_key_is_reported_clearly() -> None:
    with pytest.raises(DecryptionError, match="could not be decrypted") as info:
        decrypt_aes128(encrypt(DATA, KEY, IV), bytes(16), IV)
    assert info.value.hint


@pytest.mark.parametrize("data", [b"", b"x" * 15, b"x" * 17])
def test_data_that_is_not_whole_blocks_is_rejected(data: bytes) -> None:
    with pytest.raises(DecryptionError, match="blocks"):
        decrypt_aes128(data, KEY, IV)


def test_key_and_iv_sizes_are_checked() -> None:
    with pytest.raises(DecryptionError, match="16 bytes"):
        decrypt_aes128(b"x" * 16, KEY[:8], IV)
    with pytest.raises(DecryptionError, match="16 bytes"):
        decrypt_aes128(b"x" * 16, KEY, IV[:8])


def test_the_iv_comes_from_the_playlist_or_the_sequence_number() -> None:
    assert derive_iv(segment(sequence=9)) == IV
    implicit = EncryptionInfo(EncryptionMethod.AES_128, BASE + "key.bin")
    assert derive_iv(segment(sequence=9, encryption=implicit)) == (9).to_bytes(16, "big")
    assert derive_iv(segment(sequence=0, encryption=None)) == bytes(16)
    big = 2**100 + 7
    assert derive_iv(segment(sequence=big, encryption=implicit)) == big.to_bytes(16, "big")


def test_decrypt_segment_uses_the_segments_iv() -> None:
    implicit = EncryptionInfo(EncryptionMethod.AES_128, BASE + "key.bin")
    iv = (5).to_bytes(16, "big")
    assert decrypt_segment(encrypt(DATA, KEY, iv), segment(5, implicit), KEY) == DATA


def test_only_plain_aes_128_is_supported() -> None:
    require_supported_encryption([segment(), segment(encryption=None)])
    sample = EncryptionInfo(EncryptionMethod.SAMPLE_AES, "skd://k", key_format="com.apple.fps")
    with pytest.raises(UnsupportedPlaylistError, match="SAMPLE-AES") as info:
        require_supported_encryption([segment(), segment(encryption=sample)])
    assert "DRM" in (info.value.hint or "")
    drm = EncryptionInfo(EncryptionMethod.AES_128, "skd://k", key_format="com.example.drm")
    with pytest.raises(UnsupportedPlaylistError):
        require_supported_encryption([segment(encryption=drm)])


# --- key retrieval -----------------------------------------------------------------------------


class KeyServer:
    def __init__(self, key: bytes = KEY, status: int = 200) -> None:
        self.key = key
        self.status = status
        self.requests: list[httpx.Request] = []
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
        return httpx.Response(self.status, content=self.key)


def provider(server: KeyServer, **options: int) -> KeyProvider:
    client = HttpClient(
        NetworkConfig(retries=0), origin_url=BASE, transport=httpx.MockTransport(server)
    )
    return KeyProvider(client, **options)


def test_a_key_is_fetched_once_and_cached() -> None:
    server = KeyServer()
    keys = provider(server)
    assert keys.key_for(ENCRYPTION) == KEY
    assert keys.key_for(ENCRYPTION) == KEY
    assert len(server.requests) == 1


def test_threads_asking_for_one_key_cause_one_request() -> None:
    server = KeyServer()
    keys = provider(server)
    threads = [threading.Thread(target=keys.key_for, args=(ENCRYPTION,)) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(server.requests) == 1


def test_a_key_of_the_wrong_size_is_rejected() -> None:
    with pytest.raises(InvalidResponseError, match="16 bytes") as info:
        provider(KeyServer(key=b"not-a-16-byte-key-0123")).key_for(ENCRYPTION)
    assert info.value.hint


def test_a_refused_key_request_suggests_credential_hosts() -> None:
    with pytest.raises(AuthenticationError) as info:
        provider(KeyServer(status=403)).key_for(ENCRYPTION)
    assert "credential_hosts" in (info.value.hint or "")


def test_the_cache_is_bounded() -> None:
    server = KeyServer()
    keys = provider(server, max_keys=2)
    for name in ("a", "b", "c"):
        keys.key_for(EncryptionInfo(EncryptionMethod.AES_128, BASE + name))
    keys.key_for(EncryptionInfo(EncryptionMethod.AES_128, BASE + "a"))
    assert len(server.requests) == 4


def test_a_missing_key_address_is_an_error() -> None:
    info = EncryptionInfo.__new__(EncryptionInfo)
    object.__setattr__(info, "method", EncryptionMethod.AES_128)
    object.__setattr__(info, "uri", None)
    with pytest.raises(DecryptionError, match="no address"):
        provider(KeyServer()).key_for(info)
