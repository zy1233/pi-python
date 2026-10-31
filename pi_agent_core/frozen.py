"""Whether this process runs from a frozen build (PyInstaller) and not from an installed package.

A frozen build has no ``python`` to start: ``sys.executable`` is the program itself, so a command
of the form ``sys.executable -m module`` does not work in it. It also contains only the providers
that its build included (see ``resolve_chat_model``).
"""

from __future__ import annotations

import sys


def is_frozen() -> bool:
    """True in a PyInstaller (or similar) executable."""
    return bool(getattr(sys, "frozen", False))
