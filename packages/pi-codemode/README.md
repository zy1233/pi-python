# pi-codemode-py

Programmatic tool orchestration (`codemode`) and on-demand deferred tool discovery (`tool_search`)
for `pi-python` (`zycode`).

Design specification: `docs/specs/2026-11-24-code-mode-design.md`.

## Features

- **`codemode` tool**: Executes async Python orchestration scripts in the shared
  `pi_agent_core.sandbox` child process (`pi-sandbox-child`, protocol v2). Intermediate tool outputs
  stay inside the sandbox; only explicit `text()` / `image()` calls, trailing return values, and
  `<console_output>` (`print()`) return to the LLM context.
- **Runtime permission gating**: By default (`[codemode] require_approval = true`), starting an outer `codemode` script asks for permission in `ask` and `auto` modes just like `workflow` (users inside a container/VM/trusted setup can set `require_approval = false`). Every inner `await tools.<name>(...)` call evaluates its concrete arguments at runtime and passes through `AgentHarness.invoke_tool` (`check_tool_call`, `tool_result` hooks, Auto rules and model classifier, and ACP permission prompts) with `origin={"kind": "codemode", ...}` and writer-preferring serialization when mutating or under sequential tool policy.
- **`tool_search` & Deferred Tool Loading**: Discovers deferred tools (such as `pi-mcp` servers) via `ALL_TOOLS` / `search_tools()` inside scripts or promotes them directly with `tool_search`.
- **`/codemode` command**: Toggle or inspect runtime modes (`/codemode [status|on|hybrid|only|off]`).

## Sandbox boundaries

Scripts run in a child process (`pi_agent_core.sandbox`) with a scrubbed environment, empty temporary working directory, stdlib-only allowlisted imports (`_safe_import`), PEP 578 audit hook (a speed bump, not a security boundary on its own), and OS-level resource limits (Linux `rlimit` / `PR_SET_NO_NEW_PRIVS` where `unlink`, same-user `/proc`, and sockets remain open; Windows Job Object `ActiveProcessLimit=1` and low integrity without `RLIMIT_FSIZE`). On macOS the kernel does not enforce `RLIMIT_AS` or `RLIMIT_NPROC` (repository decision 2026-10-31). Keep `[codemode] require_approval = true` (default) or run the agent inside `zypi --sandbox`, a container, or a VM for untrusted repositories.
