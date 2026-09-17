# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-09-17

### Added

- **TUI binary in GitHub Releases (beta)**: prebuilt `zypi` Linux x86_64 binary is now included in GitHub Releases alongside Python wheels. Download `zypi-linux-x86_64` from the release assets page. Build from source for other platforms.
- **Phase 5 (Coding Agent prompt engine)**: pi-aligned `build_system_prompt()` / `build_coding_agent_harness_system_prompt()` in `pi_agent_cli`; tool `prompt_snippet` / `prompt_guidelines` contributions; `AGENTS.md` / `CLAUDE.md` / `SYSTEM.md` / `APPEND_SYSTEM.md` context files; `<available_skills>` XML format; bash `PI_*` env.
- **FrontierHarness 30-task evaluation**: `scripts/run_eval.py` CLI with 21 Terminal-Bench + 9 DeepSWE industrial tasks; dual runtime (Windows local NTFS junctions + WSL2 Docker sandbox); v2ray proxy integration; full report in `docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md`.
- **TUI & Code-Agent guide**: new `docs/TUI-AND-CODE-AGENT.md` covering installation, configuration, and usage for both `zypi` TUI and `pi_agent_cli`.
- **Release workflow**: `build-tui` job builds the Rust TUI binary (`cargo build --profile release-dist -p pi-pager-bin`) and uploads `zypi-linux-x86_64` to GitHub Releases.

### Changed

- **TUI `x.ai/` residual cleanup (P4P5-1 resolution)**: systematic purge of 680 `x.ai/` references across 115 files in `tui/crates/codegen/pi-pager/src/`. New `acp/vendor.rs` centralises vendor-prefix constants and helpers; 4 inbound filter sites use `vendor::is_vendor_ext_method()` as defensive guards; doc comments, test fixtures, views/dispatch literals all scrubbed. Only `vendor.rs` (4 constants) and `worktree_cmd/mod.rs` (20, deferred) retain `x.ai/` strings.
- **Test regression fixes**: fixed 5 x.ai cleanup regressions where test data was over-renamed to `pi/` while handler code still used original constant names (`TITLE_IS_MANUAL_META_KEY`, `PI_SESSION_UPDATE_METHOD`, `is_vendor_meta_key`).
- **Grok-specific tests marked `#[ignore]`**: 62 tests for features removed from pi-python (grok slash commands, `/loop` scheduler, `/share`, dashboard, `~/.grok` path, `agent` subcommand) annotated with `#[ignore = "pi-python: grok-specific feature not supported"]`.
- TUI test result: `8880 passed, 0 failed, 72 ignored`.
- TUI and code-agent marked as **beta** in README.
- README restructured: added Status table, TUI install section, and updated roadmap.

## [0.2.0] - 2026-08-31

### Added

- **Phase 4 P0–P4 (Coding Agent CLI)**: vendored grok-build TUI under `tui/` (Apache-2.0, excluded from wheels); `packages/pi-agent-cli` standard ACP agent over `AgentHarness` (no `x.ai/*`); TUI spawn `python -m pi_agent_cli`, skip xAI login, drop vendor extension RPCs, home `~/.pi-python`, product binary `zypi`. TUI Cargo crates renamed `xai-*` → `pi-*`, then **P5 de-grok**: `pi-grok-*` → `pi-*`, grok CLI subcommands removed, auth/prefetch startup skipped when `auth_methods` is empty. `/new`→`session/new`, `/resume`→`session/list`+`session/load`; `@` is in-process directory listing; `zypi -p` / `python -m pi_agent_cli -p` is Python headless. P4: `config.toml` (model, permission, skills, `[agent].command`), coding system prompt + skills XML, Windows notes in `docs/WINDOWS.md`.
- **Pelican-on-a-bicycle foundation benchmark**: `pi_agent_cli.benchmarks.pelican`, `scripts/smoke_pelican.py`, `docs/benchmarks/PELCAN-BICYCLE.md` — structural SVG smoke test for the pi TUI agent path.
- **PyPI**: `pi-agent-cli-lc` included in the release workflow (tag `v*` builds and publishes core, harness, and cli wheels/sdists; Rust TUI excluded).

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

[0.3.0]: https://github.com/zy1233/pi-python/releases/tag/v0.3.0
[0.2.0]: https://github.com/zy1233/pi-python/releases/tag/v0.2.0
[0.1.0]: https://github.com/zy1233/pi-python/releases/tag/v0.1.0
