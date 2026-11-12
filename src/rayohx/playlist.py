"""HLS playlist parsing (RFC 8216).

The parser reads a playlist line by line. It recognises a playlist by its content, not by its
file name or address, resolves every address against the playlist's own address with RFC 3986
rules, and reports problems with the line number. Tags it does not know are skipped, so newer
playlists keep working. Features that would give wrong results if ignored (variable substitution,
I-frame-only playlists) are refused with ``UnsupportedPlaylistError``.
"""

import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from rayohx.enums import ContainerFormat, EncryptionMethod
from rayohx.errors import InvalidPlaylistError, UnsupportedPlaylistError
from rayohx.models import (
    AudioTrack,
    EncryptionInfo,
    InitMap,
    MasterPlaylist,
    MediaPlaylist,
    Playlist,
    Resolution,
    Segment,
    SegmentRange,
    SubtitleTrack,
    Variant,
    VideoTrack,
)

HEADER = "#EXTM3U"

_ATTRIBUTE_NAME = re.compile(r"^[A-Z0-9-]+$")
_EXTINF = re.compile(r"^(\d+(?:\.\d+)?|\.\d+)\s*(?:,(.*))?$")
_LINE_BREAK = re.compile(r"\r\n|\n|\r")

_UNSUPPORTED_TAGS = {
    "EXT-X-DEFINE": "playlist variables (EXT-X-DEFINE)",
    "EXT-X-I-FRAMES-ONLY": "I-frame-only playlists",
}
_MEDIA_TAGS = frozenset({"EXTINF", "EXT-X-TARGETDURATION"})
_MASTER_TAGS = frozenset({"EXT-X-STREAM-INF", "EXT-X-MEDIA"})

_FMP4_SUFFIXES = (".m4s", ".mp4", ".m4v", ".m4a", ".cmfv", ".cmfa", ".cmft")
_TS_SUFFIXES = (".ts", ".tsv", ".tsa")
_VTT_SUFFIXES = (".vtt", ".webvtt")

_Line = tuple[int, str]


def is_hls_playlist(text: str) -> bool:
    """Whether ``text`` starts like an HLS playlist, whatever it was called or fetched from."""
    return text.lstrip("﻿").lstrip().startswith(HEADER)


def parse_attribute_list(text: str, *, line: int | None = None) -> dict[str, str]:
    """Split ``KEY=value,KEY="quoted, value"`` into a dictionary, in any order.

    Quotes are removed from quoted values.

    Raises:
        InvalidPlaylistError: If the list is malformed.
    """
    attributes: dict[str, str] = {}
    length = len(text)
    position = 0
    while position < length:
        while position < length and text[position] in " \t":
            position += 1
        if position >= length:
            break
        equals = text.find("=", position)
        if equals == -1:
            raise InvalidPlaylistError(
                f"Attribute without a value: {text[position:]!r}.", line=line
            )
        name = text[position:equals].strip()
        if not _ATTRIBUTE_NAME.match(name):
            raise InvalidPlaylistError(f"Invalid attribute name {name!r}.", line=line)
        position = equals + 1
        if position < length and text[position] == '"':
            end = text.find('"', position + 1)
            if end == -1:
                raise InvalidPlaylistError(f"Unterminated quoted value for {name}.", line=line)
            value = text[position + 1 : end]
            position = end + 1
        else:
            end = text.find(",", position)
            end = length if end == -1 else end
            value = text[position:end].strip()
            position = end
        attributes[name] = value
        while position < length and text[position] in " \t":
            position += 1
        if position < length:
            if text[position] != ",":
                raise InvalidPlaylistError(f"Expected a comma after {name}.", line=line)
            position += 1
    return attributes


def parse_playlist(text: str, uri: str) -> Playlist:
    """Parse a master or media playlist fetched from ``uri``.

    Every address in the result is absolute.

    Raises:
        InvalidPlaylistError: If the text is not a valid playlist.
        UnsupportedPlaylistError: If it is valid but uses something the engine cannot handle.
    """
    lines = _lines(text)
    if not lines or lines[0][1] != HEADER:
        raise InvalidPlaylistError(
            "This is not an HLS playlist: it does not start with #EXTM3U.",
            hint="Check that the address points to an .m3u8 playlist and not to a web page.",
        )
    tags = {_tag_name(content) for _, content in lines if content.startswith("#EXT")}
    for tag, description in _UNSUPPORTED_TAGS.items():
        if tag in tags:
            raise UnsupportedPlaylistError(
                f"This playlist uses {description}, which is not supported."
            )
    is_media = bool(tags & _MEDIA_TAGS)
    if is_media and "EXT-X-STREAM-INF" in tags:
        raise InvalidPlaylistError("The playlist mixes master and media playlist tags.")
    if is_media:
        return _parse_media(lines, uri)
    if tags & _MASTER_TAGS:
        return _parse_master(lines, uri)
    raise InvalidPlaylistError(
        "The playlist has neither streams nor segments.",
        hint="A playlist needs #EXT-X-STREAM-INF or #EXTINF entries.",
    )


def _lines(text: str) -> list[_Line]:
    """Numbered, non-blank lines. Only real line breaks split text, never other Unicode ones."""
    numbered = []
    for number, raw in enumerate(_LINE_BREAK.split(text.lstrip("﻿")), start=1):
        content = raw.strip()
        if content:
            numbered.append((number, content))
    return numbered


def _tag_name(content: str) -> str:
    return content[1:].partition(":")[0]


def _tag_value(content: str) -> str:
    return content.partition(":")[2]


def _is_comment(content: str) -> bool:
    return content.startswith("#") and not content.startswith("#EXT")


def _resolve(base: str, reference: str, line: int, *, web_only: bool = True) -> str:
    """Resolve ``reference`` against ``base``.

    Media and playlist addresses must be web addresses. A key address may use another scheme
    (such as ``skd://``); it is kept so the playlist can still be inspected.
    """
    if any(ord(char) < 32 or char == "\x7f" for char in reference):
        raise InvalidPlaylistError("An address contains control characters.", line=line)
    absolute = urljoin(base, reference)
    if web_only and urlsplit(absolute).scheme.lower() not in {"http", "https"}:
        raise UnsupportedPlaylistError(
            f"Only http and https addresses are supported, found {reference!r} (line {line})."
        )
    return absolute


def _integer(
    attributes: dict[str, str], name: str, line: int, *, required: bool = False
) -> int | None:
    value = attributes.get(name)
    if value is None:
        if required:
            raise InvalidPlaylistError(f"Missing required attribute {name}.", line=line)
        return None
    if not value.isdigit():
        raise InvalidPlaylistError(f"{name} must be a whole number, found {value!r}.", line=line)
    return int(value)


def _decimal(attributes: dict[str, str], name: str, line: int) -> float | None:
    value = attributes.get(name)
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        raise InvalidPlaylistError(
            f"{name} must be a number, found {value!r}.", line=line
        ) from None
    if number < 0 or number != number or number == float("inf"):
        raise InvalidPlaylistError(f"{name} must be a finite positive number.", line=line)
    return number


def _whole_tag_number(content: str, line: int) -> int:
    value = _tag_value(content).strip()
    if not value.isdigit():
        raise InvalidPlaylistError(f"{_tag_name(content)} needs a whole number.", line=line)
    return int(value)


def _split_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _resolution(attributes: dict[str, str], line: int) -> Resolution | None:
    value = attributes.get("RESOLUTION")
    if value is None:
        return None
    try:
        return Resolution.parse(value)
    except InvalidPlaylistError as exc:
        raise InvalidPlaylistError(exc.message, line=line) from None


def _byte_range(value: str, line: int) -> SegmentRange:
    try:
        return SegmentRange.parse(value)
    except InvalidPlaylistError as exc:
        raise InvalidPlaylistError(exc.message, line=line) from None


# --- master playlists --------------------------------------------------------------------------


def _parse_master(lines: list[_Line], base: str) -> MasterPlaylist:
    variants: list[Variant] = []
    video: list[VideoTrack] = []
    audio: list[AudioTrack] = []
    subtitles: list[SubtitleTrack] = []
    version: int | None = None

    index = 1
    while index < len(lines):
        number, content = lines[index]
        index += 1
        name = _tag_name(content) if content.startswith("#EXT") else ""
        if name == "EXT-X-VERSION":
            version = _whole_tag_number(content, number)
        elif name == "EXT-X-MEDIA":
            _read_media_tag(content, number, base, video, audio, subtitles)
        elif name == "EXT-X-STREAM-INF":
            # Comments may sit between the tag and the address; another tag may not.
            while index < len(lines) and _is_comment(lines[index][1]):
                index += 1
            if index >= len(lines) or lines[index][1].startswith("#"):
                raise InvalidPlaylistError(
                    "EXT-X-STREAM-INF must be followed by the stream's address.", line=number
                )
            uri_number, uri = lines[index]
            index += 1
            attributes = parse_attribute_list(_tag_value(content), line=number)
            variants.append(_variant(attributes, _resolve(base, uri, uri_number), number))
    return MasterPlaylist(
        uri=base,
        variants=tuple(variants),
        video_tracks=tuple(video),
        audio_tracks=tuple(audio),
        subtitle_tracks=tuple(subtitles),
        version=version,
    )


def _variant(attributes: dict[str, str], uri: str, line: int) -> Variant:
    bandwidth = _integer(attributes, "BANDWIDTH", line, required=True)
    assert bandwidth is not None  # noqa: S101 - required=True guarantees a value
    return Variant(
        uri=uri,
        bandwidth=bandwidth,
        average_bandwidth=_integer(attributes, "AVERAGE-BANDWIDTH", line),
        resolution=_resolution(attributes, line),
        codecs=_split_list(attributes.get("CODECS")),
        frame_rate=_decimal(attributes, "FRAME-RATE", line),
        video_range=attributes.get("VIDEO-RANGE"),
        video_group=attributes.get("VIDEO"),
        audio_group=attributes.get("AUDIO"),
        subtitle_group=attributes.get("SUBTITLES"),
        name=attributes.get("NAME"),
    )


def _read_media_tag(
    content: str,
    line: int,
    base: str,
    video: list[VideoTrack],
    audio: list[AudioTrack],
    subtitles: list[SubtitleTrack],
) -> None:
    attributes = parse_attribute_list(_tag_value(content), line=line)
    kind = attributes.get("TYPE")
    if kind is None:
        raise InvalidPlaylistError("EXT-X-MEDIA needs a TYPE.", line=line)
    if kind == "CLOSED-CAPTIONS":
        return  # carried inside the video, nothing to download
    group_id = attributes.get("GROUP-ID")
    name = attributes.get("NAME")
    if not group_id or name is None:
        raise InvalidPlaylistError("EXT-X-MEDIA needs GROUP-ID and NAME.", line=line)
    raw_uri = attributes.get("URI")
    uri = _resolve(base, raw_uri, line) if raw_uri else None
    default = attributes.get("DEFAULT") == "YES"
    autoselect = attributes.get("AUTOSELECT") == "YES"
    forced = attributes.get("FORCED") == "YES"
    language = attributes.get("LANGUAGE")
    characteristics = _split_list(attributes.get("CHARACTERISTICS"))
    if kind == "AUDIO":
        audio.append(
            AudioTrack(
                group_id=group_id,
                name=name,
                language=language,
                uri=uri,
                default=default,
                autoselect=autoselect,
                forced=forced,
                channels=attributes.get("CHANNELS"),
                characteristics=characteristics,
            )
        )
    elif kind == "SUBTITLES":
        subtitles.append(
            SubtitleTrack(
                group_id=group_id,
                name=name,
                language=language,
                uri=uri,
                default=default,
                autoselect=autoselect,
                forced=forced,
                characteristics=characteristics,
            )
        )
    elif kind == "VIDEO":
        video.append(
            VideoTrack(
                group_id=group_id, name=name, uri=uri, default=default, autoselect=autoselect
            )
        )
    else:
        raise InvalidPlaylistError(f"Unknown EXT-X-MEDIA TYPE {kind!r}.", line=line)


# --- media playlists ---------------------------------------------------------------------------


def _parse_media(lines: list[_Line], base: str) -> MediaPlaylist:
    segments: list[Segment] = []
    target_duration: float | None = None
    media_sequence = 0
    end_list = False
    version: int | None = None
    playlist_type: str | None = None

    key: EncryptionInfo | None = None
    init_map: InitMap | None = None
    next_offsets: dict[str, int] = {}

    duration: float | None = None
    title = ""
    pending_range: SegmentRange | None = None
    discontinuity = False
    program_date_time: datetime | None = None
    extinf_line = 0

    for number, content in lines[1:]:
        if not content.startswith("#"):
            if duration is None:
                raise InvalidPlaylistError(
                    "A segment address has no #EXTINF before it.", line=number
                )
            uri = _resolve(base, content, number)
            byte_range = None
            if pending_range is not None:
                offset = pending_range.offset
                if offset is None:
                    offset = next_offsets.get(uri)
                    if offset is None:
                        raise InvalidPlaylistError(
                            "A byte range without an offset must follow a range of the same file.",
                            line=number,
                        )
                byte_range = SegmentRange(pending_range.length, offset)
                next_offsets[uri] = offset + byte_range.length
            segments.append(
                Segment(
                    index=len(segments),
                    uri=uri,
                    duration=duration,
                    sequence_number=media_sequence + len(segments),
                    byte_range=byte_range,
                    init_map=init_map,
                    encryption=key,
                    discontinuity=discontinuity,
                    program_date_time=program_date_time,
                    title=title,
                )
            )
            duration, title, pending_range = None, "", None
            discontinuity, program_date_time = False, None
            continue
        if not content.startswith("#EXT"):
            continue  # a comment
        name = _tag_name(content)
        if name == "EXTINF":
            if duration is not None:
                raise InvalidPlaylistError(
                    f"#EXTINF on line {extinf_line} has no segment address.", line=number
                )
            match = _EXTINF.match(_tag_value(content).strip())
            if not match:
                raise InvalidPlaylistError("#EXTINF needs a duration.", line=number)
            duration = float(match.group(1))
            title = (match.group(2) or "").strip()
            extinf_line = number
        elif name == "EXT-X-TARGETDURATION":
            target_duration = float(_whole_tag_number(content, number))
        elif name == "EXT-X-MEDIA-SEQUENCE":
            media_sequence = _whole_tag_number(content, number)
        elif name == "EXT-X-VERSION":
            version = _whole_tag_number(content, number)
        elif name == "EXT-X-ENDLIST":
            end_list = True
        elif name == "EXT-X-PLAYLIST-TYPE":
            playlist_type = _tag_value(content).strip()
            if playlist_type not in {"VOD", "EVENT"}:
                raise InvalidPlaylistError(f"Unknown playlist type {playlist_type!r}.", line=number)
        elif name == "EXT-X-DISCONTINUITY":
            discontinuity = True
        elif name == "EXT-X-BYTERANGE":
            pending_range = _byte_range(_tag_value(content), number)
        elif name == "EXT-X-PROGRAM-DATE-TIME":
            program_date_time = _date_time(_tag_value(content).strip(), number)
        elif name == "EXT-X-KEY":
            key = _read_key(content, number, base)
        elif name == "EXT-X-MAP":
            init_map = _read_map(content, number, base)

    if duration is not None:
        raise InvalidPlaylistError(f"#EXTINF on line {extinf_line} has no segment address.")
    if target_duration is None:
        raise InvalidPlaylistError("The playlist has no #EXT-X-TARGETDURATION.")

    return MediaPlaylist(
        uri=base,
        segments=tuple(segments),
        target_duration=target_duration,
        media_sequence=media_sequence,
        end_list=end_list,
        version=version,
        playlist_type=playlist_type,
        container=_detect_container(tuple(segments)),
    )


def _date_time(value: str, line: int) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise InvalidPlaylistError(
            f"#EXT-X-PROGRAM-DATE-TIME is not a valid date: {value!r}.", line=line
        ) from None


def _read_key(content: str, line: int, base: str) -> EncryptionInfo | None:
    attributes = parse_attribute_list(_tag_value(content), line=line)
    method_text = attributes.get("METHOD")
    if method_text is None:
        raise InvalidPlaylistError("EXT-X-KEY needs a METHOD.", line=line)
    try:
        method = EncryptionMethod(method_text)
    except ValueError:
        raise UnsupportedPlaylistError(
            f"The encryption method {method_text!r} is not supported (line {line})."
        ) from None
    if method is EncryptionMethod.NONE:
        return None
    raw_uri = attributes.get("URI")
    if not raw_uri:
        raise InvalidPlaylistError("An encrypted playlist's EXT-X-KEY needs a URI.", line=line)
    iv = None
    if "IV" in attributes:
        try:
            iv = EncryptionInfo.parse_iv(attributes["IV"])
        except InvalidPlaylistError as exc:
            raise InvalidPlaylistError(exc.message, line=line) from None
    return EncryptionInfo(
        method=method,
        uri=_resolve(base, raw_uri, line, web_only=False),
        iv=iv,
        key_format=attributes.get("KEYFORMAT", "identity"),
        key_format_versions=attributes.get("KEYFORMATVERSIONS"),
    )


def _read_map(content: str, line: int, base: str) -> InitMap:
    attributes = parse_attribute_list(_tag_value(content), line=line)
    raw_uri = attributes.get("URI")
    if not raw_uri:
        raise InvalidPlaylistError("EXT-X-MAP needs a URI.", line=line)
    byte_range = None
    if "BYTERANGE" in attributes:
        parsed = _byte_range(attributes["BYTERANGE"], line)
        byte_range = SegmentRange(parsed.length, parsed.offset if parsed.offset is not None else 0)
    return InitMap(uri=_resolve(base, raw_uri, line), byte_range=byte_range)


def _detect_container(segments: tuple[Segment, ...]) -> ContainerFormat:
    """Guess the segment format from the playlist alone. Unclear cases are left to the sniffer."""
    if not segments:
        return ContainerFormat.UNKNOWN
    if any(segment.init_map is not None for segment in segments):
        return ContainerFormat.FMP4
    path = urlsplit(segments[0].uri).path.lower()
    if path.endswith(_FMP4_SUFFIXES):
        return ContainerFormat.FMP4
    if path.endswith(_TS_SUFFIXES):
        return ContainerFormat.MPEG_TS
    if path.endswith(_VTT_SUFFIXES):
        return ContainerFormat.WEBVTT
    return ContainerFormat.UNKNOWN
