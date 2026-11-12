"""The published package (library and command line) imports only what it declares."""

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: Import names that differ from the distribution names in ``pyproject.toml``.
OWN = {"rayohx", "rayohx_cli"}


def _declared() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = set()
    for requirement in project["dependencies"]:
        name = requirement
        for separator in ">=<~! ;[":
            name = name.split(separator)[0]
        names.add(name.lower().replace("-", "_"))
    return names


def _imported(package: str) -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "src" / package).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def test_every_third_party_import_is_a_declared_dependency() -> None:
    declared = _declared()
    for package in OWN:
        third_party = {
            name for name in _imported(package) if name not in sys.stdlib_module_names | OWN
        }
        assert third_party <= declared, f"{package} imports {third_party - declared}"
