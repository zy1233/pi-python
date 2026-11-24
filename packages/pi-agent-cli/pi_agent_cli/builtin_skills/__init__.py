"""Built-in skills bundled with ``pi-agent-cli``."""

from __future__ import annotations

from pathlib import Path

from pi_agent_harness.skills import load_skills
from pi_agent_harness.types import FileSystem, SkillLoadResult

__all__ = ["builtin_skills_dir", "load_builtin_skills"]


def builtin_skills_dir() -> Path:
    """Return the root directory containing built-in skill subdirectories."""
    return Path(__file__).resolve().parent


async def load_builtin_skills(env: FileSystem) -> SkillLoadResult:
    """Load skills bundled with ``pi-agent-cli`` using POSIX paths."""
    root = builtin_skills_dir()
    if not root.is_dir():
        return SkillLoadResult()
    return await load_skills(env, [root.as_posix()])
