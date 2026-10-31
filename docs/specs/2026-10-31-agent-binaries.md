# Agent binaries and the ACP registry entry

> Scope: ship the ACP agent (`python -m pi_agent_cli`) as one executable per platform, for the
> `binary` entry of the [ACP registry](https://github.com/agentclientprotocol/registry).
> Decision: 2026-10-31. The user chose the `binary` route, PyInstaller one-file, five targets,
> no signing in the first version, and a workflow of its own.
>
> Code: `packaging/agent/{build.py,zycode-agent.spec,entry.py,smoke.py}`,
> `.github/workflows/agent-binaries.yml`. Changes for a frozen build:
> `pi_dynamic_workflows/sandbox/host.py`, `pi_agent_cli/{auth_methods,setup_command}.py`,
> `pi_agent_core/{frozen.py,adapters/langchain_stream.py}`.

---

## 1. Why a binary

Zed reads only `binary` and `npx` entries of the registry. It skips an entry that has only
`uvx` (source: `agent_registry_store.rs`; a Zed maintainer says the same in registry issue
#147). A registry entry cannot add `--with` or `--from` to a `uvx` command. So a binary is the
form that reaches Zed users and needs no Node, no `uv` and no Python on the user's machine.

The same entry also has `uvx`. JetBrains and the community DSH adapter read it. For that,
`pi-agent-cli-lc` depends on the OpenAI-format provider (`pi-agent-core-lc[openai]`): a bare
`uvx pi-agent-cli-lc` then has a provider.

## 2. What is built

| Platform | Runner | Archive |
|----------|--------|---------|
| `linux-x86_64` | `ubuntu-22.04` | `zycode-agent-linux-x86_64-<version>.tar.gz` |
| `linux-aarch64` | `ubuntu-22.04-arm` | `zycode-agent-linux-aarch64-<version>.tar.gz` |
| `darwin-aarch64` | `macos-15` | `zycode-agent-darwin-aarch64-<version>.tar.gz` |
| `darwin-x86_64` | `macos-15-intel` | `zycode-agent-darwin-x86_64-<version>.tar.gz` |
| `windows-x86_64` | `windows-2022` | `zycode-agent-windows-x86_64-<version>.zip` |

There is no `windows-aarch64` build: `tiktoken`, which `langchain-openai` needs, has no wheel
for it. PyInstaller does not cross-compile, so each archive comes from a runner of its own.
An archive holds one file at its top level: `zycode-agent` (`zycode-agent.exe` on Windows).
`<version>` is the version of `pi-agent-cli-lc` (the registry entry has one version for the PyPI
package and for the archives; the workflow stops when the tag differs from it).

The program is a PyInstaller **one-file** program (`packaging/agent/zycode-agent.spec`). It holds
the agent, the extensions, the OpenAI-format provider (`langchain-openai`) and nothing else:
`langchain_anthropic` and `langchain_deepseek` are left out on purpose. A model that needs a
provider that the build lacks fails with a text that says to use `provider = "openai"` and a
`base_url` (`--setup` writes that table). One-folder is the fallback if one-file fails on a
platform (slow start, antivirus): change `EXE`/`COLLECT` in the spec and `pack()` in
`build.py`; the registry archive then holds the folder, and `cmd` is the program inside it.

### 2.1 The workflow script process

The `workflow` tool runs a script in a child process (`docs/specs/2026-10-01-workflow-sandbox-design.md`).
A frozen program has no interpreter for `child.py`. The build freezes `child.py` alone, as a
one-folder program (`pi-sandbox-child`: standard library only, one process), and puts the
folder inside the agent program, in `pi_dynamic_workflows/sandbox/frozen-child`. A one-file child
would be two processes on Windows (start-up program and interpreter), and the job there allows
one. The layers of the sandbox do not change.

### 2.2 Environment of the tools

PyInstaller's start-up program puts its library folder first in `LD_LIBRARY_PATH` (Linux). A tool
that the agent starts (`bash`, `git`) would then load the bundle's `libssl` or `libz`. `entry.py`
restores the variable that the agent had at start. The smoke test checks it.

## 3. The workflow

`.github/workflows/agent-binaries.yml`. It does not touch `release.yml`.

| Event | What it does |
|-------|--------------|
| `pull_request` (paths: the workflow, `packaging/agent`, the sandbox, the CLI, the core) | Builds and tests on all platforms. Uploads nothing. |
| `workflow_run` of `Release`, when that run succeeded for a tag push (`v*`) | Builds the tag and attaches the archives to its release. |
| `workflow_dispatch` | With `tag`: the same as `workflow_run`, for that tag (use it to repair a release). Without `tag`: a dry run. |

**Why `workflow_run`.** The Release workflow creates the GitHub release (`gh release create`).
If this workflow started on the same tag push, it could create the release first and make that
step fail. An event made with `GITHUB_TOKEN` starts no workflow, so the Release workflow cannot
start this one itself. `workflow_run` starts after the Release run ends, so the release is
there. The `publish` job still checks for the release and creates it only if it is missing
(for a manual run), and it uploads with `--clobber`, so a second run is safe.
If the Release run fails (for example in a TUI job), nothing is attached: run
`workflow_dispatch` with the tag after the release exists.

Each build job runs, on its own platform:

1. `build.py` (about 1 minute on Linux).
2. `smoke.py --frozen`: a fake OpenAI-format server on `127.0.0.1`, the agent with
   `provider = "openai"`, one ACP turn. The model asks for the `workflow` tool (a script that
   calls `agent()` once: the frozen script process and a sub-agent run), and on Linux for `bash`
   (`git --version`, and `LD_LIBRARY_PATH` must be the value the agent had when it started). Then it starts a second agent with
   `provider = "anthropic"` and checks the advice text. It opens no connection to a non-local
   address.
3. The ACP contract tests (`test_acp_stdio_contract.py`) against the program
   (`PI_ACP_CONTRACT_COMMAND`).
4. The sandbox tests against the frozen script process (`PI_SANDBOX_FROZEN_CHILD`).

The `platform-tests` job (pull request and manual run) runs the tests of the changed code on
Windows and on macOS (Apple silicon and Intel), where the sandbox layers differ from Linux.

## 4. Registry entry (draft, not submitted)

Directory `zycode/` in the registry, files `agent.json` and `icon.svg` (16x16, monochrome,
`currentColor`; not made yet). `sha256` is left out: the update bot fills it from the
digests of the GitHub release assets. After a first manual pull request, the bot makes the
version bumps (hourly). A bump needs the PyPI package and all archives at the new version.

```json
{
  "id": "zycode",
  "name": "zycode",
  "version": "0.7.0",
  "description": "Coding agent on LangChain: workflows, sub-agents, extensions",
  "repository": "https://github.com/zy1233/pi-python",
  "website": "https://zycode.dev",
  "authors": ["zy1233"],
  "license": "MIT",
  "license_url": "https://github.com/zy1233/pi-python/blob/main/LICENSE",
  "icon": "./icon.svg",
  "distribution": {
    "binary": {
      "linux-x86_64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.7.0/zycode-agent-linux-x86_64-0.7.0.tar.gz",
        "cmd": "./zycode-agent"
      },
      "linux-aarch64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.7.0/zycode-agent-linux-aarch64-0.7.0.tar.gz",
        "cmd": "./zycode-agent"
      },
      "darwin-aarch64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.7.0/zycode-agent-darwin-aarch64-0.7.0.tar.gz",
        "cmd": "./zycode-agent"
      },
      "darwin-x86_64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.7.0/zycode-agent-darwin-x86_64-0.7.0.tar.gz",
        "cmd": "./zycode-agent"
      },
      "windows-x86_64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.7.0/zycode-agent-windows-x86_64-0.7.0.zip",
        "cmd": "./zycode-agent.exe"
      }
    },
    "uvx": { "package": "pi-agent-cli-lc==0.7.0" }
  }
}
```

Notes:

- `cmd` starts with `./` (Zed rejects other forms). Zed appends the arguments of the terminal
  login to `args`; the entry has none, so `--setup` is the whole argument list.
- The entry can only be submitted when the first release that has these archives exists (the
  URLs must answer with HTTP 200). `0.7.0` above is the version of the draft; the first
  archives will carry the version of the next release.
- The registry check runs the Linux x86_64 binary (and the `uvx` package) on `ubuntu-latest`
  with a 120 s limit, including the download. It needs an `initialize` answer with a terminal
  authentication method; the agent gives one.
- Do not comment on registry issue #726 and do not open the registry pull request before the
  user agrees.

## 5. Limits of the first version

| Limit | Effect |
|-------|--------|
| No signing | macOS: a binary that a browser downloaded carries the quarantine flag, and Gatekeeper refuses an unsigned one; a client that downloads it itself does not set the flag (not verified on a real Mac). Windows: SmartScreen and antivirus can warn about an unsigned one-file program. |
| Linux builds on `ubuntu-22.04` | Needs glibc 2.35 or newer (Ubuntu 22.04, Debian 12, RHEL 9). Debian 11 and RHEL 8 do not run it. A build in an older container needs a Python with a shared `libpython`; not done. |
| One-file start | Each start unpacks the program to a temporary folder: about 1.7 s to the `initialize` answer on Linux, against 0.7 s for `python -m`. |
| Provider | Only the OpenAI-format provider. Anthropic and DeepSeek in a frozen build: use `provider = "openai"` with `base_url`, or the Python package. |
| No `windows-aarch64` | A Windows on Arm client finds no key for its platform. |
| Intel Mac | `macos-15-intel` is on GitHub's list of runners that end; check the notices. |

## 6. Trial results

Trial run on the pull request branch (2026-10-31, run "Agent binaries" 41176423902). All steps
passed on all five targets: build, smoke test, 13 ACP contract tests against the program, and the
sandbox tests against the frozen script process. The `publish` job did not run: it runs only for a
tag, so the upload path is untested until the first real release.

| Target | Runner | Program | Archive | Smoke + contract + sandbox |
|--------|--------|---------|---------|----------------------------|
| linux-x86_64 | `ubuntu-22.04` | 76.7 MB | 76.0 MB | pass (154 passed, 10 skipped) |
| linux-aarch64 | `ubuntu-22.04-arm` | 74.2 MB | 73.5 MB | pass (154 passed, 10 skipped) |
| darwin-aarch64 | `macos-15` | 41.1 MB | 40.6 MB | pass (149 passed, 15 skipped) |
| darwin-x86_64 | `macos-15-intel` | 40.9 MB | 40.5 MB | pass (149 passed, 15 skipped) |
| windows-x86_64 | `windows-2022` | 42.6 MB | 42.1 MB | pass (152 passed, 12 skipped) |

The non-frozen sandbox tests also pass on `windows-2022`, `macos-15` and `macos-15-intel`.
On macOS the sandbox has no memory limit and no process limit (measured; sandbox design,
section 5). Those two tests are skipped there.

Not tested by a machine: Gatekeeper and quarantine for the unsigned macOS program, SmartScreen
and anti-virus for the unsigned Windows program, and a real download and start from Zed or
JetBrains.
