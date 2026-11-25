---
title: "Designing Code Mode for zycode: Comparing pi and Claude Code, and Our Trade-offs"
description: "A breakdown of how upstream pi 1.0 and Claude Code implement Code Mode—including the runtime-value blindspot—and the eight architectural trade-offs behind pi-codemode-py in zycode 1.1.0."
date: 2026-11-25
lang: en
tags:
  - code-mode
  - architecture
  - security
  - mcp
translation: code-mode-design-and-tradeoffs
---

In `zycode` 1.1.0, we shipped a standalone extension package, `pi-codemode-py` (`codemode`, `tool_search`, and `/codemode`), to address two recurring bottlenecks in coding agents working across large repositories and many MCP servers:

1. **Multi-turn ping-pong and context bloat**: In a standard tool loop, reading several files, running a few searches, and filtering the output burns multiple LLM round-trips—and leaves every raw intermediate payload permanently sitting in conversation history.
2. **Fixed prompt-token tax from MCP tool schemas**: Attaching several MCP servers with dozens or hundreds of tools injects their full JSON schemas into every request before the model even begins working.

Before writing code, we compared upstream `pi` 1.0 (`zycode` builds on a port of `pi-agent-core`, though it is an unofficial, independent project not affiliated with or endorsed by the `pi` project) and Claude Code's Code Mode, alongside Python-ecosystem options such as `pydantic-monty` and `python-quickjs-ng`. This post covers that comparison and the trade-offs we made in `zycode`. The full specification lives in [`docs/specs/2026-11-24-code-mode-design.md`](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-11-24-code-mode-design.md).

---

## 1. Comparing `pi` 1.0 and Claude Code

Both designs let the model write a short orchestration script to run tools in one turn, but they diverge on sandbox runtime, deferred tool loading, and—most importantly—when security checks happen.

### 1.1 Upstream `pi` 1.0 (`@earendil-works/pi-codemode` + `tool_search`)

In 1.0, upstream `pi` reversed its earlier stance against built-in MCP support and adopted **Codemode** as its native foundation for tool orchestration and MCP exposure. According to the `pi` 1.0 release notes, combining a streamlined Codemode with deferred tool loading cut prompt tokens by roughly **40%** in complex setups (for example, from ~5,300 tokens down to ~3,300).

Its core pieces stay minimal:

- **Single tool entry point**: A `codemode` tool accepting a bare async JS/TS function body (`code: string`) with top-level `await` and `return`.
- **Tight WASM sandbox**: Runs QuickJS compiled to WebAssembly inside a Worker thread—no Node.js APIs, no filesystem, no network stack—communicating with the host strictly through injected globals.
- **In-script tool discovery**: Exposes normalized `tools.<normalized_name>(args)` (replacing non-identifier characters such as hyphens with `_`), `ALL_TOOLS`, `searchTools()`, `describeTool()`, `text()`, `image()`, and `console.log()` (collected into `<console_output>`).
- **Deferred loading**: Deferred MCP servers contribute only a compact summary to the initial prompt; the model can discover and call them directly inside `codemode`, or promote them into direct tool calls via `tool_search`.

### 1.2 Claude Code (`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`) and Anthropic PTC

Claude Code introduced Code Mode behind `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1` (starting in v2.1.323), corresponding to Anthropic's Programmatic Tool Calling (`code_execution_20260120` + per-tool `allowed_callers` on the API side):

- **Strict configuration trust boundary**: Can only be enabled via environment variables or user-level `~/.claude/settings.json`; **project-level config files are explicitly ignored** for security.
- **`REPL` tool and JS primitives**: The model calls an internal `REPL` tool with JavaScript using a `$` template tag for shell commands (with concurrent fan-out and local text filtering) and helpers like `edit`; the **last expression's value** is returned automatically.
- **Background execution**: Foreground scripts have a 10-minute wall-clock timeout; in `auto` or `bypassPermissions` mode, long scripts can move to the background for up to 2 hours.
- **The key security lesson — the "Runtime Value Blindspot"**:
  - Individual operations triggered by the script still pass through local `PreToolUse` hooks and standard allow/ask/deny rules.
  - However, in **Auto mode**, the safety classifier inspects the generated JavaScript **source code statically before execution**, rather than gating each evaluated call at runtime.
  - Whenever a script computes paths or commands dynamically—for example, running `rg` and then assembling target paths inside a loop before calling `edit` or `$`—the pre-execution classifier cannot see the real arguments that will actually run.

### 1.3 Side-by-Side Comparison

We also evaluated two Python-ecosystem alternatives: `pydantic-monty` (a Rust interpreter for a restricted Python subset) and `python-quickjs-ng` (QuickJS-NG embedded as a C extension).

| Dimension | Upstream `pi` 1.0 | Claude Code | `pydantic-monty` / `quickjs-ng` | `zycode` 1.1.0 (`pi-codemode-py`) |
| --- | --- | --- | --- | --- |
| **Script language** | JS / TS | JS (`REPL`) | Restricted Python subset / JS | **Standard Python 3.11+ (`async def` wrapper + trailing expression capture)** |
| **Sandbox runtime** | QuickJS WASM + Worker | Local JS runtime | Rust worker pool / C extension | **Shared `pi_agent_core.sandbox` multi-platform child process** |
| **Deferred MCP loading** | `codemode` + `tool_search` + `deferred` | No built-in deferred equivalent | Left to outer framework | **`ToolDefinition.deferred` + `tool_search` + `pi-mcp` coordination** |
| **Auto-mode gating** | Host permission callback | **Pre-execution static check on source (runtime value blindspot)** | Custom | **Synchronous runtime gating on evaluated arguments + serial lock** |
| **Cross-turn state** | Stateless | Mostly stateless | Optional session state | **Stateless (clean per-call execution)** |
| **Background execution** | Not core path | Up to 2h in auto/bypass | None built-in | **Not in v1 (long async work uses `workflow` / `/goal`)** |
| **Dependencies & packaging** | Native Node chain | Node / Bun | Extra native Rust/C wheels across 5 targets | **Zero new third-party deps; reuses single `pi-sandbox-child`** |

---

## 2. `zycode`'s Decisions and Trade-offs

Guided by our repository invariants (written event contract, permission by declaration, project trust, fail-closed sequential Auto gating, and five-platform PyInstaller single-file packaging), we settled on eight concrete decisions (D1–D8 in the design spec).

### 2.1 Language and Sandbox: Standard Python + Shared Child Process over QuickJS or `pydantic-monty`

`zycode`'s loop, tools, and extensions are Python. Embedding `python-quickjs-ng` would force models inside a Python stack to switch to JavaScript for tool orchestration, lose everyday stdlib helpers (`re`, `json`, `difflib`, `ast`), and add sync-`eval`/async-`asyncio` bridging complexity plus native wheels across five build targets.

`pydantic-monty` starts fast, but its restricted Python subset trips over common stdlib patterns models reach for naturally—causing avoidable retry turns—and adds another Rust runtime binary to package and ship inside `zycode-agent`.

Instead, we **promoted the multi-platform child-process sandbox from `pi-dynamic-workflows` into `pi_agent_core.sandbox` (protocol v2)**, sharing one stdlib-only frozen child executable (`pi-sandbox-child`) across both `workflow` and `codemode`:

- Isolated interpreter (`-I -S -B -X utf8`), scrubbed environment, and an empty temporary working directory;
- OS-level limits (Linux `rlimit` and `PR_SET_NO_NEW_PRIVS`; Windows Job Object with `ActiveProcessLimit=1` and a low-integrity token);
- PEP 578 audit hook plus a `_safe_import` allowlist restricted to pure-compute stdlib modules (attempting to import `os`, `subprocess`, or `socket` fails immediately with a hint to use `await tools.*`);
- AST wrapping into `async def __codemode_main__()` preserving original line numbers, plus Claude Code-style **trailing expression capture**;
- Dual call convention on `tools.*`—accepting both Python keyword arguments `await tools.read(path="a.py")` and JS-style dict payloads `await tools.read({"path": "a.py"})`—and a `ToolText(str)` return type that works directly with `json.loads()` / `re` while also exposing `.details` and `.images`.

We keep the sandbox boundary explicit: **the PEP 578 audit hook and `_safe_import` are a speed bump, not a container boundary**. On Linux, `unlink`, same-user `/proc`, and sockets remain accessible; on macOS, the kernel does not enforce `RLIMIT_AS` or `RLIMIT_NPROC`. For untrusted repositories, the real boundary remains `zypi --sandbox`, a container, or a VM.

### 2.2 Eliminating the Runtime Value Blindspot and Tightening Outer Approval

This is the centerpiece security trade-off.

When a `codemode` script reaches `await tools.<name>(args)`, every loop, branch, and string concatenation has **already evaluated into concrete JSON literals**. The host routes each inner call through `AgentHarness.invoke_tool` tagged with `origin={"kind": "codemode", "cwd": ..., "parent_call_id": ...}`:

```text
Evaluated concrete arguments from sandbox (JSON)
  → AgentHarness.invoke_tool(origin="codemode")
  → Argument validation (invalid args return ToolError + signature; no noisy prompt)
  → check_tool_call → extension hooks & ask / AutoSession.decide(real args!)
  → tool.execute (inner bash consumes BashLimitRegistry timeout & output caps)
```

Even if a script builds `p = ".github/" + "workflows/ci.yml"` dynamically before calling `await tools.write(path=p, ...)`, the Auto floor rules, model classifier, and ACP permission prompts always judge the concrete path `.github/workflows/ci.yml`. **The runtime value blindspot is eliminated by construction.**

Two subtler details lock this down:

1. **Writer-preferring serialization in Auto mode (`_GateLock`)**: Invariant 9d requires that while Auto is active, calls execute strictly as `decide → execute → decide → execute`, so a `parallel(...)` batch cannot have a second mutating call approved against stale filesystem state while the first is mid-write. `_GateLock` in `runtime.py` serializes mutating or sequential-policy calls cleanly while still allowing concurrent read-only calls when no writer is waiting.
2. **Tightening outer approval to `require_approval = true` by default (PR #147)**: An early draft considered marking the outer `codemode` call harmless so pure read-only scripts wouldn't prompt. During PR #147 review, we tightened the default to `require_approval = true` to match `workflow` and Invariant 8: since the child sandbox is not a container, `ask` and `auto` confirm before starting a script, while every inner `tools.*` call is still gated individually at runtime. Users running inside a container, VM, or `zypi --sandbox` can opt into silent outer starts in their home `~/.pi-python/agent.toml` (project-level config is ignored):

```toml
[codemode]
enabled = true
mode = "hybrid"
defer_mcp = true
require_approval = false
```

### 2.3 Default `hybrid` Mode and Decoupled `pi-mcp` Coordination

Rather than forcing every turn through script generation, `zycode` defaults to **`hybrid`** (`enabled = true, mode = "hybrid", defer_mcp = true`):

- The seven built-in coding tools (`read`, `edit`, `write`, `bash`, `grep`, `find`, `ls`) stay directly callable for simple single-step actions and weaker models;
- Multi-step batching, local filtering, and MCP orchestration run through `codemode`;
- Users can switch at any time with `/codemode only` (to minimize active tool schemas) or `/codemode off` (to disable `codemode` and restore all MCP tools directly).

Core, Harness, and CLI still contain **no MCP client**. They expose only generic `ToolDefinition.deferred`, `activate_tools` / `deactivate_tools`, and `invoke_tool` primitives. `pi-mcp-py` and `pi-codemode-py` do not hard-depend on each other: when `codemode` is active and `defer_mcp = true` (overridable via `PI_MCP_EXPOSURE=auto|direct|deferred`), `pi-mcp` registers its tools as `deferred=True`, and the system prompt includes only a compact `<deferred_tools>` namespace summary.

### 2.4 Three Things We Intentionally Left Out

1. **No cross-turn stateful REPL (D6)**: Every `codemode` invocation runs cleanly from scratch. Keeping hidden in-memory variables across turns breaks replay consistency with JSONL session trees (`navigate_tree`), compaction, and `session/resume`.
2. **No detached background execution in v1 (D7)**: Letting a script continue mutating the workspace in the background for two hours breaks Auto mode's single-session sequential assumptions. Long multi-step or multi-agent work already belongs to `pi-dynamic-workflows` (`workflow`, with independent git worktree isolation) and `/goal`—orthogonal layers over the same sandbox, with recursive calls to `codemode`, `workflow`, or `tool_search` explicitly blocked inside scripts.
3. **Zero non-standard ACP wire extensions**: Throttled progress streams use standard `tool_call_update`, inner prompts use standard `session/request_permission` (labeled `(codemode)` and serialized per session so single-modal clients never deadlock), and `/codemode` is announced via standard `available_commands_update`. `zypi`, the VS Code extension, and Zed work out of the box.

---

## Closing Notes

From upstream `pi` 1.0, we adopted deferred tool discovery and compact in-sandbox tool catalogs to cut context waste. From Claude Code, we adopted user-only configuration trust and learned why pre-execution static checks leave a runtime blindspot in Auto mode. Putting those lessons together with `zycode`'s Python stack, shared child sandbox, runtime `_GateLock`, and standard ACP boundary gave us a conservative, auditable Code Mode with zero added external dependencies.

Source and design references:

- Design specification: [`docs/specs/2026-11-24-code-mode-design.md`](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-11-24-code-mode-design.md)
- Extension package: [`packages/pi-codemode/`](https://github.com/zy1233/pi-python/tree/main/packages/pi-codemode)
- Shared sandbox runtime: [`pi_agent_core/sandbox/`](https://github.com/zy1233/pi-python/tree/main/pi_agent_core/sandbox)
