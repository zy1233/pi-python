# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **`<git_status>` on non-UTF-8 locales (Phase 6)**: git output was decoded with the locale codec (cp936 on Chinese Windows), so a branch such as `功` crashed the snapshot (`AttributeError` from a failed reader thread, failing the turn) and others such as `功能` were rendered as mojibake. Git now runs with `encoding="utf-8", errors="replace"` and `core.quotePath=false` (non-ASCII paths appear verbatim instead of as `"\344\270\255..."` escapes), and any failure omits the section instead of failing the turn.
- **`tool_call` hooks can no longer overturn a denial**: `AgentHarness` used a last-non-`None`-wins rule, so an extension registered after the permission layer could un-deny a call by returning `{}` or `{"block": False}`. Dispatch now stops at the first blocking verdict, like upstream pi's `emitToolCall`.
- **Extension tools in the system prompt**: `prompt_snippet` / `prompt_guidelines` of extension-registered tools (`workflow` gating guideline, `goal_update`, `web_search`, ...) were never rendered by the CLI; the tools reached the LLM but were missing from the prompt's tool list and guidelines. Because `pi-goal-x` registers its tools in every session, its guidelines were reworded so they hold when no goal is active (they no longer state "You are in goal-driven mode"; the tools apply only after `/goal`).
- **Extensions scanned twice from the home directory**: with the working directory set to the home directory, `~/.pi-python/extensions` was both the user directory and the "project" one and was imported twice. It is now scanned once.
- **Sub-agent model routing and credentials (`pi-dynamic-workflows`)**: a `tier` resolved to an Anthropic model whatever the parent ran on, and the single configured API key was handed to every provider, so a DeepSeek session sent its key to Anthropic. A model override on the parent's own provider also lost the parent's `base_url`.
- **`workflow` sub-agents ran `bash` / `edit` / `write` without asking (`pi-dynamic-workflows`)**: the permission layer is a `tool_call` hook on the session's harness, but every sub-agent got a fresh harness without it, so in `ask` mode a workflow script could write files and run shell commands with no prompt (the `workflow` tool itself was not gated either). Now `workflow` is a permission-gated tool, and every sub-agent tool call goes through the parent session's `tool_call` chain (`AgentHarness.check_tool_call`, exposed to extensions as `HarnessBridge.tool_call_gate`; fail-closed). Calls carry the id `subagent-<hex>:<model>` and `ToolCallEvent.origin = {"kind": "subagent", "cwd": ...}`, so the prompt says the call comes from a workflow sub-agent and where it runs. Permission prompts are serialized per session, so parallel sub-agents cannot stack dialogs. The workflow runtime is a restricted namespace, not a sandbox, and its docs no longer say so. Script-chosen sub-agent `cwd` is shown but not restricted, and `auto` / `always-approve` modes still let sub-agents do what the session may.

### Changed

- `resolve_tier(tier, tiers=None, *, provider=None)`: built-in tier defaults are now per parent provider (`DEFAULT_MODEL_TIERS_BY_PROVIDER`, currently `anthropic` only; `DEFAULT_MODEL_TIERS` is removed). With any other parent provider a `tier` inherits the parent model. An explicit `tiers` mapping still wins and may name any provider.
- `make_get_api_key(config)` returns the key only for `config.provider`; other providers get `None`, so their SDK reads its own standard variable (for example `ANTHROPIC_API_KEY`). Export that variable when a workflow routes sub-agents to another provider.
- Sub-agent models keep the parent's `base_url` only when they stay on the parent's provider.
- **Project-local extensions are opt-in (Phase 7)**: `<project>/.pi-python/extensions` was imported — that is, executed — when any session opened, so opening an untrusted repository ran its code before the first prompt, outside the permission layer. It is now skipped unless the project is trusted, in the spirit of upstream pi's project trust (a minimal subset: no prompt, no `/trust`). Trust comes only from places the project cannot write: `[extensions] trusted_projects = ["/abs/path", ...]` (subdirectories count; relative entries are ignored; symlinks are resolved) or `trust_project_extensions = true` in `~/.pi-python/agent.toml`, `PI_TRUST_PROJECT_EXTENSIONS=1`, or `--trust-project-extensions` (ACP and headless). Skipped extensions are never imported; they are logged and reported to the user (ACP agent message after `session/new`, headless stderr) with how to enable them. Installed entry-point packages, `~/.pi-python/extensions`, and `extensions=` / `extension_dirs=` are unaffected. Library API: `AgentHarness(trust_project_extensions=False)`, `ExtensionLoader.load_all(trust_project_extensions=False)`, `ExtensionLoader.skipped` / `AgentHarness.skipped_extensions` (`SkippedExtensions`). **Migration**: a project that ships its own extensions must be allow-listed once. Trust is by path, not content; `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md` and relative skills paths are still read from untrusted projects (see the Phase 7 spec, §4.4).
- **`<git_status>` is labelled as a session-start snapshot (Phase 6)**: the 0.4.0 note "on every turn" was inaccurate. Since `bee053c` the harness caches the system prompt for the whole session (a stable prefix for the provider's prompt cache), so the block never tracked the working tree while the model was told nothing about it. The block now opens with "Snapshot taken at the start of this session; it is not refreshed as files change. Run `git status` for the current state." (no timestamp, so the prompt stays byte-stable). Refresh-per-turn was rejected: every file edit would invalidate the cache for the skills section and the whole history after it.

## [0.4.0] - 2026-09-24

### Added

- **Phase 6 (git context)**: `pi_agent_cli` injects a bounded, read-only `<git_status>` block into the system prompt on every turn. Disable with `[git] enabled = false` or headless `--no-git-context`. Non-repos, timeouts, and git failures omit the section. MCP stays out of the product path; callers still adapt `langchain-mcp-adapters` tools with `from_langchain_tool()`.
- **Provider matrix**: opt-in live tests for OpenAI, Anthropic, DeepSeek, and SiliconFlow (`pytest -m real_llm pi_agent_core/tests/test_provider_matrix.py`). Manual workflow `.github/workflows/provider-matrix.yml` (`workflow_dispatch` only; missing secrets skip).
- **Phase 7 extension packages** are built and published with the release: `pi-web-access-py`, `pi-goal-x-py`, `pi-dynamic-workflows-py`. CI and the pre-release test job install all three.

### Changed

- `pi-agent-core-lc`, `pi-agent-harness-lc`, and `pi-agent-cli-lc` are version `0.4.0`.

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
- **Tool ecosystem (Phase 3.5)**:
  - 7 built-in coding tools: `read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`.
  - Group factories: `create_coding_tools()` / `create_read_only_tools()`.
  - LangChain `BaseTool` → `AgentTool` adapter (`from_langchain_tool`).

#### pi-agent-harness

- **Session tree (H1)**: `SessionRepo` with filesystem storage, `uuid7` IDs, branch/fork semantics, message append/list.
- **AgentHarness runtime (H2)**: orchestration layer over `Agent`, queue rollback, abort aggregation, event-based idle wait.
- **Compaction & tree navigation (H3)**: token-based compaction with branch summaries, `navigate_tree` for branch exploration.
- **Skills, templates & env (H4)**: skill discovery, prompt templates, system prompt injection, `LocalExecutionEnv` for sandboxed execution.

[0.4.0]: https://github.com/zy1233/pi-python/releases/tag/v0.4.0
[0.3.0]: https://github.com/zy1233/pi-python/releases/tag/v0.3.0
[0.2.0]: https://github.com/zy1233/pi-python/releases/tag/v0.2.0
[0.1.0]: https://github.com/zy1233/pi-python/releases/tag/v0.1.0
