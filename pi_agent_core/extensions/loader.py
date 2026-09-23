"""Extension discovery and loading.

Three discovery mechanisms (evaluated in order):

1. **entry_points** — ``[project.entry-points."pi_agent.extensions"]``
   in installed packages (standard setuptools mechanism).
2. **Directory scan** — ``~/.pi-python/extensions/`` (user) and
   ``.pi-python/extensions/`` (project-local).
3. **Programmatic** — ``load_callable(activate_fn)`` for tests / embedding.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pi_agent_core.extensions.api import ExtensionAPI
from pi_agent_core.extensions.registry import ExtensionRegistry
from pi_agent_core.extensions.types import ExtensionMeta

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "pi_agent.extensions"

ActivateFn = Callable[[ExtensionAPI], None]


class ExtensionLoader:
    """Discovers and loads extensions into a shared ``ExtensionRegistry``."""

    def __init__(self, registry: ExtensionRegistry | None = None) -> None:
        self.registry = registry or ExtensionRegistry()
        self._apis: list[ExtensionAPI] = []
        self._loaded_names: set[str] = set()

    # -- public API ----------------------------------------------------------

    def discover_entry_points(self) -> list[ActivateFn]:
        """Discover extensions declared via ``entry_points``."""
        results: list[ActivateFn] = []
        try:
            if sys.version_info >= (3, 12):
                from importlib.metadata import entry_points

                eps = entry_points(group=ENTRY_POINT_GROUP)
            else:
                from importlib.metadata import entry_points

                all_eps = entry_points()
                eps = all_eps.get(ENTRY_POINT_GROUP, [])  # type: ignore[union-attr]
        except Exception:
            logger.debug("entry_points discovery unavailable", exc_info=True)
            return results

        for ep in eps:
            try:
                obj = ep.load()
                activate = _resolve_activate(obj)
                if activate is not None:
                    results.append(activate)
                    logger.debug("Discovered entry_point extension: %s", ep.name)
            except Exception:
                logger.warning("Failed to load entry_point %s", ep.name, exc_info=True)
        return results

    def discover_directory(self, directory: str | Path) -> list[ActivateFn]:
        """Scan a directory for Python extension modules."""
        results: list[ActivateFn] = []
        path = Path(directory)
        if not path.is_dir():
            return results

        for item in sorted(path.iterdir()):
            activate: ActivateFn | None = None
            if item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
                activate = _load_module_from_file(item)
            elif item.is_dir() and (item / "__init__.py").is_file():
                activate = _load_module_from_file(item / "__init__.py", package_name=item.name)
            if activate is not None:
                results.append(activate)
                logger.debug("Discovered directory extension: %s", item.name)
        return results

    def discover_default_dirs(self, cwd: str | None = None) -> list[ActivateFn]:
        """Scan the standard extension directories."""
        results: list[ActivateFn] = []
        home_ext = Path.home() / ".pi-python" / "extensions"
        results.extend(self.discover_directory(home_ext))
        if cwd:
            project_ext = Path(cwd) / ".pi-python" / "extensions"
            results.extend(self.discover_directory(project_ext))
        return results

    def load_callable(
        self,
        activate: ActivateFn,
        *,
        name: str | None = None,
        source: str = "programmatic",
    ) -> ExtensionAPI:
        """Load a single extension from an ``activate`` callable."""
        ext_name = name or getattr(activate, "__module__", None) or "anonymous"
        if ext_name in self._loaded_names:
            logger.debug("Extension %r already loaded — skipping", ext_name)
            existing = next((a for a in self._apis if a.extension_name == ext_name), None)
            if existing is not None:
                return existing
        meta = ExtensionMeta(name=ext_name, source=source)
        api = ExtensionAPI(registry=self.registry, meta=meta)
        try:
            activate(api)
        except Exception:
            logger.error("Extension %r failed during activate()", ext_name, exc_info=True)
            raise
        self._apis.append(api)
        self._loaded_names.add(ext_name)
        return api

    def load_all(
        self,
        *,
        extra: list[ActivateFn] | None = None,
        cwd: str | None = None,
        auto_discover: bool = True,
    ) -> list[ExtensionAPI]:
        """Discover and load all extensions.  Returns the list of ``ExtensionAPI`` instances."""
        callables: list[tuple[ActivateFn, str]] = []
        if auto_discover:
            for fn in self.discover_entry_points():
                callables.append((fn, "entry_point"))
            for fn in self.discover_default_dirs(cwd):
                callables.append((fn, "directory"))
        for fn in extra or []:
            callables.append((fn, "programmatic"))

        for activate, source in callables:
            import contextlib

            with contextlib.suppress(Exception):
                self.load_callable(activate, source=source)

        return list(self._apis)

    @property
    def apis(self) -> list[ExtensionAPI]:
        return list(self._apis)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_activate(obj: Any) -> ActivateFn | None:
    """Given a loaded entry-point object, find the ``activate`` callable."""
    if callable(obj) and getattr(obj, "__name__", "") == "activate":
        return obj  # type: ignore[return-value]
    if hasattr(obj, "activate") and callable(obj.activate):
        return obj.activate  # type: ignore[return-value]
    if callable(obj):
        return obj  # type: ignore[return-value]
    return None


def _load_module_from_file(
    path: Path,
    package_name: str | None = None,
) -> ActivateFn | None:
    """Import a Python file and return its ``activate`` function (if any)."""
    module_name = f"_pi_ext_{package_name or path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return _resolve_activate(module)
    except Exception:
        logger.warning("Failed to import extension from %s", path, exc_info=True)
        return None
