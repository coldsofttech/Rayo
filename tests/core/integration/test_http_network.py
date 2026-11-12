"""The HTTP layer over real sockets: connection reuse, timeouts, proxy, TLS and credentials."""

import base64
import threading
from pathlib import Path

import pytest

from rayohx import (
    AuthenticationError,
    HttpClient,
    InvalidResponseError,
    MasterPlaylist,
    NetworkConfig,
    NetworkConnectionError,
    NetworkError,
    NetworkTimeoutError,
    PlaylistNotFoundError,
    ProxyConfig,
    SegmentRange,
    TLSError,
    inspect,
)
from tests.core.hls import fixture_text
from tests.core.servers import LocalServer, Reply, Seen, make_certificate

PAYLOAD = bytes(range(256)) * 40


def serve_payload(_seen: Seen) -> Reply:
    return Reply(body=PAYLOAD, headers={"Content-Type": "video/mp2t"})


def test_connections_are_reused_between_requests() -> None:
    with LocalServer({"/a.ts": serve_payload}) as server:
        with HttpClient(NetworkConfig(), origin_url=server.url) as client:
            for _ in range(5):
                assert client.fetch_bytes(f"{server.url}/a.ts").content == PAYLOAD
        assert len(server.requests) == 5
        assert server.connections == 1


def test_many_threads_share_the_pool() -> None:
    with LocalServer({"/a.ts": serve_payload}) as server:
        results: list[bytes] = []
        with HttpClient(NetworkConfig(), origin_url=server.url, pool_size=4) as client:

            def work() -> None:
                results.append(client.fetch_bytes(f"{server.url}/a.ts").content)

            threads = [threading.Thread(target=work) for _ in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert results == [PAYLOAD] * 12
        assert server.connections <= 12


def test_headers_cookies_and_user_agent_reach_the_server() -> None:
    with LocalServer({"/a.ts": serve_payload}) as server:
        network = NetworkConfig(
            user_agent="Tester/1",
            headers={"Referer": "https://site.example/", "Authorization": "Bearer abc"},
            cookies={"sid": "s1", "lang": "en"},
        )
        with HttpClient(network, origin_url=server.url) as client:
            client.fetch_bytes(f"{server.url}/a.ts")
        seen = server.requests[0].headers
        assert seen["user-agent"] == "Tester/1"
        assert seen["referer"] == "https://site.example/"
        assert seen["authorization"] == "Bearer abc"
        assert seen["cookie"] == "sid=s1; lang=en"


def test_status_codes_over_the_wire() -> None:
    routes = {
        "/gone": lambda _s: Reply(404, b"no"),
        "/denied": lambda _s: Reply(403, b"no"),
        "/limited": lambda _s: Reply(429, b"slow down", headers={"Retry-After": "0"}),
        "/broken": lambda _s: Reply(500, b"oops"),
    }
    with LocalServer(routes) as server:
        network = NetworkConfig(retries=2, backoff_factor=0.01)
        with HttpClient(network, origin_url=server.url) as client:
            with pytest.raises(PlaylistNotFoundError):
                client.fetch_text(f"{server.url}/gone")
            with pytest.raises(AuthenticationError):
                client.fetch_bytes(f"{server.url}/denied")
            with pytest.raises(NetworkError):
                client.fetch_bytes(f"{server.url}/limited")
            with pytest.raises(NetworkError):
                client.fetch_bytes(f"{server.url}/broken")
        counts = [sum(1 for r in server.requests if r.path == p) for p in ("/gone", "/denied")]
        assert counts == [1, 1]
        assert sum(1 for r in server.requests if r.path == "/limited") == 3
        assert sum(1 for r in server.requests if r.path == "/broken") == 3


def test_a_flaky_server_recovers_through_retries() -> None:
    state = {"calls": 0}

    def flaky(_seen: Seen) -> Reply:
        state["calls"] += 1
        return Reply(503) if state["calls"] < 3 else Reply(body=PAYLOAD)

    with LocalServer({"/a.ts": flaky}) as server:
        network = NetworkConfig(retries=3, backoff_factor=0.01)
        with HttpClient(network, origin_url=server.url) as client:
            assert client.fetch_bytes(f"{server.url}/a.ts").content == PAYLOAD
        assert state["calls"] == 3


def test_a_slow_server_times_out() -> None:
    with LocalServer({"/slow": lambda _s: Reply(body=b"late", delay=1.0)}) as server:
        network = NetworkConfig(read_timeout=0.2, retries=1, backoff_factor=0.01)
        with HttpClient(network, origin_url=server.url) as client:
            with pytest.raises(NetworkTimeoutError):
                client.fetch_bytes(f"{server.url}/slow")
        assert len(server.requests) == 2


def test_a_closed_port_is_a_connection_error() -> None:
    with LocalServer() as server:
        url = server.url
    network = NetworkConfig(connect_timeout=1, retries=0)
    with HttpClient(network, origin_url=url) as client, pytest.raises(NetworkConnectionError):
        client.fetch_bytes(f"{url}/a.ts")


def test_a_body_cut_off_by_the_server_is_an_error() -> None:
    with LocalServer({"/cut": lambda _s: Reply(body=b"x" * 100, truncate=True)}) as server:
        network = NetworkConfig(retries=1, backoff_factor=0.01)
        with HttpClient(network, origin_url=server.url) as client:
            with pytest.raises((InvalidResponseError, NetworkConnectionError)):
                client.fetch_bytes(f"{server.url}/cut")
        assert len(server.requests) == 2


def test_web_pages_and_ranges_over_the_wire() -> None:
    def ranged(seen: Seen) -> Reply:
        start, end = (int(v) for v in seen.headers["range"].removeprefix("bytes=").split("-"))
        return Reply(
            206,
            PAYLOAD[start : end + 1],
            {"Content-Range": f"bytes {start}-{end}/{len(PAYLOAD)}"},
        )

    routes = {
        "/page.ts": lambda _s: Reply(
            body=b"<html>denied</html>", headers={"Content-Type": "text/html"}
        ),
        "/ranged.mp4": ranged,
    }
    with LocalServer(routes) as server:
        with HttpClient(NetworkConfig(retries=0), origin_url=server.url) as client:
            with pytest.raises(InvalidResponseError, match="web page"):
                client.fetch_bytes(f"{server.url}/page.ts")
            part = client.fetch_bytes(f"{server.url}/ranged.mp4", byte_range=SegmentRange(100, 50))
            assert part.content == PAYLOAD[50:150]


def test_requests_go_through_the_proxy_with_its_credentials() -> None:
    with LocalServer({"/video/a.ts": serve_payload}) as proxy:
        proxy_config = ProxyConfig(url=proxy.url, username="me", password="s3cret")
        network = NetworkConfig(proxy=proxy_config, retries=0)
        with HttpClient(network, origin_url="http://media.invalid/") as client:
            response = client.fetch_bytes("http://media.invalid/video/a.ts")
        assert response.content == PAYLOAD
        seen = proxy.requests[0]
        assert seen.target == "http://media.invalid/video/a.ts"
        expected = "Basic " + base64.b64encode(b"me:s3cret").decode()
        assert seen.headers["proxy-authorization"] == expected


def test_a_dead_proxy_is_a_connection_error() -> None:
    with LocalServer() as dead:
        address = dead.url
    network = NetworkConfig(proxy=ProxyConfig(url=address), retries=0, connect_timeout=1)
    with HttpClient(network, origin_url="http://media.invalid/") as client:
        with pytest.raises(NetworkConnectionError):
            client.fetch_bytes("http://media.invalid/a.ts")


def test_tls_certificates_are_verified_by_default(tmp_path: Path) -> None:
    certificate = make_certificate(tmp_path)
    with LocalServer({"/a.ts": serve_payload}, tls=certificate) as server:
        with HttpClient(NetworkConfig(retries=0), origin_url=server.url) as client:
            with pytest.raises(TLSError) as info:
                client.fetch_bytes(f"{server.url}/a.ts")
        assert info.value.hint
        assert server.requests == []


def test_tls_verification_can_be_turned_off(tmp_path: Path) -> None:
    certificate = make_certificate(tmp_path)
    with LocalServer({"/a.ts": serve_payload}, tls=certificate) as server:
        network = NetworkConfig(verify_tls=False, retries=0)
        with HttpClient(network, origin_url=server.url) as client:
            assert client.fetch_bytes(f"{server.url}/a.ts").content == PAYLOAD


def test_tls_can_trust_a_given_certificate_file(tmp_path: Path) -> None:
    certificate = make_certificate(tmp_path)
    with LocalServer({"/a.ts": serve_payload}, tls=certificate) as server:
        network = NetworkConfig(verify_tls=certificate[0], retries=0)
        with HttpClient(network, origin_url=server.url) as client:
            assert client.fetch_bytes(f"{server.url}/a.ts").content == PAYLOAD


def test_inspect_reads_a_playlist_from_a_real_server() -> None:
    text = fixture_text("master_basic.m3u8").encode()
    routes = {"/stream": lambda _s: Reply(body=text, headers={"Content-Type": "text/plain"})}
    with LocalServer(routes) as server:
        playlist = inspect(f"{server.url}/stream", NetworkConfig(user_agent="Inspector"))
        assert isinstance(playlist, MasterPlaylist)
        assert playlist.variants[0].uri == f"{server.url}/360p/index.m3u8"
        assert server.requests[0].headers["user-agent"] == "Inspector"
