"""Writing the pieces of a fragmented MP4 file: the initialization section and the fragments."""

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from rayohx.mux.boxes import box, full_box, u16, u32, u64
from rayohx.mux.model import Sample, TrackConfig

MOVIE_TIMESCALE = 1000
SYNC_FLAGS = 0x02000000
NON_SYNC_FLAGS = 0x01010000
_MATRIX = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
_LANGUAGES = {
    "ar": "ara", "cs": "ces", "da": "dan", "de": "deu", "el": "ell", "en": "eng", "es": "spa",
    "fi": "fin", "fr": "fra", "he": "heb", "hi": "hin", "hu": "hun", "id": "ind", "it": "ita",
    "ja": "jpn", "ko": "kor", "nl": "nld", "no": "nor", "pl": "pol", "pt": "por", "ro": "ron",
    "ru": "rus", "sv": "swe", "th": "tha", "tr": "tur", "uk": "ukr", "vi": "vie", "zh": "zho",
}  # fmt: skip


def language_code(language: str | None) -> str:
    """The three-letter code MP4 files use, from a two- or three-letter language tag."""
    if not language:
        return "und"
    main = language.lower().split("-")[0].split("_")[0]
    if len(main) == 3 and main.isalpha():
        return main
    return _LANGUAGES.get(main, "und")


def ftyp() -> bytes:
    """The file type box."""
    return box(b"ftyp", b"iso5", u32(0), b"iso5", b"iso6", b"mp41")


@dataclass(frozen=True, slots=True)
class RawTrack:
    """A track copied from another fragmented MP4: its ``trak`` and ``trex`` boxes, patched."""

    trak: bytes
    trex: bytes
    #: ``"video"`` or ``"audio"``, used to put video first.
    kind: str = "audio"


def build_trak(track_id: int, config: TrackConfig, *, enabled: bool = True) -> bytes:
    """A ``trak`` box with an empty sample table (all samples live in the fragments)."""
    video = config.kind == "video"
    flags = 0x3 if enabled else 0x2
    tkhd = full_box(
        b"tkhd", 0, flags,
        u32(0), u32(0), u32(track_id), u32(0), u32(0),
        bytes(8), u16(0), u16(0 if video else 1), u16(0 if video else 0x0100), u16(0),
        _MATRIX, u32(config.width << 16), u32(config.height << 16),
    )  # fmt: skip
    letters = [ord(char) - 0x60 for char in language_code(config.language)]
    packed = (letters[0] << 10) | (letters[1] << 5) | letters[2]
    mdhd = full_box(
        b"mdhd", 0, 0, u32(0), u32(0), u32(config.timescale), u32(0), u16(packed), u16(0)
    )
    handler, label = (b"vide", b"VideoHandler") if video else (b"soun", b"SoundHandler")
    name = config.name.encode("utf-8", "replace") if config.name else label
    hdlr = full_box(b"hdlr", 0, 0, u32(0), handler, bytes(12), name + b"\x00")
    media_header = full_box(b"vmhd", 0, 1, bytes(8)) if video else full_box(b"smhd", 0, 0, bytes(4))
    dinf = box(b"dinf", full_box(b"dref", 0, 0, u32(1), full_box(b"url ", 0, 1)))
    empty = u32(0)
    stbl = box(
        b"stbl",
        full_box(b"stsd", 0, 0, u32(1), config.sample_entry),
        full_box(b"stts", 0, 0, empty),
        full_box(b"stsc", 0, 0, empty),
        full_box(b"stsz", 0, 0, u32(0), empty),
        full_box(b"stco", 0, 0, empty),
    )
    minf = box(b"minf", media_header, dinf, stbl)
    return box(b"trak", tkhd, box(b"mdia", mdhd, hdlr, minf))


def build_trex(track_id: int) -> bytes:
    """Default sample settings for a track (all overridden in each fragment)."""
    return full_box(b"trex", 0, 0, u32(track_id), u32(1), u32(0), u32(0), u32(0))


def build_init_ordered(tracks: Sequence[object]) -> bytes:
    """The ``ftyp`` and ``moov`` boxes for tracks (each with ``track_id`` and either a
    ``config`` or a ``raw`` track), in the order given."""
    traks, trexes = [], []
    for track in tracks:
        config = getattr(track, "config", None)
        raw = getattr(track, "raw", None)
        track_id = track.track_id  # type: ignore[attr-defined]
        if config is not None:
            traks.append(build_trak(track_id, config))
            trexes.append(build_trex(track_id))
        elif raw is not None:
            traks.append(raw.trak)
            trexes.append(raw.trex)
    mvhd = full_box(
        b"mvhd", 0, 0,
        u32(0), u32(0), u32(MOVIE_TIMESCALE), u32(0), u32(0x10000), u16(0x0100), bytes(10),
        _MATRIX, bytes(24), u32(len(traks) + 1),
    )  # fmt: skip
    return ftyp() + box(b"moov", mvhd, *traks, box(b"mvex", *trexes))


def build_fragment(track_id: int, samples: Sequence[Sample], base_decode_time: int) -> bytes:
    """A ``moof`` box and its ``mdat`` box for consecutive samples of one track."""
    flags = 0x000001 | 0x000100 | 0x000200 | 0x000400 | 0x000800
    entries = b"".join(
        struct.pack(
            ">IIIi",
            sample.duration,
            len(sample.data),
            SYNC_FLAGS if sample.sync else NON_SYNC_FLAGS,
            sample.cts_offset,
        )
        for sample in samples
    )

    def moof(data_offset: int) -> bytes:
        trun = full_box(
            b"trun", 1, flags, u32(len(samples)), struct.pack(">i", data_offset), entries
        )
        traf = box(
            b"traf",
            full_box(b"tfhd", 0, 0x020000, u32(track_id)),
            full_box(b"tfdt", 1, 0, u64(base_decode_time)),
            trun,
        )
        return box(b"moof", full_box(b"mfhd", 0, 0, u32(0)), traf)

    size = len(moof(0))
    payload = b"".join(sample.data for sample in samples)
    return moof(size + 8) + struct.pack(">I4s", 8 + len(payload), b"mdat") + payload
