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

A project's own content is used only if the project is trusted: `<cwd>/.pi-python/extensions` (imported — i.e. executed — when a session opens), `<cwd>/.pi/SYSTEM.md`, `<cwd>/.pi/APPEND_SYSTEM.md`, and `[skills].paths` entries relative to the project (`.pi/skills`) — `trust_fingerprint.gated_project_resources` lists them. Never read the decision from a file inside the project, or a repository could trust itself. Not gated: entry-point packages, `~/.pi-python/extensions`, `AGENTS.md` / `CLAUDE.md` (upstream loads them whatever the trust), prompts set in `agent.toml`, and absolute or `~` skills paths. Design: `docs/specs/2026-09-22-phase7-extension-api-design.md` §4.4.

`extension_trust.decide_project_trust(config, cwd, home=)` returns a `ProjectTrustDecision` (hashes files: call it off the event loop). Order: `PI_TRUST_PROJECT_EXTENSIONS` / `--trust-project-extensions` → `default_project_trust = "always"` (or the older `trust_project_extensions`; an explicit `default_project_trust` wins) → `[extensions] trusted_projects` in the **home** `agent.toml` (absolute paths; subdirectories count) → nothing gated (`nothing`) → files cannot be fingerprinted (`unpinnable`: no question, use `trusted_projects`) → an answer saved in `<home>/agent/trust.json` whose fingerprint still matches (`saved`) → `default_project_trust = "never"` → otherwise `undecided` / `changed` (a saved answer for other files), which an ACP client is asked about. `project_extensions_trusted` is the file-free subset (configuration and command line only).

- **Who asks.** Only the ACP agent (`agent.py`), and only when the decision `can_ask` and a client is connected. Headless never asks (upstream pi does not prompt in non-interactive modes either) but honours `saved`. The question (`trust_prompt.py`) is preceded by an ordinary agent message naming the directory, the files and what each does (the TUI shows a permission request's title and options only), then a `session/request_permission` with `Don't trust` (`reject_once`, first) and `Trust and remember` (`allow_always`). **No `allow_once` option, deliberately**: clients may answer tool permissions for the user (the TUI's YOLO mode takes the first `allow_once`), and this is not that decision. Only the exact trust option id, selected, is a yes (never reuse `permissions.outcome_allows`, which treats every id not starting with `reject` as yes); asking happens whatever the `permission` mode says.
- **When.** After the `session/new` (`load`, `resume`) response has gone out — clients drop requests for sessions they have not registered (`_deferred_session_setup`) — and extensions load only after the answer; `prompt()` waits for it (`session/cancel` or closing the session ends the wait; `_SessionTrust`). One question per project at a time (`_project_locks`); the later session decides afresh and normally finds the saved answer. The answer is checked against the disk again after the dialog (`modified` if the files moved), then `TrustStore.remember` (atomic, fail-closed; an unwritable store leaves the session trusted and says so). Refusals are not remembered. `ProjectTrust` is the session's live answer that `create_session_harness`, `load_system_prompt_options` and `load_session_resources` read, so a yes takes effect without rebuilding the session (`AgentHarness.set_trust_project_extensions`, valid until extensions load).
- **What was left out** is logged and reported to the user with the reason (`untrusted_project_notice(why=)`: not decided, changed since trusted, changed while asked, declined, could not ask, `never`, unpinnable) — ACP agent message after `session/new`, headless stderr.
- **Fingerprint** (`trust_fingerprint.fingerprint_resources`): SHA-256 over file contents of everything gated (recursive; `__pycache__` and `.git` skipped; symlinks followed with a loop guard; regular files only; at most 2000 entries and 64 MiB, else `None`) — contents, not mtimes. Stored per exact directory (`TrustStore`: `project_key` = `normcase(realpath)`), not per subtree.

## Tool permissions

`permission` in `agent.toml` is `ask` (default), `auto` or `always-approve`; only `ask` ever asks (headless forces `auto`). In `ask` mode a tool call goes to the client as `session/request_permission` **unless the tool declares itself harmless** (`permissions.needs_permission`): `annotations.readOnlyHint is True`, or `destructiveHint is False` and `openWorldHint is False`. Names and meanings are MCP's, with MCP's worst-case defaults — no annotations means asked — and the values must be real booleans. Tool names no longer matter (there is no allow-list).

- **Where annotations live.** `ToolAnnotations` (`pi_agent_core.types`) on `SimpleTool`, `CodingTool` and `ToolDefinition` (extensions); `from_langchain_tool` reads the four hints from `tool.metadata`. `AgentHarness.check_tool_call` looks the tool up **by name in the session's own tool table** and puts its annotations on `ToolCallEvent.annotations` — the session's loop and workflow sub-agents share that path, so a sub-agent's call is judged by the session's tool of the same name (a name the session lacks is asked about).
- **Our own tools.** `read` / `grep` / `find` / `ls`: read-only. `web_search` / `fetch_url`: read-only and open-world. `goal_update` / `goal_complete`: not read-only, not destructive, not open-world (they only keep notes in the session). `bash` / `edit` / `write` and `workflow` say nothing on purpose. A new tool that only reads, or only keeps the agent's own notes, should say so, or `ask` mode will interrupt the user for it.
- **Hints are the author's word**, never verified. There is no per-tool allow-list in `agent.toml`; to let an undeclared third-party tool through, give it annotations or use `auto`.

A prompt that arrives while a turn the client did not start (a `workflow` result delivered through `trigger_message`) is still running waits for that turn rather than failing `busy`; `session/cancel` and closing the session end the wait, and two prompts in flight from the client are still `busy` (`PiAcpAgent._run_prompt`). The ACP v2-draft `state_update` is deliberately not sent: the v1 SDK and the Rust crate under the TUI reject it. Design: `docs/specs/2026-09-22-phase7-extension-api-design.md` §12 and §13.

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
| `extension_trust.py` | `decide_project_trust` / `ProjectTrust`: whether a project may load its own extensions, prompt files and skills; the "skipped" and "could not be saved" notices |
| `trust_fingerprint.py` | The gated resources of a project and a content fingerprint of them |
| `trust_store.py` | `<home>/agent/trust.json`: answers saved per directory with the fingerprint they were about |
| `trust_prompt.py` | The `session/request_permission` question (shape, option ids, how an answer is read) and its explanation message |
| `extension_notices.py` | The "these extensions failed to load" notice (`AgentHarness.failed_extensions`): ACP agent message after `session/new`, headless stderr |
| `system_prompt.py` | `build_system_prompt`, tool snippet/guideline consumption |
| `context_files.py` | AGENTS.md / CLAUDE.md / .pi/SYSTEM.md discovery |
| `git_context.py` | Read-only branch and `git status` snapshot for the system prompt |
| `headless.py` | `-p` one-shot mode, prompt override application |
| `events.py` | Internal events → ACP `session_update` projection |
| `permissions.py` | Tool permission gating (ask / auto / always-approve); in `ask` mode a tool is asked about unless its annotations say it is harmless (see Tool permissions) |
| `create_harness.py` | Harness-level system prompt, coding tool wiring |
| `benchmarks/` | Pelican benchmark suite, evaluator, Docker runner |
