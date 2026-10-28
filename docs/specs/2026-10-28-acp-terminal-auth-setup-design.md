# `--setup` and the ACP terminal login: design

> Scope: let a user who has no model and no API key fix that from inside an ACP client such as Zed.
> Decided: 2026-10-28. The owner chose: the key goes to `<home>/local.env`; the scope is `--setup`, the `authMethods` entry and two small fixes; the registry question is posted as is ([registry issue 726](https://github.com/agentclientprotocol/registry/issues/726)).
> Code: `packages/pi-agent-cli/pi_agent_cli/{setup_command,auth_methods,agent,config,factory,headless,__main__}.py`.
> Tests: `packages/pi-agent-cli/tests/{test_setup_command,test_acp_auth_methods,test_first_run_errors,test_config}.py`.

---

## 1. Problem

A first run has no `agent.toml` and no key. Before this change, the user saw this:

- The turn failed with a text about a missing package (`init_chat_model` and `pip install langchain`). The real cause is that no model is set: `CliConfig` falls back to the provider `mock`, and LangChain has no such provider.
- `python -m pi_agent_cli -p` printed an empty line on stdout and exited 0.
- An ACP client had nothing to show: `initialize` answered `authMethods: []`, so Zed could not offer a login. The only fix was to write `agent.toml` and `local.env` by hand.

## 2. Facts that the design rests on

The Zed facts come from the Zed sources (`agent_servers`, `acp_thread/auth_methods.rs` and their tests), read on 2026-10-27. They were not run inside Zed.

| Fact | Where it matters |
|------|------------------|
| Zed declares `clientCapabilities.auth.terminal = true` and `_meta["terminal-auth"] = true`. | Form of the method (§3.2). |
| For a method of type `terminal`, Zed runs the command of the custom agent with the arguments of the method **added** at the end (test: `["--acp", "--verbose", "first-class-auth", ""]`), and merges `env`. It does not call the `authenticate` request for such a method. | `args = ["--setup"]` gives `<agent command> --setup`. |
| The registry text `AUTHENTICATION.md` says that `args` "replace" the agent arguments. Zed adds them. | For `--setup` both readings give the same command line. |
| For a method of the default type `agent` with `_meta["terminal-auth"]` = `{label, command, args, env}`, Zed runs that command. A method of type `terminal` wins when both are present. | Form for older clients (§3.2). |
| The registry validator declares only `_meta["terminal-auth"] = true` and accepts a method that has `type` `agent` or `terminal`, or that has one of the `_meta` keys. | Both forms pass it. |
| The SDK `agent-client-protocol` 0.12 drops an item of `authMethods` that does not validate, with no error. `AuthMethodTerminal` has no `type`, so it is not on the wire (`authMethods: []`). `TerminalAuthMethod(type="terminal", …)` is. | The wire test reads raw JSON (§5). |
| `zypi` announces `fs` and `terminal` (set from its flags), no `auth`, and an empty `_meta` (`acp/mod.rs`, `client_capabilities_meta`). The pager treats an empty `authMethods` as a standard ACP agent (`standard_acp` in `event_loop.rs`). | `zypi` must keep getting `[]` (§3.2). |

## 3. Design

### 3.1 The dialog (`setup_command.py`, flag `--setup`)

It needs a terminal on stdin and stdout (exit status 2 if there is none). It asks in plain lines and reads the key with `getpass`, so the key is not echoed. It saves nothing when the input ends or Ctrl+C is pressed: all questions come before the first write.

| State of the home | Questions |
|-------------------|-----------|
| No model (the provider is `mock`) | Provider (OpenAI, Anthropic, DeepSeek), model id, base URL (OpenAI and DeepSeek only), name of the key variable (default: the usual name of the provider), the key. |
| A model, and its `api_key_env` has no value | The key. |
| A model, and its `api_key_env` has a value | "Replace the key?" Then the key. |
| A model with no `api_key_env` | Nothing. It says so. |

What it writes:

- `agent.toml`: a `[model]` table with `provider`, `id`, `base_url` (if asked) and `api_key_env`. A new file gets a short header. In a file that has no `[model]` table, the table is added at the end, and the text that is already there stays to the byte, line ends included (`tomllib` cannot write a file back with its comments). The table is parsed again before it is written.
- `local.env`: the line `NAME=key`. Other lines stay. A second line for the same name goes, because the first line of a name is the one that counts (`load_local_env`).
- Both files are written by a file beside them that is renamed over the target, so a crash never leaves half a file. A symbolic link (a dotfile manager makes them) is followed. `local.env` is created with mode 0600, and `fchmod` is called before the key is written, so a temporary file of a killed run cannot make the key public. On Windows the file has the rights of the user folder.

What it refuses, before the first question: an `agent.toml` that is not TOML, a `[model]` table that sets no provider (the user wrote it; the program does not guess), and a home that has only the old `config.toml` (a new `agent.toml` would hide that file, and all its settings, from the agent).

The key is never printed, logged or put in `agent.toml`. The final text says where it is, and, when the LangChain package of the provider is not installed, how to install it (`--with "pi-agent-core-lc[openai]"` for `uvx`, `pip install` otherwise).

### 3.2 The authentication method (`auth_methods.py`)

`initialize` looks at what the client announces:

| The client announces | `authMethods` |
|----------------------|---------------|
| `auth.terminal = true` (Zed) | One method of type `terminal`: id `pi-setup`, name "Set up model and API key", `args = ["--setup"]`. |
| Only `_meta["terminal-auth"] = true` | One method of the default type with `_meta["terminal-auth"]` = `{label, command: sys.executable, args: ["-m", "pi_agent_cli", "--setup"], env: {PI_HOME: <home>}}`. The terminal of an older client has not the environment that the agent got, so the home is given. |
| Neither, or the value is not exactly `true` | `[]`, as before. |

An agent that was given its own `config` or `stream_fn` (the tests, an embedding program) offers nothing: it does not read its settings from the files.

### 3.3 When a new session is sent to the login (`agent.py`)

`config_problem(config)` says in one sentence why a configuration cannot run a turn, with no network:

- No model is set (the provider is `mock`). The scripted LLM `PI_USE_MOCK=1` has no problem.
- The variable named by `api_key_env` has no value, and the variable that the provider's SDK reads (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`) has none too. The SDK takes its own variable when `api_key_getter` gives it nothing, so a user who has only that variable is not turned away.

`session/new` answers `auth_required` (code -32000; the message is the sentence plus "Choose "Set up model and API key" to set it up."; `data = {"authMethods": ["pi-setup"]}`) when there is a problem **and** the client can run a terminal login. No session is made. Every other client gets its session, and its first turn shows the message of `no_model_message()`.

`session/load` and `session/resume` never refuse: old history can be read without a model.

`authenticate("pi-setup")` answers `{}` when there is no problem, and `auth_required` when there is. Any other id is `invalid_params`.

**Read again.** `--setup` runs in the terminal of the client while the agent process is up. At `session/new`, `load` and `resume`, an agent that has a problem reads `agent.toml` and `local.env` again. A configuration that works is never read again, so it does not change under a running agent. A key that is replaced needs a new agent process: `load_local_env` never overwrites a variable that is set, and the final text of `--setup` says so.

### 3.4 The two small fixes

- `default_stream_fn()` wraps the LangChain stream function. A model of the provider `mock` gets one `error` event with `no_model_message()`: "No model is set. Run the agent with --setup, or add a [model] table to <agent.toml>." The scripted LLM and the real providers are not touched.
- `run_print` writes `error: <reason>` to stderr and returns 1 when the model fails. Stdout keeps only the text of an answer.

## 4. Decisions and the choices that were not taken

| Choice | Taken | Why not the other |
|--------|-------|-------------------|
| Where the key lives | `local.env` in the home (the owner's answer) | An OS key store needs a new dependency and does not work the same on all hosts. The README already tells users to use `local.env`. |
| Offer the method to every client | No: only to clients that announce terminal login | A client that cannot run it would show a button that does nothing. The ACP text says the agent "may" include terminal entries when the client says it can. |
| Refuse `session/load` too | No | A user can read old threads without a model. |
| Read the files again at every session | No: only while the configuration has a problem | A working agent should not change while it runs. |
| Rewrite `agent.toml` as a whole | No: add a table at the end | `tomllib` has no writer that keeps comments and line ends. |
| More providers in the dialog | No: three (OpenAI, Anthropic, DeepSeek) | They are the three extras of `pi-agent-core-lc`. A user of another provider writes the `[model]` table by hand (`agent.example.toml`). |

## 5. Verification

- 42 tests for the dialog and the files (`test_setup_command.py`, `test_config.py`), 32 for the ACP side (`test_acp_auth_methods.py`), 6 for the two fixes (`test_first_run_errors.py`).
- The wire test starts the agent process, sends `initialize` as raw JSON and reads the raw answer. It fails on a plain `AuthMethodTerminal`, which the SDK would drop.
- Mutation checks: the dialog (12 changes), the ACP side (11 changes). All were caught. The mode of `local.env` is checked on Linux only.
- A script plays Zed against one live agent process on Linux: `initialize`, `session/new` (error), the agent command with `--setup` in a pseudo-terminal, `session/new` again (a session with the new model), `authenticate`.
- The dialog was run with the real `input()` and `getpass()` in a pseudo-terminal: the key is not echoed, the file mode is 0600.

**Not verified.**

- Nothing was run inside Zed. What Zed's thread view does after the terminal command exits (it should try `session/new` again) is read from the Zed sources, not seen.
- Whether the stable release of Zed has first-class terminal login. The older form (§3.2) is there for clients that do not.
- The dialog on Windows in a real console. The tests there use a script in place of the terminal.

## 6. Open items

- Registry: the question of how a PyPI-only agent can be listed is posted as [registry issue 726](https://github.com/agentclientprotocol/registry/issues/726). Zed reads only `binary` and `npx` entries of the registry, so a listing needs one of those two forms. Nothing is started until the maintainers answer.
- An `env_var` method (the SDK has `EnvVarAuthMethod`) could let a client ask for the key in its own form. Not used: it is not known which clients show such a form.
