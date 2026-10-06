"""Where pi-python keeps its user-level files: config, extensions, sessions, workflows.

One resolver for every component. The CLI, the extension loader and the extensions
themselves used to disagree: the CLI honoured ``PI_HOME`` while the loader and the
workflow extension went to ``Path.home() / ".pi-python"`` directly, so a session started
with ``PI_HOME`` set read its config from one place and its extensions and workflow
journals from another.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "PI_HOME"


def pi_home(override: Path | str | None = None) -> Path:
    """The pi-python home directory (the ``.pi-python`` directory itself).

    ``override`` if given, else ``$PI_HOME``, else ``~/.pi-python``. ``~`` is expanded.
    """
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get(HOME_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".pi-python"
