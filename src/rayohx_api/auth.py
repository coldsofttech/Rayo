"""Authentication for protected routes, used only when the API runs on its own.

Under Cornea the API is mounted behind its security gate and this check is switched off (see
``create_app(embedded=True)``). A standalone run requires an ``X-API-Key`` header unless
``RAYOHX_API_AUTH_MODE=none`` is set for local development.
"""

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from rayohx_api.config import AuthMode, Settings, get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Who made the request."""

    subject: str
    method: str


def authenticate(
    settings: Annotated[Settings, Depends(get_settings)],
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> Principal:
    """Dependency that returns the caller, or raises 401."""
    if settings.auth_mode is AuthMode.NONE:
        return Principal(subject="anonymous", method=AuthMode.NONE)
    if api_key is not None and any(
        secrets.compare_digest(api_key.encode(), key.get_secret_value().encode())
        for key in settings.api_keys
    ):
        return Principal(subject="api-key", method=AuthMode.API_KEY)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key.",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def embedded_principal() -> Principal:
    """Replacement for ``authenticate`` when Cornea has already authenticated the request."""
    return Principal(subject="cornea-session", method="cornea")
