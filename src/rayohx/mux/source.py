"""Feeding an MPEG-TS stream into the muxer: demux, parse each codec, cut fragments."""

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from rayohx.errors import UnsupportedCodecError
from rayohx.mux import ts as tsmod
from rayohx.mux.aac import AacParser, audio_samples
from rayohx.mux.ac3 import Ac3Parser
from rayohx.mux.fragmenter import TrackFragmenter
from rayohx.mux.h264 import H264Parser
from rayohx.mux.hevc import HevcParser
from rayohx.mux.model import Sample, TrackConfig
from rayohx.mux.mp3 import Mp3Parser
from rayohx.mux.muxer import Mp4Muxer
from rayohx.mux.ts import PesPacket, TsDemuxer, TsStream

logger = logging.getLogger(__name__)

#: Stream types that carry nothing we copy: sections, timed metadata and SCTE-35 markers.
_IGNORED = frozenset({0x05, 0x15, 0x86})
_UNSUPPORTED = {
    0x01: ("MPEG-1 video", "video"),
    0x02: ("MPEG-2 video", "video"),
    0x10: ("MPEG-4 video", "video"),
    0x11: ("AAC in LATM framing", "audio"),
    0x20: ("H.264 multiview video", "video"),
    0x21: ("JPEG 2000 video", "video"),
    0x27: ("HEVC enhancement video", "video"),
    0x42: ("AVS video", "video"),
    0x80: ("MPEG-2 video (DigiCipher)", "video"),
    0x82: ("DTS audio", "audio"),
    0x83: ("Dolby TrueHD audio", "audio"),
    0x84: ("Dolby audio (SDDS)", "audio"),
    0x85: ("DTS-HD audio", "audio"),
    0xEA: ("VC-1 video", "video"),
}
_AUDIO = frozenset({"aac", "mp3", "ac3", "eac3"})
#: A picture shown this many seconds from its decode time is a damaged timestamp, not a real one.
_MAX_REORDER = 60.0
#: Gaps in audio shorter than this many seconds are kept; longer jumps are timestamp resets.
_MAX_AUDIO_GAP = 5.0


class _AudioParser(Protocol):
    config: TrackConfig | None
    samples_per_frame: int

    def frames(self, payload: bytes) -> list[bytes]: ...

    def flush(self) -> list[bytes]: ...


def classify(stream: TsStream) -> str | None:
    """The codec name for a stream we copy, ``"ignore"`` for data we skip, or ``None``."""
    kind = stream.stream_type
    if kind == tsmod.STREAM_H264:
        return "h264"
    if kind == tsmod.STREAM_HEVC:
        return "hevc"
    if kind == tsmod.STREAM_AAC_ADTS:
        return "aac"
    if kind in {tsmod.STREAM_MP3, tsmod.STREAM_MP3_B}:
        return "mp3"
    if kind == tsmod.STREAM_AC3:
        return "ac3"
    if kind == tsmod.STREAM_EAC3:
        return "eac3"
    if kind == tsmod.STREAM_PRIVATE:
        if stream.has_descriptor(tsmod.DESCRIPTOR_EAC3):
            return "eac3"
        if stream.has_descriptor(tsmod.DESCRIPTOR_AC3):
            return "ac3"
        return "ignore"
    if kind in _IGNORED:
        return "ignore"
    return None


def describe_unsupported(stream: TsStream) -> str:
    """A name for a stream we cannot copy."""
    name, _ = _UNSUPPORTED.get(stream.stream_type, (f"stream type 0x{stream.stream_type:02x}", ""))
    return name


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """A stream found in a transport stream: what codec, audio or video, and if we can copy it."""

    codec: str
    kind: str
    supported: bool


def stream_info(stream: TsStream) -> StreamInfo | None:
    """What a stream is, or ``None`` for data that is skipped (metadata, markers)."""
    found = classify(stream)
    if found == "ignore":
        return None
    if found is None:
        name, kind = _UNSUPPORTED.get(stream.stream_type, (describe_unsupported(stream), "video"))
        return StreamInfo(name, kind, supported=False)
    return StreamInfo(found, "audio" if found in _AUDIO else "video", supported=True)


class TsSource:
    """Takes MPEG-TS data in any piece sizes and gives the muxer fragments.

    ``feed`` raises ``UnsupportedCodecError`` if the stream has audio or video that cannot be
    copied. ``on_codecs`` is told the codec names found, as soon as the program map is read.
    """

    def __init__(
        self,
        muxer: Mp4Muxer,
        *,
        on_codecs: Callable[[list[str]], None] | None = None,
        language: str | None = None,
        name: str | None = None,
    ) -> None:
        self._language = language
        self._name = name
        self._muxer = muxer
        self._on_codecs = on_codecs
        self._demuxer = TsDemuxer(self._pes, self._streams)
        self._parsers: dict[int, _StreamState] = {}
        self._fragmenters: list[TrackFragmenter] = []
        #: How many tracks the stream has, once its program map has been read.
        self.expected_tracks: int | None = None

    @property
    def track_ids(self) -> list[int]:
        """The muxer track ids created so far."""
        return [fragmenter.track_id for fragmenter in self._fragmenters]

    def feed(self, data: bytes) -> None:
        """Process more transport stream data."""
        self._demuxer.feed(data)

    def finish(self) -> None:
        """Flush everything and tell the muxer no more is coming."""
        self._demuxer.flush()
        for state in self._parsers.values():
            state.flush()
        for fragmenter in self._fragmenters:
            fragmenter.finish()
            self._muxer.end_track(fragmenter.track_id)

    def _streams(self, streams: list[TsStream]) -> None:
        wanted: list[tuple[TsStream, str]] = []
        for stream in streams:
            kind = classify(stream)
            if kind == "ignore":
                continue
            if kind is None:
                raise UnsupportedCodecError(describe_unsupported(stream))
            wanted.append((stream, kind))
        if self._on_codecs is not None:
            self._on_codecs([kind for _, kind in wanted])
        self.expected_tracks = len(wanted)
        self._muxer.expect(len(wanted))
        for stream, kind in wanted:
            self._parsers[stream.pid] = _StreamState(self, kind)

    def _pes(self, pes: PesPacket, stream: TsStream) -> None:
        state = self._parsers.get(pes.pid)
        if state is not None:
            state.feed(pes)

    def _new_track(self, config: TrackConfig) -> TrackFragmenter:
        if config.kind == "audio" and (self._language or self._name):
            config = dataclasses.replace(
                config,
                language=self._language or config.language,
                name=self._name or config.name,
            )
        track_id = self._muxer.add_track(config)
        fragmenter = TrackFragmenter(
            track_id,
            config,
            lambda fragment, start: self._muxer.add_fragment(track_id, fragment, start),
        )
        self._fragmenters.append(fragmenter)
        return fragmenter


class _StreamState:
    """One elementary stream: its parser, its fragmenter and its running timeline."""

    def __init__(self, source: TsSource, kind: str) -> None:
        self._source = source
        self._fragmenter: TrackFragmenter | None = None
        self._video: H264Parser | HevcParser | None = None
        self._audio: _AudioParser | None = None
        self._seen_sync = False
        self._next_dts: int | None = None
        if kind == "h264":
            self._video = H264Parser()
        elif kind == "hevc":
            self._video = HevcParser()
        elif kind == "aac":
            self._audio = AacParser()
        elif kind == "mp3":
            self._audio = Mp3Parser()
        else:
            self._audio = Ac3Parser()

    def feed(self, pes: PesPacket) -> None:
        if self._video is not None:
            self._feed_video(pes)
        elif self._audio is not None:
            frames = self._audio.frames(pes.payload)
            self._add_audio(frames, pes.pts)

    def flush(self) -> None:
        if self._audio is not None:
            self._add_audio(self._audio.flush(), None)

    # --- video -----------------------------------------------------------------------------

    def _feed_video(self, pes: PesPacket) -> None:
        assert self._video is not None  # noqa: S101 - chosen in __init__
        for index, unit in enumerate(self._video.access_units(pes.payload)):
            if unit is None or pes.dts is None:
                continue
            data, sync = unit
            if not self._seen_sync and not sync:
                continue  # cannot be decoded without the keyframe before it
            self._seen_sync = True
            pts = pes.pts if pes.pts is not None else pes.dts
            if abs(pts - pes.dts) > _MAX_REORDER * 90000:
                pts = pes.dts  # damaged timestamps must not break the file
            sample = Sample(
                data, dts=pes.dts + index, duration=0, cts_offset=pts - pes.dts, sync=sync
            )
            fragmenter = self._fragmenter_for(self._video.config)
            if fragmenter is not None:
                fragmenter.add(sample)

    # --- audio -----------------------------------------------------------------------------

    def _add_audio(self, frames: list[bytes], pts: int | None) -> None:
        assert self._audio is not None  # noqa: S101 - chosen in __init__
        config = self._audio.config
        if not frames or config is None:
            return
        rate = config.timescale
        if pts is not None:
            anchor = round(pts * rate / 90000)
            if self._next_dts is None:
                self._next_dts = anchor
            elif rate // 4 < anchor - self._next_dts < rate * _MAX_AUDIO_GAP:
                self._next_dts = anchor  # a real gap in the audio: keep it, to stay in sync
        if self._next_dts is None:
            return
        fragmenter = self._fragmenter_for(config)
        if fragmenter is None:
            return
        per_frame = self._audio.samples_per_frame
        for sample in audio_samples(frames, self._next_dts, per_frame):
            fragmenter.add(sample)
        self._next_dts += len(frames) * per_frame

    def _fragmenter_for(self, config: TrackConfig | None) -> TrackFragmenter | None:
        if self._fragmenter is None and config is not None:
            self._fragmenter = self._source._new_track(config)
        return self._fragmenter


def probe_streams(data: bytes) -> list[StreamInfo]:
    """The streams in a piece of MPEG-TS data, in program-map order, without muxing anything."""
    found: list[StreamInfo] = []

    def streams(listed: list[TsStream]) -> None:
        for stream in listed:
            info = stream_info(stream)
            if info is not None:
                found.append(info)

    TsDemuxer(lambda _pes, _stream: None, streams).feed(data)
    return found


def probe_codecs(data: bytes) -> list[str]:
    """The codec names in a piece of MPEG-TS data.

    Raises:
        UnsupportedCodecError: If the stream has audio or video that cannot be copied.
    """
    names = []
    for info in probe_streams(data):
        if not info.supported:
            raise UnsupportedCodecError(info.codec)
        names.append(info.codec)
    return names
