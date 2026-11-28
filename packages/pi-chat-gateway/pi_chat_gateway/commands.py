"""The commands that the gateway handles itself. Any other ``/word`` goes to the agent as typed
(the agent has its own, such as ``/skill:<name>``)."""

from __future__ import annotations

from dataclasses import dataclass

GATEWAY_COMMANDS = (
    "pair",
    "new",
    "project",
    "resume",
    "stop",
    "btw",
    "mode",
    "model",
    "thinking",
    "status",
    "help",
)


@dataclass(frozen=True)
class Command:
    name: str
    arg: str


def parse_command(text: str) -> Command | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped[1:].partition(" ")
    name = head.strip().lower()
    if name not in GATEWAY_COMMANDS:
        return None
    return Command(name=name, arg=rest.strip())
