# AGENTS.md

## Project overview

`pi-python` is an unofficial Python port inspired by [`@earendil-works/pi-agent-core`](https://github.com/earendil-works/pi), with **LangChain replacing the `pi-ai` LLM layer**. It is not affiliated with or endorsed by the official `pi` project.

Design documents:

| Document | Contents |
|----------|----------|
| `docs/DESIGN.md` | Project-wide architecture and module design |
| `docs/PLAN/PLAN-PHASE1.md` | Phase 1 original planning (historical, was `plan.md`) |
| `docs/specs/2026-05-25-phase2-production-enhancements-design.md` | Phase 2 spec |
| `docs/specs/2026-07-03-phase3-agent-harness-design.md` | Phase 3 harness spec |
| `docs/specs/2026-07-03-p6-tool-ecosystem-design.md` | P6 tool-ecosystem spec |
| `docs/specs/2026-08-25-phase4-coding-agent-cli-design.md` | Phase 4 CLI: forked grok TUI + standard ACP |
| `docs/AUDIT/AUDIT-2026-07-02.md` | Core-layer audit tracker |
| `docs/AUDIT/AUDIT-H1.md` ~ `docs/AUDIT/AUDIT-H4.md` | Harness batch audits |
| `docs/AUDIT/SPIKE-P0-GROK-TUI.md` | Phase 4 P0: pager `x.ai/*` strip list |

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
| `pi_agent_core/coding_tools/`, `adapters/langchain_tools.py` | Built-in coding tools (all 7: `read`/`bash`/`edit`/`write`/`grep`/`find`/`ls`) + LangChain tool adapter | pi coding-agent built-in tools |
| `packages/pi-agent-harness/pi_agent_harness` | Phase 3 harness package: sessions, AgentHarness, compaction, skills/templates, LocalExecutionEnv | `packages/agent/src/harness/` |
| `packages/pi-agent-cli/pi_agent_cli` | Phase 4 standard-ACP agent (`python -m pi_agent_cli`); no `x.ai/*` | — |
| `tui/` | Phase 4 forked grok TUI (Apache-2.0 Cargo workspace, not in wheels). Product binary `zypi` | grok-build pager |

### Invariants (do not break)

1. **Event contract** — `prompt()` without tools emits exactly: `agent_start → turn_start → message_start(user) → message_end(user) → message_start(assistant) → message_update* → message_end(assistant) → turn_end → agent_end`. With tools, `tool_execution_*` and `toolResult` message events are inserted after `message_end(assistant)`, possibly across multiple turns.
2. **Parallel tool ordering** — `tool_execution_end` fires in **completion order**; `toolResult` messages persist in tool-call **source order**.
3. **terminate semantics** — skip the next LLM turn only when **all** finalized tool results in the batch have `terminate=True`.
4. **StreamFn contract** — never raises to the caller; failures are encoded as an `error` event with `stop_reason=error|aborted`.
5. **Thinking/reasoning gating** — reasoning params are injected iff `Model.reasoning=True` **and** `thinking_level != "off"`; the same flag drives thinking-history stripping in `transform_messages`, keeping request params and message replay consistent.
6. **Usage accumulation is per-field max, not sum** — real providers report usage as a single final report, complementary splits (Anthropic), or cumulative per-chunk snapshots (SiliconFlow/vLLM gateways); summing inflates the last shape by orders of magnitude.
7. **Structured output must not break streaming** — `response_schema` works via prompt injection + native `response_format` (OpenAI-style); never switch to `with_structured_output` (it replaces `AIMessageChunk` streaming and kills the event protocol).

### Status

**Engine layer complete** — Phase 1–3 + P6 as above. Real-API smoke (`scripts/smoke_real_api.py`) passed against SiliconFlow. All harness audits (H1–H4) closed.

**Phase 4 (Coding Agent CLI) P0–P5 landed** — `tui/` vendored grok-build fork de-grokked: crates renamed `pi-*`, product binary `zypi` (`cargo check -p pi-pager-bin`); grok CLI subcommands removed; startup skips auth/prefetch; welcome uses zypi branding. `packages/pi-agent-cli` is standard-ACP-only over `AgentHarness`; TUI spawn is `python -m pi_agent_cli` (`PI_AGENT_COMMAND` / `[agent].command` / `PI_PYTHON`), skips xAI login, drops outbound `x.ai/*`, home `~/.pi-python`. `/new` `/resume` `/quit` map to standard ACP; `@` is local directory listing; `zypi -p` is Python headless. Config: `packages/pi-agent-cli/agent.example.toml` (Python agent; keep `config.toml` empty or pi-python-only); Windows: `docs/WINDOWS.md`. See `docs/specs/2026-08-25-phase4-coding-agent-cli-design.md`.

## Cursor Cloud specific instructions

This is a Python monorepo with `pi-agent-core`, `pi-agent-harness`, and `pi-agent-cli`, plus a Rust TUI workspace under `tui/` (vendored fork of grok-build). There are no services to start — Python packages are installed in editable mode and tested via `pytest`. TUI: `cd tui && cargo check -p pi-pager-bin` (product binary `zypi`; prefer `CARGO_TARGET_DIR` on a Linux filesystem, not `/mnt/d`).

### Virtual environments (uv)

This project uses **uv** for dependency management and virtual environments. Existing venvs:

| Venv | Purpose |
|------|---------|
| `.venv` | Primary development venv (Python 3.12, `[dev]` + harness) — unit tests, linting |
| `.venv-test-real` | Real-LLM integration tests (PyPI deps + `langchain-deepseek`) |
| `.venv-audit` | Audit/review work |
| `.venv-ci-check` | CI lint/format checks |

**ALWAYS use the existing venvs.** Never install packages globally with `pip` — use `uv pip install --python <venv> <pkg>` to add deps into the target venv.

All `.venv-*` directories are in `.gitignore`. **API keys must ONLY be set via environment variables — never hardcode them in source files.**

On Windows, invoke Python / pytest via the venv Scripts path:

```powershell
# Unit tests (mock, no API key needed)
.venv\Scripts\python.exe -m pytest -v

# Real-LLM integration tests (requires REAL_LLM_API_KEY env var)
$env:REAL_LLM_API_KEY = 'sk-...'
.venv-test-real\Scripts\python.exe -m pytest pi_agent_core/tests/test_real_llm.py -m real_llm -v

# Smoke script
$env:SMOKE_API_KEY = 'sk-...'
.venv\Scripts\python.exe scripts/smoke_real_api.py
```

To recreate the real-LLM test venv from scratch:

```powershell
uv venv --python 3.12 .venv-test-real
uv pip install --python .venv-test-real pytest pytest-asyncio pydantic "langchain-core>=0.3.0" "typing-extensions>=4.6" langchain-deepseek
uv pip install --python .venv-test-real --no-deps -e .
```

To create a fresh dev venv (only if `.venv` is broken):

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]" -e "./packages/pi-agent-harness" -e "./packages/pi-agent-cli"
```

### Key commands

| Action | Command |
|--------|---------|
| Install (dev) | `uv pip install --python .venv -e ".[dev]" -e "./packages/pi-agent-harness" -e "./packages/pi-agent-cli"` |
| Add a provider | `uv pip install --python .venv-test-real langchain-deepseek` |
| Lint check | `ruff check .` |
| Format check | `ruff format --check .` |
| Auto-fix lint | `ruff check --fix .` |
| Auto-format | `ruff format .` |
| Run tests (mock) | `.venv\Scripts\python.exe -m pytest` (or `-v` for verbose) |
| Run tests (real LLM) | `$env:REAL_LLM_API_KEY='sk-...'; .venv-test-real\Scripts\python.exe -m pytest -m real_llm -v` |
| Pelican TUI smoke | `$env:REAL_LLM_API_KEY='sk-...'; .venv\Scripts\python.exe scripts/smoke_pelican.py` — see `docs/benchmarks/PELCAN-BICYCLE.md` |
| TUI cargo check | WSL: `cd tui && CARGO_TARGET_DIR=~/grok-build-target cargo check -p pi-pager-bin` (binary name `zypi`) |

### Notes

- **Ruff** is the linter and formatter. Config is in `pyproject.toml` under `[tool.ruff]`. Rules enabled: E, F, I, UP, B, SIM, RUF. Line length: 100. Target: Python 3.11.
- **Always use venv Python** — on Windows the system `python3` may point to the Windows Store stub. Use `.venv\Scripts\python.exe` or `.venv-test-real\Scripts\python.exe` explicitly.
- All unit tests use a mock stream (`pi_agent_core/tests/mock_stream.py`) — **no API keys needed**.
- Real-LLM integration tests (`pi_agent_core/tests/test_real_llm.py`) are marked with `@pytest.mark.real_llm`. They auto-skip when `REAL_LLM_API_KEY` is unset. Use the dedicated `.venv-test-real` venv with the env var set to run them.
- **Never hardcode API keys in source.** All secrets go via environment variables (`REAL_LLM_API_KEY`, `SMOKE_API_KEY`, etc.).
- `asyncio_mode = "auto"` is set in `pyproject.toml`, so async test functions are automatically detected by `pytest-asyncio`.
- `langchain-deepseek` is the provider for SiliconFlow / DeepSeek-compatible endpoints (preserves `reasoning_content` thinking).
