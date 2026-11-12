import struct

import pytest

from rayohx import OutputFormatError
from rayohx.mux import aac, ac3, mp3
from rayohx.mux.mp4read import find_box
from rayohx.mux.ts import TsDemuxer
from tests.core.mux.helpers import media


def adts(
    payload: bytes, *, rate_index: int = 4, channels: int = 2, profile: int = 2, crc: bool = False
) -> bytes:
    header_size = 9 if crc else 7
    length = header_size + len(payload)
    header = bytes(
        [
            0xFF,
            0xF0 | (0 if crc else 1),
            ((profile - 1) << 6) | (rate_index << 2) | (channels >> 2),
            ((channels & 3) << 6) | (length >> 11),
            (length >> 3) & 0xFF,
            ((length & 7) << 5) | 0x1F,
            0xFC,
        ]
    )
    return header + (b"\x00\x00" if crc else b"") + payload


def audio_payloads(name: str, stream_type: int) -> list[bytes]:
    payloads: list[bytes] = []
    demuxer = TsDemuxer(
        lambda p, s: payloads.append(p.payload) if s.stream_type == stream_type else None
    )
    demuxer.feed(media(name))
    demuxer.flush()
    return payloads


# --- AAC ---------------------------------------------------------------------------------------


def test_adts_frames_are_split_and_the_config_is_built() -> None:
    parser = aac.AacParser()
    frames = parser.frames(adts(b"A" * 10) + adts(b"B" * 20))
    assert frames == [b"A" * 10, b"B" * 20]
    config = parser.config
    assert config is not None
    assert (config.timescale, config.channels, config.sample_rate) == (44100, 2, 44100)
    assert config.codec == "mp4a.40.2"
    esds = find_box(config.sample_entry, (b"esds",), 36)
    assert esds is not None
    body = config.sample_entry[esds[0] : esds[0] + esds[1]]
    assert struct.pack(">H", (2 << 11) | (4 << 7) | (2 << 3)) in body  # the AudioSpecificConfig
    assert bytes([0x40, 0x15]) in body  # MPEG-4 audio


def test_frames_cut_across_payloads_are_put_back_together() -> None:
    stream = adts(b"A" * 100) + adts(b"B" * 100) + adts(b"C" * 100)
    parser = aac.AacParser()
    found = []
    for start in range(0, len(stream), 37):
        found += parser.frames(stream[start : start + 37])
    assert found == [b"A" * 100, b"B" * 100, b"C" * 100]


def test_frames_with_a_crc_and_junk_between_them() -> None:
    parser = aac.AacParser()
    frames = parser.frames(b"\x00\x01" + adts(b"A" * 8, crc=True) + b"junk" + adts(b"B" * 8))
    assert frames == [b"A" * 8, b"B" * 8]


@pytest.mark.parametrize(
    ("rate_index", "rate"), [(0, 96000), (3, 48000), (4, 44100), (8, 16000), (11, 8000)]
)
def test_all_common_sampling_rates(rate_index: int, rate: int) -> None:
    parser = aac.AacParser()
    parser.frames(adts(b"x" * 5, rate_index=rate_index))
    assert parser.config is not None
    assert parser.config.sample_rate == rate


def test_unusual_aac_is_refused() -> None:
    with pytest.raises(OutputFormatError, match="sampling rate"):
        aac.AacParser().frames(adts(b"x" * 5, rate_index=14))
    with pytest.raises(OutputFormatError, match="channel layout"):
        aac.AacParser().frames(adts(b"x" * 5, channels=0))


def test_aac_samples_follow_each_other() -> None:
    samples = aac.audio_samples([b"a", b"b", b"c"], 1000, 1024)
    assert [(s.dts, s.duration) for s in samples] == [(1000, 1024), (2024, 1024), (3048, 1024)]


def test_the_real_aac_stream_parses_completely() -> None:
    parser = aac.AacParser()
    frames = [
        frame for payload in audio_payloads("aac_only.ts", 0x0F) for frame in parser.frames(payload)
    ]
    assert len(frames) == 174
    assert parser.flush() == []


# --- Dolby -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind", "enhanced"), [("h264_ac3.ts", 0x81, False), ("h264_eac3.ts", 0x87, True)]
)
def test_real_dolby_streams_parse_frame_after_frame(name: str, kind: int, enhanced: bool) -> None:
    parser = ac3.Ac3Parser()
    units = []
    for payload in audio_payloads(name, kind):
        units += parser.frames(payload)
    units += parser.flush()
    assert len(units) == 115
    assert all(unit[:2] == ac3.SYNC for unit in units)
    assert parser.config is not None
    assert parser.enhanced is enhanced
    assert parser.sample_rate == 44100
    assert parser.samples_per_frame == 1536
    assert find_box(parser.config.sample_entry, (b"dec3" if enhanced else b"dac3",), 36) is not None


def test_dac3_describes_the_stream() -> None:
    parser = ac3.Ac3Parser()
    for payload in audio_payloads("h264_ac3.ts", 0x81):
        parser.frames(payload)
    assert parser.config is not None
    found = find_box(parser.config.sample_entry, (b"dac3",), 36)
    assert found is not None
    bits = int.from_bytes(parser.config.sample_entry[found[0] + 8 : found[0] + 11], "big")
    assert bits >> 22 == 1  # 44.1 kHz
    assert (bits >> 5) & 0x1F == 6  # the code for 96 kbit/s


def test_dolby_frames_cut_across_payloads() -> None:
    data = b"".join(audio_payloads("h264_ac3.ts", 0x81))
    parser = ac3.Ac3Parser()
    units = []
    for start in range(0, len(data), 53):
        units += parser.frames(data[start : start + 53])
    units += parser.flush()
    assert len(units) == 115


def test_a_dolby_frame_with_an_unknown_version_is_refused() -> None:
    data = ac3.SYNC + bytes(2) + b"\x00" + bytes([0x1F << 3]) + bytes(20)
    with pytest.raises(OutputFormatError, match="bit stream version"):
        ac3.parse_frame(data, 0)


def test_incomplete_dolby_frames_wait_for_more_data() -> None:
    data = b"".join(audio_payloads("h264_ac3.ts", 0x81))
    assert ac3.parse_frame(data[:10], 0) is None
    first = ac3.parse_frame(data, 0)
    assert first is not None
    assert ac3.parse_frame(data[: len(first.data) - 1], 0) is None


# --- MP3 ---------------------------------------------------------------------------------------


def mpeg_frame(header: int, size: int) -> bytes:
    return header.to_bytes(4, "big") + bytes(size - 4)


def test_mp3_frames_are_found_by_their_headers() -> None:
    header = 0xFFFB9004  # MPEG-1 layer 3, 128 kbit/s, 44.1 kHz, stereo
    parser = mp3.Mp3Parser()
    frames = parser.frames(mpeg_frame(header, 417) + mpeg_frame(header | 0x200, 418) + b"\x00\x00")
    assert [len(frame) for frame in frames] == [417, 418]
    assert parser.config is not None
    assert (parser.config.sample_rate, parser.config.channels) == (44100, 2)
    assert parser.samples_per_frame == 1152
    esds = find_box(parser.config.sample_entry, (b"esds",), 36)
    assert esds is not None
    assert bytes([0x6B, 0x15]) in parser.config.sample_entry


def test_mpeg2_layer3_has_shorter_frames() -> None:
    header = 0xFFF39004 & ~0xF000 | 0x8000  # MPEG-2 layer 3, 64 kbit/s at 22.05 kHz
    parser = mp3.Mp3Parser()
    expected = 72 * 64000 // 22050
    frames = parser.frames(mpeg_frame(header, expected) + mpeg_frame(header, expected))
    assert len(frames) == 2
    assert parser.samples_per_frame == 576
    assert parser.config is not None
    assert bytes([0x69, 0x15]) in parser.config.sample_entry


def test_a_real_mp3_stream_parses_completely() -> None:
    parser = mp3.Mp3Parser()
    frames = [
        frame for payload in audio_payloads("h264_mp3.ts", 0x03) for frame in parser.frames(payload)
    ]
    assert len(frames) == 155
    assert parser.flush() == []


def test_garbage_is_skipped_over() -> None:
    parser = mp3.Mp3Parser()
    header = 0xFFFB9004
    assert len(parser.frames(b"\x12\x34\x56" + mpeg_frame(header, 417))) == 1
