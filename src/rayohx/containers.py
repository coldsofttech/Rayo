"""Recognising and checking segment formats.

Rayo joins MPEG-TS and fragmented MP4 segments by simple concatenation, which is only valid when
the pieces really are that format. These checks catch the common failures (an error page served as
media, a wrong decryption key, a cut-off file) before they turn into a corrupt output that looks
like a success.
"""

import logging
import struct
from pathlib import Path

from rayohx.enums import ContainerFormat
from rayohx.errors import FinalizationError

logger = logging.getLogger(__name__)

TS_PACKET = 188
TS_SYNC = 0x47
#: Top-level boxes a media segment of fragmented MP4 may start with.
_FMP4_SEGMENT_START = frozenset({b"styp", b"moof", b"sidx", b"emsg", b"prft", b"free", b"skip"})
_FMP4_INIT_START = frozenset({b"ftyp", b"moov", b"styp"})
_MAX_SAMPLES = 2000
_HEADER = struct.Struct(">I4s")
_LARGE = struct.Struct(">Q")


def sniff_container(data: bytes) -> ContainerFormat:
    """Guess the format from the first bytes of a segment (or of an initialization section)."""
    if data.lstrip(b"\xef\xbb\xbf").startswith(b"WEBVTT"):
        return ContainerFormat.WEBVTT
    if _looks_like_ts(data):
        return ContainerFormat.MPEG_TS
    if len(data) >= 8 and data[4:8] in (_FMP4_SEGMENT_START | _FMP4_INIT_START):
        return ContainerFormat.FMP4
    return ContainerFormat.UNKNOWN


def _looks_like_ts(data: bytes) -> bool:
    if not data or data[0] != TS_SYNC:
        return False
    # One packet is too little to be sure; with more data the next sync bytes must line up.
    return all(
        data[offset] == TS_SYNC
        for offset in range(TS_PACKET, min(len(data), 3 * TS_PACKET), TS_PACKET)
    )


def check_segment(container: ContainerFormat, data: bytes, *, is_init: bool = False) -> str | None:
    """Return what is wrong with a downloaded segment, or ``None`` if it looks right."""
    if container is ContainerFormat.MPEG_TS:
        if not _looks_like_ts(data):
            return "it is not MPEG-TS data"
    elif container is ContainerFormat.FMP4:
        allowed = _FMP4_INIT_START if is_init else _FMP4_SEGMENT_START
        if len(data) < 8 or data[4:8] not in allowed:
            return "it is not fragmented MP4 data"
    return None


# --- whole-file validation ---------------------------------------------------------------------


def validate_output(container: ContainerFormat, path: Path) -> None:
    """Check a finished output file.

    Raises:
        FinalizationError: If the file is not a valid file of its format.
    """
    if container is ContainerFormat.MPEG_TS:
        validate_ts_file(path)
    elif container is ContainerFormat.FMP4:
        validate_fmp4_file(path)


def validate_ts_file(path: Path) -> None:
    """Spot-check an MPEG-TS file: every sampled packet must start with the sync byte.

    Reading all of a multi-gigabyte file would cost as much as downloading it, so up to 2000
    evenly spread packets plus the first and last are checked.

    Raises:
        FinalizationError: If the file is empty or its packets do not line up.
    """
    size = path.stat().st_size
    if size < TS_PACKET:
        raise FinalizationError(
            f"The output is too small to be an MPEG-TS file ({size} bytes).",
            hint="The download may have returned errors instead of video.",
        )
    packets = size // TS_PACKET
    step = max(1, packets // _MAX_SAMPLES)
    positions = sorted({*range(0, packets, step), 0, packets - 1})
    bad = 0
    with path.open("rb") as handle:
        for index in positions:
            handle.seek(index * TS_PACKET)
            if handle.read(1) != bytes([TS_SYNC]):
                bad += 1
    if bad > len(positions) // 100:
        raise FinalizationError(
            f"The output is not a valid MPEG-TS file ({bad} of {len(positions)} checked packets "
            "are misaligned).",
            hint="Some segments may be damaged or not MPEG-TS.",
        )
    if size % TS_PACKET:
        logger.warning(
            "The MPEG-TS output ends with a partial packet (%d extra bytes).", size % TS_PACKET
        )


def validate_fmp4_file(path: Path) -> None:
    """Walk the top-level boxes of a fragmented MP4 file.

    Raises:
        FinalizationError: If a box runs past the end of the file, or the file lacks the
            initialization (``moov``) or any fragment (``moof`` and ``mdat``).
    """
    size = path.stat().st_size
    seen: dict[bytes, int] = {}
    first: bytes | None = None
    with path.open("rb") as handle:
        position = 0
        while position < size:
            handle.seek(position)
            header = handle.read(_HEADER.size)
            if len(header) < _HEADER.size:
                raise FinalizationError("The output ends in the middle of an MP4 box header.")
            length, kind = _HEADER.unpack(header)
            header_size = _HEADER.size
            if length == 1:
                large = handle.read(_LARGE.size)
                if len(large) < _LARGE.size:
                    raise FinalizationError("The output ends in the middle of an MP4 box header.")
                length = _LARGE.unpack(large)[0]
                header_size += _LARGE.size
            elif length == 0:
                length = size - position  # the box runs to the end of the file
            if length < header_size or position + length > size:
                raise FinalizationError(
                    f"The MP4 box {kind!r} at byte {position} runs past the end of the output.",
                    hint="The download may be cut short or contain a damaged segment.",
                )
            first = first or kind
            seen[kind] = seen.get(kind, 0) + 1
            position += length
    if first not in _FMP4_INIT_START:
        raise FinalizationError("The output does not start like an MP4 file.")
    for needed, name in (
        (b"moov", "initialization (moov)"),
        (b"moof", "fragment (moof)"),
        (b"mdat", "media data (mdat)"),
    ):
        if needed not in seen:
            raise FinalizationError(
                f"The MP4 output has no {name} box.",
                hint="The stream may be missing its initialization section or its media.",
            )
