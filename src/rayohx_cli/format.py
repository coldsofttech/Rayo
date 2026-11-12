"""Turning numbers into short text for people."""

from rayohx.errors import format_bytes


def size(count: int | float) -> str:
    """Bytes as ``1.5 MiB``."""
    return format_bytes(int(count))


def rate(bytes_per_second: float) -> str:
    """A speed as ``4.2 MiB/s``."""
    return f"{format_bytes(int(bytes_per_second))}/s"


def bitrate(bits_per_second: int) -> str:
    """A stream's bit rate as ``2.8 Mbit/s``."""
    if bits_per_second >= 1_000_000:
        return f"{bits_per_second / 1_000_000:.1f} Mbit/s"
    return f"{round(bits_per_second / 1000)} kbit/s"


def duration(seconds: float | None) -> str:
    """A time span as ``45s``, ``3:07`` or ``1:02:03``; ``--`` when it is not known."""
    if seconds is None:
        return "--"
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    if minutes:
        return f"{minutes}:{secs:02d}"
    return f"{secs}s"
