# zycode

English | [简体中文](README.zh-CN.md)

[![CI](https://github.com/zy1233/zycode/actions/workflows/ci.yml/badge.svg)](https://github.com/zy1233/zycode/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pi-agent-cli-lc)](https://pypi.org/project/pi-agent-cli-lc/)
[![Python](https://img.shields.io/pypi/pyversions/pi-agent-cli-lc)](https://pypi.org/project/pi-agent-cli-lc/)
[![License](https://img.shields.io/pypi/l/pi-agent-cli-lc)](https://github.com/zy1233/zycode/blob/main/LICENSE)

**zycode** is an open-source coding agent for your terminal and your editor. It speaks the standard [Agent Client Protocol (ACP)](https://agentclientprotocol.com) over stdio, runs in the terminal (`zypi`), in VS Code (`zycode.zycode`), in Zed, or headlessly (`-p`), and is built on a modular Python agent runtime.

Website: [zycode.dev](https://zycode.dev) ([中文](https://zycode.dev/zh/))

> Unofficial project: this repository is not affiliated with, endorsed by, or maintained by the official [pi](https://github.com/earendil-works/pi) project or its maintainers. It is also not affiliated with, endorsed by, or sponsored by any maker of an AI model (for example OpenAI, Anthropic, Google, DeepSeek, xAI, Moonshot AI or Alibaba Cloud). Model and company names only say which models you can connect to. They belong to their owners.

## Overview

```text
  zypi (Rust TUI)  /  VS Code (zycode.zycode)  /  Zed & other ACP clients
                                │
                         ACP over stdio
                                ▼
             zycode-agent / pi-agent-cli-lc (ACP & headless CLI)
                                │
                                ▼
             pi-agent-harness-lc (sessions, compaction, skills)
                                │
                                ▼
             pi-agent-core-lc (agent loop, events, coding tools)
                                │
                                ▼
             LangChain StreamFn adapter (OpenAI / Anthropic / DeepSeek / gateways)
```

- **Clients**:
  - `zypi` (`tui/`): full-screen Rust terminal UI — vendored fork of [grok-build](https://github.com/xai-org/grok-build) (Apache-2.0) with all `x.ai/*` vendor extensions removed. `zypi` has no model client, tool runner, or MCP client; it talks to the agent over ACP.
  - `zycode.zycode` ([`vscode/`](vscode/README.md)): VS Code / Open VSX extension with a chat view and the `zycode-agent` binary bundled inside.
  - Any ACP editor or client (such as [Zed](#in-zed-custom-agent) or [DeepSeek Harness](docs/DEEPSEEK-HARNESS.md)).
- **Coding Agent (`pi-agent-cli-lc`)** ([`packages/pi-agent-cli/`](packages/pi-agent-cli/README.md)): standard ACP stdio agent and headless `-p` CLI, also packaged as the standalone `zycode-agent` executable.
- **Extensions**: optional Python packages for web search/fetch ([`pi-web-access-py`](packages/pi-web-access/README.md)), goal planning ([`pi-goal-x-py`](packages/pi-goal-x/README.md)), subagent workflows ([`pi-dynamic-workflows-py`](packages/pi-dynamic-workflows/README.md)), code mode ([`pi-codemode-py`](packages/pi-codemode/README.md)), and client-passed MCP servers ([`pi-mcp-py`](packages/pi-mcp/README.md)).
- **Underlying Python SDK**: built on a faithful Python port of `pi-agent-core` with LangChain used strictly as a `StreamFn` boundary adapter. Full SDK documentation lives in [`pi_agent_core/README.md`](pi_agent_core/README.md) (agent loop, events, built-in coding tools, LangChain/MCP tool adapter, structured output, guardrails) and [`packages/pi-agent-harness/README.md`](packages/pi-agent-harness/README.md) (`AgentHarness`, session tree storage, compaction, custom messages, skills, `LocalExecutionEnv`).

## Status

| Component | Status | Distribution |
|-----------|--------|--------------|
| `zypi` (Rust TUI) | **Stable** | [GitHub Releases](https://github.com/zy1233/zycode/releases) (Linux x86_64 and aarch64, macOS arm64) |
| `zycode.zycode` (VS Code extension) | **Stable** | [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=zycode.zycode) and [Open VSX](https://open-vsx.org/extension/zycode/zycode) (five platforms; the project owner tested the install of 1.0.0 and the update from 0.1.0 to 1.0.0 on Windows on 2026-11-12, and 1.4.0 with a real model on Windows on 2026-11-17 (the `max` level and the Context row); the rest of the Windows flow, macOS and Linux in a real VS Code are not verified) |
| `pi-agent-cli-lc` | **Stable** | [PyPI](https://pypi.org/project/pi-agent-cli-lc/) |
| `pi-agent-core-lc` | **Stable** | [PyPI](https://pypi.org/project/pi-agent-core-lc/) |
| `pi-agent-harness-lc` | **Stable** | [PyPI](https://pypi.org/project/pi-agent-harness-lc/) |
| `pi-web-access-py` | **Stable** | [PyPI](https://pypi.org/project/pi-web-access-py/) |
| `pi-goal-x-py` | **Stable** | [PyPI](https://pypi.org/project/pi-goal-x-py/) |
| `pi-dynamic-workflows-py` | **Stable** | [PyPI](https://pypi.org/project/pi-dynamic-workflows-py/) |
| `pi-mcp-py` | **Stable** | [PyPI](https://pypi.org/project/pi-mcp-py/) (from 0.6.0) |
| `pi-codemode-py` | **Stable** | [PyPI](https://pypi.org/project/pi-codemode-py/) (from 1.1.0) |

## Features

- **Standard ACP coding agent & headless CLI (`pi-agent-cli-lc`)**
  - Runs as a stdio ACP server for `zypi`, VS Code, Zed, and other ACP clients — no vendor-specific RPCs.
  - Headless one-shot mode (`python -m pi_agent_cli -p "prompt"`) for scripting and CI; exits with status 1 and prints `error: <reason>` to stderr when a model call fails.
  - First-run interactive setup (`--setup` or `zypi --setup`) writes model settings to `~/.pi-python/agent.toml` and stores API keys in `~/.pi-python/local.env` (never in TOML). `PI_HOME` relocates the whole home directory.
  - Session commands (`/new`, `/resume`, `/quit`), `@` directory listing, context compaction, and model switching (`/model`).
  - pi-aligned system prompt engine with tool contributions, `AGENTS.md` / `CLAUDE.md` context files, `<available_skills>`, and optional `<git_status>` snapshot (trusted projects only; `--no-git-context` or `[git] enabled = false` turns it off).
- **Full-screen Terminal UI (`zypi`)**
  - Interactive Rust TUI with session history, markdown rendering, Mermaid diagrams, and Shift+Tab permission-mode switching.
  - Standalone release bundles (`zycode-<target>-<version>.tar.gz`) ship `zypi` and `zycode-agent` together with zero Python dependencies required.
  - See [TUI & Code-Agent Guide](docs/TUI-AND-CODE-AGENT.md) for full setup and usage.
- **VS Code extension (`zycode.zycode`)**
  - Chat view with tool approval cards, **Show diff** for `write`/`edit`, **Past chats** view, getting-started walkthrough, mode menu (**Ask**, **Auto**, **Always approve**), **Thinking** menu, and **Context** usage row (when `context_window` is set in `agent.toml`).
  - Bundles `zycode-agent` for five platforms; see [`vscode/README.md`](vscode/README.md).
- **Built-in coding tools & Python extensions**
  - Seven built-in coding tools: `read`, `edit`, `write`, `bash`, `grep`, `find`, and `ls`, with pi-faithful truncation limits and paging notices (see [`pi_agent_core/README.md`](pi_agent_core/README.md#built-in-coding-tools)).
  - Optional extensions: web search/fetch (`pi-web-access-py`), goal planning (`pi-goal-x-py`), sandboxed subagent workflows (`pi-dynamic-workflows-py`), code mode & deferred tool discovery (`pi-codemode-py`), and MCP server bridge (`pi-mcp-py`, connects `stdio`/`http`/`sse` servers passed by an ACP client as `mcp__<server>__<tool>`).
  - Extensions that fail to load are skipped and reported at session start rather than failing silently.
  - **`workflow` on macOS:** the script process has no kernel limit on memory or process count (measured in CI on `macos-15` and `macos-15-intel`), so only the audit hook stands in the way; Linux and Windows enforce kernel limits. Keep `ask` (or `auto`, which also asks before every `workflow` call) on macOS. Details: [`packages/pi-dynamic-workflows/README.md#macos-weaker-limits`](packages/pi-dynamic-workflows/README.md#macos-weaker-limits).
- **Three permission modes (`ask`, `auto`, `always-approve`)**
  - **`ask` (default):** asks before any tool call unless the tool declares itself harmless (`ToolAnnotations`: `readOnlyHint`, or neither destructive nor open-world). Undeclared tools always ask.
  - **`auto`:** runs read-only and clearly in-scope calls by rules and checks undecided `bash` calls with a model step (fails closed to `ask`; `[auto] classifier = "rules"` disables the model step).
  - **`always-approve`:** runs tool calls without prompting (also used by headless `-p` runs).
  - **Auto is not a sandbox.** Auto guards what a call writes or runs; it does not guard what a call reads or sends (`read`, `grep`, `find`, `ls`, `fetch_url`, `web_search`, and recursive reads in `bash` run without asking). Use a sandbox (`zypi --sandbox`, a container, or a VM) if you need an isolation boundary.
- **Project trust**
  - Project-supplied extensions (`<project>/.pi-python/extensions`), saved workflows (`<project>/.pi-python/workflows`), `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md`, project-relative skills, the `<git_status>` snapshot (which runs `git` in the repository), and `workflow` `isolation` worktrees load only for trusted projects.
  - Interactive ACP clients prompt once per directory and store a SHA-256 fingerprint of gated files in `~/.pi-python/agent/trust.json`, asking again if any gated file changes. Configure statically via `[extensions] trusted_projects` or `default_project_trust` (`ask` / `never` / `always`) in `~/.pi-python/agent.toml`, or `--trust-project-extensions` / `PI_TRUST_PROJECT_EXTENSIONS=1`. Headless `-p` runs never ask but honour saved trust.

## Install

### Terminal UI (`zypi`)

Download the standalone bundle (contains both `zypi` and `zycode-agent`; no Python required) or the standalone `zypi` binary for Linux (`x86_64`, `aarch64`) or macOS (`arm64`) from [GitHub Releases](https://github.com/zy1233/zycode/releases):

```bash
# Option 1: Standalone bundle (zypi + zycode-agent; no Python required)
# Linux x86_64 shown. Other targets: zycode-linux-aarch64-1.1.0.tar.gz, zycode-darwin-aarch64-1.1.0.tar.gz.
curl -L -o zycode.tar.gz https://github.com/zy1233/zycode/releases/download/v1.1.0/zycode-linux-x86_64-1.1.0.tar.gz
tar -xzf zycode.tar.gz
sudo mv zypi zycode-agent /usr/local/bin/

# Set up model and API key in your terminal, then start:
zypi --setup
zypi
```

The bundle file name holds the version, so the URL names the tag (`v1.1.0`), not `latest`.

```bash
# Option 2: zypi binary alone with the Python pip package
curl -L -o zypi https://github.com/zy1233/zycode/releases/latest/download/zypi-linux-x86_64
chmod +x zypi
sudo mv zypi /usr/local/bin/

# Tell zypi which Python has pi-agent-cli-lc installed:
export PI_PYTHON=python3
zypi --setup
```

Or build `zypi` from source (requires Rust toolchain; on Windows, build and run in WSL2):

```bash
cd tui && cargo build --profile release-dist -p pi-pager-bin
# binary at target/release-dist/zypi
```

See [TUI & Code-Agent Guide](docs/TUI-AND-CODE-AGENT.md) and [Windows notes](docs/WINDOWS.md) for platform details (glibc 2.39+ on Linux, unsigned macOS builds, WSL2 setup).

### VS Code extension

The extension `zycode.zycode` ([`vscode/README.md`](vscode/README.md)) puts the zycode agent in a chat view in VS Code with the agent binary bundled inside (no separate Python install needed). It is unofficial and not affiliated with pi, Microsoft, or any model vendor. The mascot avatars in the chat are community illustrations, not the logos of any company.

- **VS Code:** install from the [Visual Studio Marketplace](https://marketplace.visualstudio.com/items?itemName=zycode.zycode), or run `code --install-extension zycode.zycode`.
- **VSCodium / Open VSX:** install from [Open VSX](https://open-vsx.org/extension/zycode/zycode).
- **Platforms:** `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64`, and `win32-x64`.
- **Limits:** the bundled agent is not code-signed and includes only the OpenAI-format provider (for Anthropic or DeepSeek providers, install `pi-agent-cli-lc` and set `zycode.agentPath`). The project owner tested installing 1.0.0 and updating from 0.1.0 to 1.0.0 on Windows (2026-11-12), and ran 1.4.0 with agent 1.0.0 and a real model on Windows (2026-11-17: `max` thinking level and the Context row after setting `context_window` in `agent.toml`). The rest of the Windows flow, all macOS flows, and Linux in a real VS Code are not verified. Release record: [`vscode/RELEASING.md`](vscode/RELEASING.md).

### In Zed (custom agent)

Because the agent speaks ACP over stdio, [Zed](https://zed.dev) can run it as a custom agent. Install [uv](https://docs.astral.sh/uv/), then add this entry to Zed's `settings.json`:

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

- `--with` installs the LangChain provider package (`[openai]` or `[anthropic]` for those providers).
- First run: Zed offers the terminal login "Set up model and API key" (runs `--setup` in a terminal to write `~/.pi-python/agent.toml` and `~/.pi-python/local.env`), or run `uvx --from pi-agent-cli-lc --with "pi-agent-core-lc[deepseek]" pi-agent-cli --setup` directly in a shell. (The Zed login dialog was verified with a scripted ACP client, not yet inside Zed itself.)
- To configure manually, start from [`packages/pi-agent-cli/agent.example.toml`](packages/pi-agent-cli/agent.example.toml) and supply the API key via environment variables or `~/.pi-python/local.env` (never inside `agent.toml`).
- To run from a local checkout, set `command` to your venv Python and `args` to `["-m", "pi_agent_cli"]`.

### Headless CLI & Python packages (PyPI)

```bash
# Install the coding agent CLI + a provider:
pip install pi-agent-cli-lc
pip install "pi-agent-core-lc[openai]"     # or [anthropic], [deepseek]

# First-run interactive configuration:
python -m pi_agent_cli --setup

# Headless one-shot prompt (for scripts & CI):
python -m pi_agent_cli -p "Explain this codebase" --cwd /path/to/project

# Optional extensions:
pip install pi-web-access-py          # web search & URL fetch
pip install pi-goal-x-py              # goal-driven planning (/goal)
pip install pi-dynamic-workflows-py   # subagent workflow orchestration
pip install pi-mcp-py                 # MCP servers passed by an ACP editor
```

Using the underlying Python agent loop or harness as a library? See [`pi_agent_core/README.md`](pi_agent_core/README.md) and [`packages/pi-agent-harness/README.md`](packages/pi-agent-harness/README.md).

<details>
<summary>Development install (editable)</summary>

```bash
pip install -e ".[dev]"
pip install -e "./packages/pi-agent-harness"
pip install -e "./packages/pi-agent-cli"
pip install -e "./packages/pi-web-access"
pip install -e "./packages/pi-goal-x"
pip install -e "./packages/pi-dynamic-workflows"
pip install -e "./packages/pi-mcp"
```

</details>

### Other ACP clients (DeepSeek Harness)

zycode can also run inside [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) as a delegated ACP subagent (`@deepseek-ai/dsh-subagent-acp`) or as a full session via the third-party community plugin (`@zaimokuza/dsh-acp-adapter`). See [docs/DEEPSEEK-HARNESS.md](docs/DEEPSEEK-HARNESS.md) for setup, permission/trust caveats, and verification scope.

## Benchmark

Evaluated on **FrontierHarness 30** (21 Terminal-Bench + 9 DeepSWE tasks across Windows local and WSL2 Docker sandbox runtimes), including a side-by-side comparison with official Pi and documented methodology caveats.

Full report: [FRONTIER-HARNESS-30-EVAL-REPORT.md](docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md).

## Development & Testing

```bash
# Python tests (no API keys required; real_llm tests are excluded by default)
uv run --extra dev --extra harness python -m pytest
ruff check . && ruff format --check .

# Rust TUI check (Linux / WSL2)
cd tui && cargo check -p pi-pager-bin

# VS Code extension check
cd vscode && npm run check
```

For real-LLM regression tests, the weekly provider matrix, and smoke scripts, see [`pi_agent_core/README.md#testing`](pi_agent_core/README.md#testing). For contributor conventions and invariants, see [`AGENTS.md`](AGENTS.md).

## Documentation

| Document | Contents |
|----------|----------|
| [**TUI & Code-Agent Guide**](docs/TUI-AND-CODE-AGENT.md) | Installation, configuration, and usage for `zypi` TUI and `pi-agent-cli-lc` |
| [**VS Code extension**](vscode/README.md) | `zycode.zycode` features, permissions, thinking menu, settings, and limits |
| [**ACP CLI package**](packages/pi-agent-cli/README.md) | `pi-agent-cli-lc` entry points, headless flags, config, and setup |
| [**Core runtime & coding tools**](pi_agent_core/README.md) | `pi-agent-core-lc`: agent loop, LangChain adapter, structured output, guardrails, coding tools, events, pi concept mapping |
| [**AgentHarness runtime**](packages/pi-agent-harness/README.md) | `pi-agent-harness-lc`: sessions, compaction, custom messages, skills, `LocalExecutionEnv` |
| [**DeepSeek Harness guide**](docs/DEEPSEEK-HARNESS.md) | Running zycode in DeepSeek Harness (subagent and community ACP adapter) |
| [Windows notes](docs/WINDOWS.md) | Windows setup, WSL2 TUI build, spawn/config notes |
| [Benchmark report](docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md) | FrontierHarness 30-task evaluation & Pi comparison |
| [Architecture & specs](docs/DESIGN.md) | Project-wide architecture; detailed phase specs in `docs/specs/` and audits in `docs/AUDIT/` |
| [AGENTS.md](AGENTS.md) | Repository map, invariants, and development workflows |

## Roadmap

Core runtime, `AgentHarness`, coding tools, ACP CLI, `zypi` TUI, and extension packages are released at `1.1.0`, and the VS Code extension is stable on VS Code Marketplace and Open VSX (see [`CHANGELOG.md`](CHANGELOG.md) and [`vscode/CHANGELOG.md`](vscode/CHANGELOG.md) for the full history).

**Next:**

- **TUI platform expansion** — planned, no date: a prebuilt `zypi` binary for Windows. There is no macOS `x86_64` build of `zypi` and none is planned, and there is no automatic update.

## License

Python packages (`pi-agent-core-lc`, `pi-agent-harness-lc`, `pi-agent-cli-lc`, and extension packages) and the VS Code extension (`vscode/`) are **MIT**.

The `tui/` directory is a vendored fork of [xai-org/grok-build](https://github.com/xai-org/grok-build) and remains **Apache License 2.0**. See `tui/LICENSE`, `tui/NOTICE`, and `tui/THIRD-PARTY-NOTICES`. `tui/` is not included in Python sdists or wheels.
