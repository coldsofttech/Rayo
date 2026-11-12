"""WebVTT subtitles: reading segments, aligning them with the video, and joining them.

HLS delivers subtitles as many small WebVTT files. Each has its own header and may repeat a cue that
spans a segment boundary. Cue times are local to the segment unless an ``X-TIMESTAMP-MAP`` line ties
them to the video's clock (in 90 kHz ticks). Joining them means mapping every cue onto one timeline,
shifting it to match the output file, and dropping the repeats.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from rayohx.errors import OutputFormatError

_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})\.(\d{3})"
_TIMING = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}(.*)$")
_MPEGTS = re.compile(r"MPEGTS:(\d+)")
_LOCAL = re.compile(rf"LOCAL:{_TIME}")


@dataclass(frozen=True, slots=True)
class Cue:
    """One subtitle: when it shows, and what it says."""

    start: float
    end: float
    text: str
    identifier: str = ""
    settings: str = ""


@dataclass(slots=True)
class VttSegment:
    """One parsed WebVTT file."""

    cues: list[Cue] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)  # STYLE and REGION blocks, kept as written
    #: Seconds to add to the cue times to get media time (from ``X-TIMESTAMP-MAP``).
    offset: float = 0.0


def _seconds(hours: str | None, minutes: str, seconds: str, millis: str) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def format_time(seconds: float) -> str:
    """Format as ``HH:MM:SS.mmm``."""
    total = round(max(0.0, seconds) * 1000)
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    return f"{hours:02d}:{minutes:02d}:{remainder // 1000:02d}.{remainder % 1000:03d}"


def parse_segment(text: str) -> VttSegment:
    """Read one WebVTT file.

    Raises:
        OutputFormatError: If it does not start with ``WEBVTT``.
    """
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or not lines[0].startswith("WEBVTT"):
        raise OutputFormatError("A subtitle segment is not valid WebVTT.")
    segment = VttSegment()
    index = 1
    while index < len(lines) and lines[index].strip():
        segment.offset = _timestamp_map(lines[index], segment.offset)
        index += 1
    block: list[str] = []
    for line in [*lines[index:], ""]:
        if line.strip():
            block.append(line)
            continue
        if block:
            _add_block(segment, block)
        block = []
    return segment


def _timestamp_map(line: str, current: float) -> float:
    if not line.startswith("X-TIMESTAMP-MAP"):
        return current
    ticks = _MPEGTS.search(line)
    local = _LOCAL.search(line)
    if ticks is None or local is None:
        return current
    return int(ticks.group(1)) / 90000 - _seconds(*local.groups())


def _add_block(segment: VttSegment, block: list[str]) -> None:
    first = block[0]
    if first.startswith("NOTE"):
        return
    if first.startswith(("STYLE", "REGION")):
        segment.blocks.append("\n".join(block))
        return
    timing_at = 0 if "-->" in first else 1
    if timing_at >= len(block):
        return
    match = _TIMING.match(block[timing_at])
    if match is None:
        return
    values = match.groups()
    start = _seconds(*values[0:4]) + segment.offset
    end = _seconds(*values[4:8]) + segment.offset
    segment.cues.append(
        Cue(
            start,
            end,
            "\n".join(block[timing_at + 1 :]),
            identifier=block[0] if timing_at else "",
            settings=values[8].strip(),
        )
    )


def merge_segments(segments: Sequence[str], shift: float = 0.0) -> str:
    """Join WebVTT segments into one file.

    ``shift`` seconds are taken off every time so the subtitles line up with an output whose
    timeline starts that much later than the stream's. Repeated cues appear once, and cues that
    end before the start of the output are dropped.
    """
    cues: dict[tuple[float, float, str, str], Cue] = {}
    blocks: list[str] = []
    for text in segments:
        parsed = parse_segment(text)
        for block in parsed.blocks:
            if block not in blocks:
                blocks.append(block)
        for cue in parsed.cues:
            start, end = cue.start - shift, cue.end - shift
            if end <= 0:
                continue
            key = (round(start, 3), round(end, 3), cue.text, cue.settings)
            cues.setdefault(key, Cue(max(0.0, start), end, cue.text, cue.identifier, cue.settings))
    lines = ["WEBVTT", ""]
    for block in blocks:
        lines += [block, ""]
    for cue in sorted(cues.values(), key=lambda item: (item.start, item.end)):
        if cue.identifier:
            lines.append(cue.identifier)
        timing = f"{format_time(cue.start)} --> {format_time(cue.end)}"
        lines.append(f"{timing} {cue.settings}".rstrip())
        lines += [cue.text, ""]
    return "\n".join(lines)
