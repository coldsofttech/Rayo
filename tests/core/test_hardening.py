"""Hostile and damaged input: the engine fails with its own clear errors, never a crash.

Every case is deterministic (fixed seeds), so a failure can be reproduced.
"""

import contextlib
import os
import random
import string
from pathlib import Path

import pytest

from rayohx import ContainerFormat, OutputFormat, RayoHXError, parse_playlist, plan_output
from rayohx.mux import webvtt
from rayohx.mux.fmp4_source import Fmp4Source
from rayohx.mux.muxer import Mp4Muxer
from rayohx.mux.source import probe_streams
from rayohx.output import name_from_url, sanitize_filename
from tests.core.mux.helpers import media, mux_ts

SEEDS = range(int(os.environ.get("RAYOHX_FUZZ_SEEDS", "12")))


def damage(data: bytes, seed: int) -> bytes:
    """Flip, drop or insert bytes, or cut the data short."""
    rng = random.Random(seed)  # noqa: S311 - repeatable test data, not security
    out = bytearray(data)
    kind = seed % 4
    if kind == 0:  # flip bytes
        for _ in range(max(1, len(out) // 200)):
            out[rng.randrange(len(out))] = rng.randrange(256)
    elif kind == 1:  # cut it short
        del out[rng.randrange(1, len(out)) :]
    elif kind == 2:  # remove a middle part
        start = rng.randrange(len(out))
        del out[start : start + rng.randrange(1, 3000)]
    else:  # insert noise
        start = rng.randrange(len(out))
        out[start:start] = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 500)))
    return bytes(out)


# --- playlists -----------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_damaged_playlists_are_parsed_or_refused_cleanly(seed: int) -> None:
    base = '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n' + "".join(
        f"#EXTINF:4.0,\ns{i}.ts\n" for i in range(5)
    )
    raw = damage(base.encode(), seed).decode("utf-8", errors="replace")
    with contextlib.suppress(RayoHXError):
        parse_playlist(raw, "https://h/p.m3u8")


@pytest.mark.parametrize("seed", SEEDS)
def test_random_text_is_never_a_crash(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - repeatable test data, not security
    alphabet = string.printable + '#:=,"@'
    lines = ["#EXTM3U"] + [
        "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 60)))
        for _ in range(rng.randrange(1, 30))
    ]
    with contextlib.suppress(RayoHXError):
        parse_playlist("\n".join(lines), "https://h/p.m3u8")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\x00\x00\x00",
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=abc\nv.m3u8\n",
        "#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=0x0,BANDWIDTH=1\nv.m3u8\n",
        "#EXTM3U\n#EXTINF:abc,\ns.ts\n",
        "#EXTM3U\n#EXTINF:1,\n" + "a" * 100_000 + "\n#EXT-X-ENDLIST\n",
        "#EXTM3U\n#EXT-X-BYTERANGE:99999999999999999999@1\n#EXTINF:1,\ns.ts\n",
        '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k",IV=0xZZ\n#EXTINF:1,\ns.ts\n',
        "#EXTM3U\n#EXT-X-MAP:URI=\n#EXTINF:1,\ns.ts\n",
    ],
)
def test_odd_playlists(text: str) -> None:
    with contextlib.suppress(RayoHXError):
        parse_playlist(text, "https://h/p.m3u8")


# --- media ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["h264_aac.ts", "hevc_aac.ts", "h264_ac3.ts", "h264_mp3.ts"])
@pytest.mark.parametrize("seed", SEEDS)
def test_damaged_transport_streams(name: str, seed: int) -> None:
    data = damage(media(name), seed)
    with contextlib.suppress(RayoHXError):
        mux_ts(data, chunk=7_000)
    with contextlib.suppress(RayoHXError):
        probe_streams(data)


@pytest.mark.parametrize("seed", SEEDS)
def test_damaged_fragmented_mp4(seed: int) -> None:
    data = damage(media("h264_only_frag.mp4"), seed)
    output = bytearray()
    muxer = Mp4Muxer(output.extend)
    with contextlib.suppress(RayoHXError):
        source = Fmp4Source(muxer)
        for start in range(0, len(data), 5_000):
            source.feed(data[start : start + 5_000])
        source.finish()
        muxer.close()


@pytest.mark.parametrize("seed", SEEDS)
def test_random_bytes_are_not_media(seed: int) -> None:
    data = random.Random(seed).randbytes(20_000)  # noqa: S311 - repeatable test data
    with contextlib.suppress(RayoHXError):
        mux_ts(data)


@pytest.mark.parametrize("seed", SEEDS)
def test_damaged_subtitles(seed: int) -> None:
    text = "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:00.000\n\n" + "".join(
        f"{i}\n00:00:0{i}.000 --> 00:00:0{i}.500\nline {i}\n\n" for i in range(1, 6)
    )
    raw = damage(text.encode(), seed).decode("utf-8", errors="replace")
    with contextlib.suppress(RayoHXError):
        webvtt.merge_segments([raw, raw])


# --- names and paths -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://h/../../etc/passwd",
        "https://h/%2e%2e/%2e%2e/secret.m3u8",
        "https://h/a/..%2f..%2fboot.m3u8",
        "https://h/C:\\Windows\\system32\\evil.m3u8",
        "https://h/con.m3u8",
        "https://h/NUL",
        "https://h/.hidden.m3u8",
        "https://h/" + "x" * 5000 + ".m3u8",
        "https://h/a%00b.m3u8",
        "https://h/%E2%80%AEgpj.m3u8",
    ],
)
def test_names_from_addresses_cannot_escape(url: str, tmp_path: Path) -> None:
    name = name_from_url(url)
    assert name
    assert "/" not in name
    assert "\\" not in name
    assert "\x00" not in name
    assert not name.startswith(".")
    assert len(name) <= 255
    target = (tmp_path / f"{name}.mp4").resolve()
    assert target.parent == tmp_path.resolve()


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", "..", ".", "", " ", "CON", "aux.txt"])
def test_sanitized_names_are_plain_file_names(name: str) -> None:
    cleaned = sanitize_filename(name)
    assert cleaned
    assert "/" not in cleaned
    assert "\\" not in cleaned
    assert cleaned not in {".", ".."}


def test_the_default_output_stays_in_the_chosen_folder(tmp_path: Path) -> None:
    plan = plan_output(
        path=None,
        requested=OutputFormat.AUTO,
        container=ContainerFormat.MPEG_TS,
        url="https://h/%2e%2e/%2e%2e/evil.m3u8",
        directory=tmp_path,
    )
    assert plan.path.resolve().parent == tmp_path.resolve()
