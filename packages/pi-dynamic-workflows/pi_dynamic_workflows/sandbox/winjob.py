"""Re-export ``pi_agent_core.sandbox.winjob`` so legacy import paths share one module object."""

import sys

from pi_agent_core.sandbox import winjob as _winjob

sys.modules[__name__] = _winjob
