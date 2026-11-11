"""The window is mocked: Cornea's ``Shell`` is replaced, so no WebView2 is needed."""

import sys
from pathlib import Path
from typing import Any

import cornea
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from example_package import __version__
from example_ui import app as ui_app


class FakeShell:
    """Records how the app wired Cornea, and what it was asked to run."""

    instances: list["FakeShell"] = []  # noqa: RUF012

    def __init__(self, source: Any, **options: Any) -> None:
        self.source = source
        self.options = options
        self.actions: dict[str, Any] = {}
        self.run_kwargs: dict[str, Any] | None = None
        FakeShell.instances.append(self)

    def action(self, name: str) -> Any:
        def register(function: Any) -> Any:
            self.actions[name] = function
            return function

        return register

    def run(self, **kwargs: Any) -> None:
        self.run_kwargs = kwargs


@pytest.fixture
def shell_on_windows(monkeypatch: pytest.MonkeyPatch) -> type[FakeShell]:
    FakeShell.instances = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cornea, "Shell", FakeShell)
    return FakeShell


def test_other_systems_get_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(ui_app.UiUnavailableError, match="Windows"):
        ui_app.build_shell()


def test_missing_cornea_gets_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "cornea", None)

    with pytest.raises(ui_app.UiUnavailableError, match="cornea-shell"):
        ui_app.build_shell()


def test_static_mode_serves_the_bundled_frontend(shell_on_windows: type[FakeShell]) -> None:
    ui_app.build_shell()

    shell = shell_on_windows.instances[0]
    assert isinstance(shell.source, cornea.Static)
    assert shell.options["window"].title == ui_app.TITLE
    assert shell.options["security"].csp == cornea.Security().csp
    assert shell.options["identity"].id == ui_app.APP_ID


def test_dev_mode_starts_vite_through_cornea(shell_on_windows: type[FakeShell]) -> None:
    ui_app.build_shell(dev=True)

    shell = shell_on_windows.instances[0]
    assert isinstance(shell.source, cornea.Process)
    assert shell.source.command == ui_app.DEV_COMMAND
    assert shell.source.mode == "proxy"
    assert shell.options["security"].csp == cornea.DEV_CSP


def test_window_uses_title_and_icon_when_the_icon_exists(
    shell_on_windows: type[FakeShell], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"png")
    monkeypatch.setattr(ui_app, "ICON", icon)

    ui_app.build_shell()

    window = shell_on_windows.instances[0].options["window"]
    assert (window.title, window.icon) == (ui_app.TITLE, icon)


def test_window_has_no_icon_when_the_file_is_missing(
    shell_on_windows: type[FakeShell], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ui_app, "ICON", tmp_path / "missing.png")

    ui_app.build_shell()

    assert shell_on_windows.instances[0].options["window"].icon is None


def test_version_comes_from_the_package() -> None:
    assert __version__ == ui_app.VERSION


def test_no_window_means_no_window_object(shell_on_windows: type[FakeShell]) -> None:
    ui_app.build_shell(window=False)

    assert shell_on_windows.instances[0].options["window"] is None


def test_native_action_is_registered(shell_on_windows: type[FakeShell]) -> None:
    ui_app.build_shell()

    action = shell_on_windows.instances[0].actions["app_info"]
    assert action() == ui_app.app_info() == {"name": ui_app.TITLE, "version": ui_app.VERSION}


def test_api_router_is_mounted_under_cornea_prefix() -> None:
    app = FastAPI()
    app.include_router(ui_app.api_router(), prefix="/api")

    response = TestClient(app).get("/api/v1/hello/Ada")

    assert response.json() == {"message": "Hello, Ada!"}


def test_api_router_leaves_authentication_to_cornea(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_API_AUTH_MODE", "api_key")
    monkeypatch.setenv("EXAMPLE_API_API_KEYS", '["s3cret"]')
    app = FastAPI()
    app.include_router(ui_app.api_router(), prefix="/api")

    response = TestClient(app).get("/api/v1/whoami")

    assert response.status_code == 200
    assert response.json()["method"] == "none"


def test_run_without_callback_just_runs(shell_on_windows: type[FakeShell]) -> None:
    ui_app.run_ui()

    assert shell_on_windows.instances[0].run_kwargs == {}


def test_run_passes_the_ready_line_to_the_callback(shell_on_windows: type[FakeShell]) -> None:
    lines: list[str] = []

    ui_app.run_ui(window=False, on_ready=lines.append)

    on_ready = shell_on_windows.instances[0].run_kwargs["on_ready"]  # type: ignore[index]
    assert callable(on_ready)
