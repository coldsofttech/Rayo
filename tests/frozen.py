"""Finding the executables PyInstaller built, for the smoke tests in the integration folders.

The tests need ``installer/pyinstaller/cli.spec`` and ``ui.spec`` to have run first (see the specs
for the commands). Set ``RAYOHX_INSTALLER_DIR`` to test a build somewhere other than
``build/installer``. Without a build the tests are skipped.
"""

import os
import sys
from pathlib import Path

import pytest

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "build" / "installer"
SUFFIX = ".exe" if sys.platform == "win32" else ""


def blocked(error: OSError) -> bool:
    """Whether the system refused to start a program (permission denied, or Windows application
    control, error 4551), which says nothing about the build itself."""
    return isinstance(error, PermissionError) or getattr(error, "winerror", None) == 4551


def skip_if_blocked(error: OSError, exe: Path) -> None:
    """Skip the test if the system blocked ``exe``; otherwise raise the error again."""
    if not blocked(error):
        raise error
    pytest.skip(f"{exe} was blocked by the system (for example Windows application control)")


def locate(folder: str, script: str) -> Path:
    """Return ``<build dir>/<folder>/<script>``, or skip the test if it was not built."""
    root = Path(os.environ.get("RAYOHX_INSTALLER_DIR", DEFAULT_DIR))
    exe = root / folder / f"{script}{SUFFIX}"
    if not exe.is_file():
        pytest.skip(f"{exe} is not built; see installer/pyinstaller/*.spec")
    return exe
