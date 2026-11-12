"""MPEG audio (MP3, MP2): framing and the ``esds`` configuration."""

from rayohx.mux.aac import audio_sample_entry, esds
from rayohx.mux.model import TrackConfig

_BITRATES = {
    (1, 1): (32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 3: (11025, 12000, 8000)}


class Mp3Parser:
    """Splits MPEG audio frames out of PES payloads."""

    def __init__(self) -> None:
        self.config: TrackConfig | None = None
        self.sample_rate = 0
        self.samples_per_frame = 1152
        self._buffer = b""

    def frames(self, payload: bytes) -> list[bytes]:
        """Return the complete audio frames in the data so far."""
        data = self._buffer + payload
        frames = []
        position = 0
        while position + 4 <= len(data):
            header = int.from_bytes(data[position : position + 4], "big")
            info = _parse_header(header)
            if info is None:
                position += 1
                continue
            size, rate, samples, channels, layer, version = info
            if position + size > len(data):
                break
            if self.config is None:
                self._build(rate, samples, channels, layer, version)
            frames.append(data[position : position + size])
            position += size
        self._buffer = data[position:]
        return frames

    def flush(self) -> list[bytes]:
        """Forget a partial frame at the end of the data."""
        self._buffer = b""
        return []

    def _build(self, rate: int, samples: int, channels: int, layer: int, version: int) -> None:
        self.sample_rate = rate
        self.samples_per_frame = samples
        object_type = 0x6B if version == 1 else 0x69
        entry = audio_sample_entry(b"mp4a", channels, rate, esds(object_type))
        self.config = TrackConfig(
            "audio", rate, entry, f"mp4a.{object_type:02x}", channels=channels, sample_rate=rate
        )


def _parse_header(header: int) -> tuple[int, int, int, int, int, int] | None:
    if header >> 21 != 0x7FF:
        return None
    version_bits = (header >> 19) & 3
    layer_bits = (header >> 17) & 3
    bitrate_index = (header >> 12) & 0xF
    rate_index = (header >> 10) & 3
    if version_bits == 1 or layer_bits == 0 or bitrate_index in (0, 15) or rate_index == 3:
        return None
    version = {3: 1, 2: 2, 0: 3}[version_bits]
    layer = 4 - layer_bits
    bitrate = _BITRATES[(1 if version == 1 else 2, layer)][bitrate_index - 1] * 1000
    rate = _RATES[version][rate_index]
    padding = (header >> 9) & 1
    channels = 1 if (header >> 6) & 3 == 3 else 2
    if layer == 1:
        size = (12 * bitrate // rate + padding) * 4
        samples = 384
    else:
        samples = 1152 if (layer == 2 or version == 1) else 576
        size = (samples // 8) * bitrate // rate + padding
    return size, rate, samples, channels, layer, version
