"""Logging for the engine, on top of the standard ``logging`` module.

The library only creates loggers under ``rayohx``. It never configures the root logger, and it
writes to stderr (never stdout). ``configure_logging`` is for applications such as the command line.
Every record passes through a filter that removes secrets.
"""

import logging
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from rayohx.redaction import redact_text

ROOT_LOGGER_NAME = "rayohx"
LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(download_id)s] %(name)s: %(message)s"

_download_id: ContextVar[str] = ContextVar("rayohx_download_id", default="-")
_HANDLER_MARK = "_rayohx_handler"


def set_download_id(download_id: str) -> None:
    """Tag the log records of the current thread or task with ``download_id``."""
    _download_id.set(download_id)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the ``rayohx`` namespace."""
    if not name or name == ROOT_LOGGER_NAME:
        return logging.getLogger(ROOT_LOGGER_NAME)
    if name.startswith(f"{ROOT_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def _redact_arg(value: Any) -> Any:
    return redact_text(value) if isinstance(value, str) else value


class RedactionFilter(logging.Filter):
    """Adds the download id and strips secrets from the message and its arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.download_id = _download_id.get()
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact_arg(arg) for key, arg in record.args.items()}
        if record.exc_info and record.exc_info[1] is not None:
            # Format the traceback now so the stored text can be cleaned too.
            record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        return True


def configure_logging(
    level: int | str = logging.INFO,
    *,
    console: bool = True,
    log_file: Path | str | None = None,
) -> logging.Logger:
    """Send ``rayohx`` log records to stderr and, optionally, to a file.

    Calling it again replaces the handlers it added before, so it is safe to repeat.
    """
    logger = get_logger()
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
            handler.close()

    logger.setLevel(level)
    handlers: list[logging.Handler] = []
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(RedactionFilter())
        setattr(handler, _HANDLER_MARK, True)
        logger.addHandler(handler)
    return logger


# A library must stay quiet until the application asks for logs.
logging.getLogger(ROOT_LOGGER_NAME).addHandler(logging.NullHandler())
