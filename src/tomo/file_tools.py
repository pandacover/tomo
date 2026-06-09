from __future__ import annotations

from glob import glob as expand_glob
from pathlib import Path

from langchain_core.tools import tool

from .tools import _is_relative_to, _resolve_path, _workspace


MAX_FILE_READ_CHARS = 20_000


def _path_error(path: Path) -> str | None:
    if any(part.startswith(".") for part in path.parts):
        return "touches a dotfile or dot-directory."
    return None


def _guard(path: str) -> tuple[Path | None, str | None]:
    target = _resolve_path(path)
    error = _path_error(target)
    if error:
        return None, f"Error: {path} {error}"
    return target, None


@tool("read_file")
def read_file(path: str) -> str:
    """Read exact contents from a known file path. Relative paths resolve from cwd; absolute paths are allowed except dotfiles."""
    target, error = _guard(path)
    if error:
        return error
    assert target is not None
    if not target.exists():
        return f"Error: {path} does not exist."
    if not target.is_file():
        return f"Error: {path} is not a file."
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_FILE_READ_CHARS:
        return text[:MAX_FILE_READ_CHARS] + "\n... output truncated ..."
    return text


@tool("glob")
def glob(pattern: str = "**/*", path: str = ".") -> str:
    """Return file paths matching a glob pattern. Relative paths resolve from cwd; absolute paths are allowed except dotfiles."""
    root = _resolve_path(path)
    root_error = _path_error(root)
    if root_error:
        return f"Error: {path} {root_error}"
    search_pattern = pattern if Path(pattern).is_absolute() else (root / pattern).as_posix()
    matches: list[str] = []
    for raw_match in expand_glob(search_pattern, recursive=True):
        match = Path(raw_match).resolve()
        if _path_error(match):
            continue
        matches.append(match.as_posix())
        if len(matches) >= 200:
            break
    return "\n".join(sorted(matches)) if matches else "No matches found."


@tool("write_file")
def write_file(path: str, content: str) -> str:
    """Create a new file. Relative paths resolve from cwd; absolute paths are allowed except dotfiles."""
    target, error = _guard(path)
    if error:
        return error
    assert target is not None
    if not target.parent.exists():
        return f"Error: parent directory does not exist for {path}."
    if target.exists():
        return f"Error: {path} already exists. Use edit_file for existing files."
    target.write_text(content, encoding="utf-8")
    return f"Wrote {path}."


@tool("edit_file")
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Modify an existing file with exact string replacement. Relative paths resolve from cwd; absolute paths are allowed except dotfiles."""
    target, error = _guard(path)
    if error:
        return error
    assert target is not None
    if not target.exists():
        return f"Error: {path} does not exist."
    if not target.is_file():
        return f"Error: {path} is not a file."
    if not old_text:
        return "Error: old_text cannot be empty."
    text = target.read_text(encoding="utf-8")
    if old_text not in text:
        return f"Error: old_text was not found in {path}."
    target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    return f"Edited {path}."
