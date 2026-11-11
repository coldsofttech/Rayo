"""Smoke test of the built command line: it starts and answers. Needs ``cli.spec`` / ``ui.spec``."""

import json
import subprocess
from pathlib import Path

import pytest

import example_package
from tests.frozen import locate, skip_if_blocked

# cli.spec builds the command line alone; ui.spec builds it again beside the window.
FOLDERS = ["example-cli", "example-ui"]


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
    result = _run(locate(folder, "example-cli"), "--version")

    assert result.returncode == 0
    assert example_package.__version__ in result.stdout


@pytest.mark.parametrize("folder", FOLDERS)
def test_runs_a_command(folder: str) -> None:
    exe = locate(folder, "example-cli")

    greeting = _run(exe, "hello", "Ada")
    details = _run(exe, "info", "--json")

    assert greeting.stdout.strip() == "Hello, Ada!"
    assert json.loads(details.stdout)["version"] == example_package.__version__


@pytest.mark.parametrize("folder", FOLDERS)
def test_reports_errors_with_a_failing_exit_code(folder: str) -> None:
    result = _run(locate(folder, "example-cli"), "hello", " ")

    assert result.returncode == 1
    assert "empty" in result.stderr
