"""Where the ordered segments go.

A sink receives segments in playlist order, one at a time, from a single thread. ``FileSink`` writes
a ``.part`` file next to the output and renames it only when the download is complete, so a
half-finished download is never mistaken for a finished one. ``StreamSink`` hands bytes to any
function, for pipes and tests.
"""

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import IO, Protocol

from rayohx.enums import ContainerFormat
from rayohx.errors import OutputError, OutputExistsError
from rayohx.models import SegmentData
from rayohx.output import segment_file_name
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

    def commit(self, validate: Callable[[Path], None] | None = None) -> Path | None:
        """Finish successfully and return the final path, if any.

        ``validate`` is called with the finished data before it is put in place, and may raise
        to refuse it.
        """
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

    def commit(self, validate: Callable[[Path], None] | None = None) -> Path:
        self.checkpoint()
        self._close()
        if validate is not None:
            validate(self.part_path)
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

    def commit(self, validate: Callable[[Path], None] | None = None) -> Path | None:
        return None

    def abort(self, *, keep_partial: bool) -> None:
        return None


class DirectorySink:
    """Saves every segment as its own file in a folder, for people who join them another way.

    Files are written into ``<folder>.part`` and the folder is renamed only when the download is
    complete. This sink cannot resume.
    """

    supports_resume = False
    writes_file = True

    def __init__(
        self, folder: Path | str, container: ContainerFormat, *, overwrite: bool = False
    ) -> None:
        self.target: Path | None = Path(folder).absolute()
        self._container = container
        self._overwrite = overwrite
        self._written = 0

    @property
    def part_path(self) -> Path:
        """The folder being filled until the download completes."""
        assert self.target is not None  # noqa: S101 - set in __init__
        return self.target.with_name(self.target.name + PART_SUFFIX)

    def preflight(self) -> None:
        target = self._target()
        if target.is_file():
            raise OutputError(f"The output path {target} is a file.", hint="Give a folder name.")
        if target.exists() and any(target.iterdir()) and not self._overwrite:
            raise OutputExistsError(
                f"The output folder is not empty: {target}",
                hint="Choose another folder or allow overwriting.",
            )

    def open(self, *, resume_offset: int | None = None) -> int:
        self.preflight()
        try:
            shutil.rmtree(self.part_path, ignore_errors=True)
            self.part_path.mkdir(parents=True)
        except OSError as exc:
            raise translate_os_error(exc, str(self.target)) from exc
        self._written = 0
        return 0

    def write(self, data: SegmentData) -> int:
        try:
            if data.init:
                (
                    self.part_path / segment_file_name(data.index, self._container, init=True)
                ).write_bytes(data.init)
            (self.part_path / segment_file_name(data.index, self._container)).write_bytes(
                data.media
            )
        except OSError as exc:
            raise translate_os_error(exc, str(self.part_path)) from exc
        self._written += data.size
        return data.size

    def checkpoint(self) -> int:
        return self._written

    def commit(self, validate: Callable[[Path], None] | None = None) -> Path:
        # Each segment was checked as it arrived, so there is no single file to validate.
        target = self._target()
        try:
            if target.exists():
                if not self._overwrite:
                    raise OutputExistsError(
                        f"The output folder appeared while downloading: {target}"
                    )
                shutil.rmtree(target)
            os.replace(self.part_path, target)
        except OSError as exc:
            raise translate_os_error(exc, str(target)) from exc
        return target

    def abort(self, *, keep_partial: bool) -> None:
        shutil.rmtree(self.part_path, ignore_errors=True)

    def _target(self) -> Path:
        assert self.target is not None  # noqa: S101 - set in __init__
        return self.target
