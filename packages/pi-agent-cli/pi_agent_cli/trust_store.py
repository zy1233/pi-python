"""The user's saved project-trust decisions: ``<home>/agent/trust.json``.

Upstream pi keeps its decisions in ``~/.pi/agent/trust.json``; this is the same place in
pi-python's home. An entry says "I looked at the resources this project ships, and they are
fine": the project's canonical path, the fingerprint of those resources
(``trust_fingerprint``), when the decision was made, and a readable list of what was covered.
The decision holds while the fingerprint still matches. A ``git pull`` that changes an
extension, a prompt file or a skill makes the entry stop applying, and the user is asked again.

The file lives in the user's home, where a repository cannot write, and it fails closed: a
file that is missing, unreadable, not JSON, of the wrong shape or from a newer version grants
nothing, and no entry is ever guessed from a damaged one. Saving is atomic (a temporary file
in the same directory, then ``os.replace``), so a crash cannot leave half a file behind, and a
file that could not be parsed is kept as ``trust.json.corrupt`` instead of being overwritten,
because a hand-edited file with a typo should not cost the user the rest of their entries.

A record covers exactly the directory it was saved for. Unlike upstream, where a decision for
a folder also applies to the folders below it, a fingerprint describes the files of one
directory, so a subdirectory needs a decision of its own. "Trust this whole tree" is what
``trusted_projects`` in ``agent.toml`` is for.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pi_agent_core.home import pi_home

logger = logging.getLogger(__name__)

STORE_VERSION = 1

_LoadState = Literal["ok", "missing", "corrupt", "unreadable"]


@dataclass(frozen=True)
class TrustRecord:
    """One saved decision."""

    project: str
    fingerprint: str
    trusted_at: str = ""
    resources: tuple[str, ...] = ()


def project_key(project: str | Path) -> str:
    """What identifies a project directory: where it really is, spelled canonically.

    Symbolic links are resolved because the files that run are where the link points, and the
    case (on Windows) and the slash style are normalised because they name the same directory.
    """
    return os.path.normcase(os.path.realpath(os.fspath(project)))


class TrustStore:
    """Read and write ``trust.json``. Cheap to construct; every call reads the file afresh, so
    two sessions (or two processes) see each other's decisions."""

    def __init__(self, home: Path | str | None = None) -> None:
        self.path = pi_home(home) / "agent" / "trust.json"

    # -- reading ---------------------------------------------------------------

    def get(self, project: str | Path) -> TrustRecord | None:
        """The saved decision for exactly this directory (``None``: none, or unusable)."""
        key = project_key(project)
        for record in self.records():
            if os.path.normcase(record.project) == key:
                return record
        return None

    def records(self) -> list[TrustRecord]:
        projects, _state = self._load()
        parsed = (_parse_record(path, entry) for path, entry in projects.items())
        return [record for record in parsed if record is not None]

    # -- writing ---------------------------------------------------------------

    def remember(
        self,
        project: str | Path,
        fingerprint: str,
        *,
        resources: Iterable[str] = (),
        now: datetime | None = None,
    ) -> TrustRecord:
        """Save the user's decision to trust *project* with these contents.

        Raises ``OSError`` when the file cannot be read or written; the caller decides what
        that means for the session (the decision still holds until it ends).
        """
        projects = self._load_for_update()
        key = project_key(project)
        # Drop entries for the same directory however they were spelled (case, symlink).
        projects = {p: e for p, e in projects.items() if os.path.normcase(p) != key}
        real = os.path.realpath(os.fspath(project))
        record = TrustRecord(
            project=real,
            fingerprint=fingerprint,
            trusted_at=(now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            resources=tuple(resources),
        )
        projects[real] = {
            "fingerprint": record.fingerprint,
            "trusted_at": record.trusted_at,
            "resources": list(record.resources),
        }
        self._write(projects)
        return record

    def forget(self, project: str | Path) -> bool:
        """Drop the decision for *project*. ``True`` if there was one."""
        projects = self._load_for_update()
        key = project_key(project)
        kept = {p: e for p, e in projects.items() if os.path.normcase(p) != key}
        if len(kept) == len(projects):
            return False
        self._write(kept)
        return True

    # -- the file --------------------------------------------------------------

    def _load(self) -> tuple[dict[str, Any], _LoadState]:
        """The raw ``projects`` table and how far the file could be trusted to be one."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, "missing"
        except UnicodeDecodeError:
            logger.warning("Ignoring %s: it is not UTF-8 text", self.path)
            return {}, "corrupt"
        except OSError as exc:
            logger.warning("Ignoring %s: it cannot be read (%s)", self.path, exc)
            return {}, "unreadable"
        try:
            data = json.loads(text)
        except ValueError:
            logger.warning("Ignoring %s: it is not valid JSON", self.path)
            return {}, "corrupt"
        version = data.get("version") if isinstance(data, dict) else None
        projects = data.get("projects") if isinstance(data, dict) else None
        # ``True == 1``: a hand-edited "version": true must not pass for version 1.
        if type(version) is not int or version != STORE_VERSION or not isinstance(projects, dict):
            logger.warning(
                "Ignoring %s: not a version %d trust file (a newer agent may have written it)",
                self.path,
                STORE_VERSION,
            )
            return {}, "corrupt"
        return projects, "ok"

    def _load_for_update(self) -> dict[str, Any]:
        projects, state = self._load()
        if state == "unreadable":
            # Saving over a file we could not read would destroy whatever is in it.
            raise OSError(f"cannot read the existing {self.path}")
        if state == "corrupt":
            self._keep_aside()
        return projects

    def _keep_aside(self) -> None:
        aside = self.path.with_name(self.path.name + ".corrupt")
        try:
            os.replace(self.path, aside)
        except OSError as exc:
            raise OSError(f"cannot set aside the unusable {self.path}: {exc}") from exc
        logger.warning("Kept the unusable %s as %s", self.path, aside)

    def _write(self, projects: dict[str, Any]) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        data = {"version": STORE_VERSION, "projects": projects}
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        try:
            payload = text.encode("utf-8")
        except UnicodeEncodeError:
            # A path with bytes that are not valid text (possible on POSIX): escape them.
            payload = (json.dumps(data, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
        # ``mkstemp`` creates the file readable by the user alone.
        fd, tmp_name = tempfile.mkstemp(prefix="trust.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


def _parse_record(path: object, entry: object) -> TrustRecord | None:
    if not isinstance(path, str) or not isinstance(entry, dict):
        return None
    fingerprint = entry.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        return None
    trusted_at = entry.get("trusted_at")
    resources = entry.get("resources")
    return TrustRecord(
        project=path,
        fingerprint=fingerprint,
        trusted_at=trusted_at if isinstance(trusted_at, str) else "",
        resources=tuple(item for item in resources if isinstance(item, str))
        if isinstance(resources, list)
        else (),
    )
