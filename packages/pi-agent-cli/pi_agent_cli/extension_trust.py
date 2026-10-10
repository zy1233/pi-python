"""Which projects may speak for the user: their extensions, saved workflows, prompt files and
skills (audit P7-02, F7-01).

``<project>/.pi-python/extensions`` ships with the repository, and importing an extension
executes its code the moment a session opens, before the user has typed anything. The
project's ``.pi/SYSTEM.md``, ``.pi/APPEND_SYSTEM.md`` and project-relative skills run nothing,
but they decide what the model is told. Upstream pi puts all of these behind project trust, and
so does this module: they are skipped unless the user vouched for the project, and only
somewhere the project itself cannot write. So are the saved workflows in
``<project>/.pi-python/workflows``: the dynamic-workflows extension makes each script a slash
command with a description the repository wrote, and has the model run it. In order of
strength:

1. ``--trust-project-extensions`` on the command line, or the ``PI_TRUST_PROJECT_EXTENSIONS``
   environment variable (the same thing): for headless and CI runs.
2. ``[extensions]`` in ``~/.pi-python/agent.toml``: ``trusted_projects`` (a list of absolute
   paths; a directory inside a listed one is trusted too) and ``default_project_trust =
   "always"`` (or the older global ``trust_project_extensions`` switch). Only the home config is
   read, never one inside the project, or a repository could grant itself trust.
3. A decision the user made in a prompt and had remembered (``trust.json``, see
   ``trust_store``). It is bound to a fingerprint of the files it was made about
   (``trust_fingerprint``), so it holds until one of them changes: a ``git pull`` that adds an
   extension asks again instead of running it.
4. Otherwise ``default_project_trust``: ``ask`` (the default: an ACP client is asked, a
   headless run leaves the resources out) or ``never`` (leave them out, ask nothing).

``AGENTS.md`` / ``CLAUDE.md`` are not gated (upstream loads them whatever the trust), and
neither is anything the user's own config names by absolute or ``~`` path.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pi_agent_cli.config import (
    CliConfig,
    DefaultProjectTrust,
    agent_config_path,
)
from pi_agent_cli.trust_fingerprint import (
    GatedResource,
    fingerprint_resources,
    gated_project_resources,
)
from pi_agent_cli.trust_store import TrustStore
from pi_agent_core.extensions.loader import SkippedExtensions

logger = logging.getLogger(__name__)

TRUST_ENV = "PI_TRUST_PROJECT_EXTENSIONS"
_TRUTHY = {"1", "true", "yes", "on"}

# Why a project is, or is not, trusted.
TrustReason = Literal[
    "override",  # --trust-project-extensions / PI_TRUST_PROJECT_EXTENSIONS
    "always",  # default_project_trust = "always" (or the older global switch)
    "allow-list",  # trusted_projects
    "saved",  # remembered from a prompt, and the files are unchanged
    "nothing",  # the project ships nothing that needs trust
    "undecided",  # nobody has decided yet
    "changed",  # remembered once, but a file has changed since
    "never",  # default_project_trust = "never"
    "unpinnable",  # too many / too big / unreadable to fingerprint, so it cannot be remembered
]
_TRUSTED: frozenset[str] = frozenset({"override", "always", "allow-list", "saved"})
_ASKABLE: frozenset[str] = frozenset({"undecided", "changed"})

# Why an untrusted project's resources were left out, for the notice the user is shown.
# The first, second, fifth and sixth come from the decision itself; the rest are what
# happened once the user was asked (or could not be).
NoticeReason = Literal[
    "undecided", "changed", "modified", "declined", "unasked", "never", "unpinnable"
]


@dataclass(frozen=True)
class ProjectTrustDecision:
    """What was decided about a project before anyone was asked.

    ``resources`` are the things the project asks to be trusted for (empty when configuration
    already trusts it, since nothing then needs looking at). ``fingerprint`` pins their
    content: it is what a prompt's answer is remembered against (``None``: not computed, or
    ``unpinnable``).
    """

    reason: TrustReason
    resources: tuple[GatedResource, ...] = ()
    fingerprint: str | None = None

    @property
    def trusted(self) -> bool:
        return self.reason in _TRUSTED

    @property
    def can_ask(self) -> bool:
        """May the user be offered the choice? (Undecided or changed, with something to pin
        the answer to; ``never`` and ``unpinnable`` are settled without asking.)"""
        return self.reason in _ASKABLE


class ProjectTrust:
    """Whether one session may load its project's own resources.

    It starts as the decision said and can change once, from untrusted to trusted, when the
    user answers the prompt. Everything that reads resources on the session's behalf (the
    system prompt, skills, extensions) asks this object instead of deciding for itself, so
    they agree, and so the answer takes effect without rebuilding the session.
    """

    def __init__(self, decision: ProjectTrustDecision | None = None) -> None:
        self.decision = decision
        self.trusted = decision.trusted if decision is not None else False

    def grant(self) -> None:
        self.trusted = True


def effective_default_trust(config: CliConfig) -> DefaultProjectTrust:
    """What to do about a project nobody has vouched for.

    ``default_project_trust`` if the user set it; otherwise ``always`` when the older global
    ``trust_project_extensions`` switch is on, and ``ask`` if not. An explicit setting wins
    over the older switch: someone who wrote it meant it.
    """
    if config.default_project_trust is not None:
        return config.default_project_trust
    return "always" if config.trust_project_extensions else "ask"


def project_extensions_trusted(config: CliConfig, cwd: str | Path) -> bool:
    """Is this project trusted by the command line or the user's config alone?

    The cheap check: no files are read. A decision remembered from a prompt is not consulted
    here; it is bound to the project's contents and belongs to ``decide_project_trust``.
    """
    return _configured_trust(config, cwd) is not None


def decide_project_trust(
    config: CliConfig,
    cwd: str | Path,
    *,
    home: Path | str | None = None,
    store: TrustStore | None = None,
) -> ProjectTrustDecision:
    """Decide how far this project is trusted, without asking anyone.

    Reads and hashes files, so call it off the event loop. Configuration that trusts the
    project short-circuits before any of that.
    """
    configured = _configured_trust(config, cwd)
    if configured is not None:
        return ProjectTrustDecision(reason=configured)

    resources = tuple(gated_project_resources(config, cwd, home=home))
    if not resources:
        return ProjectTrustDecision(reason="nothing")
    fingerprint = fingerprint_resources(resources)
    if fingerprint is None:
        return ProjectTrustDecision(reason="unpinnable", resources=resources)

    record = (store or TrustStore(home)).get(cwd)
    if record is not None and record.fingerprint == fingerprint:
        return ProjectTrustDecision("saved", resources, fingerprint)
    if effective_default_trust(config) == "never":
        return ProjectTrustDecision("never", resources, fingerprint)
    return ProjectTrustDecision(
        "changed" if record is not None else "undecided", resources, fingerprint
    )


def skipped_project_resources(
    config: CliConfig,
    cwd: str | Path,
    *,
    trusted: bool | None = None,
    home: Path | str | None = None,
) -> list[Path]:
    """Project saved workflows, prompt files and skills that exist and would have applied, but
    are ignored.

    Mirrors what the extension that reads the workflows, ``load_system_prompt_options`` and
    ``load_session_resources`` skip, so the user is told about exactly what was left out
    (nothing when the project is trusted, and nothing for a prompt the user's own config
    already overrides). The extensions are reported by the loader that skipped them. Pass
    *trusted* when the session knows better than the configuration does (the user said yes in
    a prompt), and *home* when the session runs with a pi home other than the default (it
    tells the project's own directories from the user's).
    """
    if trusted is None:
        trusted = project_extensions_trusted(config, cwd)
    if trusted:
        return []
    return [
        r.path for r in gated_project_resources(config, cwd, home=home) if r.kind != "extensions"
    ]


_WHY: dict[str, str] = {
    "undecided": "this project is not trusted",
    "changed": "this project has changed since you last trusted it",
    "modified": (
        "this project's files changed while you were being asked, so your answer was not applied"
    ),
    "declined": "you chose not to trust this project",
    "unasked": "this project is not trusted, and the client could not be asked about it",
    "never": "`default_project_trust` is set to `never`",
    "unpinnable": (
        "this project is not trusted, and its files are too many, too large or unreadable "
        "to be checked, so a decision about them cannot be remembered"
    ),
}


def notice_reason(decision: ProjectTrustDecision) -> NoticeReason | None:
    """Why the resources of a project that is not trusted (yet) are being left out.

    As far as the decision alone can say: ``None`` when it does not explain anything (the
    project is trusted, or ships nothing that needs trust).
    """
    if decision.reason in ("undecided", "changed", "never", "unpinnable"):
        return decision.reason
    return None


def unsaved_trust_notice(*, error: str, cwd: str | Path, home: str | Path) -> str:
    """Tell the user their answer applies to this session only, and what to do about it."""
    entry = Path(cwd).as_posix()
    return (
        f"Trusted for this session, but your decision could not be saved ({error}), so you will "
        "be asked again next time. To stop that, add "
        f'"{entry}" to `trusted_projects` under `[extensions]` in {agent_config_path(home)}.'
    )


def untrusted_project_notice(
    *,
    extensions: Sequence[SkippedExtensions] = (),
    resources: Sequence[Path] = (),
    cwd: str | Path,
    home: str | Path,
    why: NoticeReason | None = None,
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
        "loaded, saved workflows add commands that have the model run their scripts, and "
        "prompt files and skills change what the model is told, and "
        f"{_WHY[why or 'undecided']}. "
        f'To load them, add "{entry}" to `trusted_projects` under `[extensions]` in '
        f"{agent_config_path(home)}, or start the agent with --trust-project-extensions "
        f"(or set {TRUST_ENV}=1)."
    )


def _configured_trust(
    config: CliConfig, cwd: str | Path
) -> Literal["override", "always", "allow-list"] | None:
    if os.environ.get(TRUST_ENV, "").strip().lower() in _TRUTHY:
        return "override"
    if effective_default_trust(config) == "always":
        return "always"
    target = _normalize(cwd)
    for entry in config.trusted_projects:
        root = _allow_list_root(entry)
        if root is not None and (target == root or root in target.parents):
            return "allow-list"
    return None


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
