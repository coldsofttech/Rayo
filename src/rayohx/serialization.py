"""Plain-data views of playlists, selections, progress and results.

Front ends (the command line's ``--json``, the API, the window) all need to show the same things.
Doing it here keeps them identical. Every address is passed through the redactor first, so a signed
link's token never ends up in a log or a pipe.
"""

from typing import Any

from rayohx.models import (
    AudioTrack,
    DownloadProgress,
    DownloadResult,
    MasterPlaylist,
    MediaPlaylist,
    Playlist,
    SubtitleTrack,
    Variant,
    VideoTrack,
)
from rayohx.redaction import redact_url
from rayohx.selection import StreamSelection


def variant_to_dict(index: int, variant: Variant) -> dict[str, Any]:
    """A variant, with its position in the playlist."""
    return {
        "index": index,
        "name": variant.name,
        "resolution": str(variant.resolution) if variant.resolution else None,
        "bandwidth": variant.bandwidth,
        "average_bandwidth": variant.average_bandwidth,
        "frame_rate": variant.frame_rate,
        "codecs": list(variant.codecs),
        "video_range": variant.video_range,
        "audio_group": variant.audio_group,
        "subtitle_group": variant.subtitle_group,
        "url": redact_url(variant.uri),
    }


def track_to_dict(track: AudioTrack | SubtitleTrack | VideoTrack) -> dict[str, Any]:
    """An audio, subtitle or video track."""
    data: dict[str, Any] = {
        "group": track.group_id,
        "name": track.name,
        "default": track.default,
        "autoselect": track.autoselect,
        # A track without an address of its own is inside the video: nothing separate to download.
        "separate": track.uri is not None,
        "url": redact_url(track.uri) if track.uri else None,
    }
    if not isinstance(track, VideoTrack):
        data["language"] = track.language
        data["forced"] = track.forced
    if isinstance(track, AudioTrack):
        data["channels"] = track.channels
    return data


def selection_to_dict(master: MasterPlaylist, selection: StreamSelection) -> dict[str, Any]:
    """What a download with the current settings would pick."""
    index = master.variants.index(selection.variant)
    return {
        "variant": index,
        "audio": [track.name for track in selection.audio_tracks],
        "subtitles": [track.name for track in selection.subtitle_tracks],
        "notes": list(selection.notes),
    }


def playlist_to_dict(
    playlist: Playlist, selection: StreamSelection | None = None
) -> dict[str, Any]:
    """Describe a master or media playlist."""
    if isinstance(playlist, MasterPlaylist):
        data: dict[str, Any] = {
            "type": "master",
            "url": redact_url(playlist.uri),
            "variants": [variant_to_dict(i, v) for i, v in enumerate(playlist.variants)],
            "audio_tracks": [track_to_dict(t) for t in playlist.audio_tracks],
            "subtitle_tracks": [track_to_dict(t) for t in playlist.subtitle_tracks],
            "video_tracks": [track_to_dict(t) for t in playlist.video_tracks],
        }
        if selection is not None:
            data["selection"] = selection_to_dict(playlist, selection)
        return data
    return _media_to_dict(playlist)


def _media_to_dict(playlist: MediaPlaylist) -> dict[str, Any]:
    methods = sorted(
        {s.encryption.method.value for s in playlist.segments if s.encryption is not None}
    )
    return {
        "type": "media",
        "url": redact_url(playlist.uri),
        "segments": playlist.segment_count,
        "duration": round(playlist.total_duration, 3),
        "target_duration": playlist.target_duration,
        "container": playlist.container.value,
        "complete": playlist.is_vod,
        "media_sequence": playlist.media_sequence,
        "encryption": methods,
        "initialization_section": any(s.init_map is not None for s in playlist.segments),
        "byte_ranges": any(s.byte_range is not None for s in playlist.segments),
        "discontinuities": sum(1 for s in playlist.segments if s.discontinuity),
    }


def progress_to_dict(progress: DownloadProgress) -> dict[str, Any]:
    """A progress snapshot."""
    return {
        "state": progress.state.value,
        "downloaded_bytes": progress.downloaded_bytes,
        "written_bytes": progress.written_bytes,
        "total_bytes": progress.total_bytes,
        "completed_segments": progress.completed_segments,
        "total_segments": progress.total_segments,
        "fraction": progress.fraction,
        "speed": round(progress.speed, 1),
        "average_speed": round(progress.average_speed, 1),
        "eta_seconds": None if progress.eta_seconds is None else round(progress.eta_seconds, 1),
        "active_workers": progress.active_workers,
        "peak_temporary_bytes": progress.storage.peak_temporary_bytes,
    }


def result_to_dict(result: DownloadResult) -> dict[str, Any]:
    """How a download ended."""
    return {
        "download_id": result.download_id,
        "state": result.state.value,
        "success": result.success,
        "cancelled": result.cancelled,
        "url": result.url,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "output": (
            {
                "path": str(result.output.path),
                "size_bytes": result.output.size_bytes,
                "container": result.output.container.value,
            }
            if result.output
            else None
        ),
        "error": result.error,
        "progress": progress_to_dict(result.progress),
    }
