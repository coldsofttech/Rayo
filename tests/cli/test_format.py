import pytest

from rayohx_cli import format as fmt


def test_sizes_and_rates() -> None:
    assert fmt.size(0) == "0 B"
    assert fmt.size(1536) == "1.5 KiB"
    assert fmt.size(1536.7) == "1.5 KiB"
    assert fmt.rate(2 * 1024**2) == "2.0 MiB/s"


@pytest.mark.parametrize(
    ("bits", "text"),
    [
        (64_000, "64 kbit/s"),
        (217_800, "218 kbit/s"),
        (2_800_000, "2.8 Mbit/s"),
        (999_999, "1000 kbit/s"),
    ],
)
def test_bit_rates(bits: int, text: str) -> None:
    assert fmt.bitrate(bits) == text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (None, "--"),
        (0, "0s"),
        (-5, "0s"),
        (45.4, "45s"),
        (59.6, "1:00"),
        (187, "3:07"),
        (3723, "1:02:03"),
    ],
)
def test_durations(seconds: float | None, text: str) -> None:
    assert fmt.duration(seconds) == text
