import gzip
import ssl
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from rayohx import (
    AuthenticationError,
    CancellationError,
    CancellationToken,
    ConfigurationError,
    HttpClient,
    HTTPError,
    InvalidResponseError,
    NetworkConfig,
    NetworkConnectionError,
    NetworkError,
    NetworkTimeoutError,
    PlaylistNotFoundError,
    ProxyConfig,
    SegmentRange,
    TLSError,
)
from rayohx.http_client import MAX_RETRY_AFTER, _proxy_url

ORIGIN = "https://media.example.com/vod/master.m3u8"
Handler = Callable[[httpx.Request], httpx.Response]


class FakeTime:
    """A clock that only moves when something sleeps or the test moves it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Body(httpx.SyncByteStream):
    """A response body that can fail part-way."""

    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks
        if self.error is not None:
            raise self.error


class Recorder:
    """Wraps a handler and remembers every request it saw."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def make(
    handler: Handler,
    *,
    fake: FakeTime | None = None,
    origin: str | None = ORIGIN,
    cancel: CancellationToken | None = None,
    url_guard: Callable[[str], None] | None = None,
    **network: object,
) -> tuple[HttpClient, Recorder, FakeTime]:
    fake = fake or FakeTime()
    recorder = Recorder(handler)
    client = HttpClient(
        NetworkConfig(**network),  # type: ignore[arg-type]
        origin_url=origin,
        cancel=cancel,
        url_guard=url_guard,
        transport=httpx.MockTransport(recorder),
        sleep=fake.sleep,
        clock=fake.clock,
    )
    return client, recorder, fake


def ok(content: bytes = b"data", **headers: str) -> Handler:
    return lambda _request: httpx.Response(200, content=content, headers=headers)


def sequence(*responses: httpx.Response) -> Handler:
    queue = list(responses)
    return lambda _request: queue.pop(0) if len(queue) > 1 else queue[0]


URL = "https://media.example.com/vod/a.ts"


# --- basics ------------------------------------------------------------------------------------


def test_fetch_text_returns_text_and_address() -> None:
    client, recorder, _ = make(ok(b"#EXTM3U\n"))
    assert client.fetch_text(URL) == ("#EXTM3U\n", URL)
    request = recorder.requests[0]
    assert request.headers["user-agent"] == "RayoHX"
    assert request.headers["accept-encoding"] == "gzip, deflate"


def test_fetch_text_decodes_compressed_playlists() -> None:
    body = gzip.compress(b"#EXTM3U\n#EXT-X-VERSION:3\n")
    client, _, _ = make(ok(body, **{"content-encoding": "gzip"}))
    assert client.fetch_text(URL)[0] == "#EXTM3U\n#EXT-X-VERSION:3\n"


def test_fetch_text_survives_invalid_utf8() -> None:
    client, _, _ = make(ok(b"#EXTM3U\n\xff\xfe"))
    assert client.fetch_text(URL)[0].startswith("#EXTM3U")


def test_fetch_text_hands_back_web_pages_for_the_parser_to_reject() -> None:
    client, _, _ = make(ok(b"<html></html>", **{"content-type": "text/html"}))
    assert client.fetch_text(URL)[0] == "<html></html>"


def test_fetch_bytes_asks_for_uncompressed_data() -> None:
    client, recorder, _ = make(ok(b"\x47" * 188, **{"content-type": "video/mp2t"}))
    response = client.fetch_bytes(URL)
    assert response.content == b"\x47" * 188
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp2t"
    assert recorder.requests[0].headers["accept-encoding"] == "identity"


def test_custom_user_agent_and_headers_are_sent() -> None:
    client, recorder, _ = make(
        ok(), user_agent="Agent/2", headers={"Referer": "https://site.example/", "X-Extra": "1"}
    )
    client.fetch_bytes(URL)
    headers = recorder.requests[0].headers
    assert headers["user-agent"] == "Agent/2"
    assert headers["referer"] == "https://site.example/"
    assert headers["x-extra"] == "1"


def test_a_user_agent_header_replaces_the_default() -> None:
    client, recorder, _ = make(ok(), headers={"user-agent": "Mine"})
    client.fetch_bytes(URL)
    assert recorder.requests[0].headers.get_list("user-agent") == ["Mine"]


def test_invalid_addresses_are_refused_before_any_request() -> None:
    client, recorder, _ = make(ok())
    with pytest.raises(ConfigurationError):
        client.fetch_bytes("ftp://example.com/a.ts")
    assert recorder.requests == []


def test_the_client_is_a_context_manager() -> None:
    with make(ok())[0] as client:
        client.fetch_bytes(URL)
    assert client._client.is_closed


# --- redirects ---------------------------------------------------------------------------------


def test_redirects_are_followed_and_the_final_address_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/vod/a.ts":
            return httpx.Response(302, headers={"location": "/moved/b.ts"})
        return httpx.Response(200, content=b"payload")

    client, recorder, _ = make(handler)
    assert client.fetch_bytes(URL).url == "https://media.example.com/moved/b.ts"
    assert len(recorder.requests) == 2


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_every_redirect_status_is_followed(status: int) -> None:
    queue = [httpx.Response(status, headers={"location": "https://other.example.net/x"})]

    def handler(_request: httpx.Request) -> httpx.Response:
        return queue.pop(0) if queue else httpx.Response(200, content=b"x")

    client, _, _ = make(handler)
    assert client.fetch_bytes(URL).url == "https://other.example.net/x"


def test_a_redirect_loop_stops() -> None:
    client, recorder, _ = make(
        lambda _r: httpx.Response(302, headers={"location": "/again"}), max_redirects=3
    )
    with pytest.raises(NetworkError, match="Too many redirects"):
        client.fetch_bytes(URL)
    assert len(recorder.requests) == 4


@pytest.mark.parametrize(
    "response",
    [httpx.Response(302), httpx.Response(302, headers={"location": "ftp://host/file"})],
)
def test_broken_redirects_are_rejected(response: httpx.Response) -> None:
    client, _, _ = make(lambda _r: response)
    with pytest.raises(InvalidResponseError):
        client.fetch_bytes(URL)


def test_the_address_guard_sees_every_hop_and_can_refuse() -> None:
    seen: list[str] = []

    def guard(url: str) -> None:
        seen.append(url)
        if "internal" in url:
            raise ConfigurationError("Private addresses are not allowed.")

    handler = sequence(
        httpx.Response(302, headers={"location": "http://internal.local/secret"}),
    )
    client, recorder, _ = make(handler, url_guard=guard)
    with pytest.raises(ConfigurationError, match="Private"):
        client.fetch_bytes(URL)
    assert seen == [URL, "http://internal.local/secret"]
    assert len(recorder.requests) == 1


# --- credentials stay with their host ----------------------------------------------------------

SECRETS = {"Authorization": "Bearer s3", "X-Api-Key": "k3", "Referer": "https://site.example/"}


def echo_after_redirect() -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "media.example.com":
            return httpx.Response(302, headers={"location": "https://cdn.other.net/seg.ts"})
        return httpx.Response(200, content=b"segment")

    return handler


def test_secrets_do_not_follow_a_redirect_to_another_host() -> None:
    client, recorder, _ = make(echo_after_redirect(), headers=SECRETS, cookies={"sid": "abc"})
    client.fetch_bytes(URL)
    first, second = recorder.requests
    assert first.headers["authorization"] == "Bearer s3"
    assert first.headers["x-api-key"] == "k3"
    assert first.headers["cookie"] == "sid=abc"
    for name in ("authorization", "x-api-key", "cookie"):
        assert name not in second.headers
    assert second.headers["referer"] == "https://site.example/"
    assert second.headers["user-agent"] == "RayoHX"


def test_listed_credential_hosts_receive_secrets() -> None:
    client, recorder, _ = make(
        echo_after_redirect(),
        headers=SECRETS,
        cookies={"sid": "abc"},
        credential_hosts=("CDN.other.net",),
    )
    client.fetch_bytes(URL)
    second = recorder.requests[1]
    assert second.headers["authorization"] == "Bearer s3"
    assert second.headers["cookie"] == "sid=abc"


def test_a_fixed_origin_does_not_trust_a_different_first_host() -> None:
    client, recorder, _ = make(
        ok(), origin="https://start.example.com/m.m3u8", headers=SECRETS, cookies={"a": "b"}
    )
    client.fetch_bytes("https://elsewhere.example.org/a.ts")
    assert "authorization" not in recorder.requests[0].headers
    assert "cookie" not in recorder.requests[0].headers


def test_without_an_origin_the_first_host_is_trusted() -> None:
    client, recorder, _ = make(ok(), origin=None, headers=SECRETS)
    client.fetch_bytes("https://first.example.com/a.ts")
    client.fetch_bytes("https://second.example.com/a.ts")
    assert "authorization" in recorder.requests[0].headers
    assert "authorization" not in recorder.requests[1].headers


def test_an_explicit_cookie_header_is_not_duplicated() -> None:
    client, recorder, _ = make(ok(), headers={"Cookie": "mine=1"}, cookies={"sid": "abc"})
    client.fetch_bytes(URL)
    assert recorder.requests[0].headers.get_list("cookie") == ["mine=1"]


# --- byte ranges -------------------------------------------------------------------------------


def test_a_byte_range_is_requested_and_checked() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206, content=b"0123456789", headers={"content-range": "bytes 5-14/100"}
        )

    client, recorder, _ = make(handler)
    response = client.fetch_bytes(URL, byte_range=SegmentRange(10, 5))
    assert response.content == b"0123456789"
    assert recorder.requests[0].headers["range"] == "bytes=5-14"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(206, content=b"0123456789", headers={"content-range": "bytes 0-9/100"}),
        httpx.Response(206, content=b"0123456789"),
        httpx.Response(200, content=b"0123456789"),
    ],
)
def test_wrong_or_missing_range_answers_are_rejected_without_retrying(
    response: httpx.Response,
) -> None:
    client, recorder, _ = make(lambda _r: response)
    with pytest.raises(InvalidResponseError) as info:
        client.fetch_bytes(URL, byte_range=SegmentRange(10, 5))
    assert not info.value.retryable
    assert len(recorder.requests) == 1


def test_a_short_range_body_is_retried() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(206, content=b"01234", headers={"content-range": "bytes 5-14/100"})

    client, recorder, _ = make(handler, retries=1)
    with pytest.raises(InvalidResponseError, match="Expected 10 bytes"):
        client.fetch_bytes(URL, byte_range=SegmentRange(10, 5))
    assert len(recorder.requests) == 2


def test_a_range_without_an_offset_cannot_be_requested() -> None:
    client, _, _ = make(ok())
    with pytest.raises(ValueError, match="offset"):
        client.fetch_bytes(URL, byte_range=SegmentRange(10))


# --- status codes and retries ------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404, 410, 416])
def test_client_errors_are_not_retried(status: int) -> None:
    client, recorder, fake = make(lambda _r: httpx.Response(status))
    with pytest.raises(HTTPError) as info:
        client.fetch_bytes(URL)
    assert info.value.status_code == status
    assert not info.value.retryable
    assert len(recorder.requests) == 1
    assert fake.sleeps == []


@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_raise_authentication_errors(status: int) -> None:
    client, recorder, _ = make(lambda _r: httpx.Response(status))
    with pytest.raises(AuthenticationError) as info:
        client.fetch_bytes(URL)
    assert info.value.hint
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("status", [404, 410])
def test_a_missing_playlist_has_its_own_error(status: int) -> None:
    client, _, _ = make(lambda _r: httpx.Response(status))
    with pytest.raises(PlaylistNotFoundError):
        client.fetch_text(URL)


def test_other_playlist_errors_stay_http_errors() -> None:
    client, _, _ = make(lambda _r: httpx.Response(403))
    with pytest.raises(AuthenticationError):
        client.fetch_text(URL)


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_temporary_errors_are_retried_until_they_clear(status: int) -> None:
    client, recorder, fake = make(
        sequence(httpx.Response(status), httpx.Response(200, content=b"ok"))
    )
    assert client.fetch_bytes(URL).content == b"ok"
    assert len(recorder.requests) == 2
    assert len(fake.sleeps) == 1


def test_retries_stop_after_the_configured_count() -> None:
    client, recorder, fake = make(lambda _r: httpx.Response(503), retries=3, backoff_factor=0.5)
    with pytest.raises(HTTPError) as info:
        client.fetch_bytes(URL)
    assert info.value.retryable
    assert len(recorder.requests) == 4
    assert len(fake.sleeps) == 3
    for number, delay in enumerate(fake.sleeps):
        expected = 0.5 * 2**number
        assert expected * 0.75 <= delay <= expected * 1.25


def test_zero_retries_means_one_attempt() -> None:
    client, recorder, _ = make(lambda _r: httpx.Response(500), retries=0)
    with pytest.raises(HTTPError):
        client.fetch_bytes(URL)
    assert len(recorder.requests) == 1


def test_retry_after_seconds_is_honoured() -> None:
    client, _, fake = make(
        sequence(
            httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, content=b"x")
        )
    )
    client.fetch_bytes(URL)
    assert fake.sleeps == [7.0]


def test_retry_after_is_capped() -> None:
    client, _, fake = make(
        sequence(
            httpx.Response(503, headers={"retry-after": "3600"}), httpx.Response(200, content=b"x")
        )
    )
    client.fetch_bytes(URL)
    assert fake.sleeps == [MAX_RETRY_AFTER]


def test_retry_after_dates_are_understood() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    client, _, fake = make(
        sequence(
            httpx.Response(503, headers={"retry-after": when}), httpx.Response(200, content=b"x")
        )
    )
    client.fetch_bytes(URL)
    assert 25 < fake.sleeps[0] <= 30


def test_unreadable_retry_after_falls_back_to_backoff() -> None:
    client, _, fake = make(
        sequence(
            httpx.Response(503, headers={"retry-after": "soon"}), httpx.Response(200, content=b"x")
        ),
        backoff_factor=1.0,
    )
    client.fetch_bytes(URL)
    assert 0.75 <= fake.sleeps[0] <= 1.25


# --- response checks ---------------------------------------------------------------------------


def test_web_pages_served_as_media_are_retried_then_reported() -> None:
    client, recorder, _ = make(ok(b"<h1>Denied</h1>", **{"content-type": "text/html"}), retries=2)
    with pytest.raises(InvalidResponseError, match="web page") as info:
        client.fetch_bytes(URL)
    assert info.value.retryable
    assert info.value.hint
    assert len(recorder.requests) == 3


def test_web_pages_with_a_media_content_type_are_caught_by_their_content() -> None:
    page = b"  <!DOCTYPE html><html><body>expired</body></html>"
    client, _, _ = make(ok(page, **{"content-type": "application/octet-stream"}), retries=0)
    with pytest.raises(InvalidResponseError, match="web page"):
        client.fetch_bytes(URL)


def test_empty_bodies_are_errors() -> None:
    client, recorder, _ = make(ok(b""), retries=1)
    with pytest.raises(InvalidResponseError, match="empty"):
        client.fetch_bytes(URL)
    assert len(recorder.requests) == 2


def test_cut_off_bodies_are_errors_and_retried() -> None:
    cut = httpx.Response(200, content=b"x" * 50, headers={"content-length": "100"})
    client, recorder, _ = make(sequence(cut, httpx.Response(200, content=b"y" * 100)))
    assert client.fetch_bytes(URL).content == b"y" * 100
    assert len(recorder.requests) == 2


def test_cut_off_bodies_that_never_recover_raise() -> None:
    client, _, _ = make(
        lambda _r: httpx.Response(200, content=b"x" * 50, headers={"content-length": "100"}),
        retries=1,
    )
    with pytest.raises(InvalidResponseError, match="cut short"):
        client.fetch_bytes(URL)


def test_oversized_responses_are_rejected_without_retrying() -> None:
    client, recorder, _ = make(ok(b"x" * 20))
    with pytest.raises(InvalidResponseError, match="larger") as info:
        client.fetch_bytes(URL, max_bytes=10)
    assert not info.value.retryable
    assert len(recorder.requests) == 1


def test_oversized_streams_without_a_declared_length_are_rejected() -> None:
    response = httpx.Response(200, stream=Body([b"x" * 40_000, b"x" * 40_000]))
    client, _, _ = make(lambda _r: response)
    with pytest.raises(InvalidResponseError, match="larger"):
        client.fetch_bytes(URL, max_bytes=50_000)


def test_progress_is_reported_per_chunk() -> None:
    body = b"x" * 200_000
    seen: list[int] = []
    client, _, _ = make(ok(body))
    client.fetch_bytes(URL, on_bytes=seen.append)
    assert sum(seen) == 200_000
    assert len(seen) > 1


# --- failures on the wire ----------------------------------------------------------------------


def failing(error: Exception) -> Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    return handler


@pytest.mark.parametrize(
    ("error", "expected", "attempts"),
    [
        (httpx.ConnectTimeout("slow"), NetworkTimeoutError, 3),
        (httpx.ReadTimeout("slow"), NetworkTimeoutError, 3),
        (httpx.ConnectError("refused"), NetworkConnectionError, 3),
        (httpx.RemoteProtocolError("closed"), NetworkConnectionError, 3),
        (httpx.ProxyError("denied"), NetworkConnectionError, 3),
        (httpx.DecodingError("bad gzip"), InvalidResponseError, 1),
        (httpx.UnsupportedProtocol("weird"), NetworkError, 1),
    ],
)
def test_transport_errors_become_clear_errors(
    error: Exception, expected: type[NetworkError], attempts: int
) -> None:
    client, recorder, _ = make(failing(error), retries=2)
    with pytest.raises(expected):
        client.fetch_bytes(URL)
    assert len(recorder.requests) == attempts


def test_certificate_problems_are_not_retried() -> None:
    error = httpx.ConnectError("handshake failed")
    error.__cause__ = ssl.SSLCertVerificationError("self signed certificate")
    client, recorder, _ = make(failing(error), retries=3)
    with pytest.raises(TLSError) as info:
        client.fetch_bytes(URL)
    assert info.value.hint
    assert len(recorder.requests) == 1


def test_a_failure_part_way_through_a_body_is_retried_from_the_start() -> None:
    broken = httpx.Response(200, stream=Body([b"abc"], httpx.ReadError("reset")))
    client, recorder, _ = make(sequence(broken, httpx.Response(200, content=b"whole")))
    assert client.fetch_bytes(URL).content == b"whole"
    assert len(recorder.requests) == 2


def test_error_messages_never_contain_credentials() -> None:
    url = "https://user:pw@media.example.com/a.ts?token=secret123"
    client, _, _ = make(lambda _r: httpx.Response(500), retries=0)
    with pytest.raises(HTTPError) as info:
        client.fetch_bytes(url)
    assert "pw@" not in str(info.value)
    assert "secret123" not in str(info.value)


# --- time limits, speed limit and cancellation -------------------------------------------------


def test_a_retry_that_would_pass_the_overall_limit_is_not_attempted() -> None:
    client, recorder, fake = make(
        lambda _r: httpx.Response(503), total_timeout=5, backoff_factor=10, retries=3
    )
    with pytest.raises(HTTPError):
        client.fetch_bytes(URL)
    assert len(recorder.requests) == 1
    assert fake.sleeps == []


def test_the_overall_limit_stops_a_slow_body() -> None:
    fake = FakeTime()
    client, recorder, _ = make(ok(b"x" * 200_000), fake=fake, total_timeout=5, retries=3)

    def slow(_size: int) -> None:
        fake.now += 10

    with pytest.raises(NetworkTimeoutError, match="overall") as info:
        client.fetch_bytes(URL, on_bytes=slow)
    assert not info.value.retryable
    assert len(recorder.requests) == 1


def test_downloads_respect_the_speed_limit() -> None:
    client, _, fake = make(ok(b"x" * 3000), rate_limit=1000)
    client.fetch_bytes(URL)
    assert fake.sleeps == [2.0]


def test_cancelled_before_starting_sends_nothing() -> None:
    token = CancellationToken()
    token.cancel()
    client, recorder, _ = make(ok(), cancel=token)
    with pytest.raises(CancellationError):
        client.fetch_bytes(URL)
    assert recorder.requests == []


def test_cancelling_while_waiting_to_retry_stops_at_once() -> None:
    token = CancellationToken()

    def handler(_request: httpx.Request) -> httpx.Response:
        token.cancel()
        return httpx.Response(503)

    client, recorder, _ = make(handler, cancel=token, retries=5)
    with pytest.raises(CancellationError):
        client.fetch_bytes(URL)
    assert len(recorder.requests) == 1


def test_cancelling_while_reading_stops_between_chunks() -> None:
    token = CancellationToken()
    client, _, _ = make(ok(b"x" * 300_000), cancel=token)
    received: list[int] = []

    def on_bytes(size: int) -> None:
        received.append(size)
        token.cancel()

    with pytest.raises(CancellationError):
        client.fetch_bytes(URL, on_bytes=on_bytes)
    assert len(received) == 1


# --- construction ------------------------------------------------------------------------------


def test_an_unreadable_certificate_file_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="certificate file"):
        HttpClient(NetworkConfig(verify_tls=tmp_path / "missing.pem"))


def test_turning_verification_off_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="rayohx"):
        HttpClient(NetworkConfig(verify_tls=False)).close()
    assert "verification is turned off" in caplog.text


def test_proxy_credentials_are_placed_in_the_proxy_address() -> None:
    plain = ProxyConfig(url="http://proxy.example:3128")
    assert _proxy_url(plain) == "http://proxy.example:3128"
    secured = ProxyConfig(url="http://proxy.example:3128", username="me@work", password="p:w/d")
    assert _proxy_url(secured) == "http://me%40work:p%3Aw%2Fd@proxy.example:3128"
    inline = ProxyConfig(url="http://old:old@proxy.example:3128", username="new", password="pw")
    assert _proxy_url(inline) == "http://new:pw@proxy.example:3128"
