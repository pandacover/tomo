from __future__ import annotations

from tomo.file_tools import edit_file, glob, read_file, write_file


def test_read_file_allows_absolute_path_outside_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    workspace.mkdir()
    outside.write_text("outside content", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert read_file.invoke({"path": outside.as_posix()}) == "outside content"


def test_write_file_allows_absolute_path_outside_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    target = tmp_path / "outside.txt"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    assert write_file.invoke({"path": target.as_posix(), "content": "created"}) == f"Wrote {target.as_posix()}."
    assert target.read_text(encoding="utf-8") == "created"


def test_edit_file_allows_absolute_path_outside_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    target = tmp_path / "outside.txt"
    workspace.mkdir()
    target.write_text("hello old", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert edit_file.invoke({"path": target.as_posix(), "old_text": "old", "new_text": "new"}) == f"Edited {target.as_posix()}."
    assert target.read_text(encoding="utf-8") == "hello new"


def test_glob_allows_absolute_path_outside_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outside_dir = tmp_path / "outside"
    target = outside_dir / "note.txt"
    workspace.mkdir()
    outside_dir.mkdir()
    target.write_text("x", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert glob.invoke({"pattern": "*.txt", "path": outside_dir.as_posix()}) == target.as_posix()


def test_file_tools_still_deny_dotfiles_outside_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    dotfile = tmp_path / ".secret"
    workspace.mkdir()
    dotfile.write_text("secret", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert read_file.invoke({"path": dotfile.as_posix()}) == f"Error: {dotfile.as_posix()} touches a dotfile or dot-directory."

