from collections.abc import Iterator

import pytest

from example_api.config import get_settings
from example_api.routers.v1 import get_store


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test in an ``integration`` folder (core, cli, api, ui) as an integration test."""
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test new settings and an empty greeting store, with no API key needed."""
    monkeypatch.setenv("EXAMPLE_API_AUTH_MODE", "none")
    monkeypatch.delenv("EXAMPLE_API_API_KEYS", raising=False)
    get_settings.cache_clear()
    get_store.cache_clear()
    yield
    get_settings.cache_clear()
    get_store.cache_clear()
