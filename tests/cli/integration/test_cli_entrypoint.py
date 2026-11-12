"""The installed command, started as a real process and talking to a real local server."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import rayohx
from tests.core.media import ts_payload
from tests.core.servers import LocalServer, Reply, Seen

pytestmark = pytest.mark.integration

COUNT = 6


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)


def _script() -> str:
    return str(
        Path(sys.executable).parent / ("rayohx.exe" if sys.platform == "win32" else "rayohx")
    )


def _routes() -> dict[str, object]:
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4", "#EXT-X-PLAYLIST-TYPE:VOD"]
    routes: dict[str, object] = {}
    for index in range(COUNT):
        lines += ["#EXTINF:4.0,", f"seg{index}.ts"]

        def serve(_seen: Seen, index: int = index) -> Reply:
            return Reply(body=ts_payload(index))

        routes[f"/seg{index}.ts"] = serve
    body = ("\n".join([*lines, "#EXT-X-ENDLIST"]) + "\n").encode()
    routes["/index.m3u8"] = lambda _s: Reply(body=body)
    return routes


def test_module_entrypoint() -> None:
    result = _run(sys.executable, "-m", "rayohx_cli", "--version")
    assert result.returncode == 0
    assert rayohx.__version__ in result.stdout


def test_console_script_and_its_alias() -> None:
    for name in ("rayohx", "rayohx-cli"):
        script = Path(sys.executable).parent / (name + (".exe" if sys.platform == "win32" else ""))
        result = _run(str(script), "--version")
        assert result.returncode == 0
        assert rayohx.__version__ in result.stdout


def test_usage_errors_exit_with_2() -> None:
    result = _run(sys.executable, "-m", "rayohx_cli", "download")
    assert result.returncode == 2
    assert result.stdout == ""


def test_errors_go_to_stderr() -> None:
    with LocalServer({}) as server:
        result = _run(_script(), "info", f"{server.url}/missing.m3u8")
    assert result.returncode == 1
    assert "was not found" in result.stderr
    assert result.stdout == ""


def test_info_and_download_against_a_server(tmp_path: Path) -> None:
    with LocalServer(_routes()) as server:  # type: ignore[arg-type]
        url = f"{server.url}/index.m3u8"
        info = _run(_script(), "info", url, "--json")
        out = tmp_path / "clip.ts"
        done = _run(_script(), "download", url, "-o", str(out), "--format", "ts", "--json")
    assert info.returncode == 0, info.stderr
    assert json.loads(info.stdout)["segments"] == COUNT
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["success"] is True
    assert out.read_bytes() == b"".join(ts_payload(i) for i in range(COUNT))
