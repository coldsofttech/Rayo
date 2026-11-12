import json
from pathlib import Path

import pytest

from rayohx import MediaPlaylist, ResumeState, Segment, StateFile
from rayohx.playlist import parse_playlist
from rayohx.state import STATE_VERSION, playlist_signature
from tests.core.media import BASE


def segments(text: str) -> tuple[Segment, ...]:
    playlist = parse_playlist(text, BASE + "index.m3u8")
    assert isinstance(playlist, MediaPlaylist)
    return playlist.segments


PLAIN = (
    "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
    "#EXTINF:4,\na.ts?token=1\n#EXTINF:4,\nb.ts?token=1\n#EXT-X-ENDLIST\n"
)


def test_the_signature_ignores_query_strings_but_not_content() -> None:
    first = playlist_signature(segments(PLAIN))
    assert first == playlist_signature(segments(PLAIN.replace("token=1", "token=2")))
    assert first != playlist_signature(segments(PLAIN.replace("b.ts", "c.ts")))
    assert first != playlist_signature(segments(PLAIN.replace("#EXTINF:4,\nb", "#EXTINF:5,\nb")))
    assert first != playlist_signature(segments(PLAIN)[:1])
    reordered = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nb.ts\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST\n"
    )
    assert playlist_signature(segments(reordered)) != playlist_signature(segments(PLAIN))


def test_the_signature_notices_key_and_init_changes() -> None:
    keyed = (
        '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI="k1"\n#EXTINF:4,\na.ts\n'
    )
    assert playlist_signature(segments(keyed)) != playlist_signature(
        segments(keyed.replace("k1", "k2"))
    )
    mapped = '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MAP:URI="i1.mp4"\n#EXTINF:4,\na.m4s\n'
    assert playlist_signature(segments(mapped)) != playlist_signature(
        segments(mapped.replace("i1", "i2"))
    )


def state(**changes: object) -> ResumeState:
    values: dict[str, object] = {
        "signature": "sig",
        "total_segments": 10,
        "strategy": "direct",
        "next_index": 4,
        "written_bytes": 2000,
        "source": "https://h/index.m3u8",
    }
    values.update(changes)
    return ResumeState(**values)  # type: ignore[arg-type]


def load(file: StateFile, **changes: object) -> ResumeState | None:
    arguments: dict[str, object] = {"signature": "sig", "total_segments": 10, "strategy": "direct"}
    arguments.update(changes)
    return file.load(**arguments)  # type: ignore[arg-type]


def test_state_round_trips_and_is_stamped(tmp_path: Path) -> None:
    file = StateFile(tmp_path / "state.json")
    assert load(file) is None
    file.save(state())
    loaded = load(file)
    assert loaded is not None
    assert (loaded.next_index, loaded.written_bytes) == (4, 2000)
    assert loaded.updated
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]


def test_state_for_another_download_is_ignored(tmp_path: Path) -> None:
    file = StateFile(tmp_path / "state.json")
    file.save(state())
    assert load(file, signature="other") is None
    assert load(file, total_segments=11) is None
    assert load(file, strategy="temporary") is None


@pytest.mark.parametrize(
    "bad",
    [
        state(version=STATE_VERSION + 1),
        state(next_index=11),
        state(next_index=-1),
        state(written_bytes=-5),
    ],
)
def test_implausible_state_is_ignored(tmp_path: Path, bad: ResumeState) -> None:
    file = StateFile(tmp_path / "state.json")
    file.save(bad)
    assert load(file) is None


@pytest.mark.parametrize("text", ["not json", "[]", '{"signature": "sig"}', '{"extra": 1}', ""])
def test_damaged_state_is_ignored(tmp_path: Path, text: str) -> None:
    path = tmp_path / "state.json"
    path.write_text(text)
    assert load(StateFile(path)) is None


def test_state_holds_no_secrets(tmp_path: Path) -> None:
    file = StateFile(tmp_path / "state.json")
    file.save(state(source="https://h/index.m3u8?token=***"))
    dump = json.loads((tmp_path / "state.json").read_text())
    assert set(dump) == {
        "signature",
        "total_segments",
        "strategy",
        "next_index",
        "written_bytes",
        "source",
        "updated",
        "version",
    }
