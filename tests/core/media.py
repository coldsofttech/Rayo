"""A fake media origin for download tests: serves segments, keys and init sections in memory."""

import struct
import threading
import time
from collections.abc import Iterator

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from rayohx import CancellationToken, HttpClient, MediaPlaylist, NetworkConfig
from rayohx.playlist import parse_playlist

BASE = "https://media.example.com/v/"
KEY = bytes(range(16, 32))
IV = bytes(range(16))


def payload(index: int, size: int = 500) -> bytes:
    """Deterministic, recognisable bytes for segment ``index``."""
    return bytes((index * 31 + offset * 7) % 251 for offset in range(size))


def box(kind: bytes, body: bytes = b"") -> bytes:
    """An MP4 box: a 4-byte size, a 4-byte type, then the body."""
    return struct.pack(">I4s", 8 + len(body), kind) + body


def ts_payload(index: int, packets: int = 3) -> bytes:
    """MPEG-TS packets (188 bytes each, starting with the 0x47 sync byte)."""
    return b"".join(
        b"\x47" + bytes([(index + number) % 256]) + bytes(186) for number in range(packets)
    )


def fmp4_init() -> bytes:
    """A minimal fragmented-MP4 initialization section."""
    return box(b"ftyp", b"iso5\x00\x00\x00\x01iso5") + box(b"moov", box(b"mvhd", bytes(20)))


def fmp4_fragment(index: int, size: int = 100) -> bytes:
    """A movie fragment: a ``moof`` box followed by its ``mdat`` box."""
    return box(b"moof", box(b"mfhd", struct.pack(">II", 0, index + 1))) + box(
        b"mdat", bytes([index % 251]) * size
    )


def encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC with PKCS#7 padding, as HLS does."""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


class Origin:
    """An in-memory HLS origin. Call it as an ``httpx`` mock transport handler."""

    def __init__(
        self,
        count: int,
        *,
        size: int = 500,
        ext: str = "ts",
        init: bytes | None = None,
        encrypted: bool = False,
        explicit_iv: bool = True,
        byte_ranges: bool = False,
        end_list: bool = True,
        duration: float = 4.0,
        content: str = "raw",
    ) -> None:
        self.count = count
        self.size = size
        self.ext = ext
        self.init = init
        self.encrypted = encrypted
        self.explicit_iv = explicit_iv
        self.byte_ranges = byte_ranges
        self.end_list = end_list
        self.duration = duration
        self.delays: dict[int, float] = {}
        self.fail: dict[int, int] = {}
        self.flaky: dict[int, int] = {}
        self.arrivals: dict[str, float] = {}
        self.log: list[str] = []
        self.key_requests = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()
        self.files: dict[str, bytes] = {}
        if content == "ts":
            self.plain = [ts_payload(i) for i in range(count)]
        elif content == "fmp4":
            self.plain = [fmp4_fragment(i) for i in range(count)]
            self.init = self.init if self.init is not None else fmp4_init()
        else:
            self.plain = [payload(i, size) for i in range(count)]
        self._blobs = [self._wire(i) for i in range(count)]

    def _iv(self, index: int) -> bytes:
        return IV if self.explicit_iv else index.to_bytes(16, "big")

    def _wire(self, index: int) -> bytes:
        return (
            encrypt(self.plain[index], KEY, self._iv(index))
            if self.encrypted
            else self.plain[index]
        )

    # --- what the origin publishes -------------------------------------------------------------

    def playlist_text(self) -> str:
        lines = ["#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{int(self.duration) + 1}"]
        if self.init is not None:
            lines.append('#EXT-X-MAP:URI="init.mp4"')
        if self.encrypted:
            iv = f",IV=0x{IV.hex()}" if self.explicit_iv else ""
            lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="key.bin"{iv}')
        offset = 0
        for index in range(self.count):
            lines.append(f"#EXTINF:{self.duration},")
            if self.byte_ranges:
                length = len(self._blobs[index])
                lines.append(f"#EXT-X-BYTERANGE:{length}@{offset}")
                lines.append(f"media.{self.ext}")
                offset += length
            else:
                lines.append(f"seg{index}.{self.ext}")
        if self.end_list:
            lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def playlist(self) -> MediaPlaylist:
        parsed = parse_playlist(self.playlist_text(), BASE + "index.m3u8")
        assert isinstance(parsed, MediaPlaylist)
        return parsed

    def expected(self) -> bytes:
        """What a correct download writes: the init section, then every segment in order."""
        return (self.init or b"") + b"".join(self.plain)

    def requested(self) -> list[int]:
        """Segment indexes requested so far, in order of arrival (a retry appears twice)."""
        return [int(name[3:].split(".")[0]) for name in self.log if name.startswith("seg")]

    # --- the transport handler -----------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.removeprefix("/v/")
        with self._lock:
            self.log.append(name)
            self.arrivals.setdefault(name, time.monotonic())
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            return self._respond(name, request)
        finally:
            with self._lock:
                self.active -= 1

    def _respond(self, name: str, request: httpx.Request) -> httpx.Response:
        if name in self.files:
            return httpx.Response(200, content=self.files[name])
        if name == "index.m3u8":
            return httpx.Response(200, content=self.playlist_text().encode())
        if name == "key.bin":
            with self._lock:
                self.key_requests += 1
            return httpx.Response(200, content=KEY)
        if name == "init.mp4" and self.init is not None:
            return httpx.Response(200, content=self.init)
        if name == f"media.{self.ext}" and self.byte_ranges:
            return self._ranged(request)
        if name.startswith("seg"):
            index = int(name[3:].split(".")[0])
            time.sleep(self.delays.get(index, 0))
            if index in self.fail:
                return httpx.Response(self.fail[index])
            with self._lock:
                remaining = self.flaky.get(index, 0)
                self.flaky[index] = max(0, remaining - 1)
            if remaining > 0:
                return httpx.Response(503)
            return httpx.Response(200, content=self._blobs[index])
        return httpx.Response(404)

    def _ranged(self, request: httpx.Request) -> httpx.Response:
        whole = b"".join(self._blobs)
        start, end = (int(v) for v in request.headers["range"].removeprefix("bytes=").split("-"))
        index = self._index_at(start)
        time.sleep(self.delays.get(index, 0))
        if index in self.fail:
            return httpx.Response(self.fail[index])
        with self._lock:
            self.log.append(f"seg{index}.{self.ext}")
        return httpx.Response(
            206,
            content=whole[start : end + 1],
            headers={"content-range": f"bytes {start}-{end}/{len(whole)}"},
        )

    def _index_at(self, offset: int) -> int:
        position = 0
        for index, blob in enumerate(self._blobs):
            if position <= offset < position + len(blob):
                return index
            position += len(blob)
        raise AssertionError(offset)


def client_for(
    origin: Origin, *, cancel: CancellationToken | None = None, **network: object
) -> HttpClient:
    """An ``HttpClient`` wired to ``origin`` with no retries, so failures are immediate."""
    settings: dict[str, object] = {"retries": 0}
    settings.update(network)
    return HttpClient(
        NetworkConfig(**settings),  # type: ignore[arg-type]
        origin_url=BASE,
        cancel=cancel,
        transport=httpx.MockTransport(origin),
    )


def worker_threads() -> Iterator[threading.Thread]:
    """Download worker threads that are still alive."""
    return (t for t in threading.enumerate() if t.name.startswith("rayohx-segment"))
