"""Helpers for reading the HLS fixture files."""

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "hls"
BASE = "https://media.example.com/vod/title/master.m3u8"


def fixture_text(name: str) -> str:
    """Return the text of ``tests/fixtures/hls/<name>``."""
    return (FIXTURES / name).read_text(encoding="utf-8")
