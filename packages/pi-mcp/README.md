# pi-mcp-py

An extension for [pi-python](https://github.com/zy1233/pi-python). It connects the MCP servers that an
ACP client (an editor such as Zed) passes to the agent, and gives their tools to the model.

The agent itself has no MCP client. Without this package, the servers that the editor passes are not
connected, and the agent says so. With it, the agent connects them.

Install it into the environment of the agent:

```bash
pip install pi-mcp-py
```

The package is on PyPI from the 0.6.0 release of pi-python. In a checkout of the repository, use
`pip install -e ./packages/pi-mcp`.

Nothing else to set up. The servers come from the editor (Zed: `context_servers` in the settings).
It needs `pi-agent-core-lc` 0.8.0 or later: the extensions are released with the core, so the
floor of the package is the release it ships with. A core older than 0.6.0 does not hand over
the servers of the client, and then the package logs a warning and does nothing.

## What it does

- Connects `stdio`, `http` (Streamable HTTP) and `sse` servers. One connection for each server and
  session. A server that does not start does not stop the session, nor the other servers.
- Gives each tool to the model as `mcp__<server>__<tool>`. The prefix keeps an MCP tool from replacing a
  built-in tool of the same name. A name longer than 64 characters is cut and gets a hash at the end.
- Tells the user when a server fails to start or is slow. It says nothing when all is well.
- Connects a server again when it has stopped: the next tool call does it, at most 3 times in a row (a
  call that gets an answer starts the count again). A call that was already sent when the server died
  is not sent again, because the server may have run it. It fails with the reason.
- `/mcp` shows the servers, their state and the number of tools. `/mcp reconnect <name>` connects one
  again, with no limit.
- Ends every server process when the session ends (Windows and Linux).
- Limits: a server may take 60 s to start and a call 120 s (see **Settings**). The first turn waits at
  most 10 s for a server that still starts. A tool result of more than 50 KB of text is cut, and a note
  says so.

## Things to know

- **Permissions.** A tool call is asked about in the `ask` mode of the CLI, unless the server marks the
  tool with a hint that says it is harmless (`readOnlyHint`). The hints are taken as the server gives
  them: you chose the server.
- **Environment of a stdio server.** It gets the safe list of the MCP SDK, the proxy and certificate
  variables of the agent (`HTTP_PROXY`, `SSL_CERT_FILE`, ...), and the `env` of the editor. It does not
  get the other variables of the agent, the LLM keys among them. `${VAR}` in a value is not expanded.
- **Secrets.** The `env`, `headers`, `args` and `url` of a server are not written to a log, a notice, the
  `/mcp` output or an error text.
- **Logs.** The stderr of a stdio server goes to `<PI_HOME>/logs/mcp/<name>.log` (`PI_HOME` is
  `~/.pi-python` when it is not set). The file is appended to, and started over when it is larger than
  512 KB. The text of the file is what the server wrote: it may hold what the server was given.
- **Servers on this machine.** An `http` or `sse` server at `localhost`, `127.0.0.1` or `::1` is
  reached directly, not through the proxy of the system (`httpx` would send it there).
- **Not supported:** MCP resources, prompts, sampling, roots, elicitation, OAuth, `tools/list_changed`,
  websocket servers.

## Settings

Environment variables only:

| Variable | Default | Meaning |
|----------|---------|---------|
| `PI_MCP_STARTUP_TIMEOUT` | 60 | Seconds a server may take to start. Then it counts as failed. |
| `PI_MCP_CALL_TIMEOUT` | 120 | Seconds a tool call may take. |

The design is in `docs/specs/2026-10-23-mcp-extension-design.md` of the repository.
