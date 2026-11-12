"""The working folder of one download: resume state, staged segments and a lock.

The folder name is derived from the download itself (a hash), never from user input, so a hostile
address cannot point it elsewhere. The same download finds the same folder again, which is what
makes resuming possible.
"""

import hashlib
import os
import shutil
import sys
from pathlib import Path
from types import TracebackType
from typing import IO, Self
from urllib.parse import urlsplit

from rayohx.errors import WorkspaceLockedError

STATE_FILE = "state.json"
SEGMENTS_DIR = "segments"
LOCK_FILE = "lock"
FOLDER_PREFIX = ".rayohx-"


def workspace_key(url: str, *parts: str) -> str:
    """A short stable name for a download, ignoring query strings so signed links still match."""
    split = urlsplit(url)
    identity = "|".join([f"{split.scheme}://{split.netloc.lower()}{split.path}", *parts])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def resolve_workspace_root(workspace_dir: Path | None, output: Path | None, key: str) -> Path:
    """Where the working folder for ``key`` lives.

    With ``workspace_dir`` set it is ``<workspace_dir>/<key>``. Otherwise it sits beside the
    output file, so finishing is a rename within one disk.
    """
    if workspace_dir is not None:
        return Path(workspace_dir) / key
    base = Path(output).resolve().parent if output is not None else Path.cwd()
    return base / f"{FOLDER_PREFIX}{key}"


class _FileLock:
    """An exclusive, non-blocking lock on a file, released when the process ends."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        handle = self._path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None


class Workspace:
    """A private working folder. Use it as a context manager to hold its lock."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = _FileLock(self.path / LOCK_FILE)

    @property
    def state_path(self) -> Path:
        """The resume state file."""
        return self.path / STATE_FILE

    @property
    def segments_path(self) -> Path:
        """The folder of staged segments."""
        return self.path / SEGMENTS_DIR

    def exists(self) -> bool:
        """Whether the folder is already there (a previous run left it)."""
        return self.path.is_dir()

    def create(self) -> None:
        """Create the folder, readable only by the current user where the system allows."""
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.segments_path.mkdir(exist_ok=True, mode=0o700)

    def __enter__(self) -> Self:
        self.create()
        if not self._lock.acquire():
            raise WorkspaceLockedError(
                f"Another download is already using {self.path}.",
                hint="Wait for it to finish, or remove the folder if no download is running.",
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._lock.release()

    def reset(self) -> None:
        """Forget any earlier run's state and staged segments, keeping the lock."""
        _unlink(self.state_path)
        shutil.rmtree(self.segments_path, ignore_errors=True)
        self.segments_path.mkdir(exist_ok=True, mode=0o700)

    def clean(self, *, keep_state: bool, keep_segments: bool) -> None:
        """Remove what is not needed any more.

        With both ``keep_*`` flags off the whole folder goes. Otherwise only the kept parts
        remain, so an unfinished download holds just what resuming needs.
        """
        if not self.path.is_dir():
            return
        if not keep_state and not keep_segments:
            shutil.rmtree(self.path, ignore_errors=True)
            return
        if not keep_state:
            _unlink(self.state_path)
        if not keep_segments:
            shutil.rmtree(self.segments_path, ignore_errors=True)
        _unlink(self.path / LOCK_FILE)
        # An empty folder is just clutter.
        if not any(self.path.iterdir()):
            self.path.rmdir()


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except PermissionError:  # another process may still hold the lock file on Windows
        if os.name != "nt":
            raise
