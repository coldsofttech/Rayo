"""AAC in ADTS framing: to MP4 samples and the ``esds`` configuration."""

import struct

from rayohx.errors import OutputFormatError
from rayohx.mux.boxes import box, descriptor, full_box, u16, u32
from rayohx.mux.model import Sample, TrackConfig

SAMPLE_RATES = (
    96000,
    88200,
    64000,
    48000,
    44100,
    32000,
    24000,
    22050,
    16000,
    12000,
    11025,
    8000,
    7350,
)
FRAME_SAMPLES = 1024


def audio_sample_entry(kind: bytes, channels: int, sample_rate: int, *children: bytes) -> bytes:
    """An audio sample entry (``mp4a``, ``ac-3``, ``ec-3``) with its configuration boxes."""
    return box(
        kind,
        bytes(6),
        u16(1),  # data reference index
        bytes(8),  # version, revision, vendor
        u16(channels),
        u16(16),  # sample size
        u16(0),
        u16(0),
        u32(min(sample_rate, 0xFFFF) << 16),
        *children,
    )


def esds(object_type: int, specific_info: bytes = b"", bitrate: int = 0) -> bytes:
    """The ``esds`` box: object type, and optionally the decoder-specific configuration."""
    decoder = descriptor(
        0x04,
        bytes([object_type, 0x15]),
        b"\x00\x00\x00",  # buffer size
        u32(bitrate),
        u32(bitrate),
        *([descriptor(0x05, specific_info)] if specific_info else []),
    )
    stream = descriptor(0x03, u16(0), b"\x00", decoder, descriptor(0x06, b"\x02"))
    return full_box(b"esds", 0, 0, stream)


class AacParser:
    """Splits ADTS frames out of PES payloads. Frames may be cut across payloads."""

    def __init__(self) -> None:
        self.config: TrackConfig | None = None
        self._buffer = b""
        self.sample_rate = 0
        self.samples_per_frame = FRAME_SAMPLES

    def frames(self, payload: bytes) -> list[bytes]:
        """Return the complete raw AAC frames (headers removed) in the data so far."""
        data = self._buffer + payload
        frames = []
        position = 0
        while position + 7 <= len(data):
            if data[position] != 0xFF or data[position + 1] & 0xF0 != 0xF0:
                position = self._resync(data, position + 1)
                continue
            header = data[position : position + 7]
            protection_absent = header[1] & 1
            length = ((header[3] & 3) << 11) | (header[4] << 3) | (header[5] >> 5)
            header_size = 7 if protection_absent else 9
            if length < header_size:
                position += 1
                continue
            if position + length > len(data):
                break
            if self.config is None:
                self._build(header)
            frames.append(data[position + header_size : position + length])
            position += length
        self._buffer = data[position:]
        return frames

    @staticmethod
    def _resync(data: bytes, start: int) -> int:
        found = data.find(b"\xff", start)
        return len(data) if found < 0 else found

    def flush(self) -> list[bytes]:
        """Forget a partial frame at the end of the data."""
        self._buffer = b""
        return []

    def _build(self, header: bytes) -> None:
        profile = (header[2] >> 6) + 1
        index = (header[2] >> 2) & 0xF
        channels = ((header[2] & 1) << 2) | (header[3] >> 6)
        if index >= len(SAMPLE_RATES):
            raise OutputFormatError("The AAC audio uses a sampling rate that is not supported.")
        if channels == 0:
            raise OutputFormatError("AAC audio with a custom channel layout is not supported.")
        rate = SAMPLE_RATES[index]
        config = struct.pack(">H", (profile << 11) | (index << 7) | (channels << 3))
        entry = audio_sample_entry(b"mp4a", channels, rate, esds(0x40, config))
        self.sample_rate = rate
        self.config = TrackConfig(
            "audio", rate, entry, f"mp4a.40.{profile}", channels=channels, sample_rate=rate
        )


def audio_samples(frames: list[bytes], start: int, per_frame: int) -> list[Sample]:
    """Samples for consecutive audio frames of ``per_frame`` samples, from time ``start``."""
    return [
        Sample(data=frame, dts=start + index * per_frame, duration=per_frame)
        for index, frame in enumerate(frames)
    ]
