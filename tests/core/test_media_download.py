"""Whole downloads against an in-memory origin: ordering, bounds, resume, cancel and cleanup."""

import threading
from pathlib import Path

import pytest

from rayohx import (
    CancellationError,
    CancellationToken,
    ConfigurationError,
    DownloadConfig,
    DownloadError,
    DownloadEvent,
    DownloadSummary,
    EventBus,
    EventType,
    FileSink,
    InsufficientStorageError,
    MediaDownload,
    OutputExistsError,
    SegmentDownloadError,
    StorageConfig,
    StorageStrategy,
    StreamSink,
    UnsupportedPlaylistError,
    Variant,
    WorkspaceLockedError,
)
from rayohx.playlist import parse_playlist
from rayohx.workspace import Workspace, resolve_workspace_root, workspace_key
from tests.core.media import BASE, Origin, client_for, worker_threads

S = StorageStrategy


def download(
    origin: Origin,
    tmp_path: Path,
    *,
    strategy: StorageStrategy = S.AUTO,
    concurrency: int = 4,
    resume: bool = True,
    keep_state: bool = True,
    overwrite: bool = False,
    token: CancellationToken | None = None,
    bus: EventBus | None = None,
    variant: Variant | None = None,
    name: str = "out.ts",
    retries: int = 0,
    **storage: object,
) -> DownloadSummary:
    config = DownloadConfig(
        concurrency=concurrency,
        resume=resume,
        storage=StorageConfig(
            strategy=strategy,
            keep_state_on_failure=keep_state,
            **storage,  # type: ignore[arg-type]
        ),
    )
    return MediaDownload(
        origin.playlist(),
        client=client_for(origin, cancel=token, retries=retries, backoff_factor=0.001),
        config=config,
        sink=FileSink(tmp_path / name, overwrite=overwrite),
        events=bus,
        cancel=token,
        variant=variant,
    ).run()


def leftovers(tmp_path: Path) -> list[str]:
    return sorted(path.name for path in tmp_path.iterdir())


def workspace_dirs(tmp_path: Path) -> list[Path]:
    return [path for path in tmp_path.iterdir() if path.name.startswith(".rayohx-")]


# --- the normal case ---------------------------------------------------------------------------


def test_segments_arriving_out_of_order_are_written_in_order(tmp_path: Path) -> None:
    origin = Origin(20)
    origin.delays = {index: (19 - index) * 0.002 for index in range(20)}
    summary = download(origin, tmp_path)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert summary.strategy is S.DIRECT
    assert summary.total_segments == 20
    assert summary.resumed_segments == 0
    assert summary.output == tmp_path / "out.ts"
    assert summary.metrics.downloaded_bytes == 20 * 500
    assert summary.metrics.written_bytes == 20 * 500
    assert summary.metrics.peak_temporary_bytes == 0
    assert summary.metrics.working_bytes == 0
    assert summary.elapsed_seconds >= 0
    assert leftovers(tmp_path) == ["out.ts"]


def test_downloads_run_in_parallel_but_never_beyond_the_limit(tmp_path: Path) -> None:
    origin = Origin(16)
    origin.delays = dict.fromkeys(range(16), 0.02)
    download(origin, tmp_path, concurrency=4)
    assert 2 <= origin.max_active <= 4


def test_one_worker_downloads_one_segment_at_a_time(tmp_path: Path) -> None:
    origin = Origin(6)
    origin.delays = dict.fromkeys(range(6), 0.005)
    download(origin, tmp_path, concurrency=1)
    assert origin.max_active == 1
    assert origin.requested() == list(range(6))


def test_a_stream_with_thousands_of_segments(tmp_path: Path) -> None:
    origin = Origin(1500, size=50)
    summary = download(origin, tmp_path, concurrency=16)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert summary.total_segments == 1500
    assert list(worker_threads()) == []


def test_fmp4_gets_its_init_section_first(tmp_path: Path) -> None:
    origin = Origin(8, ext="m4s", init=b"INIT-SECTION")
    download(origin, tmp_path, name="out.mp4")
    data = (tmp_path / "out.mp4").read_bytes()
    assert data == origin.expected()
    assert data.startswith(b"INIT-SECTION")
    assert origin.log.count("init.mp4") == 1


def test_byte_range_segments(tmp_path: Path) -> None:
    origin = Origin(10, ext="mp4", byte_ranges=True)
    download(origin, tmp_path)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()


@pytest.mark.parametrize("explicit_iv", [True, False])
def test_encrypted_streams_are_decrypted(tmp_path: Path, explicit_iv: bool) -> None:
    origin = Origin(12, encrypted=True, explicit_iv=explicit_iv)
    download(origin, tmp_path)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert origin.key_requests == 1


def test_a_segment_that_fails_once_is_retried(tmp_path: Path) -> None:
    origin = Origin(6)
    origin.flaky = {3: 2}
    download(origin, tmp_path, retries=3)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert origin.requested().count(3) == 3


# --- strategies and their limits ---------------------------------------------------------------


def head_blocked_origin(count: int = 20) -> Origin:
    origin = Origin(count)
    origin.delays = {0: 0.3}
    return origin


def started_before_head_finished(origin: Origin) -> int:
    """How many segments were requested while segment 0 was still being served."""
    head_done = origin.arrivals["seg0.ts"] + 0.25
    return sum(
        1 for name, when in origin.arrivals.items() if name.startswith("seg") and when < head_done
    )


def test_direct_only_runs_a_short_distance_ahead_of_the_slowest_segment(tmp_path: Path) -> None:
    origin = head_blocked_origin()
    download(origin, tmp_path, strategy=S.DIRECT, concurrency=2)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert started_before_head_finished(origin) <= 4  # head + a window of 2 x 2


def test_a_buffered_download_stops_when_the_buffer_is_full(tmp_path: Path) -> None:
    origin = head_blocked_origin()
    summary = download(origin, tmp_path, strategy=S.BUFFERED, concurrency=3, buffer_bytes=1000)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert started_before_head_finished(origin) <= 5
    assert summary.metrics.peak_temporary_bytes == 0


def test_temporary_storage_stages_every_segment_on_disk(tmp_path: Path) -> None:
    origin = head_blocked_origin()
    summary = download(origin, tmp_path, strategy=S.TEMPORARY, concurrency=4)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert started_before_head_finished(origin) >= 15
    assert summary.metrics.peak_temporary_bytes > 10 * 500
    assert summary.metrics.temporary_bytes == 0
    assert leftovers(tmp_path) == ["out.ts"]


def test_temporary_storage_can_be_bounded(tmp_path: Path) -> None:
    origin = head_blocked_origin()
    summary = download(origin, tmp_path, strategy=S.TEMPORARY, concurrency=3, max_temp_bytes=1500)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert started_before_head_finished(origin) <= 10
    assert summary.metrics.peak_temporary_bytes <= 1500 + 3 * 508


def test_hybrid_keeps_memory_use_down_and_spills_a_bounded_amount(tmp_path: Path) -> None:
    origin = head_blocked_origin()
    summary = download(
        origin,
        tmp_path,
        strategy=S.HYBRID,
        concurrency=3,
        buffer_bytes=600,
        max_temp_bytes=1500,
    )
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert 0 < summary.metrics.peak_temporary_bytes <= 1500 + 3 * 508
    assert leftovers(tmp_path) == ["out.ts"]


def test_streaming_to_a_pipe_needs_no_working_folder(tmp_path: Path) -> None:
    origin = Origin(10)
    received: list[bytes] = []
    summary = MediaDownload(
        origin.playlist(),
        client=client_for(origin),
        config=DownloadConfig(storage=StorageConfig(workspace_dir=tmp_path / "work")),
        sink=StreamSink(received.append),
    ).run()
    assert summary.strategy is S.STREAMING
    assert b"".join(received) == origin.expected()
    assert summary.output is None
    assert list(tmp_path.iterdir()) == []


def test_the_direct_strategy_cannot_write_to_a_pipe() -> None:
    origin = Origin(2)
    with pytest.raises(ConfigurationError, match="DIRECT"):
        MediaDownload(
            origin.playlist(),
            client=client_for(origin),
            config=DownloadConfig(storage=StorageConfig(strategy=S.DIRECT)),
            sink=StreamSink(lambda _data: None),
        ).run()
    assert origin.log == []


def test_a_configured_workspace_folder_is_used_and_cleaned(tmp_path: Path) -> None:
    origin = Origin(6)
    download(origin, tmp_path, strategy=S.TEMPORARY, workspace_dir=tmp_path / "work")
    assert list((tmp_path / "work").iterdir()) == []
    assert workspace_dirs(tmp_path) == []


# --- refusing early ----------------------------------------------------------------------------


def test_protected_streams_are_refused_before_anything_is_downloaded(tmp_path: Path) -> None:
    origin = Origin(2)
    text = (
        '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://k"\n'
        "#EXTINF:4,\nseg0.ts\n#EXT-X-ENDLIST\n"
    )
    playlist = parse_playlist(text, BASE + "index.m3u8")
    from rayohx import MediaPlaylist

    assert isinstance(playlist, MediaPlaylist)
    with pytest.raises(UnsupportedPlaylistError):
        MediaDownload(
            playlist,
            client=client_for(origin),
            config=DownloadConfig(),
            sink=FileSink(tmp_path / "out.ts"),
        ).run()
    assert origin.log == []
    assert leftovers(tmp_path) == []


def test_live_streams_are_refused(tmp_path: Path) -> None:
    origin = Origin(3, end_list=False)
    with pytest.raises(UnsupportedPlaylistError, match="live") as info:
        download(origin, tmp_path)
    assert info.value.hint
    assert origin.log == []


def test_a_playlist_without_segments_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="no segments"):
        download(Origin(0), tmp_path)


def test_an_existing_output_is_never_replaced_silently(tmp_path: Path) -> None:
    (tmp_path / "out.ts").write_bytes(b"precious")
    origin = Origin(3)
    with pytest.raises(OutputExistsError):
        download(origin, tmp_path)
    assert origin.log == []
    assert (tmp_path / "out.ts").read_bytes() == b"precious"
    assert leftovers(tmp_path) == ["out.ts"]
    download(origin, tmp_path, overwrite=True)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()


def test_too_little_disk_space_is_reported_before_downloading(tmp_path: Path) -> None:
    origin = Origin(4)
    huge = Variant(uri="v", bandwidth=10**15)
    with pytest.raises(InsufficientStorageError) as info:
        download(origin, tmp_path, variant=huge)
    assert info.value.required_bytes > info.value.available_bytes
    assert origin.log == []
    assert leftovers(tmp_path) == []


def test_the_estimate_is_checked_again_once_real_sizes_are_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int | None] = []
    original = MediaDownload._check_space

    def counting(self: MediaDownload, strategy: S, estimator: object, *rest: Path) -> None:
        calls.append(getattr(estimator, "samples", None))
        original(self, strategy, estimator, *rest)  # type: ignore[arg-type]

    monkeypatch.setattr(MediaDownload, "_check_space", counting)
    download(Origin(10), tmp_path)
    assert len(calls) == 2
    assert calls[0] == 0
    assert calls[1] is not None
    assert calls[1] >= 3


def test_a_workspace_in_use_by_another_download_is_not_touched(tmp_path: Path) -> None:
    origin = Origin(4)
    playlist = origin.playlist()
    key = workspace_key(playlist.uri, "out.ts")
    root = resolve_workspace_root(None, tmp_path / "out.ts", key)
    with Workspace(root), pytest.raises(WorkspaceLockedError):
        download(origin, tmp_path)
    assert origin.log == []


# --- failure and resume ------------------------------------------------------------------------


def read_state(tmp_path: Path) -> dict[str, object]:
    import json

    (folder,) = workspace_dirs(tmp_path)
    return dict(json.loads((folder / "state.json").read_text()))


def test_a_failed_download_keeps_only_what_resuming_needs(tmp_path: Path) -> None:
    origin = Origin(20)
    origin.fail = {7: 404}
    origin.delays = {7: 0.1}
    with pytest.raises(SegmentDownloadError) as info:
        download(origin, tmp_path)
    assert info.value.index == 7
    assert "404" in str(info.value)
    assert not (tmp_path / "out.ts").exists()
    assert sorted(leftovers(tmp_path)) == [
        ".rayohx-" + workspace_dirs(tmp_path)[0].name[8:],
        "out.ts.part",
    ]
    state = read_state(tmp_path)
    assert state["next_index"] == 7
    assert state["written_bytes"] == 7 * 500
    assert "token" not in str(state)
    assert list(worker_threads()) == []


def test_a_resumed_download_does_not_fetch_finished_segments_again(tmp_path: Path) -> None:
    origin = Origin(20)
    origin.fail = {7: 404}
    origin.delays = {7: 0.1}
    with pytest.raises(SegmentDownloadError):
        download(origin, tmp_path)
    origin.fail.clear()
    origin.log.clear()
    summary = download(origin, tmp_path)
    assert min(origin.requested()) == 7
    assert summary.resumed_segments == 7
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert leftovers(tmp_path) == ["out.ts"]


def test_staged_segments_survive_a_failure_and_are_reused(tmp_path: Path) -> None:
    origin = Origin(20)
    origin.fail = {5: 500}
    origin.delays = {5: 0.15}
    with pytest.raises(SegmentDownloadError):
        download(origin, tmp_path, strategy=S.TEMPORARY)
    (folder,) = workspace_dirs(tmp_path)
    staged = {int(path.stem) for path in (folder / "segments").glob("*.seg")}
    assert staged
    assert min(staged) > 5
    origin.fail.clear()
    origin.log.clear()
    summary = download(origin, tmp_path, strategy=S.TEMPORARY)
    assert staged.isdisjoint(origin.requested())
    assert summary.resumed_segments == 5 + len(staged)
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert leftovers(tmp_path) == ["out.ts"]


def test_without_resume_a_failure_leaves_nothing_behind(tmp_path: Path) -> None:
    origin = Origin(10)
    origin.fail = {4: 404}
    with pytest.raises(SegmentDownloadError):
        download(origin, tmp_path, resume=False)
    assert leftovers(tmp_path) == []


def test_state_can_be_discarded_on_failure(tmp_path: Path) -> None:
    origin = Origin(10)
    origin.fail = {4: 404}
    with pytest.raises(SegmentDownloadError):
        download(origin, tmp_path, keep_state=False, strategy=S.TEMPORARY)
    assert leftovers(tmp_path) == []


def test_resume_can_be_switched_off_to_start_over(tmp_path: Path) -> None:
    origin = Origin(12)
    origin.fail = {6: 404}
    origin.delays = {6: 0.1}
    with pytest.raises(SegmentDownloadError):
        download(origin, tmp_path)
    origin.fail.clear()
    origin.log.clear()
    summary = download(origin, tmp_path, resume=False)
    assert sorted(origin.requested()) == list(range(12))
    assert summary.resumed_segments == 0
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()


def test_a_deleted_partial_file_means_starting_over(tmp_path: Path) -> None:
    origin = Origin(12)
    origin.fail = {6: 404}
    origin.delays = {6: 0.1}
    with pytest.raises(SegmentDownloadError):
        download(origin, tmp_path)
    (tmp_path / "out.ts.part").unlink()
    origin.fail.clear()
    origin.log.clear()
    summary = download(origin, tmp_path)
    assert 0 in origin.requested()
    assert summary.resumed_segments == 0
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()


def test_leftovers_of_a_different_stream_are_never_reused(tmp_path: Path) -> None:
    first = Origin(20)
    first.fail = {5: 500}
    first.delays = {5: 0.15}
    with pytest.raises(SegmentDownloadError):
        download(first, tmp_path, strategy=S.TEMPORARY)

    second = Origin(25, size=300)  # same address, different stream
    download(second, tmp_path, strategy=S.TEMPORARY, overwrite=True)
    assert (tmp_path / "out.ts").read_bytes() == second.expected()
    assert sorted(set(second.requested())) == list(range(25))
    assert leftovers(tmp_path) == ["out.ts"]


# --- cancellation ------------------------------------------------------------------------------


def cancel_after(token: CancellationToken, completed: int) -> EventBus:
    bus = EventBus()
    count = {"done": 0}

    def listener(event: DownloadEvent) -> None:
        if event.type is EventType.SEGMENT_COMPLETED:
            count["done"] += 1
            if count["done"] == completed:
                token.cancel()

    bus.subscribe(listener)
    return bus


def test_cancelling_stops_cleanly_and_can_be_resumed(tmp_path: Path) -> None:
    origin = Origin(40)
    origin.delays = dict.fromkeys(range(40), 0.005)
    token = CancellationToken()
    with pytest.raises(CancellationError):
        download(origin, tmp_path, token=token, bus=cancel_after(token, 5), concurrency=2)
    assert list(worker_threads()) == []
    assert not (tmp_path / "out.ts").exists()
    assert (tmp_path / "out.ts.part").exists()
    saved = read_state(tmp_path)["next_index"]
    assert isinstance(saved, int)
    assert len(origin.log) < 40

    origin.log.clear()
    summary = download(origin, tmp_path)
    assert summary.resumed_segments == saved
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert leftovers(tmp_path) == ["out.ts"]


def test_cancelling_without_resume_removes_everything(tmp_path: Path) -> None:
    origin = Origin(30)
    origin.delays = dict.fromkeys(range(30), 0.005)
    token = CancellationToken()
    with pytest.raises(CancellationError):
        download(
            origin, tmp_path, token=token, bus=cancel_after(token, 3), concurrency=2, resume=False
        )
    assert list(worker_threads()) == []
    assert leftovers(tmp_path) == []


def test_a_download_cancelled_before_it_starts_does_nothing(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    origin = Origin(5)
    with pytest.raises(CancellationError):
        download(origin, tmp_path, token=token)
    assert origin.log == []
    assert not (tmp_path / "out.ts").exists()


# --- events and progress -----------------------------------------------------------------------


def test_events_describe_the_download(tmp_path: Path) -> None:
    origin = Origin(12)
    bus = EventBus()
    events: list[DownloadEvent] = []
    lock = threading.Lock()

    def listener(event: DownloadEvent) -> None:
        with lock:
            events.append(event)

    bus.subscribe(listener)
    download(origin, tmp_path, bus=bus)
    kinds = [event.type for event in events]
    assert kinds.count(EventType.SEGMENT_STARTED) == 12
    assert kinds.count(EventType.SEGMENT_COMPLETED) == 12
    assert EventType.SEGMENT_FAILED not in kinds
    progress = [event.progress for event in events if event.type is EventType.DOWNLOAD_PROGRESS]
    assert progress
    final = progress[-1]
    assert final is not None
    assert final.completed_segments == 12
    assert final.total_segments == 12
    assert final.written_bytes == 12 * 500
    assert final.downloaded_bytes == 12 * 500
    assert final.eta_seconds == 0
    assert final.fraction == 1.0
    assert {event.download_id for event in events} == {events[0].download_id}
    assert {
        event.segment_index for event in events if event.type is EventType.SEGMENT_STARTED
    } == set(range(12))


def test_a_failed_segment_is_announced(tmp_path: Path) -> None:
    origin = Origin(10)
    origin.fail = {3: 404}
    bus = EventBus()
    failures: list[DownloadEvent] = []
    bus.subscribe(lambda e: failures.append(e) if e.type is EventType.SEGMENT_FAILED else None)
    with pytest.raises(SegmentDownloadError):
        download(origin, tmp_path, bus=bus)
    assert [event.segment_index for event in failures] == [3]
    assert "404" in (failures[0].message or "")
