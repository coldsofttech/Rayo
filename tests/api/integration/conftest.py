import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import pytest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    """Run the real API in a subprocess, the way users start it on its own."""
    port = _free_port()
    env = {
        **os.environ,
        "RAYOHX_API_PORT": str(port),
        "RAYOHX_API_HOST": "127.0.0.1",
        "RAYOHX_API_AUTH_MODE": "none",
        # The contract test reads the OpenAPI document from the running server.
        "RAYOHX_API_DOCS_ENABLED": "true",
    }
    process = subprocess.Popen([sys.executable, "-m", "rayohx_api"], env=env)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("server exited during startup")
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            pytest.fail("server did not become ready")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)
