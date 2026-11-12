"""Smoke test of the built command line: it starts and answers. Needs ``cli.spec`` / ``ui.spec``."""

import subprocess
from pathlib import Path

import pytest

import rayohx
from tests.frozen import locate, skip_if_blocked

# cli.spec builds the command line alone; ui.spec builds it again beside the window.
FOLDERS = ["rayohx", "rayohx-ui"]


def _run(exe: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(exe), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
    except OSError as error:
        skip_if_blocked(error, exe)
        raise


@pytest.mark.parametrize("folder", FOLDERS)
def test_reports_its_version(folder: str) -> None:
    result = _run(locate(folder, "rayohx"), "--version")

    assert result.returncode == 0
    assert rayohx.__version__ in result.stdout


@pytest.mark.parametrize("folder", FOLDERS)
def test_shows_help_for_a_command(folder: str) -> None:
    result = _run(locate(folder, "rayohx"), "download", "--help")

    assert result.returncode == 0
    assert "--output" in result.stdout


@pytest.mark.parametrize("folder", FOLDERS)
def test_reports_errors_with_a_failing_exit_code(folder: str) -> None:
    result = _run(locate(folder, "rayohx"), "download", "not a url")

    assert result.returncode == 2
    assert "http" in result.stderr
