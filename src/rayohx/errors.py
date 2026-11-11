"""Exceptions raised by the engine.

Every message passes through the redactor, so a credential in a URL, header or free text never
reaches a log line or a traceback. Each exception can carry a ``hint`` that tells the user what to
try next.
"""

from rayohx.redaction import redact_text, redact_url


def format_bytes(size: int) -> str:
    """Return ``size`` in a short human-readable form such as ``1.5 GiB``."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError  # pragma: no cover - the loop always returns


class RayoHXError(Exception):
    """Base class of every error the engine raises on purpose."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.message = redact_text(message)
        self.hint = redact_text(hint) if hint else None
        super().__init__(self.message)

    def describe(self) -> str:
        """Return the message followed by the hint, when there is one."""
        return f"{self.message} {self.hint}" if self.hint else self.message


class ConfigurationError(RayoHXError):
    """A setting is missing, malformed or contradicts another setting."""


class PlaylistError(RayoHXError):
    """A playlist could not be used."""


class InvalidPlaylistError(PlaylistError):
    """A playlist is malformed."""

    def __init__(self, message: str, *, line: int | None = None, hint: str | None = None) -> None:
        self.line = line
        super().__init__(f"{message} (line {line})" if line is not None else message, hint=hint)


class UnsupportedPlaylistError(PlaylistError):
    """A playlist is valid but uses something the engine does not support."""


class PlaylistNotFoundError(PlaylistError):
    """The playlist address does not exist."""


class NetworkError(RayoHXError):
    """A request failed."""


class NetworkConnectionError(NetworkError):
    """The server could not be reached, or the connection broke."""


class NetworkTimeoutError(NetworkConnectionError):
    """A connect, read or overall timeout expired."""


class HTTPError(NetworkError):
    """The server answered with an error status."""

    def __init__(self, url: str, status_code: int, *, hint: str | None = None) -> None:
        self.url = redact_url(url)
        self.status_code = status_code
        super().__init__(f"HTTP {status_code} for {self.url}", hint=hint)

    @property
    def retryable(self) -> bool:
        """Whether trying the same request again could succeed."""
        return self.status_code in {408, 425, 429} or self.status_code >= 500


class AuthenticationError(HTTPError):
    """The server refused the credentials, or wants some (HTTP 401 or 403)."""


class DownloadError(RayoHXError):
    """The download failed."""


class SegmentDownloadError(DownloadError):
    """A single segment could not be downloaded."""

    def __init__(self, index: int, url: str, reason: str, *, hint: str | None = None) -> None:
        self.index = index
        self.url = redact_url(url)
        super().__init__(f"Segment {index} failed ({reason}): {self.url}", hint=hint)


class InvalidStateTransitionError(DownloadError):
    """A download was asked to move to a state it cannot reach from where it is."""


class OutputError(RayoHXError):
    """The output could not be produced."""


class InsufficientStorageError(OutputError):
    """There is not enough free disk space for the chosen storage strategy."""

    def __init__(self, required_bytes: int, available_bytes: int, *, location: str) -> None:
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes
        super().__init__(
            f"Not enough disk space in {location}: need {format_bytes(required_bytes)}, "
            f"{format_bytes(available_bytes)} available.",
            hint="Free some space, pick another folder, or choose a storage strategy that "
            "uses less working space.",
        )


class OutputExistsError(OutputError):
    """The output file already exists and overwriting was not requested."""


class OutputFormatError(OutputError):
    """The stream cannot be written in the requested format."""


class FinalizationError(OutputError):
    """Joining or validating the downloaded data failed."""


class CancellationError(RayoHXError):
    """The download was cancelled."""
