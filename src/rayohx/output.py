"""Deciding what file to write.

The rules exist to stop the old mistake of raw MPEG-TS bytes being saved under an ``.mp4`` name.
The file name must match what is inside. A request that would need remuxing (for example MPEG-TS
into MP4) is refused with an explanation rather than quietly mislabelled.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from rayohx.enums import ContainerFormat, OutputFormat
from rayohx.errors import OutputFormatError

_SUFFIX_FORMATS = {
    ".ts": OutputFormat.TS,
    ".m2ts": OutputFormat.TS,
    ".mts": OutputFormat.TS,
    ".mp4": OutputFormat.MP4,
    ".m4v": OutputFormat.MP4,
    ".m4a": OutputFormat.MP4,
}
_NATIVE = {
    ContainerFormat.MPEG_TS: (OutputFormat.TS, ".ts"),
    ContainerFormat.FMP4: (OutputFormat.MP4, ".mp4"),
}
_SEGMENT_SUFFIX = {ContainerFormat.MPEG_TS: ".ts", ContainerFormat.FMP4: ".m4s"}
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{n}" for n in range(1, 10)),
        *(f"lpt{n}" for n in range(1, 10)),
    }
)
MAX_NAME_LENGTH = 80


def sanitize_filename(text: str, default: str = "video") -> str:
    """Turn arbitrary text (such as part of a web address) into a safe file name stem.

    The result has only letters, digits, dots, dashes and underscores, never starts with a dot, is
    never a reserved Windows device name, and cannot contain a path separator.
    """
    cleaned = _UNSAFE.sub("_", text).strip("._-")
    cleaned = re.sub(r"_+", "_", cleaned)[:MAX_NAME_LENGTH].strip("._-")
    if not cleaned:
        return default
    if cleaned.split(".")[0].lower() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def name_from_url(url: str) -> str:
    """A file name stem taken from the last part of a web address."""
    generic = {"index", "master", "playlist", "main", "manifest"}
    for part in reversed([p for p in unquote(urlsplit(url).path).split("/") if p]):
        stem = Path(part).stem
        if stem.lower() not in generic:
            return sanitize_filename(stem)
    return "video"


@dataclass(frozen=True, slots=True)
class OutputPlan:
    """The file (or folder) to write and what goes in it."""

    path: Path
    format: OutputFormat
    container: ContainerFormat

    @property
    def is_directory(self) -> bool:
        """Whether the output is a folder of separate segment files."""
        return self.format is OutputFormat.SEGMENTS


def plan_output(
    *,
    path: Path | None,
    requested: OutputFormat,
    container: ContainerFormat,
    url: str,
    directory: Path | None = None,
) -> OutputPlan:
    """Choose the output path and format for a stream.

    Args:
        path: Where the user wants the output, or ``None`` to name it after the address.
        requested: The format asked for. ``AUTO`` follows the file name, else the stream.
        container: What the segments are.
        url: The address downloaded; used to name the file when ``path`` is ``None``.
        directory: Where an unnamed output goes. Defaults to the current folder.

    Raises:
        OutputFormatError: If the stream cannot be written in that format without remuxing, or
            the file name does not match the format.
    """
    if container is ContainerFormat.WEBVTT:
        raise OutputFormatError(
            "This playlist contains subtitles only, which cannot be saved on their own yet.",
            hint="Download the video stream instead.",
        )
    if container is ContainerFormat.UNKNOWN:
        raise OutputFormatError(
            "The format of the segments could not be recognised.",
            hint="Only MPEG-TS and fragmented MP4 streams are supported.",
        )
    native_format, native_suffix = _NATIVE[container]

    if requested is OutputFormat.SEGMENTS:
        folder = path if path is not None else (directory or Path.cwd()) / name_from_url(url)
        return OutputPlan(Path(folder), OutputFormat.SEGMENTS, container)

    suffix = path.suffix.lower() if path is not None else ""
    if path is not None and suffix and suffix not in _SUFFIX_FORMATS:
        raise OutputFormatError(
            f"Files ending in {path.suffix!r} are not supported.",
            hint=f"Use {native_suffix} for this stream.",
        )
    from_name = _SUFFIX_FORMATS.get(suffix)
    if requested is not OutputFormat.AUTO and from_name not in (None, requested):
        assert path is not None  # noqa: S101 - a suffix exists only when there is a path
        raise OutputFormatError(
            f"The file name ends in {path.suffix!r} but the format {requested.value!r} "
            "was asked for."
        )
    chosen = requested if requested is not OutputFormat.AUTO else (from_name or native_format)

    if chosen is not native_format:
        raise OutputFormatError(
            f"This stream is {_describe(container)} and cannot be saved as "
            f"{chosen.value.upper()} without remuxing, which is not available yet.",
            hint=f"Save it as {native_suffix} instead.",
        )
    if path is None:
        return OutputPlan(
            (directory or Path.cwd()) / f"{name_from_url(url)}{native_suffix}", chosen, container
        )
    if not suffix:
        path = path.with_name(path.name + native_suffix)
    return OutputPlan(path, chosen, container)


def _describe(container: ContainerFormat) -> str:
    return "MPEG-TS" if container is ContainerFormat.MPEG_TS else "fragmented MP4"


def segment_file_name(index: int, container: ContainerFormat, *, init: bool = False) -> str:
    """The name of a separate segment file, for example ``segment-00001.ts``."""
    if init:
        return f"init-{index:05d}.mp4"
    return f"segment-{index + 1:05d}{_SEGMENT_SUFFIX.get(container, '.bin')}"
