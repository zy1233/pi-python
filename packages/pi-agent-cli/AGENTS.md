# pi-agent-cli

Standard-ACP agent over `AgentHarness`. Two modes:

| Mode | Entry | Purpose |
|------|-------|---------|
| ACP stdio | `python -m pi_agent_cli` | TUI-spawned agent; `/new` `/resume` `/quit` map to ACP methods |
| Headless | `python -m pi_agent_cli -p "prompt"` | One-shot turn, prints assistant text, exits |

Headless flags: `--system-prompt`, `--append-system-prompt`, `--no-context-files`, `--no-git-context` — see `--help`.

## Config

Home: `~/.pi-python/` (override with `PI_HOME`). Two config files with distinct owners:

| File | Owner | Scope |
|------|-------|-------|
| `agent.toml` | Python agent | model, permission, skills, prompt overrides |
| `config.toml` | Rust TUI only | grok-shell settings; Python-only keys break TUI parsing |

`local.env` in home loads `KEY=VALUE` pairs into env without overwriting (secrets stay out of TOML). See `agent.example.toml` for all keys.

## Model selection (`/model`)

Standard ACP **Session Config Options** — `session/set_model` is gone upstream (SDK ≥ 0.11). `new_session` / `load_session` / `resume_session` return `configOptions` with one select (`id="model"`, `category="model"`, `currentValue`, `options`); the client switches with `session/set_config_option` (`PiAcpAgent.set_config_option` → `harness.set_model`, response carries the full option set). Choices = `[model]` + optional `[[models]]` tables in `agent.toml` (`ModelChoice`, `CliConfig.model_choices()`). The model is persisted in the session (`model_change`) and restored on load/resume if still configured. Legacy `_meta` hints (`pi/currentModelId`, `pi/currentModelDisplayName`, `pi/provider`) are still emitted for older clients.

## TUI spawn

The Rust TUI (`zypi`) spawns the Python agent via (priority order):

1. `PI_AGENT_COMMAND` env var
2. `[agent].command` in `agent.toml`
3. `PI_PYTHON` env var (appends `-m pi_agent_cli`)

## System prompt pipeline

`build_system_prompt` (`system_prompt.py`) assembles:

1. Base prompt with tool snippets and `prompt_guidelines`
2. `append_system_prompt` (config or CLI flag)
3. Context files — `context_files.py` walks cwd → repo root collecting `AGENTS.md`, `CLAUDE.md`, `.pi/SYSTEM.md`
4. Git status — `<git_status>` from `git_context.py` when cwd is a repo (`[git]` / `--no-git-context`)
5. Skills from `[skills].paths`, formatted as `<available_skills>`

`build_coding_agent_harness_system_prompt` (`create_harness.py`) wraps this for harness use.

## Modules

| Module | Role |
|--------|------|
| `agent.py` | `PiAcpAgent` — ACP methods, permission handling, event projection |
| `config.py` | TOML loading, `pi_home()`, `CliConfig` dataclass |
| `factory.py` | `create_session_harness` — wires tools, model, stream_fn, skills into harness |
| `system_prompt.py` | `build_system_prompt`, tool snippet/guideline consumption |
| `context_files.py` | AGENTS.md / CLAUDE.md / .pi/SYSTEM.md discovery |
| `git_context.py` | Read-only branch and `git status` snapshot for the system prompt |
| `headless.py` | `-p` one-shot mode, prompt override application |
| `events.py` | Internal events → ACP `session_update` projection |
| `permissions.py` | Tool permission gating (ask / auto / always-approve) |
| `session_list.py` | `session/list` display data: first-user-message title and file-mtime `updated_at` (bounded read; one page, no cursor) |
| `create_harness.py` | Harness-level system prompt, coding tool wiring |
| `benchmarks/` | Pelican benchmark suite, evaluator, Docker runner |
