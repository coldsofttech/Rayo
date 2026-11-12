"""Showing a download's events: a progress bar, plain lines, or nothing.

The library reports what happens as events. These reporters turn them into text, using the
error console (stderr) so that a command's result on stdout stays clean.
"""

import threading
from typing import Protocol

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.text import Text

from rayohx import DownloadEvent, DownloadProgress, EventType
from rayohx_cli import format as fmt

MODES = ("auto", "bar", "plain", "none")


class Reporter(Protocol):
    """Receives events from a running download."""

    def __call__(self, event: DownloadEvent) -> None: ...

    def start(self) -> None:
        """Called before the download starts."""
        ...

    def close(self) -> None:
        """Called after it ends, however it ended."""
        ...


def narrate(event: DownloadEvent, *, verbose: bool = False) -> list[str]:
    """What an event means to a person, as lines of text (usually none or one)."""
    kind = event.type
    data = event.data
    if kind is EventType.STREAM_SELECTED:
        parts = [str(data["resolution"])] if data.get("resolution") else []
        parts.append(fmt.bitrate(int(data["bandwidth"])))
        codecs = ", ".join(data.get("codecs") or [])
        lines = [f"Stream: {', '.join(parts)}" + (f" ({codecs})" if codecs else "")]
        lines += [f"Note: {note}" for note in data.get("notes", [])]
        return lines
    if kind is EventType.PLAYLIST_LOADED:
        if data.get("audio"):
            return [f"Audio track: {data['audio']}"]
        if data.get("subtitles"):
            return [f"Subtitles: {data['subtitles']}"]
        return [f"Loaded the {data.get('kind', '')} playlist"] if verbose else []
    if kind is EventType.FINALIZATION_STARTED:
        return ["Finalizing..."]
    if kind is EventType.SEGMENT_FAILED:
        return [f"Segment {event.segment_index} failed: {event.message}"]
    if kind is EventType.DOWNLOAD_STARTED and verbose:
        return [f"Downloading {event.message}"]
    return []


def _detail(progress: DownloadProgress) -> str:
    parts = [fmt.size(progress.downloaded_bytes)]
    if progress.speed > 0:
        parts.append(fmt.rate(progress.speed))
    if progress.eta_seconds is not None:
        parts.append(f"ETA {fmt.duration(progress.eta_seconds)}")
    return "  ".join(parts)


def _percent(progress: DownloadProgress) -> float:
    if progress.total_segments:
        return min(1.0, progress.completed_segments / progress.total_segments)
    return 0.0


class NullReporter:
    """Shows nothing."""

    def __call__(self, event: DownloadEvent) -> None:
        return None

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None


class PlainReporter:
    """One line per milestone, for logs and pipes: every 10 percent, and each step."""

    def __init__(self, console: Console, *, verbose: bool = False) -> None:
        self._console = console
        self._verbose = verbose
        self._last = -1
        self._lock = threading.Lock()

    def __call__(self, event: DownloadEvent) -> None:
        with self._lock:
            for line in narrate(event, verbose=self._verbose):
                self._console.print(Text(line), soft_wrap=True)
            if event.type is EventType.DOWNLOAD_PROGRESS and event.progress is not None:
                step = int(_percent(event.progress) * 10)
                if step > self._last:
                    self._last = step
                    self._console.print(
                        Text(f"{step * 10:3d}%  {_detail(event.progress)}"), soft_wrap=True
                    )

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None


class BarReporter:
    """A live progress bar with size, speed and time left."""

    def __init__(self, console: Console, *, verbose: bool = False) -> None:
        self._verbose = verbose
        self._console = console
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.fields[detail]}"),
            console=console,
            transient=True,
        )
        self._task = self._progress.add_task("Downloading", total=1.0, detail="")
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        with self._lock:
            self._progress.start()
            self._running = True

    def __call__(self, event: DownloadEvent) -> None:
        with self._lock:
            for line in narrate(event, verbose=self._verbose):
                self._progress.console.print(Text(line), soft_wrap=True)
            if event.type is EventType.DOWNLOAD_PROGRESS and event.progress is not None:
                self._progress.update(
                    self._task, completed=_percent(event.progress), detail=_detail(event.progress)
                )
            elif event.type is EventType.FINALIZATION_STARTED:
                self._progress.update(self._task, description="Finalizing", detail="")

    def close(self) -> None:
        with self._lock:
            if self._running:
                self._progress.stop()
                self._running = False


def make_reporter(
    mode: str, console: Console, *, quiet: bool, json_output: bool, verbose: bool
) -> Reporter:
    """Choose how to show progress. Quiet and JSON output show none."""
    if quiet or json_output or mode == "none":
        return NullReporter()
    if mode == "plain":
        return PlainReporter(console, verbose=verbose)
    if mode == "bar" or (mode == "auto" and console.is_terminal):
        return BarReporter(console, verbose=verbose)
    return PlainReporter(console, verbose=verbose)
