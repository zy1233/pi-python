"""What a project asks to be trusted for, and a fingerprint that pins it (audit P7-02).

Four things a repository ships can act on the user's behalf, and each is gated behind project
trust (see ``extension_trust``):

- ``<project>/.pi-python/extensions``: Python that is *imported*, so it runs, the moment a
  session opens;
- ``<project>/.pi-python/workflows``: scripts the dynamic-workflows extension turns into slash
  commands (with a description the repository wrote) and has the model run as sub-agents;
- ``<project>/.pi/SYSTEM.md`` and ``.pi/APPEND_SYSTEM.md``: they decide what the model is told;
- the project-relative entries of ``[skills].paths``: skill text goes into the system prompt,
  and a skill may ship scripts the model is told to run.

``gated_project_resources`` finds exactly what the rest of the CLI would load, so nothing the
user is asked about is left out and nothing they are asked about is a no-op.
``fingerprint_resources`` condenses those files into one digest. A saved decision is bound to
that digest (``trust_store``), so a ``git pull`` that changes any of the files ends the trust
and the user is asked again, where trust bound to a path alone would run the new code unasked.

The fingerprint covers the *whole* tree of each resource, not only the files the loader
imports: an extension imports its helpers, and a skill directory may hold scripts. It hashes
bytes exactly as they are (a rewritten line ending is a change; being asked again costs a
click) and names each file by its place *inside* its resource, so the digest does not depend on
where the project is checked out. ``__pycache__`` (Python writes it beside an extension the
moment it is imported, which must not undo the trust that allowed the import) and ``.git`` are
left out.

Content that cannot be pinned has no fingerprint (``None``) and so can never be remembered: a
tree too large to read in a moment, or a file that cannot be read. Symbolic links are followed,
because what runs is where the link points, with a guard against loops. Only regular files are
opened; a named pipe would block the reader forever.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pi_agent_cli.config import CliConfig, expand_config_path, is_project_relative_path
from pi_agent_cli.context_files import (
    APPEND_SYSTEM_PROMPT_FILENAMES,
    SYSTEM_PROMPT_FILENAMES,
    project_append_system_prompt_file,
    project_system_prompt_file,
)
from pi_agent_core.extensions.loader import extension_module_names
from pi_agent_core.home import pi_home

ResourceKind = Literal["extensions", "workflows", "prompt", "skills"]

# A project's own extensions, workflows and skills are a handful of small files. These bounds keep a
# hostile or accidental tree (a whole checkout named as a skills directory) from stalling
# session start; past them there is simply no fingerprint. Directories count as entries too.
MAX_ENTRIES = 2000
MAX_BYTES = 64 * 1024 * 1024

# Metadata that is not project content and changes without the project changing.
_SKIPPED_NAMES = frozenset({"__pycache__", ".git"})
_CHUNK = 1024 * 1024
_EXTENSIONS_LABEL = ".pi-python/extensions"
_WORKFLOWS_LABEL = ".pi-python/workflows"


@dataclass(frozen=True)
class GatedResource:
    """One thing in the project that needs the user's trust before it takes effect.

    ``label`` is its identity, independent of where the project is (``.pi/SYSTEM.md``, or the
    skills entry exactly as configured); ``detail`` is only for people (the extension modules
    it holds).
    """

    kind: ResourceKind
    label: str
    path: Path
    detail: str = ""

    def describe(self) -> str:
        text = f"{self.kind}: {self.label}"
        return f"{text} ({self.detail})" if self.detail else text


def gated_project_resources(
    config: CliConfig, cwd: str | Path, *, home: Path | str | None = None
) -> list[GatedResource]:
    """Everything in this project that would apply if it were trusted, in a fixed order:
    extensions, then saved workflows, then prompt files, then skills. ``[]``: the project asks
    for nothing.

    Mirrors what the loaders skip for an untrusted project. A prompt the user's own config
    already sets is not listed (the project's file would never be read), nor is a skills entry
    that is absolute or ``~``-relative (that is the user's), nor the user's own extensions or
    workflows directory (a session run from the home directory sees them as the project's too).
    """
    project = Path(cwd).resolve()
    found: list[GatedResource] = []

    extensions = project / ".pi-python" / "extensions"
    if not _same_directory(extensions, pi_home(home) / "extensions"):
        names = extension_module_names(extensions)
        if names:
            found.append(
                GatedResource("extensions", _EXTENSIONS_LABEL, extensions, ", ".join(names))
            )

    workflows = project / ".pi-python" / "workflows"
    if not _same_directory(workflows, pi_home(home) / "workflows"):
        scripts = _workflow_script_names(workflows)
        if scripts:
            found.append(
                GatedResource("workflows", _WORKFLOWS_LABEL, workflows, ", ".join(scripts))
            )

    if config.custom_system_prompt is None and not config.custom_system_prompt_file:
        system = project_system_prompt_file(project)
        if system.is_file():
            found.append(GatedResource("prompt", SYSTEM_PROMPT_FILENAMES[0][0], system))
    if config.append_system_prompt is None and not config.append_system_prompt_file:
        append = project_append_system_prompt_file(project)
        if append.is_file():
            found.append(GatedResource("prompt", APPEND_SYSTEM_PROMPT_FILENAMES[0][0], append))

    for entry in config.skills_dirs:
        if not is_project_relative_path(entry):
            continue
        path = Path(expand_config_path(entry, cwd=project))
        if path.exists():
            found.append(GatedResource("skills", entry, path))
    return found


def fingerprint_resources(
    resources: Sequence[GatedResource],
    *,
    max_entries: int = MAX_ENTRIES,
    max_bytes: int = MAX_BYTES,
) -> str | None:
    """``sha256:<hex>`` over the contents of *resources*; ``None`` if they cannot be pinned.

    Two sets of resources have the same fingerprint exactly when they hold the same files with
    the same bytes under the same names inside the same-labelled resources.
    """
    budget = _Budget(entries=max_entries, bytes=max_bytes)
    records: list[tuple[str, str, str, str]] = []
    try:
        for resource in resources:
            for relative, digest in _digests(resource.path, budget):
                records.append((resource.kind, resource.label, relative, digest))
    except (_Unfingerprintable, OSError):
        return None
    overall = hashlib.sha256(b"pi-python project trust v1\n")
    for record in sorted(records):
        # JSON keeps every field unambiguous whatever characters a file name holds.
        overall.update(json.dumps(record).encode("ascii") + b"\n")
    return f"sha256:{overall.hexdigest()}"


# ---------------------------------------------------------------------------


class _Unfingerprintable(Exception):
    """The contents cannot be pinned: too large, or something in them cannot be read."""


@dataclass
class _Budget:
    entries: int
    bytes: int

    def take_entry(self) -> None:
        self.entries -= 1
        if self.entries < 0:
            raise _Unfingerprintable("too many files")


def _digests(root: Path, budget: _Budget) -> list[tuple[str, str]]:
    """``(path inside the resource, digest)`` for everything in one resource."""
    mode = os.stat(root).st_mode  # follows a link; raises for a resource that has vanished
    if stat.S_ISREG(mode):
        budget.take_entry()
        return [("", _file_digest(str(root), budget))]
    if not stat.S_ISDIR(mode):
        raise _Unfingerprintable("not a file or a directory")

    found: list[tuple[str, str]] = []
    # Each directory carries the real paths of the directories above it, so a link back up
    # the tree ends the walk instead of repeating it until the budget runs out.
    pending: list[tuple[str, str, frozenset[str]]] = [(str(root), "", frozenset())]
    while pending:
        directory, prefix, ancestors = pending.pop()
        real = os.path.realpath(directory)
        if real in ancestors:
            continue
        ancestors = ancestors | {real}
        with os.scandir(directory) as listing:
            entries = list(listing)
        for entry in entries:
            if entry.name in _SKIPPED_NAMES:
                continue
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            budget.take_entry()
            try:
                mode = entry.stat().st_mode  # follows a link: what is there is what counts
            except FileNotFoundError:
                continue  # a link to nothing (yet); the digest changes when something appears
            if stat.S_ISDIR(mode):
                pending.append((entry.path, relative, ancestors))
            elif stat.S_ISREG(mode):
                found.append((relative, _file_digest(entry.path, budget)))
            # Anything else is a pipe, a socket or a device: not content an extension or a
            # skill is made of, and reading it could block for ever.
    return found


def _file_digest(path: str, budget: _Budget) -> str:
    hexdigest, size = _hash_file(path, budget.bytes)
    budget.bytes -= size
    return hexdigest


def _hash_file(path: str, limit: int) -> tuple[str, int]:
    """``(sha256 hex, size)``; refuses to read more than *limit* bytes."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            size += len(chunk)
            if size > limit:
                raise _Unfingerprintable("too many bytes")
            digest.update(chunk)
    return digest.hexdigest(), size


def _same_directory(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _workflow_script_names(directory: Path) -> tuple[str, ...]:
    """The scripts in a saved-workflows directory, as the workflow store reads them: the
    ``*.py`` files directly inside it (what is below, or not a file, it never opens)."""
    try:
        return tuple(sorted(path.name for path in directory.glob("*.py") if path.is_file()))
    except OSError:
        return ()
