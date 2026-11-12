"""Helpers for the muxer tests: real fixtures, a synthetic MPEG-TS builder and an FFmpeg oracle."""

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from rayohx.mux.model import TrackConfig
from rayohx.mux.mp4read import iter_boxes
from rayohx.mux.muxer import Mp4Muxer
from rayohx.mux.source import TsSource

MEDIA = Path(__file__).resolve().parent.parent.parent / "fixtures" / "media"

HAS_FFPROBE = shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(
    not HAS_FFPROBE,
    reason="ffmpeg and ffprobe are used only as a test oracle and are not installed",
)


def media(name: str) -> bytes:
    """The bytes of a fixture in ``tests/fixtures/media``."""
    return (MEDIA / name).read_bytes()


def mux_ts(data: bytes, chunk: int = 50_000) -> tuple[bytes, Mp4Muxer]:
    """Remux transport stream data into a fragmented MP4, fed in pieces of ``chunk`` bytes."""
    output = bytearray()
    muxer = Mp4Muxer(output.extend)
    source = TsSource(muxer)
    for start in range(0, len(data), chunk):
        source.feed(data[start : start + chunk])
    source.finish()
    muxer.close()
    return bytes(output), muxer


def top_level(data: bytes) -> list[bytes]:
    """The types of the top-level boxes of an MP4."""
    return [kind for kind, _, _, _ in iter_boxes(data)]


# --- a bit writer for hand-made headers --------------------------------------------------------


class BitWriter:
    """Builds bit-packed data, for crafting video headers."""

    def __init__(self) -> None:
        self._bits = ""

    def bits(self, value: int, count: int) -> "BitWriter":
        self._bits += format(value, f"0{count}b") if count else ""
        return self

    def flag(self, value: bool) -> "BitWriter":
        return self.bits(int(value), 1)

    def ue(self, value: int) -> "BitWriter":
        number = value + 1
        length = number.bit_length()
        self._bits += "0" * (length - 1) + format(number, "b")
        return self

    def se(self, value: int) -> "BitWriter":
        return self.ue(2 * value - 1 if value > 0 else -2 * value)

    def to_bytes(self, *, trailing: bool = True) -> bytes:
        bits = self._bits + ("1" if trailing else "")
        bits += "0" * (-len(bits) % 8)
        return int(bits, 2).to_bytes(len(bits) // 8, "big") if bits else b""


def escape_nal(data: bytes) -> bytes:
    """Insert emulation-prevention bytes, as an encoder does."""
    out = bytearray()
    zeros = 0
    for byte in data:
        if zeros >= 2 and byte <= 3:
            out.append(3)
            zeros = 0
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


# --- a synthetic MPEG-TS builder ---------------------------------------------------------------


def _timestamp(marker: int, value: int) -> bytes:
    return bytes(
        [
            (marker << 4) | (((value >> 30) & 7) << 1) | 1,
            (value >> 22) & 0xFF,
            (((value >> 15) & 0x7F) << 1) | 1,
            (value >> 7) & 0xFF,
            ((value & 0x7F) << 1) | 1,
        ]
    )


def pes(stream_id: int, payload: bytes, pts: int | None, dts: int | None = None) -> bytes:
    """A PES packet with optional timestamps (in 90 kHz ticks)."""
    flags = 0
    header = b""
    if pts is not None:
        flags = 0x80
        header = _timestamp(3 if dts is not None else 2, pts)
        if dts is not None:
            flags = 0xC0
            header += _timestamp(1, dts)
    return (
        b"\x00\x00\x01"
        + bytes([stream_id])
        + b"\x00\x00"
        + bytes([0x80, flags, len(header)])
        + header
        + payload
    )


def ts_packets(pid: int, payload: bytes, *, start: bool = True, counter: int = 0) -> bytes:
    """Cut ``payload`` into 188-byte transport packets, padding the last with stuffing."""
    packets = bytearray()
    position = 0
    first = start
    while position < len(payload) or first:
        room = 184
        chunk = payload[position : position + room]
        position += len(chunk)
        header = bytes([0x47, (0x40 if first else 0) | (pid >> 8), pid & 0xFF])
        if len(chunk) < room:
            stuffing = room - len(chunk)
            adaptation = (
                bytes([0x30 | (counter & 0xF)])
                + bytes([stuffing - 1])
                + (b"\x00" + b"\xff" * (stuffing - 2) if stuffing >= 2 else b"")
            )
            packets += header + adaptation + chunk
        else:
            packets += header + bytes([0x10 | (counter & 0xF)]) + chunk
        counter += 1
        first = False
    return bytes(packets)


def _section(table_id: int, body: bytes) -> bytes:
    length = len(body) + 4 + 5
    head = bytes([table_id, 0xB0 | (length >> 8), length & 0xFF]) + body[:0]
    return head


def pat(program: int = 1, pmt_pid: int = 0x100) -> bytes:
    """A program association table packet."""
    body = (
        bytes([0x00, 0x01, 0xC1, 0x00, 0x00])
        + program.to_bytes(2, "big")
        + bytes([0xE0 | (pmt_pid >> 8), pmt_pid & 0xFF])
    )
    section = bytes([0x00, 0xB0, len(body) + 4]) + body + b"\x00\x00\x00\x00"
    return ts_packets(0, b"\x00" + section)


def pmt(streams: list[tuple[int, int, bytes]], pmt_pid: int = 0x100, program: int = 1) -> bytes:
    """A program map table packet. Each stream is ``(stream_type, pid, descriptors)``."""
    loop = b"".join(
        bytes([kind, 0xE0 | (pid >> 8), pid & 0xFF, 0xF0 | (len(desc) >> 8), len(desc) & 0xFF])
        + desc
        for kind, pid, desc in streams
    )
    body = program.to_bytes(2, "big") + bytes([0xC1, 0x00, 0x00, 0xE1, 0x00, 0xF0, 0x00]) + loop
    section = bytes([0x02, 0xB0 | ((len(body) + 4) >> 8), (len(body) + 4) & 0xFF]) + body
    return ts_packets(pmt_pid, b"\x00" + section + b"\x00\x00\x00\x00")


# --- the FFmpeg oracle -------------------------------------------------------------------------


def probe(path: Path) -> dict[str, list[dict[str, str]]]:
    """Streams of a file as reported by ``ffprobe``, grouped by type."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    streams: dict[str, list[dict[str, str]]] = {"video": [], "audio": []}
    for stream in json.loads(result.stdout)["streams"]:
        streams.setdefault(stream["codec_type"], []).append(stream)
    return streams


def decodes_cleanly(path: Path) -> bool:
    """Whether ``ffmpeg`` can decode every frame of the file without errors."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and not result.stderr.strip()


def decoded(path: Path, kind: str) -> tuple[int, str]:
    """Decoded frame count and hash (video), or the hash of the decoded samples (audio)."""
    if kind == "video":
        result = subprocess.run(
            [  # noqa: S607
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v",
                "-f",
                "framemd5",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = [
            line.split(",")[-1].strip()
            for line in result.stdout.splitlines()
            if not line.startswith("#")
        ]
        return len(lines), "".join(lines)
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-f", "md5", "-"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return 0, result.stdout.strip()


def config(kind: str = "video") -> TrackConfig:
    """A minimal track description for tests that do not look inside it."""
    return TrackConfig(kind, 1000, b"\x00\x00\x00\x08test", "test", width=16, height=16)


def keep(iterator: Iterator[object]) -> list[object]:
    """Drain an iterator."""
    return list(iterator)


# --- reading MP4 files back --------------------------------------------------------------------


def read_fragments(data: bytes) -> list[dict[str, int | list[int]]]:
    """Each ``moof`` of an MP4 as a dictionary: track, sequence, decode time and sample facts."""
    import struct

    from rayohx.mux.mp4read import find_box

    found: list[dict[str, int | list[int]]] = []
    for kind, offset, size, _ in iter_boxes(data):
        if kind != b"moof":
            continue
        mfhd = find_box(data, (b"mfhd",), offset + 8, offset + size)
        traf = find_box(data, (b"traf",), offset + 8, offset + size)
        assert mfhd is not None
        assert traf is not None
        inner = (traf[0] + 8, traf[0] + traf[1])
        tfhd = find_box(data, (b"tfhd",), *inner)
        tfdt = find_box(data, (b"tfdt",), *inner)
        trun = find_box(data, (b"trun",), *inner)
        assert tfhd is not None
        assert tfdt is not None
        assert trun is not None
        version = data[tfdt[0] + 8]
        base = struct.unpack_from(">Q" if version else ">I", data, tfdt[0] + 12)[0]
        count = struct.unpack_from(">I", data, trun[0] + 12)[0]
        entries = trun[0] + 20
        durations = [
            struct.unpack_from(">I", data, entries + index * 16)[0] for index in range(count)
        ]
        found.append(
            {
                "track": struct.unpack_from(">I", data, tfhd[0] + 12)[0],
                "sequence": struct.unpack_from(">I", data, mfhd[0] + 12)[0],
                "start": base,
                "count": count,
                "durations": durations,
                "moof_offset": offset,
                "data_offset": struct.unpack_from(">i", data, trun[0] + 16)[0],
            }
        )
    return found


# --- a fake website serving files from memory --------------------------------------------------


class Site:
    """An ``httpx`` mock transport handler that serves bytes by path and logs the requests."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.log: list[str] = []
        self.fail: set[str] = set()

    def __call__(self, request):  # type: ignore[no-untyped-def]
        import httpx

        path = request.url.path.lstrip("/")
        self.log.append(path)
        if path in self.fail:
            return httpx.Response(500)
        if path in self.files:
            return httpx.Response(200, content=self.files[path])
        return httpx.Response(404)


def hls_site(kind: str, prefix: str) -> Site:
    """A site serving one of the generated HLS sets under ``prefix/``."""
    folder = MEDIA / f"hls_{kind}"
    return Site({f"{prefix}/{item.name}": item.read_bytes() for item in folder.iterdir()})


def single_file_site(name: str, prefix: str = "show", parts: int = 3) -> Site:
    """A playlist of one real transport stream cut into ``parts`` segments at packet boundaries."""
    data = media(name)
    packets = len(data) // 188
    bounds = [(packets * i // parts) * 188 for i in range(parts)] + [packets * 188]
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", "#EXT-X-PLAYLIST-TYPE:VOD"]
    files = {}
    for index in range(parts):
        lines += ["#EXTINF:1.3,", f"seg{index}.ts"]
        files[f"{prefix}/seg{index}.ts"] = data[bounds[index] : bounds[index + 1]]
    lines.append("#EXT-X-ENDLIST")
    files[f"{prefix}/index.m3u8"] = ("\n".join(lines) + "\n").encode()
    return Site(files)
