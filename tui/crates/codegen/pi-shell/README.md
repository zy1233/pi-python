# pi-shell

A library crate of the `zypi` workspace (`tui/`). `tui/` is a hard fork of Grok Build. Read
`tui/NOTICE` for the terms and for what changed.

## What this file used to say

This file was the user manual of the upstream Grok CLI. It described the install script, the
`grok` commands, headless mode, the agent stdio mode, MCP servers, subagents and the login flows.
This repository does not build that program, so the text was wrong for every reader.

Do not follow the old install steps. They install the official Grok CLI, not `zypi`. The old text is
in git history (`git log --follow -- tui/crates/codegen/pi-shell/README.md`) and at
<https://github.com/xai-org/grok-build>.

## What `zypi` is

`zypi` is a terminal UI. It starts the Python agent (`python -m pi_agent_cli`) and talks standard
ACP to it on stdio. The Python agent makes the LLM calls and runs the tools. `zypi` has no model
client, no tool runner and no MCP client.

## What this crate is now

The runtime part of the crate is gone: model sampling, the agent loop, the leader process and
headless mode. The code that stays is the code that the pager still reaches: configuration, folder
trust, the session files that the pager reads, and shared types. Some of it is scheduled for
removal. The plan names the session data layer (phase D.2) and the xAI authentication layer
(phase D.3).

To see which modules the pager reaches, run this from the repository root:

```bash
python scripts/tui_callgraph.py units --crate pi-shell
```

## Where to read more

| Topic | Document |
|-------|----------|
| How to install and use `zypi` | `docs/TUI-AND-CODE-AGENT.md` |
| How `zypi` starts the agent, and the config files | `packages/pi-agent-cli/AGENTS.md` |
| Test counts, gates and the call-graph tools | `docs/baselines/tui.md` |
| What was removed, and what is next | `docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md` |
