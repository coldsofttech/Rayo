"""The controller end to end: states, events, naming, refusals, cancellation and failures."""

from pathlib import Path

import httpx
import pytest

from rayohx import (
    CancellationToken,
    ContainerFormat,
    DownloadConfig,
    Downloader,
    DownloadError,
    DownloadEvent,
    DownloadResult,
    DownloadState,
    EventType,
    FinalizationError,
    HttpClient,
    MediaDownload,
    NetworkConfig,
    OutputConfig,
    OutputExistsError,
    OutputFormat,
    OutputFormatError,
    SegmentDownloadError,
    SelectionConfig,
    download,
    logs,
)
from tests.core.media import BASE, Origin, worker_threads

URL = BASE + "index.m3u8"
NETWORK = NetworkConfig(retries=0, backoff_factor=0.001)

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
mid.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
high.m3u8
"""
DEMUXED = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",DEFAULT=YES,URI="audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,AUDIO="aud"
index.m3u8
"""
WITH_SUBTITLES = """#EXTM3U
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",URI="subs.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,SUBTITLES="subs"
index.m3u8
"""


def ts_origin(count: int = 10) -> Origin:
    return Origin(count, content="ts")


def with_master(origin: Origin, text: str = MASTER) -> Origin:
    playlist = origin.playlist_text().encode()
    origin.files = {
        "master.m3u8": text.encode(),
        "low.m3u8": playlist,
        "mid.m3u8": playlist,
        "high.m3u8": playlist,
    }
    return origin


def make(
    origin: Origin,
    tmp_path: Path,
    *,
    url: str = URL,
    output: str | None = "out.ts",
    token: CancellationToken | None = None,
    selection: SelectionConfig | None = None,
    format: OutputFormat = OutputFormat.AUTO,
    overwrite: bool = False,
    validate: bool = True,
    concurrency: int = 4,
) -> tuple[Downloader, list[DownloadEvent]]:
    config = DownloadConfig(
        network=NETWORK,
        selection=selection or SelectionConfig(),
        output=OutputConfig(format=format, overwrite=overwrite, validate=validate),
        concurrency=concurrency,
    )
    downloader = Downloader(
        url,
        output=tmp_path / output if output else None,
        config=config,
        cancel=token,
        transport=httpx.MockTransport(origin),
    )
    events: list[DownloadEvent] = []
    downloader.events.subscribe(events.append)
    return downloader, events


def milestones(events: list[DownloadEvent]) -> list[EventType]:
    noisy = {
        EventType.SEGMENT_STARTED,
        EventType.SEGMENT_COMPLETED,
        EventType.DOWNLOAD_PROGRESS,
    }
    return [event.type for event in events if event.type not in noisy]


# --- a successful download ---------------------------------------------------------------------


def test_a_ts_stream_becomes_a_ts_file(tmp_path: Path) -> None:
    origin = ts_origin(10)
    downloader, _ = make(origin, tmp_path)
    result = downloader.run()

    assert result.success
    assert result.state is DownloadState.COMPLETED
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert result.output is not None
    assert result.output.path == tmp_path / "out.ts"
    assert result.output.container is ContainerFormat.MPEG_TS
    assert result.output.size_bytes == 10 * 3 * 188
    assert result.error is None
    assert result.elapsed_seconds >= 0
    assert result.progress.state is DownloadState.COMPLETED
    assert result.progress.fraction == 1.0
    assert result.progress.completed_segments == 10
    assert downloader.state is DownloadState.COMPLETED
    assert downloader.exception is None
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out.ts"]


def test_events_follow_the_download_through_its_states(tmp_path: Path) -> None:
    downloader, events = make(ts_origin(6), tmp_path)
    downloader.run()
    assert milestones(events) == [
        EventType.DOWNLOAD_STARTED,
        EventType.PLAYLIST_LOADED,
        EventType.FINALIZATION_STARTED,
        EventType.FINALIZATION_COMPLETED,
        EventType.DOWNLOAD_COMPLETED,
    ]
    by_type = {event.type: event for event in events}
    assert by_type[EventType.DOWNLOAD_STARTED].state is DownloadState.QUEUED
    assert by_type[EventType.PLAYLIST_LOADED].state is DownloadState.INSPECTING
    assert by_type[EventType.FINALIZATION_STARTED].state is DownloadState.FINALIZING
    assert by_type[EventType.DOWNLOAD_COMPLETED].state is DownloadState.COMPLETED
    segment_states = {e.state for e in events if e.type is EventType.SEGMENT_COMPLETED}
    assert segment_states == {DownloadState.DOWNLOADING}
    assert {event.download_id for event in events} == {downloader.download_id}
    last = by_type[EventType.DOWNLOAD_COMPLETED]
    assert last.progress is not None
    assert last.progress.fraction == 1.0


def test_a_master_playlist_is_inspected_and_the_best_stream_chosen(tmp_path: Path) -> None:
    origin = with_master(ts_origin(8))
    downloader, events = make(origin, tmp_path, url=BASE + "master.m3u8")
    result = downloader.run()
    assert result.success
    assert "high.m3u8" in origin.log
    assert "low.m3u8" not in origin.log
    assert milestones(events)[:4] == [
        EventType.DOWNLOAD_STARTED,
        EventType.PLAYLIST_LOADED,
        EventType.STREAM_SELECTED,
        EventType.PLAYLIST_LOADED,
    ]
    selected = next(e for e in events if e.type is EventType.STREAM_SELECTED)
    assert selected.state is DownloadState.INSPECTING
    assert selected.data["resolution"] == "1920x1080"
    assert selected.data["bandwidth"] == 5_000_000


def test_quality_settings_choose_the_stream(tmp_path: Path) -> None:
    origin = with_master(ts_origin(4))
    downloader, _ = make(
        origin,
        tmp_path,
        url=BASE + "master.m3u8",
        selection=SelectionConfig(quality="720p"),
    )
    downloader.run()
    assert "mid.m3u8" in origin.log
    assert "high.m3u8" not in origin.log


def test_fragmented_mp4_becomes_an_mp4_file(tmp_path: Path) -> None:
    origin = Origin(8, ext="m4s", content="fmp4")
    downloader, _ = make(origin, tmp_path, output="clip.mp4")
    result = downloader.run()
    assert result.success
    assert result.output is not None
    assert result.output.container is ContainerFormat.FMP4
    assert (tmp_path / "clip.mp4").read_bytes() == origin.expected()


def test_the_right_extension_is_added_when_the_name_has_none(tmp_path: Path) -> None:
    origin = Origin(4, ext="m4s", content="fmp4")
    downloader, _ = make(origin, tmp_path, output="clip")
    assert downloader.run().output.path == tmp_path / "clip.mp4"  # type: ignore[union-attr]


def test_an_unnamed_download_is_named_after_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    origin = ts_origin(3)
    downloader, _ = make(origin, tmp_path, url=BASE + "shows/my-show/index.m3u8", output=None)
    origin.files = {"shows/my-show/index.m3u8": origin.playlist_text().encode()}
    for index in range(3):
        origin.files[f"shows/my-show/seg{index}.ts"] = origin.plain[index]
    result = downloader.run()
    assert result.success, result.error
    assert result.output is not None
    assert result.output.path == tmp_path / "my-show.ts"


def test_segments_can_be_kept_as_separate_files(tmp_path: Path) -> None:
    origin = ts_origin(5)
    downloader, _ = make(origin, tmp_path, output="parts", format=OutputFormat.SEGMENTS)
    result = downloader.run()
    assert result.success
    names = sorted(path.name for path in (tmp_path / "parts").iterdir())
    assert names == [f"segment-0000{i}.ts" for i in range(1, 6)]
    assert (tmp_path / "parts" / "segment-00003.ts").read_bytes() == origin.plain[2]
    assert result.output is not None
    assert result.output.size_bytes == 5 * 3 * 188
    assert sorted(path.name for path in tmp_path.iterdir()) == ["parts"]


def test_an_unknown_format_is_found_by_looking_at_the_first_segment(tmp_path: Path) -> None:
    origin = Origin(4, ext="bin", content="ts")
    downloader, _ = make(origin, tmp_path, output="clip")
    result = downloader.run()
    assert result.success
    assert result.output is not None
    assert result.output.path == tmp_path / "clip.ts"
    assert origin.log.count("seg0.bin") == 2


def test_credentials_in_the_address_are_hidden_in_the_result(tmp_path: Path) -> None:
    downloader, events = make(ts_origin(2), tmp_path, url=URL + "?token=hunter2")
    result = downloader.run()
    assert "hunter2" not in result.url
    assert all("hunter2" not in str(event) for event in events)


def test_worker_threads_log_with_the_download_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    from rayohx.fetcher import HlsSegmentFetcher

    real_fetch = HlsSegmentFetcher.fetch

    def spy(self: HlsSegmentFetcher, index: int, on_bytes: object = None) -> object:
        seen.append(logs._download_id.get())
        return real_fetch(self, index, on_bytes)  # type: ignore[arg-type]

    monkeypatch.setattr(HlsSegmentFetcher, "fetch", spy)
    downloader, _ = make(ts_origin(6), tmp_path)
    downloader.run()
    assert seen
    assert set(seen) == {downloader.download_id}


# --- things it refuses to do -------------------------------------------------------------------


def test_ts_will_not_be_mislabelled_as_mp4(tmp_path: Path) -> None:
    origin = ts_origin(5)
    downloader, events = make(origin, tmp_path, output="video.mp4")
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert not result.success
    assert "cannot be saved as MP4" in (result.error or "")
    assert ".ts" in (result.error or "")
    assert isinstance(downloader.exception, OutputFormatError)
    assert not any(name.startswith("seg") for name in origin.log)
    assert not (tmp_path / "video.mp4").exists()
    assert milestones(events)[-1] is EventType.DOWNLOAD_FAILED
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_a_separate_audio_track_is_explained_not_dropped(tmp_path: Path) -> None:
    origin = ts_origin(4)
    origin.files = {"master.m3u8": DEMUXED.encode()}
    downloader, _ = make(origin, tmp_path, url=BASE + "master.m3u8")
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "separate track" in (result.error or "")
    assert "video_only" in (result.error or "")
    assert not any(name.startswith("seg") for name in origin.log)


def test_video_only_skips_separate_audio(tmp_path: Path) -> None:
    origin = ts_origin(4)
    origin.files = {"master.m3u8": DEMUXED.encode()}
    downloader, events = make(
        origin,
        tmp_path,
        url=BASE + "master.m3u8",
        selection=SelectionConfig(video_only=True),
    )
    result = downloader.run()
    assert result.success, result.error
    selected = next(e for e in events if e.type is EventType.STREAM_SELECTED)
    assert selected.data["audio"] == []
    assert any("video" in note for note in selected.data["notes"])


def test_requested_subtitles_are_refused_for_now(tmp_path: Path) -> None:
    origin = ts_origin(4)
    origin.files = {"master.m3u8": WITH_SUBTITLES.encode()}
    downloader, _ = make(
        origin,
        tmp_path,
        url=BASE + "master.m3u8",
        selection=SelectionConfig(subtitle_languages=("en",)),
    )
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "subtitles" in (result.error or "").lower()


def test_an_existing_file_is_not_replaced(tmp_path: Path) -> None:
    (tmp_path / "out.ts").write_bytes(b"precious")
    origin = ts_origin(3)
    downloader, _ = make(origin, tmp_path)
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert isinstance(downloader.exception, OutputExistsError)
    assert (tmp_path / "out.ts").read_bytes() == b"precious"
    assert not any(name.startswith("seg") for name in origin.log)
    again, _ = make(origin, tmp_path, overwrite=True)
    assert again.run().success
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()


def test_a_downloader_runs_only_once(tmp_path: Path) -> None:
    downloader, _ = make(ts_origin(2), tmp_path)
    downloader.run()
    with pytest.raises(DownloadError, match="already run"):
        downloader.run()


# --- failures ----------------------------------------------------------------------------------


def test_a_broken_segment_fails_the_download_with_a_clear_message(tmp_path: Path) -> None:
    origin = ts_origin(10)
    origin.fail = {6: 404}
    downloader, events = make(origin, tmp_path)
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "Segment 6" in (result.error or "")
    assert "404" in (result.error or "")
    assert isinstance(downloader.exception, SegmentDownloadError)
    assert downloader.state is DownloadState.FAILED
    failed = [e for e in events if e.type is EventType.DOWNLOAD_FAILED]
    assert len(failed) == 1
    assert failed[0].state is DownloadState.FAILED
    assert result.progress.state is DownloadState.FAILED
    assert (tmp_path / "out.ts.part").exists()
    assert list(worker_threads()) == []


def test_a_segment_of_the_wrong_kind_is_caught_at_once(tmp_path: Path) -> None:
    origin = ts_origin(8)
    origin._blobs[3] = bytes(564)
    downloader, _ = make(origin, tmp_path)
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "Segment 3" in (result.error or "")
    assert "not MPEG-TS" in (result.error or "")


def test_a_failed_final_check_leaves_no_output_and_nothing_to_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_container: ContainerFormat, _path: Path) -> None:
        raise FinalizationError("The output is not a valid file.")

    monkeypatch.setattr("rayohx.media_download.validate_output", refuse)
    downloader, events = make(ts_origin(5), tmp_path)
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert isinstance(downloader.exception, FinalizationError)
    assert EventType.FINALIZATION_STARTED in milestones(events)
    assert EventType.FINALIZATION_COMPLETED not in milestones(events)
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_the_final_check_can_be_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_container: ContainerFormat, _path: Path) -> None:
        raise FinalizationError("never called")

    monkeypatch.setattr("rayohx.media_download.validate_output", refuse)
    downloader, _ = make(ts_origin(3), tmp_path, validate=False)
    assert downloader.run().success


def test_an_unexpected_error_is_reported_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: MediaDownload) -> None:
        raise RuntimeError("something odd with token=secret99")

    monkeypatch.setattr(MediaDownload, "run", boom)
    downloader, events = make(ts_origin(3), tmp_path)
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "Unexpected error" in (result.error or "")
    assert "secret99" not in (result.error or "")
    assert isinstance(downloader.exception, RuntimeError)
    assert events[-1].type is EventType.DOWNLOAD_FAILED


def test_a_page_instead_of_a_playlist_is_explained(tmp_path: Path) -> None:
    origin = ts_origin(2)
    origin.files = {"index.m3u8": b"<html>Access denied</html>"}
    downloader, _ = make(origin, tmp_path)
    result = downloader.run()
    assert result.state is DownloadState.FAILED
    assert "HLS playlist" in (result.error or "")


# --- cancellation ------------------------------------------------------------------------------


def cancel_after(downloader: Downloader, completed: int) -> None:
    count = {"done": 0}

    def listener(event: DownloadEvent) -> None:
        if event.type is EventType.SEGMENT_COMPLETED:
            count["done"] += 1
            if count["done"] == completed:
                downloader.cancel()

    downloader.events.subscribe(listener)


def test_a_cancelled_download_is_a_cancelled_result_not_a_failure(tmp_path: Path) -> None:
    origin = ts_origin(40)
    origin.delays = dict.fromkeys(range(40), 0.005)
    downloader, events = make(origin, tmp_path, concurrency=2)
    cancel_after(downloader, 4)
    result = downloader.run()
    assert result.state is DownloadState.CANCELLED
    assert result.cancelled
    assert not result.success
    assert result.error is None
    assert downloader.exception is None
    assert downloader.state is DownloadState.CANCELLED
    assert [e.type for e in events if e.type is EventType.DOWNLOAD_CANCELLED]
    assert EventType.DOWNLOAD_FAILED not in milestones(events)
    assert list(worker_threads()) == []
    assert (tmp_path / "out.ts.part").exists()  # kept so that the download can continue
    assert not (tmp_path / "out.ts").exists()


def test_a_cancelled_download_can_be_continued(tmp_path: Path) -> None:
    origin = ts_origin(40)
    origin.delays = dict.fromkeys(range(40), 0.005)
    first, _ = make(origin, tmp_path, concurrency=2)
    cancel_after(first, 4)
    assert first.run().cancelled
    origin.log.clear()
    second, _ = make(origin, tmp_path, concurrency=2)
    result = second.run()
    assert result.success
    assert (tmp_path / "out.ts").read_bytes() == origin.expected()
    assert len(origin.requested()) < 40


def test_cancelling_before_starting_does_nothing(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    origin = ts_origin(3)
    downloader, _ = make(origin, tmp_path, token=token)
    assert downloader.run().cancelled
    assert not any(name.startswith("seg") for name in origin.log)


# --- the download() function -------------------------------------------------------------------


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Origin:
    """Make the module-level HttpClient talk to an in-memory origin."""
    origin = with_master(ts_origin(6))

    def factory(config: NetworkConfig, **options: object) -> HttpClient:
        options["transport"] = httpx.MockTransport(origin)
        return HttpClient(config, **options)  # type: ignore[arg-type]

    monkeypatch.setattr("rayohx.controller.HttpClient", factory)
    return origin


def test_download_is_one_call(tmp_path: Path, served: Origin) -> None:
    result = download(BASE + "master.m3u8", tmp_path / "video.ts")
    assert isinstance(result, DownloadResult)
    assert result.success
    assert (tmp_path / "video.ts").read_bytes() == served.expected()


def test_download_options_override_the_config(tmp_path: Path, served: Origin) -> None:
    served.delays = dict.fromkeys(range(6), 0.01)
    base = DownloadConfig(
        network=NETWORK, selection=SelectionConfig(max_bitrate=3_000_000, max_resolution="1080p")
    )
    seen: list[DownloadEvent] = []
    download(
        BASE + "master.m3u8",
        tmp_path / "video.ts",
        config=base,
        quality="best",
        concurrency=1,
        on_event=seen.append,
    )
    assert "mid.m3u8" in served.log  # the bitrate limit from the config still applies
    assert "high.m3u8" not in served.log
    assert served.max_active == 1
    assert any(event.type is EventType.DOWNLOAD_COMPLETED for event in seen)


def test_download_audio_and_subtitle_options_accept_one_or_several_languages(
    tmp_path: Path, served: Origin
) -> None:
    served.files["master.m3u8"] = WITH_SUBTITLES.encode()
    with pytest.raises(OutputFormatError, match="subtitles"):
        download(BASE + "master.m3u8", tmp_path / "a.ts", subtitles=["en", "fr"])
    served.files["master.m3u8"] = DEMUXED.encode()
    with pytest.raises(OutputFormatError, match="separate track"):
        download(BASE + "master.m3u8", tmp_path / "b.ts", audio="en")
    result = download(BASE + "master.m3u8", tmp_path / "c.ts", audio="en", video_only=True)
    assert result.success


def test_download_format_option_and_overwrite(tmp_path: Path, served: Origin) -> None:
    download(BASE + "master.m3u8", tmp_path / "parts", format="segments")
    assert (tmp_path / "parts" / "segment-00001.ts").exists()
    with pytest.raises(OutputExistsError):
        download(BASE + "master.m3u8", tmp_path / "parts", format=OutputFormat.SEGMENTS)
    download(BASE + "master.m3u8", tmp_path / "parts", format="segments", overwrite=True)


def test_download_raises_on_failure_with_the_original_error(tmp_path: Path, served: Origin) -> None:
    served.fail = {2: 404}
    with pytest.raises(SegmentDownloadError) as info:
        download(
            BASE + "master.m3u8", tmp_path / "video.ts", config=DownloadConfig(network=NETWORK)
        )
    assert info.value.index == 2


def test_download_returns_a_cancelled_result_instead_of_raising(
    tmp_path: Path, served: Origin
) -> None:
    token = CancellationToken()
    token.cancel()
    result = download(BASE + "master.m3u8", tmp_path / "video.ts", cancel=token)
    assert result.cancelled


def test_download_rejects_bad_options_early(tmp_path: Path) -> None:
    from rayohx import ConfigurationError

    with pytest.raises(ConfigurationError):
        download("not a url", tmp_path / "x.ts")
    with pytest.raises(ConfigurationError):
        download(URL, tmp_path / "x.ts", quality="hd")
    with pytest.raises(ValueError, match="not a valid OutputFormat"):
        download(URL, tmp_path / "x.ts", format="mkv")
