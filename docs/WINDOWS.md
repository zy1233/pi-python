# Windows notes (Phase 4)

This document covers running **pi-python** on Windows: Python ACP agent, headless mode, and building the Rust TUI.

## Layout

| Component | Role on Windows |
|---|---|
| `python -m pi_agent_cli` | ACP agent (stdio) and headless `-p` |
| `zypi` (Rust TUI) | Full-screen client; spawns the Python agent |
| `~/.pi-python` | Config and sessions (`%USERPROFILE%\.pi-python` when `PI_HOME` is unset) |

Python packages ship on PyPI / editable install. The TUI lives under `tui/` and is **not** included in Python wheels.

## Python agent (recommended first step)

1. Create a venv and install editable packages:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]" -e "./packages/pi-agent-harness" -e "./packages/pi-agent-cli"
```

2. Copy the example config:

```powershell
New-Item -ItemType Directory -Force $env:USERPROFILE\.pi-python
Copy-Item packages\pi-agent-cli\agent.example.toml $env:USERPROFILE\.pi-python\agent.toml
# Leave config.toml empty (TUI/grok) or use packages\pi-agent-cli\config.toml.example
```

3. Set your API key via environment variable (name must match `api_key_env` in config):

```powershell
$env:REAL_LLM_API_KEY = 'sk-...'
```

4. Run headless (no TUI):

```powershell
$env:PI_USE_MOCK = '1'   # omit for real LLM
.venv\Scripts\python.exe -m pi_agent_cli -p "hello" --cwd .
```

### Config (`%USERPROFILE%\.pi-python\`)

- **`agent.toml`** — Python ACP agent (`[model]`, `permission`, `[skills]`, `[agent].command`). Copy from `packages/pi-agent-cli/agent.example.toml`.
- **`config.toml`** — Rust TUI / grok-shell only. Keep empty or use `packages/pi-agent-cli/config.toml.example`. Do **not** put `permission = "ask"` here (grok expects a `[permission]` table).

Override home directory with `PI_HOME`.

**Secrets (WSL + TUI):** put API keys in `%USERPROFILE%\.pi-python\local.env` (see `packages/pi-agent-cli/local.env.example`). The Rust TUI spawns Windows `python.exe`; WSL shell env vars are **not** forwarded — `local.env` is loaded by `pi_agent_cli` on startup.

**Paths (WSL + TUI):** the TUI passes session `cwd` as `/mnt/d/work/...`. The Python agent (Windows) converts these to `D:\work\...` before running tools. Without this, files land under `D:\mnt\d\...` and bash/ls fail with "Working directory does not exist".

### Spawn overrides (TUI → Python)

Priority:

1. `PI_AGENT_COMMAND` — full command string, e.g. `D:\work\pi-python\.venv\Scripts\python.exe -m pi_agent_cli`
2. `[agent].command` in `agent.toml`
3. `PI_PYTHON` + default args `-m pi_agent_cli`
4. `python` on Windows / `python3` elsewhere

On Windows, **prefer pointing at your venv** — the Store `python` stub is unreliable.

## Bash tool on Windows

The `bash` coding tool runs **Git Bash** (`bash.exe` on `PATH`) or an override from harness env config. Install [Git for Windows](https://gitforwindows.org/) for shell tool support. There is no `cmd.exe` fallback in pi-python.

## Rust TUI (`zypi`)

Upstream grok-build treats **Windows native `cargo build` as best-effort**. For development, use **WSL2**:

```bash
cd /mnt/d/work/pi-python/tui
# Fix CRLF on vendored bin/* if cloned from Windows:
sed -i 's/\r$//' bin/*
cargo install dotslash   # once, if bin/protoc needs it
CARGO_TARGET_DIR=$HOME/grok-build-target cargo build -p pi-pager-bin --release
# binary: $HOME/grok-build-target/release/zypi
```

Build the target dir on the Linux filesystem (`~/grok-build-target`), not under `/mnt/d`, for acceptable compile times.

Before running the TUI from WSL against a Windows venv, put this in `%USERPROFILE%\.pi-python\agent.toml` (Windows `D:/` paths are auto-translated to `/mnt/d/…` when the Linux `zypi` binary runs under WSL):

```toml
[agent]
command = "D:/work/pi-python/.venv/Scripts/python.exe -m pi_agent_cli"
```

Use forward slashes in TOML paths to avoid escape issues.

### WSL checkout gotchas

- Git clone on Windows can leave **CRLF** in `tui/bin/*`; Linux shebangs break (`dotslash\r`). Run `sed -i 's/\r$//' bin/*` before `cargo check`.
- HTTP proxy: point WSL at the host LAN IP (e.g. `172.x.x.x:10809`), not the WSL gateway address, if Clash/firewall drops inbound proxy traffic.

## Editors (ACP stdio)

Zed / Neovim / other ACP clients can spawn the agent directly:

```json
"agent_servers": {
  "pi": {
    "command": "D:\\work\\pi-python\\.venv\\Scripts\\python.exe",
    "args": ["-m", "pi_agent_cli"]
  }
}
```

Set `PI_HOME` or place config under `%USERPROFILE%\.pi-python`.

## What is not supported on Windows (Phase 4)

- No xAI login / `x.ai/*` RPCs
- No grok auto-update or marketplace from the forked TUI
- No official prebuilt `zypi` Windows release in this repo yet — build from `tui/` or use Python headless

## Foundation benchmark (pelican on a bicycle)

After the TUI or headless agent works, run the SVG smoke test (same Python agent path):

```powershell
. $env:USERPROFILE\.pi-python\local.env.ps1
d:\work\pi-python\.venv-tui\Scripts\python.exe d:\work\pi-python\scripts\smoke_pelican.py
```

See [docs/benchmarks/PELCAN-BICYCLE.md](benchmarks/PELCAN-BICYCLE.md).

See `docs/specs/2026-08-25-phase4-coding-agent-cli-design.md` for the full Phase 4 design.
