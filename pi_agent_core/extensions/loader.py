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
        bridge: Any = None,
    ) -> ExtensionAPI:
        """Load a single extension from an ``activate`` callable.

        Args:
            bridge: Optional ``HarnessBridge`` to bind *before* ``activate()``
                so the extension can call ``pi.cwd`` etc. during init.
        """
        ext_name = name or getattr(activate, "__module__", None) or "anonymous"
        old_api: ExtensionAPI | None = None
        if ext_name in self._loaded_names:
            logger.debug("Extension %r already loaded — overriding", ext_name)
            old_api = next((a for a in self._apis if a.extension_name == ext_name), None)

        meta = ExtensionMeta(name=ext_name, source=source)
        api = ExtensionAPI(registry=self.registry, meta=meta)

        # P7-03: bind bridge BEFORE activate so pi.cwd etc. are usable
        if bridge is not None:
            api._set_bridge(bridge)

        # Snapshot before any mutation so failure can roll back completely
        snap = self.registry.snapshot()
        old_apis_copy = list(self._apis)
        old_names_copy = set(self._loaded_names)

        # Remove old registrations (registry + harness live state)
        if old_api is not None:
            self.registry.remove_by_extension(ext_name)
            if bridge is not None:
                self._purge_live_harness(bridge, ext_name, snap)
            self._apis = [a for a in self._apis if a.extension_name != ext_name]
            self._loaded_names.discard(ext_name)

        try:
            activate(api)
        except Exception:
            # Roll back to state before override attempt
            self.registry.restore(snap)
            self._apis = old_apis_copy
            self._loaded_names = old_names_copy
            logger.error("Extension %r failed during activate()", ext_name, exc_info=True)
            raise

        api._loading = False  # enable dynamic register_tool → bridge injection
        self._apis.append(api)
        self._loaded_names.add(ext_name)
        return api

    def load_all(
        self,
        *,
        extra: list[ActivateFn] | None = None,
        cwd: str | None = None,
        auto_discover: bool = True,
        bridge: Any = None,
    ) -> list[ExtensionAPI]:
        """Discover and load all extensions.  Returns the list of ``ExtensionAPI`` instances."""
        import contextlib

        callables: list[tuple[ActivateFn, str]] = []
        if auto_discover:
            for fn in self.discover_entry_points():
                callables.append((fn, "entry_point"))
            for fn in self.discover_default_dirs(cwd):
                callables.append((fn, "directory"))
        for fn in extra or []:
            callables.append((fn, "programmatic"))

        for activate, source in callables:
            with contextlib.suppress(Exception):
                self.load_callable(activate, source=source, bridge=bridge)

        return list(self._apis)

    @staticmethod
    def _purge_live_harness(bridge: Any, ext_name: str, snap: dict[str, Any]) -> None:
        """Remove old extension's tools and hooks from the live harness.

        Compares the snapshot (before removal) with what the extension owned
        and calls bridge methods to clean up runtime state.
        """
        import contextlib

        old_tool_owners = snap.get("tool_owners", {})
        for tool_name, owner in old_tool_owners.items():
            if owner == ext_name:
                with contextlib.suppress(Exception):
                    bridge.remove_tool(tool_name)

        old_handlers = snap.get("event_handlers", {})
        for event, registrations in old_handlers.items():
            for reg in registrations:
                if getattr(reg, "extension_name", None) == ext_name:
                    with contextlib.suppress(Exception):
                        bridge.remove_hook(event, reg.handler)

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
