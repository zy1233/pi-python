"""Saved workflow discovery, storage, and meta extraction.

Scans user-level (``~/.pi-python/workflows/``) and project-level
(``.pi-python/workflows/``) directories for ``.py`` scripts, extracts
``meta`` dicts via ``ast.literal_eval`` (no script execution), and
exposes them for slash-command registration.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_SCRIPT_BYTES = 256 * 1024  # 256 KB


def extract_meta(script: str) -> dict[str, Any]:
    """Extract the top-level ``meta`` dict from a workflow script.

    Uses ``ast.literal_eval`` on the assignment value so no code is executed.
    Returns an empty dict if ``meta`` is absent or unparseable.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "meta":
                try:
                    return ast.literal_eval(node.value)  # type: ignore[arg-type]
                except (ValueError, TypeError):
                    return {}
    return {}


@dataclass
class SavedWorkflow:
    name: str
    description: str
    when_to_use: str | None
    source: Literal["builtin", "user", "project"]
    path: Path | None
    script: str


class WorkflowStore:
    """Discover and manage saved workflow scripts."""

    def __init__(self, cwd: str = ".", home: Path | None = None) -> None:
        self._cwd = cwd
        self._home = home or Path.home()

    def _user_dir(self) -> Path:
        return self._home / ".pi-python" / "workflows"

    def _project_dir(self) -> Path:
        return Path(self._cwd) / ".pi-python" / "workflows"

    def scan(self) -> list[SavedWorkflow]:
        """Scan user and project directories. Project overrides user on name collision."""
        results: list[SavedWorkflow] = []
        seen: set[str] = set()

        for directory, source in [
            (self._project_dir(), "project"),
            (self._user_dir(), "user"),
        ]:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.py")):
                wf = self._load_one(path, source)
                if wf is None:
                    continue
                if wf.name in seen:
                    continue
                results.append(wf)
                seen.add(wf.name)
        return results

    def resolve(self, name: str) -> SavedWorkflow | None:
        """Find a saved workflow by name."""
        for wf in self.scan():
            if wf.name == name:
                return wf
        return None

    def save_project(self, name: str, script: str) -> Path:
        """Atomically save a workflow script to the project directory."""
        return self._save(self._project_dir(), name, script)

    def save_user(self, name: str, script: str) -> Path:
        """Atomically save a workflow script to the user directory."""
        return self._save(self._user_dir(), name, script)

    def _save(self, directory: Path, name: str, script: str) -> Path:
        if not NAME_RE.match(name):
            raise ValueError(f"Invalid workflow name {name!r}: must match [a-z0-9-]{{1,64}}")
        if len(script.encode()) > MAX_SCRIPT_BYTES:
            raise ValueError(f"Script exceeds {MAX_SCRIPT_BYTES // 1024}KB limit")
        dest = directory / f"{name}.py"
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(directory), suffix=".py.tmp")
        fd_closed = False
        try:
            os.write(fd, script.encode("utf-8"))
            os.close(fd)
            fd_closed = True
            # Atomic no-clobber: os.link fails with OSError when dest
            # already exists, closing the TOCTOU window that existed
            # with the prior dest.exists() + os.replace() sequence.
            os.link(tmp, str(dest))
        except OSError as exc:
            if dest.exists():
                raise FileExistsError(f"Workflow {name!r} already exists at {dest}") from exc
            raise
        except BaseException:
            if not fd_closed:
                with contextlib.suppress(OSError):
                    os.close(fd)
            raise
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        return dest

    def _load_one(self, path: Path, source: str) -> SavedWorkflow | None:
        try:
            if path.stat().st_size > MAX_SCRIPT_BYTES:
                logger.debug("Skipping %s: exceeds size limit", path)
                return None
            script = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Cannot read %s: %s", path, exc)
            return None

        meta = extract_meta(script)
        name = meta.get("name") or path.stem
        if not NAME_RE.match(name):
            logger.debug("Skipping %s: invalid name %r", path, name)
            return None

        return SavedWorkflow(
            name=name,
            description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use"),
            source=source,  # type: ignore[arg-type]
            path=path,
            script=script,
        )
