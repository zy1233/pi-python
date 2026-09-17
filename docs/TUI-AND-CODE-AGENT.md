# TUI & Code-Agent Guide

> **Status: Beta** — The TUI (`zypi`) and coding agent (`pi_agent_cli`) are functional but under active development. APIs, configuration, and binary distribution may change between releases.

This document covers how to install, configure, and use the pi-python coding agent — both the headless Python CLI and the full-screen Rust TUI.

---

## Architecture overview

```
┌─────────────────────────────────────┐
│          zypi (Rust TUI)            │   Full-screen terminal UI
│  Session management, markdown       │   Apache-2.0 (vendored grok-build fork)
│  rendering, MCP, Mermaid, etc.      │
└────────────┬────────────────────────┘
             │ ACP (Agent Client Protocol) over stdio
             ▼
┌─────────────────────────────────────┐
│     pi_agent_cli (Python ACP)       │   Standard ACP agent
│  System prompt engine, tool         │   MIT license
│  contributions, context files       │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│     pi_agent_harness (Python)       │   Sessions, compaction, skills
│  AgentHarness, SessionRepo,         │
│  LocalExecutionEnv                  │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│     pi_agent_core (Python)          │   Core agent loop
│  Agent, agent_loop, StreamFn,       │   Event protocol, tool execution
│  LangChain adapter                  │
└─────────────────────────────────────┘
```

The TUI (`zypi`) is the interactive frontend. It communicates with the Python coding agent (`pi_agent_cli`) over the [Agent Client Protocol (ACP)](https://github.com/anthropics/agent-protocol) via stdio. The Python agent handles all LLM calls, tool execution, and session management through the core engine.

You can use the coding agent in two ways:

1. **Headless mode** — `python -m pi_agent_cli -p "prompt"` — no TUI, just prompt→response
2. **TUI mode** — `zypi` — full-screen interactive terminal with session history, markdown rendering, etc.

---

## Installation

### 1. Python packages (required)

```bash
# Using pip
pip install pi-agent-cli-lc

# Or editable install from source
pip install -e ".[dev]" -e "./packages/pi-agent-harness" -e "./packages/pi-agent-cli"
```

### 2. TUI binary (`zypi`)

#### Option A: Download prebuilt binary (Linux x86_64)

Prebuilt binaries are attached to [GitHub Releases](https://github.com/zy1233/pi-python/releases) starting from v0.3.0:

```bash
# Download
curl -L -o zypi https://github.com/zy1233/pi-python/releases/latest/download/zypi-linux-x86_64
chmod +x zypi
sudo mv zypi /usr/local/bin/
```

#### Option B: Build from source

Requires a Rust toolchain (1.85+), protoc, and a Linux environment (WSL2 recommended on Windows):

```bash
cd tui
cargo build --profile release-dist -p pi-pager-bin
# Binary: target/release-dist/zypi
```

> **Tip**: Build the target directory on the Linux filesystem (e.g. `~/cargo-target`), not under `/mnt/...`, for acceptable compile times. Set `CARGO_TARGET_DIR` in your shell environment.

---

## Configuration

All configuration lives under `~/.pi-python/` (override with `PI_HOME`).

### `agent.toml` — Python agent config

Copy the example and edit:

```bash
mkdir -p ~/.pi-python
cp packages/pi-agent-cli/agent.example.toml ~/.pi-python/agent.toml
```

Key settings:

```toml
[model]
provider = "deepseek"                              # or "openai", "anthropic"
model_id = "deepseek-ai/DeepSeek-V3"
api_key_env = "REAL_LLM_API_KEY"                   # env var name holding your API key
base_url = "https://api.siliconflow.cn/v1"         # for OpenAI-compatible gateways
context_window = 65536

permission = "auto-edit"                           # "auto-edit" | "ask" | "auto-full"

[skills]
enabled = true

[agent]
# Override how the TUI spawns the Python agent (optional)
command = "python -m pi_agent_cli"
```

### `config.toml` — TUI-only config (optional)

For TUI-specific settings. Usually keep empty or use `packages/pi-agent-cli/config.toml.example`.

### `local.env` — API keys (recommended for TUI)

When running the TUI from WSL, environment variables from the Windows host are not forwarded. Place secrets in `~/.pi-python/local.env`:

```bash
REAL_LLM_API_KEY=sk-...
```

The Python agent loads this file on startup.

---

## Usage

### Headless mode (no TUI)

```bash
# Set your API key
export REAL_LLM_API_KEY='sk-...'

# Single prompt
python -m pi_agent_cli -p "Explain this codebase" --cwd /path/to/project

# Mock mode (no real LLM, for testing)
PI_USE_MOCK=1 python -m pi_agent_cli -p "hello" --cwd .
```

### TUI mode

```bash
# Start the TUI
zypi

# Inside the TUI:
#   Type your prompt and press Enter to send
#   /new      — start a new session
#   /resume   — list and resume a previous session
#   /quit     — exit
#   @         — browse local directory listing
```

### ACP stdio mode (for editors)

The agent works with any ACP-compatible editor (Zed, Neovim, etc.):

```json
{
  "agent_servers": {
    "pi": {
      "command": "python",
      "args": ["-m", "pi_agent_cli"]
    }
  }
}
```

---

## Coding tools

The agent comes with 7 built-in coding tools (ported from pi):

| Tool | Description |
|------|-------------|
| `read` | Read files (text + images), 2000-line truncation with paging |
| `edit` | Exact-text replacement with fuzzy fallback |
| `write` | Create/overwrite files with auto parent dirs |
| `bash` | Shell execution (Git Bash on Windows), streaming output |
| `grep` | ripgrep-first search, pure-Python fallback |
| `find` | Glob-based file discovery |
| `ls` | Directory listing |

Tools contribute `prompt_snippet` and `prompt_guidelines` to the system prompt automatically.

---

## System prompt engine (Phase 5)

The coding agent assembles its system prompt from multiple sources:

1. **Base prompt** — `build_system_prompt()` with available tools, guidelines, project context
2. **Tool contributions** — each tool's `prompt_snippet` / `prompt_guidelines`
3. **Context files** — `AGENTS.md`, `CLAUDE.md`, `SYSTEM.md`, `APPEND_SYSTEM.md` from the working directory
4. **Skills** — `<available_skills>` XML format, discovered from config

This matches the pi upstream `packages/coding-agent/src/core/system-prompt.ts` assembly.

---

## Platform support

| Platform | Python agent | TUI (`zypi`) |
|----------|-------------|--------------|
| Linux x86_64 | ✅ | ✅ prebuilt binary |
| Linux arm64 | ✅ | Build from source |
| macOS (arm64/x86_64) | ✅ | Build from source |
| Windows | ✅ (Git Bash required for `bash` tool) | Build from source via WSL2 |

See [Windows notes](WINDOWS.md) for detailed Windows/WSL2 setup instructions.

---

## What is NOT included

The TUI is a vendored fork of [xai-org/grok-build](https://github.com/xai-org/grok-build). The following grok-specific features have been **removed**:

- xAI login / authentication
- `x.ai/*` vendor extension RPCs (680+ references stripped)
- grok CLI subcommands (`agent`, `/loop`, `/share`, dashboard)
- Auto-update from grok servers
- Marketplace integration
- `~/.grok` home directory (replaced by `~/.pi-python`)

---

## Related documents

| Document | Contents |
|----------|----------|
| [Phase 4 design spec](specs/2026-08-25-phase4-coding-agent-cli-design.md) | Coding Agent CLI architecture |
| [Phase 5 design spec](specs/2026-09-02-phase5-prompt-engine-design.md) | Prompt engine design |
| [Windows notes](WINDOWS.md) | Windows/WSL2 setup guide |
| [Benchmark report](benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md) | FrontierHarness 30-task evaluation |
| [Pelican benchmark](benchmarks/PELCAN-BICYCLE.md) | Foundation SVG smoke test |
| [P0 TUI spike](AUDIT/SPIKE-P0-GROK-TUI.md) | grok pager `x.ai/*` strip inventory |
