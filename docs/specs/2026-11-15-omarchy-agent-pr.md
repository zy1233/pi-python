# Omarchy Linux: Add zycode (`zypi`) Coding Agent to Agent Menu

> Date: 2026-11-15 (updated 2026-11-19)  
> Status: **Sent upstream on 2026-11-19: [`omacom/omarchy#19501`](https://github.com/omacom/omarchy/pull/19501) (4 commits, 14 files, ready for review). The staging PR in the fork is `zy1233/omarchy#1`.** Before this, a review (2026-11-16) found two defects: `latest` resolved to the oldest release, and the agent id `zycode` was not the name of a command (the command is `zypi`). Both are fixed (commit `67fb4fe`). A test run then showed a third defect: the shell tests could write into a real zycode home (commit `6b1fb25`). On 2026-11-19 the branch was rebased on `quattro` (`fc781d3`) and got a fourth commit, because upstream had added a launcher for each agent. Sections 2 to 5 show the first three commits, as they were before the rebase. Section 6 has the rebase, the fourth commit, and the tests on a real Omarchy system.  
> Upstream target: `omacom/omarchy` (`quattro` branch)  
> Component: Omarchy desktop coding agent menu & launcher  

---

## 1. Background & Rationale

Omarchy Linux provides a curated set of coding agents selectable via the Super+A / Super+Space desktop Agent menu (`omarchy-default-agent`) and launchable via the unified terminal wrapper `omarchy-agent`. Supported agents range from cloud harness CLIs to open-source terminal agents (such as Pi, Qwen Code, Kiro, Devin, Amp, Command Code, etc.).

`zycode` is an open-source, full-featured agentic coding environment:
- **TUI binary (`zypi`)**: High-performance Rust terminal user interface built on an ACP client architecture with rich split-pane diff review, streaming markdown, syntax-highlighted tool inspections, and three permission modes (`ask`, `auto`, `always-approve`).
- **Core runtime (`zycode-agent`)**: Standalone binary executing the agent loop, coding tools (`read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`), sandboxed workflows, and MCP server integrations.
- **Self-contained distribution**: Starting with `v1.0.0` (Production/Stable), standalone combined tarballs `zycode-linux-x86_64-1.0.0.tar.gz` and `zycode-linux-aarch64-1.0.0.tar.gz` are published on GitHub Releases. Each archive extracts both `zypi` and `zycode-agent` to the installation prefix root.
- **Zero-runtime dependency**: When installed via mise's `http:` backend, `zypi` automatically discovers the sibling `zycode-agent` binary directly beside it, requiring no global Python runtime or system pip dependencies.
- **First-run setup**: `zypi --setup` provides an interactive terminal configuration wizard to choose a model provider and configure API keys.

Adding `zycode` to Omarchy gives users immediate, native access to zycode from Omarchy's Agent menu with automatic mise package installation, headless bypass (`--always-approve`), and shared AGENTS instructions.

---

## 2. Upstream PR Proposal

### PR Title
`feat(agent): add zycode (zypi) coding agent to Agent menu`

### PR Description
```markdown
### Overview
Adds `zycode` to Omarchy's default coding agent menu and launcher. Its command is `zypi`.

`zycode` is a terminal coding agent with an ACP-driven Rust TUI (`zypi`) and a standalone agent runtime (`zycode-agent`). Starting with `v1.0.0`, it publishes a release archive with both programs for Linux x86_64 and aarch64 (`zycode-linux-<arch>-<version>.tar.gz`, two flat files: `zypi` and `zycode-agent`).

### Changes
- **`etc/mise/conf.d/omarchy-tools.toml`**: declares `http:zycode` with `lazy = true`, `auto_update = "4h"` and `lazy_bins = ["zypi"]`. The archive URLs are for x86_64 and aarch64. The versions come from the GitHub Releases API (`.[].tag_name`) with `version_order = "semver"`.
- **`bin/omarchy-default-agent`**: `zycode` and `zypi` both select the agent. The saved name is `zypi`, because `omarchy-agent` checks the saved name as a command and runs it. The menu label is `zycode`.
- **`bin/omarchy-agent`**: launches `zypi --always-approve [-- "$prompt"]`.
- **`bin/omarchy-agent-instructions`**: links the shared instructions to `${PI_HOME:-$HOME/.pi-python}/agent/AGENTS.md`, the file that zycode reads (`context_files.py` of zycode).
- **`default/omarchy/omarchy-menu.jsonc`** and **`default/i18n/en/menu.json`**: menu entry `agent.harness.zypi`, label `zycode`.
- **`test/shell.d/default-agent-test.sh`**: expected declarations, lazy count (31 -> 32), self-updating count (21 -> 22), install, prompt launch and permission bypass for `zypi`. New checks: the saved name of each declared agent is the command that its declaration shims, and zycode launches once with the real `omarchy-cmd-missing`.
- **`test/shell.d/agent-instructions-test.sh`**: new check for the zycode link, in `~/.pi-python` and under a moved `PI_HOME`.
- **`test/shell.d/base-test.sh`**: clears `PI_HOME` (see below).

### Fixed after review (commits `67fb4fe` and `6b1fb25`)
- **`latest` was the oldest release.** mise takes the last entry of a version list as the newest. GitHub lists releases newest first. So `latest` resolved to `0.1.0`. `version_order = "semver"` fixes it. Checked with real mise (see below): without the option, `latest` is `0.1.0`; with it, `latest` is `1.0.0`.
- **The saved name was `zycode`, but the command is `zypi`.** `omarchy-agent` runs `omarchy-cmd-missing "$agent"`, so a saved `zycode` was refused as "not installed". The tests did not show this, because their `omarchy-cmd-missing` is a stub that says "present". The saved name is now `zypi`. `omarchy-default-agent zycode` still works.
- **URL templates use `{{ version }}`** (as the Devin line does). The form `{{version}}` gives a mise deprecation warning ("legacy-version-template", removed in mise 2027.3.0).
- **The tests could write into a real zycode home.** `omarchy-agent-instructions` links `agent/AGENTS.md` under `PI_HOME`, and a run without harness names links every harness whose directory exists. A shell that exports `PI_HOME`, as the shell of a zycode user does, sent the tests there. I found this when a test run left a link in a real zycode home. `base-test.sh` clears `PI_HOME` now, as it clears the homes of the other agents. `agent-instructions-test.sh` links zycode in `~/.pi-python` and under a moved `PI_HOME`, and checks that the clear works. The new test fails without the clear and without the link (both checked).

### Decision for the reviewer
The launcher runs agents without a stop to ask. For zycode this is `--always-approve`: no question before any tool call, `bash` included. zycode also has `--permission-mode auto` (rules, plus a model check for `bash`; it can still ask). Claude Code uses its auto mode in this launcher. Say if you prefer `auto` for zycode.

### Verification
- `bash test/shell.d/default-agent-test.sh`: **48 ok, 0 not ok** (47 before, plus the new launch check).
- `bash test/shell.d/i18n-test.sh`: **401 ok, 0 not ok** (1 of them is a SKIP). An earlier version of this text said "123"; that number was wrong.
- `agent-instructions-test.sh` 5 ok (4 before, plus the zycode link), `preinstalls-test.sh` 15 ok, `mise-dev-env-test.sh` 7 ok, `remove-ai-test.sh` 35 ok, `agent-account-test.sh` 32 ok, `first-run-test.sh` 5 ok, `agent-invitation-test.sh` 2 ok, `crash-capture-test.sh` 28 ok. All of these ran with `PI_HOME` set to an empty decoy directory. It stayed empty.
- `mise-provision-test.sh` with real mise 2026.11.8: `ok - mise resolves the backend options of Omarchy's declarations`. Without mise it only SKIPs this check. The script looks for `mise` only in `/usr/local/bin` and `/usr/bin`, so I ran a copy that also looks in the directory of my mise.
- With the old files and the new test, `default-agent-test.sh` fails at `user setup declares http:zycode with version_order inside its table`.
- Real mise 2026.11.8, in an isolated home. A local server took the place of GitHub. It served the real release list of `zy1233/pi-python` (11 releases, newest first) and fake archives with the real layout (two flat files). Results:
  - `latest` with `version_order = "semver"`: `1.0.0`. The same line without the option: `0.1.0`.
  - The version list, in the order that mise sees it: `0.1.0 0.2.0 0.3.0 0.4.0 0.5.0 0.6.0 0.7.0 0.8.0 0.9.0 0.9.1 1.0.0`.
  - Install: `http:zycode@1.0.0`. The install directory holds `zycode-agent` and `zypi`. `zypi` runs and finds `zycode-agent` next to it.
  - `mise reshim --system` makes a `zypi` shim, and `command -v zypi` finds it (this is the question that `omarchy-cmd-missing` asks). The first run through the shim installs `1.0.0` and starts `zypi`. The second run installs nothing.

### Not verified
- The real `v1.0.0` archive was not downloaded through mise. GitHub's asset download from the test machine ran at about 20 KB/s. The archive layout comes from `packaging/agent/build.py` of pi-python (`pack_bundle`).
- The aarch64 archive and the first use on a real Omarchy desktop (Hyprland, the menu in the live shell).
- `mise-agent-shims-migration-test.sh` and `agent-account-exec-test.sh` did not run to the end: they need a `mise` on their own test PATH, which this sparse clone did not give them. They do not use the zycode lines.
- A new release has its archives a few minutes after its tag (the pi-python agent-binaries workflow attaches them after the Release workflow). In that time `latest` can name a version that has no archive yet.

### Not in this PR
- The `kiro-cli` line still uses `{{version}}`. Real mise 2026.11.8 warns that this form is deprecated and will stop working in mise 2027.3.0.
```

---

## 3. Mise Backend Configuration (`http:zycode`)

Mise manages standalone binary packages via HTTP tarballs. The archive layout for `zycode` is flat:
```text
zycode-linux-x86_64-1.0.0.tar.gz
├── zypi (0755)
└── zycode-agent (0755)
```

In `etc/mise/conf.d/omarchy-tools.toml`:
```toml
"http:zycode" = { version = "latest", lazy = true, auto_update = "4h", lazy_bins = ["zypi"], minimum_release_age = "0s", platforms_linux_x64_url = "https://github.com/zy1233/pi-python/releases/download/v{{ version }}/zycode-linux-x86_64-{{ version }}.tar.gz", platforms_linux_arm64_url = "https://github.com/zy1233/pi-python/releases/download/v{{ version }}/zycode-linux-aarch64-{{ version }}.tar.gz", version_list_url = "https://api.github.com/repos/zy1233/pi-python/releases", version_json_path = ".[].tag_name", version_order = "semver" }
```

Key configuration points:
- `lazy = true`: `mise reshim --system` makes a shim for each name in `lazy_bins`. The first run through the shim installs the newest version and then starts it.
- `lazy_bins = ["zypi"]`: Only `zypi` is a command. `zycode-agent` stays beside it in the mise install directory, and `zypi` starts it from there. The saved default-agent name is `zypi` too, because `omarchy-agent` checks the saved name as a command and runs it. `zycode` is an alias.
- `auto_update = "4h"`: Updates check every 4 hours alongside other self-updating agent CLIs.
- `version_list_url` and `version_json_path`: Queries `https://api.github.com/repos/zy1233/pi-python/releases` and takes `.tag_name`. Mise removes the `v` prefix of tags such as `v1.0.0`.
- `version_order = "semver"`: Mise takes the last entry of a version list as the newest. GitHub lists releases newest first. Without this option, `latest` is `0.1.0`. With it, mise sorts the list by semver, and `latest` is `1.0.0`. Pre-releases stay out of the list unless `prerelease = true`.
- `{{ version }}`, with spaces: The form `{{version}}` gives a mise deprecation warning (`legacy-version-template`).

---

## 4. Full Git Diff Patch against `omacom/omarchy` (`quattro`)

Made with `gh pr diff 1 --repo zy1233/omarchy` at commit `6b1fb25` (3 commits, 9 files).

```patch
diff --git a/bin/omarchy-agent b/bin/omarchy-agent
index a2495296658..652703f8a84 100755
--- a/bin/omarchy-agent
+++ b/bin/omarchy-agent
@@ -277,6 +277,10 @@ commandcode)
   command=(commandcode --yolo)
   [[ -n ${prompt:-} ]] && command+=(-- "$prompt")
   ;;
+zypi)
+  command=(zypi --always-approve)
+  [[ -n ${prompt:-} ]] && command+=(-- "$prompt")
+  ;;
 *)
   echo "Unsupported default agent: $agent" >&2
   exit 1
diff --git a/bin/omarchy-agent-instructions b/bin/omarchy-agent-instructions
index 21f769d01a9..cedcc84bfde 100755
--- a/bin/omarchy-agent-instructions
+++ b/bin/omarchy-agent-instructions
@@ -51,6 +51,7 @@ declare -A links=(
   [kilo]="$config/kilo/AGENTS.md"
   [commandcode]="$HOME/.commandcode/AGENTS.md"
   [freebuff]="$HOME/.AGENTS.md"
+  [zypi]="${PI_HOME:-$HOME/.pi-python}/agent/AGENTS.md"
 )
 
 backups=()
diff --git a/bin/omarchy-default-agent b/bin/omarchy-default-agent
index cee381040f6..ba35df44205 100755
--- a/bin/omarchy-default-agent
+++ b/bin/omarchy-default-agent
@@ -1,7 +1,7 @@
 #!/bin/bash
 
 # omarchy:summary=Set and launch the default coding agent
-# omarchy:args=[pi|omp|opencode|ori|claude|codex|grok|openclaw|agy|hermes|copilot|crush|cursor-agent|muse|kimi|qwen|dsh|mcode|kiro-cli|freebuff|kilo|devin|amp|commandcode]
+# omarchy:args=[pi|omp|opencode|ori|claude|codex|grok|openclaw|agy|hermes|copilot|crush|cursor-agent|muse|kimi|qwen|dsh|mcode|kiro-cli|freebuff|kilo|devin|amp|commandcode|zycode]
 # omarchy:examples=omarchy default agent | omarchy default agent codex | omarchy default agent claude
 
 installing=false
@@ -64,8 +64,19 @@ devin | devin-cli)
   ;;
 amp | ampcode) agent="amp"; name="Amp" ;;
 commandcode | command-code | cmdc) agent="commandcode"; name="Command Code"; agent_package="npm:command-code" ;;
+zycode | zypi)
+  # The agent is named zycode, and its command is zypi. The saved name is the
+  # command, because omarchy-agent checks it and runs it as one.
+  agent="zypi"; name="zycode"
+  # zycode publishes per-platform archives containing zypi and zycode-agent.
+  # GitHub lists releases newest first, and mise takes the last entry of a list
+  # as the newest, so the versions are ordered by semver to make "latest" the
+  # newest release rather than the oldest.
+  agent_tool="http:zycode"
+  agent_package="$agent_tool[platforms_linux_x64_url=https://github.com/zy1233/pi-python/releases/download/v{{ version }}/zycode-linux-x86_64-{{ version }}.tar.gz,platforms_linux_arm64_url=https://github.com/zy1233/pi-python/releases/download/v{{ version }}/zycode-linux-aarch64-{{ version }}.tar.gz,version_list_url=https://api.github.com/repos/zy1233/pi-python/releases,version_json_path=.[].tag_name,version_order=semver]"
+  ;;
 *)
-  echo "Usage: omarchy-default-agent <pi|omp|opencode|ori|claude|codex|grok|openclaw|agy|hermes|copilot|crush|cursor-agent|muse|kimi|qwen|dsh|mcode|kiro-cli|freebuff|kilo|devin|amp|commandcode>"
+  echo "Usage: omarchy-default-agent <pi|omp|opencode|ori|claude|codex|grok|openclaw|agy|hermes|copilot|crush|cursor-agent|muse|kimi|qwen|dsh|mcode|kiro-cli|freebuff|kilo|devin|amp|commandcode|zycode>"
   exit 1
   ;;
 esac
diff --git a/default/i18n/en/menu.json b/default/i18n/en/menu.json
index 1221efa7d55..e5df2d20a7c 100644
--- a/default/i18n/en/menu.json
+++ b/default/i18n/en/menu.json
@@ -575,6 +575,9 @@
   "agent.harness.qwen": {
     "label": "Qwen Code"
   },
+  "agent.harness.zypi": {
+    "label": "zycode"
+  },
   "setup.default.browser": {
     "label": "Browser",
     "title": "Default Browser"
diff --git a/default/omarchy/omarchy-menu.jsonc b/default/omarchy/omarchy-menu.jsonc
index cbf2662a16c..819744dbeca 100644
--- a/default/omarchy/omarchy-menu.jsonc
+++ b/default/omarchy/omarchy-menu.jsonc
@@ -63,6 +63,7 @@
   "agent.harness.ori": {"icon":"","iconFont":"omarchy","label":"Ori","checked":"[[ \"$(omarchy-default-agent)\" == \"ori\" ]]","action":"omarchy-default-agent ori"},
   "agent.harness.pi": {"icon":"","iconFont":"omarchy","label":"Pi","checked":"[[ \"$(omarchy-default-agent)\" == \"pi\" ]]","action":"omarchy-default-agent pi"},
   "agent.harness.qwen": {"icon":"","iconFont":"omarchy","label":"Qwen Code","checked":"[[ \"$(omarchy-default-agent)\" == \"qwen\" ]]","action":"omarchy-default-agent qwen"},
+  "agent.harness.zypi": {"icon":"󰚩","label":"zycode","checked":"[[ \"$(omarchy-default-agent)\" == \"zypi\" ]]","action":"omarchy-default-agent zypi"},
   "agent.instructions": {"icon":"󰈙","label":"Instructions","action":"omarchy-agent-instructions --edit"},
 
   // System
diff --git a/etc/mise/conf.d/omarchy-tools.toml b/etc/mise/conf.d/omarchy-tools.toml
index b784d7198a9..8bcaf4c5106 100644
--- a/etc/mise/conf.d/omarchy-tools.toml
+++ b/etc/mise/conf.d/omarchy-tools.toml
@@ -23,6 +23,7 @@ grok = { version = "latest", lazy = true, auto_update = "4h", minimum_release_ag
 hey-cli = { version = "latest", lazy = true, minimum_release_age = "0s" }
 "http:devin" = { version = "latest", lazy = true, auto_update = "4h", lazy_bins = ["devin"], minimum_release_age = "0s", platforms_linux_x64_url = "https://static.devin.ai/cli/{{ version }}/devin-{{ version }}-x86_64-unknown-linux.tar.gz", platforms_linux_arm64_url = "https://static.devin.ai/cli/{{ version }}/devin-{{ version }}-aarch64-unknown-linux.tar.gz", bin_path = "bin", version_list_url = "https://static.devin.ai/cli/current/manifest.json", version_json_path = ".version" }
 "http:kiro-cli" = { version = "latest", lazy = true, auto_update = "4h", lazy_bins = ["kiro-cli"], minimum_release_age = "0s", platforms_linux_x64_url = "https://prod.download.cli.kiro.dev/stable/{{version}}/kirocli-x86_64-linux.tar.gz", platforms_linux_arm64_url = "https://prod.download.cli.kiro.dev/stable/{{version}}/kirocli-aarch64-linux.tar.gz", version_list_url = "https://prod.download.cli.kiro.dev/stable/latest/manifest.json", version_json_path = ".version" }
+"http:zycode" = { version = "latest", lazy = true, auto_update = "4h", lazy_bins = ["zypi"], minimum_release_age = "0s", platforms_linux_x64_url = "https://github.com/zy1233/pi-python/releases/download/v{{ version }}/zycode-linux-x86_64-{{ version }}.tar.gz", platforms_linux_arm64_url = "https://github.com/zy1233/pi-python/releases/download/v{{ version }}/zycode-linux-aarch64-{{ version }}.tar.gz", version_list_url = "https://api.github.com/repos/zy1233/pi-python/releases", version_json_path = ".[].tag_name", version_order = "semver" }
 hunk = { version = "latest", lazy = true, minimum_release_age = "0s" }
 muse = { version = "latest", lazy = true, auto_update = "4h", minimum_release_age = "0s" }
 node = { version = "latest", lazy = true, minimum_release_age = "0s" }
diff --git a/test/shell.d/agent-instructions-test.sh b/test/shell.d/agent-instructions-test.sh
index fa3709d1c80..1e8c4ae3420 100755
--- a/test/shell.d/agent-instructions-test.sh
+++ b/test/shell.d/agent-instructions-test.sh
@@ -82,3 +82,18 @@ GROK_HOME=$account instructions grok >/dev/null
 HOME=$test_home PATH="$ROOT/bin:$PATH" omarchy-agent-account-state link grok "$account" >/dev/null
 [[ $account/AGENTS.md -ef $canonical ]] || fail "an added Grok account reads the shared rules" "$(ls -la "$account")"
 pass "an added Grok account reads the shared rules"
+
+# zycode reads its rules from agent/AGENTS.md in its home: ~/.pi-python, or
+# where PI_HOME puts it. A shell that exports PI_HOME must not take the tests
+# out of their scratch HOME, so base-test.sh clears it.
+new_home zycode
+instructions zypi >/dev/null
+[[ $test_home/.pi-python/agent/AGENTS.md -ef $canonical ]] ||
+  fail "zycode is linked in its home" "$(ls -la "$test_home/.pi-python/agent")"
+new_home zycode-moved
+PI_HOME=$test_home/zycode instructions zypi >/dev/null
+[[ $test_home/zycode/agent/AGENTS.md -ef $canonical && ! -e $test_home/.pi-python ]] ||
+  fail "PI_HOME moves the zycode link" "$(ls -la "$test_home" "$test_home/zycode/agent")"
+[[ $(PI_HOME=$test_tmp/outside bash -c 'source "$1" && printf "%s" "${PI_HOME-unset}"' _ "$SHELL_TEST_DIR/base-test.sh") == "unset" ]] ||
+  fail "tests do not inherit PI_HOME from the shell that runs them"
+pass "zycode reads the shared rules, in its home or where PI_HOME puts it"
diff --git a/test/shell.d/base-test.sh b/test/shell.d/base-test.sh
index 9df5241ec85..24acd04ec68 100644
--- a/test/shell.d/base-test.sh
+++ b/test/shell.d/base-test.sh
@@ -30,8 +30,10 @@ export OMARCHY_LANGUAGE_FILE=$ROOT/test/.no-language
 unset XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME XDG_STATE_HOME
 
 # Run from inside an agent session, the agent's own account home would leak
-# into tests of agent accounts the same way.
-unset CLAUDE_CONFIG_DIR CODEX_HOME GROK_HOME OMARCHY_AGENT_CLAUDE_HOME OMARCHY_AGENT_CODEX_HOME OMARCHY_AGENT_GROK_HOME
+# into tests of agent accounts the same way. PI_HOME is zycode's home, where
+# the instructions link goes: a shell that exports it would send a test to the
+# developer's real zycode home.
+unset CLAUDE_CONFIG_DIR CODEX_HOME GROK_HOME OMARCHY_AGENT_CLAUDE_HOME OMARCHY_AGENT_CODEX_HOME OMARCHY_AGENT_GROK_HOME PI_HOME
 
 pass() {
   printf 'ok - %s\n' "$1"
diff --git a/test/shell.d/default-agent-test.sh b/test/shell.d/default-agent-test.sh
index 57b877f82cf..35e34eb01a0 100644
--- a/test/shell.d/default-agent-test.sh
+++ b/test/shell.d/default-agent-test.sh
@@ -168,6 +168,9 @@ amp_package="amp"
 commandcode_package="npm:command-code"
 cursor_agent_package="cursor-agent"
 muse_package="muse"
+# zycode is the name of the agent, and zypi is its command and its saved name.
+zypi_tool="http:zycode"
+zypi_package="$zypi_tool[platforms_linux_x64_url=https://github.com/zy1233/pi-python/releases/download/v{{ version }}/zycode-linux-x86_64-{{ version }}.tar.gz,platforms_linux_arm64_url=https://github.com/zy1233/pi-python/releases/download/v{{ version }}/zycode-linux-aarch64-{{ version }}.tar.gz,version_list_url=https://api.github.com/repos/zy1233/pi-python/releases,version_json_path=.[].tag_name,version_order=semver]"
 
 assert_lazy_stub() {
   local package=$1
@@ -229,10 +232,16 @@ declare -A declared_option_bins=(
   [mcode]='"mcode"'
   [kiro_cli]='"kiro-cli"'
   [devin]='"devin"'
+  [zypi]='"zypi"'
 )
 for agent in "${!declared_option_bins[@]}"; do
   tool_var="${agent}_tool"
   package_var="${agent}_package"
+  # omarchy-agent checks the saved agent name as a command and runs it, so the
+  # name is the command that the declaration shims.
+  declared_bin=${declared_option_bins[$agent]//\"/}
+  [[ ${agent//_/-} == "$declared_bin" ]] ||
+    fail "the saved name of $agent is the command that its declaration shims"
   declaration=$(grep -F "\"${!tool_var}\" = { version = \"latest\", lazy = true, auto_update = \"4h\", lazy_bins = [${declared_option_bins[$agent]}], minimum_release_age = \"0s\", " "$lazy_config") ||
     fail "user setup declares ${!tool_var} as a self-updating native lazy tool with its commands"
   options=${!package_var#*[}
@@ -242,12 +251,12 @@ for agent in "${!declared_option_bins[@]}"; do
       fail "user setup declares ${!tool_var} with ${option%%=*} inside its table"
   done
 done
-[[ $(grep -c 'auto_update = "4h"' "$lazy_config") == 21 ]] || fail "only the agent CLIs update themselves"
+[[ $(grep -c 'auto_update = "4h"' "$lazy_config") == 22 ]] || fail "only the agent CLIs update themselves"
 for tool in node uv; do
   grep -Eq "^$tool = \\{ version = \"latest\", lazy = true, minimum_release_age = \"0s\" \\}$" "$lazy_config" ||
     fail "user setup declares $tool as a native lazy tool"
 done
-[[ $(grep -c 'lazy = true' "$lazy_config") == 31 ]] || fail "user setup declares every default mise tool as lazy"
+[[ $(grep -c 'lazy = true' "$lazy_config") == 32 ]] || fail "user setup declares every default mise tool as lazy"
 grep -Fx "reshim --system" "$mise_history" >/dev/null || fail "user setup builds the system lazy bootstrap shims"
 pass "user setup configures system lazy tools from registry shorthands"
 : >"$stub_log"
@@ -424,6 +433,8 @@ declare -A expected_agents=(
   [muse]="muse"
   [muse-code]="muse"
   [musecode]="muse"
+  [zycode]="zypi"
+  [zypi]="zypi"
 )
 
 declare -A expected_packages=(
@@ -449,6 +460,7 @@ declare -A expected_packages=(
   [commandcode]="$commandcode_package"
   [cursor-agent]="$cursor_agent_package"
   [muse]="$muse_package"
+  [zypi]="$zypi_package"
 )
 
 for selection in "${!expected_agents[@]}"; do
@@ -707,7 +719,7 @@ mapfile -d '' -t agent_open_args <"$agent_open_log"
 pass "Muse installs visibly through mise and opens directly"
 
 saved_agent=$(<"$agent_file")
-for agent in qwen dsh mcode kiro-cli freebuff kilo devin amp commandcode; do
+for agent in qwen dsh mcode kiro-cli freebuff kilo devin amp commandcode zypi; do
   tool_var="${agent//-/_}_tool"
   package_var="${agent//-/_}_package"
   declared_tool=${!tool_var:-${!package_var}}
@@ -715,7 +727,7 @@ for agent in qwen dsh mcode kiro-cli freebuff kilo devin amp commandcode; do
   omarchy-default-agent --install "$agent" >/dev/null
   # npm-backed agents bring node along, which a fresh machine doesn't have yet.
   case "$agent" in
-  kiro-cli | devin) expected_install="install $declared_tool" ;;
+  kiro-cli | devin | zypi) expected_install="install $declared_tool" ;;
   *) expected_install="install node $declared_tool" ;;
   esac
   grep -Fx "$expected_install" "$mise_history" >/dev/null || fail "visible $agent installation installs Omarchy's declaration (expected: $expected_install)"
@@ -842,6 +854,7 @@ assert_launch kilo kilo --auto "--prompt=Review this project"
 assert_launch devin devin --permission-mode dangerous --respect-workspace-trust false -- "Review this project"
 assert_launch amp bash -c 'printf "%s\n" "$1" | exec "$0" --dangerously-allow-all' amp "Review this project"
 assert_launch commandcode commandcode --yolo -- "Review this project"
+assert_launch zypi zypi --always-approve -- "Review this project"
 [[ $(<"$clipboard_log") == "Review this project" ]] || fail "Freebuff launcher puts the prompt on the clipboard"
 mapfile -d '' -t notification_args <"$notification_history"
 [[ ${notification_args[*]} == *"Freebuff takes no starting prompt"* ]] || fail "Freebuff launcher says where the prompt went"
@@ -885,9 +898,36 @@ assert_bypass kilo kilo --auto
 assert_bypass devin devin --permission-mode dangerous --respect-workspace-trust false
 assert_bypass amp amp --dangerously-allow-all
 assert_bypass commandcode commandcode --yolo
+assert_bypass zypi zypi --always-approve
 [[ ! -s $clipboard_log && ! -s $notification_history ]] || fail "Freebuff launcher leaves the clipboard alone without a prompt"
 pass "agent launcher skips permission prompts for every supported agent"
 
+# omarchy-agent checks the saved name as a command, and the mock above says that
+# no command is missing. Run zycode once with the real omarchy-cmd-missing:
+# the archive installs zypi and zycode-agent, so zypi is the only name that
+# passes. A saved "zycode" would be refused as not installed.
+real_cmd_bin="$test_tmp/real-cmd-bin"
+mkdir -p "$real_cmd_bin"
+for stub in "$mock_bin"/*; do
+  [[ ${stub##*/} == omarchy-cmd-missing ]] || ln -s "$stub" "$real_cmd_bin/${stub##*/}"
+done
+cat >"$real_cmd_bin/zypi" <<'SH'
+#!/bin/bash
+exit 0
+SH
+chmod +x "$real_cmd_bin/zypi"
+saved_path=$PATH
+PATH="$real_cmd_bin:$ROOT/bin:$saved_path"
+assert_launch zypi zypi --always-approve -- "Review this project"
+printf '%s\n' zycode >"$agent_file"
+if omarchy-agent >"$test_tmp/zycode-name-output" 2>&1; then
+  fail "agent launcher refuses a saved name that is not a command"
+fi
+grep -F "zycode is not installed" "$test_tmp/zycode-name-output" >/dev/null ||
+  fail "agent launcher says that a saved name without a command is not installed"
+PATH=$saved_path
+pass "agent launcher finds zycode by its command, checked with the real omarchy-cmd-missing"
+
 printf '%s\n' "opencode" >"$agent_file"
 omarchy-agent
 mapfile -d '' -t launch_args <"$launch_log"
```

---

## 5. Test Evidence

The shell tests ran in WSL (Ubuntu) on the branch `feat/add-zycode-agent` at commit `6b1fb25`. `PI_HOME` pointed at an empty decoy directory, and it stayed empty.

### A. Omarchy Agent Test (`test/shell.d/default-agent-test.sh`)
```bash
$ bash test/shell.d/default-agent-test.sh
ok - custom agent lazy stubs preserve their mise packages
ok - user setup configures system lazy tools from registry shorthands
ok - user setup leaves ordinary lazy wrappers to mise
ok - Remove Preinstalls deletes every optional agent lazy stub
ok - Remove Preinstalls keeps an official Cursor CLI install
ok - Remove Preinstalls keeps a user-managed Muse install
ok - default agent is unset until one is chosen
ok - agent launcher refuses to launch without a default
ok - agent launcher opens a local model without a default
ok - agent launcher opens Qwen Code on a local model, keeping its key off the command line
ok - --pick opens the agent defaults menu when none is set
ok - terminal alias launches the default agent inline
ok - agent launcher has a keyboard shortcut
ok - default agent selects and opens every supported provider and alias
ok - default agent stores its selection in Omarchy user config
ok - extension installation failure silently continues selecting and launching Claude
ok - missing agents install visibly and open in the same terminal
ok - an agent installed from the Quake console opens tiled in the console
ok - an install from a terminal sharing one process across windows finds its own window
ok - installed agents select and open without notifications
ok - selecting an official Cursor CLI install skips mise
ok - a dead file at the wrapper's path does not pass for an install
ok - default agent rejects unsupported providers without changing the selection
ok - default agent opens only after mise installs the provider
ok - default agent reports mise failures without notifications
ok - failed Muse mise installation preserves the selection and skips login
ok - Muse installs visibly through mise and opens directly
ok - the added agents install through Omarchy's mise declarations
ok - installed Muse selects and opens directly
ok - default agent reports Muse mise failures without changing the selection
ok - selecting a user-installed Muse preserves its launcher
ok - agent launcher adapts initial prompts for every supported agent
ok - Muse receives option-like prompts as one literal argument
ok - Hermes receives prompted launches as one literal query argument
ok - agent launcher skips permission prompts for every supported agent
ok - agent launcher finds zycode by its command, checked with the real omarchy-cmd-missing
ok - agent launcher starts the selected agent without an initial prompt
ok - inline agent launcher runs in the current terminal
ok - --pick launches once an agent is chosen
ok - --prefer-local opens the running local model, and the usual agent otherwise
ok - omarchy agent keeps prompts on the prompt route
ok - agent launcher reports a missing default command
ok - choosing OpenClaw uses its runtime and launches its terminal UI
ok - a missing OpenClaw routes through the install terminal
ok - installing OpenClaw as default agent sets up its runtime
ok - OpenClaw receives prompts through --message
ok - OpenClaw's agent hands a missing command to its launcher only when the package is there
ok - agent launcher runs user-installed agents ahead of native lazy shims
```
48 `ok` lines and no `not ok` line. The PR had 47 `ok` lines before the review fix. The new one is `agent launcher finds zycode by its command, checked with the real omarchy-cmd-missing`.

### B. Instructions Test (`test/shell.d/agent-instructions-test.sh`)
```bash
$ bash test/shell.d/agent-instructions-test.sh
ok - an existing instructions link becomes the shared file
ok - a differing instructions link is kept as .bak, with a notice or without
ok - a dangling instructions link is left alone
ok - an added Grok account reads the shared rules
ok - zycode reads the shared rules, in its home or where PI_HOME puts it
```

### C. Omarchy Menu & i18n Test (`test/shell.d/i18n-test.sh`)
401 `ok` lines and no `not ok` line. One of the `ok` lines is a SKIP (`no translations are pending (structural mode)`). The key `agent.harness.zypi` is in `default/omarchy/omarchy-menu.jsonc` and in `default/i18n/en/menu.json`. (The first version of this spec said 123 tests. That number was wrong.)

### D. Other tests that use the changed files
- `agent-account-test.sh` 32 ok, `agent-invitation-test.sh` 2 ok, `crash-capture-test.sh` 28 ok, `first-run-test.sh` 5 ok, `mise-dev-env-test.sh` 7 ok, `preinstalls-test.sh` 15 ok, `remove-ai-test.sh` 35 ok. None has a `not ok` line.
- `mise-provision-test.sh` with real mise 2026.11.8: 2 ok, among them `mise resolves the backend options of Omarchy's declarations`. The script looks for `mise` only in `/usr/local/bin` and `/usr/bin`, so a copy of it ran with one more directory. Without mise, the script only skips this check.
- Not run to the end: `mise-agent-shims-migration-test.sh` and `agent-account-exec-test.sh`. They need a `mise` on their own test PATH. They do not use the zycode lines.

### E. Real mise 2026.11.8, in an isolated home
A local server took the place of GitHub. It served the real release list of `zy1233/pi-python` (11 releases, newest first) and fake archives with the real layout (two flat files).
- `latest` with `version_order = "semver"`: `1.0.0`. The same line without the option: `0.1.0`.
- The version list, in the order that mise sees it: `0.1.0 0.2.0 0.3.0 0.4.0 0.5.0 0.6.0 0.7.0 0.8.0 0.9.0 0.9.1 1.0.0`.
- Install: `http:zycode@1.0.0`. The install directory holds `zycode-agent` and `zypi`. `zypi` runs and finds `zycode-agent` next to it.
- `mise reshim --system` makes a `zypi` shim, and `command -v zypi` finds it (this is the question that `omarchy-cmd-missing` asks). The first run through the shim installs `1.0.0` and starts `zypi`. The second run installs nothing.

### F. Mutation checks (the new tests must fail on the old code)
- Old files with the new default-agent test: it fails at `user setup declares http:zycode with version_order inside its table`.
- `base-test.sh` without the `PI_HOME` clear: the instructions test fails at `zycode is linked in its home`, and links appear in the decoy `PI_HOME`.
- `omarchy-agent-instructions` without the `[zypi]` line: the instructions test fails at `zycode is linked in its home`.

### G. Not verified
- The real `v1.0.0` archive was not downloaded through mise. GitHub's asset download from the test machine ran at about 20 KB/s. The archive layout comes from `packaging/agent/build.py` (`pack_bundle`).
- The aarch64 archive, and the first use on a real Omarchy desktop (Hyprland, the menu in the live shell).
- A new release has its archives a few minutes after its tag (the agent-binaries workflow attaches them after the Release workflow). In that time `latest` can name a version that has no archive yet.

---

## 6. Real Omarchy system, rebase and upstream PR (2026-11-19)

### A. Machine and method

- A new Omarchy 4.5.0 install (Arch Linux, x86_64), real mise 2026.11.7, real GitHub. The network was slow (about 100 KB/s at first). A full clone of `omarchy` failed twice, so the test checkout was a sparse, blobless clone of the PR branch (no `themes/` wallpapers).
- The PR was read with the public GitHub API. `gh` was logged in later.

### B. Tests at commit `6b1fb25` (before the rebase)

- `default-agent-test.sh` 48 ok, 0 not ok. The other related files also have no `not ok`: `agent-instructions` 5, `preinstalls` 15, `mise-dev-env` 7, `remove-ai` 35, `agent-account` 32, `first-run` 5, `agent-invitation` 2, `crash-capture` 28, `mise-provision` 2 (with real mise).
- `i18n-test.sh`: 204 ok, 1 not ok (`every cli catalog key is referenced`, key `about.days`). The test stops at that point. The PR base commit gives the same result (the base was made by putting the 9 changed files back to their state before the PR). So the PR does not cause it. The first run on WSL had a full checkout and gave 401 ok, 0 not ok, so the sparse checkout can be the cause.
- **Real archive.** `mise install http:zycode` with the declaration of the PR (isolated mise directories) downloaded the real `zycode-linux-x86_64-1.0.0.tar.gz` (166 MB). `latest` is `1.0.0`. The install directory holds two files: `zypi` and `zycode-agent`. `zypi --version` prints `zypi 1.0.0 (037b162ea59a)`. `zypi --help` lists `--always-approve`.
- **Real system.** With the `bin/` of the PR first in `PATH`, and only the last `omarchy-agent` call stubbed (no terminal):
  - `omarchy-default-agent --install zycode` ran `mise use -g http:zycode[...]`, wrote `~/.config/mise/config.toml`, and saved `zypi` in `~/.config/omarchy/defaults/agent`.
  - After `mise reshim`, `zypi` is a shim, and the real `omarchy-cmd-missing zypi` says present. `omarchy-cmd-missing zycode` says missing, as designed.
  - `omarchy-agent --prompt ping` runs `zypi --always-approve -- ping` with `--app-id=org.omarchy.agent`.
  - `omarchy-agent-instructions` made `~/.pi-python/agent/AGENTS.md`, linked to the shared `AGENTS.md`.
- **A wrong conclusion, found and removed.** After the first run, `command -v zypi` found nothing, and it looked like a defect of the PR. The cause was the test itself: the install directory was copied from the earlier download without mise's `.mise.backend.toml`, so `mise reshim` did not know the tool. With the file, `mise reshim` made the shims for `zypi` and `zycode-agent`. The `mise use -g` path (a system whose `/etc/mise` has no `http:zycode` line) makes shims for both names. The declared path (`lazy_bins = ["zypi"]`) makes one.

### C. Rebase on `quattro` (`fc781d3`) and the fourth commit

- The 3 commits were rebased on `quattro` (172 upstream commits ahead) without conflicts. The compare API cuts its file list at 300 files, so it did not show which of the PR files had changed upstream; four of them had.
- After the rebase, `default-agent-test.sh` failed at `installing zypi adds a launcher that starts it`. Upstream now gives each agent a launcher (`bin/omarchy-agent-launcher`, `default/applications/omarchy-agent-<agent>.desktop`), and a test checks that its icon is in both icon sets (`icons/papercraft`, `icons/arcade`) and in `applications/icons`. (This check needs `icons/` and `applications/` in the checkout.)
- The fourth commit (`d296bd3`) adds:
  - `default/applications/omarchy-agent-zypi.desktop` (copied from the Pi entry: `Exec=omarchy-agent --agent zypi --app-id=org.omarchy.zypi`).
  - `icons/papercraft/omarchy-agent-zypi.svg.tpl`: the prompt and the cursor block of the zycode logo (`web/public/favicon.svg`) in the terminal window of the Pi icon, with a hard shadow.
  - `icons/arcade/omarchy-agent-zypi.svg.tpl`: the same in 8-bit pixels on the 16 by 16 grid (made by a small script from a pixel map).
  - `applications/icons/omarchy-agent-zypi.png`: the Papercraft template drawn in the default colors (Tokyo Night). The recipe was checked first: the Pi template drawn with it gives the Pi PNG with 0 different pixels.
  - A row in `manual/17-ai.md`. The translations are left to the upstream sync.
- The menu row keeps a Nerd Font glyph. A brand glyph in the Omarchy icon font needs a single-path monochrome SVG and a change to `omarchy.ttf`. It is offered in the PR text, not done.
- Tests after the rebase: `default-agent-test.sh` **62 ok, 0 not ok**. `theme-templates` 19 ok (it draws every Papercraft template, the new one too). `desktop-entries` 2, `app-icon-scan` 10, `agents-panel` 139, `dock` 48, `launcher-remove` 7, `agent-instructions` 5, `preinstalls` 15, `mise-dev-env` 7, `remove-ai` 35, `agent-account` 38, `first-run` 4, `agent-invitation` 2, `crash-capture` 28, `mise-provision` 2. No `not ok`.
- `i18n-test.sh`: 204 ok, 1 not ok (`about.days`), as before the rebase. It was not run again on the new base commit.
- `agent-look-test.sh` did not run: it needs the wallpapers of a theme, which the sparse checkout leaves out.

### D. Publication

- The branch needed the `workflow` scope of the `gh` token to be pushed (the new base has upstream workflow files). The push to the fork was a forced update (`--force-with-lease`, old head `6b1fb25`, kept as the local tag `backup/pre-rebase-6b1fb25`).
- The fork's `quattro` was synced with upstream, and the fork PR (`zy1233/omarchy#1`, now not a draft) was closed and opened again, so that GitHub counts its base again (it showed 1284 files with the old base). It shows 4 commits and 14 files now.
- The upstream PR body follows the upstream template (`What changes`, `Verification`, `Screenshots`). It shows the Papercraft PNG from the commit. The Arcade icon has no PNG in the repository, so it has no image.
- The real system was changed by the tests: `~/.config/mise/config.toml` (a `http:zycode` line; a backup is in `/tmp/mise-config.toml.bak`), the default agent in `~/.config/omarchy/defaults/agent` is `zypi`, and `omarchy-agent-instructions` made `AGENTS.md` links for the installed agents.

### E. Not verified

- The menu row and the launcher in the live shell (Hyprland). The installed 4.5.0 package has neither. A test needs `omarchy dev link` and a reboot.
- The aarch64 archive.
- `mise reshim --system` for the declared `lazy_bins`, and the first run through that lazy shim from a clean state (needs `sudo`).
- The upstream CI for `omacom/omarchy#19501`. Right after the PR was opened, `gh pr checks` listed one check only (`Greptile Review`, pending). The test workflows of `.github/workflows` were not listed yet.
