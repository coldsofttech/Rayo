"""Start the server with ``python -m rayo_api`` (``rayo-cli api`` does the same)."""

import uvicorn

from rayo_api.config import get_settings


def serve(host: str | None = None, port: int | None = None) -> None:
    """Run uvicorn. Arguments left out come from the settings.

    Raises:
        ValueError: If authentication is misconfigured (no API key and auth not switched off).
    """
    settings = get_settings()
    settings.check_auth()
    # log_config=None keeps uvicorn's logs on our JSON handler.
    uvicorn.run(
        "rayo_api.main:create_app",
        factory=True,
        host=host or settings.host,
        port=port if port is not None else settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    serve()
