"""Smoke test of the built window program, started without a window. Needs ``ui.spec``."""

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import example_package
from tests.frozen import locate, skip_if_blocked

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the UI is built on Windows only")

STARTUP_SECONDS = 30


@pytest.fixture
def address(tmp_path: Path) -> Iterator[str]:
    """Start ``example-ui --no-window`` and return its first URL; stop it afterwards."""
    exe = locate("example-ui", "example-ui")
    out = tmp_path / "out.txt"
    with out.open("w", encoding="utf-8") as stdout:
        try:
            process = subprocess.Popen([str(exe), "--no-window"], stdout=stdout, stderr=stdout)
        except OSError as error:
            skip_if_blocked(error, exe)
            raise
        try:
            deadline = time.monotonic() + STARTUP_SECONDS
            while time.monotonic() < deadline:
                lines = out.read_text(encoding="utf-8").splitlines()
                if lines:
                    yield str(json.loads(lines[0])["url"])
                    return
                if process.poll() is not None:
                    pytest.fail(f"example-ui exited early: {out.read_text(encoding='utf-8')}")
                time.sleep(0.2)
            pytest.fail("example-ui did not report an address in time")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def test_serves_the_page_the_api_and_the_native_action(address: str) -> None:
    base = address.split("/?", 1)[0]
    with httpx.Client(follow_redirects=True) as client:
        page = client.get(address)
        assert page.status_code == 200
        assert '<div id="root">' in page.text

        hello = client.get(f"{base}/api/v1/hello/Ada")
        assert hello.json() == {"message": "Hello, Ada!"}

        info = client.post(f"{base}/api/bridge/app_info", json={}, headers={"Origin": base})
        assert info.json()["result"]["version"] == example_package.__version__


def test_refuses_requests_without_the_session(address: str) -> None:
    base = address.split("/?", 1)[0]

    assert httpx.get(f"{base}/api/v1/hello/Ada").status_code in (401, 403)
