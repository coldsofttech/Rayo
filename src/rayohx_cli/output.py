"""Rich consoles shared by every command, so all output is written the same way.

Data goes to ``console`` (stdout) and diagnostics go to ``err_console`` (stderr), so a command's
result can be piped or captured while its messages still reach the person. Rich honors the
``NO_COLOR`` environment variable and disables color when output is piped. Nothing prints with
``print``.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from rayohx import RayoHXError
from rayohx import configure_logging as configure_library_logging
from rayohx.logs import RedactionFilter

console = Console()
err_console = Console(stderr=True)


@dataclass
class Settings:
    """Global output options, set once by the Typer callback."""

    verbose: bool = False
    quiet: bool = False


settings = Settings()


def configure(*, no_color: bool, verbose: bool, quiet: bool = False) -> None:
    """Apply global output options."""
    settings.verbose = verbose
    settings.quiet = quiet
    for target in (console, err_console):
        target.no_color = no_color or target.no_color


def error(message: str, hint: str | None = None) -> None:
    """Print an error message (and what to try) to stderr."""
    err_console.print(Text(f"Error: {message}", style="bold red"))
    if hint:
        err_console.print(Text(hint, style="dim"))


def error_from(exc: RayoHXError) -> None:
    """Print a library error with its hint."""
    error(exc.message, exc.hint)


def message(text: str, *, style: str | None = None) -> None:
    """Print a status message to stderr, unless ``--quiet``."""
    if not settings.quiet:
        err_console.print(Text(text, style=style or ""))


def data(text: str) -> None:
    """Print one line of result data to stdout exactly as given (no wrapping, no markup)."""
    console.print(Text(text), soft_wrap=True, highlight=False)


def print_json(value: Any) -> None:
    """Print ``value`` as JSON to stdout. Plain text, so it is the same on every terminal."""
    text = json.dumps(value, indent=2, ensure_ascii=False)
    console.print(text, soft_wrap=True, markup=False, highlight=False)


def print_exception(exc: Exception) -> None:
    """Report an unexpected error: a short message, plus a traceback only with ``--verbose``.

    Local variables are never shown, since they can contain secrets.
    """
    error(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
    if settings.verbose:
        err_console.print_exception(show_locals=False)
    else:
        err_console.print(Text("Run with --verbose for details.", style="dim"))


class _ConsoleHandler(logging.Handler):
    """Writes log records through the error console, so they do not tear a progress bar."""

    def __init__(self, level: int) -> None:
        super().__init__(level)

    def emit(self, record: logging.LogRecord) -> None:
        text = self.format(record)
        style = "yellow" if record.levelno >= logging.WARNING else "dim"
        if record.levelno >= logging.ERROR:
            style = "red"
        err_console.print(Text(text, style=style), soft_wrap=True)


_handler: _ConsoleHandler | None = None


def configure_logging(level: int, *, verbose: bool, log_file: Path | None = None) -> None:
    """Show the library's log records on stderr at ``level``, replacing any set up before.

    With ``log_file`` everything (down to debug) is also written there, with secrets removed.
    """
    global _handler
    logger = logging.getLogger("rayohx")
    if _handler is not None:
        logger.removeHandler(_handler)
    # The library sets up (or, without a file, clears) its own file handler.
    configure_library_logging(logging.DEBUG, console=False, log_file=log_file)
    _handler = _ConsoleHandler(level)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s: %(message)s" if verbose else "%(message)s")
    )
    _handler.addFilter(RedactionFilter())
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG if log_file else level)
