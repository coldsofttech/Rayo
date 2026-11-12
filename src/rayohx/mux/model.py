"""What the muxer passes around: track descriptions and compressed samples."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrackConfig:
    """Everything needed to describe a track in an MP4 file."""

    kind: str  # "video" or "audio"
    timescale: int
    #: The complete sample entry box (``avc1``, ``hvc1``, ``mp4a``, ``ac-3`` or ``ec-3``).
    sample_entry: bytes
    #: A short description such as ``avc1.640028``, for messages.
    codec: str
    width: int = 0
    height: int = 0
    channels: int = 0
    sample_rate: int = 0
    language: str = "und"
    name: str = ""


@dataclass(slots=True)
class Sample:
    """One compressed frame, in the track's timescale."""

    data: bytes
    dts: int
    duration: int
    #: How far the presentation time is after the decode time (zero without B-frames).
    cts_offset: int = 0
    sync: bool = True
