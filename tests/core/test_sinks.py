from pathlib import Path

import pytest

from rayohx import FileSink, OutputError, OutputExistsError, SegmentData, StreamSink


def seg(index: int, media: bytes, init: bytes = b"") -> SegmentData:
    return SegmentData(index=index, media=media, init=init)


def test_a_file_is_written_as_part_and_renamed_on_commit(tmp_path: Path) -> None:
    out = tmp_path / "deep" / "er" / "video.ts"
    sink = FileSink(out)
    assert sink.open() == 0
    assert sink.write(seg(0, b"aaa", init=b"II")) == 5
    assert sink.write(seg(1, b"bb")) == 2
    assert not out.exists()
    assert sink.part_path.read_bytes() in (b"", b"IIaaabb")  # buffered until flushed
    assert sink.checkpoint() == 7
    assert sink.part_path.read_bytes() == b"IIaaabb"
    assert sink.commit() == out
    assert out.read_bytes() == b"IIaaabb"
    assert not sink.part_path.exists()


def test_existing_output_is_protected_unless_overwriting_is_allowed(tmp_path: Path) -> None:
    out = tmp_path / "video.ts"
    out.write_bytes(b"precious")
    with pytest.raises(OutputExistsError) as info:
        FileSink(out).preflight()
    assert info.value.hint
    with pytest.raises(OutputExistsError):
        FileSink(out).open()
    assert out.read_bytes() == b"precious"

    sink = FileSink(out, overwrite=True)
    sink.open()
    sink.write(seg(0, b"new"))
    assert out.read_bytes() == b"precious"  # untouched until commit
    sink.commit()
    assert out.read_bytes() == b"new"


def test_a_file_that_appears_during_the_download_is_not_clobbered(tmp_path: Path) -> None:
    out = tmp_path / "video.ts"
    sink = FileSink(out)
    sink.open()
    sink.write(seg(0, b"data"))
    out.write_bytes(b"someone else's")
    with pytest.raises(OutputExistsError):
        sink.commit()
    assert out.read_bytes() == b"someone else's"


def test_a_folder_is_not_a_valid_output(tmp_path: Path) -> None:
    with pytest.raises(OutputError, match="folder"):
        FileSink(tmp_path).preflight()


def test_abort_removes_or_keeps_the_partial_file(tmp_path: Path) -> None:
    sink = FileSink(tmp_path / "a.ts")
    sink.open()
    sink.write(seg(0, b"data"))
    sink.abort(keep_partial=True)
    assert sink.part_path.read_bytes() == b"data"
    again = FileSink(tmp_path / "a.ts")
    again.open()
    again.abort(keep_partial=False)
    assert not again.part_path.exists()


def test_resuming_continues_after_the_bytes_already_written(tmp_path: Path) -> None:
    first = FileSink(tmp_path / "a.ts")
    first.open()
    first.write(seg(0, b"one"))
    first.write(seg(1, b"two"))
    first.checkpoint()
    first.write(seg(2, b"lost"))  # written after the last checkpoint
    first.abort(keep_partial=True)

    second = FileSink(tmp_path / "a.ts")
    assert second.open(resume_offset=6) == 6
    second.write(seg(2, b"three"))
    assert second.commit().read_bytes() == b"onetwothree"


def test_resuming_with_a_missing_or_short_part_file_starts_over(tmp_path: Path) -> None:
    fresh = FileSink(tmp_path / "a.ts")
    assert fresh.open(resume_offset=100) == 0
    fresh.write(seg(0, b"abc"))
    fresh.abort(keep_partial=True)
    short = FileSink(tmp_path / "a.ts")
    assert short.open(resume_offset=100) == 0
    short.write(seg(0, b"new"))
    assert short.commit().read_bytes() == b"new"


def test_using_a_sink_before_opening_it_is_an_error(tmp_path: Path) -> None:
    sink = FileSink(tmp_path / "a.ts")
    with pytest.raises(OutputError, match="not open"):
        sink.write(seg(0, b"x"))
    with pytest.raises(OutputError, match="not open"):
        sink.checkpoint()


def test_a_stream_sink_passes_bytes_in_order() -> None:
    received: list[bytes] = []
    sink = StreamSink(received.append)
    assert not sink.supports_resume
    assert not sink.writes_file
    assert sink.target is None
    sink.preflight()
    assert sink.open(resume_offset=50) == 0
    assert sink.write(seg(0, b"aa", init=b"I")) == 3
    assert sink.write(seg(1, b"b")) == 1
    assert sink.checkpoint() == 4
    assert received == [b"I", b"aa", b"b"]
    assert sink.commit() is None
    sink.abort(keep_partial=True)
