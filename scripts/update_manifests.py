#!/usr/bin/env python3
"""Generate the release notes and the Homebrew and Scoop manifests for a release.

Used by .github/workflows/release.yml; everything comes from pyproject.toml, uv.lock and the
CHANGELOG, so renaming the project needs no edit here.

    name                                              the command and package name (rayohx)
    notes    --version 0.1.0                          the CHANGELOG section, for the release notes
    homebrew --version 0.1.0 --sdist-sha256 HEX --out Formula/rayohx.rb
    scoop    --version 0.1.0 --zip-sha256 HEX --repo owner/name --out bucket/rayohx.json

The Homebrew formula installs the PyPI source distribution in a virtualenv, with one resource per
runtime dependency (url and sha256 taken from uv.lock) plus the build backend, because Homebrew
builds without network access to PyPI. winget needs no generated file: the workflow submits the
setup.exe through winget-releaser.
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer"))
import projectinfo  # noqa: E402

INFO = projectinfo.load()
PROJECT: dict[str, Any] = INFO.project
CLI_SCRIPT = INFO.cli_script  # the command and package name, a [tool.installer] key
BUILD_BACKEND = "hatchling"  # [build-system] requires; locked in the "installer" group
PYTHON_FORMULA = "python@3.13"
# Homebrew runs on macOS and Linux, never Windows.
HOMEBREW_ENVIRONMENTS = (
    {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix"},
    {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"},
)


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def applies(marker: str | None) -> bool:
    """Whether a dependency is installed on macOS or Linux."""
    if not marker:
        return True
    return any(Marker(marker).evaluate({**env, "extra": ""}) for env in HOMEBREW_ENVIRONMENTS)


# ---- release notes -------------------------------------------------------------------------------
def release_notes(version: str) -> str:
    """The CHANGELOG section of ``version``, or a pointer to the file if there is none."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = rf"^## \[v?{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)"
    match = re.search(pattern, changelog, re.MULTILINE | re.DOTALL)
    body = match.group(1).strip() if match else ""
    return body or "See [CHANGELOG.md](CHANGELOG.md) for the changes in this release."


# ---- Homebrew ------------------------------------------------------------------------------------
def locked_packages() -> dict[str, dict[str, Any]]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {normalise(package["name"]): package for package in lock["package"]}


def closure(packages: dict[str, dict[str, Any]], roots: list[str]) -> list[str]:
    """Names of ``roots`` and everything they need on macOS or Linux, in install order."""
    seen: dict[str, None] = {}
    stack = list(roots)
    while stack:
        name = normalise(stack.pop())
        if name in seen:
            continue
        seen[name] = None
        for dependency in packages[name].get("dependencies", []):
            if applies(dependency.get("marker")):
                stack.append(dependency["name"])
    return sorted(seen)


def resources(version: str) -> list[tuple[str, str, str]]:
    packages = locked_packages()
    project = normalise(PROJECT["name"])
    names = closure(packages, [project, BUILD_BACKEND])
    result = []
    for name in names:
        if name == project:
            continue
        sdist = packages[name].get("sdist")
        if not sdist:
            raise SystemExit(f"{name} has no source distribution in uv.lock; Homebrew needs one.")
        result.append((name, sdist["url"], sdist["hash"].removeprefix("sha256:")))
    return result


def class_name(formula: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[-_]", formula))


def homebrew_formula(version: str, sdist_sha256: str) -> str:
    project = PROJECT["name"]
    filename = f"{project.replace('-', '_')}-{version}.tar.gz"
    url = f"https://files.pythonhosted.org/packages/source/{project[0]}/{project}/{filename}"
    homepage = (PROJECT.get("urls") or {}).get("Homepage", "")
    blocks = "\n".join(
        f'  resource "{name}" do\n    url "{resource_url}"\n    sha256 "{digest}"\n  end\n'
        for name, resource_url, digest in resources(version)
    )
    return f"""class {class_name(CLI_SCRIPT)} < Formula
  include Language::Python::Virtualenv

  desc "{PROJECT.get("description", CLI_SCRIPT)}"
  homepage "{homepage}"
  url "{url}"
  sha256 "{sdist_sha256}"
  license "{PROJECT.get("license", "")}"

  depends_on "{PYTHON_FORMULA}"

{blocks}
  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/{CLI_SCRIPT} --version")
  end
end
"""


# ---- Scoop ---------------------------------------------------------------------------------------
def zip_name(version: str) -> str:
    return f"{CLI_SCRIPT}-{version}-windows-x64.zip"


def scoop_manifest(version: str, zip_sha256: str, repo: str) -> str:
    base = f"https://github.com/{repo}/releases/download"
    manifest = {
        "version": version,
        "description": PROJECT.get("description", CLI_SCRIPT),
        "homepage": (PROJECT.get("urls") or {}).get("Homepage", f"https://github.com/{repo}"),
        "license": PROJECT.get("license", ""),
        "architecture": {
            "64bit": {"url": f"{base}/v{version}/{zip_name(version)}", "hash": zip_sha256}
        },
        "bin": f"{CLI_SCRIPT}.exe",
        "checkver": "github",
        "autoupdate": {
            "architecture": {"64bit": {"url": f"{base}/v$version/{zip_name('$version')}"}}
        },
    }
    return json.dumps(manifest, indent=4) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("name")
    for name in ("notes", "homebrew", "scoop"):
        command = sub.add_parser(name)
        command.add_argument("--version", required=True)
        command.add_argument("--out", type=Path, help="write here instead of stdout")
    sub.choices["homebrew"].add_argument("--sdist-sha256", required=True)
    sub.choices["scoop"].add_argument("--zip-sha256", required=True)
    sub.choices["scoop"].add_argument("--repo", required=True, help="owner/name of the repository")
    args = parser.parse_args()

    if args.command == "name":
        print(CLI_SCRIPT)
        return
    if args.command == "notes":
        text = release_notes(args.version) + "\n"
    elif args.command == "homebrew":
        text = homebrew_formula(args.version, args.sdist_sha256)
    else:
        text = scoop_manifest(args.version, args.zip_sha256, args.repo)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
