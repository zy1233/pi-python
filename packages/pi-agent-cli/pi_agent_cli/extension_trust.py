"""Which projects may load their own extensions (audit P7-02).

``<project>/.pi-python/extensions`` ships with the repository, and importing an extension
executes its code the moment a session opens, before the user has typed anything. So a
project's extensions are skipped unless the user vouched for the project, and only
somewhere the project itself cannot write:

- ``[extensions]`` in ``~/.pi-python/agent.toml``: ``trusted_projects`` (a list of absolute
  paths; a directory inside a listed one is trusted too) or the global
  ``trust_project_extensions`` switch. Only the home config is read, never one inside the
  project, or a repository could grant itself trust.
- the ``PI_TRUST_PROJECT_EXTENSIONS`` environment variable, or ``--trust-project-extensions``
  on the command line (the same thing): for headless and CI runs.

Trust is by path, not by content: an allow-listed project that later gains a new extension
(a ``git pull``) runs it. A prompt or content-hash trust store would close that; this is
the explicit opt-in.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path

from pi_agent_cli.config import CliConfig, agent_config_path
from pi_agent_core.extensions.loader import SkippedExtensions

logger = logging.getLogger(__name__)

TRUST_ENV = "PI_TRUST_PROJECT_EXTENSIONS"
_TRUTHY = {"1", "true", "yes", "on"}


def project_extensions_trusted(config: CliConfig, cwd: str | Path) -> bool:
    """May ``<cwd>/.pi-python/extensions`` be imported, i.e. executed?"""
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


def skipped_extensions_notice(
    skipped: Sequence[SkippedExtensions], *, cwd: str | Path, home: str | Path
) -> str | None:
    """A message telling the user what was skipped and how to enable it (``None``: nothing)."""
    if not skipped:
        return None
    listing = "; ".join(f"{item.directory}: {', '.join(item.names)}" for item in skipped)
    # Forward slashes: a raw ``C:\Users\...`` in a TOML double-quoted string is a syntax
    # error, and the user will paste this.
    entry = Path(cwd).as_posix()
    return (
        f"Skipped project extensions ({listing}). They run arbitrary Python when loaded, "
        "and this project is not trusted. "
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
