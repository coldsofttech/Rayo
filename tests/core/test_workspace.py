import os
from pathlib import Path

import pytest

from rayohx import WorkspaceLockedError
from rayohx.workspace import (
    FOLDER_PREFIX,
    Workspace,
    resolve_workspace_root,
    workspace_key,
)


def test_the_key_is_stable_and_ignores_query_strings() -> None:
    first = workspace_key("https://h.example/v/index.m3u8?token=a", "sig", "out.mp4")
    assert first == workspace_key("https://H.EXAMPLE/v/index.m3u8?token=b", "sig", "out.mp4")
    assert first != workspace_key("https://h.example/v/other.m3u8", "sig", "out.mp4")
    assert first != workspace_key("https://h.example/v/index.m3u8", "other", "out.mp4")
    assert first != workspace_key("https://h.example/v/index.m3u8", "sig", "other.mp4")
    assert len(first) == 16
    assert all(char in "0123456789abcdef" for char in first)


def test_the_folder_sits_beside_the_output_by_default(tmp_path: Path) -> None:
    root = resolve_workspace_root(None, tmp_path / "out" / "video.mp4", "abc")
    assert root == (tmp_path / "out").resolve() / f"{FOLDER_PREFIX}abc"


def test_a_configured_folder_is_used_with_the_key(tmp_path: Path) -> None:
    assert resolve_workspace_root(tmp_path / "work", tmp_path / "v.mp4", "abc") == (
        tmp_path / "work" / "abc"
    )


def test_without_an_output_the_current_folder_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_workspace_root(None, None, "k") == Path.cwd() / f"{FOLDER_PREFIX}k"


def test_creating_a_workspace_makes_private_folders(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "ws")
    assert not workspace.exists()
    workspace.create()
    assert workspace.exists()
    assert workspace.segments_path.is_dir()
    if os.name == "posix":
        assert (tmp_path / "ws").stat().st_mode & 0o077 == 0
    workspace.create()  # creating twice is harmless


def test_only_one_download_may_hold_a_workspace(tmp_path: Path) -> None:
    first = Workspace(tmp_path / "ws")
    with first:
        with pytest.raises(WorkspaceLockedError) as info, Workspace(tmp_path / "ws"):
            pytest.fail("the second workspace must not open")
        assert info.value.hint
    with Workspace(tmp_path / "ws"):
        pass


def test_a_finished_workspace_is_removed_entirely(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "ws")
    with workspace:
        workspace.state_path.write_text("{}")
        (workspace.segments_path / "00000001.seg").write_bytes(b"x")
    workspace.clean(keep_state=False, keep_segments=False)
    assert not (tmp_path / "ws").exists()


def test_an_unfinished_workspace_keeps_only_what_resuming_needs(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "ws")
    with workspace:
        workspace.state_path.write_text("{}")
        (workspace.segments_path / "00000001.seg").write_bytes(b"x")
    workspace.clean(keep_state=True, keep_segments=False)
    assert workspace.state_path.exists()
    assert not workspace.segments_path.exists()
    assert not (tmp_path / "ws" / "lock").exists()
    workspace.clean(keep_state=False, keep_segments=False)
    assert not (tmp_path / "ws").exists()


def test_segments_can_be_kept_without_state(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "ws")
    with workspace:
        workspace.state_path.write_text("{}")
        (workspace.segments_path / "00000001.seg").write_bytes(b"x")
    workspace.clean(keep_state=False, keep_segments=True)
    assert not workspace.state_path.exists()
    assert (workspace.segments_path / "00000001.seg").exists()


def test_cleaning_a_missing_workspace_is_harmless(tmp_path: Path) -> None:
    Workspace(tmp_path / "never").clean(keep_state=False, keep_segments=False)


def test_reset_forgets_old_state_but_keeps_the_lock(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "ws")
    with workspace:
        workspace.state_path.write_text("{}")
        (workspace.segments_path / "00000001.seg").write_bytes(b"x")
        workspace.reset()
        assert not workspace.state_path.exists()
        assert list(workspace.segments_path.iterdir()) == []
        with pytest.raises(WorkspaceLockedError), Workspace(tmp_path / "ws"):
            pytest.fail("still locked")
