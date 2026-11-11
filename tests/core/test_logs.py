import logging
from pathlib import Path

import pytest

from rayohx.logs import (
    ROOT_LOGGER_NAME,
    RedactionFilter,
    configure_logging,
    get_logger,
    set_download_id,
)


@pytest.fixture(autouse=True)
def _clean_logger() -> object:
    yield None
    configure_logging(console=False)
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    for handler in list(logger.handlers):
        if not isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
    set_download_id("-")


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("rayohx.t", logging.INFO, __file__, 1, msg, args or None, None)


def test_get_logger_stays_in_the_namespace() -> None:
    assert get_logger().name == "rayohx"
    assert get_logger("rayohx").name == "rayohx"
    assert get_logger("http").name == "rayohx.http"
    assert get_logger("rayohx.http").name == "rayohx.http"


def test_library_is_silent_by_default() -> None:
    handlers = logging.getLogger(ROOT_LOGGER_NAME).handlers
    assert any(isinstance(handler, logging.NullHandler) for handler in handlers)


def test_filter_redacts_message_and_args() -> None:
    record = _record("fetch %s with %s", "https://u:pw@h/a?token=abc", "Bearer xyz")
    assert RedactionFilter().filter(record)
    text = record.getMessage()
    for secret in ("pw@", "abc", "xyz"):
        assert secret not in text


def test_filter_redacts_dict_args_and_exceptions() -> None:
    record = logging.LogRecord(
        "rayohx.t", logging.ERROR, __file__, 1, "%(url)s", ({"url": "https://h/a?token=zzz"},), None
    )
    RedactionFilter().filter(record)
    assert "zzz" not in record.getMessage()

    try:
        raise ValueError("Authorization: Bearer leaked")
    except ValueError:
        import sys

        error_record = logging.LogRecord(
            "rayohx.t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    RedactionFilter().filter(error_record)
    assert error_record.exc_text is not None
    assert "leaked" not in error_record.exc_text
    assert error_record.exc_info is None


def test_configure_logging_writes_file_with_download_id(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "rayohx.log"
    configure_logging("DEBUG", console=False, log_file=log_file)
    set_download_id("dl-42")
    get_logger("test").info("got https://h/a?sig=topsecret")
    for handler in logging.getLogger(ROOT_LOGGER_NAME).handlers:
        handler.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "dl-42" in text
    assert "topsecret" not in text
    assert "got https://h/a?sig=***" in text


def test_configure_logging_is_repeatable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    logger = configure_logging("INFO")
    marked = [handler for handler in logger.handlers if getattr(handler, "_rayohx_handler", False)]
    assert len(marked) == 1
    get_logger("test").warning("once")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("once") == 1
