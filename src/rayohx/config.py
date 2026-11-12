"""Typed, validated settings.

Every object is immutable and checks itself on creation, so a bad value fails at the edge with a
``ConfigurationError`` instead of deep inside a download. Secrets are kept out of ``repr`` and out
of ``as_dict``.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from rayohx.enums import OutputFormat, RemuxBackend, StorageStrategy
from rayohx.errors import ConfigurationError
from rayohx.redaction import REDACTED, redact_headers, redact_url

DEFAULT_USER_AGENT = "RayoHX"
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 64
DEFAULT_BUFFER_BYTES = 64 * 1024 * 1024

#: Proxy schemes accepted today. SOCKS can be added here without touching the callers.
SUPPORTED_PROXY_SCHEMES = frozenset({"http", "https"})

_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_QUALITY = re.compile(r"^(best|worst|auto|\d{3,4}p)$", re.IGNORECASE)
_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_RESOLUTION = re.compile(r"^\d{3,4}p$", re.IGNORECASE)
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$"
)


def validate_url(url: str, *, name: str = "url") -> str:
    """Return ``url`` stripped, or raise if it is not an absolute http(s) address."""
    cleaned = url.strip()
    if any(ord(char) < 32 for char in cleaned):
        raise ConfigurationError(f"{name} contains control characters.")
    try:
        parts = urlsplit(cleaned)
        hostname = parts.hostname
    except ValueError as exc:
        raise ConfigurationError(f"{name} is not a valid address.") from exc
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        raise ConfigurationError(
            f"{name} must be an absolute http or https address.",
            hint="Example: https://example.com/master.m3u8",
        )
    return cleaned


def _frozen_map(value: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(value))


def _check_positive(name: str, value: float | None, *, allow_none: bool = False) -> None:
    if value is None:
        if not allow_none:
            raise ConfigurationError(f"{name} is required.")
        return
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero.")


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class ProxyConfig:
    """An HTTP or HTTPS proxy. Credentials go in ``username`` and ``password``, or in the URL."""

    url: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        cleaned = self.url.strip()
        try:
            parts = urlsplit(cleaned)
            hostname = parts.hostname
        except ValueError as exc:
            raise ConfigurationError("The proxy address is not valid.") from exc
        if parts.scheme.lower() not in SUPPORTED_PROXY_SCHEMES or not hostname:
            raise ConfigurationError(
                "The proxy address must look like http://host:port or https://host:port.",
                hint="SOCKS proxies are not supported yet.",
            )
        if (self.username is None) != (self.password is None):
            raise ConfigurationError("Set both the proxy username and password, or neither.")
        object.__setattr__(self, "url", cleaned)

    def __repr__(self) -> str:
        return f"ProxyConfig(url={self.redacted_url!r})"

    @property
    def redacted_url(self) -> str:
        """The proxy address with any credentials hidden."""
        return redact_url(self.url)

    def as_dict(self) -> dict[str, Any]:
        """A serializable view without secrets."""
        return {
            "url": self.redacted_url,
            "username": REDACTED if self.username else None,
            "password": REDACTED if self.password else None,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class NetworkConfig:
    """How requests are made."""

    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    cookies: Mapping[str, str] = field(default_factory=dict, repr=False)
    user_agent: str = DEFAULT_USER_AGENT
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    total_timeout: float | None = None
    retries: int = 3
    backoff_factor: float = 0.5
    max_redirects: int = 10
    #: ``True`` verifies with the default trust store, ``False`` turns verification off, and a
    #: path verifies against that CA bundle.
    verify_tls: bool | Path = True
    proxy: ProxyConfig | None = None
    #: Cap on download speed in bytes per second across all workers; ``None`` means no cap.
    rate_limit: int | None = None
    #: Hosts, besides the one the download starts from, that may receive credentials
    #: (``Authorization``, ``Cookie`` and other secret headers). Other hosts get none of them.
    credential_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for host in self.credential_hosts:
            if not _HOSTNAME.match(host):
                raise ConfigurationError(
                    f"{host!r} is not a valid host name.",
                    hint="Give a bare host such as cdn.example.com, without scheme or path.",
                )
        object.__setattr__(
            self, "credential_hosts", tuple(host.lower() for host in self.credential_hosts)
        )
        for name, value in self.headers.items():
            if not _HEADER_NAME.match(name):
                raise ConfigurationError(f"{name!r} is not a valid header name.")
            if any(char in value for char in "\r\n\0"):
                raise ConfigurationError(f"The value of header {name!r} contains a line break.")
        for name, value in self.cookies.items():
            if not name or any(char in name + value for char in "\r\n\0;"):
                raise ConfigurationError(f"The cookie {name!r} is not valid.")
        if not self.user_agent.strip() or any(char in self.user_agent for char in "\r\n\0"):
            raise ConfigurationError("The user agent must be a single non-empty line.")
        _check_positive("connect_timeout", self.connect_timeout)
        _check_positive("read_timeout", self.read_timeout)
        _check_positive("total_timeout", self.total_timeout, allow_none=True)
        _check_positive("rate_limit", self.rate_limit, allow_none=True)
        if self.retries < 0:
            raise ConfigurationError("retries must not be negative.")
        if self.backoff_factor < 0:
            raise ConfigurationError("backoff_factor must not be negative.")
        if self.max_redirects < 0:
            raise ConfigurationError("max_redirects must not be negative.")
        object.__setattr__(self, "headers", _frozen_map(self.headers))
        object.__setattr__(self, "cookies", _frozen_map(self.cookies))

    def request_headers(self) -> dict[str, str]:
        """The headers to send: the user agent, then the user's own, which win on a clash."""
        merged = {"User-Agent": self.user_agent}
        for name, value in self.headers.items():
            for existing in [key for key in merged if key.lower() == name.lower()]:
                del merged[existing]
            merged[name] = value
        return merged

    def as_dict(self) -> dict[str, Any]:
        """A serializable view without secrets."""
        verify = str(self.verify_tls) if isinstance(self.verify_tls, Path) else self.verify_tls
        return {
            "headers": redact_headers(self.headers),
            "cookies": dict.fromkeys(self.cookies, REDACTED),
            "user_agent": self.user_agent,
            "connect_timeout": self.connect_timeout,
            "read_timeout": self.read_timeout,
            "total_timeout": self.total_timeout,
            "retries": self.retries,
            "backoff_factor": self.backoff_factor,
            "max_redirects": self.max_redirects,
            "verify_tls": verify,
            "proxy": self.proxy.as_dict() if self.proxy else None,
            "rate_limit": self.rate_limit,
            "credential_hosts": list(self.credential_hosts),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class StorageConfig:
    """Where working data lives and how much of it may pile up."""

    strategy: StorageStrategy = StorageStrategy.AUTO
    #: Folder for working files. ``None`` lets the engine pick a folder beside the output.
    workspace_dir: Path | None = None
    #: Memory the bounded buffer may hold.
    buffer_bytes: int = DEFAULT_BUFFER_BYTES
    #: Most temporary data allowed on disk at once; ``None`` means no explicit limit.
    max_temp_bytes: int | None = None
    #: Free space that must remain after the download.
    reserve_bytes: int = 256 * 1024 * 1024
    #: Keep resume state after a failure or cancellation.
    keep_state_on_failure: bool = True

    def __post_init__(self) -> None:
        _check_positive("buffer_bytes", self.buffer_bytes)
        _check_positive("max_temp_bytes", self.max_temp_bytes, allow_none=True)
        if self.reserve_bytes < 0:
            raise ConfigurationError("reserve_bytes must not be negative.")

    def as_dict(self) -> dict[str, Any]:
        """A serializable view."""
        return {
            "strategy": self.strategy.value,
            "workspace_dir": str(self.workspace_dir) if self.workspace_dir else None,
            "buffer_bytes": self.buffer_bytes,
            "max_temp_bytes": self.max_temp_bytes,
            "reserve_bytes": self.reserve_bytes,
            "keep_state_on_failure": self.keep_state_on_failure,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputConfig:
    """What file is produced and how it is checked."""

    path: Path | None = None
    format: OutputFormat = OutputFormat.AUTO
    #: Replace an existing file. Off by default so a download never destroys data silently.
    overwrite: bool = False
    validate: bool = True
    remux_backend: RemuxBackend = RemuxBackend.AUTO
    #: Path of an FFmpeg program to use when the backend is ``FFMPEG`` or ``AUTO`` needs it.
    ffmpeg_path: Path | None = None

    def __post_init__(self) -> None:
        if self.path is not None:
            text = str(self.path)
            if not text.strip() or text == ".":
                raise ConfigurationError("The output path must not be empty.")
            if "\0" in text:
                raise ConfigurationError("The output path contains a null character.")

    def as_dict(self) -> dict[str, Any]:
        """A serializable view."""
        return {
            "path": str(self.path) if self.path else None,
            "format": self.format.value,
            "overwrite": self.overwrite,
            "validate": self.validate,
            "remux_backend": self.remux_backend.value,
            "ffmpeg_path": str(self.ffmpeg_path) if self.ffmpeg_path else None,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class SelectionConfig:
    """Which tracks to download."""

    #: ``best``, ``worst``, ``auto`` or a height such as ``1080p`` (the nearest not above it).
    quality: str = "best"
    max_resolution: str | None = None
    #: Highest bandwidth in bits per second.
    max_bitrate: int | None = None
    #: Audio languages to download, one track each, in order of preference. If none is available
    #: the stream's default track is used. Empty means the default track.
    audio_languages: tuple[str, ...] = ()
    #: Subtitle languages to download, one track each. Empty means no subtitles.
    subtitle_languages: tuple[str, ...] = ()
    #: Exact track names to download (as shown by ``inspect``), matched ignoring case.
    audio_names: tuple[str, ...] = ()
    subtitle_names: tuple[str, ...] = ()
    #: Download only the video stream, leaving out separate audio and subtitle tracks.
    video_only: bool = False

    def __post_init__(self) -> None:
        if not _QUALITY.match(self.quality):
            raise ConfigurationError(
                f"quality {self.quality!r} is not valid.",
                hint="Use best, worst, auto, or a height such as 1080p.",
            )
        object.__setattr__(self, "quality", self.quality.lower())
        if self.max_resolution is not None:
            if not _RESOLUTION.match(self.max_resolution):
                raise ConfigurationError(
                    f"max_resolution {self.max_resolution!r} is not valid.",
                    hint="Use a height such as 1080p.",
                )
            object.__setattr__(self, "max_resolution", self.max_resolution.lower())
        _check_positive("max_bitrate", self.max_bitrate, allow_none=True)
        for name in ("audio_languages", "subtitle_languages"):
            values = tuple(getattr(self, name))
            for language in values:
                if not _LANGUAGE.match(language):
                    raise ConfigurationError(f"{language!r} is not a valid language code.")
            object.__setattr__(self, name, values)
        for name in ("audio_names", "subtitle_names"):
            names = tuple(getattr(self, name))
            if any(not item.strip() for item in names):
                raise ConfigurationError("Track names must not be empty.")
            object.__setattr__(self, name, names)

    def as_dict(self) -> dict[str, Any]:
        """A serializable view."""
        return {
            "quality": self.quality,
            "max_resolution": self.max_resolution,
            "max_bitrate": self.max_bitrate,
            "audio_languages": list(self.audio_languages),
            "subtitle_languages": list(self.subtitle_languages),
            "audio_names": list(self.audio_names),
            "subtitle_names": list(self.subtitle_names),
            "video_only": self.video_only,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadConfig:
    """Everything that shapes a download, grouped by area."""

    #: Segments fetched at the same time. Conservative by default.
    concurrency: int = DEFAULT_CONCURRENCY
    resume: bool = True
    network: NetworkConfig = field(default_factory=NetworkConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)

    def __post_init__(self) -> None:
        if not 1 <= self.concurrency <= MAX_CONCURRENCY:
            raise ConfigurationError(f"concurrency must be between 1 and {MAX_CONCURRENCY}.")

    def as_dict(self) -> dict[str, Any]:
        """A serializable view without secrets, safe for logs and resume state."""
        return {
            "concurrency": self.concurrency,
            "resume": self.resume,
            "network": self.network.as_dict(),
            "storage": self.storage.as_dict(),
            "output": self.output.as_dict(),
            "selection": self.selection.as_dict(),
        }
