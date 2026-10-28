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
| `pi_agent_core/types.py` | `Model`, `AgentTool`, `ToolAnnotations`, contexts, events, `AgentLoopConfig` | `types.ts` |
| `pi_agent_core/event_stream.py` | `EventStream` / `AssistantMessageEventStream` | `pi-ai` EventStream |
| `pi_agent_core/agent_loop.py` | Core loop: turns, tool execution, hooks, events | `agent-loop.ts` |
| `pi_agent_core/agent.py` | Stateful `Agent`: prompt/steer/follow-up queues, abort | `agent.ts` |
| `pi_agent_core/adapters/` | pi ⇄ LangChain conversion; `StreamFn` over `astream()` | `stream.ts` |
| `pi_agent_core/transform.py` | Cross-provider replay (id normalization, thinking downgrade, image stripping) | `pi-ai` transforms |
| `pi_agent_core/tools.py`, `validation.py`, `queues.py` | `SimpleTool`, argument validation, queues | — |
| `pi_agent_core/coding_tools/` | Built-in coding tools (`read`/`bash`/`edit`/`write`/`grep`/`find`/`ls`) | pi coding tools |
| `packages/pi-agent-harness/` | Sessions, AgentHarness, compaction, skills, LocalExecutionEnv | `harness/` |
| `packages/pi-dynamic-workflows/` | `workflow` tool and runtime. Scripts run in a child process (`sandbox/host.py` ⇄ stdlib-only `sandbox/child.py`, JSON lines): the kernel limits are the boundary, the audit hook is a speed bump. Sandbox work starts from `docs/specs/2026-10-01-workflow-sandbox-design.md` and runs `tests/test_sandbox_*.py` on Windows **and** WSL (the layers differ per platform) | `@quintinshaw/pi-dynamic-workflows` |
| `packages/pi-mcp/` | Optional extension `pi-mcp-py` (entry point `pi-mcp`): connects the MCP servers (stdio, http, sse) that an ACP client passes in `session/new`, one runner task per server, tools named `mcp__<server>__<tool>`, `/mcp` command. Core, harness and CLI have no MCP client; they hand the servers over as data (`ClientMcpServer`, `ExtensionAPI.client_mcp_servers`). Design: `docs/specs/2026-10-23-mcp-extension-design.md`. Its tests are left out of a run that did not install the package (the header says so) | — |
| `packages/pi-agent-cli/` | Standard-ACP CLI (`python -m pi_agent_cli`); config `agent.example.toml`, home `~/.pi-python`; TUI binary `zypi` under `tui/`. See [`packages/pi-agent-cli/AGENTS.md`](packages/pi-agent-cli/AGENTS.md) for spawn, config, first run (`--setup` and the terminal login that `initialize` offers to a client that can run one; `zypi` still gets `authMethods: []`), and prompt pipeline | — |

### Invariants

1. **Event contract** — `prompt()` without tools: `agent_start → turn_start → message_start(user) → message_end(user) → message_start(assistant) → message_update* → message_end(assistant) → turn_end → agent_end`. With tools, `tool_execution_*` and `toolResult` events insert after `message_end(assistant)`, possibly across turns.
2. **Parallel tool ordering** — `tool_execution_end` fires in completion order; `toolResult` messages persist in source order.
3. **Terminate semantics** — skip next LLM turn only when **all** finalized tool results have `terminate=True`.
4. **StreamFn contract** — never raises; failures encoded as `error` event (`stop_reason=error|aborted`).
5. **Thinking gating** — reasoning params injected iff `Model.reasoning=True` and `thinking_level != "off"`; same flag drives thinking-history stripping in `transform_messages`. One exception: DeepSeek's own API (no `base_url`, or one on `deepseek.com`) thinks by default, so with the gate closed it gets an explicit `thinking: disabled`; gateways serving DeepSeek models get nothing. With thinking on, that API wants every earlier assistant message's `reasoning_content` back (400 otherwise): `adapters/deepseek_replay.py` sends it, on that API only and only while thinking is asked for (not verified against the real API).
6. **Usage accumulation** — per-field max, not sum (providers report cumulative snapshots or complementary splits).
7. **Structured output** — `response_schema` via prompt injection + `response_format`; `with_structured_output` kills streaming.
8. **Permission by declaration** — the CLI's `ask` mode asks about a tool call unless the tool's `annotations` (`ToolAnnotations`, MCP's hints) declare it harmless (`readOnlyHint`, or neither destructive nor open-world); no annotations means asked. A new tool that only reads should say so; `bash`/`edit`/`write`/`workflow` say nothing on purpose. See `packages/pi-agent-cli/AGENTS.md`.
9. **Project trust** — what the project directory supplies (extensions, saved workflows, `.pi/SYSTEM.md`, project skills) is used only once the user vouched for the project. An extension that reads such files itself checks `pi.project_trusted is True` first; anything else means no. See `packages/pi-agent-cli/AGENTS.md`.

## Development

Python monorepo (`pi-agent-core`, `pi-agent-harness`, `pi-agent-cli`) + Rust TUI workspace `tui/` (vendored grok-build fork, binary `zypi`). Packages installed editable; tested via `pytest`. TUI: `cd tui && cargo check -p pi-pager-bin` (WSL; Cargo reads `CARGO_TARGET_DIR` from WSL env exclusively). Rust tests run through `python scripts/tui_baseline.py test` (manifest `scripts/tui_baseline.toml`, numbers in `docs/baselines/tui.md`); it runs `cargo nextest` (one process per test, as the vendored tests expect; install `cargo-nextest`, or pass `--runner cargo` for libtest), and it clears `PI_HOME` and friends, which a WSL login shell may export pointing at the real home — do not run test binaries or `zypi` without clearing them. The run does not isolate `HOME` (plan D.9): some tests write into the real `~/.pi-python` and `/tmp`, so check both after a local run. Build on the Linux file system, not `/mnt/<drive>`. With a built `zypi`, `scripts/tui_pty/` drives it under a pseudo-terminal on Linux (smoke test, exit matrix, `-p` shutdown, the matrix of flags that resume a session, a probe of which permission modes the agent honours, a network audit (it shows that `zypi` itself opens no connection to a non-local address during a turn), a probe of the status line; README there). Two tools read the TUI tree: `python scripts/tui_callgraph.py keep --out docs/baselines/tui-support.md` regenerates the list of what the support crates still use (it reads source text only; the list is an estimate, `cargo check` has the last word), and `python scripts/tui_notices.py check` (needs cargo) says whether `tui/THIRD-PARTY-NOTICES` still matches `Cargo.lock`; run it after a dependency change, then `update --write`. The pager's ACP client is also run against the real Python agent without a terminal: `tui/crates/codegen/pi-pager/tests/python_agent_e2e.rs`, which needs `PI_E2E_PYTHON` (a Python with `pi_agent_cli` installed; TUI CI sets it and `PI_E2E_REQUIRED=1`, locally an unset variable makes the tests skip with a note). Under `cargo test`, the tests of one Rust test binary run as threads of one process (4 threads, a fixed order), and they share the environment variables and every `static`; the vendored tests were written for `cargo nextest` (one process per test), so their locks are partial. A test that sets such state can break another test in some runs only. TUI CI and `scripts/tui_baseline.py` run nextest now; a local `cargo test` still shows these races. `scripts/tui_envspy/` finds these tests (an `LD_PRELOAD` spy for environment variables and a shuffled many-thread stress script; README there). Every test that writes or reads one variable must hold the one lock that the other tests of that variable hold. A test that changes a process-wide `static` that other tests read without a lock (the theme, the colour level, the minimal-mode flag, the bidi latch) runs its body in a process of its own: `crate::test_util::in_own_process(|| { … })` in `pi-pager`.

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
uv pip install --python .venv -e ".[dev]" -e "./packages/pi-agent-harness" -e "./packages/pi-agent-cli" -e "./packages/pi-web-access" -e "./packages/pi-goal-x" -e "./packages/pi-dynamic-workflows" -e "./packages/pi-mcp"

# Real-LLM
uv venv --python 3.12 .venv-test-real
uv pip install --python .venv-test-real pytest pytest-asyncio pydantic "langchain-core>=0.3.0" "typing-extensions>=4.6" langchain-deepseek
uv pip install --python .venv-test-real --no-deps -e .
```
