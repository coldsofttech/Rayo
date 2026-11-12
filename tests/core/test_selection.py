import pytest

from rayohx import (
    AudioTrack,
    MasterPlaylist,
    PlaylistError,
    Resolution,
    SelectionConfig,
    SubtitleTrack,
    Variant,
)
from rayohx.playlist import parse_playlist
from rayohx.selection import quality_height, select_stream, select_variant
from tests.core.hls import BASE, fixture_text


def load(name: str) -> MasterPlaylist:
    playlist = parse_playlist(fixture_text(name), BASE)
    assert isinstance(playlist, MasterPlaylist)
    return playlist


def heights(master: MasterPlaylist, **settings: object) -> int:
    notes: list[str] = []
    variant = select_variant(master.variants, SelectionConfig(**settings), notes)  # type: ignore[arg-type]
    return quality_height(variant)


def variant(
    bandwidth: int,
    size: tuple[int, int] | None = None,
    *codecs: str,
    uri: str = "v.m3u8",
    **extra: object,
) -> Variant:
    return Variant(
        uri=uri,
        bandwidth=bandwidth,
        resolution=Resolution(*size) if size else None,
        codecs=codecs,
        **extra,  # type: ignore[arg-type]
    )


# --- quality -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({}, 2160),
        ({"quality": "best"}, 2160),
        ({"quality": "worst"}, 360),
        ({"quality": "1080p"}, 1080),
        ({"quality": "720p"}, 720),
        ({"quality": "1440p"}, 1080),
        ({"quality": "auto"}, 1080),
        ({"max_resolution": "1080p"}, 1080),
        ({"max_resolution": "720p", "quality": "1080p"}, 720),
        ({"max_bitrate": 3_000_000}, 720),
        ({"max_bitrate": 3_000_000, "quality": "worst"}, 360),
    ],
)
def test_quality_modes(settings: dict[str, object], expected: int) -> None:
    assert heights(load("master_basic.m3u8"), **settings) == expected


def test_a_target_below_every_stream_picks_the_closest_and_says_so() -> None:
    notes: list[str] = []
    chosen = select_variant(
        load("master_basic.m3u8").variants, SelectionConfig(quality="240p"), notes
    )
    assert quality_height(chosen) == 360
    assert "240p" in notes[0]


def test_limits_nothing_can_meet_choose_the_smallest_and_say_so() -> None:
    notes: list[str] = []
    master = load("master_basic.m3u8")
    chosen = select_variant(master.variants, SelectionConfig(max_bitrate=100), notes)
    assert quality_height(chosen) == 360
    assert "smallest" in notes[0]
    notes.clear()
    select_variant(master.variants, SelectionConfig(max_resolution="240p"), notes)
    assert notes


def test_selection_is_deterministic() -> None:
    master = load("master_audio_subs.m3u8")
    first = select_stream(master, SelectionConfig(quality="best"))
    for _ in range(5):
        assert select_stream(master, SelectionConfig(quality="best")) == first


def test_no_variants_is_an_error() -> None:
    with pytest.raises(PlaylistError, match="no streams"):
        select_variant((), SelectionConfig())


def test_ties_prefer_better_supported_codecs_then_the_first_listed() -> None:
    hevc = variant(3000, (1920, 1080), "hvc1.2.4", uri="hevc")
    avc = variant(3000, (1920, 1080), "avc1.640028", uri="avc")
    assert select_variant((hevc, avc), SelectionConfig()).uri == "avc"
    twin_a = variant(3000, (1920, 1080), "avc1.640028", uri="a")
    twin_b = variant(3000, (1920, 1080), "avc1.640028", uri="b")
    assert select_variant((twin_a, twin_b), SelectionConfig()).uri == "a"
    assert select_variant((twin_b, twin_a), SelectionConfig()).uri == "b"
    assert select_variant((twin_a, twin_b), SelectionConfig(quality="worst")).uri == "a"


def test_bandwidth_and_frame_rate_break_ties_in_resolution() -> None:
    low = variant(2000, (1280, 720), uri="low")
    high = variant(4000, (1280, 720), uri="high")
    assert select_variant((low, high), SelectionConfig()).uri == "high"
    slow = variant(2000, (1280, 720), uri="slow", frame_rate=24.0)
    fast = variant(2000, (1280, 720), uri="fast", frame_rate=60.0)
    assert select_variant((slow, fast), SelectionConfig()).uri == "fast"


def test_audio_only_streams_are_skipped_when_video_exists() -> None:
    master = load("master_audio_subs.m3u8")
    assert heights(master, quality="worst") == 720
    only_audio = (variant(64000, None, "mp4a.40.2", uri="audio"),)
    assert select_variant(only_audio, SelectionConfig()).uri == "audio"


def test_streams_without_a_size_are_not_ruled_out_by_a_size_limit() -> None:
    unknown = variant(1000, uri="unknown")
    big = variant(9000, (3840, 2160), uri="big")
    assert select_variant((unknown, big), SelectionConfig(max_resolution="720p")).uri == "unknown"


def test_portrait_video_is_judged_by_its_shorter_side() -> None:
    portrait = variant(2000, (720, 1280), uri="portrait")
    landscape = variant(1000, (1280, 720), uri="landscape")
    assert quality_height(portrait) == 720
    assert select_variant((portrait, landscape), SelectionConfig(quality="720p")).uri == "portrait"
    assert select_variant((portrait,), SelectionConfig(max_resolution="720p")).uri == "portrait"


# --- audio and subtitles -----------------------------------------------------------------------


def audio(config: SelectionConfig) -> tuple[list[str], tuple[str, ...]]:
    chosen = select_stream(load("master_audio_subs.m3u8"), config)
    return [track.name for track in chosen.audio_tracks], chosen.notes


def subtitles(config: SelectionConfig) -> tuple[list[str], tuple[str, ...]]:
    chosen = select_stream(load("master_audio_subs.m3u8"), config)
    return [track.name for track in chosen.subtitle_tracks], chosen.notes


def test_default_selection_is_the_best_video_with_the_default_audio_and_no_subtitles() -> None:
    chosen = select_stream(load("master_audio_subs.m3u8"), SelectionConfig())
    assert quality_height(chosen.variant) == 1080
    assert [track.name for track in chosen.audio_tracks] == ["English"]
    assert chosen.subtitle_tracks == ()
    assert chosen.notes == ()


def test_audio_language_selection() -> None:
    assert audio(SelectionConfig(audio_languages=("fr",))) == (["Français"], ())
    assert audio(SelectionConfig(audio_languages=("en",)))[0] == ["English"]


def test_several_audio_languages_keep_the_requested_order() -> None:
    names, notes = audio(SelectionConfig(audio_languages=("fr", "en")))
    assert names == ["Français", "English"]
    assert notes == ()


def test_a_main_language_matches_its_regional_variant() -> None:
    assert audio(SelectionConfig(audio_languages=("de",)))[0] == ["Deutsch"]
    assert audio(SelectionConfig(audio_languages=("de-DE",)))[0] == ["Deutsch"]
    names, notes = audio(SelectionConfig(audio_languages=("de-AT",)))
    assert names == ["English"]
    assert notes


def test_an_unavailable_audio_language_falls_back_to_the_default_with_notes() -> None:
    names, notes = audio(SelectionConfig(audio_languages=("pt",)))
    assert names == ["English"]
    assert any("'pt'" in note for note in notes)
    assert any("default audio track" in note for note in notes)


def test_a_partly_available_audio_list_keeps_what_exists() -> None:
    names, notes = audio(SelectionConfig(audio_languages=("fr", "pt")))
    assert names == ["Français"]
    assert len(notes) == 1


def test_audio_tracks_can_be_chosen_by_name() -> None:
    names, _ = audio(SelectionConfig(audio_names=("english commentary",)))
    assert names == ["English Commentary"]
    names, notes = audio(SelectionConfig(audio_names=("Klingon",)))
    assert names == ["English"]
    assert any("Klingon" in note for note in notes)


def test_subtitles_are_off_unless_requested() -> None:
    assert subtitles(SelectionConfig())[0] == []


def test_subtitle_language_selection_prefers_unforced_tracks() -> None:
    assert subtitles(SelectionConfig(subtitle_languages=("en",)))[0] == ["English"]
    names, _ = subtitles(SelectionConfig(subtitle_languages=("es", "en")))
    assert names == ["Español", "English"]
    names, _ = subtitles(SelectionConfig(subtitle_names=("English (forced)",)))
    assert names == ["English (forced)"]


def test_an_unavailable_subtitle_language_selects_nothing_and_says_so() -> None:
    names, notes = subtitles(SelectionConfig(subtitle_languages=("ja",)))
    assert names == []
    assert any("'ja'" in note for note in notes)


def test_streams_without_tracks_say_so() -> None:
    chosen = select_stream(
        load("master_basic.m3u8"),
        SelectionConfig(audio_languages=("en",), subtitle_languages=("en",)),
    )
    assert chosen.audio_tracks == ()
    assert chosen.subtitle_tracks == ()
    assert len(chosen.notes) == 2


def test_audio_that_is_part_of_the_video_needs_no_separate_download() -> None:
    muxed = AudioTrack(group_id="a", name="Main", language="en", uri=None, default=True)
    master = MasterPlaylist(
        uri=BASE,
        variants=(variant(1000, (1280, 720), uri="v", audio_group="a"),),
        audio_tracks=(muxed,),
    )
    chosen = select_stream(master, SelectionConfig())
    assert chosen.audio_tracks == ()
    assert chosen.notes == ()


def test_language_matching_ignores_case() -> None:
    master = MasterPlaylist(
        uri=BASE,
        variants=(variant(1000, (1280, 720), uri="v", audio_group="a", subtitle_group="s"),),
        audio_tracks=(AudioTrack(group_id="a", name="Eng", language="EN", uri="a.m3u8"),),
        subtitle_tracks=(SubtitleTrack(group_id="s", name="Sub", language="En-us", uri="s.m3u8"),),
    )
    chosen = select_stream(
        master, SelectionConfig(audio_languages=("en",), subtitle_languages=("en-US",))
    )
    assert [track.name for track in chosen.audio_tracks] == ["Eng"]
    assert [track.name for track in chosen.subtitle_tracks] == ["Sub"]


def test_video_only_skips_audio_and_subtitle_tracks() -> None:
    master = load("master_audio_subs.m3u8")
    wanted = SelectionConfig(audio_languages=("fr",), subtitle_languages=("en",), video_only=True)
    chosen = select_stream(master, wanted)
    assert quality_height(chosen.variant) == 1080
    assert chosen.audio_tracks == ()
    assert chosen.subtitle_tracks == ()
    assert any("Only the video" in note for note in chosen.notes)
    assert SelectionConfig().as_dict()["video_only"] is False
    assert wanted.as_dict()["video_only"] is True
