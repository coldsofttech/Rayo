import errno
from pathlib import Path

import pytest

from rayohx import (
    ConfigurationError,
    InsufficientStorageError,
    OutputError,
    SegmentData,
    StorageConfig,
    StorageStrategy,
)
from rayohx.storage import (
    DEFAULT_SPILL_BYTES,
    ESTIMATE_MARGIN,
    DiskStore,
    MemoryStore,
    SizeEstimator,
    SpaceNeed,
    SpillStore,
    StorageTracker,
    check_disk_space,
    choose_strategy,
    free_bytes,
    make_store,
    nearest_existing,
    same_volume,
    space_needed,
    translate_os_error,
    validate_strategy,
)

GIB = 1024**3
S = StorageStrategy


def seg(index: int, size: int = 100, init: bytes = b"") -> SegmentData:
    return SegmentData(index=index, media=bytes([index % 256]) * size, init=init)


# --- strategy ----------------------------------------------------------------------------------


def test_auto_writes_straight_to_a_file_or_streams() -> None:
    assert choose_strategy(S.AUTO, sink_supports_resume=True) is S.DIRECT
    assert choose_strategy(S.AUTO, sink_supports_resume=False) is S.STREAMING
    for explicit in (S.TEMPORARY, S.HYBRID, S.BUFFERED, S.DIRECT, S.STREAMING):
        assert choose_strategy(explicit, sink_supports_resume=True) is explicit


def test_direct_needs_a_resumable_file() -> None:
    validate_strategy(S.DIRECT, sink_supports_resume=True)
    validate_strategy(S.STREAMING, sink_supports_resume=False)
    with pytest.raises(ConfigurationError, match="DIRECT"):
        validate_strategy(S.DIRECT, sink_supports_resume=False)


# --- estimates ---------------------------------------------------------------------------------


def test_the_estimate_starts_from_the_bitrate() -> None:
    estimator = SizeEstimator([4.0] * 10, 8_000_000)
    assert estimator.total_bytes() == 40_000_000
    assert estimator.bytes_per_second() == 1_000_000
    assert estimator.average_segment_bytes() == 4_000_000
    assert estimator.samples == 0


def test_the_estimate_learns_from_real_segments() -> None:
    estimator = SizeEstimator([4.0] * 10, 8_000_000)
    estimator.observe(4.0, 2_000_000)
    estimator.observe(4.0, 2_000_000)
    assert estimator.samples == 2
    assert estimator.total_bytes() == 20_000_000
    assert estimator.average_segment_bytes() == 2_000_000


def test_without_a_bitrate_nothing_is_known_until_a_segment_arrives() -> None:
    estimator = SizeEstimator([4.0] * 5, None)
    assert estimator.total_bytes() is None
    assert estimator.average_segment_bytes() == 0
    estimator.observe(4.0, 1000)
    assert estimator.total_bytes() == 5000


def test_the_estimator_uses_the_average_bitrate_when_given() -> None:
    from rayohx import MediaPlaylist, Segment, Variant

    playlist = MediaPlaylist(
        uri="u",
        target_duration=4,
        segments=tuple(
            Segment(index=i, uri=f"{i}.ts", duration=4.0, sequence_number=i) for i in range(5)
        ),
    )
    plain = SizeEstimator.for_playlist(playlist, Variant(uri="v", bandwidth=8_000_000))
    averaged = SizeEstimator.for_playlist(
        playlist, Variant(uri="v", bandwidth=8_000_000, average_bandwidth=4_000_000)
    )
    unknown = SizeEstimator.for_playlist(playlist, None)
    assert plain.total_bytes() == 20_000_000
    assert averaged.total_bytes() == 10_000_000
    assert unknown.total_bytes() is None


# --- how much space each strategy needs --------------------------------------------------------


def need(
    strategy: StorageStrategy, config: StorageConfig | None = None, **options: bool
) -> SpaceNeed:
    return space_needed(strategy, 10 * GIB, config or StorageConfig(), **options)


def test_a_direct_download_needs_the_output_and_no_working_space() -> None:
    result = need(S.DIRECT)
    assert result.output_bytes == int(10 * GIB * ESTIMATE_MARGIN)
    assert result.workspace_bytes == 0
    # The old tool needed twice the output; this one must not.
    assert result.output_bytes + result.workspace_bytes < 2 * 10 * GIB


def test_streaming_to_a_pipe_needs_no_disk() -> None:
    assert need(S.STREAMING, writes_file=False) == SpaceNeed(0, 0)
    assert need(S.BUFFERED, writes_file=False) == SpaceNeed(0, 0)


def test_temporary_storage_is_bounded_when_a_limit_is_set() -> None:
    unlimited = need(S.TEMPORARY)
    limited = need(S.TEMPORARY, StorageConfig(max_temp_bytes=GIB))
    assert unlimited.workspace_bytes == int(10 * GIB * ESTIMATE_MARGIN)
    assert limited.workspace_bytes == GIB
    assert limited.output_bytes == unlimited.output_bytes


def test_hybrid_working_space_is_small_by_default() -> None:
    assert need(S.HYBRID).workspace_bytes == DEFAULT_SPILL_BYTES
    assert need(S.HYBRID, StorageConfig(max_temp_bytes=GIB // 4)).workspace_bytes == GIB // 4
    tiny = space_needed(S.HYBRID, 1000, StorageConfig())
    assert tiny.workspace_bytes == int(1000 * ESTIMATE_MARGIN)


# --- checking the disk -------------------------------------------------------------------------


def test_the_paths_and_volumes(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    assert nearest_existing(deep) == tmp_path
    assert free_bytes(deep) > 0
    assert same_volume(deep, tmp_path / "other")


def test_enough_space_passes_and_too_little_fails_accurately(tmp_path: Path) -> None:
    need_ = SpaceNeed(output_bytes=700, workspace_bytes=200)
    check_disk_space(
        need_, output_dir=tmp_path, workspace_dir=tmp_path, reserve_bytes=100, free=lambda _p: 1000
    )
    with pytest.raises(InsufficientStorageError) as info:
        check_disk_space(
            need_,
            output_dir=tmp_path,
            workspace_dir=tmp_path,
            reserve_bytes=101,
            free=lambda _p: 1000,
        )
    assert info.value.required_bytes == 1001
    assert info.value.available_bytes == 1000
    assert str(tmp_path) in str(info.value)


def test_separate_disks_are_checked_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rayohx.storage.same_volume", lambda _a, _b: False)
    output, work = tmp_path / "out", tmp_path / "work"
    space = {output: 1000, work: 300}
    need_ = SpaceNeed(output_bytes=900, workspace_bytes=250)

    def free(path: Path) -> int:
        return space[path]

    check_disk_space(need_, output_dir=output, workspace_dir=work, reserve_bytes=50, free=free)
    space[work] = 299
    with pytest.raises(InsufficientStorageError) as info:
        check_disk_space(need_, output_dir=output, workspace_dir=work, reserve_bytes=50, free=free)
    assert str(work) in str(info.value)
    space[work] = 300
    space[output] = 949
    with pytest.raises(InsufficientStorageError):
        check_disk_space(need_, output_dir=output, workspace_dir=work, reserve_bytes=50, free=free)


def test_a_disk_with_no_need_is_not_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("rayohx.storage.same_volume", lambda _a, _b: False)
    check_disk_space(
        SpaceNeed(0, 0),
        output_dir=tmp_path,
        workspace_dir=tmp_path,
        reserve_bytes=10,
        free=lambda _p: pytest.fail("nothing should be checked"),
    )


def test_a_full_disk_is_recognised_when_writing() -> None:
    full = translate_os_error(OSError(errno.ENOSPC, "No space left on device"), "out.mp4")
    assert isinstance(full, InsufficientStorageError)
    other = translate_os_error(OSError(errno.EACCES, "Permission denied"), "out.mp4")
    assert isinstance(other, OutputError)
    assert "Permission denied" in str(other)


# --- metrics -----------------------------------------------------------------------------------


def test_the_tracker_follows_usage_and_remembers_the_peak() -> None:
    tracker = StorageTracker()
    tracker.downloaded(500)
    tracker.written(400)
    tracker.memory(300)
    tracker.disk(200)
    tracker.disk(300)
    tracker.disk(-450)
    snapshot = tracker.snapshot()
    assert snapshot.downloaded_bytes == 500
    assert snapshot.written_bytes == snapshot.output_bytes == 400
    assert snapshot.temporary_bytes == 50
    assert snapshot.peak_temporary_bytes == 500
    assert snapshot.working_bytes == 350
    assert (tracker.memory_bytes, tracker.disk_bytes) == (300, 50)


# --- stores ------------------------------------------------------------------------------------


def test_memory_store_holds_and_releases_segments() -> None:
    tracker = StorageTracker()
    store = MemoryStore(tracker)
    store.put(seg(3, 100))
    store.put(seg(1, 50, init=b"II"))
    assert store.has(3)
    assert not store.has(2)
    assert tracker.memory_bytes == 152
    taken = store.take(1)
    assert (taken.media, taken.init) == (bytes([1]) * 50, b"II")
    assert tracker.memory_bytes == 100
    assert store.existing() == set()
    store.clear()
    assert tracker.memory_bytes == 0
    assert not store.has(3)


def test_disk_store_round_trips_segments_and_frees_space(tmp_path: Path) -> None:
    tracker = StorageTracker()
    store = DiskStore(tmp_path / "segments", tracker)
    store.put(seg(2, 100, init=b"INIT"))
    store.put(seg(7, 10))
    assert store.has(2)
    assert tracker.disk_bytes == 8 + 104 + 8 + 10
    assert sorted(path.name for path in (tmp_path / "segments").iterdir()) == [
        "00000002.seg",
        "00000007.seg",
    ]
    taken = store.take(2)
    assert (taken.init, taken.media) == (b"INIT", bytes([2]) * 100)
    assert not store.has(2)
    assert tracker.disk_bytes == 18
    assert tracker.snapshot().peak_temporary_bytes == 130


def test_disk_store_finds_complete_files_from_an_earlier_run(tmp_path: Path) -> None:
    folder = tmp_path / "segments"
    first = DiskStore(folder, StorageTracker())
    first.put(seg(1, 100))
    first.put(seg(2, 100, init=b"X"))
    (folder / "00000003.tmp").write_bytes(b"half written")
    (folder / "00000004.seg").write_bytes(b"short")  # too short to be valid
    (folder / "00000005.seg").write_bytes((1000).to_bytes(8, "big") + b"abc")  # header lies
    (folder / "note.seg").write_bytes(b"?")

    tracker = StorageTracker()
    second = DiskStore(folder, tracker)
    assert second.existing() == {1, 2}
    assert second.has(1)
    assert tracker.disk_bytes == 108 + 109
    assert sorted(path.name for path in folder.iterdir()) == ["00000001.seg", "00000002.seg"]
    assert second.take(2).init == b"X"


def test_disk_store_clear_removes_everything(tmp_path: Path) -> None:
    tracker = StorageTracker()
    store = DiskStore(tmp_path / "segments", tracker)
    store.put(seg(1))
    store.clear()
    assert not (tmp_path / "segments").exists()
    assert tracker.disk_bytes == 0


def test_a_failed_write_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DiskStore(tmp_path / "segments", StorageTracker())

    def full(_source: object, _target: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("rayohx.storage.os.replace", full)
    with pytest.raises(InsufficientStorageError):
        store.put(seg(1))
    assert list((tmp_path / "segments").iterdir()) == []
    assert not store.has(1)


def test_spill_store_uses_memory_first_and_then_disk(tmp_path: Path) -> None:
    tracker = StorageTracker()
    store = SpillStore(DiskStore(tmp_path / "segments", tracker), tracker, memory_limit=250)
    for index in range(5):
        store.put(seg(index, 100))
    assert tracker.memory_bytes == 200
    assert tracker.disk_bytes == 3 * 108
    assert all(store.has(index) for index in range(5))
    assert store.take(0).index == 0  # from memory
    assert store.take(4).index == 4  # from disk
    assert tracker.memory_bytes == 100
    assert tracker.disk_bytes == 2 * 108
    store.clear()
    assert (tracker.memory_bytes, tracker.disk_bytes) == (0, 0)
    assert store.existing() == set()


def test_each_strategy_gets_the_right_store(tmp_path: Path) -> None:
    tracker = StorageTracker()
    config = StorageConfig()
    folder = tmp_path / "segments"
    assert isinstance(make_store(S.DIRECT, config, tracker, folder), MemoryStore)
    assert isinstance(make_store(S.STREAMING, config, tracker, folder), MemoryStore)
    assert isinstance(make_store(S.BUFFERED, config, tracker, folder), MemoryStore)
    assert isinstance(make_store(S.TEMPORARY, config, tracker, folder), DiskStore)
    assert isinstance(make_store(S.HYBRID, config, tracker, folder), SpillStore)
