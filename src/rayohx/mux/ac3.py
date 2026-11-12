"""Dolby Digital (AC-3) and Dolby Digital Plus (E-AC-3): framing and the ``dac3``/``dec3`` boxes."""

from dataclasses import dataclass

from rayohx.errors import OutputFormatError
from rayohx.mux.aac import audio_sample_entry
from rayohx.mux.bits import BitReader
from rayohx.mux.boxes import box
from rayohx.mux.model import TrackConfig

SYNC = b"\x0b\x77"
SAMPLE_RATES = (48000, 44100, 32000)
_HALF_RATES = (24000, 22050, 16000)
_CHANNELS = (2, 1, 2, 3, 3, 4, 4, 5)  # indexed by audio coding mode, without the LFE channel
_BLOCKS = (1, 2, 3, 6)
# Frame sizes in 16-bit words for fscod 0 (48 kHz), 1 (44.1 kHz) and 2 (32 kHz); the 44.1 kHz
# column alternates between two sizes, so its values are for the even frame size code.
_FRAME_WORDS_48 = (
    64, 64, 80, 80, 96, 96, 112, 112, 128, 128, 160, 160, 192, 192, 224, 224, 256, 256,
    320, 320, 384, 384, 448, 448, 512, 512, 640, 640, 768, 768, 896, 896, 1024, 1024,
    1152, 1152, 1280, 1280,
)  # fmt: skip
_FRAME_WORDS_44 = (
    69, 70, 87, 88, 104, 105, 121, 122, 139, 140, 174, 175, 208, 209, 243, 244, 278, 279,
    348, 349, 417, 418, 487, 488, 557, 558, 696, 697, 835, 836, 975, 976, 1114, 1115,
    1253, 1254, 1393, 1394,
)  # fmt: skip
_FRAME_WORDS_32 = (
    96, 96, 120, 120, 144, 144, 168, 168, 192, 192, 240, 240, 288, 288, 336, 336, 384, 384,
    480, 480, 576, 576, 672, 672, 768, 768, 960, 960, 1152, 1152, 1344, 1344, 1536, 1536,
    1728, 1728, 1920, 1920,
)  # fmt: skip
_BITRATES = (
    32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512, 576, 640,
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Ac3Frame:
    """One synchronisation frame."""

    data: bytes
    enhanced: bool
    sample_rate: int
    samples: int
    channels: int
    bsid: int
    bsmod: int
    acmod: int
    lfe: bool
    frame_size_code: int
    #: Enhanced frames only: 0 for an independent stream, 1 for a dependent one.
    stream_type: int
    substream_id: int


def parse_frame(data: bytes, offset: int) -> Ac3Frame | None:
    """Read the frame that starts at ``offset``, or ``None`` if it is not all there yet."""
    if offset + 8 > len(data) or data[offset : offset + 2] != SYNC:
        return None
    bsid = data[offset + 5] >> 3
    if bsid <= 10:
        return _parse_ac3(data, offset, bsid)
    if bsid <= 16:
        return _parse_eac3(data, offset)
    raise OutputFormatError("This Dolby audio uses a bit stream version that is not supported.")


def _parse_ac3(data: bytes, offset: int, bsid: int) -> Ac3Frame | None:
    fscod = data[offset + 4] >> 6
    code = data[offset + 4] & 0x3F
    if fscod == 3 or code >= 38:
        raise OutputFormatError("The Dolby Digital audio has an invalid header.")
    words = (_FRAME_WORDS_48, _FRAME_WORDS_44, _FRAME_WORDS_32)[fscod][code]
    size = words * 2
    if offset + size > len(data):
        return None
    reader = BitReader(data[offset + 5 : offset + 8])
    reader.skip(5)
    bsmod = reader.read(3)
    acmod = reader.read(3)
    if acmod & 1 and acmod != 1:
        reader.skip(2)  # centre mix level
    if acmod & 4:
        reader.skip(2)  # surround mix level
    if acmod == 2:
        reader.skip(2)  # Dolby surround mode
    lfe = reader.flag()
    return Ac3Frame(
        data[offset : offset + size], False, SAMPLE_RATES[fscod], 1536,
        _CHANNELS[acmod] + int(lfe), bsid, bsmod, acmod, lfe, code, 0, 0,
    )  # fmt: skip


def _parse_eac3(data: bytes, offset: int) -> Ac3Frame | None:
    reader = BitReader(data[offset + 2 : offset + 8])
    stream_type = reader.read(2)
    substream = reader.read(3)
    size = (reader.read(11) + 1) * 2
    if offset + size > len(data):
        return None
    fscod = reader.read(2)
    if fscod == 3:
        rate = _HALF_RATES[reader.read(2)]
        blocks = 6
    else:
        rate = SAMPLE_RATES[fscod]
        blocks = _BLOCKS[reader.read(2)]
    acmod = reader.read(3)
    lfe = reader.flag()
    bsid = reader.read(5)
    return Ac3Frame(
        data[offset : offset + size], True, rate, 256 * blocks,
        _CHANNELS[acmod] + int(lfe), bsid, 0, acmod, lfe, 0, stream_type, substream,
    )  # fmt: skip


def dac3(frame: Ac3Frame) -> bytes:
    """The ``dac3`` box for Dolby Digital."""
    fscod = SAMPLE_RATES.index(frame.sample_rate)
    bits = (
        (fscod << 22) | (frame.bsid << 17) | (frame.bsmod << 14)
        | (frame.acmod << 11) | (int(frame.lfe) << 10) | ((frame.frame_size_code >> 1) << 5)
    )  # fmt: skip
    return box(b"dac3", bits.to_bytes(3, "big"))


def kbps(size: int, frame: Ac3Frame) -> int:
    """The data rate of ``size`` bytes per frame, in kilobits per second."""
    return round(size * 8 * frame.sample_rate / frame.samples / 1000)


def dec3(frame: Ac3Frame, dependent_substreams: int, data_rate_kbps: int) -> bytes:
    """The ``dec3`` box for Dolby Digital Plus with one independent substream."""
    fscod = SAMPLE_RATES.index(frame.sample_rate) if frame.sample_rate in SAMPLE_RATES else 3
    bits = data_rate_kbps & 0x1FFF  # 13 bits
    bits = (bits << 3) | 0  # one independent substream is written as zero
    for value, width in (
        (fscod, 2), (frame.bsid, 5), (0, 1), (0, 1), (frame.bsmod, 3), (frame.acmod, 3),
        (int(frame.lfe), 1), (0, 3), (dependent_substreams, 4),
    ):  # fmt: skip
        bits = (bits << width) | value
    total = 16 + 24 - 1
    if dependent_substreams:
        bits = (bits << 9) | 0  # channel locations: not signalled
        total += 9
    else:
        bits = (bits << 1) | 0
        total += 1
    padding = (-total) % 8
    bits <<= padding
    return box(b"dec3", bits.to_bytes((total + padding) // 8, "big"))


class Ac3Parser:
    """Splits Dolby audio into samples (one per access unit) from PES payloads."""

    def __init__(self) -> None:
        self.config: TrackConfig | None = None
        self.enhanced = False
        self.sample_rate = 0
        self.samples_per_frame = 1536
        self._buffer = b""
        self._first: Ac3Frame | None = None
        self._dependents = 0
        self._current: list[Ac3Frame] = []

    def frames(self, payload: bytes) -> list[bytes]:
        """Return the access units complete so far (each may hold several E-AC-3 frames)."""
        data = self._buffer + payload
        units: list[bytes] = []
        position = 0
        while position + 8 <= len(data):
            if data[position : position + 2] != SYNC:
                found = data.find(SYNC, position + 1)
                position = len(data) if found < 0 else found
                continue
            frame = parse_frame(data, position)
            if frame is None:
                break
            position += len(frame.data)
            if frame.enhanced and frame.stream_type == 1:
                self._current.append(frame)
                self._dependents = max(self._dependents, len(self._current) - 1)
                continue
            if self._current:
                units.append(b"".join(f.data for f in self._current))
            self._current = [frame]
            if self._first is None:
                self._first = frame
        self._buffer = data[position:]
        if self.config is None and self._first is not None and units:
            self._build(self._first, len(units[0]))
        return units

    def flush(self) -> list[bytes]:
        """The access unit still being collected at the end of the stream."""
        units = [b"".join(f.data for f in self._current)] if self._current else []
        if self.config is None and self._first is not None and units:
            self._build(self._first, len(units[0]))
        self._current = []
        self._buffer = b""
        return units

    def _build(self, frame: Ac3Frame, unit_size: int) -> None:
        self.enhanced = frame.enhanced
        self.sample_rate = frame.sample_rate
        self.samples_per_frame = frame.samples
        rate_kbps = kbps(unit_size, frame)
        if frame.enhanced:
            child = dec3(frame, self._dependents, rate_kbps)
            kind, name = b"ec-3", "ec-3"
        else:
            child = dac3(frame)
            kind, name = b"ac-3", "ac-3"
        entry = audio_sample_entry(kind, frame.channels, frame.sample_rate, child)
        self.config = TrackConfig(
            "audio", frame.sample_rate, entry, name,
            channels=frame.channels, sample_rate=frame.sample_rate,
        )  # fmt: skip
