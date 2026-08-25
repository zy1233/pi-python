"""stdio entry: python -m pi_agent_cli"""

from __future__ import annotations

import asyncio

from acp import run_agent

from pi_agent_cli.agent import PiAcpAgent


async def _amain() -> None:
    await run_agent(PiAcpAgent())


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
