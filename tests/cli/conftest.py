"""Fixtures for the command line tests."""

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from typer.testing import CliRunner

from rayohx import (
    ContainerFormat,
    DownloadConfig,
    DownloadProgress,
    DownloadResult,
    DownloadState,
    EventBus,
    OutputInfo,
)
from tests.core.mux.helpers import Site


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No stray settings or environment variables from the machine running the tests."""
    import os

    for name in list(os.environ):
        if name.startswith("RAYOHX_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("COLUMNS", "110")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    work = tmp_path / "_cwd"
    work.mkdir()
    monkeypatch.chdir(work)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """The command adds log handlers to the library's logger; remove them after each test."""
    yield
    logger = logging.getLogger("rayohx")
    for handler in list(logger.handlers):
        if not isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(logging.NOTSET)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[[Site], Site]:
    """Make the library talk to an in-memory site instead of the network."""

    def install(site: Site) -> Site:
        from rayohx import HttpClient

        def factory(config: Any, **options: Any) -> HttpClient:
            options["transport"] = httpx.MockTransport(site)
            return HttpClient(config, **options)

        monkeypatch.setattr("rayohx.controller.HttpClient", factory)
        monkeypatch.setattr("rayohx.inspection.HttpClient", factory)
        return site

    return install


def parsed(text: str) -> Any:
    """The JSON a command printed."""
    return json.loads(text)


class FakeDownloader:
    """Stands in for ``Downloader``: records what it was asked and returns a canned result."""

    instances: ClassVar[list["FakeDownloader"]] = []
    state = DownloadState.COMPLETED
    output = Path("saved.mp4")
    error: Exception | None = None

    def __init__(
        self, url: str, *, output: Path | None = None, config: DownloadConfig | None = None
    ) -> None:
        self.url = url
        self.requested_output = output
        self.config = config
        self.events = EventBus()
        self.cancelled = False
        self.exception: Exception | None = None
        FakeDownloader.instances.append(self)

    def cancel(self) -> None:
        self.cancelled = True

    def run(self) -> DownloadResult:
        self.exception = type(self).error
        done = type(self).state is DownloadState.COMPLETED
        return DownloadResult(
            download_id="fake",
            url=self.url,
            state=type(self).state,
            elapsed_seconds=1.5,
            progress=DownloadProgress(state=type(self).state),
            output=(
                OutputInfo(path=type(self).output, container=ContainerFormat.FMP4, size_bytes=2048)
                if done
                else None
            ),
            error=None if done or type(self).state is DownloadState.CANCELLED else "it broke",
        )


@pytest.fixture
def fake_downloader(monkeypatch: pytest.MonkeyPatch) -> type[FakeDownloader]:
    FakeDownloader.instances = []
    FakeDownloader.state = DownloadState.COMPLETED
    FakeDownloader.output = Path("saved.mp4")
    FakeDownloader.error = None
    monkeypatch.setattr("rayohx_cli.cli.Downloader", FakeDownloader)
    return FakeDownloader
