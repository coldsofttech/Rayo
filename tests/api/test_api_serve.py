import pytest
import uvicorn

from example_api import __main__ as api_main
from example_api.config import get_settings


def test_serve_uses_settings_unless_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    monkeypatch.setenv("EXAMPLE_API_PORT", "9001")
    get_settings.cache_clear()

    api_main.serve()
    api_main.serve(host="0.0.0.0", port=0)  # noqa: S104

    assert (calls[0]["host"], calls[0]["port"]) == ("127.0.0.1", 9001)
    assert (calls[1]["host"], calls[1]["port"]) == ("0.0.0.0", 0)  # noqa: S104
    assert calls[0]["factory"] is True


def test_serve_refuses_to_start_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_API_AUTH_MODE", "api_key")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="EXAMPLE_API_API_KEYS"):
        api_main.serve()
