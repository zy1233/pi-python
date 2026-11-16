# Omarchy Linux: Add zycode (`zypi`) Coding Agent to Agent Menu

> Date: 2026-11-15  
> Status: **Draft. Do not submit yet.** Two defects are known and not fixed. (1) The mise declaration in section 3 makes `latest` resolve to the oldest release: the GitHub list of releases starts with the newest one, and mise takes the last item. (2) The agent id `zycode` is not the name of a command: the command is `zypi`. A review found both in the draft PR `zy1233/omarchy#1`.  
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
Adds `zycode` (`zypi`) to Omarchy's default coding agent menu and launcher.

`zycode` is a fast terminal coding agent featuring an ACP-driven Rust TUI (`zypi`) and standalone agent runtime (`zycode-agent`). Starting with `v1.0.0`, zycode publishes self-contained binary release archives containing both binaries for Linux x86_64 and aarch64.

### Changes
- **`etc/mise/conf.d/omarchy-tools.toml`**: Declares `http:zycode` with `lazy = true`, `auto_update = "4h"`, `lazy_bins = ["zypi"]`, GitHub release URLs for x86_64 and aarch64 (`zycode-linux-<arch>-{{version}}.tar.gz`), and version resolution via GitHub Releases API (`.[].tag_name`).
- **`bin/omarchy-default-agent`**: Registers `zycode` / `zypi` with the `http:zycode` declaration; updates usage text and arg metadata.
- **`bin/omarchy-agent`**: Adds launcher entry running `zypi --always-approve [-- "$prompt"]`.
- **`bin/omarchy-agent-instructions`**: Links agent instructions to `${PI_HOME:-$HOME/.pi-python}/agent/AGENTS.md`.
- **`default/omarchy/omarchy-menu.jsonc`**: Adds `agent.harness.zycode` entry with agent icon `󰚩`.
- **`default/i18n/en/menu.json`**: Adds English i18n label for `agent.harness.zycode`.
- **`test/shell.d/default-agent-test.sh`**: Updates expected tool declarations, lazy counts (31 -> 32), self-updating CLI counts (21 -> 22), install expectations, prompt launch, and permission bypass assertions.

### Verification
- `bash test/shell.d/default-agent-test.sh`: 47 tests passed (0 failures).
- `bash test/shell.d/i18n-test.sh`: 123 tests passed (0 failures).
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
"http:zycode" = { version = "latest", lazy = true, auto_update = "4h", lazy_bins = ["zypi"], minimum_release_age = "0s", platforms_linux_x64_url = "https://github.com/zy1233/pi-python/releases/download/v{{version}}/zycode-linux-x86_64-{{version}}.tar.gz", platforms_linux_arm64_url = "https://github.com/zy1233/pi-python/releases/download/v{{version}}/zycode-linux-aarch64-{{version}}.tar.gz", version_list_url = "https://api.github.com/repos/zy1233/pi-python/releases", version_json_path = ".[].tag_name" }
```

Key configuration points:
- `lazy = true`: Generates a lazy shim wrapper under `~/.local/share/omarchy/bin/zypi` so invoking `zypi` or launching from the menu triggers installation on first run.
- `lazy_bins = ["zypi"]`: Only `zypi` is exposed on `PATH`. `zycode-agent` remains adjacent in the mise install directory and is invoked as a child process by `zypi` via sibling binary resolution.
- `auto_update = "4h"`: Updates check every 4 hours alongside other self-updating agent CLIs.
- `version_list_url` and `version_json_path`: Queries `https://api.github.com/repos/zy1233/pi-python/releases` extracting `.tag_name`. Mise automatically strips `v` prefixes to match semver versions.

---

## 4. Full Git Diff Patch against `omacom/omarchy` (`quattro`)

```patch
diff --git a/bin/omarchy-agent b/bin/omarchy-agent
index a249529..85cfa8e 100755
--- a/bin/omarchy-agent
+++ b/bin/omarchy-agent
@@ -277,6 +277,10 @@ commandcode)
   command=(commandcode --yolo)
   [[ -n ${prompt:-} ]] && command+=(-- "$prompt")
   ;;
+zycode)
+  command=(zypi --always-approve)
+  [[ -n ${prompt:-} ]] && command+=(-- "$prompt")
+  ;;
 *)
   echo "Unsupported default agent: $agent" >&2
   exit 1
diff --git a/bin/omarchy-agent-instructions b/bin/omarchy-agent-instructions
index 21f769d..1935306 100755
--- a/bin/omarchy-agent-instructions
+++ b/bin/omarchy-agent-instructions
@@ -51,6 +51,7 @@ declare -A links=(
   [kilo]="$config/kilo/AGENTS.md"
   [commandcode]="$HOME/.commandcode/AGENTS.md"
   [freebuff]="$HOME/.AGENTS.md"
+  [zycode]="${PI_HOME:-$HOME/.pi-python}/agent/AGENTS.md"
 )
 
 backups=()
diff --git a/bin/omarchy-default-agent b/bin/omarchy-default-agent
index cee3810..ffadd59 100755
--- a/bin/omarchy-default-agent
+++ b/bin/omarchy-default-agent
@@ -1,7 +1,7 @@
 #!/bin/bash
 
 # omarchy:summary=Set and launch the default coding agent
-# omarchy:args=[pi|omp|opencode|ori|claude|codex|grok|openclaw|agy|hermes|copilot|crush|cursor-agent|muse|kimi|qwen|dsh|mcode|kiro-cli|freebuff|kilo|devin|amp|commandcode]
+# omarchy:args=[pi|omp|opencode|ori|claude|codex|grok|openclaw|agy|hermes|copilot|crush|cursor-agent|muse|kimi|qwen|dsh|mcode|kiro-cli|freebuff|kilo|devin|amp|commandcode|zycode]
 # omarchy:examples=omarchy default agent | omarchy default agent codex | omarchy default agent claude
 
 installing=false
@@ -64,8 +64,14 @@ devin | devin-cli)
   ;;
 amp | ampcode) agent="amp"; name="Amp" ;;
 commandcode | command-code | cmdc) agent="commandcode"; name="Command Code"; agent_package="npm:command-code" ;;
+zycode | zypi)
+  agent="zycode"; name="zycode"
+  # zycode publishes per-platform archives containing zypi and zycode-agent.
+  agent_tool="http:zycode"
+  agent_package="$agent_tool[platforms_linux_x64_url=https://github.com/zy1233/pi-python/releases/download/v{{version}}/zycode-linux-x86_64-{{version}}.tar.gz,platforms_linux_arm64_url=https://github.com/zy1233/pi-python/releases/download/v{{version}}/zycode-linux-aarch64-{{version}}.tar.gz,version_list_url=https://api.github.com/repos/zy1233/pi-python/releases,version_json_path=.[].tag_name]"
+  ;;
 *)
-  echo "Usage: omarchy-default-agent <pi|omp|opencode|ori|claude|codex|grok|openclaw|agy|hermes|copilot|crush|cursor-agent|muse|kimi|qwen|dsh|mcode|kiro-cli|freebuff|kilo|devin|amp|commandcode>"
+  echo "Usage: omarchy-default-agent <pi|omp|opencode|ori|claude|codex|grok|openclaw|agy|hermes|copilot|crush|cursor-agent|muse|kimi|qwen|dsh|mcode|kiro-cli|freebuff|kilo|devin|amp|commandcode|zycode>"
   exit 1
   ;;
 esac
diff --git a/default/i18n/en/menu.json b/default/i18n/en/menu.json
index 1221efa..522b296 100644
--- a/default/i18n/en/menu.json
+++ b/default/i18n/en/menu.json
@@ -575,6 +575,9 @@
   "agent.harness.qwen": {
     "label": "Qwen Code"
   },
+  "agent.harness.zycode": {
+    "label": "zycode"
+  },
   "setup.default.browser": {
     "label": "Browser",
     "title": "Default Browser"
diff --git a/default/omarchy/omarchy-menu.jsonc b/default/omarchy/omarchy-menu.jsonc
index cbf2662..49a4be0 100644
--- a/default/omarchy/omarchy-menu.jsonc
+++ b/default/omarchy/omarchy-menu.jsonc
@@ -63,6 +63,7 @@
   "agent.harness.ori": {"icon":"","iconFont":"omarchy","label":"Ori","checked":"[[ \"$(omarchy-default-agent)\" == \"ori\" ]]","action":"omarchy-default-agent ori"},
   "agent.harness.pi": {"icon":"","iconFont":"omarchy","label":"Pi","checked":"[[ \"$(omarchy-default-agent)\" == \"pi\" ]]","action":"omarchy-default-agent pi"},
   "agent.harness.qwen": {"icon":"","iconFont":"omarchy","label":"Qwen Code","checked":"[[ \"$(omarchy-default-agent)\" == \"qwen\" ]]","action":"omarchy-default-agent qwen"},
+  "agent.harness.zycode": {"icon":"󰚩","label":"zycode","checked":"[[ \"$(omarchy-default-agent)\" == \"zycode\" ]]","action":"omarchy-default-agent zycode"},
   "agent.instructions": {"icon":"󰈙","label":"Instructions","action":"omarchy-agent-instructions --edit"},
 
   // System
diff --git a/etc/mise/conf.d/omarchy-tools.toml b/etc/mise/conf.d/omarchy-tools.toml
index b784d71..06ebd5b 100644
--- a/etc/mise/conf.d/omarchy-tools.toml
+++ b/etc/mise/conf.d/omarchy-tools.toml
@@ -23,6 +23,7 @@ grok = { version = "latest", lazy = true, auto_update = "4h", minimum_release_ag
 hey-cli = { version = "latest", lazy = true, minimum_release_age = "0s" }
 "http:devin" = { version = "latest", lazy = true, auto_update = "4h", lazy_bins = ["devin"], minimum_release_age = "0s", platforms_linux_x64_url = "https://static.devin.ai/cli/{{ version }}/devin-{{ version }}-x86_64-unknown-linux.tar.gz", platforms_linux_arm64_url = "https://static.devin.ai/cli/{{ version }}/devin-{{ version }}-aarch64-unknown-linux.tar.gz", bin_path = "bin", version_list_url = "https://static.devin.ai/cli/current/manifest.json", version_json_path = ".version" }
 "http:kiro-cli" = { version = "latest", lazy = true, auto_update = "4h", lazy_bins = ["kiro-cli"], minimum_release_age = "0s", platforms_linux_x64_url = "https://prod.download.cli.kiro.dev/stable/{{version}}/kirocli-x86_64-linux.tar.gz", platforms_linux_arm64_url = "https://prod.download.cli.kiro.dev/stable/{{version}}/kirocli-aarch64-linux.tar.gz", version_list_url = "https://prod.download.cli.kiro.dev/stable/latest/manifest.json", version_json_path = ".version" }
+"http:zycode" = { version = "latest", lazy = true, auto_update = "4h", lazy_bins = ["zypi"], minimum_release_age = "0s", platforms_linux_x64_url = "https://github.com/zy1233/pi-python/releases/download/v{{version}}/zycode-linux-x86_64-{{version}}.tar.gz", platforms_linux_arm64_url = "https://github.com/zy1233/pi-python/releases/download/v{{version}}/zycode-linux-aarch64-{{version}}.tar.gz", version_list_url = "https://api.github.com/repos/zy1233/pi-python/releases", version_json_path = ".[].tag_name" }
 hunk = { version = "latest", lazy = true, minimum_release_age = "0s" }
 muse = { version = "latest", lazy = true, auto_update = "4h", minimum_release_age = "0s" }
 node = { version = "latest", lazy = true, minimum_release_age = "0s" }
diff --git a/test/shell.d/default-agent-test.sh b/test/shell.d/default-agent-test.sh
index 57b877f..c94efc3 100644
--- a/test/shell.d/default-agent-test.sh
+++ b/test/shell.d/default-agent-test.sh
@@ -168,6 +168,8 @@ amp_package="amp"
 commandcode_package="npm:command-code"
 cursor_agent_package="cursor-agent"
 muse_package="muse"
+zycode_tool="http:zycode"
+zycode_package="$zycode_tool[platforms_linux_x64_url=https://github.com/zy1233/pi-python/releases/download/v{{version}}/zycode-linux-x86_64-{{version}}.tar.gz,platforms_linux_arm64_url=https://github.com/zy1233/pi-python/releases/download/v{{version}}/zycode-linux-aarch64-{{version}}.tar.gz,version_list_url=https://api.github.com/repos/zy1233/pi-python/releases,version_json_path=.[].tag_name]"
 
 assert_lazy_stub() {
   local package=$1
@@ -229,6 +231,7 @@ declare -A declared_option_bins=(
   [mcode]='"mcode"'
   [kiro_cli]='"kiro-cli"'
   [devin]='"devin"'
+  [zycode]='"zypi"'
 )
 for agent in "${!declared_option_bins[@]}"; do
   tool_var="${agent}_tool"
@@ -242,12 +245,12 @@ for agent in "${!declared_option_bins[@]}"; do
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
@@ -424,6 +427,8 @@ declare -A expected_agents=(
   [muse]="muse"
   [muse-code]="muse"
   [musecode]="muse"
+  [zycode]="zycode"
+  [zypi]="zycode"
 )
 
 declare -A expected_packages=(
@@ -449,6 +454,7 @@ declare -A expected_packages=(
   [commandcode]="$commandcode_package"
   [cursor-agent]="$cursor_agent_package"
   [muse]="$muse_package"
+  [zycode]="$zycode_package"
 )
 
 for selection in "${!expected_agents[@]}"; do
@@ -707,7 +713,7 @@ mapfile -d '' -t agent_open_args <"$agent_open_log"
 pass "Muse installs visibly through mise and opens directly"
 
 saved_agent=$(<"$agent_file")
-for agent in qwen dsh mcode kiro-cli freebuff kilo devin amp commandcode; do
+for agent in qwen dsh mcode kiro-cli freebuff kilo devin amp commandcode zycode; do
   tool_var="${agent//-/_}_tool"
   package_var="${agent//-/_}_package"
   declared_tool=${!tool_var:-${!package_var}}
@@ -715,7 +721,7 @@ for agent in qwen dsh mcode kiro-cli freebuff kilo devin amp commandcode; do
   omarchy-default-agent --install "$agent" >/dev/null
   # npm-backed agents bring node along, which a fresh machine doesn't have yet.
   case "$agent" in
-  kiro-cli | devin) expected_install="install $declared_tool" ;;
+  kiro-cli | devin | zycode) expected_install="install $declared_tool" ;;
   *) expected_install="install node $declared_tool" ;;
   esac
   grep -Fx "$expected_install" "$mise_history" >/dev/null || fail "visible $agent installation installs Omarchy's declaration (expected: $expected_install)"
@@ -842,6 +848,7 @@ assert_launch kilo kilo --auto "--prompt=Review this project"
 assert_launch devin devin --permission-mode dangerous --respect-workspace-trust false -- "Review this project"
 assert_launch amp bash -c 'printf "%s\n" "$1" | exec "$0" --dangerously-allow-all' amp "Review this project"
 assert_launch commandcode commandcode --yolo -- "Review this project"
+assert_launch zycode zypi --always-approve -- "Review this project"
 [[ $(<"$clipboard_log") == "Review this project" ]] || fail "Freebuff launcher puts the prompt on the clipboard"
 mapfile -d '' -t notification_args <"$notification_history"
 [[ ${notification_args[*]} == *"Freebuff takes no starting prompt"* ]] || fail "Freebuff launcher says where the prompt went"
@@ -885,6 +892,7 @@ assert_bypass kilo kilo --auto
 assert_bypass devin devin --permission-mode dangerous --respect-workspace-trust false
 assert_bypass amp amp --dangerously-allow-all
 assert_bypass commandcode commandcode --yolo
+assert_bypass zycode zypi --always-approve
 [[ ! -s $clipboard_log && ! -s $notification_history ]] || fail "Freebuff launcher leaves the clipboard alone without a prompt"
 pass "agent launcher skips permission prompts for every supported agent"
```

---

## 5. Test Evidence

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
# 47/47 tests passed (0 failures)
```

### B. Omarchy Menu & i18n Test (`test/shell.d/i18n-test.sh`)
```bash
$ bash test/shell.d/i18n-test.sh
# 123/123 tests passed (0 failures)
```
- Validated that `agent.harness.zycode` in `default/omarchy/omarchy-menu.jsonc` matches the catalog entry in `default/i18n/en/menu.json`.
- Validated that no duplicate or orphan keys exist in menu configurations.
