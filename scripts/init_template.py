#!/usr/bin/env python3
"""Rename the template's example names to yours. Run once by the Init workflow.

    python scripts/init_template.py --name acme --title "Acme Studio" [--dry-run]

``--name`` replaces the word ``example`` everywhere it names something: the packages
(``example_package`` becomes ``acme_package``), the commands (``example-cli`` becomes ``acme-cli``),
the settings prefix (``EXAMPLE_API_`` becomes ``ACME_API_``) and the window's app id. ``--title``
replaces the window's display name ("Example App"). The two product GUIDs in the NSIS scripts are
replaced with new ones. Directories named after the packages are renamed too.

Only those exact patterns are replaced, so prose such as "for example" and addresses such as
``example.com`` are left alone. Workflows need no renaming: they read the names from pyproject.toml.
"""

import argparse
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "build", "dist", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".hypothesis", ".schemathesis", ".temp", ".claude",
}  # fmt: skip
# Not renamed: the lock file is regenerated, the license is not ours to change, and the workflows
# read their names from pyproject.toml.
SKIP_FILES = {"uv.lock", "LICENSE"}
SKIP_PREFIXES = (".github/workflows/", "scripts/init_template.py")
GUID = re.compile(r'(!define GUID ")\{[0-9A-Fa-f-]{36}\}(")')


def valid_name(name: str) -> bool:
    return re.fullmatch(r"[a-z][a-z0-9]{1,30}", name) is not None


def valid_title(title: str) -> bool:
    return bool(title.strip()) and len(title) <= 60 and not re.search(r'["\\\n\r]', title)


def replacements(name: str, title: str) -> list[tuple[re.Pattern[str], str]]:
    """Ordered patterns: the specific ones first."""
    return [
        (re.compile(r"com\.example\.app"), f"com.{name}.app"),
        (re.compile(r"\bExample App\b"), title),
        (re.compile(r"EXAMPLE_"), f"{name.upper()}_"),
        (re.compile(r"example_"), f"{name}_"),
        (re.compile(r"example-"), f"{name}-"),
    ]


def project_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts) or not path.is_file():
            continue
        posix = relative.as_posix()
        if posix in SKIP_FILES or posix.startswith(SKIP_PREFIXES):
            continue
        files.append(path)
    return files


def rewrite(root: Path, name: str, title: str, *, dry_run: bool) -> list[str]:
    """Replace the names in file contents. Returns the files that changed."""
    patterns = replacements(name, title)
    changed = []
    for path in project_files(root):
        try:
            text = path.read_text(encoding="utf-8", newline="")
        except UnicodeDecodeError:
            continue  # a binary file (the icon, for example)
        new = text
        for pattern, replacement in patterns:
            new = pattern.sub(lambda _, value=replacement: value, new)
        if path.suffix == ".nsi":
            new = GUID.sub(
                lambda m: f"{m.group(1)}{{{str(uuid.uuid4()).upper()}}}{m.group(2)}", new
            )
        if new != text:
            changed.append(path.relative_to(root).as_posix())
            if not dry_run:
                path.write_text(new, encoding="utf-8", newline="")
    return changed


def rename_paths(root: Path, name: str, *, dry_run: bool) -> list[str]:
    """Rename files and folders named after the example packages, deepest first."""
    renamed = []
    candidates = [
        p for p in root.rglob("*") if not any(x in SKIP_DIRS for x in p.relative_to(root).parts)
    ]
    for path in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
        new_name = re.sub(r"^example([_-])", rf"{name}\1", path.name)
        if new_name != path.name:
            renamed.append(f"{path.relative_to(root).as_posix()} -> {new_name}")
            if not dry_run:
                path.rename(path.with_name(new_name))
    return renamed


def leftovers(root: Path) -> list[str]:
    """Lines that still mention ``example`` as a name, for the maintainer to review."""
    pattern = re.compile(r"example[_-]|EXAMPLE_|Example App", re.IGNORECASE)
    found = []
    for path in project_files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            if pattern.search(line):
                found.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()[:100]}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="lowercase letters and digits, e.g. acme")
    parser.add_argument("--title", required=True, help='display name, e.g. "Acme Studio"')
    parser.add_argument("--root", type=Path, default=ROOT, help="repository to change")
    parser.add_argument("--dry-run", action="store_true", help="only list what would change")
    args = parser.parse_args()

    if not valid_name(args.name):
        raise SystemExit(
            "--name must be 2 to 31 lowercase letters and digits, starting with a letter."
        )
    if not valid_title(args.title):
        raise SystemExit("--title must be 1 to 60 characters, without quotes or backslashes.")
    if not (args.root / "src" / "example_package").is_dir():
        raise SystemExit("src/example_package not found: this repository is already initialised.")

    changed = rewrite(args.root, args.name, args.title, dry_run=args.dry_run)
    renamed = rename_paths(args.root, args.name, dry_run=args.dry_run)
    sys.stdout.write(f"{len(changed)} files changed, {len(renamed)} paths renamed.\n")
    for line in renamed:
        sys.stdout.write(f"  renamed {line}\n")
    left = leftovers(args.root) if not args.dry_run else []
    if left:
        sys.stdout.write("Still mention the example name (check these):\n")
        sys.stdout.write("".join(f"  {line}\n" for line in left))


if __name__ == "__main__":
    main()
