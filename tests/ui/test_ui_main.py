import pytest

from rayohx_ui import __main__ as ui_main
from rayohx_ui.app import UiUnavailableError


class Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.error:
            raise self.error


def test_defaults_open_a_window(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(ui_main, "run_ui", recorder)

    assert ui_main.main([]) == 0
    assert recorder.calls == [{"dev": False, "window": True, "on_ready": None}]


def test_options_are_passed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(ui_main, "run_ui", recorder)

    ui_main.main(["--dev", "--no-window"])

    assert recorder.calls == [{"dev": True, "window": False, "on_ready": ui_main._show_ready}]


def test_ready_line_goes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    ui_main._show_ready('{"url": "http://127.0.0.1:1/?t=x"}')

    assert capsys.readouterr().out == '{"url": "http://127.0.0.1:1/?t=x"}\n'


def test_unavailable_ui_is_a_short_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ui_main, "run_ui", Recorder(UiUnavailableError("needs Windows")))

    assert ui_main.main([]) == 1
    assert capsys.readouterr().err == "Error: needs Windows\n"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        ui_main.main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("rayohx-ui ")
