"""pi-codemode: programmatic tool orchestration and deferred tool discovery extension.

Design: ``docs/specs/2026-11-24-code-mode-design.md``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from pi_codemode.codemode_tool import build_codemode_description, create_codemode_tool
from pi_codemode.runtime import CodemodeRuntime
from pi_codemode.settings import CodemodeSettings
from pi_codemode.tool_search_tool import create_tool_search_tool

if TYPE_CHECKING:
    from pi_agent_core.extensions import ExtensionAPI

__all__ = ["activate"]

logger = logging.getLogger(__name__)

MODE_ENTRY_TYPE = "pi.codemode_mode"
SessionMode = Literal["hybrid", "only", "off"]
_META_TOOLS = frozenset({"codemode", "tool_search"})


class _Controller:
    def __init__(self, pi: ExtensionAPI, settings: CodemodeSettings) -> None:
        self.pi = pi
        self.settings = settings
        self.mode: SessionMode = settings.mode if settings.enabled else "off"
        self.tools_registered = False
        self.saved_before_only: list[str] | None = None
        self._deferred_by_only: set[str] = set()
        self.runtime = CodemodeRuntime(pi, lambda: self.settings)

    def _restore_hidden_tools(self) -> None:
        if not self.saved_before_only:
            self._deferred_by_only.clear()
            return
        known = {t.name for t in self.pi.get_all_tools()}
        restore = [n for n in self.saved_before_only if n in known]
        to_undefer = [n for n in restore if n in self._deferred_by_only]
        others = [n for n in restore if n not in self._deferred_by_only]
        if to_undefer:
            self.pi.activate_tools(to_undefer, undefer=True)
        if others:
            self.pi.activate_tools(others)
        self._deferred_by_only.clear()
        self.saved_before_only = None

    def ensure_tools_registered(self, *, refresh_description: bool = False) -> None:
        if self.tools_registered and not refresh_description:
            return
        direct_names = frozenset(self.saved_before_only or ())
        desc = (
            build_codemode_description(self.pi, direct_names=direct_names)
            if self.tools_registered
            else None
        )
        if not self.tools_registered:
            try:
                desc = build_codemode_description(self.pi, direct_names=direct_names)
            except Exception:
                desc = None
        self.pi.register_tool(
            create_codemode_tool(
                self.pi,
                self.runtime,
                lambda: self.settings,
                description=desc,
            )
        )
        if not self.tools_registered:
            self.pi.register_tool(create_tool_search_tool(self.pi))
        self.tools_registered = True

    def apply_mode(self, new_mode: SessionMode, *, persist: bool) -> None:
        previous = self.mode
        self.mode = new_mode

        if new_mode == "off":
            if self.tools_registered:
                self.pi.deactivate_tools(["codemode", "tool_search"])
            self._restore_hidden_tools()
            deferred = [t.name for t in self.pi.get_all_tools() if t.deferred and not t.active]
            if deferred:
                self.pi.activate_tools(deferred)
        elif new_mode == "hybrid":
            self.ensure_tools_registered(refresh_description=True)
            self.pi.activate_tools(["codemode", "tool_search"])
            self._restore_hidden_tools()
            self.ensure_tools_registered(refresh_description=True)
        elif new_mode == "only":
            self.ensure_tools_registered(refresh_description=True)
            self.pi.activate_tools(["codemode", "tool_search"])
            all_by_name = {t.name: t for t in self.pi.get_all_tools()}
            active = self.pi.get_active_tools()
            to_hide = [name for name in active if name not in _META_TOOLS]
            if to_hide:
                if self.saved_before_only is None:
                    self.saved_before_only = to_hide
                else:
                    existing = set(self.saved_before_only)
                    self.saved_before_only.extend(n for n in to_hide if n not in existing)
                self._deferred_by_only.update(
                    n for n in to_hide if n in all_by_name and not all_by_name[n].deferred
                )
                self.pi.deactivate_tools(to_hide, defer=True)
            self.ensure_tools_registered(refresh_description=True)

        if persist and new_mode != previous:
            self.pi.append_entry(MODE_ENTRY_TYPE, {"mode": new_mode})

    def status_text(self) -> str:
        all_tools = self.pi.get_all_tools()
        active_count = sum(1 for t in all_tools if t.active)
        deferred_count = sum(1 for t in all_tools if t.deferred and not t.active)
        return (
            f"Codemode is **{self.mode}** "
            f"(active tools: {active_count}, deferred tools: {deferred_count}, "
            f"require_approval: {self.settings.require_approval}, "
            f"timeout: {self.settings.timeout_seconds:g}s, "
            f"max_tool_calls: {self.settings.max_tool_calls})."
        )

    def handle_command(self, args: str) -> None:
        tokens = args.strip().lower().split()
        sub = tokens[0] if tokens else "status"
        if sub in ("status", ""):
            self.pi.send_message(self.status_text())
            return
        if sub in ("on", "hybrid"):
            self.apply_mode("hybrid", persist=True)
            self.pi.send_message(f"Codemode enabled (mode: hybrid). {self.status_text()}")
            return
        if sub == "only":
            self.apply_mode("only", persist=True)
            self.pi.send_message(f"Codemode set to only mode. {self.status_text()}")
            return
        if sub == "off":
            self.apply_mode("off", persist=True)
            self.pi.send_message(f"Codemode disabled for this session. {self.status_text()}")
            return
        self.pi.send_message("Usage: /codemode [status|on|hybrid|only|off]")


def activate(pi: ExtensionAPI) -> None:
    """Extension entry point: register codemode, tool_search, and ``/codemode``."""
    settings = CodemodeSettings.from_mapping(pi.get_settings())
    controller = _Controller(pi, settings)

    if settings.enabled:
        pi.register_tool(create_codemode_tool(pi, controller.runtime, lambda: controller.settings))
        pi.register_tool(create_tool_search_tool(pi))
        controller.tools_registered = True

    def on_session_start(_event: Any) -> None:
        if not controller.settings.enabled:
            return
        restored: SessionMode | None = None
        for entry in pi.get_custom_entries(MODE_ENTRY_TYPE):
            data = getattr(entry, "data", None)
            if isinstance(data, dict) and data.get("mode") in ("hybrid", "only", "off"):
                restored = data["mode"]
        if restored is not None:
            controller.apply_mode(restored, persist=False)
        elif controller.mode == "only":
            controller.apply_mode("only", persist=False)
        elif controller.mode == "hybrid" and controller.tools_registered:
            controller.ensure_tools_registered(refresh_description=True)

    pi.on("session_start", on_session_start)
    pi.register_command(
        "codemode",
        description="Inspect or switch codemode (/codemode [status|on|hybrid|only|off])",
        handler=controller.handle_command,
    )
