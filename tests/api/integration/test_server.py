import httpx

import rayo


def test_health(base_url: str) -> None:
    assert httpx.get(f"{base_url}/health").json() == {"status": "ok"}


def test_ready_reports_version(base_url: str) -> None:
    assert httpx.get(f"{base_url}/ready").json()["version"] == rayo.__version__


def test_greeting_round_trip(base_url: str) -> None:
    created = httpx.post(f"{base_url}/api/v1/greetings", json={"name": "Ada"})
    assert created.status_code == 201

    url = f"{base_url}/api/v1/greetings/{created.json()['id']}"
    assert httpx.get(url).json()["message"] == "Hello, Ada!"
    assert httpx.delete(url).status_code == 204


def test_security_headers(base_url: str) -> None:
    assert httpx.get(f"{base_url}/health").headers["x-content-type-options"] == "nosniff"
