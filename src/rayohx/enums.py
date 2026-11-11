"""Enumerations shared across the engine."""

from enum import StrEnum


class DownloadState(StrEnum):
    """Where a download is in its life cycle."""

    QUEUED = "queued"
    INSPECTING = "inspecting"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"

    @property
    def is_terminal(self) -> bool:
        """Whether no further state change is possible."""
        return self in {DownloadState.COMPLETED, DownloadState.FAILED, DownloadState.CANCELLED}


class SegmentState(StrEnum):
    """Where a single segment is in its life cycle."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class EventType(StrEnum):
    """Kinds of event the engine emits."""

    DOWNLOAD_STARTED = "download_started"
    PLAYLIST_LOADED = "playlist_loaded"
    STREAM_SELECTED = "stream_selected"
    SEGMENT_STARTED = "segment_started"
    SEGMENT_COMPLETED = "segment_completed"
    SEGMENT_FAILED = "segment_failed"
    DOWNLOAD_PROGRESS = "download_progress"
    FINALIZATION_STARTED = "finalization_started"
    FINALIZATION_COMPLETED = "finalization_completed"
    DOWNLOAD_COMPLETED = "download_completed"
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOAD_CANCELLED = "download_cancelled"


class ContainerFormat(StrEnum):
    """How the segments of a stream are packaged."""

    MPEG_TS = "mpeg-ts"
    FMP4 = "fmp4"
    WEBVTT = "webvtt"
    UNKNOWN = "unknown"


class EncryptionMethod(StrEnum):
    """The ``METHOD`` of an ``EXT-X-KEY`` tag."""

    NONE = "NONE"
    AES_128 = "AES-128"
    SAMPLE_AES = "SAMPLE-AES"


class StorageStrategy(StrEnum):
    """How segment data moves from the network to the output."""

    AUTO = "auto"
    DIRECT = "direct"
    STREAMING = "streaming"
    BUFFERED = "buffered"
    TEMPORARY = "temporary"
    HYBRID = "hybrid"


class OutputFormat(StrEnum):
    """The kind of file the user asks for."""

    AUTO = "auto"
    TS = "ts"
    MP4 = "mp4"
    SEGMENTS = "segments"


class RemuxBackend(StrEnum):
    """Which tool may remux the stream into the requested container."""

    AUTO = "auto"
    NATIVE = "native"
    FFMPEG = "ffmpeg"
