# pi-agent-cli

Standard-ACP agent over `AgentHarness`. Two modes:

| Mode | Entry | Purpose |
|------|-------|---------|
| ACP stdio | `python -m pi_agent_cli` | TUI-spawned agent; `/new` `/resume` `/quit` map to ACP methods |
| Headless | `python -m pi_agent_cli -p "prompt"` | One-shot turn, prints assistant text, exits |

Headless flags: `--system-prompt`, `--append-system-prompt`, `--no-context-files`, `--no-git-context` — see `--help`. `--trust-project-extensions` works in both modes (see Project extensions).

## Config

Home: `~/.pi-python/` (override with `PI_HOME`). One resolver, `pi_agent_core.home.pi_home` (re-exported as `config.pi_home`), serves the CLI, the extension loader (`<home>/extensions`, exposed to extensions as `pi.home`) and the workflow extension (saved workflows, journals); `create_session_harness` hands the session's home to the harness so they cannot disagree. Two config files with distinct owners:

| File | Owner | Scope |
|------|-------|-------|
| `agent.toml` | Python agent | model, permission, skills, prompt overrides |
| `config.toml` | Rust TUI only | grok-shell settings; Python-only keys break TUI parsing |

`local.env` in home loads `KEY=VALUE` pairs into env without overwriting (secrets stay out of TOML). See `agent.example.toml` for all keys.

## Project trust

A project's own content is used only if the project is trusted (`extension_trust.project_extensions_trusted`): `<cwd>/.pi-python/extensions` (imported — i.e. executed — when a session opens), `<cwd>/.pi/SYSTEM.md`, `<cwd>/.pi/APPEND_SYSTEM.md`, and `[skills].paths` entries relative to the project (`.pi/skills`). Trust comes from `[extensions] trusted_projects` (absolute paths; subdirectories count) or `trust_project_extensions = true` in the **home** `agent.toml`, `PI_TRUST_PROJECT_EXTENSIONS=1`, or `--trust-project-extensions`. Never read that decision from a file inside the project, or a repository could trust itself. What was skipped is logged and reported to the user (ACP agent message after `session/new`; headless stderr). Not gated: entry-point packages, `~/.pi-python/extensions`, `AGENTS.md` / `CLAUDE.md` (upstream loads them whatever the trust), prompts set in `agent.toml`, and absolute or `~` skills paths. Design: `docs/specs/2026-09-22-phase7-extension-api-design.md` §4.4.

## TUI spawn

The Rust TUI (`zypi`) spawns the Python agent via (priority order):

1. `PI_AGENT_COMMAND` env var
2. `[agent].command` in `agent.toml`
3. `PI_PYTHON` env var (appends `-m pi_agent_cli`)

## System prompt pipeline

`build_system_prompt` (`system_prompt.py`) assembles:

1. Base prompt with tool snippets and `prompt_guidelines`
2. `append_system_prompt` (config or CLI flag)
3. Context files — `context_files.py` walks cwd → repo root collecting `AGENTS.md`, `CLAUDE.md`; a custom `.pi/SYSTEM.md` / `.pi/APPEND_SYSTEM.md` replaces or extends the base prompt only for a trusted project (see Project trust)
4. Git status — `<git_status>` from `git_context.py` when cwd is a repo (`[git]` / `--no-git-context`); the prompt inputs (files, git subprocesses) are built in a worker thread, and `timeout_seconds` is one budget for the whole snapshot
5. Skills from `[skills].paths`, formatted as `<available_skills>`

`build_coding_agent_harness_system_prompt` (`create_harness.py`) wraps this for harness use.

## Modules

| Module | Role |
|--------|------|
| `agent.py` | `PiAcpAgent` — ACP methods, permission handling, event projection |
| `config.py` | TOML loading, `pi_home()`, `CliConfig` dataclass |
| `factory.py` | `create_session_harness` — wires tools, model, stream_fn, skills into harness |
| `extension_trust.py` | Whether a project may load its own extensions, prompt files and skills; the "skipped" notice |
| `extension_notices.py` | The "these extensions failed to load" notice (`AgentHarness.failed_extensions`): ACP agent message after `session/new`, headless stderr |
| `system_prompt.py` | `build_system_prompt`, tool snippet/guideline consumption |
| `context_files.py` | AGENTS.md / CLAUDE.md / .pi/SYSTEM.md discovery |
| `git_context.py` | Read-only branch and `git status` snapshot for the system prompt |
| `headless.py` | `-p` one-shot mode, prompt override application |
| `events.py` | Internal events → ACP `session_update` projection |
| `permissions.py` | Tool permission gating (ask / auto / always-approve) |
| `create_harness.py` | Harness-level system prompt, coding tool wiring |
| `benchmarks/` | Pelican benchmark suite, evaluator, Docker runner |
