# pi-agent-cli-lc

Standard [Agent Client Protocol (ACP)](https://agentclientprotocol.com/) agent over `pi-agent-harness`.

- stdio entry: `python -m pi_agent_cli` (or console script `pi-agent-cli`)
- headless one-shot: `python -m pi_agent_cli -p "..."` 
- Headless prompt overrides (override `agent.toml` `[prompt]`): `--system-prompt`, `--system-prompt-file`, `--append-system-prompt` / `--rules`, `--append-system-prompt-file`, `--no-context-files`
- Config: `~/.pi-python/agent.toml` (see `agent.example.toml` in this directory)
- No `x.ai/*` vendor RPCs — core + harness + ACP only

Install:

```bash
pip install pi-agent-cli-lc
# needs a LangChain provider, e.g.:
pip install pi-agent-core-lc[deepseek]
```

See the [repository README](https://github.com/zy1233/pi-python#install) for development setup and Windows/WSL notes.
