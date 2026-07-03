# AGENTS.md

## Project overview

`pi-python` is an unofficial Python port inspired by [`@earendil-works/pi-agent-core`](https://github.com/earendil-works/pi), with **LangChain replacing the `pi-ai` LLM layer**. It is not affiliated with or endorsed by the official `pi` project. Full design: `docs/DESIGN.md`; Phase 2 spec: `docs/superpowers/specs/2026-05-25-phase2-production-enhancements-design.md`; Phase 3 harness spec: `docs/superpowers/specs/2026-07-03-phase3-agent-harness-design.md`; P6 tool-ecosystem spec: `docs/superpowers/specs/2026-07-03-p6-tool-ecosystem-design.md`; audit tracker: `docs/AUDIT-2026-07-02.md`.

Guiding principles:

- **Faithfully port pi's loop semantics.** The TS sources (`packages/agent/src/agent-loop.ts` / `agent.ts` upstream) are the reference; when in doubt, match their behavior.
- **LangChain is only a `StreamFn` boundary adapter.** Tool execution, turn management, and the event protocol live in `agent_loop.py` — never delegate them to LangChain agents/ToolNode.

### Architecture

```
AgentMessage[] → transform_context() → convert_to_llm() → LangChain BaseMessage[]
                                                              ↓
                                                    stream_fn (LangChain astream)
                                                              ↓
                                                    AssistantMessageEvent → AgentEvent
```

### Module map

| Module | Role | pi (TS) counterpart |
|--------|------|---------------------|
| `pi_agent_core/messages.py` | Canonical messages (user/assistant/toolResult), content blocks, `Usage` | `pi-ai` messages |
| `pi_agent_core/types.py` | `Model`, `AgentTool`, contexts, event types, `AgentLoopConfig` | `packages/agent/src/types.ts` |
| `pi_agent_core/event_stream.py` | `EventStream` / `AssistantMessageEventStream` | `pi-ai` EventStream |
| `pi_agent_core/agent_loop.py` | Core loop: turns, tool execution, hooks, event emission | `packages/agent/src/agent-loop.ts` |
| `pi_agent_core/agent.py` | Stateful `Agent` wrapper: prompt/steer/follow-up queues, abort | `packages/agent/src/agent.ts` |
| `pi_agent_core/adapters/langchain_convert.py`, `langchain_stream.py` | pi ⇄ LangChain conversion; `StreamFn` over `astream()` | `packages/ai/src/stream.ts` |
| `pi_agent_core/transform.py` | Cross-provider replay (tool-call id normalization, thinking downgrade, image stripping) | `pi-ai` transforms |
| `pi_agent_core/tools.py`, `validation.py`, `queues.py` | `SimpleTool` helper, argument validation, steering/follow-up queues | — |
| `pi_agent_core/coding_tools/`, `adapters/langchain_tools.py` | Built-in coding tools (`read`/`write`/`ls`/`edit`/`grep`/`find` done; `bash` pending) + LangChain tool adapter | pi coding-agent built-in tools |
| `packages/pi-agent-harness/pi_agent_harness` | Phase 3 harness package: sessions, AgentHarness, compaction, skills/templates, LocalExecutionEnv | `packages/agent/src/harness/` |

### Invariants (do not break)

1. **Event contract** — `prompt()` without tools emits exactly: `agent_start → turn_start → message_start(user) → message_end(user) → message_start(assistant) → message_update* → message_end(assistant) → turn_end → agent_end`. With tools, `tool_execution_*` and `toolResult` message events are inserted after `message_end(assistant)`, possibly across multiple turns.
2. **Parallel tool ordering** — `tool_execution_end` fires in **completion order**; `toolResult` messages persist in tool-call **source order**.
3. **terminate semantics** — skip the next LLM turn only when **all** finalized tool results in the batch have `terminate=True`.
4. **StreamFn contract** — never raises to the caller; failures are encoded as an `error` event with `stop_reason=error|aborted`.
5. **Thinking/reasoning gating** — reasoning params are injected iff `Model.reasoning=True` **and** `thinking_level != "off"`; the same flag drives thinking-history stripping in `transform_messages`, keeping request params and message replay consistent.
6. **Usage accumulation is per-field max, not sum** — real providers report usage as a single final report, complementary splits (Anthropic), or cumulative per-chunk snapshots (SiliconFlow/vLLM gateways); summing inflates the last shape by orders of magnitude.
7. **Structured output must not break streaming** — `response_schema` works via prompt injection + native `response_format` (OpenAI-style); never switch to `with_structured_output` (it replaces `AIMessageChunk` streaming and kills the event protocol).

### Status

Phase 1 (MVP loop), Phase 2 (usage/cost, thinking/reasoning, transform_messages), Phase 2.5 (retries/backoff, `max_turns`/`tool_timeout`, observability, guardrail hooks, `ContextBudget`, structured output, `Model.base_url` + `deepseek` provider), and Phase 3 H1-H4 (`pi-agent-harness`: session tree, AgentHarness runtime, compaction/tree navigation, skills/templates/system prompt/LocalExecutionEnv) are complete. Real-API smoke (`scripts/smoke_real_api.py`) passed against SiliconFlow (OpenAI-compatible). P6 tool ecosystem (`pi_agent_core/coding_tools/` + `adapters/langchain_tools.py`) is partially implemented — spec batches 1-3 and 5-7 are done (shared infra, `read`/`write`/`ls`, `edit`, `grep`/`find`, LangChain tool adapter, tool factories); `bash` (batch 4) and its factory slot remain — see `docs/superpowers/specs/2026-07-03-p6-tool-ecosystem-design.md`. Current test count: 239 (P6 work still landing; recount when `bash` lands).

## Cursor Cloud specific instructions

This is a Python monorepo with `pi-agent-core` and `pi-agent-harness`. There are no services to start — packages are installed in editable mode and tested via `pytest`.

### Key commands

| Action | Command |
|--------|---------|
| Install (dev) | `pip install -e ".[dev]" -e "./packages/pi-agent-harness"` |
| Lint check | `ruff check .` |
| Format check | `ruff format --check .` |
| Auto-fix lint | `ruff check --fix .` |
| Auto-format | `ruff format .` |
| Run tests | `pytest` (or `pytest -v` for verbose) |
| Run example (no API key) | `PI_USE_MOCK=1 python3 examples/minimal_agent.py` |

### Notes

- **Ruff** is the linter and formatter. Config is in `pyproject.toml` under `[tool.ruff]`. Rules enabled: E, F, I, UP, B, SIM, RUF. Line length: 100. Target: Python 3.11.
- **`python` is not on PATH** — always use `python3`.
- `pytest` and other scripts install to `~/.local/bin`. Ensure `PATH` includes this directory (it should already be on PATH in most shells, but if `pytest` is not found, run `export PATH="$HOME/.local/bin:$PATH"`).
- All tests use a mock stream (`pi_agent_core/tests/mock_stream.py`) — **no API keys are needed** to run the test suite or the mock example.
- `asyncio_mode = "auto"` is set in `pyproject.toml`, so async test functions are automatically detected by `pytest-asyncio`.
- On a local Windows machine where the system Python is older than 3.11, create a dedicated venv instead of forcing compatibility: `uv venv --python 3.12 .venv-audit && uv pip install --python .venv-audit -e ".[dev]" -e "./packages/pi-agent-harness"` (the harness package must be installed too, or its tests fail to collect).
