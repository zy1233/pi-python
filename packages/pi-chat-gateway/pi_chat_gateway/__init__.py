"""zycode-chat: use the zycode agent from a chat app.

The gateway is one more standard ACP client, like the VS Code extension and ``zypi``. It keeps a
connection to the chat platform (Feishu / Lark first), turns messages, buttons and commands into
ACP requests, and starts one ``zycode-agent`` per project. Design:
``docs/specs/2026-11-28-chat-gateway-design.md``.
"""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("pi-chat-gateway-py")
except metadata.PackageNotFoundError:  # a checkout that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
