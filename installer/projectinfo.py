#!/usr/bin/env python3
"""The names the installer files, scripts and workflows need, read from pyproject.toml.

Nothing here imports the project, so it runs before anything is installed (a workflow's first
step). Renaming the project means changing pyproject.toml and the code; the installer scripts and
the workflows follow.

What is read:

- ``[tool.installer]`` gives the command's ``[project.scripts]`` key (``cli``), the window's
  executable name and function (``ui`` and ``ui-target``, since the window is not published and has
  no script) and the API package (``api``); the command-line and window packages come from the
  targets of the two entry points
- ``[tool.hatch.version] path`` gives the core package, and ``__version__`` is read from that file
- the window's title is ``TITLE`` in ``<ui package>/app.py``
- the frontend lives in ``src/<ui package>/ui``

Usage in a workflow step: ``python installer/projectinfo.py github`` adds the values to
``GITHUB_ENV`` (``UI_DIR``, ``CLI_NAME``, ``UI_NAME``, ...); with no argument it prints them.
"""

import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ProjectInfo:
    root: Path
    project: dict[str, Any]
    cli_script: str  # the command line executable and package name
    ui_script: str  # the window executable name
    cli_module: str  # module holding the Typer app, e.g. example_cli.cli
    entry_points: dict[str, str]  # executable name -> "module:function", the command and the window
    core_package: str
    cli_package: str
    ui_package: str
    api_package: str
    version: str
    title: str

    @property
    def ui_dir(self) -> Path:
        return self.root / "src" / self.ui_package / "ui"

    @property
    def api_env_prefix(self) -> str:
        """Prefix of the API's settings: the package name upper-cased (EXAMPLE_API_)."""
        return f"{self.api_package.upper()}_"

    def values(self) -> dict[str, str]:
        """The values workflows use, as environment variables."""
        return {
            "UI_DIR": self.ui_dir.relative_to(self.root).as_posix(),
            "CORE_PACKAGE": self.core_package,
            "CLI_PACKAGE": self.cli_package,
            "API_PACKAGE": self.api_package,
            "UI_PACKAGE": self.ui_package,
            "CLI_NAME": self.cli_script,
            "UI_NAME": self.ui_script,
            "CLI_MODULE": self.cli_module,
            "API_ENV_PREFIX": self.api_env_prefix,
            "APP_VERSION": self.version,
            "APP_TITLE": self.title,
        }


def _read(path: Path, pattern: str, what: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit(f"{what} not found in {path}")
    return match.group(1)


def load(root: Path = ROOT) -> ProjectInfo:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project: dict[str, Any] = data["project"]
    settings = data.get("tool", {}).get("installer", {})
    scripts: dict[str, str] = project["scripts"]
    try:
        cli, ui, api = settings["cli"], settings["ui"], settings["api"]
        cli_target, ui_target = scripts[cli], settings["ui-target"]
    except KeyError as missing:
        raise SystemExit(
            "pyproject.toml needs [tool.installer] cli, ui, ui-target and api, and the command "
            f"in [project.scripts] ({missing} is missing)."
        ) from None

    version_file = root / data["tool"]["hatch"]["version"]["path"]
    ui_package = ui_target.split(":")[0].split(".")[0]
    return ProjectInfo(
        root=root,
        project=project,
        cli_script=cli,
        ui_script=ui,
        cli_module=cli_target.split(":")[0],
        entry_points={cli: cli_target, ui: ui_target},
        core_package=version_file.parent.name,
        cli_package=cli_target.split(":")[0].split(".")[0],
        ui_package=ui_package,
        api_package=api,
        version=_read(version_file, r'^__version__\s*=\s*"([^"]+)"', "__version__"),
        title=_read(root / "src" / ui_package / "app.py", r'^TITLE\s*=\s*"([^"]+)"', "TITLE"),
    )


def main() -> None:
    info = load()
    values = info.values()
    if sys.argv[1:] == ["github"]:
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
            env.write("".join(f"{key}={value}\n" for key, value in values.items()))
    sys.stdout.write("".join(f"{key}={value}\n" for key, value in values.items()))


if __name__ == "__main__":
    main()
