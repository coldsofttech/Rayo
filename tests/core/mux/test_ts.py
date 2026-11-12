import itertools

import pytest

from rayohx.errors import UnsupportedCodecError
from rayohx.mux import ts
from rayohx.mux.source import classify, probe_codecs
from rayohx.mux.ts import PesPacket, TsDemuxer, TsStream
from tests.core.mux.helpers import HAS_FFPROBE, media, needs_ffmpeg, pat, pes, pmt, ts_packets


def demux(data: bytes, chunk: int = 7777) -> tuple[list[TsStream], list[tuple[int, PesPacket]]]:
    streams: list[TsStream] = []
    packets: list[tuple[int, PesPacket]] = []
    demuxer = TsDemuxer(
        lambda packet, stream: packets.append((stream.stream_type, packet)),
        lambda found: streams.extend(found),
    )
    for start in range(0, len(data), chunk):
        demuxer.feed(data[start : start + chunk])
    demuxer.flush()
    return streams, packets


# --- real files --------------------------------------------------------------------------------


def test_the_streams_of_a_real_file_are_found() -> None:
    streams, packets = demux(media("h264_aac.ts"))
    assert [(s.stream_type, s.pid) for s in streams] == [
        (ts.STREAM_H264, streams[0].pid),
        (ts.STREAM_AAC_ADTS, streams[1].pid),
    ]
    video = [p for kind, p in packets if kind == ts.STREAM_H264]
    assert len(video) == 100
    stamps = [p.dts for p in video if p.dts is not None]
    deltas = {later - earlier for earlier, later in itertools.pairwise(stamps)}
    assert deltas == {3600}  # 25 frames per second at 90 kHz
    assert all(p.pts is not None and p.dts is not None and p.pts >= p.dts for p in video)
    assert video[0].pts - video[0].dts == 7200  # type: ignore[operator]  # two B-frames of delay


@pytest.mark.parametrize("chunk", [1, 7, 100, 187, 188, 189, 1000, 100_000])
def test_the_result_does_not_depend_on_how_the_data_is_cut(chunk: int) -> None:
    data = media("h264_aac.ts")[: 188 * 600]
    reference = demux(data, chunk=len(data))[1]
    cut = demux(data, chunk=chunk)[1]
    assert [(k, p.pid, p.pts, p.dts, len(p.payload)) for k, p in cut] == [
        (k, p.pid, p.pts, p.dts, len(p.payload)) for k, p in reference
    ]


def test_junk_before_and_between_packets_is_skipped() -> None:
    data = media("h264_aac.ts")[: 188 * 400]
    clean = demux(data)[1]
    dirty = demux(b"\x01\x02\x03garbage" + data[: 188 * 100] + b"\xde\xad\xbe" + data[188 * 100 :])[
        1
    ]
    assert len(dirty) >= len(clean) - 2  # at most the packet next to the junk is lost


def test_a_file_that_ends_in_a_partial_packet_is_fine() -> None:
    data = media("h264_aac.ts")
    full = demux(data)[1]
    truncated = demux(data[:-100])[1]
    assert len(truncated) >= len(full) - 1


@needs_ffmpeg
@pytest.mark.parametrize("name", ["h264_aac", "hevc_aac", "h264_ac3", "h264_eac3", "h264_mp3"])
def test_video_timestamps_match_ffprobe(name: str) -> None:
    import json
    import subprocess

    from tests.core.mux.helpers import MEDIA

    output = subprocess.run(
        ["ffprobe", "-v", "error", "-show_packets", "-select_streams", "v",  # noqa: S607
         "-show_entries", "packet=pts,dts", "-of", "json", str(MEDIA / f"{name}.ts")],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    expected = [(int(p["pts"]), int(p["dts"])) for p in json.loads(output)["packets"]]
    _, packets = demux(media(f"{name}.ts"))
    found = [(p.pts, p.dts) for kind, p in packets if kind in (ts.STREAM_H264, ts.STREAM_HEVC)]
    assert found == expected
    assert HAS_FFPROBE


# --- built by hand -----------------------------------------------------------------------------


def stream(kind: int, pid: int, descriptors: bytes = b"") -> tuple[int, int, bytes]:
    return (kind, pid, descriptors)


def build(pid: int, payloads: list[tuple[int | None, int | None, bytes]]) -> bytes:
    """A TS with one stream of the given type: each payload as its own PES packet."""
    data = pat() + pmt([stream(ts.STREAM_H264, pid)])
    counter = 0
    for pts, dts, payload in payloads:
        packets = ts_packets(pid, pes(0xE0, payload, pts, dts), counter=counter)
        counter += len(packets) // 188
        data += packets
    return data


def test_timestamps_are_read_with_and_without_a_decode_time() -> None:
    _, packets = demux(
        build(0x101, [(9000, 6000, b"A" * 50), (12000, None, b"B" * 400), (None, None, b"C")])
    )
    got = [(p.pts, p.dts, len(p.payload)) for _, p in packets]
    assert got == [(9000, 6000, 50), (12000, 12000, 400), (None, None, 1)]


def test_timestamps_wrap_around_without_jumping_back() -> None:
    top = (1 << 33) - 3000
    _, packets = demux(
        build(0x101, [(top, top, b"a"), (top + 3000 - (1 << 33), top + 3000 - (1 << 33), b"b")])
    )
    first, second = (p for _, p in packets)
    assert second.pts - first.pts == 3000  # type: ignore[operator]


def test_a_large_payload_spans_many_packets() -> None:
    payload = bytes(range(256)) * 40
    _, packets = demux(build(0x101, [(1, 1, payload)]))
    assert packets[0][1].payload == payload


def test_a_new_program_map_replaces_the_old_one() -> None:
    first = pat() + pmt([stream(ts.STREAM_H264, 0x101)])
    second = pmt([stream(ts.STREAM_H264, 0x101), stream(ts.STREAM_AAC_ADTS, 0x102)])
    seen: list[int] = []
    demuxer = TsDemuxer(lambda p, s: None, lambda found: seen.append(len(found)))
    demuxer.feed(first)
    demuxer.feed(first)  # the same table again changes nothing
    demuxer.feed(second)
    assert seen == [1, 2]
    assert [s.pid for s in demuxer.streams] == [0x101, 0x102]


def test_data_on_unlisted_pids_is_ignored() -> None:
    data = pat() + pmt([stream(ts.STREAM_H264, 0x101)]) + ts_packets(0x200, pes(0xE0, b"x", 1, 1))
    assert demux(data)[1] == []


def test_scrambled_packets_are_counted_and_skipped() -> None:
    clear = ts_packets(0x101, pes(0xE0, b"payload", 1, 1))
    scrambled = bytearray(clear)
    scrambled[3] |= 0x80
    demuxer = TsDemuxer(lambda p, s: None)
    demuxer.feed(pat() + pmt([stream(ts.STREAM_H264, 0x101)]) + bytes(scrambled))
    assert demuxer.scrambled_packets == 1


def test_private_streams_carrying_dolby_audio_are_recognised() -> None:
    streams, _ = demux(
        pat()
        + pmt(
            [
                stream(ts.STREAM_PRIVATE, 0x101, bytes([ts.DESCRIPTOR_AC3, 1, 0])),
                stream(ts.STREAM_PRIVATE, 0x102, bytes([ts.DESCRIPTOR_EAC3, 1, 0])),
                stream(ts.STREAM_PRIVATE, 0x103, bytes([0x59, 0])),
                stream(0x15, 0x104),
            ]
        )
    )
    assert [classify(s) for s in streams] == ["ac3", "eac3", "ignore", "ignore"]
    assert streams[0].has_descriptor(ts.DESCRIPTOR_AC3)
    assert not streams[0].has_descriptor(ts.DESCRIPTOR_EAC3)


@pytest.mark.parametrize(
    ("kind", "name"),
    [(0x02, "MPEG-2 video"), (0x82, "DTS audio"), (0xEA, "VC-1 video"), (0x99, "stream type 0x99")],
)
def test_codecs_that_cannot_be_copied_are_named(kind: int, name: str) -> None:
    data = pat() + pmt([stream(kind, 0x101)])
    with pytest.raises(UnsupportedCodecError, match=name) as info:
        probe_codecs(data)
    assert info.value.codec == name
    assert info.value.hint


def test_probing_lists_the_codecs() -> None:
    assert probe_codecs(media("h264_aac.ts")) == ["h264", "aac"]
    assert probe_codecs(media("hevc_aac.ts")) == ["hevc", "aac"]
    assert probe_codecs(media("h264_eac3.ts")) == ["h264", "eac3"]
    assert probe_codecs(media("aac_only.ts")) == ["aac"]
