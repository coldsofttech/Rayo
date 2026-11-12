"""Choosing a variant and its audio and subtitle tracks.

Selection is a pure function of the playlist and the settings: the same input always gives the
same answer. Whenever a wish cannot be met exactly, the closest sensible track is used and a note
explains why, so nothing is silently dropped.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from rayohx.config import SelectionConfig
from rayohx.errors import PlaylistError
from rayohx.models import AudioTrack, MasterPlaylist, SubtitleTrack, Variant

#: Height that ``quality="auto"`` aims for: sharp, without the cost of the very largest stream.
AUTO_TARGET_HEIGHT = 1080

_VIDEO_PREFIXES = ("avc", "hvc", "hev", "vp0", "av01", "dvh", "dvav", "mp4v")
#: Codec families from most to least widely playable. Only breaks ties between equal streams.
_CODEC_RANKS = (("avc", 4), ("hvc", 3), ("hev", 3), ("vp0", 2), ("av01", 1))

Track = TypeVar("Track", AudioTrack, SubtitleTrack)


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamSelection:
    """The tracks to download. ``notes`` explain any fallback that was needed."""

    variant: Variant
    audio_tracks: tuple[AudioTrack, ...] = ()
    subtitle_tracks: tuple[SubtitleTrack, ...] = ()
    notes: tuple[str, ...] = ()


def select_stream(master: MasterPlaylist, config: SelectionConfig) -> StreamSelection:
    """Choose the variant, audio tracks and subtitle tracks for ``config``.

    Raises:
        PlaylistError: If the playlist has no variants.
    """
    notes: list[str] = []
    variant = select_variant(master.variants, config, notes)
    if config.video_only:
        notes.append(
            "Only the video stream is downloaded; separate audio and subtitles are skipped."
        )
        return StreamSelection(variant=variant, notes=tuple(notes))
    audio = _select_audio(master, variant, config, notes)
    subtitles = _select_subtitles(master, variant, config, notes)
    return StreamSelection(
        variant=variant,
        audio_tracks=audio,
        subtitle_tracks=subtitles,
        notes=tuple(notes),
    )


# --- variants ----------------------------------------------------------------------------------


def quality_height(variant: Variant) -> int:
    """Picture size as a "p" number: the shorter side, so portrait video is judged fairly."""
    if variant.resolution is None:
        return 0
    return min(variant.resolution.width, variant.resolution.height)


def _codec_rank(variant: Variant) -> int:
    for codec in variant.codecs:
        lowered = codec.lower()
        for prefix, rank in _CODEC_RANKS:
            if lowered.startswith(prefix):
                return rank
    return 0


def _is_audio_only(variant: Variant) -> bool:
    if variant.resolution is not None or not variant.codecs:
        return False
    return not any(codec.lower().startswith(_VIDEO_PREFIXES) for codec in variant.codecs)


def _best_key(entry: tuple[int, Variant]) -> tuple[int, int, int, float, int, int]:
    index, variant = entry
    return (
        quality_height(variant),
        variant.bandwidth,
        variant.average_bandwidth or 0,
        variant.frame_rate or 0.0,
        _codec_rank(variant),
        -index,  # equal streams: the one listed first wins
    )


def _worst_key(entry: tuple[int, Variant]) -> tuple[int, int, int]:
    index, variant = entry
    return (quality_height(variant), variant.bandwidth, index)


def _fits(variant: Variant, *, max_height: int | None, max_bitrate: int | None) -> bool:
    height = quality_height(variant)
    # A stream that does not say how big it is cannot be ruled out by size.
    if max_height is not None and height and height > max_height:
        return False
    return max_bitrate is None or variant.bandwidth <= max_bitrate


def select_variant(
    variants: Sequence[Variant], config: SelectionConfig, notes: list[str] | None = None
) -> Variant:
    """Choose one variant. Reasons for any fallback are appended to ``notes``.

    Raises:
        PlaylistError: If there are no variants.
    """
    notes = notes if notes is not None else []
    if not variants:
        raise PlaylistError("The playlist lists no streams to download.")

    entries = list(enumerate(variants))
    video = [entry for entry in entries if not _is_audio_only(entry[1])]
    entries = video or entries

    max_height = int(config.max_resolution[:-1]) if config.max_resolution else None
    allowed = [
        entry
        for entry in entries
        if (
            max_height is None
            or not quality_height(entry[1])
            or quality_height(entry[1]) <= max_height
        )
        and (config.max_bitrate is None or entry[1].bandwidth <= config.max_bitrate)
    ]
    if not allowed:
        chosen = min(entries, key=_worst_key)[1]
        notes.append(
            "No stream fits the resolution or bitrate limit, so the smallest one "
            f"({_describe(chosen)}) was chosen."
        )
        return chosen

    mode = config.quality
    if mode == "worst":
        return min(allowed, key=_worst_key)[1]
    if mode == "best":
        return max(allowed, key=_best_key)[1]
    target = AUTO_TARGET_HEIGHT if mode == "auto" else int(mode[:-1])
    return _closest_to(allowed, target, notes)


def _closest_to(entries: list[tuple[int, Variant]], target: int, notes: list[str]) -> Variant:
    """The best stream at or below ``target``; if all are larger, the smallest of them."""
    at_or_below = [entry for entry in entries if quality_height(entry[1]) <= target]
    if at_or_below:
        return max(at_or_below, key=_best_key)[1]
    chosen = min(entries, key=_worst_key)[1]
    notes.append(
        f"No stream is {target}p or smaller, so the closest one ({_describe(chosen)}) was chosen."
    )
    return chosen


def _describe(variant: Variant) -> str:
    size = f"{quality_height(variant)}p" if variant.resolution else "unknown size"
    return f"{size}, {variant.bandwidth // 1000} kbps"


# --- audio and subtitles -----------------------------------------------------------------------


def _normalise(language: str) -> str:
    return language.strip().lower().replace("_", "-")


def _language_matches(requested: str, available: str | None) -> int:
    """0 for no match, 2 for an exact match, 1 when only the main language agrees."""
    if not available:
        return 0
    wanted, offered = _normalise(requested), _normalise(available)
    if wanted == offered:
        return 2
    if wanted.partition("-")[0] == offered.partition("-")[0] and (
        "-" not in wanted or "-" not in offered
    ):
        return 1
    return 0


def _track_order(entry: tuple[int, Track]) -> tuple[bool, bool, bool, int]:
    index, track = entry
    return (track.forced, not track.default, not track.autoselect, index)


def _by_language(tracks: Sequence[Track], language: str) -> Track | None:
    scored = [
        (_language_matches(language, track.language), entry)
        for entry in enumerate(tracks)
        for track in [entry[1]]
    ]
    best_score = max((score for score, _ in scored), default=0)
    if best_score == 0:
        return None
    return min((entry for score, entry in scored if score == best_score), key=_track_order)[1]


def _by_name(tracks: Sequence[Track], name: str) -> Track | None:
    wanted = name.strip().lower()
    matches = [entry for entry in enumerate(tracks) if entry[1].name.lower() == wanted]
    return min(matches, key=_track_order)[1] if matches else None


def _default_track(tracks: Sequence[Track]) -> Track | None:
    return min(enumerate(tracks), key=_track_order)[1] if tracks else None


def _choose(
    tracks: Sequence[Track],
    languages: Sequence[str],
    names: Sequence[str],
    kind: str,
    notes: list[str],
) -> list[Track]:
    chosen: list[Track] = []
    for name in names:
        track = _by_name(tracks, name)
        if track is None:
            notes.append(f"No {kind} track is named {name!r}.")
        elif track not in chosen:
            chosen.append(track)
    for language in languages:
        track = _by_language(tracks, language)
        if track is None:
            notes.append(f"No {kind} track is available in {language!r}.")
        elif track not in chosen:
            chosen.append(track)
    return chosen


def _select_audio(
    master: MasterPlaylist, variant: Variant, config: SelectionConfig, notes: list[str]
) -> tuple[AudioTrack, ...]:
    tracks = master.audio_group(variant.audio_group) if variant.audio_group else ()
    wanted = bool(config.audio_languages or config.audio_names)
    if not tracks:
        if wanted:
            notes.append("The stream has no separate audio tracks, so its built-in audio is used.")
        return ()
    chosen = _choose(tracks, config.audio_languages, config.audio_names, "audio", notes)
    if not chosen:
        default = _default_track(tracks)
        if wanted and default is not None:
            notes.append(f"Using the default audio track {default.name!r} instead.")
        chosen = [default] if default is not None else []
    # A track without its own address is already inside the video segments.
    return tuple(track for track in chosen if track.uri)


def _select_subtitles(
    master: MasterPlaylist, variant: Variant, config: SelectionConfig, notes: list[str]
) -> tuple[SubtitleTrack, ...]:
    if not (config.subtitle_languages or config.subtitle_names):
        return ()
    tracks = master.subtitle_group(variant.subtitle_group) if variant.subtitle_group else ()
    if not tracks:
        notes.append("The stream has no subtitle tracks.")
        return ()
    chosen = _choose(tracks, config.subtitle_languages, config.subtitle_names, "subtitle", notes)
    return tuple(track for track in chosen if track.uri)
