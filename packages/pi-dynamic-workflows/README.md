# pi-dynamic-workflows-py

Dynamic workflow orchestration extension for [pi-python](https://github.com/zy1233/pi-python).

Python port of [@quintinshaw/pi-dynamic-workflows](https://github.com/QuintinShaw/pi-dynamic-workflows).

## Install

```bash
pip install pi-dynamic-workflows-py
```

## Overview

The `workflow` tool lets the LLM write a Python orchestration script that
fans work out across isolated subagents via `agent()`, `parallel()`,
`pipeline()`, and `phase()`.

## How a script runs

A script does not run in the host's interpreter. Each `WorkflowRuntime.execute()` starts a
child process (standard library only, empty environment, empty scratch directory) and the
script computes there. Anything with an effect — running a sub-agent, logging, asking the
budget — is a request over a pipe that the host answers; the host treats whatever arrives on
that pipe as untrusted.

- **Limits** (`SandboxLimits`, passed as `WorkflowRuntime(sandbox_limits=...)`): memory, CPU
  time, open files, an optional wall-clock deadline, and caps on calls in flight, log output and
  phases. The kernel enforces the first ones (rlimits and `NO_NEW_PRIVS` on Linux; a Job Object
  and a lowered integrity level on Windows), so a script that spins or allocates is stopped and
  the host's event loop keeps running.
- **Values** cross as JSON: `args`, `result(...)` and what `agent()` returns. `budget` is a
  read-only view of the host's books.
- **Errors**: a script that raises makes `execute()` raise `WorkflowScriptError` (its `str()` is
  the script's own message); a script that is stopped or cannot be run raises a `SandboxError`.
- **`agent(..., cwd=)`** must stay inside the project directory.

This is a process boundary with resource limits, **not a security boundary against a script
written to break out**: the in-process audit hook that refuses `open`, `os.*`, `socket.*`,
`subprocess.*` and `ctypes.*` is Python and can be undone by Python. What a script can still do
then, per platform, is in
[`docs/specs/2026-10-01-workflow-sandbox-design.md`](../../docs/specs/2026-10-01-workflow-sandbox-design.md)
(section 9); run the agent in a container if scripts cannot be trusted. Sub-agents' tool calls
still go through the session's permission policy.

### macOS: weaker limits

On macOS the sandbox has **no kernel limit on memory and none on the number of processes**
(measured in CI on `macos-15` and `macos-15-intel`). The system does not enforce `RLIMIT_AS`, and
there is no process-count limit. CPU time, open files, core size and file size are limited. A
script can use as much memory as the machine has, and it can start child processes. Only the
audit hook stands in the way, and a script written to break out can undo the hook (see above).
Linux and Windows have kernel limits for memory and for processes; the tests check them there.
The macOS tests that need them are skipped, with the reason in the test.

What you can do about it:

- The `workflow` tool declares no "harmless" annotations. In the default permission mode
  (`permission = "ask"` in `agent.toml`), the agent asks you before every `workflow` call, and a
  sub-agent's tool call is judged by the session's tool of the same name. Keep `ask` on macOS.
  The modes `auto` and `always-approve` never ask, and headless runs (`-p`) use `auto`.
- Do not start a workflow from a prompt or a project that you do not trust. Saved workflows of a
  project (`<project>/.pi-python/workflows`) load only for a trusted project.
- If scripts cannot be trusted, run the agent in a container or a virtual machine.
- There is no setting that turns the tool off for an installed extension. With the Python
  packages, do not install `pi-dynamic-workflows-py` (or uninstall it). The prebuilt `zycode-agent`
  program contains it.

## Built-in Patterns

- `/deep-research` — Research a question across the web with cross-checked sources
- `/adversarial-review` — Investigate then cross-check findings with skeptical reviewers
- `/code-review` — Multi-angle parallel code review
- `/multi-perspective` — Analyze a topic from several perspectives, then synthesize
- `/codebase-audit` — Run parallel checks against a codebase scope
