# Using zycode in DeepSeek Harness (ACP)

Because `pi-agent-cli-lc` speaks the standard [Agent Client Protocol (ACP)](https://agentclientprotocol.com) over stdio, it can run inside [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) either as a delegated subagent or as a full session via a community plugin.

## As a subagent (`@deepseek-ai/dsh-subagent-acp`)

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) has an ACP client for subagents: `@deepseek-ai/dsh-subagent-acp`. It starts any ACP agent as a child process, gives it one task, and returns the final text. zycode can be that child. This is not a DSH plugin of ours, and zycode is not affiliated with DeepSeek or with DSH.

> **Verified end to end with a real model (OpenRouter `deepseek/deepseek-v4.1-flash`, one read task and one write task, Linux, `headless` profile only).** Tested with DSH `0.2.2-alpha.2` and `pi-agent-cli-lc` 0.7.0. DSH's main model and zycode's model were both this model. The wiring (spawn, handshake, cwd, permissions, cancel, exit) was also run with a mock model. Details: [Not verified](#not-verified-dsh).

Install the two packages (the `alpha` tag matches DSH `0.2.2-alpha.2`) and add rows to `$DSH_HOME/profiles/<profile>/cordis.patch.yml`. The install adds no row for you:

```sh
dsh plugin --profile headless add @deepseek-ai/dsh-subagent-acp@alpha @deepseek-ai/dsh-tool-subagent@alpha
```

```yaml
- insert:
    - id: subagent-acp-zycode
      name: '@deepseek-ai/dsh-subagent-acp'
      config:
        providerName: zycode
        command: uvx
        args: ['--from', 'pi-agent-cli-lc', '--with', 'pi-agent-core-lc[deepseek]', 'pi-agent-cli']
        permission: reject
        env:
          PI_HOME: /absolute/path/to/zycode-home
    - id: tool-subagent-zycode
      name: '@deepseek-ai/dsh-tool-subagent'
      config:
        provider: zycode
        toolName: subagent_zycode
        maxDepth: provider-managed
```

The model then has a tool `subagent_zycode` (`description`, `prompt`, optional `cwd`). It returns a child id at once; the final text of zycode comes back as a completion notice. The child runs in the `cwd` of the call, or in DSH's current directory.

- **Use an isolated `PI_HOME`** for this child, with its own `agent.toml` and `local.env` (set the model and the key once: `PI_HOME=... uvx --from pi-agent-cli-lc --with "pi-agent-core-lc[deepseek]" pi-agent-cli --setup`). DSH removes credential-shaped variables from the child's environment, so the key must be in `local.env` or in `env` above. Without a model the child answers `Error: No model is set. Run the agent with --setup ...`; this client cannot offer the login.
- **Example model for the isolated home (OpenRouter).** Install the `openai` extra in the `args` (`--with 'pi-agent-core-lc[openai]'`) and put this in `agent.toml`; put `OPENROUTER_API_KEY=...` in `local.env` (mode 600):

  ```toml
  [model]
  provider = "openai"
  id = "deepseek/deepseek-v4.1-flash"
  base_url = "https://openrouter.ai/api/v1"
  api_key_env = "OPENROUTER_API_KEY"
  ```

  For DSH's own main model, add a provider with `api: openai-completions`, `baseURL: https://openrouter.ai/api/v1` and `apiKeyEnv: OPENROUTER_API_KEY` to the `llm-pi-ai` row. DSH reads the key from its own environment. A model that reasons by default can use all of a small `max_tokens` on thought; keep the limit large.
- **Permissions.** DSH answers the child's permission requests by its `permission` field, with no question to a person. `reject` (default) denies every request, so zycode in `ask` mode can only use read-only tools (`read`, `grep`, `find`, `ls`). To let it write, set `permission = "always-approve"` in the `agent.toml` of that isolated home; zycode then runs `bash`, `edit` and `write` without asking. Do not use `auto` for this: it is a mode that asks about what its rules and its model check cannot allow, and DSH's `reject` denies those questions.
- **Warning: do not set `permission: allow` in the DSH row.** zycode asks before it runs a project's own extensions, and the only "yes" it offers is `allow_always` ("Trust and remember"). The DSH client takes the first `allow_once` or `allow_always` option, so it says yes for you, runs the project's code, and saves the answer in `<PI_HOME>/agent/trust.json`. Keep `reject`, and add this to the `agent.toml` of the isolated home so that zycode never asks and never trusts a project:

  ```toml
  [extensions]
  default_project_trust = "never"
  ```

- One process per task: no follow-up messages to a running child, no session resume, and only the final text returns (tool calls and thoughts stay in zycode's own session files under `PI_HOME`).

<a id="not-verified-dsh"></a>
**Not verified:** zycode's `[deepseek]` extra with a real key and DeepSeek's own API (the real run used the `openai` extra through OpenRouter); a long or multi-step real task, and how other models phrase the delegation; the `ask` mode with a write task and a real model (the write task ran with `permission = "auto"`, which meant "no question" at that time; the same run needs `always-approve` now); a cold `uvx` download; the DSH Web and Desktop apps (only `headless` ran); Windows and macOS. The community plugin `@zaimokuza/dsh-acp-adapter` is a different route: see the next section.

## Full session (community plugin `@zaimokuza/dsh-acp-adapter`)

The subagent route above returns only the final text. The community plugin [`@zaimokuza/dsh-acp-adapter`](https://github.com/zaimokuza-yoshiteru/dsh-acp-adapter) (MIT) shows an ACP agent as a full session in the DSH app: messages, tool steps, diffs, permission cards, a model picker, resume. zycode can be that agent.

> **Unofficial.** The plugin is third-party software. DeepSeek has not reviewed or endorsed it, and its README does not list zycode as a tested agent (it lists Devin, CodeBuddy, Antigravity, Claude, Codex, Kimi and Qwen Code). zycode is not affiliated with DeepSeek, with DSH, or with the plugin's author. The plugin works with exactly one DSH version, and DSH is a developer preview.

> **Tested (Linux, DSH Web app in Chrome, real model):** DSH `0.2.1-rc.4` with `@zaimokuza/dsh-acp-adapter` `0.2.1-rc.4.1` (the newest adapter on npm; its `engines.dsh` is exactly `0.2.1-rc.4`), and `pi-agent-cli-lc` 0.8.0 from PyPI. DSH's main model and zycode's model were both OpenRouter `deepseek/deepseek-v4.1-flash`. Newer DSH versions exist (`0.2.1-rc.5`, `0.2.2-alpha.2`). The installer refuses this adapter on `0.2.1-rc.5`, so use the pair above. Details: [Not verified](#not-verified-dsh-community).

Install DSH `0.2.1-rc.4` and the plugin into a profile (here `web`):

```sh
npm install --prefix ./dsh @deepseek-ai/dsh@0.2.1-rc.4
export PATH="$PWD/dsh/node_modules/.bin:$PATH"
dsh plugin --profile web add @zaimokuza/dsh-acp-adapter@0.2.1-rc.4.1
```

Add zycode as an agent. The "Plugins - ACP adapter - Add Agent" page of the app does this and writes the row below into `$DSH_HOME/profiles/web/cordis.patch.yml`. The page has no field for `loginHint`, so write the row by hand if you want one, and restart `dsh`:

```yaml
- id: dsh-acp-adapter
  name: '@zaimokuza/dsh-acp-adapter'
  config:
    agents:
      zycode:
        name: zycode
        command: uvx
        args: ['--from', 'pi-agent-cli-lc', 'pi-agent-cli']
        env:
          PI_HOME: /absolute/path/to/zycode-home
        loginHint: 'PI_HOME=/absolute/path/to/zycode-home uvx --from pi-agent-cli-lc pi-agent-cli --setup'
```

`uvx` must be on the `PATH` of the `dsh` process (a desktop app may not have your shell `PATH`; then use the absolute path). Then start a new session and pick `zycode · ACP` in the model picker.

- **Key and model.** Set them once in a terminal: `PI_HOME=/absolute/path/to/zycode-home uvx --from pi-agent-cli-lc pi-agent-cli --setup`. It writes `agent.toml` and `local.env` (mode 600) in that folder. DSH removes credential-shaped variables from the child's environment and passes only the `env` of the row, so the key must be in `local.env` (do not put it in the row: the plugin stores the row in a plain file). The model of the test (OpenRouter): provider `OpenAI, or an OpenAI-compatible endpoint`, model `deepseek/deepseek-v4.1-flash`, base URL `https://openrouter.ai/api/v1`, key variable `OPENROUTER_API_KEY`. DSH's own model and key are separate: a zycode session does not use them.
- **First run, no model or key.** The plugin does not say that it can run a terminal login (its `initialize` request has no `auth` capability), so zycode offers none (`authMethods: []`), and `loginHint` was not shown. The plugin shows `loginHint` only when the agent answers `auth_required`, and zycode does not do that for this client. The session opens, the model picker shows a model named `mock`, and the first prompt gets the answer `Error: No model is set. Run the agent with --setup, or add a [model] table to <PI_HOME>/agent.toml.` Run `--setup` once in a shell, as above. Open the plugin page and press Re-check, then start a new session.
- **Permissions.** In the default `ask` mode zycode asks for `bash`, `edit` and `write`, and DSH shows its own card with zycode's two options, `Allow once` and `Reject`. `Reject` stops that call (the model may then try another tool and zycode asks again). The plugin's setting "DSH tool approval: Auto approve" did not answer zycode's own tool requests: it covers only requests about DSH tools, and zycode has no access to them (see the MCP note below). Read-only tools (`read`, `grep`, `find`, `ls`) run without a question.
- **Project trust.** If the project has its own `.pi-python/extensions`, `.pi-python/workflows` or `.pi/SYSTEM.md`, zycode asks before it uses them. DSH shows a question card with the original options, `Don't trust` and `Trust and remember`, and nothing is answered for you. This is different from the subagent route, where a DSH client with `permission: allow` answers "Trust and remember" itself. `Don't trust` is not remembered, so the question comes back in the next session. `Trust and remember` saves the answer in `<PI_HOME>/agent/trust.json`. To never be asked, set `default_project_trust = "never"` in the `[extensions]` table of `agent.toml`.
- **Models.** The picker group `zycode · ACP` lists the `[model]` table and every `[[models]]` table of `agent.toml`. A model switch in a running session takes effect on the next turn. After a change of `agent.toml`, press Re-check on the plugin page.
- **Resume, cancel, exit.** After a restart of `dsh`, open an old session and send a prompt: the plugin starts a new agent process with `session/resume`, and zycode remembers the earlier turns. Stop sends `session/cancel`: the turn ends as `cancelled` and a running `bash` command is gone. Ctrl+C and `kill -9` of `dsh` both left no agent process behind (the `kill -9` run had a logging proxy in front of the agent).
- **MCP notice.** On the first prompt of a session DSH passes one MCP server (its own tools), and prepends about 9 KB of instructions about them to the prompt. zycode has no MCP client in the core, so it says `The client passed 1 MCP server ... To use it, install the pi-mcp-py package.` and the DSH tools are not available. This notice is joined to the next sentence without a space in the chat view.
- **The plugin's catalog.** The plugin ships a snapshot of the ACP registry and uses it to pre-fill the "Add Agent" form. It never starts an entry with `npx` or `uvx` for you. An entry that has a `uvx` distribution gets the command name of its console script (for example `fast-agent-acp`) and the hint `uv tool install <package>==<version>`, so the tool must be installed first. An entry that has only `binary` distributions gets an empty command (the form cannot be saved until you type one) and a hint that lists the archive URLs of the platforms, or the website. So a registry entry for zycode would not give a one-step start in DSH: type the `command: uvx` row above, or install the program first.

<a id="not-verified-dsh-community"></a>
**Not verified:** DSH versions other than `0.2.1-rc.4`; Windows and macOS; the DSH Desktop app (only the Web app ran); a cold `uvx` download from a profile with no cache; `session/load`, a fork of a session, and the images, Agent Teams and Auto Review features of the plugin; zycode with `pi-mcp-py` installed, to reach the DSH tools; zycode's `[deepseek]` extra and DeepSeek's own API; `permission = "auto"` or `"always-approve"` in `agent.toml` behind the plugin; the effect of "DSH tool approval: Ask"; long or multi-step tasks; any model other than `deepseek/deepseek-v4.1-flash` for the main run.
