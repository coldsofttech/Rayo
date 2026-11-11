import pytest

from rayohx import errors
from rayohx.errors import (
    AuthenticationError,
    CancellationError,
    ConfigurationError,
    HTTPError,
    InsufficientStorageError,
    InvalidPlaylistError,
    NetworkError,
    NetworkTimeoutError,
    RayoHXError,
    SegmentDownloadError,
    format_bytes,
)


def test_every_error_derives_from_the_base() -> None:
    names = [
        name
        for name, value in vars(errors).items()
        if isinstance(value, type) and issubclass(value, Exception) and value is not RayoHXError
    ]
    assert names
    for name in names:
        assert issubclass(getattr(errors, name), RayoHXError)


def test_hierarchy_follows_the_spec() -> None:
    assert issubclass(NetworkTimeoutError, errors.NetworkConnectionError)
    assert issubclass(AuthenticationError, HTTPError)
    assert issubclass(HTTPError, NetworkError)
    assert issubclass(SegmentDownloadError, errors.DownloadError)
    assert issubclass(InsufficientStorageError, errors.OutputError)
    assert issubclass(CancellationError, RayoHXError)


def test_message_is_redacted() -> None:
    error = ConfigurationError("bad proxy https://me:secret@proxy:8080", hint="Authorization: x1")
    assert "secret" not in str(error)
    assert "x1" not in error.describe()


def test_describe_adds_the_hint() -> None:
    assert ConfigurationError("oops", hint="try again").describe() == "oops try again"
    assert ConfigurationError("oops").describe() == "oops"


def test_http_error_hides_credentials_and_reports_retryability() -> None:
    error = HTTPError("https://u:p@host/a?token=t", 503)
    assert error.status_code == 503
    assert "p@" not in str(error)
    assert "token=t" not in str(error)
    assert error.retryable
    assert not HTTPError("https://host/a", 404).retryable
    assert HTTPError("https://host/a", 429).retryable


def test_segment_error_carries_index_and_clean_url() -> None:
    error = SegmentDownloadError(7, "https://host/s7.ts?sig=abc", "timeout")
    assert error.index == 7
    assert "abc" not in str(error)
    assert "Segment 7" in str(error)


def test_invalid_playlist_reports_the_line() -> None:
    assert "line 4" in str(InvalidPlaylistError("missing tag", line=4))
    assert InvalidPlaylistError("missing tag").line is None


def test_insufficient_storage_message_is_actionable() -> None:
    error = InsufficientStorageError(5 * 1024**3, 1024**3, location="/data")
    assert "5.0 GiB" in str(error)
    assert "1.0 GiB" in str(error)
    assert error.hint
    assert error.required_bytes == 5 * 1024**3


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (1536 * 1024, "1.5 MiB"),
        (2 * 1024**4, "2.0 TiB"),
    ],
)
def test_format_bytes(size: int, text: str) -> None:
    assert format_bytes(size) == text
