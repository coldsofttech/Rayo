"""The segment pipeline: workers fetch in parallel, the main thread writes in order.

Workers only fetch, decrypt and store. One thread (the caller's) takes segments out of the store
in playlist order and hands them to the sink, so sinks need no locking. New work is started only
while the storage strategy allows it, which is what keeps memory and disk use bounded.
"""

import contextvars
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from rayohx.cancellation import CancellationToken
from rayohx.config import StorageConfig
from rayohx.enums import DownloadState, EventType, StorageStrategy
from rayohx.errors import CancellationError, DownloadError, SegmentDownloadError
from rayohx.events import DownloadEvent, EventBus
from rayohx.fetcher import HlsSegmentFetcher
from rayohx.models import Segment
from rayohx.progress import ProgressTracker
from rayohx.sinks import OutputSink
from rayohx.storage import (
    DEFAULT_SPILL_BYTES,
    WINDOW_FACTOR,
    SegmentStore,
    SizeEstimator,
    StorageTracker,
)

POLL_SECONDS = 0.2
CHECKPOINT_SEGMENTS = 16
CHECKPOINT_SECONDS = 2.0
PROGRESS_SECONDS = 0.25
#: Segments measured before the disk-space estimate is checked again.
RECHECK_AFTER_SAMPLES = 3


class SegmentPipeline:
    """Downloads ``segments`` and writes them to ``sink`` in order."""

    def __init__(
        self,
        *,
        segments: Sequence[Segment],
        fetcher: HlsSegmentFetcher,
        store: SegmentStore,
        sink: OutputSink,
        strategy: StorageStrategy,
        concurrency: int,
        storage: StorageConfig,
        tracker: StorageTracker,
        progress: ProgressTracker,
        estimator: SizeEstimator,
        cancel: CancellationToken | None = None,
        bus: EventBus | None = None,
        download_id: str = "",
        start_index: int = 0,
        on_checkpoint: Callable[[int, int], None] | None = None,
        recheck_space: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._segments = segments
        self._fetcher = fetcher
        self._store = store
        self._sink = sink
        self._strategy = strategy
        self._concurrency = concurrency
        self._storage = storage
        self._tracker = tracker
        self._progress = progress
        self._estimator = estimator
        self._cancel = cancel
        self._bus = bus
        self._download_id = download_id
        self._on_checkpoint = on_checkpoint
        self._recheck_space = recheck_space
        self._clock = clock

        #: Index of the next segment to write; everything before it is already in the output.
        self.head = start_index
        self._active = 0
        self._active_lock = threading.Lock()
        self._rechecked = recheck_space is None
        self._since_checkpoint = 0
        self._last_checkpoint = clock()
        self._last_progress = 0.0

    # --- running ---------------------------------------------------------------------------

    def run(self) -> None:
        """Download everything. Raises on the first failure, after stopping the workers."""
        total = len(self._segments)
        present = self._store.existing()
        cursor = self.head
        pending: dict[Future[int], int] = {}
        pool = ThreadPoolExecutor(
            max_workers=self._concurrency, thread_name_prefix="rayohx-segment"
        )
        try:
            while True:
                self._raise_if_cancelled()
                self.head = self._drain(self.head, total)
                if self.head >= total:
                    break
                cursor = self._dispatch(pool, pending, cursor, total, present)
                if not pending and not self._store.has(self.head):
                    raise DownloadError("The download stalled: no segment is in progress.")
                done, _ = wait(pending, timeout=POLL_SECONDS, return_when=FIRST_COMPLETED)
                for future in done:
                    self._finish(future, pending.pop(future))
            # A segment can be written before its future is looked at; settle those so the
            # completion events and the progress count include every segment.
            for future, index in list(pending.items()):
                self._finish(future, index)
            self._emit_progress(force=True)
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            self._best_effort_checkpoint()
            raise
        pool.shutdown(wait=True)

    def _raise_if_cancelled(self) -> None:
        if self._cancel is not None and self._cancel.is_cancelled:
            raise CancellationError("The download was cancelled.")

    # --- starting work ---------------------------------------------------------------------

    def _dispatch(
        self,
        pool: ThreadPoolExecutor,
        pending: dict[Future[int], int],
        cursor: int,
        total: int,
        present: set[int],
    ) -> int:
        while len(pending) < self._concurrency and cursor < total:
            if cursor in present and self._store.has(cursor):
                self._progress.segments_completed()
                self._emit(EventType.SEGMENT_COMPLETED, cursor, message="Reused from earlier run.")
                cursor += 1
                continue
            if not self._admit(cursor, len(pending)):
                break
            # Each worker runs in a copy of this thread's context, so its log lines carry the
            # download id.
            context = contextvars.copy_context()
            pending[pool.submit(context.run, self._work, cursor)] = cursor
            cursor += 1
        return cursor

    def _admit(self, index: int, in_flight: int) -> bool:
        """Whether the strategy lets work on ``index`` start now. The next segment to write
        is always admitted, which rules out a deadlock."""
        if index == self.head:
            return True
        strategy = self._strategy
        expected = in_flight * self._estimator.average_segment_bytes()
        if strategy in {StorageStrategy.DIRECT, StorageStrategy.STREAMING}:
            return index < self.head + WINDOW_FACTOR * self._concurrency
        if strategy is StorageStrategy.BUFFERED:
            return self._tracker.memory_bytes + expected < self._storage.buffer_bytes
        if strategy is StorageStrategy.TEMPORARY:
            limit = self._storage.max_temp_bytes
            return limit is None or self._tracker.disk_bytes + expected < limit
        limit = self._storage.max_temp_bytes or DEFAULT_SPILL_BYTES
        return self._tracker.disk_bytes + expected < limit

    def _work(self, index: int) -> int:
        with self._active_lock:
            self._active += 1
        try:
            self._emit(EventType.SEGMENT_STARTED, index)
            data = self._fetcher.fetch(index, self._progress.downloaded)
            self._store.put(data)
            self._estimator.observe(self._segments[index].duration, data.size)
            return data.size
        finally:
            with self._active_lock:
                self._active -= 1

    def _finish(self, future: Future[int], index: int) -> None:
        try:
            size = future.result()
        except CancellationError:
            raise
        except SegmentDownloadError as exc:
            self._emit(EventType.SEGMENT_FAILED, index, message=exc.message)
            raise
        self._progress.segments_completed()
        self._emit(EventType.SEGMENT_COMPLETED, index, data={"bytes": size})
        if not self._rechecked and self._estimator.samples >= RECHECK_AFTER_SAMPLES:
            self._rechecked = True
            assert self._recheck_space is not None  # noqa: S101 - _rechecked is True without it
            self._recheck_space()

    # --- writing in order ------------------------------------------------------------------

    def _drain(self, head: int, total: int) -> int:
        while head < total and self._store.has(head):
            data = self._store.take(head)
            written = self._sink.write(data)
            self._tracker.written(written)
            head += 1
            self.head = head
            self._since_checkpoint += 1
            self._maybe_checkpoint()
            self._emit_progress()
        return head

    def _maybe_checkpoint(self, *, force: bool = False) -> None:
        due = (
            self._since_checkpoint >= CHECKPOINT_SEGMENTS
            or self._clock() - self._last_checkpoint >= CHECKPOINT_SECONDS
        )
        if not (force or due) or self._on_checkpoint is None:
            return
        durable = self._sink.checkpoint()
        self._on_checkpoint(self.head, durable)
        self._since_checkpoint = 0
        self._last_checkpoint = self._clock()

    def _best_effort_checkpoint(self) -> None:
        try:
            self._maybe_checkpoint(force=True)
        except Exception:
            return

    # --- events ----------------------------------------------------------------------------

    def _emit(
        self,
        kind: EventType,
        index: int | None = None,
        *,
        message: str | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        if self._bus is None:
            return
        self._bus.emit(
            DownloadEvent(
                type=kind,
                download_id=self._download_id,
                state=DownloadState.DOWNLOADING,
                segment_index=index,
                message=message,
                data=data or {},
            )
        )

    def _emit_progress(self, *, force: bool = False) -> None:
        if self._bus is None:
            return
        now = self._clock()
        if not force and now - self._last_progress < PROGRESS_SECONDS:
            return
        self._last_progress = now
        with self._active_lock:
            active = self._active
        self._bus.emit(
            DownloadEvent(
                type=EventType.DOWNLOAD_PROGRESS,
                download_id=self._download_id,
                state=DownloadState.DOWNLOADING,
                progress=self._progress.snapshot(DownloadState.DOWNLOADING, active),
            )
        )
