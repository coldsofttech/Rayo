"""Immutable domain models: playlists, tracks, segments, progress and results.

The models carry data and simple derived values only. Parsing, selecting and downloading live in
other modules, so any front end can use these types without pulling in the engine.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Self

from rayohx.config import DownloadConfig, validate_url
from rayohx.enums import (
    ContainerFormat,
    DownloadState,
    EncryptionMethod,
)
from rayohx.errors import InvalidPlaylistError
from rayohx.redaction import redact_url

_RESOLUTION = re.compile(r"^(\d+)x(\d+)$")
_BYTE_RANGE = re.compile(r"^(\d+)(?:@(\d+))?$")
_IV = re.compile(r"^0[xX]([0-9A-Fa-f]{1,32})$")


@dataclass(frozen=True, slots=True, order=True)
class Resolution:
    """Width and height in pixels."""

    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width and height must be positive")

    @classmethod
    def parse(cls, text: str) -> Self:
        """Read ``1920x1080``.

        Raises:
            InvalidPlaylistError: If ``text`` is not in that form.
        """
        match = _RESOLUTION.match(text.strip())
        if not match or int(match.group(1)) == 0 or int(match.group(2)) == 0:
            raise InvalidPlaylistError(f"{text!r} is not a valid resolution.")
        return cls(int(match.group(1)), int(match.group(2)))

    @property
    def pixels(self) -> int:
        """Total pixel count."""
        return self.width * self.height

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass(frozen=True, slots=True)
class SegmentRange:
    """A byte range inside a resource. ``offset`` is ``None`` when it follows the previous one."""

    length: int
    offset: int | None = None

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("length must be positive")
        if self.offset is not None and self.offset < 0:
            raise ValueError("offset must not be negative")

    @classmethod
    def parse(cls, text: str) -> Self:
        """Read the ``length[@offset]`` form used by ``EXT-X-BYTERANGE`` and ``EXT-X-MAP``.

        Raises:
            InvalidPlaylistError: If ``text`` is not in that form.
        """
        match = _BYTE_RANGE.match(text.strip())
        if not match or int(match.group(1)) == 0:
            raise InvalidPlaylistError(f"{text!r} is not a valid byte range.")
        offset = int(match.group(2)) if match.group(2) is not None else None
        return cls(int(match.group(1)), offset)

    def http_header(self) -> str:
        """The ``Range`` header value for this range.

        Raises:
            ValueError: If the offset is not known yet.
        """
        if self.offset is None:
            raise ValueError("the offset must be resolved before building a Range header")
        return f"bytes={self.offset}-{self.offset + self.length - 1}"


@dataclass(frozen=True, slots=True)
class EncryptionInfo:
    """An ``EXT-X-KEY`` entry. The key itself is fetched later, through the HTTP layer."""

    method: EncryptionMethod
    uri: str | None = None
    iv: bytes | None = None
    key_format: str = "identity"
    key_format_versions: str | None = None

    def __post_init__(self) -> None:
        if self.method is not EncryptionMethod.NONE and not self.uri:
            raise ValueError("an encrypted segment needs a key address")
        if self.iv is not None and len(self.iv) != 16:
            raise ValueError("the initialization vector must be 16 bytes")

    @staticmethod
    def parse_iv(text: str) -> bytes:
        """Read an ``IV`` attribute such as ``0x00112233445566778899aabbccddeeff``.

        Raises:
            InvalidPlaylistError: If ``text`` is not a hexadecimal number of up to 128 bits.
        """
        match = _IV.match(text.strip())
        if not match:
            raise InvalidPlaylistError(f"{text!r} is not a valid initialization vector.")
        return bytes.fromhex(match.group(1).rjust(32, "0"))

    @property
    def is_supported(self) -> bool:
        """Whether the engine can decrypt this entry (plain AES-128, no DRM key format)."""
        if self.method is EncryptionMethod.NONE:
            return True
        return self.method is EncryptionMethod.AES_128 and self.key_format == "identity"


@dataclass(frozen=True, slots=True)
class InitMap:
    """An ``EXT-X-MAP`` initialization section."""

    uri: str
    byte_range: SegmentRange | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Segment:
    """One media segment, with everything needed to fetch and place it."""

    index: int
    uri: str
    duration: float
    sequence_number: int
    byte_range: SegmentRange | None = None
    init_map: InitMap | None = None
    encryption: EncryptionInfo | None = None
    discontinuity: bool = False
    program_date_time: datetime | None = None
    title: str = ""

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("index must not be negative")
        if self.duration < 0:
            raise ValueError("duration must not be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaPlaylist:
    """A playlist of segments."""

    uri: str
    segments: tuple[Segment, ...]
    target_duration: float
    media_sequence: int = 0
    end_list: bool = False
    version: int | None = None
    playlist_type: str | None = None
    container: ContainerFormat = ContainerFormat.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))
        if self.target_duration < 0:
            raise ValueError("target_duration must not be negative")

    @property
    def segment_count(self) -> int:
        """Number of segments."""
        return len(self.segments)

    @property
    def total_duration(self) -> float:
        """Sum of the segment durations, in seconds."""
        return sum(segment.duration for segment in self.segments)

    @property
    def is_vod(self) -> bool:
        """Whether the playlist is complete and will not grow."""
        return self.end_list or self.playlist_type == "VOD"


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentData:
    """A downloaded segment, decrypted. ``init`` is the initialization section to write first."""

    index: int
    media: bytes
    init: bytes = b""

    @property
    def size(self) -> int:
        """Bytes this segment adds to the output."""
        return len(self.init) + len(self.media)


@dataclass(frozen=True, slots=True, kw_only=True)
class Variant:
    """One ``EXT-X-STREAM-INF`` entry: a rendition of the video."""

    uri: str
    bandwidth: int
    average_bandwidth: int | None = None
    resolution: Resolution | None = None
    codecs: tuple[str, ...] = ()
    frame_rate: float | None = None
    video_range: str | None = None
    video_group: str | None = None
    audio_group: str | None = None
    subtitle_group: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.bandwidth < 0:
            raise ValueError("bandwidth must not be negative")
        object.__setattr__(self, "codecs", tuple(self.codecs))

    @property
    def height(self) -> int | None:
        """Picture height in pixels, when known."""
        return self.resolution.height if self.resolution else None


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoTrack:
    """An alternative video rendition (``EXT-X-MEDIA`` of type ``VIDEO``)."""

    group_id: str
    name: str
    uri: str | None = None
    default: bool = False
    autoselect: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioTrack:
    """An audio rendition. ``uri`` is ``None`` when the audio is inside the video segments."""

    group_id: str
    name: str
    language: str | None = None
    uri: str | None = None
    default: bool = False
    autoselect: bool = False
    forced: bool = False
    channels: str | None = None
    characteristics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SubtitleTrack:
    """A subtitle rendition."""

    group_id: str
    name: str
    language: str | None = None
    uri: str | None = None
    default: bool = False
    autoselect: bool = False
    forced: bool = False
    characteristics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class MasterPlaylist:
    """A playlist of variants and the tracks that go with them."""

    uri: str
    variants: tuple[Variant, ...]
    video_tracks: tuple[VideoTrack, ...] = ()
    audio_tracks: tuple[AudioTrack, ...] = ()
    subtitle_tracks: tuple[SubtitleTrack, ...] = ()
    version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "variants", tuple(self.variants))
        object.__setattr__(self, "video_tracks", tuple(self.video_tracks))
        object.__setattr__(self, "audio_tracks", tuple(self.audio_tracks))
        object.__setattr__(self, "subtitle_tracks", tuple(self.subtitle_tracks))

    def audio_group(self, group_id: str) -> tuple[AudioTrack, ...]:
        """The audio tracks of one group."""
        return tuple(track for track in self.audio_tracks if track.group_id == group_id)

    def subtitle_group(self, group_id: str) -> tuple[SubtitleTrack, ...]:
        """The subtitle tracks of one group."""
        return tuple(track for track in self.subtitle_tracks if track.group_id == group_id)


Playlist = MasterPlaylist | MediaPlaylist


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class DownloadRequest:
    """A web address to download and the settings to download it with."""

    url: str
    config: DownloadConfig = field(default_factory=DownloadConfig)

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", validate_url(self.url))

    def __repr__(self) -> str:
        return f"DownloadRequest(url={self.redacted_url!r}, config={self.config!r})"

    @property
    def redacted_url(self) -> str:
        """The address with credentials hidden, for logs and messages."""
        return redact_url(self.url)


@dataclass(frozen=True, slots=True, kw_only=True)
class StorageMetrics:
    """A snapshot of how much data the download has moved and kept, in bytes."""

    output_bytes: int = 0
    temporary_bytes: int = 0
    working_bytes: int = 0
    peak_temporary_bytes: int = 0
    downloaded_bytes: int = 0
    written_bytes: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadProgress:
    """A snapshot of a download, ready to show in any front end."""

    state: DownloadState = DownloadState.QUEUED
    downloaded_bytes: int = 0
    written_bytes: int = 0
    total_bytes: int | None = None
    completed_segments: int = 0
    total_segments: int = 0
    speed: float = 0.0
    average_speed: float = 0.0
    eta_seconds: float | None = None
    active_workers: int = 0
    storage: StorageMetrics = field(default_factory=StorageMetrics)

    @property
    def fraction(self) -> float | None:
        """Share done between 0 and 1, from bytes when the total is known, else from segments."""
        if self.total_bytes:
            return min(1.0, self.downloaded_bytes / self.total_bytes)
        if self.total_segments:
            return min(1.0, self.completed_segments / self.total_segments)
        return None


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputInfo:
    """The file a download produced."""

    path: Path
    container: ContainerFormat
    size_bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadResult:
    """How a download ended. A cancelled download has its own state, not a failure."""

    download_id: str
    url: str
    state: DownloadState
    elapsed_seconds: float
    progress: DownloadProgress
    output: OutputInfo | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.state.is_terminal:
            raise ValueError("a result needs a finished state")
        object.__setattr__(self, "url", redact_url(self.url))

    @property
    def success(self) -> bool:
        """Whether the download completed."""
        return self.state is DownloadState.COMPLETED

    @property
    def cancelled(self) -> bool:
        """Whether the download was cancelled."""
        return self.state is DownloadState.CANCELLED
