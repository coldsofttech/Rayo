"""Cooperative cancellation: ask work to stop, and let it stop cleanly."""

import threading

from rayohx.errors import CancellationError


class CancellationToken:
    """A flag that one thread sets and many workers check. Safe to share across threads."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Ask everything holding this token to stop. Calling it again does nothing."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise ``CancellationError`` if cancellation has been requested."""
        if self._event.is_set():
            raise CancellationError("The download was cancelled.")

    def wait(self, timeout: float) -> bool:
        """Sleep up to ``timeout`` seconds, waking at once if cancelled. True means cancelled."""
        return self._event.wait(timeout)
