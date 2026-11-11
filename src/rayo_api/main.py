"""Application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rayo_api.auth import authenticate, embedded_principal
from rayo_api.config import get_settings
from rayo_api.errors import register_error_handlers
from rayo_api.logs import RequestIdMiddleware, configure_logging
from rayo_api.routers import health, v1
from rayo_api.security import BodyLimitMiddleware, SecurityHeadersMiddleware
from rayo_package import __version__


def create_app(*, embedded: bool = False) -> FastAPI:
    """Build the application. Tests call this to get a fresh app.

    ``embedded=True`` is for hosting under Cornea, whose security gate already authenticates every
    request: API-key authentication and the duplicate response headers are left out. A standalone
    app refuses to start without an API key unless ``RAYO_API_AUTH_MODE=none``.
    """
    settings = get_settings()
    if not embedded:
        settings.check_auth()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="rayo-api",
        version=__version__,
        description="Example HTTP API built on **rayo_package**.",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    # Middleware added last runs first, so the order below reads from innermost to outermost:
    # body limit, security headers, request ID, CORS.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    if not embedded:
        app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-API-Key"],
        )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(v1.router, prefix="/api/v1")
    if embedded:
        app.dependency_overrides[authenticate] = embedded_principal
    return app
