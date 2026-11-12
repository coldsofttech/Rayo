import pytest

import rayohx.mux.muxer as muxer_module
from rayohx import FinalizationError
from rayohx.mux.fmp4 import RawTrack, build_fragment
from rayohx.mux.model import Sample
from rayohx.mux.muxer import Mp4Muxer
from tests.core.mux.helpers import config, read_fragments, top_level


def fragment(track: int, start: int, *, count: int = 2, size: int = 20) -> bytes:
    """A fragment of ``count`` samples of 1000 ticks each, starting at tick ``start``."""
    items = [Sample(b"x" * size, dts=start + i * 1000, duration=1000) for i in range(count)]
    return build_fragment(track, items, start)


def new() -> tuple[Mp4Muxer, bytearray]:
    out = bytearray()
    return Mp4Muxer(out.extend), out


def order(data: bytes) -> list[tuple[int, int, int]]:
    """``(sequence, track, start)`` for each fragment written."""
    return [
        (int(str(f["sequence"])), int(str(f["track"])), int(str(f["start"])))
        for f in read_fragments(data)
    ]


def test_fragments_of_two_tracks_are_written_in_time_order() -> None:
    muxer, out = new()
    muxer.expect(2)
    video = muxer.add_track(config("video"))
    audio = muxer.add_track(config("audio"))
    muxer.add_fragment(video, fragment(video, 0), 0)
    muxer.add_fragment(video, fragment(video, 2000), 2000)
    assert out == b""  # the audio track has nothing yet, so nothing can be ordered
    muxer.add_fragment(audio, fragment(audio, 500), 500)
    muxer.add_fragment(audio, fragment(audio, 2500), 2500)
    muxer.end_track(video)
    muxer.end_track(audio)
    muxer.close()
    assert top_level(bytes(out))[:2] == [b"ftyp", b"moov"]
    # Written sequence numbers count up; times are shifted so the first fragment starts at zero.
    assert order(bytes(out)) == [(1, 1, 0), (2, 2, 500), (3, 1, 2000), (4, 2, 2500)]
    assert muxer.origin == 0


def test_the_file_starts_near_zero_whatever_the_streams_clock_says() -> None:
    muxer, out = new()
    video = muxer.add_track(config("video"))
    muxer.add_fragment(video, fragment(video, 90_000), 90_000)
    muxer.add_fragment(video, fragment(video, 92_000), 92_000)
    muxer.close()
    assert muxer.origin == 90.0
    assert [start for _, _, start in order(bytes(out))] == [0, 2000]


def test_the_earliest_track_sets_the_origin() -> None:
    muxer, out = new()
    muxer.expect(2)
    video = muxer.add_track(config("video"))
    audio = muxer.add_track(config("audio"))
    muxer.add_fragment(video, fragment(video, 5000), 5000)
    muxer.add_fragment(audio, fragment(audio, 3000), 3000)
    muxer.close()
    assert muxer.origin == 3.0
    assert [(t, s) for _, t, s in order(bytes(out))] == [(2, 0), (1, 2000)]


def test_writing_waits_for_all_expected_tracks() -> None:
    muxer, out = new()
    muxer.expect(2)
    video = muxer.add_track(config("video"))
    muxer.add_fragment(video, fragment(video, 0), 0)
    assert out == b""
    audio = muxer.add_track(config("audio"))
    muxer.add_fragment(audio, fragment(audio, 0), 0)
    assert out != b""


def test_a_hold_delays_the_start_until_it_is_released() -> None:
    muxer, out = new()
    muxer.hold()
    audio = muxer.add_track(config("audio"))
    muxer.add_fragment(audio, fragment(audio, 0), 0)
    muxer.end_track(audio)
    assert out == b""
    muxer.release()
    assert out != b""
    muxer.release()  # extra releases do no harm


def test_a_track_that_has_ended_does_not_hold_the_others_back() -> None:
    muxer, out = new()
    muxer.expect(2)
    video = muxer.add_track(config("video"))
    audio = muxer.add_track(config("audio"))
    muxer.end_track(audio)
    muxer.add_fragment(video, fragment(video, 0), 0)
    assert order(bytes(out)) == [(1, 1, 0)]


def test_lazy_tracks_are_asked_for_fragments_only_when_needed() -> None:
    muxer, out = new()
    video = muxer.add_track(config("video"))
    audio = muxer.add_track(config("audio"))
    calls: list[int] = []
    pending = [fragment(audio, 0), fragment(audio, 2000), fragment(audio, 4000)]

    def pull() -> bool:
        calls.append(len(calls))
        if not pending:
            return False
        data = pending.pop(0)
        muxer.add_fragment(audio, data, 2000 * (len(calls) - 1))
        return True

    muxer.set_puller(audio, pull)
    muxer.add_fragment(video, fragment(video, 0), 0)
    assert len(calls) <= 2  # it pulled what it needed, not the whole track
    muxer.add_fragment(video, fragment(video, 2000), 2000)
    muxer.end_track(video)
    muxer.close()
    assert sorted(t for _, t, _ in order(bytes(out))).count(audio) == 3


def test_a_silent_track_cannot_hold_everything_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(muxer_module, "MAX_PENDING_BYTES", 500)
    muxer, out = new()
    muxer.expect(2)
    video = muxer.add_track(config("video"))
    muxer.add_track(config("audio"))  # never delivers anything
    for index in range(30):
        muxer.add_fragment(video, fragment(video, index * 2000, size=100), index * 2000)
    assert out != b""
    assert len(order(bytes(out))) >= 20


def test_a_track_cannot_be_added_after_writing_has_started() -> None:
    muxer, _ = new()
    video = muxer.add_track(config("video"))
    muxer.add_fragment(video, fragment(video, 0), 0)
    with pytest.raises(FinalizationError, match="after the file had been started"):
        muxer.add_track(config("audio"))


def test_closing_without_any_media_is_an_error() -> None:
    muxer, _ = new()
    muxer.add_track(config("video"))
    with pytest.raises(FinalizationError, match="no media") as info:
        muxer.close()
    assert info.value.hint


def test_copied_tracks_get_their_new_id_and_are_listed_first_when_video() -> None:
    muxer, out = new()
    audio = muxer.add_track(config("audio"))
    seen: list[int] = []

    def make(track_id: int) -> RawTrack:
        seen.append(track_id)
        return RawTrack(b"\x00\x00\x00\x0atrakVV", b"\x00\x00\x00\x0atrexVV", "video")

    video = muxer.add_raw_track(make, 90000)
    assert seen == [video]
    muxer.add_fragment(video, fragment(video, 0), 0)
    muxer.add_fragment(audio, fragment(audio, 0), 0)
    muxer.close()
    data = bytes(out)
    assert data.index(b"trakVV") < data.index(b"trak", data.index(b"trakVV") + 6)


def test_bytes_written_counts_everything() -> None:
    muxer, out = new()
    video = muxer.add_track(config("video"))
    muxer.add_fragment(video, fragment(video, 0), 0)
    muxer.close()
    assert muxer.bytes_written == len(out)
