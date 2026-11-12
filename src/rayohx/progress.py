"""Speed, ETA and the other numbers a progress display needs."""

import threading
import time
from collections import deque
from collections.abc import Callable

from rayohx.enums import DownloadState
from rayohx.models import DownloadProgress
from rayohx.storage import SizeEstimator, StorageTracker

#: How far back "current speed" looks, in seconds.
SPEED_WINDOW = 5.0
_SAMPLE_GAP = 0.1


class ProgressTracker:
    """Collects progress from many threads and turns it into ``DownloadProgress`` snapshots."""

    def __init__(
        self,
        total_segments: int,
        estimator: SizeEstimator,
        storage: StorageTracker,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._total_segments = total_segments
        self._estimator = estimator
        self._storage = storage
        self._clock = clock
        self._lock = threading.Lock()
        self._started = clock()
        self._samples: deque[tuple[float, int]] = deque([(self._started, 0)])
        self._downloaded = 0
        self._completed = 0

    def downloaded(self, size: int) -> None:
        """Count bytes that came off the network."""
        self._storage.downloaded(size)
        now = self._clock()
        with self._lock:
            self._downloaded += size
            if now - self._samples[-1][0] < _SAMPLE_GAP and len(self._samples) > 1:
                self._samples[-1] = (self._samples[-1][0], self._downloaded)
            else:
                self._samples.append((now, self._downloaded))
            while len(self._samples) > 2 and now - self._samples[0][0] > SPEED_WINDOW:
                self._samples.popleft()

    def segments_completed(self, count: int = 1) -> None:
        """Count finished segments, including ones a previous run already had."""
        with self._lock:
            self._completed += count

    def snapshot(self, state: DownloadState, active_workers: int) -> DownloadProgress:
        """The figures right now."""
        now = self._clock()
        metrics = self._storage.snapshot()
        total = self._estimator.total_bytes()
        with self._lock:
            first, last = self._samples[0], self._samples[-1]
            span = last[0] - first[0]
            speed = (last[1] - first[1]) / span if span > 0 else 0.0
            elapsed = now - self._started
            average = self._downloaded / elapsed if elapsed > 0 else 0.0
            completed = self._completed
            downloaded = self._downloaded
        eta: float | None = None
        if completed >= self._total_segments:
            eta = 0.0
        elif total and average > 0:
            eta = max(0.0, (total - downloaded) / average)
        return DownloadProgress(
            state=state,
            downloaded_bytes=downloaded,
            written_bytes=metrics.written_bytes,
            total_bytes=total,
            completed_segments=completed,
            total_segments=self._total_segments,
            speed=speed,
            average_speed=average,
            eta_seconds=eta,
            active_workers=active_workers,
            storage=metrics,
        )
