"""Start the UI with ``example-ui`` or ``python -m example_ui``.

This is the one place in ``example_ui`` that shows output and sets the exit code, so the rest of
the package stays free of both and the UI executable can be built from ``main``.
"""

import argparse
import sys
from collections.abc import Sequence

from example_package import __version__
from example_ui.app import UiUnavailableError, run_ui


def _show_ready(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the UI and return the exit code."""
    parser = argparse.ArgumentParser(prog="example-ui", description="Open the app.")
    parser.add_argument("--version", action="version", version=f"example-ui {__version__}")
    parser.add_argument(
        "--dev", action="store_true", help="run the Vite dev server for hot reload (needs Node)"
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="only serve and print the address, so a browser can be used",
    )
    args = parser.parse_args(argv)
    try:
        run_ui(
            dev=args.dev,
            window=not args.no_window,
            on_ready=_show_ready if args.no_window else None,
        )
    except UiUnavailableError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
