# pi-agent-harness-lc (`pi_agent_harness`)

[![PyPI](https://img.shields.io/pypi/v/pi-agent-harness-lc)](https://pypi.org/project/pi-agent-harness-lc/)
[![Python](https://img.shields.io/pypi/pyversions/pi-agent-harness-lc)](https://pypi.org/project/pi-agent-harness-lc/)
[![License](https://img.shields.io/pypi/l/pi-agent-harness-lc)](https://github.com/zy1233/pi-python/blob/main/LICENSE)

`pi-agent-harness-lc` (`pi_agent_harness`) is the higher-level harness runtime package for [`pi-agent-core-lc`](https://github.com/zy1233/pi-python/blob/main/pi_agent_core/README.md), corresponding to `packages/agent/src/harness` in the [pi](https://github.com/earendil-works/pi) project (unofficial Python port).

## What it provides

It contains the Phase 3 runtime pieces that are intentionally kept outside the lightweight core loop:

- **`AgentHarness`** — stateful session runtime coordinating hooks, steering/follow-up queues, tool execution gating, model/thinking changes, and persistence
- **Session tree storage & repositories** — append-only JSONL and in-memory session stores (`Session`, `JsonlSessionStorage`, `DeferredJsonlSessionStorage`, `MemorySessionStorage`, `JsonlSessionRepo`, `MemorySessionRepo`, `build_session_context`)
- **Compaction & tree navigation** — context compaction (`CompactionSettings`, `CompactionPreparation`, `CompactionResult`), branch summaries, leaf/tree switching, and optional auto-compaction
- **Harness-specific custom messages** — extensible `AgentMessageProtocol` and built-in custom message roles with LLM boundary conversion (`harness_convert_to_llm`)
- **Skills, prompt templates & system prompt assembly** — `load_skills`, `format_skills_for_system_prompt`, `load_prompt_templates`, `build_harness_system_prompt`
- **Execution environment** — `ExecutionEnv` protocol and `LocalExecutionEnv` (filesystem + shell abstraction)

## Install

Install alongside core:

```bash
pip install pi-agent-core-lc pi-agent-harness-lc
```

For local development from the monorepo:

```bash
pip install -e ".[dev]" -e "./packages/pi-agent-harness"
uv run --extra dev --extra harness python -m pytest
```

## Custom messages

`AgentMessage` is structurally open: anything with a string `role` flows through the loop and persists to the session (`AgentMessageProtocol`, a runtime-checkable Protocol in `pi_agent_harness`). The harness ships four custom roles — `bashExecution`, `custom`, `branchSummary`, `compactionSummary` — and `harness_convert_to_llm` decides how each role reaches the LLM; unknown roles are dropped at that boundary:

```python
from pydantic import BaseModel
from pi_agent_harness import AgentMessageProtocol
from pi_agent_harness.messages import BashExecutionMessage, harness_convert_to_llm

# Built-in custom role: record a shell run in the session/context
bash = BashExecutionMessage(command="pytest -q", output="97 passed", exitCode=0, timestamp=0)
await harness.append_message(bash)  # persisted; replayed to the LLM as a user message


# Your own role: any object with `role: str` satisfies the protocol
class DeployNote(BaseModel):
    role: str = "deployNote"
    environment: str
    timestamp: int


assert isinstance(DeployNote(environment="staging", timestamp=0), AgentMessageProtocol)
```

To make a custom role visible to the LLM, wrap `harness_convert_to_llm` (or pass your own `convert_to_llm` to the core loop) and map it to a `UserMessage` — the same pattern the harness uses for its own roles.

## Related documentation

- [Phase 3 spec — AgentHarness design](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-07-03-phase3-agent-harness-design.md)
- Harness batch audits: [H1 session](https://github.com/zy1233/pi-python/blob/main/docs/AUDIT/AUDIT-H1.md), [H2 runtime](https://github.com/zy1233/pi-python/blob/main/docs/AUDIT/AUDIT-H2.md), [H3 compaction](https://github.com/zy1233/pi-python/blob/main/docs/AUDIT/AUDIT-H3.md), [H4 skills/env](https://github.com/zy1233/pi-python/blob/main/docs/AUDIT/AUDIT-H4.md)
- [Architecture & module design](https://github.com/zy1233/pi-python/blob/main/docs/DESIGN.md)
- Core runtime: [`pi-agent-core-lc`](https://github.com/zy1233/pi-python/blob/main/pi_agent_core/README.md)
- Coding agent (zycode): [repository README](https://github.com/zy1233/pi-python)
