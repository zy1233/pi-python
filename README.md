# pi-python

[![CI](https://github.com/zy1233/pi-python/actions/workflows/ci.yml/badge.svg)](https://github.com/zy1233/pi-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pi-agent-core-lc)](https://pypi.org/project/pi-agent-core-lc/)
[![Python](https://img.shields.io/pypi/pyversions/pi-agent-core-lc)](https://pypi.org/project/pi-agent-core-lc/)
[![License](https://img.shields.io/pypi/l/pi-agent-core-lc)](https://github.com/zy1233/pi-python/blob/main/LICENSE)

**zycode** is an open-source coding agent for your terminal and your editor. It is built from this repository: the ACP agent `pi-agent-cli-lc` and the terminal UI `zypi`. Website: [zycode.dev](https://zycode.dev) ([中文](https://zycode.dev/zh/)).

> Unofficial project: this repository is not affiliated with, endorsed by, or maintained by the official [pi](https://github.com/earendil-works/pi) project or its maintainers.

Python port of [pi-agent-core](https://github.com/earendil-works/pi/tree/main/packages/agent) from the [pi](https://github.com/earendil-works/pi) project, with **LangChain** replacing `pi-ai` for LLM calls.

The loop semantics, event protocol, and tool execution are faithful ports of pi; LangChain is only a `StreamFn` boundary adapter — never a replacement for the agent loop.

This repository is a monorepo containing:

**Python packages** (PyPI):

- `pi-agent-core-lc` / `pi_agent_core`: lightweight core loop, messages, tools, adapters.
- `pi-agent-harness-lc` / `pi_agent_harness`: harness runtime, sessions, queues, hooks, compaction, skills/templates, and local execution env.
- `pi-agent-cli-lc` / `pi_agent_cli`: standard ACP coding agent (`python -m pi_agent_cli`, headless `-p`).
- `pi-web-access-py` / `pi_web_access`: web search and URL fetch extension.
- `pi-goal-x-py` / `pi_goal_x`: goal-planning extension.
- `pi-dynamic-workflows-py` / `pi_dynamic_workflows`: workflow orchestration extension.
- `pi-mcp-py` / `pi_mcp`: optional extension that connects the MCP servers an ACP client (an editor) passes (see [`packages/pi-mcp`](packages/pi-mcp/README.md)).

**Rust TUI** (beta — prebuilt binary in [GitHub Releases](https://github.com/zy1233/pi-python/releases)):

- `zypi`: full-screen terminal UI for interactive coding-agent sessions. Vendored fork of [grok-build](https://github.com/xai-org/grok-build) (Apache-2.0), with all `x.ai/*` vendor extensions removed. See [TUI & Code-Agent Guide](docs/TUI-AND-CODE-AGENT.md) for details.

## Status

| Component | Status | Distribution |
|-----------|--------|--------------|
| `pi-agent-core-lc` | **Stable** | [PyPI](https://pypi.org/project/pi-agent-core-lc/) |
| `pi-agent-harness-lc` | **Stable** | [PyPI](https://pypi.org/project/pi-agent-harness-lc/) |
| `pi-agent-cli-lc` | **Beta** | [PyPI](https://pypi.org/project/pi-agent-cli-lc/) |
| `pi-web-access-py` | **Beta** | [PyPI](https://pypi.org/project/pi-web-access-py/) |
| `pi-goal-x-py` | **Beta** | [PyPI](https://pypi.org/project/pi-goal-x-py/) |
| `pi-dynamic-workflows-py` | **Beta** | [PyPI](https://pypi.org/project/pi-dynamic-workflows-py/) |
| `pi-mcp-py` | **Alpha** | [PyPI](https://pypi.org/project/pi-mcp-py/) (from 0.6.0) |
| `zypi` (Rust TUI) | **Beta** | [GitHub Releases](https://github.com/zy1233/pi-python/releases) (Linux x86_64) |

## Features

**Core runtime** (Phase 1)

- `Agent` — stateful agent with steering / follow-up queues, abort, `subscribe()` event barrier
- `agent_loop` — tool execution loop with pi-compatible event protocol (parallel/sequential tools, `terminate` semantics, `should_stop_after_turn`, `prepare_next_turn`)
- LangChain `StreamFn` adapter (`langchain_stream`) for OpenAI / Anthropic / DeepSeek-style / any `init_chat_model` provider
- Mock stream for tests — the whole suite runs without API keys

**Production hardening** (Phase 2 / 2.5)

- Cross-provider message replay (`transform_messages`: tool-call id normalization, thinking downgrade, image stripping)
- Usage & cost tracking (accumulated across stream chunks, correct for all three real-world reporting shapes) + `CostCalculator`
- Thinking/reasoning: provider param mapping (`reasoning_effort` / Anthropic `thinking` budgets), streamed `thinking_delta` events, Anthropic signature replay, DeepSeek-style `reasoning_content`
- Stream-level retries before the first token (exponential backoff + jitter, `Retry-After` aware)
- Runaway protection: `max_turns` (raises `MaxTurnsExceededError`) and per-tool `tool_timeout`
- Guardrail hooks: `before_llm_call` (with a `ContextBudget` token signal — the compaction hook point), `after_llm_call` (tripwire on raise), `on_agent_end`
- Observability: `on_payload` / `on_response` hooks; every event carries `run_id` / `turn_id`
- Granular stream events: `text_start/end`, `thinking_start/end`, `toolcall_start/end` (plus deltas)
- Structured output: `response_schema` (pydantic model or JSON schema) → parsed `AssistantMessage.structured_output`
- Tool-result images: Anthropic native blocks, user-message fallback elsewhere, stripped when `supports_images=False`
- OpenAI-compatible gateways via `Model.base_url` (SiliconFlow, vLLM, DeepSeek, ...)

**Tool ecosystem** (Phase 3.5)

- Built-in coding tools (`pi_agent_core.coding_tools`): read / bash / edit / write / grep / find / ls with pi-faithful truncation notices and `details` payloads
- LangChain `BaseTool` → `AgentTool` adapter (`from_langchain_tool`) — MCP tools via `langchain-mcp-adapters` work out of the box
- The CLI itself has no MCP client. The optional `pi-mcp-py` extension connects the MCP servers that an ACP client passes in `session/new` (stdio, Streamable HTTP, SSE) and shows their tools as `mcp__<server>__<tool>`; without it, the agent says that their tools are not available in the session

**Coding Agent CLI** (Phase 4 — beta)

- `pi_agent_cli`: standard ACP coding agent over `AgentHarness` — no `x.ai/*` vendor extensions
- Headless mode: `python -m pi_agent_cli -p "prompt"` for scripting and CI
- `/new`, `/resume`, `/quit` session commands; `@` local directory listing
- pi-aligned system prompt engine with tool contributions, `AGENTS.md` context files, `<available_skills>` format
- Project trust: a project's own extensions (`<project>/.pi-python/extensions`, which run code when a session opens), saved workflows (`<project>/.pi-python/workflows`, which become slash commands), `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md` and project-relative skills load only for trusted projects. An ACP client (the TUI) is asked once per directory; "Trust and remember" is saved in `~/.pi-python/agent/trust.json` with a fingerprint of those files, so a `git pull` that changes one of them asks again. Without being asked: list the project under `[extensions] trusted_projects` in `~/.pi-python/agent.toml`, set `default_project_trust` (`ask` by default, `never`, `always`), or run with `--trust-project-extensions` / `PI_TRUST_PROJECT_EXTENSIONS=1`. Headless `-p` runs never ask but honour an earlier answer. Installed extension packages, your own saved workflows in `~/.pi-python/workflows`, `AGENTS.md`, and absolute or `~` skills paths are unaffected
- `workflow` on macOS: the script process has no kernel limit on memory or on the number of processes (measured in CI on `macos-15` and `macos-15-intel`), so a script can start child processes and only the audit hook stands in the way; Linux and Windows have those limits and their tests check them. The default permission mode `ask` asks before every `workflow` call, `auto` and `always-approve` do not. Details and what you can do: [`packages/pi-dynamic-workflows`](packages/pi-dynamic-workflows/README.md#macos-weaker-limits)
- Extensions that fail to load (import error, bad entry point, `activate()` raising, invalid tool name) are skipped, and the agent says so at session start (ACP agent message; headless stderr) instead of just not having them. `PI_HOME` moves the whole home: config, `extensions/`, saved workflows and workflow journals
- Git workspace context (`<git_status>`) is a session-start snapshot in the system prompt (kept stable for the provider prompt cache; the block tells the model to run `git status` for live state); `--no-git-context` or `[git] enabled = false` turns it off. The block (branch name and the paths of changed and untracked files) goes to the model provider with the rest of the system prompt, so turn it off if such names are sensitive

**TUI — `zypi`** (Phase 4 — beta)

- Full-screen Rust terminal UI for interactive coding-agent sessions
- Spawns the Python ACP agent (`python -m pi_agent_cli`) as the backend
- Session management, markdown rendering, Mermaid diagrams, MCP support
- Prebuilt Linux x86_64 binary in [GitHub Releases](https://github.com/zy1233/pi-python/releases); build from source for other platforms
- See [TUI & Code-Agent Guide](docs/TUI-AND-CODE-AGENT.md) for setup and usage

**FrontierHarness Evaluation** (30-task benchmark)

- 21 Terminal-Bench + 9 DeepSWE industrial tasks; Windows local + WSL2 Docker sandbox runtimes
- Side-by-side comparison with official Pi; full report: [FRONTIER-HARNESS-30-EVAL-REPORT.md](docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md)

## Install

### Python packages (PyPI)

```bash
pip install pi-agent-core-lc
pip install pi-agent-harness-lc    # optional: session/harness runtime
pip install pi-agent-cli-lc        # optional: ACP stdio / headless agent
pip install pi-web-access-py       # optional: web search / fetch extension
pip install pi-goal-x-py           # optional: goal planning extension
pip install pi-dynamic-workflows-py  # optional: workflow extension
pip install pi-mcp-py              # optional: MCP servers that the editor passes

# Optional LLM providers:
pip install pi-agent-core-lc[openai]      # ChatOpenAI
pip install pi-agent-core-lc[anthropic]   # ChatAnthropic
pip install pi-agent-core-lc[deepseek]    # ChatDeepSeek / OpenAI-compatible gateways
pip install pi-agent-core-lc[all]         # all providers + harness + cli
```

<details>
<summary>Development install (editable)</summary>

```bash
pip install -e ".[dev]"
pip install -e "./packages/pi-agent-harness"
pip install -e "./packages/pi-agent-cli"
pip install -e "./packages/pi-web-access"
pip install -e "./packages/pi-goal-x"
pip install -e "./packages/pi-dynamic-workflows"
pip install -e "./packages/pi-mcp"   # optional: MCP servers that the editor passes
```

</details>

### TUI binary (`zypi`) — beta

Download the prebuilt binary for Linux x86_64 or macOS arm64 from [GitHub Releases](https://github.com/zy1233/pi-python/releases):

```bash
# Download and install (Linux x86_64)
curl -L -o zypi https://github.com/zy1233/pi-python/releases/latest/download/zypi-linux-x86_64
chmod +x zypi
sudo mv zypi /usr/local/bin/

# macOS arm64 (Apple silicon): the same steps with the file zypi-macos-arm64
# (built in CI, not yet run by a person; see the TUI guide)

# Or build from source (requires Rust toolchain)
cd tui && cargo build --profile release-dist -p pi-pager-bin
# binary at target/release-dist/zypi
```

After installing, set up config and start the TUI — see [TUI & Code-Agent Guide](docs/TUI-AND-CODE-AGENT.md).

### In Zed (custom agent)

The agent speaks ACP over stdio, so [Zed](https://zed.dev) can start it as a custom agent. Install [uv](https://docs.astral.sh/uv/), then add this entry to the Zed settings:

```json
{
  "agent_servers": {
    "pi-python": {
      "type": "custom",
      "command": "uvx",
      "args": [
        "--from", "pi-agent-cli-lc",
        "--with", "pi-agent-core-lc[deepseek]",
        "pi-agent-cli"
      ],
      "env": {}
    }
  }
}
```

- `--with` installs the LangChain provider package. Use `[openai]` or `[anthropic]` for those providers.
- First run: the agent has no model and no key yet. Zed offers the login "Set up model and API key" for a new thread. It runs the agent command with `--setup` in a terminal. The command asks for the provider, the model and the key. It writes the model and the name of the key variable to `~/.pi-python/agent.toml`, and the key to `~/.pi-python/local.env`. To run it yourself in a shell: `uvx --from pi-agent-cli-lc --with "pi-agent-core-lc[deepseek]" pi-agent-cli --setup`. The login in Zed is not tried yet in Zed itself; a script that acts as the client tested it.
- To set up by hand: put the model and the name of the key variable in `~/.pi-python/agent.toml` (start from [`agent.example.toml`](packages/pi-agent-cli/agent.example.toml)). Never write the key in that file. Give it to the agent in an environment variable that Zed can see, in the `env` of the entry above, or in `~/.pi-python/local.env` (`KEY=VALUE` lines).
- To run the code of a checkout, set `command` to the Python of its virtual environment and `args` to `["-m", "pi_agent_cli"]`.
- The agent is not in Zed's ACP registry, so add it as a custom agent as shown.

### In DeepSeek Harness (as a subagent)

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
- **Permissions.** DSH answers the child's permission requests by its `permission` field, with no question to a person. `reject` (default) denies every request, so zycode in `ask` mode can only use read-only tools (`read`, `grep`, `find`, `ls`). To let it write, set `permission = "auto"` in the `agent.toml` of that isolated home; zycode then runs `bash`, `edit` and `write` without asking.
- **Warning: do not set `permission: allow` in the DSH row.** zycode asks before it runs a project's own extensions, and the only "yes" it offers is `allow_always` ("Trust and remember"). The DSH client takes the first `allow_once` or `allow_always` option, so it says yes for you, runs the project's code, and saves the answer in `<PI_HOME>/agent/trust.json`. Keep `reject`, and add this to the `agent.toml` of the isolated home so that zycode never asks and never trusts a project:

  ```toml
  [extensions]
  default_project_trust = "never"
  ```

- One process per task: no follow-up messages to a running child, no session resume, and only the final text returns (tool calls and thoughts stay in zycode's own session files under `PI_HOME`).

<a id="not-verified-dsh"></a>
**Not verified:** zycode's `[deepseek]` extra with a real key and DeepSeek's own API (the real run used the `openai` extra through OpenRouter); a long or multi-step real task, and how other models phrase the delegation; the `ask` mode with a write task and a real model (the write task ran with `permission = "auto"`); a cold `uvx` download; the DSH Web and Desktop apps (only `headless` ran); Windows and macOS. The community plugin `@zaimokuza/dsh-acp-adapter` is a different route: see the next section.

### In DeepSeek Harness (full session, community plugin)

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

## Quick start

```python
import asyncio
from pi_agent_core import Agent, Model
from pi_agent_core.adapters import langchain_stream


async def main():
    agent = Agent(
        initial_state={
            "system_prompt": "You are a helpful assistant.",
            "model": Model(provider="openai", model_id="gpt-4o-mini"),
        },
        stream_fn=langchain_stream,
    )

    agent.subscribe(lambda event, signal: print(event.type))

    await agent.prompt("Hello!")
    await agent.wait_for_idle()


asyncio.run(main())
```

Requires `OPENAI_API_KEY` (or the env var for your provider).

### OpenAI-compatible gateways

Point `Model.base_url` at any OpenAI-compatible endpoint. Use `provider="deepseek"`
when the endpoint streams thinking as `reasoning_content` (SiliconFlow, DeepSeek,
most vLLM gateways) so it surfaces as `thinking_delta` events instead of being dropped:

```python
model = Model(
    provider="deepseek",
    model_id="Qwen/Qwen3-8B",
    base_url="https://api.siliconflow.cn/v1",
    context_window=32_000,
)
agent = Agent(initial_state={"model": model}, stream_fn=langchain_stream)
# pass the key explicitly or via env: DEEPSEEK_API_KEY
```

On DeepSeek's own API (no `base_url`, or one on `deepseek.com`) the thinking switch follows
`Model.reasoning` and `thinking_level`, and while thinking is on the earlier assistant
messages' `reasoning_content` is sent back with every request, which DeepSeek requires once
tools are involved. A gateway gets neither: it is left to its own defaults.

### Structured output

```python
from pydantic import BaseModel


class Person(BaseModel):
    name: str
    age: int


agent = Agent(
    initial_state={"model": model},
    stream_fn=langchain_stream,
    response_schema=Person,  # or a JSON schema dict
)
await agent.prompt("Invent a fictional person.")
await agent.wait_for_idle()

agent.messages[-1].structured_output  # {'name': ..., 'age': ...} (None if not parseable)
```

Schema instructions are injected into the system prompt for every provider; OpenAI-style
providers additionally get native `response_format` enforcement. Streaming stays intact —
the JSON text still flows as `text_delta` events.

### Production configuration

```python
from pi_agent_core import Agent, ContextBudget


def on_payload(payload: dict):  # outgoing request (pre-call)
    log.debug("LLM call", model=payload["model"], tools=len(payload["tools"]))


async def before_llm_call(context, budget: ContextBudget | None):
    if budget and budget.fraction > 0.8:
        return await my_compactor.compact(context)  # durably replaces loop context
    return None


def after_llm_call(context, message):
    if contains_pii(message):  # guardrail tripwire: raise to abort the run
        raise PiiDetected()


agent = Agent(
    initial_state={"model": model, "tools": tools},
    stream_fn=langchain_stream,
    max_turns=25,  # raises MaxTurnsExceededError -> agent.error_message
    tool_timeout=120.0,  # per tool call, seconds; times out into an error tool result
    max_retries=3,  # stream-level retries before the first token
    on_payload=on_payload,
    before_llm_call=before_llm_call,
    after_llm_call=after_llm_call,
)
```

Runnable version: [`examples/production_agent.py`](examples/production_agent.py).

## Built-in coding tools

A port of pi's coding-agent tool suite lives in `pi_agent_core.coding_tools`, decoupled
from the core runtime (tools consume the loop; they are not part of it). Factories bind
each tool to a working directory:

```python
from pi_agent_core.coding_tools import create_coding_tools, create_read_only_tools

tools = create_coding_tools("/path/to/project")  # read / bash / edit / write
audit = create_read_only_tools("/path/to/project")  # read / grep / find / ls
agent = Agent(initial_state={"model": model, "tools": tools}, stream_fn=langchain_stream)
```

| Tool | Group | Behavior |
|------|-------|----------|
| `read` | both | Text (2000-line / 50KB truncation, `offset`/`limit` paging) and images (magic-number sniffing → image blocks) |
| `edit` | coding | Exact-text replacement with unique-match guarantee, fuzzy fallback (smart quotes/dashes/trailing whitespace), CRLF/BOM round-trip, unified-diff `details` |
| `write` | coding | Create/overwrite with automatic parent dirs; per-file mutation queue serializes concurrent writes |
| `bash` | coding | Real shell (`bash -c`; Git Bash on Windows, `shell_path` override), merged stdout+stderr, tail truncation with full output spilled to a temp file (`details.fullOutputPath`), timeout/abort kill the whole process tree, 100ms-throttled streaming updates |
| `grep` | read-only | ripgrep-first (`--json` streaming, kills the process at the match limit); pure-Python fallback when `rg` is missing |
| `find` | read-only | Pure-Python glob walk; basename patterns vs any-depth path patterns (`src/**/*.py`) |
| `ls` | read-only | Case-insensitive sort, `/` dir suffix, dotfiles, entry limit |

Groups mirror pi: `create_coding_tools(cwd)` (read/bash/edit/write — full file operations
plus command execution) and `create_read_only_tools(cwd)` (read/grep/find/ls — inspection
with a no-modification guarantee). Per-tool `create_*_tool(cwd)` factories and
`create_tool(name, cwd)` / `create_all_tools(cwd)` cover custom mixes. Truncation limits
are pi's (2000 lines / 50KB, grep lines capped at 500 chars) with actionable notices
("Use offset=N to continue") so the model can page through anything that was cut.

### Using LangChain / MCP tools

Any LangChain `BaseTool` — including MCP tools produced by `langchain-mcp-adapters` —
plugs into the same loop through the adapter:

```python
from langchain_core.tools import tool
from pi_agent_core.adapters import from_langchain_tool, from_langchain_tools


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"Sunny in {city}"


agent = Agent(
    initial_state={"model": model, "tools": [from_langchain_tool(get_weather)]},
    stream_fn=langchain_stream,
)
```

Parameter schemas come from `tool_call_schema` (LangChain-injected arguments are
excluded); results normalize to pi content blocks (plain strings, content-block lists,
base64 images); `content_and_artifact` artifacts land in `details["artifact"]`; tool
exceptions bubble into `is_error=True` tool results that the model can react to.

## Custom messages

`AgentMessage` is structurally open: anything with a string `role` flows through the
loop and persists to the session (`AgentMessageProtocol`, a runtime-checkable Protocol
in `pi_agent_harness`). The harness ships four custom roles — `bashExecution`,
`custom`, `branchSummary`, `compactionSummary` — and `harness_convert_to_llm` decides
how each role reaches the LLM; unknown roles are dropped at that boundary:

```python
from pydantic import BaseModel
from pi_agent_harness import AgentMessageProtocol
from pi_agent_harness.messages import BashExecutionMessage, harness_convert_to_llm

# Built-in custom role: record a shell run in the session/context
bash = BashExecutionMessage(command="pytest -q", output="97 passed", exitCode=0, timestamp=0)
await harness.append_message(bash)  # persisted; replayed to the LLM as a user message


# Your own role: any object with `role: str` satisfies the protocol
class DeployNote(BaseModel):
    role: str = "deployNote"
    environment: str
    timestamp: int


assert isinstance(DeployNote(environment="staging", timestamp=0), AgentMessageProtocol)
```

To make a custom role visible to the LLM, wrap `harness_convert_to_llm` (or pass your
own `convert_to_llm` to the core loop) and map it to a `UserMessage` — the same pattern
the harness uses for its own roles.

## Events

Agent events (all carry `run_id` and a 1-based `turn_id`):

```
agent_start → turn_start → message_start/end (user)
→ message_start(assistant) → message_update* → message_end(assistant)
→ [tool_execution_start/update/end → message_start/end (toolResult)]
→ turn_end → ... → agent_end
```

`message_update` wraps the granular assistant-stream events:
`text_start/delta/end`, `thinking_start/delta/end`, `toolcall_start/delta/end`.
End events carry the aggregate (`content` full text / complete `tool_call` block).

## Test

```bash
uv run --extra dev --extra harness python -m pytest  # 418 tests, no API keys needed
ruff check . && ruff format --check .
```

Real-API validation (key via env only). A plain `pytest` leaves the `real_llm` tests out, so keys in your shell never cost anything by accident; `-m real_llm` selects them, and each skips when its key is unset:

| Purpose | Command | Env vars |
|---------|---------|----------|
| Automated regression | `pytest -m real_llm -v` | `REAL_LLM_API_KEY`, optional `REAL_LLM_BASE_URL` / `REAL_LLM_MODEL` |
| Provider matrix (OpenAI, Anthropic, DeepSeek, SiliconFlow) | `pytest -m real_llm pi_agent_core/tests/test_provider_matrix.py -v` | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `REAL_LLM_API_KEY`; CI runs it weekly (`provider-matrix.yml`) |
| Manual pre-release smoke | `python scripts/smoke_real_api.py` | `SMOKE_API_KEY` (or `REAL_LLM_API_KEY`), `SMOKE_BASE_URL`, `SMOKE_MODEL` |
| **pi TUI foundation case** | `python scripts/smoke_pelican.py` | `REAL_LLM_API_KEY`; saves SVG to `~/.pi-python/benchmarks/pelican/` — see [docs/benchmarks/PELCAN-BICYCLE.md](docs/benchmarks/PELCAN-BICYCLE.md) |

Use `.venv-test-real` for pytest real-LLM tests; the smoke script can run from the main `.venv`.

## Documentation

| Document | Contents |
|----------|----------|
| [AGENTS.md](AGENTS.md) | Project overview, module map, invariants, current status |
| [docs/DESIGN.md](docs/DESIGN.md) | Project-wide architecture and module design |
| [docs/PLAN/PLAN-PHASE1.md](docs/PLAN/PLAN-PHASE1.md) | Phase 1 original planning document (historical) |
| [docs/AUDIT/AUDIT-2026-07-02.md](docs/AUDIT/AUDIT-2026-07-02.md) | Core-layer audit: findings, fixes, Phase 2.5 enhancements |
| [docs/AUDIT/AUDIT-H1.md](docs/AUDIT/AUDIT-H1.md) ~ [H4](docs/AUDIT/AUDIT-H4.md) | Harness batch audits (H1 session, H2 runtime, H3 compaction, H4 skills/env) |
| [P0 TUI spike](docs/AUDIT/SPIKE-P0-GROK-TUI.md) | grok pager `x.ai/*` strip inventory |
| [Phase 2 spec](docs/specs/2026-05-25-phase2-production-enhancements-design.md) | Usage/cost, thinking/reasoning, transform_messages |
| [Phase 3 spec](docs/specs/2026-07-03-phase3-agent-harness-design.md) | AgentHarness: sessions, compaction, skills, templates, ExecutionEnv |
| [Phase 3.5 spec](docs/specs/2026-07-03-phase3.5-tool-ecosystem-design.md) | Built-in coding tools + LangChain adapter |
| [Phase 4 spec](docs/specs/2026-08-25-phase4-coding-agent-cli-design.md) | Coding Agent CLI: forked grok TUI + standard ACP agent |
| [Phase 5 spec](docs/specs/2026-09-02-phase5-prompt-engine-design.md) | Coding Agent prompt engine: system prompt assembly |
| [Phase 6 spec](docs/specs/2026-09-24-phase6-extended-integrations-design.md) | Git context injection and multi-provider test matrix |
| [**TUI & Code-Agent Guide**](docs/TUI-AND-CODE-AGENT.md) | Installation, configuration, usage for `zypi` TUI and `pi_agent_cli` |
| [Windows notes](docs/WINDOWS.md) | Phase 4 Windows setup, WSL TUI build, spawn/config |
| [Benchmark report](docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md) | FrontierHarness 30-task evaluation & Pi comparison |

## Concept mapping (pi → Python)

| pi (TypeScript) | pi-python |
|-----------------|-----------|
| `@earendil-works/pi-agent-core` | `pi_agent_core` |
| `packages/agent/src/harness` | `pi_agent_harness` |
| `@earendil-works/pi-ai` `streamSimple` | `langchain_stream` |
| `AgentMessage` | `UserMessage` / `AssistantMessage` / `ToolResultMessage` + custom |
| `Model.baseUrl` | `Model.base_url` |
| `onPayload` / `onResponse` | `on_payload` / `on_response` |
| `agent.prompt()` | `await agent.prompt()` |
| `agent.continue()` | `await agent.continue_()` |

## Roadmap

### Completed

- **Phase 1 (MVP loop)** — done: `Agent` + `agent_loop` + event protocol + tool execution
- **Phase 2 (production enhancements)** — done: `transform_messages`, usage/cost, thinking/reasoning
- **Phase 2.5 (core hardening)** — done: retries, runaway protection, observability, granular events, guardrail hooks, `ContextBudget`, structured output, tool-result images
- **Phase 3 (AgentHarness)** — done: H1 session tree, H2 runtime, H3 compaction/tree navigation, H4 skills/templates/system prompt/LocalExecutionEnv ([design doc](docs/specs/2026-07-03-phase3-agent-harness-design.md))
- **Phase 3.5 (tool ecosystem)** — done: all 7 built-in tools (read/bash/edit/write/grep/find/ls), group factories, and the LangChain `BaseTool` adapter ([design doc](docs/specs/2026-07-03-phase3.5-tool-ecosystem-design.md))
- **Phase 4 (Coding Agent CLI — beta)** — done: `tui/` vendored grok-build fork (Apache-2.0); `pi_agent_cli` standard ACP agent; TUI binary `zypi`; `x.ai/*` vendor RPCs removed; session management (`/new` `/resume` `/quit`); headless `-p`; `config.toml` + Windows notes ([design doc](docs/specs/2026-08-25-phase4-coding-agent-cli-design.md), [Windows](docs/WINDOWS.md), [TUI guide](docs/TUI-AND-CODE-AGENT.md))
- **Phase 5 (Coding Agent prompt engine)** — done: pi-aligned `build_system_prompt` + tool contributions + context files + `<available_skills>` ([design doc](docs/specs/2026-09-02-phase5-prompt-engine-design.md))
- **Phase 6 (extended integrations)** — done: git workspace context in the system prompt; opt-in multi-provider live matrix. MCP stays a caller-side `BaseTool` import ([design doc](docs/specs/2026-09-24-phase6-extended-integrations-design.md)); the optional `pi-mcp-py` extension (2026-10) connects the servers an editor passes, and the core, harness and CLI still hold no MCP client ([design doc](docs/specs/2026-10-23-mcp-extension-design.md))
- **Phase 7 (extension packages)** — done: `pi-web-access-py`, `pi-goal-x-py`, `pi-dynamic-workflows-py` ([design doc](docs/specs/2026-09-22-phase7-extension-api-design.md))
- **FrontierHarness Eval** — done: 30-task benchmark (21 Terminal-Bench + 9 DeepSWE); Windows + WSL Docker runtimes; side-by-side Pi comparison ([report](docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md))

### Next

- **TUI platform expansion** — planned: prebuilt binaries for macOS x86_64 and Windows; auto-update support

## License

Python packages (`pi-agent-core-lc`, `pi-agent-harness-lc`, `pi-agent-cli-lc`) are **MIT**.

The `tui/` directory is a vendored fork of [xai-org/grok-build](https://github.com/xai-org/grok-build) and remains **Apache License 2.0**. See `tui/LICENSE`, `tui/NOTICE`, and `tui/THIRD-PARTY-NOTICES`. `tui/` is not included in Python sdists or wheels.
