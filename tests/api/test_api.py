import json

import pytest
from fastapi.testclient import TestClient

from example_api.config import get_settings
from example_api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert body["version"]


def test_hello(client: TestClient) -> None:
    response = client.get("/api/v1/hello/Ada")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello, Ada!"}


def test_hello_blank_name_is_a_problem(client: TestClient) -> None:
    response = client.get("/api/v1/hello/%20")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["detail"] == "name must not be empty"


def test_greeting_lifecycle(client: TestClient) -> None:
    created = client.post("/api/v1/greetings", json={"name": "Ada"})
    assert created.status_code == 201
    assert created.json() == {"id": 1, "name": "Ada", "message": "Hello, Ada!"}

    assert client.get("/api/v1/greetings").json() == [created.json()]
    assert client.get("/api/v1/greetings/1").json() == created.json()

    replaced = client.put("/api/v1/greetings/1", json={"name": "Grace"})
    assert replaced.json()["message"] == "Hello, Grace!"

    unchanged = client.patch("/api/v1/greetings/1", json={})
    assert unchanged.json()["name"] == "Grace"
    patched = client.patch("/api/v1/greetings/1", json={"name": "Linus"})
    assert patched.json()["name"] == "Linus"

    assert client.delete("/api/v1/greetings/1").status_code == 204
    assert client.get("/api/v1/greetings").json() == []


def test_missing_greeting_is_404(client: TestClient) -> None:
    for response in (
        client.get("/api/v1/greetings/5"),
        client.put("/api/v1/greetings/5", json={"name": "Ada"}),
        client.patch("/api/v1/greetings/5", json={"name": "Ada"}),
        client.delete("/api/v1/greetings/5"),
    ):
        assert response.status_code == 404
        assert response.json()["title"] == "Not Found"


def test_blank_names_are_rejected(client: TestClient) -> None:
    client.post("/api/v1/greetings", json={"name": "Ada"})

    assert client.post("/api/v1/greetings", json={"name": " "}).status_code == 422
    assert client.put("/api/v1/greetings/1", json={"name": " "}).status_code == 422
    assert client.patch("/api/v1/greetings/1", json={"name": " "}).status_code == 422


def test_invalid_body_lists_errors(client: TestClient) -> None:
    response = client.post("/api/v1/greetings", json={})

    assert response.status_code == 422
    assert response.json()["errors"]


def test_request_id_is_reused_or_created(client: TestClient) -> None:
    assert client.get("/health", headers={"X-Request-ID": "abc"}).headers["X-Request-ID"] == "abc"
    assert client.get("/health").headers["X-Request-ID"]


def test_security_headers(client: TestClient) -> None:
    response = client.get("/api/v1/hello/Ada")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_hsts_only_over_https() -> None:
    https = TestClient(create_app(), base_url="https://testserver")

    assert "Strict-Transport-Security" in https.get("/health").headers
    assert "Strict-Transport-Security" not in TestClient(create_app()).get("/health").headers


def test_docs_are_hidden_by_default(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_can_be_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_API_DOCS_ENABLED", "true")
    get_settings.cache_clear()

    docs = TestClient(create_app())

    assert docs.get("/openapi.json").status_code == 200
    assert "default-src 'none'" not in docs.get("/docs").headers.get("Content-Security-Policy", "")


def test_oversized_body_is_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_API_MAX_BODY_BYTES", "10")
    get_settings.cache_clear()

    response = TestClient(create_app()).post("/api/v1/greetings", json={"name": "A" * 50})

    assert response.status_code == 413


def test_unexpected_errors_do_not_leak_details() -> None:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret detail")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    assert "secret" not in response.text
    assert json.loads(response.text)["title"] == "Internal Server Error"


def test_cors_allows_configured_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_API_CORS_ORIGINS", '["https://app.example.com"]')
    get_settings.cache_clear()

    response = TestClient(create_app()).get(
        "/health", headers={"Origin": "https://app.example.com"}
    )

    assert response.headers["access-control-allow-origin"] == "https://app.example.com"


class TestAuth:
    @pytest.fixture(autouse=True)
    def _api_key_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXAMPLE_API_AUTH_MODE", "api_key")
        monkeypatch.setenv("EXAMPLE_API_API_KEYS", '["s3cret"]')
        get_settings.cache_clear()

    def test_whoami_needs_a_valid_key(self) -> None:
        client = TestClient(create_app())

        missing = client.get("/api/v1/whoami")
        wrong = client.get("/api/v1/whoami", headers={"X-API-Key": "nope"})
        valid = client.get("/api/v1/whoami", headers={"X-API-Key": "s3cret"})

        assert missing.status_code == wrong.status_code == 401
        assert missing.headers["WWW-Authenticate"] == "ApiKey"
        assert valid.json() == {"subject": "api-key", "method": "api_key"}

    def test_standalone_app_refuses_to_start_without_a_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EXAMPLE_API_API_KEYS")
        get_settings.cache_clear()

        with pytest.raises(ValueError, match="EXAMPLE_API_API_KEYS"):
            create_app()

    def test_embedded_app_trusts_cornea_instead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXAMPLE_API_API_KEYS")
        get_settings.cache_clear()

        response = TestClient(create_app(embedded=True)).get("/api/v1/whoami")

        assert response.json() == {"subject": "cornea-session", "method": "cornea"}

    def test_auth_can_be_switched_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXAMPLE_API_AUTH_MODE", "none")
        get_settings.cache_clear()

        response = TestClient(create_app()).get("/api/v1/whoami")

        assert response.json() == {"subject": "anonymous", "method": "none"}
