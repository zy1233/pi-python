# AGENTS.md

## Project overview

`pi-python`: faithful Python port of [`@earendil-works/pi-agent-core`](https://github.com/earendil-works/pi), with LangChain as the `StreamFn` boundary adapter. Unofficial; unaffiliated with the official `pi` project.

- **Faithful port.** TS sources (`packages/agent/src/agent-loop.ts` / `agent.ts`) are the reference; match their behaviour.
- **LangChain is boundary-only.** Tool execution, turn management, and the event protocol live in `agent_loop.py`.

Specs, audits, and benchmarks live under `docs/` — see `docs/DESIGN.md` for architecture, `docs/specs/` for phase designs (2–5, 3.5), `docs/AUDIT/` for audit trackers, `docs/benchmarks/` for FrontierHarness 30 evaluation.

### Architecture

```
AgentMessage[] → transform_context() → convert_to_llm() → LangChain BaseMessage[]
                                                              ↓
                                                    stream_fn (LangChain astream)
                                                              ↓
                                                    AssistantMessageEvent → AgentEvent
```

### Module map

| Module | Role | TS counterpart |
|--------|------|----------------|
| `pi_agent_core/messages.py` | Canonical messages, content blocks, `Usage` | `pi-ai` messages |
| `pi_agent_core/types.py` | `Model`, `AgentTool`, contexts, events, `AgentLoopConfig` | `types.ts` |
| `pi_agent_core/event_stream.py` | `EventStream` / `AssistantMessageEventStream` | `pi-ai` EventStream |
| `pi_agent_core/agent_loop.py` | Core loop: turns, tool execution, hooks, events | `agent-loop.ts` |
| `pi_agent_core/agent.py` | Stateful `Agent`: prompt/steer/follow-up queues, abort | `agent.ts` |
| `pi_agent_core/adapters/` | pi ⇄ LangChain conversion; `StreamFn` over `astream()` | `stream.ts` |
| `pi_agent_core/transform.py` | Cross-provider replay (id normalization, thinking downgrade, image stripping) | `pi-ai` transforms |
| `pi_agent_core/tools.py`, `validation.py`, `queues.py` | `SimpleTool`, argument validation, queues | — |
| `pi_agent_core/coding_tools/` | Built-in coding tools (`read`/`bash`/`edit`/`write`/`grep`/`find`/`ls`) | pi coding tools |
| `packages/pi-agent-harness/` | Sessions, AgentHarness, compaction, skills, LocalExecutionEnv | `harness/` |
| `packages/pi-dynamic-workflows/` | `workflow` tool and runtime. Scripts run in a child process (`sandbox/host.py` ⇄ stdlib-only `sandbox/child.py`, JSON lines): the kernel limits are the boundary, the audit hook is a speed bump. Sandbox work starts from `docs/specs/2026-10-01-workflow-sandbox-design.md` and runs `tests/test_sandbox_*.py` on Windows **and** WSL (the layers differ per platform) | `@quintinshaw/pi-dynamic-workflows` |
| `packages/pi-agent-cli/` | Standard-ACP CLI (`python -m pi_agent_cli`); config `agent.example.toml`, home `~/.pi-python`; TUI binary `zypi` under `tui/`. See [`packages/pi-agent-cli/AGENTS.md`](packages/pi-agent-cli/AGENTS.md) for spawn, config, and prompt pipeline | — |

### Invariants

1. **Event contract** — `prompt()` without tools: `agent_start → turn_start → message_start(user) → message_end(user) → message_start(assistant) → message_update* → message_end(assistant) → turn_end → agent_end`. With tools, `tool_execution_*` and `toolResult` events insert after `message_end(assistant)`, possibly across turns.
2. **Parallel tool ordering** — `tool_execution_end` fires in completion order; `toolResult` messages persist in source order.
3. **Terminate semantics** — skip next LLM turn only when **all** finalized tool results have `terminate=True`.
4. **StreamFn contract** — never raises; failures encoded as `error` event (`stop_reason=error|aborted`).
5. **Thinking gating** — reasoning params injected iff `Model.reasoning=True` and `thinking_level != "off"`; same flag drives thinking-history stripping in `transform_messages`. One exception: DeepSeek's own API (no `base_url`, or one on `deepseek.com`) thinks by default, so with the gate closed it gets an explicit `thinking: disabled`; gateways serving DeepSeek models get nothing. Thinking plus tools on that API is not supported (needs `reasoning_content` replay).
6. **Usage accumulation** — per-field max, not sum (providers report cumulative snapshots or complementary splits).
7. **Structured output** — `response_schema` via prompt injection + `response_format`; `with_structured_output` kills streaming.

## Development

Python monorepo (`pi-agent-core`, `pi-agent-harness`, `pi-agent-cli`) + Rust TUI workspace `tui/` (vendored grok-build fork, binary `zypi`). Packages installed editable; tested via `pytest`. TUI: `cd tui && cargo check -p pi-pager-bin` (WSL; Cargo reads `CARGO_TARGET_DIR` from WSL env exclusively).

### Virtual environments (uv)

| Venv | Purpose |
|------|---------|
| `.venv` | Primary dev (Python 3.12, `[dev]` + harness) — tests, linting |
| `.venv-test-real` | Real-LLM integration (`langchain-deepseek` — preserves `reasoning_content`; needs `REAL_LLM_API_KEY`) |

Add deps with `uv pip install --python <venv> <pkg>`. Pass secrets via env vars.

### Commands

Use venv Python — Windows `python3` may alias the Store stub.

| Action | Command |
|--------|---------|
| Tests (mock) | `.venv\Scripts\python.exe -m pytest` (`real_llm` tests are left out by `addopts`; a later `-m` overrides it) |
| Tests (real LLM) | `.venv-test-real\Scripts\python.exe -m pytest -m real_llm -v` |
| Eval | `scripts/run_eval.py --frontier-30 --task <id>` — see `docs/benchmarks/` |

Lint/format: see `pre-push-ruff` rule. Config in `pyproject.toml` `[tool.ruff]`.

### Venv recovery

Only if a venv is broken — recreate from scratch:

```powershell
# Dev
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]" -e "./packages/pi-agent-harness" -e "./packages/pi-agent-cli" -e "./packages/pi-web-access" -e "./packages/pi-goal-x" -e "./packages/pi-dynamic-workflows"

# Real-LLM
uv venv --python 3.12 .venv-test-real
uv pip install --python .venv-test-real pytest pytest-asyncio pydantic "langchain-core>=0.3.0" "typing-extensions>=4.6" langchain-deepseek
uv pip install --python .venv-test-real --no-deps -e .
```
