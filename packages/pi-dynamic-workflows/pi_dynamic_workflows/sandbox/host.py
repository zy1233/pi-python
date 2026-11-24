"""Re-export ``pi_agent_core.sandbox.host`` so legacy import paths share one module object."""

import sys

from pi_agent_core.sandbox import host as _host

sys.modules[__name__] = _host
