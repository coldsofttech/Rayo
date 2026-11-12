"""The settings file: defaults for the options, so common ones need not be typed every time.

Precedence, highest first: the command line, then ``RAYOHX_*`` environment variables, then the
settings file, then the built-in defaults. The file is TOML whose keys are option names
(``quality = "1080p"``, ``header = ["Referer: https://example.com/"]``). A key that is not an
option is an error, so a typo never silently does nothing.
"""

import difflib
import os
import tomllib
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from rayohx import ConfigurationError

FILE_NAME = "rayohx.toml"
#: Options that make no sense as a default.
EXCLUDED = frozenset({"url", "output", "help", "json"})


def user_config_file(environ: Mapping[str, str] | None = None) -> Path:
    """Where the per-user settings file lives on this system."""
    env = os.environ if environ is None else environ
    if os.name == "nt":
        base = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "rayohx" / "config.toml"


def find_config(
    explicit: Path | None, *, cwd: Path | None = None, environ: Mapping[str, str] | None = None
) -> Path | None:
    """The settings file to use: the one named, else ``rayohx.toml`` here, else the user's.

    Raises:
        ConfigurationError: If a file was named and does not exist.
    """
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigurationError(f"The settings file {explicit} does not exist.")
        return explicit
    local = (cwd or Path.cwd()) / FILE_NAME
    if local.is_file():
        return local
    personal = user_config_file(environ)
    return personal if personal.is_file() else None


def load_config(path: Path, valid: Collection[str]) -> dict[str, Any]:
    """Read a settings file and check its keys against the option names in ``valid``.

    Keys may use dashes or underscores. The result uses underscores, as the options do.

    Raises:
        ConfigurationError: If the file is not valid TOML or has a key that is not an option.
    """
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"The settings file {path} cannot be read.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"The settings file {path} is not valid TOML: {exc}") from exc
    allowed = set(valid) - EXCLUDED
    settings: dict[str, Any] = {}
    for key, value in raw.items():
        name = key.replace("-", "_")
        if name not in allowed:
            close = difflib.get_close_matches(name, sorted(allowed), n=1)
            hint = f"Did you mean {close[0].replace('_', '-')!r}?" if close else None
            raise ConfigurationError(
                f"{key!r} in {path} is not a setting.",
                hint=hint or "Use the names of the command line options.",
            )
        settings[name] = value
    return settings
