"""Project context file discovery (port of pi coding-agent context loading)."""

from __future__ import annotations

from pathlib import Path

from pi_agent_cli.config import pi_home
from pi_agent_cli.system_prompt import ContextFile

AGENT_CONTEXT_FILENAMES: tuple[str, ...] = (
    "AGENTS.override.md",
    "AGENTS.md",
    "AGENTS.MD",
    "CLAUDE.md",
    "CLAUDE.MD",
)

SYSTEM_PROMPT_FILENAMES: tuple[tuple[str, ...], tuple[str, ...]] = (
    (".pi/SYSTEM.md",),
    ("agent/SYSTEM.md",),
)

APPEND_SYSTEM_PROMPT_FILENAMES: tuple[tuple[str, ...], tuple[str, ...]] = (
    (".pi/APPEND_SYSTEM.md",),
    ("agent/APPEND_SYSTEM.md",),
)


def _read_if_exists(path: Path) -> ContextFile | None:
    if not path.is_file():
        return None
    return ContextFile(path=str(path.resolve()), content=path.read_text(encoding="utf-8"))


def _find_in_directory(directory: Path, filenames: tuple[str, ...]) -> list[ContextFile]:
    found: list[ContextFile] = []
    for name in filenames:
        item = _read_if_exists(directory / name)
        if item is not None:
            found.append(item)
    return found


def discover_context_files(*, cwd: str | Path, home: Path | None = None) -> list[ContextFile]:
    """Discover AGENTS/CLAUDE context files from global + cwd walk."""
    resolved_cwd = Path(cwd).resolve()
    home_dir = pi_home(home)
    files: list[ContextFile] = []

    global_agents = _read_if_exists(home_dir / "agent" / "AGENTS.md")
    if global_agents is not None:
        files.append(global_agents)

    seen_paths: set[str] = {item.path for item in files}
    current = resolved_cwd
    while True:
        for item in _find_in_directory(current, AGENT_CONTEXT_FILENAMES):
            if item.path not in seen_paths:
                files.append(item)
                seen_paths.add(item.path)
        if current.parent == current:
            break
        current = current.parent

    return files


def load_system_prompt_file(*, cwd: str | Path, home: Path | None = None) -> str | None:
    """Load custom SYSTEM.md (project ``.pi/`` first, then global agent dir)."""
    resolved_cwd = Path(cwd).resolve()
    home_dir = pi_home(home)
    project_paths, global_paths = SYSTEM_PROMPT_FILENAMES
    for relative in project_paths:
        item = _read_if_exists(resolved_cwd / relative)
        if item is not None:
            return item.content
    for relative in global_paths:
        item = _read_if_exists(home_dir / relative)
        if item is not None:
            return item.content
    return None


def load_append_system_prompt_file(*, cwd: str | Path, home: Path | None = None) -> str | None:
    """Load APPEND_SYSTEM.md (project ``.pi/`` first, then global agent dir)."""
    resolved_cwd = Path(cwd).resolve()
    home_dir = pi_home(home)
    project_paths, global_paths = APPEND_SYSTEM_PROMPT_FILENAMES
    for relative in project_paths:
        item = _read_if_exists(resolved_cwd / relative)
        if item is not None:
            return item.content
    for relative in global_paths:
        item = _read_if_exists(home_dir / relative)
        if item is not None:
            return item.content
    return None
