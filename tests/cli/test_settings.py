from pathlib import Path
from types import SimpleNamespace

import pytest

from rayohx import ConfigurationError
from rayohx_cli import settings
from rayohx_cli.settings import find_config, load_config, user_config_file

VALID = {"quality", "concurrency", "max_resolution", "header", "url", "output", "json"}


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_the_file_is_read_and_dashes_become_underscores(tmp_path: Path) -> None:
    path = write(
        tmp_path / "c.toml",
        'quality = "720p"\nmax-resolution = "1080p"\nconcurrency = 8\nheader = ["A: b"]\n',
    )
    assert load_config(path, VALID) == {
        "quality": "720p",
        "max_resolution": "1080p",
        "concurrency": 8,
        "header": ["A: b"],
    }
    assert load_config(write(tmp_path / "empty.toml", ""), VALID) == {}


def test_a_key_that_is_not_an_option_is_an_error_with_a_suggestion(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", 'qualty = "720p"\n')
    with pytest.raises(ConfigurationError, match="not a setting") as info:
        load_config(path, VALID)
    assert "quality" in (info.value.hint or "")
    assert str(path) in str(info.value)
    unrelated = write(tmp_path / "d.toml", "zzzzzzzz = 1\n")
    with pytest.raises(ConfigurationError) as other:
        load_config(unrelated, VALID)
    assert "names of the command line options" in (other.value.hint or "")


@pytest.mark.parametrize("key", ["url", "output", "json"])
def test_options_that_make_no_sense_as_defaults_are_refused(tmp_path: Path, key: str) -> None:
    with pytest.raises(ConfigurationError, match="not a setting"):
        load_config(write(tmp_path / "c.toml", f'{key} = "x"\n'), VALID)


def test_invalid_toml_is_explained(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not valid TOML"):
        load_config(write(tmp_path / "c.toml", "quality = = 3"), VALID)
    binary = tmp_path / "bin.toml"
    binary.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigurationError, match="cannot be read"):
        load_config(binary, VALID)


def test_a_named_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        find_config(tmp_path / "nope.toml")
    named = write(tmp_path / "mine.toml", "")
    assert find_config(named) == named


def test_the_search_order(tmp_path: Path) -> None:
    home = tmp_path / "home"
    environ = {"XDG_CONFIG_HOME": str(home), "APPDATA": str(home)}
    cwd = tmp_path / "work"
    cwd.mkdir()
    assert find_config(None, cwd=cwd, environ=environ) is None
    personal = user_config_file(environ)
    personal.parent.mkdir(parents=True)
    write(personal, "")
    assert find_config(None, cwd=cwd, environ=environ) == personal
    local = write(cwd / settings.FILE_NAME, "")
    assert find_config(None, cwd=cwd, environ=environ) == local  # the project's file wins


def test_the_personal_file_lives_in_the_usual_place(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only this module's view of ``os`` is replaced, never the real one.
    monkeypatch.setattr(settings, "os", SimpleNamespace(name="posix", environ={}))
    assert user_config_file({"XDG_CONFIG_HOME": "/x"}) == Path("/x/rayohx/config.toml")
    assert user_config_file({}) == Path.home() / ".config" / "rayohx" / "config.toml"
    monkeypatch.setattr(settings, "os", SimpleNamespace(name="nt", environ={}))
    assert user_config_file({"APPDATA": "/a"}) == Path("/a/rayohx/config.toml")
    assert user_config_file({}) == Path.home() / "AppData" / "Roaming" / "rayohx" / "config.toml"
