"""The command line's options, defined once and shared by every command that takes them.

Every option that takes one value can also be set with a ``RAYOHX_<NAME>`` environment variable
(``RAYOHX_QUALITY``, ``RAYOHX_PROXY``) or in the settings file; the command line wins over both.
"""

from pathlib import Path
from typing import Annotated

import typer

from rayohx import OutputFormat, RemuxBackend, StorageStrategy

SELECTION = "Choosing what to download"
NETWORK = "Network"
ENGINE = "Download engine"
OUTPUT = "Output"
DISPLAY = "Display"

Url = Annotated[
    str,
    typer.Argument(
        help="The address of the playlist (.m3u8). Quote it if it contains & or ?.",
        metavar="URL",
    ),
]

# --- choosing what to download -----------------------------------------------------------------

Quality = Annotated[
    str,
    typer.Option(
        "--quality",
        envvar="RAYOHX_QUALITY",
        help="best, worst, auto (up to 1080p), or a height such as 720p (the best at or below it).",
        rich_help_panel=SELECTION,
    ),
]
MaxResolution = Annotated[
    str | None,
    typer.Option(
        "--max-resolution",
        envvar="RAYOHX_MAX_RESOLUTION",
        help="Never pick a picture taller than this, for example 1080p.",
        rich_help_panel=SELECTION,
    ),
]
MaxBitrate = Annotated[
    str | None,
    typer.Option(
        "--max-bitrate",
        envvar="RAYOHX_MAX_BITRATE",
        help="Never pick a stream above this many bits per second, for example 4M.",
        rich_help_panel=SELECTION,
    ),
]
Variant = Annotated[
    int | None,
    typer.Option(
        "--variant",
        envvar="RAYOHX_VARIANT",
        min=1,
        help="Download this stream, by its number in `rayohx info` (the first is 1).",
        rich_help_panel=SELECTION,
    ),
]
Resolution = Annotated[
    str | None,
    typer.Option(
        "--resolution",
        envvar="RAYOHX_RESOLUTION",
        help="Only streams with exactly this size, such as 1280x720.",
        rich_help_panel=SELECTION,
    ),
]
Bandwidth = Annotated[
    str | None,
    typer.Option(
        "--bandwidth",
        envvar="RAYOHX_BANDWIDTH",
        help="Only streams with exactly this bit rate, in bits per second (for example 2800000).",
        rich_help_panel=SELECTION,
    ),
]
VariantName = Annotated[
    str | None,
    typer.Option(
        "--variant-name",
        envvar="RAYOHX_VARIANT_NAME",
        help="Only streams with exactly this name (ignoring case).",
        rich_help_panel=SELECTION,
    ),
]
Audio = Annotated[
    list[str] | None,
    typer.Option(
        "--audio",
        "-a",
        help="Audio language to download (repeat for several), for example en.",
        rich_help_panel=SELECTION,
    ),
]
AudioName = Annotated[
    list[str] | None,
    typer.Option(
        "--audio-name",
        help="Download the audio track with this exact name (repeatable).",
        rich_help_panel=SELECTION,
    ),
]
Subtitles = Annotated[
    list[str] | None,
    typer.Option(
        "--subtitles",
        "-s",
        help="Subtitle language to save beside the video (repeat for several).",
        rich_help_panel=SELECTION,
    ),
]
SubtitleName = Annotated[
    list[str] | None,
    typer.Option(
        "--subtitle-name",
        help="Save the subtitle track with this exact name (repeatable).",
        rich_help_panel=SELECTION,
    ),
]
VideoOnly = Annotated[
    bool,
    typer.Option(
        "--video-only",
        envvar="RAYOHX_VIDEO_ONLY",
        help="Download only the video, leaving out separate audio and subtitle tracks.",
        rich_help_panel=SELECTION,
    ),
]

# --- network -----------------------------------------------------------------------------------

Header = Annotated[
    list[str] | None,
    typer.Option(
        "--header",
        "-H",
        help="Send a header, as 'Name: value' (repeatable). @file reads them from a file.",
        rich_help_panel=NETWORK,
    ),
]
Cookie = Annotated[
    list[str] | None,
    typer.Option(
        "--cookie",
        help="Send a cookie, as name=value (repeatable). @file reads them from a file.",
        rich_help_panel=NETWORK,
    ),
]
UserAgent = Annotated[
    str | None,
    typer.Option(
        "--user-agent",
        envvar="RAYOHX_USER_AGENT",
        help="The User-Agent to send.",
        rich_help_panel=NETWORK,
    ),
]
Referer = Annotated[
    str | None,
    typer.Option(
        "--referer", envvar="RAYOHX_REFERER", help="The Referer to send.", rich_help_panel=NETWORK
    ),
]
Origin = Annotated[
    str | None,
    typer.Option(
        "--origin", envvar="RAYOHX_ORIGIN", help="The Origin to send.", rich_help_panel=NETWORK
    ),
]
Proxy = Annotated[
    str | None,
    typer.Option(
        "--proxy",
        envvar="RAYOHX_PROXY",
        help="An http:// or https:// proxy, for example http://user:pass@host:3128. "
        "Prefer the environment variable, since command lines can be seen by other users.",
        rich_help_panel=NETWORK,
    ),
]
Insecure = Annotated[
    bool,
    typer.Option(
        "--insecure",
        envvar="RAYOHX_INSECURE",
        help="Do not check TLS certificates. Only for servers you trust.",
        rich_help_panel=NETWORK,
    ),
]
CaBundle = Annotated[
    Path | None,
    typer.Option(
        "--ca-bundle",
        envvar="RAYOHX_CA_BUNDLE",
        help="A file of trusted certificate authorities (PEM), instead of the system's.",
        rich_help_panel=NETWORK,
    ),
]
ConnectTimeout = Annotated[
    float | None,
    typer.Option(
        "--connect-timeout",
        envvar="RAYOHX_CONNECT_TIMEOUT",
        help="Seconds to wait to connect.",
        rich_help_panel=NETWORK,
    ),
]
ReadTimeout = Annotated[
    float | None,
    typer.Option(
        "--read-timeout",
        envvar="RAYOHX_READ_TIMEOUT",
        help="Seconds to wait for data.",
        rich_help_panel=NETWORK,
    ),
]
Timeout = Annotated[
    float | None,
    typer.Option(
        "--timeout",
        envvar="RAYOHX_TIMEOUT",
        help="Most seconds one request may take, retries included.",
        rich_help_panel=NETWORK,
    ),
]
Retries = Annotated[
    int | None,
    typer.Option(
        "--retries",
        envvar="RAYOHX_RETRIES",
        min=0,
        help="Extra attempts after a failure.",
        rich_help_panel=NETWORK,
    ),
]
RateLimit = Annotated[
    str | None,
    typer.Option(
        "--rate-limit",
        envvar="RAYOHX_RATE_LIMIT",
        help="Most bytes per second to download in total, for example 2M.",
        rich_help_panel=NETWORK,
    ),
]
CredentialHost = Annotated[
    list[str] | None,
    typer.Option(
        "--credential-host",
        help="Also send headers like Authorization and your cookies to this host (repeatable). "
        "By default they go only to the host of the URL.",
        rich_help_panel=NETWORK,
    ),
]

# --- the download engine -----------------------------------------------------------------------

Concurrency = Annotated[
    int | None,
    typer.Option(
        "--concurrency",
        "-j",
        envvar="RAYOHX_CONCURRENCY",
        min=1,
        max=64,
        help="Segments to download at the same time (default 4).",
        rich_help_panel=ENGINE,
    ),
]
Resume = Annotated[
    bool,
    typer.Option(
        "--resume/--no-resume",
        envvar="RAYOHX_RESUME",
        help="Continue an interrupted download where it stopped.",
        rich_help_panel=ENGINE,
    ),
]
Storage = Annotated[
    StorageStrategy,
    typer.Option(
        "--storage",
        envvar="RAYOHX_STORAGE",
        case_sensitive=False,
        help="Where finished segments wait: auto, direct, streaming, buffered, temporary, hybrid.",
        rich_help_panel=ENGINE,
    ),
]
Workspace = Annotated[
    Path | None,
    typer.Option(
        "--workspace",
        envvar="RAYOHX_WORKSPACE",
        help="A folder for working files (default: beside the output).",
        rich_help_panel=ENGINE,
    ),
]
BufferSize = Annotated[
    str | None,
    typer.Option(
        "--buffer-size",
        envvar="RAYOHX_BUFFER_SIZE",
        help="Memory for the segment buffer, for example 64M.",
        rich_help_panel=ENGINE,
    ),
]
MaxTempSize = Annotated[
    str | None,
    typer.Option(
        "--max-temp-size",
        envvar="RAYOHX_MAX_TEMP_SIZE",
        help="Most working disk space to use, for example 2G.",
        rich_help_panel=ENGINE,
    ),
]
SpaceCheck = Annotated[
    bool,
    typer.Option(
        "--space-check/--no-space-check",
        envvar="RAYOHX_SPACE_CHECK",
        help="Check that the disk has room (turn off if the free space is reported wrongly).",
        rich_help_panel=ENGINE,
    ),
]

# --- output ------------------------------------------------------------------------------------

OutputPath = Annotated[
    Path | None,
    typer.Option(
        "--output",
        "-o",
        help="Where to save. A name without an extension gets the right one. "
        "By default it is named after the address, in the current folder.",
        rich_help_panel=OUTPUT,
    ),
]
Format = Annotated[
    OutputFormat,
    typer.Option(
        "--format",
        envvar="RAYOHX_FORMAT",
        case_sensitive=False,
        help="auto, ts, mp4, or segments (one file per segment, in a folder).",
        rich_help_panel=OUTPUT,
    ),
]
Overwrite = Annotated[
    bool,
    typer.Option(
        "--overwrite/--no-overwrite",
        envvar="RAYOHX_OVERWRITE",
        help="Replace an existing output file.",
        rich_help_panel=OUTPUT,
    ),
]
Validate = Annotated[
    bool,
    typer.Option(
        "--validate/--no-validate",
        envvar="RAYOHX_VALIDATE",
        help="Check the finished file before putting it in place.",
        rich_help_panel=OUTPUT,
    ),
]
RemuxBackendOption = Annotated[
    RemuxBackend,
    typer.Option(
        "--remux-backend",
        envvar="RAYOHX_REMUX_BACKEND",
        case_sensitive=False,
        help="Who re-wraps streams into MP4: auto (built in, then FFmpeg), native or ffmpeg.",
        rich_help_panel=OUTPUT,
    ),
]
Ffmpeg = Annotated[
    Path | None,
    typer.Option(
        "--ffmpeg",
        envvar="RAYOHX_FFMPEG",
        help="The FFmpeg program to use, if it is not on the PATH. FFmpeg is optional.",
        rich_help_panel=OUTPUT,
    ),
]

# --- display -----------------------------------------------------------------------------------

JsonOutput = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Print the result as JSON on stdout, for other programs.",
        rich_help_panel=DISPLAY,
    ),
]
ProgressMode = Annotated[
    str,
    typer.Option(
        "--progress",
        envvar="RAYOHX_PROGRESS",
        help="How to show progress: auto (a bar on a terminal, lines otherwise), bar, plain, none.",
        rich_help_panel=DISPLAY,
    ),
]
