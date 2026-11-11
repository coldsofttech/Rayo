import pytest

from rayohx.redaction import REDACTED, redact_headers, redact_text, redact_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://user:pw@example.com/a.m3u8", "https://***@example.com/a.m3u8"),
        ("https://example.com/a.m3u8?token=abc&x=1", "https://example.com/a.m3u8?token=***&x=1"),
        ("https://example.com/a?X-Amz-Signature=zz", "https://example.com/a?X-Amz-Signature=***"),
        ("https://example.com/a?key=k#frag", "https://example.com/a?key=***#frag"),
        ("https://example.com/plain.m3u8", "https://example.com/plain.m3u8"),
        ("https://example.com/a?flag&token=t", "https://example.com/a?flag&token=***"),
        ("relative/path.ts?sig=1", "relative/path.ts?sig=***"),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert redact_url(url) == expected


def test_redact_url_survives_garbage() -> None:
    assert redact_url("http://[::1") == REDACTED


def test_redact_headers_hides_only_sensitive_values() -> None:
    cleaned = redact_headers(
        {"Authorization": "Bearer x", "Cookie": "a=1", "Referer": "https://r", "X-Token": "t"}
    )
    assert cleaned == {
        "Authorization": REDACTED,
        "Cookie": REDACTED,
        "Referer": "https://r",
        "X-Token": REDACTED,
    }


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc.def", "abc.def"),
        ("sent Basic dXNlcjpwdw== to server", "dXNlcjpwdw=="),
        ("Cookie: session=s3cret; other=1", "s3cret"),
        ("{'Authorization': 'Token zzz'}", "zzz"),
        ("password=hunter2&user=me", "hunter2"),
        ("GET https://u:p4ss@host/x?token=tokval failed", "p4ss"),
        ("GET https://u:p4ss@host/x?token=tokval failed", "tokval"),
        ('api_key="abc123"', "abc123"),
    ],
)
def test_redact_text_removes_secrets(text: str, secret: str) -> None:
    assert secret not in redact_text(text)


def test_redact_text_keeps_harmless_text() -> None:
    assert redact_text("Downloaded 12 segments") == "Downloaded 12 segments"
