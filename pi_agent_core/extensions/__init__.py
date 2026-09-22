"""pi_agent_core.extensions — Python ExtensionAPI (Phase 7).

Public surface:

    from pi_agent_core.extensions import ExtensionAPI, ExtensionLoader
    from pi_agent_core.extensions import ToolDefinition, CommandDef
"""

from pi_agent_core.extensions.api import ExtensionAPI
from pi_agent_core.extensions.loader import ExtensionLoader
from pi_agent_core.extensions.registry import ExtensionRegistry
from pi_agent_core.extensions.types import (
    CommandDef,
    CommandHandler,
    EventHandler,
    ExtensionMeta,
    ToolDefinition,
    ToolInfo,
)

__all__ = [
    "CommandDef",
    "CommandHandler",
    "EventHandler",
    "ExtensionAPI",
    "ExtensionLoader",
    "ExtensionMeta",
    "ExtensionRegistry",
    "ToolDefinition",
    "ToolInfo",
]
