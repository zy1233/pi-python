# pi-agent-cli-lc

Standard [Agent Client Protocol (ACP)](https://agentclientprotocol.com/) agent over `pi-agent-harness`. It is the agent of [zycode](https://zycode.dev), an open-source coding agent for your terminal and your editor.

- stdio entry: `python -m pi_agent_cli` (or console script `pi-agent-cli`). The console script `pi-agent-cli-lc` has the name of the package, so `uvx pi-agent-cli-lc` works too (from 0.7.0). `uvx --from pi-agent-cli-lc pi-agent-cli` works with every release
- Zed: see "In Zed (custom agent)" in the [repository README](https://github.com/zy1233/zycode#in-zed-custom-agent)
- headless one-shot: `python -m pi_agent_cli -p "..."` 
- Headless prompt overrides (override `agent.toml` `[prompt]`): `--system-prompt`, `--system-prompt-file`, `--append-system-prompt` / `--rules`, `--append-system-prompt-file`, `--no-context-files`
- Version: `python -m pi_agent_cli --version` prints `pi-agent-cli <version>` and exits 0. It reads no config.
- Config: `~/.pi-python/agent.toml` (see `agent.example.toml` in this directory)
- First run: `python -m pi_agent_cli --setup` asks for a model and an API key in a terminal. It writes `agent.toml` and `local.env` (the key) in the home. It asks no key, and writes no `local.env`, when the variable that you name already has a value. An editor that can run a terminal login (Zed) offers the same dialog as the login "Set up model and API key"
- No `x.ai/*` vendor RPCs — core + harness + ACP only

Install:

```bash
pip install pi-agent-cli-lc
# needs a LangChain provider, e.g.:
pip install pi-agent-core-lc[deepseek]
```

See the [repository README](https://github.com/zy1233/zycode#install) for development setup and Windows/WSL notes.
