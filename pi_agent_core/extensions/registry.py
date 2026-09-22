"""Extension registry — central store for tools, commands, and event handlers."""

from __future__ import annotations

import logging
from typing import Any

from pi_agent_core.extensions.types import (
    CommandDef,
    EventRegistration,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


class ExtensionRegistry:
    """Collects registrations from all loaded extensions.

    The registry is a passive data store.  ``AgentHarness`` reads it after
    all extensions have been activated and wires the collected tools, commands,
    and event handlers into the existing runtime pipelines.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._commands: dict[str, CommandDef] = {}
        self._event_handlers: dict[str, list[EventRegistration]] = {}

    # -- tools ---------------------------------------------------------------

    def add_tool(self, definition: ToolDefinition, extension_name: str | None = None) -> None:
        if definition.name in self._tools:
            logger.warning(
                "Extension tool %r already registered — overwriting (last-write-wins)",
                definition.name,
            )
        self._tools[definition.name] = definition

    def get_tools(self) -> dict[str, ToolDefinition]:
        return dict(self._tools)

    # -- commands ------------------------------------------------------------

    def add_command(self, command: CommandDef) -> None:
        if command.name in self._commands:
            logger.warning(
                "Extension command /%s already registered — overwriting",
                command.name,
            )
        self._commands[command.name] = command

    def get_commands(self) -> dict[str, CommandDef]:
        return dict(self._commands)

    # -- event handlers ------------------------------------------------------

    def add_event_handler(self, registration: EventRegistration) -> None:
        self._event_handlers.setdefault(registration.event, []).append(registration)

    def remove_event_handler(self, registration: EventRegistration) -> None:
        handlers = self._event_handlers.get(registration.event)
        if handlers and registration in handlers:
            handlers.remove(registration)

    def get_event_handlers(self, event: str) -> list[EventRegistration]:
        return list(self._event_handlers.get(event, []))

    def get_all_event_handlers(self) -> dict[str, list[EventRegistration]]:
        return {k: list(v) for k, v in self._event_handlers.items()}

    # -- introspection -------------------------------------------------------

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def command_count(self) -> int:
        return len(self._commands)

    def clear(self) -> None:
        self._tools.clear()
        self._commands.clear()
        self._event_handlers.clear()

    def summary(self) -> dict[str, Any]:
        return {
            "tools": list(self._tools),
            "commands": list(self._commands),
            "event_handlers": {k: len(v) for k, v in self._event_handlers.items()},
        }
