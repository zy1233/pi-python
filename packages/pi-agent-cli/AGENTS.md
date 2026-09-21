# pi-agent-cli

Standard-ACP agent over `AgentHarness`. Two modes:

| Mode | Entry | Purpose |
|------|-------|---------|
| ACP stdio | `python -m pi_agent_cli` | TUI-spawned agent; `/new` `/resume` `/quit` map to ACP methods |
| Headless | `python -m pi_agent_cli -p "prompt"` | One-shot turn, prints assistant text, exits |

Headless flags: `--system-prompt`, `--append-system-prompt`, `--no-context-files` — see `--help`.

## Config

Home: `~/.pi-python/` (override with `PI_HOME`). Two config files with distinct owners:

| File | Owner | Scope |
|------|-------|-------|
| `agent.toml` | Python agent | model, permission, skills, prompt overrides |
| `config.toml` | Rust TUI only | grok-shell settings; Python-only keys break TUI parsing |

`local.env` in home loads `KEY=VALUE` pairs into env without overwriting (secrets stay out of TOML). See `agent.example.toml` for all keys.

## TUI spawn

The Rust TUI (`zypi`) spawns the Python agent via (priority order):

1. `PI_AGENT_COMMAND` env var
2. `[agent].command` in `agent.toml`
3. `PI_PYTHON` env var (appends `-m pi_agent_cli`)

## System prompt pipeline

`build_system_prompt` (`system_prompt.py`) assembles:

1. Base prompt with tool snippets and `prompt_guidelines`
2. Context files — `context_files.py` walks cwd → repo root collecting `AGENTS.md`, `CLAUDE.md`, `.pi/SYSTEM.md`
3. Skills from `[skills].paths`, formatted as `<available_skills>`
4. `append_system_prompt` (config or CLI flag)

`build_coding_agent_harness_system_prompt` (`create_harness.py`) wraps this for harness use.

## Modules

| Module | Role |
|--------|------|
| `agent.py` | `PiAcpAgent` — ACP methods, permission handling, event projection |
| `config.py` | TOML loading, `pi_home()`, `CliConfig` dataclass |
| `factory.py` | `create_session_harness` — wires tools, model, stream_fn, skills into harness |
| `system_prompt.py` | `build_system_prompt`, tool snippet/guideline consumption |
| `context_files.py` | AGENTS.md / CLAUDE.md / .pi/SYSTEM.md discovery |
| `headless.py` | `-p` one-shot mode, prompt override application |
| `events.py` | Internal events → ACP `session_update` projection |
| `permissions.py` | Tool permission gating (ask / auto / always-approve) |
| `create_harness.py` | Harness-level system prompt, coding tool wiring |
| `benchmarks/` | Pelican benchmark suite, evaluator, Docker runner |
