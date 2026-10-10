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

## Model selection (`/model`)

Standard ACP **Session Config Options** — `session/set_model` is gone upstream (SDK ≥ 0.11). `new_session` / `load_session` / `resume_session` return `configOptions` with one select (`id="model"`, `category="model"`, `currentValue`, `options`); the client switches with `session/set_config_option` (`PiAcpAgent.set_config_option` → `harness.set_model`, response carries the full option set). Choices = `[model]` + optional `[[models]]` tables in `agent.toml` (`ModelChoice`, `CliConfig.model_choices()`). The model is persisted in the session (`model_change`) and restored on load/resume if still configured. Legacy `_meta` hints (`pi/currentModelId`, `pi/currentModelDisplayName`, `pi/provider`) are still emitted for older clients. Each choice carries its own `api_key_env`: `get_api_key` answers only for that choice's provider (`factory.api_key_for_choice`), and `Model.reasoning` follows `CliConfig.model_reasoning` whichever choice is active.

## Project trust

A project's own content is used only if the project is trusted: `<cwd>/.pi-python/extensions` (imported — i.e. executed — when a session opens), `<cwd>/.pi-python/workflows` (each `*.py` becomes a slash command with a description the repository wrote, and a command that has the model run the script), `<cwd>/.pi/SYSTEM.md`, `<cwd>/.pi/APPEND_SYSTEM.md`, and `[skills].paths` entries relative to the project (`.pi/skills`) — `trust_fingerprint.gated_project_resources` lists them. Never read the decision from a file inside the project, or a repository could trust itself. Not gated: entry-point packages, `~/.pi-python/extensions`, `<pi home>/workflows` (the user's own), `AGENTS.md` / `CLAUDE.md` (upstream loads them whatever the trust), prompts set in `agent.toml`, and absolute or `~` skills paths. The workflows are read by the dynamic-workflows extension, not by the CLI: it asks `pi.project_trusted` (the harness's `trust_project_extensions`, final by the time extensions activate), so an extension that reads anything under `<cwd>/.pi-python/` must do the same. Design: `docs/specs/2026-09-22-phase7-extension-api-design.md` §4.4.

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
- **What `zypi` offers is what the agent honours: two modes.** The TUI tells the agent `ask` or `always-approve` (`pi/yolo_mode_changed`: Shift+Tab, `--always-approve`, `--permission-mode bypassPermissions`). It also used to offer Plan, Auto and a settings "Default", which the agent does not honour — there is no `session/set_mode` handler (Plan restricted nothing while the screen said `plan`), `auto` behaves like `always-approve` (the screen promised a classifier), and `_permission_mode_from_notification` drops `default`. They are hidden in the pager (`tui/crates/codegen/pi-pager/src/app/agent_modes.rs`: three constants, the code behind them kept) and `--permission-mode` takes only `default` and `bypassPermissions`. To give the agent a real Plan or Auto, implement it here and flip the constant there; the pager's old paths and tests come back with it (`docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md` section 10.13).

A prompt that arrives while a turn the client did not start (a `workflow` result delivered through `trigger_message`) is still running waits for that turn rather than failing `busy`; `session/cancel` and closing the session end the wait, and two prompts in flight from the client are still `busy` (`PiAcpAgent._run_prompt`). The ACP v2-draft `state_update` is deliberately not sent: the v1 SDK and the Rust crate under the TUI reject it. Design: `docs/specs/2026-09-22-phase7-extension-api-design.md` §12 and §13.

## TUI spawn

The Rust TUI (`zypi`) spawns the Python agent via (priority order):

1. `PI_AGENT_COMMAND` env var
2. `[agent].command` in `agent.toml`
3. `PI_PYTHON` env var (appends `-m pi_agent_cli`)

**The agent's stderr.** The TUI owns the screen, and its own fd 2 is `/dev/null` (`pi_tty_utils::redirect_native_stderr`), so the agent's stderr goes to `<home>/logs/agent.stderr.log` instead (`acp/spawn.rs`, `agent_stderr`): appended across runs, one `--- agent started <time> (zypi pid N) ---` line per spawn, mode 0600 on Unix (a traceback can quote prompts, paths and keys), and a log longer than 1 MiB is moved to `agent.stderr.log.1` at the next spawn. A log that cannot be opened is skipped, not fatal. This is where a Python traceback or a warning from the agent ends up; the agent should still write diagnostics to stderr, not stdout (stdout is the ACP channel). `-p` runs are not redirected: there the agent's stderr is the user's terminal.

**When the agent exits on its own.** The stdio bridge (`acp/spawn.rs`) waits on the child next to the cancel token, so an agent that dies is noticed instead of leaving requests to hang. It drains what the agent had already written, builds one message (`agent_exit_message`: the program, its exit status, and the last 12 lines / 4 KiB of `agent.stderr.log`), fails every pending request, and ends the bridge with that message. `connect()` reports a failed `initialize` as that message (`explain_failed_start`): `failed to spawn ACP agent` plus the program and `PI_AGENT_COMMAND` for a program that is not there, `exited on its own (exit status: 1)` plus the traceback's tail for a Python that lacks `pi_agent_cli`. Once the TUI is up, `AgentProcessGuard` prints `Error: …` on the restored terminal. So an agent must not exit on an error it can handle, and anything it prints to stderr before it dies reaches the user. `zypi`'s own exit status is unchanged. `tui/crates/codegen/pi-pager/tests/python_agent_e2e.rs` runs the pager's ACP client against this agent, including these failures (needs a Python with `pi_agent_cli`: `PI_E2E_PYTHON`; `PI_E2E_REQUIRED=1` makes its absence a failure, as in `tui-ci.yml`).

## MCP servers

The agent connects to **no** MCP server (Phase 4 non-goal; tools come from the harness and its extensions). `session/new`, `load` and `resume` accept `mcp_servers` and ignore them, which departs from ACP, whose session setup says an agent MUST support stdio servers; `initialize` advertises no `mcpCapabilities` (no `http`, no `sse`). The departure is made visible instead of silent: each time a session is set up with a non-empty list, the agent logs a warning and sends one agent message naming the servers (`mcp_notice`, from `_deferred_session_setup`, after the response has flushed like the other notices). Only the **names** are used: `env`, `headers`, `args` and `url` routinely hold tokens. `zypi` itself sends an empty list (it used to forward whatever it found in `.mcp.json`, `~/.claude.json` and Cursor's `mcp.json`), so there the notice never appears; it is for other ACP clients (an editor that passes its own servers). `tests/test_mcp_notice.py`. To implement MCP, start from the notice: it is the one place that sees the list.

## Listing sessions

`session/list` returns `SESSION_LIST_PAGE_SIZE` (50) sessions at a time, newest first, with `nextCursor` while more follow; the TUI keeps asking until it is gone. The order is the harness's (`JsonlSessionRepo.list_page`): creation time, which the session file names start with, so a page reads a file's header only when it gets that far and costs the files it looks at, not the directory (a `cwd` filter makes it skip other projects' files). `nextCursor` is opaque to the client and carries the file name the next page continues below; the agent keeps nothing between requests, so a cursor survives a restart, a session created meanwhile is simply newer than the cursor, and one deleted meanwhile moves nothing. A page that carries a cursor is never followed by an empty one (the agent looks one session ahead). A string the agent did not issue is `invalid_params`. `updated_at` is still the file's mtime, so it need not be in the order of the list.

Looking a session up by id (`load` / `resume` / `pi/session/delete`) is `JsonlSessionRepo.find`: the file name ends in `-<id>.jsonl`, so only that file's header is read. `scripts/session_list_bench.py` measures all of this against the real agent (`start`, first page, every page, `session/load`) on a synthetic home; numbers and the decisions that rest on them are in `docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md` §10.11.

## Two things the TUI and the agent agree on beyond ACP v1

- **`_meta.sessionId` on `session/new`** is the TUI's `--session-id`: ACP v1 has no field for the id of a new session. The agent (`_chosen_session_id`, `_create_session_with_id`) takes it as the session's id. It must be a UUID in any form `uuid.UUID` reads, and comes back canonical (lower case, hyphens); anything else is `invalid_params`, because the id ends up in a file name. An id already in use is `invalid_params` too, whatever directory the session that has it belongs to: the repository would not refuse it (file names start with the creation time, so a second file with the same id fits beside the first), and a later `session/load` would find either. Requests for the same id at once make one session (`_chosen_id_lock`); two agent processes on one home are not covered. The question is `JsonlSessionRepo.has_session`, which opens a file only when its name does not already say it belongs to another session (`find` would read every header for an id nobody has); the one thing it takes on trust is a file renamed by hand to carry another id. What a free id still costs is the directory listing, about 0.07 s at 1,000 sessions and 0.3 s at 5,000 on both Windows and WSL2, against 8–20 ms for a session with an id of the agent's own (`scripts/session_list_bench.py`, the last block of its output). Nearly all of that is `LocalExecutionEnv.list_dir` building a `FileInfo` per entry (`os.listdir` of the same 5,000 files: 2–11 ms), which `session/list` pays on every page too. `tests/test_session_chosen_id.py`.
- **`_meta["pi/cwd"]` in the response that opens a session** (`new`, `load`, `resume`) is the directory the session works in. `load` and `resume` take a `cwd` as well, and the one saved with the session wins; a client that only knows where it was started (`zypi --resume <id>` from another directory) learns the real one here. Nothing in the TUI reads it yet.

## ACP contract tests

`tests/test_acp_stdio_contract.py` drives an agent *process* over stdio the way the TUI does: `initialize`, `session/new`, streamed `prompt`, the `model` config option, `session/list` (first-prompt title), `session/load` replay and `session/resume` across a restart, `session/close`, a client-chosen id on `session/new` (and a taken one refused), the directory `session/load` reports, unknown methods answered with an error, exit code 0 on stdin EOF. It uses the mock LLM (`PI_USE_MOCK=1`) and a throw-away `PI_HOME`. Set `PI_ACP_CONTRACT_COMMAND` to run the same suite against another stdio ACP agent (it must honour `PI_HOME` and answer prompts with `Hello from mock`, without network), e.g. a future `pi-rust`.

The mock LLM never calls a tool, so the tool side of the contract has its own file: `tests/test_acp_stdio_tools.py` drives `tests/_tool_agent.py` (the real `serve()` with a scripted LLM turn that calls one tool; `PI_TEST_TOOL_CALL` picks the call, `PI_TEST_PERMISSION` the mode) and checks, over the wire, what the pager relies on: in `ask` mode a `write` call is announced as a `tool_call` update, then put as `session/request_permission` (title, kind, `raw_input`, the two options `allow-once` / `reject-once`) before the tool runs; `allow-once` runs it, `reject-once` and a `cancelled` outcome do not and the turn still ends; `auto` never asks; `session/cancel` ends a prompt as `cancelled` (while a question is open, and while a `bash` call runs, which is then reaped; POSIX only), and is harmless with nothing running or for an unknown session. The scripted stream ends `aborted` once the signal is set, as the LangChain adapter does, so these tests do not depend on a real provider's abort handling.

`__main__.serve()` starts `run_agent(..., use_unstable_protocol=True)`: `initialize` advertises `session/close` and `session/resume`, which the SDK serves only with that flag.

## Stopping the agent

The agent must reap the tools it is running when its client goes away: the TUI closes the agent's stdin and kills it only after a grace period (`acp/spawn.rs`, `AGENT_EOF_GRACE`). `serve()` ends on EOF on stdin (`run_agent` returns, `asyncio.run` cancels the turns in flight, each `bash` tool kills its process group) and on SIGTERM / SIGHUP (the main task is cancelled, same path); all exit 0. SIGHUP matters because a terminal that goes away, or a TUI killed while it is the session leader, sends it to the foreground process group, which includes the agent; its default action kills the agent before it can reap anything. A repeated signal gets the default action (the handlers remove themselves on the first one), so an agent that hangs while it stops — a thread that cannot be cancelled keeps `asyncio.run` from returning — can still be killed. SIGKILL cannot be handled and orphans the tools. `tests/test_acp_shutdown.py` runs `tests/_tool_agent.py` (the real `serve()` with a scripted `bash` call, or a stuck thread) and checks EOF, SIGTERM, SIGHUP and the repeated signal; POSIX only.

**`-p` runs** have no stdin to close: `zypi -p` starts the agent as a child on the user's own terminal. They get the same stop signals (`_print_main`: the turn is cancelled, the tools are reaped, the exit status is 128 + the signal number), and zypi names itself in `PI_AGENT_PARENT_PID`, which the agent turns into a watch: every 0.5 s it checks `os.getppid()` and stops the same way once that pid is no longer its parent. That is how a zypi killed alone (`kill -9`, `kill <pid>`, an IDE's stop button) is noticed; a terminal hang-up or Ctrl+C reaches both anyway. The variable counts only if it names the agent's actual parent, or a process that is already gone (the parent died while the agent was starting); a live process that is not the parent is a wrapper script in between, and the watch stays off. `main()` removes the variable from the environment first thing, so a tool or a nested `-p` cannot inherit it. POSIX only: on Windows `getppid()` keeps naming the dead parent. `tests/test_print_shutdown.py` runs `tests/_print_agent.py` (the real `main()` with a scripted `bash` call) and checks both signals, a killed parent, an unrelated live pid, a parent that is already gone, and which values count. End to end, against a built `zypi` (Linux), `scripts/tui_pty/exit_matrix.py` (TUI: six ways out, two launch styles, with or without `--sandbox`) and `scripts/tui_pty/print_exit.py` (`zypi -p`, only zypi signalled) check that nothing is left running; see the README there.

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
| `extension_trust.py` | `decide_project_trust` / `ProjectTrust`: whether a project may load its own extensions, saved workflows, prompt files and skills; the "skipped" and "could not be saved" notices |
| `trust_fingerprint.py` | The gated resources of a project and a content fingerprint of them |
| `trust_store.py` | `<home>/agent/trust.json`: answers saved per directory with the fingerprint they were about |
| `trust_prompt.py` | The `session/request_permission` question (shape, option ids, how an answer is read) and its explanation message |
| `extension_notices.py` | The "these extensions failed to load" notice (`AgentHarness.failed_extensions`): ACP agent message after `session/new`, headless stderr |
| `mcp_notice.py` | The "these MCP servers were ignored" notice: names only, never `env` / `headers` / `args` / `url` |
| `system_prompt.py` | `build_system_prompt`, tool snippet/guideline consumption |
| `context_files.py` | AGENTS.md / CLAUDE.md / .pi/SYSTEM.md discovery |
| `git_context.py` | Read-only branch and `git status` snapshot for the system prompt |
| `headless.py` | `-p` one-shot mode, prompt override application |
| `events.py` | Internal events → ACP `session_update` projection |
| `permissions.py` | Tool permission gating (ask / auto / always-approve); in `ask` mode a tool is asked about unless its annotations say it is harmless (see Tool permissions) |
| `session_list.py` | `session/list` display data: first-user-message title and file-mtime `updated_at` (bounded read), `SESSION_LIST_PAGE_SIZE`, the opaque `nextCursor` (`encode_cursor` / `decode_cursor`) |
| `create_harness.py` | Harness-level system prompt, coding tool wiring |
| `benchmarks/` | Pelican benchmark suite, evaluator, Docker runner |
