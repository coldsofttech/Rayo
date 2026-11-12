"""Security behaviour over real sockets and the real command: the protections hold end to end."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rayohx import (
    DownloadConfig,
    Downloader,
    HttpClient,
    NetworkConfig,
    NetworkError,
    OutputConfig,
    OutputFormat,
    inspect,
)
from tests.core.media import ts_payload
from tests.core.servers import LocalServer, Reply, Route, Seen

pytestmark = pytest.mark.integration

PLAYLIST = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\ns0.ts\n#EXT-X-ENDLIST\n"
SECRET = "hunter2-very-secret"


def _routes(prefix: str = "") -> dict[str, Route]:
    return {
        f"{prefix}/index.m3u8": lambda _s: Reply(body=PLAYLIST.encode()),
        f"{prefix}/s0.ts": lambda _s: Reply(body=ts_payload(0)),
    }


def test_credentials_do_not_follow_a_redirect_to_another_host() -> None:
    with LocalServer(_routes()) as other:
        target = other.url.replace("127.0.0.1", "localhost") + "/index.m3u8"
        origin_routes = {"/start.m3u8": lambda _s: Reply(302, b"", {"Location": target})}
        with LocalServer(origin_routes) as origin:
            network = NetworkConfig(
                headers={"Authorization": f"Bearer {SECRET}"}, cookies={"sid": SECRET}
            )
            with HttpClient(network, origin_url=origin.url) as client:
                text, _ = client.fetch_text(f"{origin.url}/start.m3u8")
        assert text.startswith("#EXTM3U")
        assert origin.requests[0].headers["authorization"] == f"Bearer {SECRET}"
        seen = other.requests[0].headers
        assert "authorization" not in seen
        assert "cookie" not in seen
        assert SECRET not in json.dumps(seen)


def test_a_redirect_to_a_file_or_other_scheme_is_refused() -> None:
    for location in ("file:///etc/passwd", "ftp://example.com/x", "gopher://h/x"):
        routes = {"/x.m3u8": lambda _s, location=location: Reply(302, b"", {"Location": location})}
        with LocalServer(routes) as server:
            with pytest.raises(NetworkError):
                inspect(f"{server.url}/x.m3u8", NetworkConfig(retries=0))


def test_a_redirect_loop_ends() -> None:
    routes = {"/loop.m3u8": lambda _s: Reply(302, b"", {"Location": "/loop.m3u8"})}
    with LocalServer(routes) as server:
        with pytest.raises(NetworkError):
            inspect(f"{server.url}/loop.m3u8", NetworkConfig(retries=0, max_redirects=3))
        assert len(server.requests) <= 5


def test_hostile_segment_names_stay_inside_the_output_folder(tmp_path: Path) -> None:
    playlist = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
        "#EXTINF:4.0,\n../../evil.ts\n#EXTINF:4.0,\n%2e%2e/%2e%2e/evil2.ts\n#EXT-X-ENDLIST\n"
    )
    routes = {
        "/a/b/index.m3u8": lambda _s: Reply(body=playlist.encode()),
        "/evil.ts": lambda _s: Reply(body=ts_payload(1)),
        "/a/b/%2e%2e/%2e%2e/evil2.ts": lambda _s: Reply(body=ts_payload(2)),
        "/a/b/../../evil2.ts": lambda _s: Reply(body=ts_payload(2)),
    }
    out = tmp_path / "out" / "segments"
    with LocalServer(routes) as server:
        config = DownloadConfig(output=OutputConfig(format=OutputFormat.SEGMENTS))
        Downloader(f"{server.url}/a/b/index.m3u8", output=out, config=config).run()
    for path in tmp_path.rglob("*"):
        assert tmp_path in path.parents or path == tmp_path
    assert not list(tmp_path.glob("evil*"))
    assert not list((tmp_path / "out").glob("evil*"))


def test_a_failed_download_keeps_an_existing_file_and_cleans_up_without_resume(
    tmp_path: Path,
) -> None:
    routes = {
        "/index.m3u8": lambda _s: Reply(body=PLAYLIST.encode()),
        "/s0.ts": lambda _s: Reply(404, b"gone"),
    }
    target = tmp_path / "keep.ts"
    target.write_bytes(b"precious")
    with LocalServer(routes) as server:
        config = DownloadConfig(
            output=OutputConfig(overwrite=True), network=NetworkConfig(retries=0), resume=False
        )
        result = Downloader(f"{server.url}/index.m3u8", output=target, config=config).run()
    assert not result.success
    assert target.read_bytes() == b"precious"
    assert not list(tmp_path.glob("*.part"))


def test_the_command_never_prints_secrets(tmp_path: Path) -> None:
    secret_headers = tmp_path / "headers.txt"
    secret_headers.write_text(f"Authorization: Bearer {SECRET}\n", encoding="utf-8")
    log = tmp_path / "run.log"
    out = tmp_path / "clip.ts"
    seen: list[Seen] = []

    def index(request: Seen) -> Reply:
        seen.append(request)
        return Reply(body=PLAYLIST.encode())

    routes = {"/index.m3u8": index, "/s0.ts": lambda _s: Reply(body=ts_payload(0))}
    with LocalServer(routes) as server:
        command = [
            sys.executable, "-m", "rayohx_cli", "--verbose", "--log-file", str(log),
            "download", f"{server.url}/index.m3u8?token={SECRET}", "-o", str(out),
            "--format", "ts", "-H", f"@{secret_headers}", "--cookie", f"sid={SECRET}",
        ]  # fmt: skip
        done = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    assert done.returncode == 0, done.stderr
    assert seen[0].headers["authorization"] == f"Bearer {SECRET}"  # the server did get it
    assert SECRET not in done.stdout
    assert SECRET not in done.stderr
    assert SECRET not in log.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.part"))
