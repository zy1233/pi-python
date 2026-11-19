# pi-agent-core-lc (`pi_agent_core`)

[![PyPI](https://img.shields.io/pypi/v/pi-agent-core-lc)](https://pypi.org/project/pi-agent-core-lc/)
[![Python](https://img.shields.io/pypi/pyversions/pi-agent-core-lc)](https://pypi.org/project/pi-agent-core-lc/)
[![License](https://img.shields.io/pypi/l/pi-agent-core-lc)](https://github.com/zy1233/pi-python/blob/main/LICENSE)

> Unofficial project: this repository is not affiliated with, endorsed by, or maintained by the official [pi](https://github.com/earendil-works/pi) project or its maintainers.

Python port of [pi-agent-core](https://github.com/earendil-works/pi/tree/main/packages/agent) from the [pi](https://github.com/earendil-works/pi) project, with **LangChain** replacing `pi-ai` for LLM calls. It is the core agent loop and tool runtime underlying [zycode](https://zycode.dev) ([repository root](https://github.com/zy1233/pi-python)).

The loop semantics, event protocol, and tool execution are faithful ports of pi; LangChain is only a `StreamFn` boundary adapter — never a replacement for the agent loop.

## Install

```bash
pip install pi-agent-core-lc

# Optional LLM providers:
pip install "pi-agent-core-lc[openai]"      # ChatOpenAI
pip install "pi-agent-core-lc[anthropic]"   # ChatAnthropic
pip install "pi-agent-core-lc[deepseek]"    # ChatDeepSeek / OpenAI-compatible gateways
pip install "pi-agent-core-lc[all]"         # all providers + harness + cli
```

For local development from the monorepo:

```bash
pip install -e ".[dev]"
```

## Features

**Core runtime** (Phase 1)

- `Agent` — stateful agent with steering / follow-up queues, abort, `subscribe()` event barrier
- `agent_loop` — tool execution loop with pi-compatible event protocol (parallel/sequential tools, `terminate` semantics, `should_stop_after_turn`, `prepare_next_turn`)
- LangChain `StreamFn` adapter (`langchain_stream`) for OpenAI / Anthropic / DeepSeek-style / any `init_chat_model` provider
- Mock stream for tests — the whole suite runs without API keys

**Production hardening** (Phase 2 / 2.5)

- Cross-provider message replay (`transform_messages`: tool-call id normalization, thinking downgrade, image stripping)
- Usage & cost tracking (accumulated across stream chunks, correct for all three real-world reporting shapes) + `CostCalculator`
- Thinking/reasoning: provider param mapping (`reasoning_effort` / Anthropic `thinking` budgets), streamed `thinking_delta` events, Anthropic signature replay, DeepSeek-style `reasoning_content`
- Stream-level retries before the first token (exponential backoff + jitter, `Retry-After` aware)
- Runaway protection: `max_turns` (raises `MaxTurnsExceededError`) and per-tool `tool_timeout`
- Guardrail hooks: `before_llm_call` (with a `ContextBudget` token signal — the compaction hook point), `after_llm_call` (tripwire on raise), `on_agent_end`
- Observability: `on_payload` / `on_response` hooks; every event carries `run_id` / `turn_id`
- Granular stream events: `text_start/end`, `thinking_start/end`, `toolcall_start/end` (plus deltas)
- Structured output: `response_schema` (pydantic model or JSON schema) → parsed `AssistantMessage.structured_output`
- Tool-result images: Anthropic native blocks, user-message fallback elsewhere, stripped when `supports_images=False`
- OpenAI-compatible gateways via `Model.base_url` (SiliconFlow, vLLM, DeepSeek, ...)

**Tool ecosystem** (Phase 3.5)

- Built-in coding tools (`pi_agent_core.coding_tools`): `read` / `bash` / `edit` / `write` / `grep` / `find` / `ls` with pi-faithful truncation notices and `details` payloads
- LangChain `BaseTool` → `AgentTool` adapter (`from_langchain_tool`) — MCP tools via `langchain-mcp-adapters` work out of the box

## Quick start

```python
import asyncio
from pi_agent_core import Agent, Model
from pi_agent_core.adapters import langchain_stream


async def main():
    agent = Agent(
        initial_state={
            "system_prompt": "You are a helpful assistant.",
            "model": Model(provider="openai", model_id="gpt-4o-mini"),
        },
        stream_fn=langchain_stream,
    )

    agent.subscribe(lambda event, signal: print(event.type))

    await agent.prompt("Hello!")
    await agent.wait_for_idle()


asyncio.run(main())
```

Requires `OPENAI_API_KEY` (or the env var for your provider).

### OpenAI-compatible gateways

Point `Model.base_url` at any OpenAI-compatible endpoint. When the endpoint streams thinking as `reasoning_content` (or `reasoning`: SiliconFlow, DeepSeek, OpenRouter, most vLLM gateways), it surfaces as `thinking_delta` events with `provider="openai"` and with `provider="deepseek"` alike. Neither sends the thinking back to a gateway. `provider="deepseek"` needs `langchain-deepseek`; `provider="openai"` needs only `langchain-openai`:

```python
model = Model(
    provider="deepseek",
    model_id="Qwen/Qwen3-8B",
    base_url="https://api.siliconflow.cn/v1",
    context_window=32_000,
)
agent = Agent(initial_state={"model": model}, stream_fn=langchain_stream)
# pass the key explicitly or via env: DEEPSEEK_API_KEY
```

On DeepSeek's own API (no `base_url`, or one on `deepseek.com`) the thinking switch follows `Model.reasoning` and `thinking_level`, and while thinking is on the earlier assistant messages' `reasoning_content` is sent back with every request, which DeepSeek requires once tools are involved. A gateway gets neither: it is left to its own defaults.

### Structured output

```python
from pydantic import BaseModel


class Person(BaseModel):
    name: str
    age: int


agent = Agent(
    initial_state={"model": model},
    stream_fn=langchain_stream,
    response_schema=Person,  # or a JSON schema dict
)
await agent.prompt("Invent a fictional person.")
await agent.wait_for_idle()

agent.messages[-1].structured_output  # {'name': ..., 'age': ...} (None if not parseable)
```

Schema instructions are injected into the system prompt for every provider; OpenAI-style providers additionally get native `response_format` enforcement. Streaming stays intact — the JSON text still flows as `text_delta` events.

### Production configuration

```python
from pi_agent_core import Agent, ContextBudget


def on_payload(payload: dict):  # outgoing request (pre-call)
    log.debug("LLM call", model=payload["model"], tools=len(payload["tools"]))


async def before_llm_call(context, budget: ContextBudget | None):
    if budget and budget.fraction > 0.8:
        return await my_compactor.compact(context)  # durably replaces loop context
    return None


def after_llm_call(context, message):
    if contains_pii(message):  # guardrail tripwire: raise to abort the run
        raise PiiDetected()


agent = Agent(
    initial_state={"model": model, "tools": tools},
    stream_fn=langchain_stream,
    max_turns=25,  # raises MaxTurnsExceededError -> agent.error_message
    tool_timeout=120.0,  # per tool call, seconds; times out into an error tool result
    max_retries=3,  # stream-level retries before the first token
    on_payload=on_payload,
    before_llm_call=before_llm_call,
    after_llm_call=after_llm_call,
)
```

Runnable version: [`examples/production_agent.py`](https://github.com/zy1233/pi-python/blob/main/examples/production_agent.py).

## Built-in coding tools

A port of pi's coding-agent tool suite lives in `pi_agent_core.coding_tools`, decoupled from the core runtime (tools consume the loop; they are not part of it). Factories bind each tool to a working directory:

```python
from pi_agent_core.coding_tools import create_coding_tools, create_read_only_tools

tools = create_coding_tools("/path/to/project")  # read / bash / edit / write
audit = create_read_only_tools("/path/to/project")  # read / grep / find / ls
agent = Agent(initial_state={"model": model, "tools": tools}, stream_fn=langchain_stream)
```

| Tool | Group | Behavior |
|------|-------|----------|
| `read` | both | Text (2000-line / 50KB truncation, `offset`/`limit` paging) and images (magic-number sniffing → image blocks) |
| `edit` | coding | Exact-text replacement with unique-match guarantee, fuzzy fallback (smart quotes/dashes/trailing whitespace), CRLF/BOM round-trip, unified-diff `details` |
| `write` | coding | Create/overwrite with automatic parent dirs; per-file mutation queue serializes concurrent writes |
| `bash` | coding | Real shell (`bash -c`; Git Bash on Windows, `shell_path` override), merged stdout+stderr, tail truncation with full output spilled to a temp file (`details.fullOutputPath`), timeout/abort kill the whole process tree, 100ms-throttled streaming updates |
| `grep` | read-only | ripgrep-first (`--json` streaming, kills the process at the match limit); pure-Python fallback when `rg` is missing |
| `find` | read-only | Pure-Python glob walk; basename patterns vs any-depth path patterns (`src/**/*.py`) |
| `ls` | read-only | Case-insensitive sort, `/` dir suffix, dotfiles, entry limit |

Groups mirror pi: `create_coding_tools(cwd)` (`read`/`bash`/`edit`/`write` — full file operations plus command execution) and `create_read_only_tools(cwd)` (`read`/`grep`/`find`/`ls` — inspection with a no-modification guarantee). Per-tool `create_*_tool(cwd)` factories and `create_tool(name, cwd)` / `create_all_tools(cwd)` cover custom mixes. Truncation limits are pi's (2000 lines / 50KB, grep lines capped at 500 chars) with actionable notices ("Use offset=N to continue") so the model can page through anything that was cut.

### Using LangChain / MCP tools

Any LangChain `BaseTool` — including MCP tools produced by `langchain-mcp-adapters` — plugs into the same loop through the adapter:

```python
from langchain_core.tools import tool
from pi_agent_core.adapters import from_langchain_tool, from_langchain_tools


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"Sunny in {city}"


agent = Agent(
    initial_state={"model": model, "tools": [from_langchain_tool(get_weather)]},
    stream_fn=langchain_stream,
)
```

Parameter schemas come from `tool_call_schema` (LangChain-injected arguments are excluded); results normalize to pi content blocks (plain strings, content-block lists, base64 images); `content_and_artifact` artifacts land in `details["artifact"]`; tool exceptions bubble into `is_error=True` tool results that the model can react to.

## Events

Agent events (all carry `run_id` and a 1-based `turn_id`):

```text
agent_start → turn_start → message_start/end (user)
→ message_start(assistant) → message_update* → message_end(assistant)
→ [tool_execution_start/update/end → message_start/end (toolResult)]
→ turn_end → ... → agent_end
```

`message_update` wraps the granular assistant-stream events: `text_start/delta/end`, `thinking_start/delta/end`, `toolcall_start/delta/end`. End events carry the aggregate (`content` full text / complete `tool_call` block).

## Concept mapping (pi → Python)

| pi (TypeScript) | pi-python |
|-----------------|-----------|
| `@earendil-works/pi-agent-core` | `pi_agent_core` |
| `packages/agent/src/harness` | `pi_agent_harness` |
| `@earendil-works/pi-ai` `streamSimple` | `langchain_stream` |
| `AgentMessage` | `UserMessage` / `AssistantMessage` / `ToolResultMessage` + custom |
| `Model.baseUrl` | `Model.base_url` |
| `onPayload` / `onResponse` | `on_payload` / `on_response` |
| `agent.prompt()` | `await agent.prompt()` |
| `agent.continue()` | `await agent.continue_()` |

## Testing

```bash
uv run --extra dev --extra harness python -m pytest  # runs without API keys
ruff check . && ruff format --check .
```

Real-API validation (key via env only). A plain `pytest` leaves the `real_llm` tests out, so keys in your shell never cost anything by accident; `-m real_llm` selects them, and each skips when its key is unset:

| Purpose | Command | Env vars |
|---------|---------|----------|
| Automated regression | `pytest -m real_llm -v` | `REAL_LLM_API_KEY`, optional `REAL_LLM_BASE_URL` / `REAL_LLM_MODEL` |
| Provider matrix (OpenAI, Anthropic, DeepSeek, SiliconFlow) | `pytest -m real_llm pi_agent_core/tests/test_provider_matrix.py -v` | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `REAL_LLM_API_KEY`; CI runs it weekly (`provider-matrix.yml`) |
| Manual pre-release smoke | `python scripts/smoke_real_api.py` | `SMOKE_API_KEY` (or `REAL_LLM_API_KEY`), `SMOKE_BASE_URL`, `SMOKE_MODEL` |
| **pi TUI foundation case** | `python scripts/smoke_pelican.py` | `REAL_LLM_API_KEY`; saves SVG to `~/.pi-python/benchmarks/pelican/` — see [PELCAN-BICYCLE.md](https://github.com/zy1233/pi-python/blob/main/docs/benchmarks/PELCAN-BICYCLE.md) |

Use `.venv-test-real` for pytest real-LLM tests; the smoke script can run from the main `.venv`.

## Related documentation

- [Architecture & module design](https://github.com/zy1233/pi-python/blob/main/docs/DESIGN.md)
- [Phase 1 original plan (historical)](https://github.com/zy1233/pi-python/blob/main/docs/PLAN/PLAN-PHASE1.md)
- [Core-layer audit & Phase 2.5 enhancements](https://github.com/zy1233/pi-python/blob/main/docs/AUDIT/AUDIT-2026-07-02.md)
- [Phase 2 spec — usage/cost, thinking/reasoning, `transform_messages`](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-05-25-phase2-production-enhancements-design.md)
- [Phase 3.5 spec — built-in coding tools & LangChain adapter](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-07-03-phase3.5-tool-ecosystem-design.md)
- Higher-level runtime: [`pi-agent-harness-lc`](https://github.com/zy1233/pi-python/blob/main/packages/pi-agent-harness/README.md)
- Coding agent (zycode): [repository README](https://github.com/zy1233/pi-python)
