"""Turning what the user typed into engine settings. Pure functions, no output."""

import re
from collections.abc import Sequence
from pathlib import Path

from rayohx import (
    ConfigurationError,
    DownloadConfig,
    NetworkConfig,
    OutputConfig,
    OutputFormat,
    ProxyConfig,
    RemuxBackend,
    SelectionConfig,
    StorageConfig,
    StorageStrategy,
)

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE)
_BINARY = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
_DECIMAL = {"": 1, "k": 1000, "m": 1000**2, "g": 1000**3, "t": 1000**4}


def parse_size(text: str, *, name: str = "size", decimal: bool = False) -> int:
    """Read ``512``, ``64k``, ``2M`` or ``1.5G``.

    Suffixes are powers of 1024 (``2M`` is 2 MiB), or powers of 1000 with ``decimal`` (used for
    bit rates, where ``3M`` means 3 million bits per second).

    Raises:
        ConfigurationError: If ``text`` is not a size.
    """
    match = _SIZE.match(text)
    if not match:
        raise ConfigurationError(
            f"{name} {text!r} is not a size.", hint="Use a number with an optional k, M or G."
        )
    scale = (_DECIMAL if decimal else _BINARY)[match.group(2).lower()]
    value = int(float(match.group(1)) * scale)
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero.")
    return value


def read_values(values: Sequence[str], *, what: str) -> list[str]:
    """Expand ``@file`` entries to the lines of that file, so secrets need not be typed.

    Blank lines and lines starting with ``#`` are skipped.

    Raises:
        ConfigurationError: If a file cannot be read.
    """
    expanded: list[str] = []
    for value in values:
        if not value.startswith("@"):
            expanded.append(value)
            continue
        path = Path(value[1:]).expanduser()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigurationError(f"The {what} file {path} cannot be read.") from exc
        expanded += [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    return expanded


def parse_headers(values: Sequence[str]) -> dict[str, str]:
    """Read ``Name: value`` entries (or ``@file``) into a dictionary. Later ones win."""
    headers: dict[str, str] = {}
    for entry in read_values(values, what="header"):
        name, separator, value = entry.partition(":")
        if not separator or not name.strip():
            raise ConfigurationError(
                f"The header {entry!r} is not written as 'Name: value'.",
            )
        for existing in [key for key in headers if key.lower() == name.strip().lower()]:
            del headers[existing]
        headers[name.strip()] = value.strip()
    return headers


def parse_cookies(values: Sequence[str]) -> dict[str, str]:
    """Read ``name=value`` entries (several may share one with ``;``, or come from ``@file``)."""
    cookies: dict[str, str] = {}
    for entry in read_values(values, what="cookie"):
        for part in entry.split(";"):
            if not part.strip():
                continue
            name, separator, value = part.partition("=")
            if not separator or not name.strip():
                raise ConfigurationError(
                    f"The cookie {part.strip()!r} is not written as name=value."
                )
            cookies[name.strip()] = value.strip()
    return cookies


def build_config(
    *,
    quality: str = "best",
    max_resolution: str | None = None,
    max_bitrate: str | None = None,
    variant: int | None = None,
    resolution: str | None = None,
    bandwidth: str | None = None,
    variant_name: str | None = None,
    audio: Sequence[str] = (),
    audio_name: Sequence[str] = (),
    subtitles: Sequence[str] = (),
    subtitle_name: Sequence[str] = (),
    video_only: bool = False,
    header: Sequence[str] = (),
    cookie: Sequence[str] = (),
    user_agent: str | None = None,
    referer: str | None = None,
    origin: str | None = None,
    proxy: str | None = None,
    insecure: bool = False,
    ca_bundle: Path | None = None,
    connect_timeout: float | None = None,
    read_timeout: float | None = None,
    timeout: float | None = None,
    retries: int | None = None,
    rate_limit: str | None = None,
    credential_host: Sequence[str] = (),
    concurrency: int | None = None,
    resume: bool = True,
    storage: StorageStrategy = StorageStrategy.AUTO,
    workspace: Path | None = None,
    buffer_size: str | None = None,
    max_temp_size: str | None = None,
    space_check: bool = True,
    format: OutputFormat = OutputFormat.AUTO,
    overwrite: bool = False,
    validate: bool = True,
    remux_backend: RemuxBackend = RemuxBackend.AUTO,
    ffmpeg: Path | None = None,
) -> DownloadConfig:
    """Build the engine's settings from the command line's options.

    Raises:
        ConfigurationError: If any value is invalid. The message says which and how to fix it.
    """
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    for name, value in parse_headers(header).items():
        # The user's own headers win over the shortcuts. Names differ only in case at times.
        for existing in [key for key in headers if key.lower() == name.lower()]:
            del headers[existing]
        headers[name] = value

    network_options: dict[str, object] = {
        "headers": headers,
        "cookies": parse_cookies(cookie),
        "verify_tls": False if insecure else (ca_bundle if ca_bundle else True),
        "credential_hosts": tuple(credential_host),
    }
    optional: tuple[tuple[str, object], ...] = (
        ("user_agent", user_agent),
        ("connect_timeout", connect_timeout),
        ("read_timeout", read_timeout),
        ("total_timeout", timeout),
        ("retries", retries),
    )
    for key, chosen in optional:
        if chosen is not None:
            network_options[key] = chosen
    if proxy:
        network_options["proxy"] = ProxyConfig(url=proxy)
    if rate_limit:
        network_options["rate_limit"] = parse_size(rate_limit, name="rate limit")
    storage_options: dict[str, object] = {
        "strategy": storage,
        "workspace_dir": workspace,
        "check_space": space_check,
    }
    if buffer_size:
        storage_options["buffer_bytes"] = parse_size(buffer_size, name="buffer size")
    if max_temp_size:
        storage_options["max_temp_bytes"] = parse_size(max_temp_size, name="temporary size limit")

    selection_options: dict[str, object] = {
        "quality": quality,
        "max_resolution": max_resolution,
        "audio_languages": tuple(audio),
        "audio_names": tuple(audio_name),
        "subtitle_languages": tuple(subtitles),
        "subtitle_names": tuple(subtitle_name),
        "video_only": video_only,
        "variant": variant,
        "resolution": resolution,
        "variant_name": variant_name,
    }
    if bandwidth:
        selection_options["bandwidth"] = parse_size(bandwidth, name="bandwidth", decimal=True)
    if max_bitrate:
        selection_options["max_bitrate"] = parse_size(max_bitrate, name="bitrate", decimal=True)

    config_options: dict[str, object] = {"resume": resume}
    if concurrency is not None:
        config_options["concurrency"] = concurrency
    return DownloadConfig(
        network=NetworkConfig(**network_options),  # type: ignore[arg-type]
        storage=StorageConfig(**storage_options),  # type: ignore[arg-type]
        output=OutputConfig(
            format=format,
            overwrite=overwrite,
            validate=validate,
            remux_backend=remux_backend,
            ffmpeg_path=ffmpeg,
        ),
        selection=SelectionConfig(**selection_options),  # type: ignore[arg-type]
        **config_options,  # type: ignore[arg-type]
    )
