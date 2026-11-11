"""Starts the real Cornea server (no window) against the built frontend. Windows only."""

import sys
from pathlib import Path

import httpx
import pytest

from rayo_ui.app import build_shell

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "win32", reason="Cornea Shell is Windows only"),
]

DIST = Path(__file__).parents[3] / "src" / "rayo_ui" / "ui" / "dist"


@pytest.mark.skipif(not DIST.is_dir(), reason="build the frontend first: npm ci && npm run build")
def test_page_api_and_native_action_work_through_cornea() -> None:
    with build_shell(window=False) as shell, httpx.Client(follow_redirects=True) as client:
        page = client.get(shell.url)
        assert page.status_code == 200
        assert '<div id="root">' in page.text

        hello = client.get(f"{shell.base_url}/api/v1/hello/Ada")
        assert hello.json() == {"message": "Hello, Ada!"}

        info = client.post(
            f"{shell.base_url}/api/bridge/app_info",
            json={},
            headers={"Origin": shell.base_url},  # Cornea refuses a POST from any other origin
        )
        assert info.json()["result"]["name"]
