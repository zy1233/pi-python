# pi-python

[![CI](https://github.com/zy1233/pi-python/actions/workflows/ci.yml/badge.svg)](https://github.com/zy1233/pi-python/actions/workflows/ci.yml)

Python port of [pi-agent-core](https://github.com/earendil-works/pi/tree/main/packages/agent) from the [pi](https://github.com/earendil-works/pi) project, with **LangChain** replacing `pi-ai` for LLM calls.

The loop semantics, event protocol, and tool execution are faithful ports of pi; LangChain is only a `StreamFn` boundary adapter — never a replacement for the agent loop.

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

## Install

```bash
pip install -e ".[dev]"
# Optional providers:
pip install -e ".[openai]"      # ChatOpenAI
pip install -e ".[anthropic]"   # ChatAnthropic
pip install -e ".[deepseek]"    # ChatDeepSeek — also for OpenAI-compatible gateways
                                # that stream reasoning_content (SiliconFlow, vLLM, ...)
```

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

Point `Model.base_url` at any OpenAI-compatible endpoint. Use `provider="deepseek"`
when the endpoint streams thinking as `reasoning_content` (SiliconFlow, DeepSeek,
most vLLM gateways) so it surfaces as `thinking_delta` events instead of being dropped:

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

### Structured output

```python
from pydantic import BaseModel

class Person(BaseModel):
    name: str
    age: int

agent = Agent(
    initial_state={"model": model},
    stream_fn=langchain_stream,
    response_schema=Person,   # or a JSON schema dict
)
await agent.prompt("Invent a fictional person.")
await agent.wait_for_idle()

agent.messages[-1].structured_output  # {'name': ..., 'age': ...} (None if not parseable)
```

Schema instructions are injected into the system prompt for every provider; OpenAI-style
providers additionally get native `response_format` enforcement. Streaming stays intact —
the JSON text still flows as `text_delta` events.

### Production configuration

```python
from pi_agent_core import Agent, ContextBudget

def on_payload(payload: dict):          # outgoing request (pre-call)
    log.debug("LLM call", model=payload["model"], tools=len(payload["tools"]))

async def before_llm_call(context, budget: ContextBudget | None):
    if budget and budget.fraction > 0.8:
        return await my_compactor.compact(context)   # durably replaces loop context
    return None

def after_llm_call(context, message):
    if contains_pii(message):           # guardrail tripwire: raise to abort the run
        raise PiiDetected()

agent = Agent(
    initial_state={"model": model, "tools": tools},
    stream_fn=langchain_stream,
    max_turns=25,           # raises MaxTurnsExceededError -> agent.error_message
    tool_timeout=120.0,     # per tool call, seconds; times out into an error tool result
    max_retries=3,          # stream-level retries before the first token
    on_payload=on_payload,
    before_llm_call=before_llm_call,
    after_llm_call=after_llm_call,
)
```

Runnable version: [`examples/production_agent.py`](examples/production_agent.py).

## Events

Agent events (all carry `run_id` and a 1-based `turn_id`):

```
agent_start → turn_start → message_start/end (user)
→ message_start(assistant) → message_update* → message_end(assistant)
→ [tool_execution_start/update/end → message_start/end (toolResult)]
→ turn_end → ... → agent_end
```

`message_update` wraps the granular assistant-stream events:
`text_start/delta/end`, `thinking_start/delta/end`, `toolcall_start/delta/end`.
End events carry the aggregate (`content` full text / complete `tool_call` block).

## Test

```bash
pytest                 # 89 tests, no API keys needed
ruff check . && ruff format --check .
```

Real-API smoke test against any OpenAI-compatible endpoint (key via env only):

```bash
SMOKE_BASE_URL=https://api.siliconflow.cn/v1 \
SMOKE_API_KEY=sk-... \
SMOKE_MODEL=Qwen/Qwen3-8B \
python scripts/smoke_real_api.py
```

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md) — architecture and porting notes
- [docs/AUDIT-2026-07-02.md](docs/AUDIT-2026-07-02.md) — audit tracker: every finding, fix, and enhancement with status

## Concept mapping (pi → Python)

| pi (TypeScript) | pi-python |
|-----------------|-----------|
| `@earendil-works/pi-agent-core` | `pi_agent_core` |
| `@earendil-works/pi-ai` `streamSimple` | `langchain_stream` |
| `AgentMessage` | `UserMessage` / `AssistantMessage` / `ToolResultMessage` + custom |
| `Model.baseUrl` | `Model.base_url` |
| `onPayload` / `onResponse` | `on_payload` / `on_response` |
| `agent.prompt()` | `await agent.prompt()` |
| `agent.continue()` | `await agent.continue_()` |

## Roadmap

- **Phase 1 (MVP loop)** — done
- **Phase 2 (production enhancements)** — done: `transform_messages`, usage/cost, thinking
- **Phase 2.5 (core hardening)** — done: retries, runaway protection, observability, granular events, guardrail hooks, `ContextBudget`, structured output, tool-result images
- **Phase 3 (AgentHarness)** — designed ([full design doc](docs/superpowers/specs/2026-07-03-phase3-agent-harness-design.md)); H1 done (session tree + pi-v3-compatible JSONL + memory/jsonl repos), H2–H4 next (AgentHarness class, compaction, skills/templates/ExecutionEnv)

## License

MIT
