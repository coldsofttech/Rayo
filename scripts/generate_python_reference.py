"""Write docs/python-reference.md: the public Python API of the ``rayohx`` package.

The text comes from the package itself (``rayohx.__all__``, signatures and docstrings), so it
cannot drift from the code. CI regenerates it and fails if the file changed.

    uv run python scripts/generate_python_reference.py            # write the file
    uv run python scripts/generate_python_reference.py --check    # exit 1 if it is out of date
"""

import argparse
import dataclasses
import enum
import inspect
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import rayohx

OUT = Path(__file__).resolve().parent.parent / "docs" / "python-reference.md"

#: Sections, in order. A name belongs to the first section whose test it passes.
SECTIONS: list[tuple[str, str]] = [
    ("Downloading", """
        download Downloader DownloadResult OutputInfo DownloadProgress DownloadSummary
    """),
    ("Inspecting and choosing", """
        inspect inspect_playlist select_stream StreamSelection parse_playlist is_hls_playlist
    """),
    ("Settings", """
        DownloadConfig NetworkConfig ProxyConfig SelectionConfig StorageConfig OutputConfig
    """),
    ("Events and cancellation", """
        EventBus DownloadEvent DownloadStateMachine CancellationToken
    """),
    ("Playlist models", """
        MasterPlaylist MediaPlaylist Variant AudioTrack SubtitleTrack VideoTrack Segment
        SegmentRange SegmentData InitMap EncryptionInfo Resolution Playlist
    """),
    ("Lower-level parts", """
        HttpClient HttpResponse HlsSegmentFetcher MediaDownload PlaylistFetcher RateLimiter
        ResumeState StateFile StorageMetrics DownloadRequest OutputPlan plan_output OutputSink
        FileSink StreamSink
    """),
    ("Output of results", """
        playlist_to_dict progress_to_dict result_to_dict configure_logging
    """),
]  # fmt: skip
#: Names left out: the template's example greeting API.
HIDDEN = {"__version__", "greet", "Greeting", "GreetingStore", "GreetingNotFoundError"}


def _summary(doc: str | None) -> str:
    return inspect.cleandoc(doc).split("\n\n")[0].replace("\n", " ") if doc else ""


def _body(doc: str | None) -> str:
    return inspect.cleandoc(doc) if doc else ""


def _signature(obj: Any) -> str:
    try:
        text = str(inspect.signature(obj))
        return re.sub(r"\b(?:[a-z_][a-z_0-9]*\.)+(?=[A-Z])", "", text)
    except (TypeError, ValueError):
        return "(...)"


def _fields(cls: type) -> list[str]:
    lines = []
    for field in dataclasses.fields(cls):
        if not field.init:
            continue
        kind = re.sub(
            r"\b(?:[a-z_][a-z_0-9]*\.)+(?=[A-Z])",
            "",
            getattr(field.type, "__name__", str(field.type)),
        )
        if field.default is not dataclasses.MISSING:
            default = f" = {field.default!r}"
        elif field.default_factory is not dataclasses.MISSING:
            default = " = (default)"
        else:
            default = ""
        lines.append(f"- `{field.name}: {kind}{default}`")
    return lines


def _members(cls: type) -> list[str]:
    lines = []
    for name, member in sorted(vars(cls).items()):
        if name.startswith("_"):
            continue
        target = member.fget if isinstance(member, property) else member
        if isinstance(target, (classmethod, staticmethod)):
            target = target.__func__
        if not callable(target) and not isinstance(member, property):
            continue
        doc = _summary(getattr(target, "__doc__", None))
        if isinstance(member, property):
            lines.append(f"- `{name}` (property): {doc}")
        else:
            lines.append(f"- `{name}{_signature(target)}`: {doc}")
    return lines


def _is_subclass(name: str, base: type) -> bool:
    obj = getattr(rayohx, name)
    return inspect.isclass(obj) and issubclass(obj, base)


def describe(name: str) -> str:
    obj = getattr(rayohx, name)
    out = [f"### `{name}`", ""]
    if inspect.isclass(obj):
        if issubclass(obj, enum.Enum):
            out += [_body(obj.__doc__), "", "Values: " + ", ".join(f"`{m.name}`" for m in obj)]
        elif issubclass(obj, BaseException):
            bases = ", ".join(f"`{b.__name__}`" for b in obj.__bases__)
            out += [_body(obj.__doc__), "", f"Inherits from {bases}."]
        else:
            out += [_body(obj.__doc__), ""]
            if dataclasses.is_dataclass(obj):
                out += ["Fields:", "", *_fields(obj), ""]
            members = _members(obj)
            if members:
                out += ["Methods and properties:", "", *members]
    elif callable(obj):
        out += [f"```python\n{name}{_signature(obj)}\n```", "", _body(obj.__doc__)]
    else:
        out += [f"`{name}` = `{obj!r}`"]
    return "\n".join(out).rstrip() + "\n"


def build() -> str:
    public = set(rayohx.__all__) - {
        "__version__",
        "greet",
        "Greeting",
        "GreetingStore",
        "GreetingNotFoundError",
    }
    errors = sorted(
        n
        for n in public
        if inspect.isclass(getattr(rayohx, n)) and issubclass(getattr(rayohx, n), BaseException)
    )
    enums = sorted(
        n
        for n in public
        if inspect.isclass(getattr(rayohx, n)) and issubclass(getattr(rayohx, n), enum.Enum)
    )
    placed: set[str] = set(errors) | set(enums)
    parts = [
        "# Python reference",
        "",
        "<!-- Generated by generate_python_reference.py; do not edit. CI checks it. -->",
        "",
        f"The public API of the `rayohx` package (version {rayohx.__version__}). "
        "Everything here is "
        "importable from `rayohx` directly. For examples see the [Python guide](python.md).",
        "",
    ]
    for title, names in SECTIONS:
        listed = [n for n in names.split() if n in public]
        placed.update(listed)
        parts += [f"## {title}", ""] + [describe(n) for n in listed]
    rest = sorted(public - placed)
    if rest:
        parts += ["## Other", ""] + [describe(n) for n in rest]
    parts += ["## Choices", ""] + [describe(n) for n in enums]
    parts += [
        "## Errors",
        "",
        "All errors derive from `RayoHXError`; a `hint` attribute, when set, says what to try.",
        "",
    ]
    parts += [describe(n) for n in errors]
    return "\n".join(parts).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate docs/python-reference.md")
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args(argv)
    text = "\n".join(line.rstrip() for line in textwrap.dedent(build()).split("\n"))
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.stderr.write("docs/python-reference.md is out of date; run this script.\n")
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(f"Docs saved to: {OUT.relative_to(OUT.parents[1])}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
