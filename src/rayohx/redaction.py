"""Removal of secrets from URLs, headers and free text before they are logged or shown."""

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

REDACTED = "***"

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
    }
)
_SENSITIVE_NAME_PARTS = ("token", "secret", "password", "passwd", "signature", "credential")
_SENSITIVE_QUERY_KEYS = frozenset({"key", "sig", "auth", "apikey", "api_key", "api-key", "policy"})

_URL = re.compile(r"""https?://[^\s'"<>]+""", re.IGNORECASE)
_SCHEME = re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_COOKIE = re.compile(r"(\b(?:set-)?cookie\b['\"]?\s*[:=]\s*)[^\r\n]*", re.IGNORECASE)
_KEYWORD = re.compile(
    r"(\b(?:authorization|proxy-authorization|x-api-key|x-auth-token|api[_-]?key|password|passwd"
    r"|secret|(?:access[_-]?)?token)\b['\"]?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&}]+)",
    re.IGNORECASE,
)


def _is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in SENSITIVE_HEADERS
        or lowered in _SENSITIVE_QUERY_KEYS
        or any(part in lowered for part in _SENSITIVE_NAME_PARTS)
    )


def redact_url(url: str) -> str:
    """Return ``url`` without credentials in its user info or sensitive query values."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return REDACTED
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{REDACTED}@{netloc.rpartition('@')[2]}"
    query = parts.query
    if query:
        pairs = []
        for pair in query.split("&"):
            name, sep, _value = pair.partition("=")
            pairs.append(f"{name}={REDACTED}" if sep and _is_sensitive_name(name) else pair)
        query = "&".join(pairs)
    scheme = f"{parts.scheme}:" if parts.scheme else ""
    prefix = f"{scheme}//{netloc}" if netloc else scheme
    rebuilt = prefix + parts.path
    if query:
        rebuilt += f"?{query}"
    if parts.fragment:
        rebuilt += f"#{parts.fragment}"
    return rebuilt


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values replaced."""
    return {
        name: REDACTED if _is_sensitive_name(name) else value for name, value in headers.items()
    }


def redact_text(text: str) -> str:
    """Remove URLs' credentials, bearer tokens, cookies and key/value secrets from ``text``."""
    text = _URL.sub(lambda match: redact_url(match.group(0)), text)
    text = _SCHEME.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
    text = _COOKIE.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    return _KEYWORD.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
