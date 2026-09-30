"""Which projects may speak for the user: their extensions, prompt files and skills (audit P7-02).

``<project>/.pi-python/extensions`` ships with the repository, and importing an extension
executes its code the moment a session opens, before the user has typed anything. The
project's ``.pi/SYSTEM.md``, ``.pi/APPEND_SYSTEM.md`` and project-relative skills run nothing,
but they decide what the model is told. Upstream pi puts all of these behind project trust, and
so does this module: they are skipped unless the user vouched for the project, and only
somewhere the project itself cannot write:

- ``[extensions]`` in ``~/.pi-python/agent.toml``: ``trusted_projects`` (a list of absolute
  paths; a directory inside a listed one is trusted too) or the global
  ``trust_project_extensions`` switch. Only the home config is read, never one inside the
  project, or a repository could grant itself trust.
- the ``PI_TRUST_PROJECT_EXTENSIONS`` environment variable, or ``--trust-project-extensions``
  on the command line (the same thing): for headless and CI runs.

``AGENTS.md`` / ``CLAUDE.md`` are not gated (upstream loads them whatever the trust), and
neither is anything the user's own config names by absolute or ``~`` path.

Trust is by path, not by content: an allow-listed project that later gains a new extension
(a ``git pull``) runs it. A prompt or content-hash trust store would close that; this is
the explicit opt-in.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path

from pi_agent_cli.config import (
    CliConfig,
    agent_config_path,
    expand_config_path,
    is_project_relative_path,
)
from pi_agent_cli.context_files import project_append_system_prompt_file, project_system_prompt_file
from pi_agent_core.extensions.loader import SkippedExtensions

logger = logging.getLogger(__name__)

TRUST_ENV = "PI_TRUST_PROJECT_EXTENSIONS"
_TRUTHY = {"1", "true", "yes", "on"}


def project_extensions_trusted(config: CliConfig, cwd: str | Path) -> bool:
    """May this project's own extensions, prompt files and skills be loaded?"""
    if config.trust_project_extensions:
        return True
    if os.environ.get(TRUST_ENV, "").strip().lower() in _TRUTHY:
        return True
    target = _normalize(cwd)
    for entry in config.trusted_projects:
        root = _allow_list_root(entry)
        if root is not None and (target == root or root in target.parents):
            return True
    return False


def skipped_project_resources(config: CliConfig, cwd: str | Path) -> list[Path]:
    """Project files that exist and would have applied, but are ignored: it is untrusted.

    Mirrors what ``load_system_prompt_options`` and ``load_session_resources`` skip, so the
    user is told about exactly what was left out (nothing when the project is trusted, and
    nothing for a prompt the user's own config already overrides).
    """
    if project_extensions_trusted(config, cwd):
        return []
    found: list[Path] = []
    if config.custom_system_prompt is None and not config.custom_system_prompt_file:
        found.append(project_system_prompt_file(cwd))
    if config.append_system_prompt is None and not config.append_system_prompt_file:
        found.append(project_append_system_prompt_file(cwd))
    for item in config.skills_dirs:
        if is_project_relative_path(item):
            found.append(Path(expand_config_path(item, cwd=cwd)))
    return [path for path in found if path.exists()]


def untrusted_project_notice(
    *,
    extensions: Sequence[SkippedExtensions] = (),
    resources: Sequence[Path] = (),
    cwd: str | Path,
    home: str | Path,
) -> str | None:
    """A message telling the user what was skipped and how to enable it (``None``: nothing)."""
    if not extensions and not resources:
        return None
    parts = [f"{item.directory}: {', '.join(item.names)}" for item in extensions]
    parts.extend(str(path) for path in resources)
    # Forward slashes: a raw ``C:\Users\...`` in a TOML double-quoted string is a syntax
    # error, and the user will paste this.
    entry = Path(cwd).as_posix()
    return (
        f"Skipped project resources ({'; '.join(parts)}). Extensions run arbitrary Python when "
        "loaded, and prompt files and skills change what the model is told, and this project "
        "is not trusted. "
        f'To load them, add "{entry}" to `trusted_projects` under `[extensions]` in '
        f"{agent_config_path(home)}, or start the agent with --trust-project-extensions "
        f"(or set {TRUST_ENV}=1)."
    )


def _allow_list_root(entry: str) -> Path | None:
    expanded = os.path.expanduser(entry)
    if not os.path.isabs(expanded):
        # Resolved against the process cwd it would trust whatever directory the user
        # happened to start the agent from.
        logger.warning("trusted_projects entry %r is not an absolute path; ignored", entry)
        return None
    return _normalize(expanded)


def _normalize(path: str | Path) -> Path:
    """Resolve symlinks: what runs lives where the path really points. (``Path`` equality
    already ignores case and slash style on Windows.)"""
    return Path(os.path.realpath(str(path)))
