# Agent binaries and the ACP registry entry

> Scope: ship the ACP agent (`python -m pi_agent_cli`) as one executable per platform, for the
> `binary` entry of the [ACP registry](https://github.com/agentclientprotocol/registry).
> Decision: 2026-10-31. The user chose the `binary` route, PyInstaller one-file, five targets,
> no signing in the first version, and a workflow of its own. Release 0.8.0 is the first release
> that has the archives (section 7). The registry entry is binary only (section 4); the user
> submits the registry pull request by hand.
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

The first draft had a `uvx` distribution in the same entry. It was dropped (section 4.1).
`pi-agent-cli-lc` still depends on the OpenAI-format provider (`pi-agent-core-lc[openai]`), so a
bare `uvx pi-agent-cli-lc` has a provider; that stays useful for users who start the package
themselves, as in the README section "In Zed (custom agent)".

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

## 4. Registry entry (final for 0.8.0, not submitted)

Directory `zycode/` in the registry, files `agent.json` and `icon.svg`. The user submits the pull
request by hand. The entry is binary only. `sha256` is set for each archive (the digest of the
release asset). There is no `icon` field: the registry build makes it from `icon.svg`
(16x16, monochrome, `currentColor` only).

```json
{
  "id": "zycode",
  "name": "zycode",
  "version": "0.8.0",
  "description": "Coding agent on LangChain: workflows, sub-agents, extensions",
  "repository": "https://github.com/zy1233/pi-python",
  "website": "https://zycode.dev",
  "authors": ["zy1233"],
  "license": "MIT",
  "license_url": "https://github.com/zy1233/pi-python/blob/main/LICENSE",
  "distribution": {
    "binary": {
      "linux-x86_64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.8.0/zycode-agent-linux-x86_64-0.8.0.tar.gz",
        "sha256": "4ab0adec5d2ed3519ed0372862497c6a4efd75c5215138ebaf967cc59a92c5cf",
        "cmd": "./zycode-agent"
      },
      "linux-aarch64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.8.0/zycode-agent-linux-aarch64-0.8.0.tar.gz",
        "sha256": "af9ecddc0cd8f8493d6d6d1c87e2f0bee7165cf91e49c18eb74ee7fb09e8d370",
        "cmd": "./zycode-agent"
      },
      "darwin-aarch64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.8.0/zycode-agent-darwin-aarch64-0.8.0.tar.gz",
        "sha256": "f2fddfc36432deafdef5705db858a15500ba293abd07df9e8c9145017ce63661",
        "cmd": "./zycode-agent"
      },
      "darwin-x86_64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.8.0/zycode-agent-darwin-x86_64-0.8.0.tar.gz",
        "sha256": "35dd2ebbf457b9cb5f339bc7de6fc5ec412be7883379ec20f1543d20e307ea52",
        "cmd": "./zycode-agent"
      },
      "windows-x86_64": {
        "archive": "https://github.com/zy1233/pi-python/releases/download/v0.8.0/zycode-agent-windows-x86_64-0.8.0.zip",
        "sha256": "972059ec552fe92f219e39508907073a8e5ffec5d685826a5c6cfcf970a8383e",
        "cmd": "./zycode-agent.exe"
      }
    }
  }
}
```

The files are in the Project Agent Store (`docs/registry-entry/`: `agent.json`, `icon.svg`,
`submit.md` with the steps and the text of the pull request).

Rules of the registry (read again on 2026-10-31, registry commit `6772a52`; they did not
change between the first and the second read):

- `sha256` is optional in the schema, but the guide says binary distributions "should pin" it.
  The update bot writes the digest of the release asset into each binary target at a version bump,
  and the verifier compares an archive with it when it is set.
- A `uvx` entry has only `package`, `args` and `env`. `args` come after the package name, so the
  entry cannot pass `--with` or `--from`. A package name with extras (`name[extra]==1.0`) fails the
  registry's PyPI check, because it cuts the name at `==`, `<`, `>`, `!` and `@` only. A `uvx`
  entry therefore cannot add extra packages.
- Zed reads only `binary` and `npx` and skips an entry that has only `uvx`. The `binary` entries
  are what Zed reads, so no npm launcher is needed.
- The platform keys are `darwin-aarch64`, `darwin-x86_64`, `linux-aarch64`, `linux-x86_64`,
  `windows-aarch64` and `windows-x86_64`. A missing OS family only gives a warning. There is no
  `windows-aarch64` entry because there is no build (section 2).
- `cmd` starts with `./` (Zed rejects other forms; the guide's example `your-binary.exe` is
  not enough for Zed on Windows). Zed appends the arguments of the terminal login to `args`; the
  entry has none, so `--setup` is the whole argument list.
- The version in the URLs must equal `version`, a URL must not contain `/latest/`, and every URL
  must answer with HTTP 200. The pull request has no template. Open pull requests use the title
  `Add <Name> agent` and the branch `add-<id>`.
- The registry check runs the Linux x86_64 binary on `ubuntu-latest` with a 120 s limit, including
  the download. It needs an `initialize` answer with a terminal authentication method; the agent
  gives one.
- Checked locally on 2026-10-31 against the final entry: `build_registry.py --agents zycode` (URL
  checks on) validated it; `verify_agents.py --agent zycode --type binary` and its `--auth-check`
  passed.
- The `description` stays plain on purpose. The weaker `workflow` sandbox on macOS (no kernel
  limit on memory or on process count) is documented in the README of the repository and in
  `packages/pi-dynamic-workflows/README.md`, not in the registry entry or in the pull request.
- Do not comment on registry issue #726.

### 4.1 Decision: no `uvx` in the entry (2026-10-31)

The first draft had `"uvx": { "package": "pi-agent-cli-lc==<version>" }` next to `binary`. The
user dropped it, for two reasons:

- A plain `uvx pi-agent-cli-lc==0.8.0` has no `workflow` tool. `pi-dynamic-workflows-py` (and the
  web, goal and MCP extensions) are separate packages that the CLI does not depend on, and an entry
  cannot add them (see the rules above). With `--with pi-dynamic-workflows-py` the smoke test
  passes, so only the entry form is the limit. The frozen binary holds the extensions. Two forms
  under one name would give users a weaker agent without a sign.
- With `binary` and `uvx` in one entry, the update bot takes the version that PyPI and the GitHub
  assets both have. In 0.8.0, PyPI was up about 34 minutes before the release and the archives
  about 11 minutes after it (section 7), so one slow job would block the bump.

What it costs: JetBrains and the community DSH adapter read `uvx` entries (research, with the
manifest part closed source for JetBrains). Whether JetBrains starts a `binary` entry is not
checked. A `uvx` form that has the extensions needs a code change (the CLI depends on them, or a
meta package) and a new release; it is not planned.

## 5. Limits of the first version

| Limit | Effect |
|-------|--------|
| No signing | macOS: a binary that a browser downloaded carries the quarantine flag, and Gatekeeper refuses an unsigned one; a client that downloads it itself does not set the flag (not verified on a real Mac). Windows: SmartScreen and antivirus can warn about an unsigned one-file program. |
| Linux builds on `ubuntu-22.04` | Needs glibc 2.35 or newer (Ubuntu 22.04, Debian 12, RHEL 9). Debian 11 and RHEL 8 do not run it. A build in an older container needs a Python with a shared `libpython`; not done. |
| One-file start | Each start unpacks the program to a temporary folder: about 1.7 s to the `initialize` answer on Linux, against 0.7 s for `python -m`. |
| Provider | Only the OpenAI-format provider. Anthropic and DeepSeek in a frozen build: use `provider = "openai"` with `base_url`, or the Python package. |
| No `windows-aarch64` | A Windows on Arm client finds no key for its platform. |
| Intel Mac | `macos-15-intel` is on GitHub's list of runners that end; check the notices. |

## 6. Release steps

Order, as the workflow files define it (the user decided on 2026-10-31: publish first, then the
registry pull request; that pull request is not submitted).

**Before the tag.**

1. Merge #70, then #71, into `main`. A `workflow_run` workflow uses the file on the default
   branch (GitHub rule; not tried here), so `agent-binaries.yml` must be on `main` before the tag.
2. Choose a new version. `v0.7.0` is already released (GitHub release and PyPI have `0.7.0`;
   checked on 2026-10-31), and `release.yml` ends with `gh release create`, which fails for an
   existing release. Change the version of `pi-agent-core-lc` (root `pyproject.toml`),
   `pi-agent-harness-lc` and `pi-agent-cli-lc` together, and every `==0.7.0` pin between them
   (`packages/pi-agent-cli/pyproject.toml`, `packages/pi-agent-harness/pyproject.toml`, the
   extras in the root `pyproject.toml`). Add a `## [<version>]` section to `CHANGELOG.md`: the
   release job copies the notes from it. The workflow compares the tag with the version of
   `pi-agent-cli-lc` and stops if `v<version>` differs.

**After the tag `v<version>` (push of a tag that starts with `v`).**

1. `Release` starts (`release.yml`): tests, builds, the TUI binaries, then `Publish to PyPI`
   and `GitHub Release`. The last one creates the release with the Python packages and the TUI
   files.
2. When `Release` ends with `success` for that tag push, `Agent binaries` starts by itself
   (`workflow_run`). If `Release` fails in any job, it does not start, even when PyPI already has
   the packages.
3. `Agent binaries`: `plan` (checks the tag against the version), then five build jobs (build,
   smoke test, contract tests, sandbox tests), then `publish`. `publish` attaches the five
   archives to the release with `gh release upload --clobber` and prints the name, size and
   digest of each asset. A failure in one build job stops `publish`: nothing is attached.

**If the archives are not attached.** The archives are also kept as workflow artifacts
(`agent-<target>`). Two ways to fix it:

- Run the workflow again for the tag: `gh workflow run agent-binaries.yml --ref main -f tag=v<version>`
  (or Actions, "Agent binaries", "Run workflow", field `tag`). It builds the tag (checkout
  `refs/tags/v<version>`), tests it, and `publish` uploads with `--clobber`, so a second run is
  safe. It creates the release if it is missing. This works only for a tag whose tree has
  `packaging/agent/`, so a tag that was made after #71 was merged. That `workflow_dispatch` runs
  from `main` for an existing release is a GitHub rule; it is **not tested** here, because the
  file is not on `main` yet.
- By hand: download the artifacts of a green run (or build with
  `python packaging/agent/build.py`), then
  `gh release upload v<version> zycode-agent-<target>-<version>.tar.gz ... --clobber`
  (`.zip` for Windows). The names must stay `zycode-agent-<target>-<version>.<ext>`: the registry
  bot rewrites the version in a URL only in the forms `-X.` and `-X-`. Not tested.

**Check the release.** Five assets: `linux-x86_64`, `linux-aarch64`, `darwin-aarch64`,
`darwin-x86_64` (`.tar.gz`) and `windows-x86_64` (`.zip`), each with a digest
(`gh release view v<version> --json assets --jq '.assets[] | "\(.name) \(.digest)"'`). The PyPI
package `pi-agent-cli-lc==<version>` must exist (the version of the archives and of the package
must be the same).

**What the registry entry needs after a release** (section 4; the pull request waits for the
user, who submits it by hand):

- `version`: `<version>` (three numbers, no `v`, no `latest`).
- The five `archive` URLs with `/v<version>/` and `-<version>.`, and the `sha256` of each
  archive, read from the release (`gh release view v<version> --json assets`).
  The versions in the URLs must equal `version`; every URL must answer with HTTP 200.
- `icon.svg` next to `agent.json`: 16x16, monochrome, `currentColor` (the file exists).
- After the first pull request, the bot makes the version bumps (hourly). It needs the PyPI package
  and the GitHub assets at the new version, so a release without archives stops the bump.
  That bump is not seen yet.

## 7. Trial and release results

Trial run on the pull request branch (2026-10-31, run "Agent binaries" 41176423902). All steps
passed on all five targets: build, smoke test, 13 ACP contract tests against the program, and the
sandbox tests against the frozen script process. The `publish` job did not run: it runs only for a
tag. The release of 0.8.0 (below) ran it for the first time.

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

### 7.1 The release of 0.8.0 (2026-10-31)

The release record of the worker that ran it is in the Project Agent Store
(`internal/release-0-8-0-merge-status.md`). The facts that matter for this design:

| Time (UTC) | Step |
|------------|------|
| by 08:52 | The user merged #70 and #71 into `main`. The agents' token could not merge (`403 Resource not accessible by personal access token`). |
| 09:21 | The `Release` dry run (`workflow_dispatch`, run 41186552284) ended. The user had to start it, for the same reason. It passed: tests, packages, both TUI builds; `Publish to PyPI` and `GitHub Release` skipped. A `workflow_run` start of `Agent binaries` followed it and skipped every job, as designed. This was the first proof that `workflow_run` starts from the file on `main`. |
| 09:49 | The user merged #72 (version 0.8.0, changelog). |
| 09:52 | The tag `v0.8.0` was pushed. `Release` (run 41190427666) started. |
| 09:57 | `Publish to PyPI` finished (7 packages). It does not wait for the TUI builds. |
| 10:12 | The TUI build for Linux x86_64 finished. |
| 10:31 | The TUI build for macOS arm64 finished after about 34 minutes, the slowest job. `GitHub Release` waits for it, so the release (21 assets) came about 34 minutes after the PyPI upload. |
| 10:31 | `Agent binaries` (run 41193119162) started by `workflow_run`, as designed. `plan` passed the tag check. |
| 10:35 to 10:43 | The five build jobs passed (four within 5 minutes; `darwin-x86_64` took until 10:42). |
| 10:43 | `Attach to the release` uploaded the five archives (12 seconds). |

What this shows:

- `workflow_run` worked: no race with `gh release create`, and the archives landed on the release
  that `Release` made. About 11 minutes passed between the release and the last archive.
- `Publish to PyPI` runs before the TUI builds finish, and nothing can undo a PyPI upload. A
  failed TUI job after it leaves PyPI at the new version without a release. So the dry run of
  `Release` before the tag matters: it is the check that the TUI builds pass.
- The PyPI upload uses the API-token secret (`password: ${{ secrets.PYPI_API_TOKEN }}` in
  `release.yml`), so Trusted Publishing is off and the action makes no attestations (PEP 740). The
  upload worked. A Trusted Publisher could replace the token later; not done.
- The five archive URLs answer HTTP 200 without credentials. Each download has the size and the
  sha256 of the release digest. The Linux x86_64 archive, downloaded from the release URL, passes
  `smoke.py --frozen` (initialize in about 2.2 s; workflow script, sub-agent, `bash`, `git` and the
  advice for an unbundled provider work). The first run failed once with `Text file busy` (the
  file was run right after it was written); the second passed. It is a test race, not an archive
  defect.
- The registry's own checks pass for the final entry (section 4).
- A plain `uvx pi-agent-cli-lc==0.8.0` has no `workflow` tool; with `--with
  pi-dynamic-workflows-py==0.5.0` the full smoke test passes (section 4.1).

### 7.2 Not checked by a person

- The macOS programs (Apple silicon and Intel): Gatekeeper and quarantine for an unsigned
  program.
- The Windows program: SmartScreen and anti-virus for an unsigned one-file program.
- A real download and start from Zed or JetBrains (and whether JetBrains starts a `binary` entry).
- The registry's update bot (first version bump after the entry is merged).
- `workflow_dispatch` with a `tag` for an existing release (the repair path).
