"""The generated Python reference is current and covers the public API."""

import importlib.util
from pathlib import Path

import pytest

import rayohx

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "generate_python_reference", ROOT / "scripts" / "generate_python_reference.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_reference_is_up_to_date(script) -> None:  # type: ignore[no-untyped-def]
    assert script.main(["--check"]) == 0


def test_every_public_name_is_described(script) -> None:  # type: ignore[no-untyped-def]
    text = (ROOT / "docs" / "python-reference.md").read_text(encoding="utf-8")
    for name in set(rayohx.__all__) - script.HIDDEN:
        assert f"### `{name}`" in text, name
