# pi-python

Python port of [pi-agent-core](https://github.com/earendil-works/pi/tree/main/packages/agent) from the [pi](https://github.com/earendil-works/pi) project, with **LangChain** replacing `pi-ai` for LLM calls.

## Features (Phase 1 MVP)

- `Agent` — stateful agent with steering / follow-up queues
- `agent_loop` — tool execution loop with pi-compatible event protocol
- LangChain `StreamFn` adapter (`langchain_stream`)
- Mock stream for tests (no API keys)

## Install

```bash
pip install -e ".[dev]"
# Optional providers:
pip install -e ".[openai,anthropic,dev]"
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

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md) — detailed architecture and porting notes

## Test

```bash
pytest
```

## Concept mapping (pi → Python)

| pi (TypeScript) | pi-python |
|-----------------|-----------|
| `@earendil-works/pi-agent-core` | `pi_agent_core` |
| `@earendil-works/pi-ai` `streamSimple` | `langchain_stream` |
| `AgentMessage` | `UserMessage` / `AssistantMessage` / `ToolResultMessage` + custom |
| `agent.prompt()` | `await agent.prompt()` |
| `agent.continue()` | `await agent.continue_()` |

## Roadmap

- **Phase 2**: cross-provider `transform_messages`, usage/cost, thinking blocks
- **Phase 3**: AgentHarness (session JSONL, compaction, skills)

## License

MIT
