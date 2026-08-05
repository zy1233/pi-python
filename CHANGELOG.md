# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-05

Initial public release.

### Added

#### pi-agent-core

- **Core runtime (Phase 1)**: `Agent` with steering/follow-up queues, abort, `subscribe()` event barrier; `agent_loop` with pi-compatible event protocol, parallel/sequential tool execution, `terminate` semantics.
- **LangChain adapter**: `StreamFn` over `astream()` for OpenAI / Anthropic / DeepSeek / any `init_chat_model` provider; mock stream for tests (no API keys needed).
- **Production hardening (Phase 2/2.5)**:
  - Cross-provider message replay (`transform_messages`: tool-call id normalization, thinking downgrade, image stripping).
  - Usage & cost tracking with `CostCalculator`.
  - Thinking/reasoning: provider param mapping, streamed `thinking_delta` events, Anthropic signature replay, DeepSeek-style `reasoning_content`.
  - Stream-level retries with exponential backoff + jitter, `Retry-After` aware.
  - Runaway protection: `max_turns` / `tool_timeout`.
  - Guardrail hooks: `before_llm_call` (with `ContextBudget`), `after_llm_call`, `on_agent_end`.
  - Observability: `on_payload` / `on_response` hooks; `run_id` / `turn_id` on every event.
  - Granular stream events: `text_start/end`, `thinking_start/end`, `toolcall_start/end`.
  - Structured output: `response_schema` (Pydantic model or JSON schema).
  - Tool-result images: Anthropic native blocks, user-message fallback elsewhere.
  - OpenAI-compatible gateways via `Model.base_url`.
- **Tool ecosystem (P6)**:
  - 7 built-in coding tools: `read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`.
  - Group factories: `create_coding_tools()` / `create_read_only_tools()`.
  - LangChain `BaseTool` → `AgentTool` adapter (`from_langchain_tool`).

#### pi-agent-harness

- **Session tree (H1)**: `SessionRepo` with filesystem storage, `uuid7` IDs, branch/fork semantics, message append/list.
- **AgentHarness runtime (H2)**: orchestration layer over `Agent`, queue rollback, abort aggregation, event-based idle wait.
- **Compaction & tree navigation (H3)**: token-based compaction with branch summaries, `navigate_tree` for branch exploration.
- **Skills, templates & env (H4)**: skill discovery, prompt templates, system prompt injection, `LocalExecutionEnv` for sandboxed execution.

[0.1.0]: https://github.com/zy1233/pi-python/releases/tag/v0.1.0
