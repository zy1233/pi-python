"""Start-up of the frozen agent: the same ``main`` as ``python -m pi_agent_cli``."""

from __future__ import annotations

import os
import sys


def _leave_the_bundle_environment() -> None:
    """Give the processes that the agent starts the environment it was started with.

    PyInstaller's start-up program puts the folder of its bundled libraries first in
    ``LD_LIBRARY_PATH`` (Linux) and adds ``_PYI_*`` variables. A tool that the agent runs
    (``bash``, ``git``, ``rg``) would then load the bundle's ``libssl`` or ``libz`` instead of
    the system's, and can fail. The dynamic loader read the variable when this process started,
    so changing it now affects only the processes started from here on.
    """
    original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        os.environ["LD_LIBRARY_PATH"] = original
    elif sys.platform.startswith("linux"):
        os.environ.pop("LD_LIBRARY_PATH", None)
    for name in [n for n in os.environ if n.startswith("_PYI_")]:
        del os.environ[name]


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        _leave_the_bundle_environment()
    from pi_agent_cli.__main__ import main

    sys.exit(main())
