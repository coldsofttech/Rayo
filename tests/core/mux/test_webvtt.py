import pytest

from rayohx import OutputFormatError
from rayohx.mux.webvtt import format_time, merge_segments, parse_segment

SEGMENT = """WEBVTT
X-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:00.000

NOTE a comment
that spans lines

STYLE
::cue { color: yellow }

1
00:00:01.000 --> 00:00:03.500 align:start position:10%
Hello
<i>world</i>

00:03.600 --> 00:05.000
Second cue
"""


def test_cues_are_read_with_their_text_and_settings() -> None:
    parsed = parse_segment(SEGMENT)
    assert [(c.start, c.end, c.text, c.identifier, c.settings) for c in parsed.cues] == [
        (11.0, 13.5, "Hello\n<i>world</i>", "1", "align:start position:10%"),
        (13.6, 15.0, "Second cue", "", ""),
    ]
    assert parsed.blocks == ["STYLE\n::cue { color: yellow }"]
    assert parsed.offset == 10.0


@pytest.mark.parametrize(
    ("line", "offset"),
    [
        ("X-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:00.000", 10.0),
        ("X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000", 10.0),
        ("X-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:05.000", 5.0),
        ("X-TIMESTAMP-MAP=MPEGTS:0,LOCAL:01:00:00.000", -3600.0),
        ("X-TIMESTAMP-MAP=garbage", 0.0),
        ("Language: en", 0.0),
    ],
)
def test_the_timestamp_map_ties_cues_to_the_video_clock(line: str, offset: float) -> None:
    assert parse_segment(f"WEBVTT\n{line}\n\n00:01.000 --> 00:02.000\nx\n").offset == offset


def test_windows_line_endings_and_a_byte_order_mark() -> None:
    text = "﻿WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\nline one\r\nline two\r\n"
    cue = parse_segment(text).cues[0]
    assert cue.text == "line one\nline two"


def test_hours_are_optional_and_large_values_are_understood() -> None:
    parsed = parse_segment("WEBVTT\n\n01:02:03.456 --> 100:00:00.000\nlong\n")
    assert (parsed.cues[0].start, parsed.cues[0].end) == (3723.456, 360000.0)


def test_a_file_that_is_not_webvtt_is_refused() -> None:
    with pytest.raises(OutputFormatError, match="not valid WebVTT"):
        parse_segment("<html>nope</html>")
    with pytest.raises(OutputFormatError):
        parse_segment("")


def test_blocks_without_timings_are_ignored() -> None:
    parsed = parse_segment(
        "WEBVTT\n\njust text\n\nid only\nstill no timing\n\n00:01.000 --> 00:02.000\nok\n"
    )
    assert [c.text for c in parsed.cues] == ["ok"]


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "00:00:00.000"),
        (1.5, "00:00:01.500"),
        (3723.456, "01:02:03.456"),
        (-4, "00:00:00.000"),
        (59.9996, "00:01:00.000"),
    ],
)
def test_times_are_written_with_milliseconds(seconds: float, text: str) -> None:
    assert format_time(seconds) == text


def test_segments_are_joined_without_repeated_cues() -> None:
    spanning = "00:00:01.000 --> 00:00:04.000\nspans the boundary\n"
    one = f"WEBVTT\n\n{spanning}\n00:00:02.000 --> 00:00:03.000\nfirst\n"
    two = f"WEBVTT\n\n{spanning}\n00:00:05.000 --> 00:00:06.000\nsecond\n"
    merged = merge_segments([one, two])
    assert merged.count("spans the boundary") == 1
    assert merged.index("first") < merged.index("second")
    assert merged.startswith("WEBVTT\n\n")
    assert merged.endswith("\n")


def test_times_are_shifted_to_match_the_output() -> None:
    merged = merge_segments([SEGMENT], shift=10.0)
    assert "00:00:01.000 --> 00:00:03.500 align:start position:10%" in merged
    assert "00:00:03.600 --> 00:00:05.000" in merged
    assert "STYLE\n::cue { color: yellow }" in merged


def test_cues_that_end_before_the_output_starts_are_dropped_and_others_clamped() -> None:
    text = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\ngone\n\n00:00:03.000 --> 00:00:06.000\npartly\n"
    )
    merged = merge_segments([text], shift=4.0)
    assert "gone" not in merged
    assert "00:00:00.000 --> 00:00:02.000\npartly" in merged


def test_identical_text_at_different_times_is_kept() -> None:
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsame\n\n00:00:05.000 --> 00:00:06.000\nsame\n"
    assert merge_segments([text]).count("same") == 2


def test_joining_nothing_gives_a_valid_empty_file() -> None:
    assert merge_segments([]) == "WEBVTT\n"
    assert merge_segments(["WEBVTT\n"]).startswith("WEBVTT")
