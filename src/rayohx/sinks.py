"""Where the ordered segments go.

A sink receives segments in playlist order, one at a time, from a single thread. ``FileSink`` writes
a ``.part`` file next to the output and renames it only when the download is complete, so a
half-finished download is never mistaken for a finished one. ``StreamSink`` hands bytes to any
function, for pipes and tests.
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import IO, Protocol

from rayohx.errors import OutputError, OutputExistsError
from rayohx.models import SegmentData
from rayohx.storage import translate_os_error

PART_SUFFIX = ".part"


class OutputSink(Protocol):
    """The receiving end of a download."""

    #: Whether an interrupted download can continue from the bytes already written.
    supports_resume: bool
    #: Whether the sink needs disk space for the output.
    writes_file: bool
    #: The final output path, when there is one.
    target: Path | None

    def preflight(self) -> None:
        """Fail early if the output cannot be written (for example, it already exists)."""
        ...

    def open(self, *, resume_offset: int | None = None) -> int:
        """Start writing; return the number of bytes already in place (0 for a fresh start)."""
        ...

    def write(self, data: SegmentData) -> int:
        """Write one segment (initialization section first) and return the bytes written."""
        ...

    def checkpoint(self) -> int:
        """Make everything written so far durable and return its size in bytes."""
        ...

    def commit(self) -> Path | None:
        """Finish successfully and return the final path, if any."""
        ...

    def abort(self, *, keep_partial: bool) -> None:
        """Stop without finishing. Keep the partial output only if asked."""
        ...


class FileSink:
    """Writes the output file safely."""

    supports_resume = True
    writes_file = True

    def __init__(self, path: Path | str, *, overwrite: bool = False) -> None:
        self.target: Path | None = Path(path).absolute()
        self._overwrite = overwrite
        self._handle: IO[bytes] | None = None
        self._length = 0

    @property
    def part_path(self) -> Path:
        """The file being written until the download completes."""
        assert self.target is not None  # noqa: S101 - set in __init__
        return self.target.with_name(self.target.name + PART_SUFFIX)

    def preflight(self) -> None:
        target = self._target()
        if target.is_dir():
            raise OutputError(f"The output path {target} is a folder.", hint="Give a file name.")
        if target.exists() and not self._overwrite:
            raise OutputExistsError(
                f"The output file already exists: {target}",
                hint="Choose another name or allow overwriting.",
            )

    def open(self, *, resume_offset: int | None = None) -> int:
        target = self._target()
        self.preflight()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            part = self.part_path
            if resume_offset and part.is_file() and part.stat().st_size >= resume_offset:
                self._handle = part.open("r+b")
                self._handle.truncate(resume_offset)
                self._handle.seek(resume_offset)
                self._length = resume_offset
            else:
                self._handle = part.open("wb")
                self._length = 0
        except OSError as exc:
            raise translate_os_error(exc, str(target)) from exc
        return self._length

    def write(self, data: SegmentData) -> int:
        handle = self._open_handle()
        try:
            if data.init:
                handle.write(data.init)
            handle.write(data.media)
        except OSError as exc:
            raise translate_os_error(exc, str(self.target)) from exc
        self._length += data.size
        return data.size

    def checkpoint(self) -> int:
        handle = self._open_handle()
        try:
            handle.flush()
            os.fsync(handle.fileno())
        except OSError as exc:
            raise translate_os_error(exc, str(self.target)) from exc
        return self._length

    def commit(self) -> Path:
        self.checkpoint()
        self._close()
        target = self._target()
        if target.exists() and not self._overwrite:
            raise OutputExistsError(f"The output file appeared while downloading: {target}")
        try:
            os.replace(self.part_path, target)
        except OSError as exc:
            raise translate_os_error(exc, str(target)) from exc
        return target

    def abort(self, *, keep_partial: bool) -> None:
        self._close()
        if not keep_partial:
            self.part_path.unlink(missing_ok=True)

    def _target(self) -> Path:
        assert self.target is not None  # noqa: S101 - set in __init__
        return self.target

    def _open_handle(self) -> IO[bytes]:
        if self._handle is None:
            raise OutputError("The output is not open.")
        return self._handle

    def _close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class StreamSink:
    """Passes the bytes to ``write``, for example a pipe to another program. It cannot resume."""

    supports_resume = False
    writes_file = False
    target: Path | None = None

    def __init__(self, write: Callable[[bytes], object]) -> None:
        self._write = write
        self._length = 0

    def preflight(self) -> None:
        return None

    def open(self, *, resume_offset: int | None = None) -> int:
        self._length = 0
        return 0

    def write(self, data: SegmentData) -> int:
        if data.init:
            self._write(data.init)
        self._write(data.media)
        self._length += data.size
        return data.size

    def checkpoint(self) -> int:
        return self._length

    def commit(self) -> Path | None:
        return None

    def abort(self, *, keep_partial: bool) -> None:
        return None
