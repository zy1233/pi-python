"""Re-export ``pi_agent_core.sandbox.child`` (and keep script entrypoint compatibility)."""

import sys

from pi_agent_core.sandbox import child as _child

if __name__ == "__main__":
    _child.main(sys.argv[1:])
else:
    sys.modules[__name__] = _child
