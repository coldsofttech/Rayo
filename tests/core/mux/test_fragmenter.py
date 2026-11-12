from rayohx.mux.fragmenter import AUDIO_FRAGMENT, MAX_FRAGMENT_BYTES, TrackFragmenter
from rayohx.mux.model import Sample, TrackConfig
from tests.core.mux.helpers import read_fragments

VIDEO = TrackConfig("video", 90000, b"\x00\x00\x00\x08vide", "x", width=16, height=16)
AUDIO = TrackConfig("audio", 44100, b"\x00\x00\x00\x08soun", "x", channels=2, sample_rate=44100)


def collect(config: TrackConfig) -> tuple[TrackFragmenter, list[tuple[bytes, int]]]:
    out: list[tuple[bytes, int]] = []
    return TrackFragmenter(1, config, lambda fragment, start: out.append((fragment, start))), out


def video_samples(count: int, *, gop: int = 50, start: int = 0, size: int = 100) -> list[Sample]:
    return [
        Sample(b"v" * size, dts=start + index * 3600, duration=0, sync=index % gop == 0)
        for index in range(count)
    ]


def summary(out: list[tuple[bytes, int]]) -> list[tuple[int, int, int]]:
    """``(start, sample count, total duration)`` of each fragment."""
    return [
        (int(f["start"]), int(f["count"]), sum(f["durations"]))  # type: ignore[arg-type]
        for data, _ in out
        for f in read_fragments(data)
    ]


def test_video_is_cut_at_keyframes_and_durations_are_filled_in() -> None:
    fragmenter, out = collect(VIDEO)
    for sample in video_samples(120):
        fragmenter.add(sample)
    fragmenter.finish()
    assert summary(out) == [(0, 50, 50 * 3600), (180000, 50, 50 * 3600), (360000, 20, 20 * 3600)]
    assert [start for _, start in out] == [0, 180000, 360000]


def test_the_last_video_sample_repeats_the_previous_duration() -> None:
    fragmenter, out = collect(VIDEO)
    for sample in video_samples(3):
        fragmenter.add(sample)
    fragmenter.finish()
    assert read_fragments(out[0][0])[0]["durations"] == [3600, 3600, 3600]


def test_short_gops_are_not_cut_below_a_second() -> None:
    fragmenter, out = collect(VIDEO)
    for sample in video_samples(100, gop=10):  # a keyframe every 0.4 s
        fragmenter.add(sample)
    fragmenter.finish()
    counts = [count for _, count, _ in summary(out)]
    assert counts == [30, 30, 30, 10]  # cut at the first keyframe past one second


def test_audio_is_cut_by_duration() -> None:
    fragmenter, out = collect(AUDIO)
    frames = int(AUDIO_FRAGMENT * 44100 / 1024) + 5
    for index in range(frames * 2):
        fragmenter.add(Sample(b"a" * 50, dts=index * 1024, duration=1024))
    fragmenter.finish()
    sizes = [count for _, count, _ in summary(out)]
    assert len(sizes) >= 2
    assert all(
        AUDIO_FRAGMENT * 44100 <= size * 1024 < (AUDIO_FRAGMENT + 0.1) * 44100
        for size in sizes[:-1]
    )
    assert sum(sizes) == frames * 2
    assert fragmenter.sample_count == frames * 2


def test_huge_fragments_are_cut_even_without_a_keyframe() -> None:
    fragmenter, out = collect(VIDEO)
    big = MAX_FRAGMENT_BYTES // 4 + 1
    for sample in video_samples(10, gop=1000, size=big):
        fragmenter.add(sample)
    fragmenter.finish()
    assert len(out) >= 2


def test_a_forward_jump_in_time_is_closed_up() -> None:
    fragmenter, out = collect(VIDEO)
    first = video_samples(50)
    second = video_samples(50, start=first[-1].dts + 3600 + 100 * 90000)  # a 100 s jump
    for sample in [*first, *second]:
        fragmenter.add(sample)
    fragmenter.finish()
    starts = [start for start, _, _ in summary(out)]
    assert starts == [0, 50 * 3600]  # continuous, no 100 s hole


def test_a_backward_jump_in_time_is_closed_up() -> None:
    fragmenter, out = collect(VIDEO)
    first = video_samples(50, start=10_000_000)
    second = video_samples(50, start=0)  # the clock restarted
    for sample in [*first, *second]:
        fragmenter.add(sample)
    fragmenter.finish()
    assert [start for start, _, _ in summary(out)] == [10_000_000, 10_000_000 + 50 * 3600]
    assert all(duration == 50 * 3600 for _, _, duration in summary(out))


def test_the_first_fragment_may_start_anywhere() -> None:
    fragmenter, out = collect(VIDEO)
    for sample in video_samples(60, start=1_000_000):
        fragmenter.add(sample)
    fragmenter.finish()
    assert out[0][1] == 1_000_000


def test_finishing_with_nothing_does_nothing() -> None:
    fragmenter, out = collect(VIDEO)
    fragmenter.finish()
    assert out == []
