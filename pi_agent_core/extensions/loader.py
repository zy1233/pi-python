"""Extension discovery and loading.

Three discovery mechanisms (evaluated in order):

1. **entry_points** — ``[project.entry-points."pi_agent.extensions"]``
   in installed packages (standard setuptools mechanism).
2. **Directory scan** — ``<home>/extensions/`` (user; ``home`` is ``~/.pi-python`` unless
   ``PI_HOME`` or the ``home`` argument says otherwise) and ``.pi-python/extensions/``
   (project-local). Importing an extension executes its code, and the project directory
   ships with the repository, so it is scanned only when the caller says the project is
   trusted (``trust_project``); otherwise nothing in it is imported and what was skipped
   is reported in ``ExtensionLoader.skipped``.
3. **Programmatic** — ``load_callable(activate_fn)`` for tests / embedding.

An extension that fails — the module raises as it is imported, an entry point resolves to
nothing, or ``activate()`` raises — is logged and recorded in ``ExtensionLoader.failed``;
the others still load.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_agent_core.extensions.api import ExtensionAPI
from pi_agent_core.extensions.registry import ExtensionRegistry
from pi_agent_core.extensions.types import ExtensionMeta
from pi_agent_core.home import pi_home

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "pi_agent.extensions"

ActivateFn = Callable[[ExtensionAPI], None]


@dataclass(frozen=True)
class SkippedExtensions:
    """Extensions found in a directory that was deliberately not imported."""

    directory: Path
    names: tuple[str, ...]


@dataclass(frozen=True)
class FailedExtension:
    """An extension that failed to load. It was rolled back; the others still loaded.

    ``name`` is what identifies it at the stage it failed: the entry-point name, the file or
    package name in a scanned directory, or (once its module imported and ``activate()``
    raised) the extension's own name. ``source`` is ``entry_point``, ``directory`` or
    ``programmatic``; ``error`` reads ``ExceptionType: message``.
    """

    name: str
    source: str
    error: str


class ExtensionLoader:
    """Discovers and loads extensions into a shared ``ExtensionRegistry``.

    ``home`` is the pi-python home directory (``pi_home()`` when omitted): its
    ``extensions/`` subdirectory is the user's extension directory, and every extension
    sees it as ``ExtensionAPI.home``.
    """

    def __init__(
        self,
        registry: ExtensionRegistry | None = None,
        *,
        home: Path | str | None = None,
    ) -> None:
        self.registry = registry or ExtensionRegistry()
        self._home = home
        self._apis: list[ExtensionAPI] = []
        self._loaded_names: set[str] = set()
        self._activations: dict[str, ActivateFn] = {}
        self.skipped: list[SkippedExtensions] = []
        self.failed: list[FailedExtension] = []

    @property
    def home(self) -> Path:
        """The pi-python home directory this loader (and its extensions) work under."""
        return pi_home(self._home)

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
                activate = _resolve_activate(ep.load())
            except Exception as exc:
                logger.warning("Failed to load entry_point %s", ep.name, exc_info=True)
                self._record_failure(ep.name, "entry_point", _describe(exc))
                continue
            if activate is None:
                # Declaring an entry point claims to be an extension; do not drop it silently.
                logger.warning("Entry point %s does not resolve to an activate() function", ep.name)
                self._record_failure(ep.name, "entry_point", "no activate() function found")
                continue
            results.append(activate)
            logger.debug("Discovered entry_point extension: %s", ep.name)
        return results

    def discover_directory(self, directory: str | Path) -> list[ActivateFn]:
        """Scan a directory for Python extension modules.

        A module that raises as it is imported is logged and recorded in ``failed``; the
        others are still found. (A file without an ``activate`` is not an error: helper
        modules live next to extensions. Prefix them with ``_`` to keep them unscanned.)
        """
        results: list[ActivateFn] = []
        for name, module_file, package_name in _extension_sources(Path(directory)):
            try:
                activate = _load_module_from_file(module_file, package_name=package_name)
            except Exception as exc:
                logger.warning("Failed to import extension from %s", module_file, exc_info=True)
                self._record_failure(name, "directory", _describe(exc))
                continue
            if activate is not None:
                results.append(activate)
                logger.debug("Discovered directory extension: %s", name)
        return results

    def discover_default_dirs(
        self, cwd: str | None = None, *, trust_project: bool = False
    ) -> list[ActivateFn]:
        """Scan the standard extension directories.

        The user directory is the user's own. The project directory
        (``<cwd>/.pi-python/extensions``) comes with the repository and importing it runs
        its code as soon as a session opens, so it is scanned only when *trust_project*
        is true. Otherwise nothing in it is imported; it is recorded in ``skipped`` and
        logged as a warning.
        """
        results: list[ActivateFn] = []
        home_ext = self.home / "extensions"
        results.extend(self.discover_directory(home_ext))
        if cwd:
            project_ext = Path(cwd) / ".pi-python" / "extensions"
            if _same_directory(project_ext, home_ext):
                pass  # cwd is the home directory: this is the user's own directory, scanned above
            elif trust_project:
                results.extend(self.discover_directory(project_ext))
            else:
                self._skip_untrusted(project_ext)
        return results

    def _skip_untrusted(self, directory: Path) -> None:
        """Record (without importing anything) what *directory* would have loaded."""
        names = tuple(name for name, _, _ in _extension_sources(directory))
        if not names:
            return
        self.skipped = [s for s in self.skipped if s.directory != directory]
        self.skipped.append(SkippedExtensions(directory=directory, names=names))
        logger.warning(
            "Skipped %d extension(s) in %s because the project is not trusted "
            "(importing them would run their code): %s",
            len(names),
            directory,
            ", ".join(names),
        )

    def load(
        self,
        module_or_callable: Any,
        *,
        name: str | None = None,
        source: str = "programmatic",
        bridge: Any = None,
    ) -> ExtensionAPI:
        """Load a single extension from a module or ``activate`` callable.

        Accepts either a module object (must expose an ``activate`` function)
        or a bare callable.  This is the public API matching the design doc;
        ``load_callable`` is the underlying implementation.
        """
        activate = _resolve_activate(module_or_callable)
        if activate is None:
            raise TypeError(f"Cannot resolve an activate function from {module_or_callable!r}")
        return self.load_callable(activate, name=name, source=source, bridge=bridge)

    def load_callable(
        self,
        activate: ActivateFn,
        *,
        name: str | None = None,
        source: str = "programmatic",
        bridge: Any = None,
    ) -> ExtensionAPI:
        """Load a single extension from an ``activate`` callable.

        An extension is identified by *name*, else by ``_default_name(activate)``. Loading
        under a name that is taken replaces the earlier extension (reloading the same
        callable, or a project extension overriding a user one, relies on it) and is logged
        as a warning when the replacement is a different callable.

        Args:
            bridge: Optional ``HarnessBridge`` to bind *before* ``activate()``
                so the extension can call ``pi.cwd`` etc. during init.
        """
        ext_name = name or _default_name(activate)
        old_api: ExtensionAPI | None = None
        replaces_another = False
        if ext_name in self._loaded_names:
            old_api = next((a for a in self._apis if a.extension_name == ext_name), None)
            replaces_another = self._activations.get(ext_name) is not activate

        meta = ExtensionMeta(name=ext_name, source=source)
        api = ExtensionAPI(registry=self.registry, meta=meta, home=self._home)

        # P7-03: bind bridge BEFORE activate so pi.cwd etc. are usable
        if bridge is not None:
            api._set_bridge(bridge)

        # Snapshot before any mutation so failure can roll back completely
        snap = self.registry.snapshot()
        old_apis_copy = list(self._apis)
        old_names_copy = set(self._loaded_names)

        # Remove old registrations from registry only (NOT from live harness
        # yet — we delay live purge until activate succeeds so a failure can
        # leave the old extension's runtime state intact).
        if old_api is not None:
            self.registry.remove_by_extension(ext_name)
            self._apis = [a for a in self._apis if a.extension_name != ext_name]
            self._loaded_names.discard(ext_name)

        try:
            activate(api)
        except Exception:
            # Roll back registry + loader state.  Live harness was NOT
            # modified (tools/hooks during activate() are registry-only
            # because _loading=True), so old extension's runtime is intact.
            self.registry.restore(snap)
            self._apis = old_apis_copy
            self._loaded_names = old_names_copy
            logger.error("Extension %r failed during activate()", ext_name, exc_info=True)
            raise

        # activate() succeeded — now purge old extension's live harness state
        if old_api is not None and bridge is not None:
            self._purge_live_harness(bridge, ext_name, snap)

        api._loading = False  # enable dynamic register_tool → bridge injection
        self._apis.append(api)
        self._loaded_names.add(ext_name)
        self._activations[ext_name] = activate
        if replaces_another:
            logger.warning(
                "Extension %r replaces a different extension loaded under the same name "
                "(its tools, commands and hooks are gone). Give them distinct names to keep both.",
                ext_name,
            )
        elif old_api is not None:
            logger.debug("Extension %r reloaded", ext_name)
        return api

    def load_all(
        self,
        *,
        extra: list[ActivateFn] | None = None,
        cwd: str | None = None,
        auto_discover: bool = True,
        trust_project_extensions: bool = False,
        bridge: Any = None,
    ) -> list[ExtensionAPI]:
        """Discover and load all extensions.  Returns the list of ``ExtensionAPI`` instances.

        ``trust_project_extensions`` decides whether ``<cwd>/.pi-python/extensions`` is
        scanned (see ``discover_default_dirs``); it is off unless the caller opts in.

        An extension that fails (its module raising as it is imported, an entry point that
        resolves to nothing, or ``activate()`` raising) is logged and recorded in ``failed``;
        one that got as far as ``activate()`` is also rolled back. The others still load: an
        extension must not vanish silently, and must not take the rest down.
        """
        callables: list[tuple[ActivateFn, str]] = []
        if auto_discover:
            for fn in self.discover_entry_points():
                callables.append((fn, "entry_point"))
            for fn in self.discover_default_dirs(cwd, trust_project=trust_project_extensions):
                callables.append((fn, "directory"))
        for fn in extra or []:
            callables.append((fn, "programmatic"))

        for activate, source in callables:
            try:
                self.load_callable(activate, source=source, bridge=bridge)
            except Exception as exc:
                # load_callable already rolled back and logged the traceback.
                self._record_failure(_default_name(activate), source, _describe(exc))

        return list(self._apis)

    def _record_failure(self, name: str, source: str, error: str) -> None:
        self.failed.append(FailedExtension(name=name, source=source, error=error))

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


def _default_name(activate: ActivateFn) -> str:
    """The identity of an extension whose caller gave no name.

    A module-level function called ``activate`` is the extension *module's* activation
    hook (entry points and directory extensions): it is named after the module. Any other
    callable is named ``module.qualname``. The module alone is not an identity, since two
    extensions may be defined side by side in one module, and the second used to silently
    replace the first.
    """
    module = getattr(activate, "__module__", None)
    qualname = getattr(activate, "__qualname__", None)
    if not module:
        return qualname or "anonymous"
    if not qualname or qualname == "activate":
        return module
    return f"{module}.{qualname}"


def _resolve_activate(obj: Any) -> ActivateFn | None:
    """Given a loaded entry-point object, find the ``activate`` callable."""
    if callable(obj) and getattr(obj, "__name__", "") == "activate":
        return obj  # type: ignore[return-value]
    if hasattr(obj, "activate") and callable(obj.activate):
        return obj.activate  # type: ignore[return-value]
    if callable(obj):
        return obj  # type: ignore[return-value]
    return None


def _same_directory(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _extension_sources(path: Path) -> list[tuple[str, Path, str | None]]:
    """What a directory scan would import: ``(name, module file, package name)``.

    Listing only; nothing is imported. ``discover_directory`` loads exactly these, and
    the untrusted-project report names exactly these.
    """
    if not path.is_dir():
        return []
    sources: list[tuple[str, Path, str | None]] = []
    for item in sorted(path.iterdir()):
        if item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
            sources.append((item.name, item, None))
        elif item.is_dir() and (item / "__init__.py").is_file():
            sources.append((item.name, item / "__init__.py", item.name))
    return sources


def _describe(exc: BaseException) -> str:
    """One-line description of a failure, as shown to the user: ``ExcType: message``."""
    return f"{type(exc).__name__}: {exc}"


def _load_module_from_file(
    path: Path,
    package_name: str | None = None,
) -> ActivateFn | None:
    """Import a Python file and return its ``activate`` function (if any).

    Raises whatever the module raises as it is imported; the caller decides what that means.
    """
    module_name = f"_pi_ext_{package_name or path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return _resolve_activate(module)
