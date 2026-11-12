from rayohx import DownloadState
from rayohx.progress import SPEED_WINDOW, ProgressTracker
from rayohx.storage import SizeEstimator, StorageTracker


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def read(self) -> float:
        return self.now


def tracker(
    total_segments: int = 10, bits: int | None = 8000, clock: Clock | None = None
) -> tuple[ProgressTracker, Clock, StorageTracker]:
    clock = clock or Clock()
    storage = StorageTracker()
    estimator = SizeEstimator([1.0] * total_segments, bits)
    return ProgressTracker(total_segments, estimator, storage, clock=clock.read), clock, storage


def test_nothing_has_happened_yet() -> None:
    progress, _, _ = tracker()
    snapshot = progress.snapshot(DownloadState.DOWNLOADING, 0)
    assert snapshot.downloaded_bytes == 0
    assert snapshot.speed == 0
    assert snapshot.eta_seconds is None
    assert snapshot.total_bytes == 10_000
    assert snapshot.total_segments == 10


def test_speed_eta_and_counts() -> None:
    progress, clock, storage = tracker()
    clock.now += 2
    progress.downloaded(1000)
    progress.downloaded(1000)
    progress.segments_completed(2)
    storage.written(2000)
    snapshot = progress.snapshot(DownloadState.DOWNLOADING, 3)
    assert snapshot.downloaded_bytes == 2000
    assert snapshot.written_bytes == 2000
    assert snapshot.completed_segments == 2
    assert snapshot.active_workers == 3
    assert snapshot.average_speed == 1000
    assert snapshot.speed == 1000
    assert snapshot.eta_seconds == 8.0
    assert snapshot.fraction == 0.2


def test_current_speed_only_looks_at_recent_data() -> None:
    progress, clock, _ = tracker()
    clock.now += 1
    progress.downloaded(10_000)
    clock.now += SPEED_WINDOW + 10
    progress.downloaded(100)
    clock.now += 1
    progress.downloaded(100)
    snapshot = progress.snapshot(DownloadState.DOWNLOADING, 1)
    assert snapshot.speed == 100
    assert snapshot.average_speed < 1000


def test_finished_downloads_have_no_time_left() -> None:
    progress, clock, _ = tracker(total_segments=2)
    clock.now += 1
    progress.downloaded(500)
    progress.segments_completed(2)
    assert progress.snapshot(DownloadState.FINALIZING, 0).eta_seconds == 0.0


def test_the_eta_is_unknown_without_a_size_estimate() -> None:
    progress, clock, _ = tracker(bits=None)
    clock.now += 1
    progress.downloaded(500)
    snapshot = progress.snapshot(DownloadState.DOWNLOADING, 1)
    assert snapshot.total_bytes is None
    assert snapshot.eta_seconds is None
    assert snapshot.fraction == 0
