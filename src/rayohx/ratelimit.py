"""A shared speed limit for all download workers."""

import threading
import time
from collections.abc import Callable

from rayohx.cancellation import CancellationToken
from rayohx.errors import CancellationError


class RateLimiter:
    """Keeps the combined speed of every caller at or below ``bytes_per_second``.

    Each caller reserves the bytes it is about to use and sleeps for as long as the reservation
    runs ahead of the allowed speed, so any number of threads share one limit.
    """

    def __init__(
        self,
        bytes_per_second: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if bytes_per_second <= 0:
            raise ValueError("bytes_per_second must be positive")
        self._rate = float(bytes_per_second)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._allowance = self._rate  # one second of data may go out at once
        self._last = clock()

    @property
    def bytes_per_second(self) -> float:
        """The configured limit."""
        return self._rate

    def acquire(self, size: int, cancel: CancellationToken | None = None) -> None:
        """Wait until ``size`` more bytes fit under the limit.

        Raises:
            CancellationError: If ``cancel`` fires while waiting.
        """
        with self._lock:
            now = self._clock()
            self._allowance = min(self._rate, self._allowance + (now - self._last) * self._rate)
            self._last = now
            self._allowance -= size
            wait = -self._allowance / self._rate if self._allowance < 0 else 0.0
        if wait <= 0:
            return
        if cancel is not None:
            if cancel.wait(wait):
                raise CancellationError("The download was cancelled.")
        else:
            self._sleep(wait)
