# Releasing the zycode extension

This file is the checklist for a release. How the files are made is in `PACKAGING.md`. The design is in `docs/specs/2026-11-04-vscode-extension-design.md`.

## Release record

### 1.10.0 (2026-11-25)

- The pull request is #148 (merge commit `99ef1db`). It made the version 1.10.0 and moved the bundled agent from `1.0.1` to `1.1.0` (the Release `v1.1.0`). It added `pi-codemode-py==1.1.0` to `pythonPackages`. The permission card now shows the built-in `workflow` name and arguments, and the `codemode` code. The pull request also changed the CHANGELOG, the README and the docs. 1.10.0 follows 1.8.0 and is a stable release (even minor, no pre-release flag).
- Before the publish: the pull request had a review and a re-review. The review asked for changes (one blocking finding and five other findings). The re-review approved with one wording finding left. The commit `fda18b4` fixed that wording finding. Every finding was fixed before the merge. The 14 check runs on `99ef1db` were all green: 4 of `ci.yml` (Ruff, pytest on Python 3.11, 3.12 and 3.13) and 10 of the publish run below (with its `Publish` job). No separate dry run was started. The `Publish` job needs the jobs `package` and `smoke`, so both ran before the approval. `package` built the five VSIX files and compared each pinned agent file with `agent-assets.json` by size and sha256. `smoke` started the linux-x64 agent from its VSIX.
- Published by the workflow: run `45534530689` on `main`, commit `99ef1db` (inputs: `publish` on, `registries` `both`, the five targets). First attempt, no rerun. Created `2026-11-25T02:39:37Z`. Finished green at `02:47:23Z`. The user `zy1233` approved it in the environment `vscode-publish` (the API shows `approved`). The Publish job `138939591287` ran from `02:45:26Z` to `02:47:22Z`.
- Open VSX took the five VSIX files by Trusted Publishing (OIDC), from `02:46:14Z` to `02:46:41Z`. Then the Marketplace took them with `vsce publish --oidc`, from `02:46:41Z` to `02:47:20Z` (the last upload, `win32-x64`, at `02:47:20Z`). Both registries accepted the first try with no token.
- Time until the version was visible: Open VSX showed all five targets at the first check (`02:51:16Z`, 4 minutes after the last upload). The Marketplace showed no 1.10.0 target at `02:52:46Z` and still listed 1.8.0 as newest. It listed all five targets at the next check (`02:53:46Z`). So the time from the last upload until all five were visible was more than 5 minutes 26 seconds and at most 6 minutes 26 seconds.
- The `lastUpdated` times on the Marketplace, read at `02:57Z`: `darwin-arm64` `02:52:47Z`, `darwin-x64` `02:52:51Z`, `win32-x64` `02:53:54Z`, `linux-x64` `02:53:58Z`, `linux-arm64` `02:54:19Z`. At `02:53:46Z`, `linux-arm64`, `linux-x64` and `win32-x64` still had the times of the upload (`02:47:03Z`, `02:47:12Z`, `02:47:19Z`). These three times changed after the first listing. So `lastUpdated` is not the time of the upload on the Marketplace. The Open VSX `timestamp` of each target is the upload time: `darwin-arm64` `02:46:19Z`, `darwin-x64` `02:46:24Z`, `linux-arm64` `02:46:30Z`, `linux-x64` `02:46:36Z`, `win32-x64` `02:46:41Z`.
- Checked with the public APIs (no token): five targets at 1.10.0 on both registries. On Open VSX `preRelease` is false, `engines.vscode` is `^1.90.0` and `verified` is true. The Marketplace has no PreRelease property for 1.10.0, and its engine is `^1.90.0`. The sha256 of the five Open VSX files equals the `sha256sum` line of the Publish log (darwin-arm64 `d948e5c1…`, darwin-x64 `fbfa3798…`, linux-arm64 `b9841351…`, linux-x64 `757abb2c…`, win32-x64 `cb59750f…`). The Marketplace shows no hash, so it was not compared there.
- Not verified: a real VS Code window (the card, the scroll and the new UI of #144 and #145 have unit tests, mock tests and headless Chrome tests only). A real model provider (all model traffic in the tests is a fake OpenAI-format server). The Windows and macOS binaries of agent 1.1.0, and the macOS sandbox limits for `codemode`. The `/codemode` command in the `/` menu of the extension (read in code, not run). A `codemode` script that the user allows (the tests reject the calls). How VS Code draws a very long script in the dialog of a hidden chat view. An install or an update to 1.10.0 on a machine.

### 1.8.0 (2026-11-20)

- The pull requests are #139 (merge commit `d94816e`, `@` context mentions and `/` slash command completion), #140 (merge commit `49d38a9`, optimization plan and P0 reliability/UX fixes), and #143 (merge commit `72fcda4`, version `1.8.0`, bundled agent moved from `1.0.0` to `1.0.1` from Release `v1.0.1`, CHANGELOG and README updates). 1.8.0 follows 1.6.0 and is a stable release (even minor, no pre-release flag).
- Before the publish: the checks of all three pull requests were green (`vscode-extension.yml`, including `npm run check`, `linux-x64` VSIX build, `check-vsix.mjs`, and `smoke-vsix.mjs` starting the bundled `1.0.1` agent). #139 and #140 were reviewed and re-reviewed before merge, with blocking findings fixed on branch.
- Published by the workflow: run `44754977146` on `main`, commit `72fcda4` (created `2026-11-20T14:35:48Z`), which completed over three attempts after `zy1233` approval in `vscode-publish`:
  - Attempt 1: `Type check, lint, unit tests` failed on the `mention-excludes` timing test (`1115 ms`, limit `1000 ms`).
  - Attempt 2: Open VSX accepted all five platform VSIX files (`darwin-arm64`, `darwin-x64`, `linux-arm64`, `linux-x64`, `win32-x64`) and the Marketplace accepted three (`darwin-arm64`, `darwin-x64`, `linux-arm64`), then the Marketplace OIDC token exchange failed with HTTP 503.
  - Attempt 3 (Publish job `136363604587`, `14:56:30Z` to `14:58:09Z`): published `linux-x64` and `win32-x64` to the Marketplace and skipped the already-published files via `--skip-duplicate` with OIDC Trusted Publishing and no token.
- The `sha256sum` values from the Publish log (identical across all attempts because every attempt reused the same five VSIX artifacts): `darwin-arm64` `f31c1efd21cec8614f9a5923731d8415e5ff25d2230e3dea59dcf8a9d1e036dd`, `darwin-x64` `a3f17e35c6cddafcc118ecbaf94d0fedb2035f81dfa5c1c5f8c7f7f50f06a29b`, `linux-arm64` `fbf6ecd6c49755c45f1a2bf2e072d3910bb703982c1221cc41bcda9d29e07824`, `linux-x64` `04b0485e32c1af4bbbc7ea4db6b8ebb303cc03d364c49321fd94d72557d3dd9a`, `win32-x64` `2040a84455c26ce9fa0f5808649529f91dea27f54a83ebd3c0d05fbe1d674dce`.
- Not verified: live VS Code window testing of the 1.8.0 UI additions (`PermissionFallback` modal `showWarningMessage`, `StatusBar` approval warning, `WorkspaceMentions` live `FileSystemWatcher`, `retainContextWhenHidden` on real sidebar tab switches), and live Google Gemini / Vertex AI API calls for `thinking_format = "gemini"` in bundled agent `1.0.1`.

### 1.6.0 (2026-11-19)

- The pull requests are #135 (merge commit `7dd6ee8`, chat styling and the copy buttons) and #136 (merge commit `25b1e3e`, the version, the CHANGELOG entry, the 1.2.0 release record). 1.6.0 follows 1.4.0 and bundles the same agent (1.0.0). It is a stable release (even minor, no pre-release flag). The owner chose the version.
- Before the publish: the checks of both pull requests were green (the workflow of this file; `ci.yml` does not run for a change that has no `.py` file). #135 was reviewed in full before the merge. A review found one small defect (the text of a tool output ran under its **Copy** button), and one commit on the branch fixed it (padding of `.tool-output pre`).
- Published by the workflow: run `44445177041` on `main`, commit `25b1e3e` (inputs: `publish` on, `registries` `both`, the five targets). The run finished green at 02:45:44Z (the Publish job ran from 02:43:20Z to 02:45:44Z). The user `zy1233` approved it in the environment `vscode-publish` (the API shows `approved`).
- Open VSX took the five VSIX files by Trusted Publishing (OIDC), from 02:44:21Z to 02:44:40Z (`Trusted publishing token issued`, then `Published zycode.zycode v1.6.0@<target>`). Then the Marketplace took them with `vsce publish --oidc`, from 02:44:49Z to 02:45:42Z. Both registries accepted the first try with no token.
- Time until the version was visible: Open VSX showed all five targets at the first check (02:50Z, 5 minutes after the last upload). The Marketplace listed the five targets from 02:51:56Z to 02:53:22Z (`lastUpdated`), about 7 to 8 minutes after the upload.
- Checked with the public APIs: five targets at 1.6.0 on both registries. On Open VSX `preRelease` is false, `engines.vscode` is `^1.90.0` and `verified` is true. The Marketplace has no PreRelease property for 1.6.0. The sha256 of the five Open VSX files equals the `sha256sum` line of the Publish log (darwin-arm64 `69b3e7d9…`, darwin-x64 `b557eea7…`, linux-arm64 `9bce6e5f…`, linux-x64 `1206d58f…`, win32-x64 `48224590…`). The Marketplace shows no hash, so it was not compared there.
- Not verified: the chat in a real VS Code. The review ran the chat in headless Chrome 156 (dark and light theme, 320 px and 800 px wide) with a mock of the VS Code API. It did not use a VS Code window and not the Chromium of the oldest supported VS Code (about 122). In a real VS Code webview, the copy through `navigator.clipboard` (with the `document.execCommand("copy")` fallback), the **Copy failed** path, and the screen-reader announcement were not tried. A VS Code older than Chromium 123 does not know `field-sizing`, so its message box keeps three lines (by design, and not tried). An install or an update to 1.6.0 on a machine was not tried.

### 1.4.0 (2026-11-16)

- The pull request is #132 (merge commit `9c24066`). It moved the pin to agent 1.0.0 (the Release `v1.0.0`) and made the version 1.4.0. 1.4.0 follows 1.2.0.
- Before the publish: the checks of the pull request were green (the workflow of this file, and `ci.yml` with Ruff and pytest on Python 3.11, 3.12 and 3.13). A dry run of this workflow on the branch (run `43895374999`, `publish` off) built the five VSIX files and passed the start test of the linux-x64 agent. Each build downloads the pinned agent file and compares the size and the sha256 with `agent-assets.json`. So the five pins are proven against the real files of the Release.
- Published by the workflow: run `43896837977` on `main`, commit `9c24066`. The run finished green at 15:37Z. The user `zy1233` approved it in the environment `vscode-publish` (the API shows `approved`), and the Publish job started at 15:35:04Z.
- Open VSX took the five VSIX files by Trusted Publishing (OIDC). It shows the files from 15:35:56Z to 15:36:12Z (the time of each target). Then the Marketplace took them with `vsce publish --oidc`. It shows them from 15:42Z to 15:43Z (`lastUpdated` of each target). Both registries accepted the first try with no token.
- Checked with the public APIs: five targets at 1.4.0 on both registries. `preRelease` is false on Open VSX. The Marketplace has no PreRelease property for 1.4.0 (1.2.0 has none, and 0.1.0 has it). The engine of 1.4.0 is `^1.90.0`.
- Tested by the project owner on 2026-11-17, in a real VS Code on Windows, with extension 1.4.0, agent 1.0.0 and a real model (an OpenAI-format gateway). His words, translated from Chinese: "I tested in a real VS Code. `max` is already usable. But I did not see the Context row." Then, after he added `context_window` to `agent.toml`: "It shows normally now." The row was hidden at first because `[model]` had no `context_window`. The agent sends `usage_update` only for a model that has this key (`docs/specs/2026-11-13-agent-context-usage.md`). The README and the CHANGELOG of the extension say this, but the chat view says nothing when the key is missing.
- VS Code on his machine records the install (read on 2026-11-17 from `extensions.json` of the VS Code profile and from the user settings): `zycode.zycode` 1.4.0, target `win32-x64`, source `gallery`, `updated` true, installed at 09:48 Asia/Shanghai (01:48Z). The folder of 1.2.0 is still on disk. The user settings have no `zycode.agentPath`. So 1.4.0 came from the Marketplace as an update from 1.2.0, and the agent was probably the bundled one (the workspace settings were not read).
- Not verified: the numbers of the Context row against the count of the provider (he did not say that he compared them). What the gateway does with `reasoning_effort = "max"`. A first install of 1.4.0 on a machine that had no earlier version. The first-run setup in a terminal, all macOS flows, Linux in a real VS Code, and the unsigned agent on other machines or under an antivirus.

### 1.2.0 (2026-11-13)

This record was added later, with the release PR of 1.6.0. The numbers come from the notes of the project at the time of the release.

- The pull request is #128 (merge commit `eedee46`, "release 1.2.0 and cap the nesting of inline marks"). 1.2.0 follows 1.0.0 and bundles the same agent (0.9.1).
- Published by the workflow: run `43385299617` on `main`. The run finished green at 07:28:36Z (10 jobs). The user `zy1233` approved `Publish` in the environment `vscode-publish` at about 15:26 Asia/Shanghai, and the job started at 07:26:41Z. The Publish job took 1 min 55 s: `npm ci` 40 s, Open VSX 07:27:29Z to 07:27:48Z, Marketplace 07:27:48Z to 07:28:34Z.
- Both registries took the five VSIX files by OIDC on the first try, with no token (`Published zycode.zycode v1.2.0@<target>` on Open VSX, `Published zycode.zycode (<target>) v1.2.0.` on the Marketplace).
- Time until the version was visible: Open VSX showed 1.0.0 as the latest at 07:29:52Z and 1.2.0 for the five targets at 07:32:52Z (2 to 5 minutes after the last upload). The Marketplace listed 1.0.0 only at 07:32:52Z and all five 1.2.0 targets at 07:36:02Z (at most 7.5 minutes after the last upload).
- Checked with the public APIs: five targets at 1.2.0 on both registries. On Open VSX `preRelease` is false, `engines.vscode` is `^1.90.0` and `verified` is true. The `.sha256` file of each Open VSX VSIX equals the `sha256sum` line of the Publish log (darwin-arm64 `c1b61222…`, darwin-x64 `59f935b5…`, linux-arm64 `efe497ca…`, linux-x64 `e531cc67…`, win32-x64 `695ccab1…`). The Marketplace shows no hash, so it was not compared there. The 16 check runs of `eedee46` all passed (Ruff, pytest on Python 3.11 to 3.13, the site check and build, the extension run, and the zycode.dev deploy).
- Not verified: an install of 1.2.0 in a real VS Code (the owner's Windows test of 2026-11-12 covered 1.0.0 only), and the update from 1.0.0 to 1.2.0 in practice. The owner's later test of 1.4.0 (see above) came as an update from 1.2.0, so 1.2.0 was on his machine, but he did not test it by itself.

### 1.0.0 (2026-11-12)

- Published by the workflow `.github/workflows/vscode-extension.yml`: run `43181052994` on `main`, commit `8b0e50c`. The run finished green at 08:20Z.
- The user approved the run in the environment `vscode-publish` at 08:18Z.
- Open VSX took the five VSIX files by Trusted Publishing (OIDC), from 08:18:55Z to 08:19:17Z. Then the Marketplace took them with `vsce publish --oidc`, from 08:19:24Z to 08:19:57Z. Both registries accepted the first try with no token. So the OIDC path and both trusted publisher registrations are proven.
- Time until the version was visible: Open VSX 08:23Z to 08:25Z (target by target, the scan queue). Marketplace 08:26Z to 08:28Z (validation, about 7 to 9 minutes after the upload).
- Checked with the public APIs: five targets at 1.0.0 on both registries. `preRelease` is false on Open VSX. The Marketplace has no PreRelease property for 1.0.0 (0.1.0 had it). The sha256 of the five Open VSX files equals the `sha256sum` in the Publish log (the CI artifacts).
- 2026-11-12, 18:17 Asia/Shanghai: the project owner tested, on Windows, the install of 1.0.0 and the update from 0.1.0 to 1.0.0. His words: "I tested the install of 1.0 and the update from 0.1 on Windows." Nothing else is claimed from this test.
- Not verified: the rest of the Windows flow (the first-run setup in a terminal, a chat with a model, the unsigned agent on other machines or under an antivirus), all macOS flows, and Linux in a real VS Code.

## Where things stand before the 1.0.0 run (read-only check on 2026-11-12, kept as history)

- Open VSX and the Marketplace each hold `zycode.zycode` **0.1.0**, a pre-release, for five targets: `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64` and `win32-x64`. Nothing else is published.
- 0.1.0 was uploaded by hand. **No release has gone out from the workflow yet.** So `1.0.0` is very likely the first real Trusted Publishing (OIDC) publish. Treat the OIDC path as untried until the run is green.
  - Result (2026-11-12): the 1.0.0 run was green. Both registries took the publish by OIDC on the first try. The OIDC path is proven. See "Release record".
- The Open VSX trusted publisher for `zycode` **is registered**: the owner said so on 2026-11-12. No one else has checked the registration, and no run of the workflow has used it, so the first publish is the first proof that the values match. A rehearsal run is not known.
  - Result (2026-11-12): the 1.0.0 run used the registration: Open VSX logged `Trusted publishing token issued`, then published the five files. The registration is proven.

## What is already set up

- GitHub environment `vscode-publish`: required reviewer `zy1233` (self-review is allowed), deployments from `main` only, no secret.
- Marketplace publisher `zycode`, with a Trusted Publishing policy `zycode release`: repository `zy1233/pi-python`, workflow `vscode-extension.yml`, environment `vscode-publish`.
- Open VSX namespace `zycode` (you are the owner). Its trusted publisher for the extension `zycode` is registered (the owner said so on 2026-11-12; compare the values in "Before the run").
- The workflow file `.github/workflows/vscode-extension.yml`. Its name must stay as it is: both registries name it. It publishes from `main` only, in the environment `vscode-publish`, with OIDC, and with no token.

## Stable and pre-release (how the registries treat them)

- A release and a pre-release are two lines of the same extension. The version decides which one a user gets. The Marketplace rule: use **even minor numbers for releases** (`1.0.0`, `1.2.0`) and **odd minor numbers for pre-releases** (`1.1.0`, `1.1.1`). A user on the pre-release channel gets the highest version of both lines. A user on the release channel gets the highest release.
- `1.0.0` is above `0.1.0`, so the pre-release `0.1.0` updates to `1.0.0`. `1.0.0` has no pre-release flag: the VSIX is made without `--pre-release`, the workflow calls `vsce publish` without it, and `scripts/check-vsix.mjs` fails a VSIX that has the flag.
- The versions in use: `1.0.0`, `1.2.0`, `1.4.0`, `1.6.0` and `1.8.0` (releases), and `0.1.0` (pre-release). After `1.8.0`, the next pre-release line is `1.9.x`, and the next stable release is `1.10.0` (or `1.8.1` for a fix). A pre-release needs a new input in the workflow and the flag in `scripts/package.mjs` first. Do that in its own pull request.
- A version cannot be published twice or changed. If a published version is wrong, publish a higher one.

## Before the run

1. Merge the pull request of the release to `main`. Work from `main` after that.
2. Check the Open VSX trusted publisher: <https://open-vsx.org/user-settings/trusted-publishers>. It is registered (the owner said so on 2026-11-12). Compare it once with these values: namespace `zycode`, extension `zycode`, provider GitHub Actions. If a value is wrong, or it is not there, add it. A registration cannot be edited: delete it and make a new one.

   | Field | Value |
   |---|---|
   | Organization or User name | `zy1233` |
   | Repository name | `pi-python` |
   | Workflow filename | `vscode-extension.yml` |
   | Environment name | `vscode-publish` |

3. Check the Marketplace policy: <https://marketplace.visualstudio.com/manage>, publisher `zycode`, Trusted Publishing. It must show the same repository, workflow and environment.
4. Make a dry run that publishes nothing. Actions > "VS Code extension" > Run workflow > branch `main`, `publish` **not ticked**. The run builds the five VSIX, checks them, and starts the agent of the Linux file with a smoke test. Download the artifacts `vsix-<target>` if you want to install one before the release.

## Publish a release (1.0.0 and later)

1. Change `version` in `package.json` (an even minor for a release) and add `## <version>` to `CHANGELOG.md`. A test fails if the entry is missing. Merge to `main`.
2. Run the workflow on `main`: `publish` ticked, `registries` = `both`, all targets. Approve it in the environment `vscode-publish`. Open VSX goes first, then the Marketplace.
3. Check the result:
   - Open VSX: `zycode.zycode` shows the new version for five platforms, and no pre-release label.
   - Marketplace: the page shows the new version for five platforms, and no pre-release label. A new version can take some minutes to pass the checks of the Marketplace.
   - On a machine: `code --install-extension zycode.zycode` installs the new version, and the status bar item starts the agent.
4. If one registry fails and the other did not, run the workflow again for the one that failed (`registries`). `--skip-duplicate` makes a repeat safe.

## If the OIDC publish fails (fallback)

The first publish from the workflow (1.0.0, 2026-11-12) was the first test of OIDC on both registries, and it worked on the first try. This section stays for a later run that fails. Read the log of the failed step first.

- **Open VSX says "No trusted publisher matches the presented token".** The registration does not match. Compare the four values in the table above with the repository, the workflow file name and the environment, then run the failed registry again.
- **The Marketplace refuses the OIDC token.** Compare the policy of the publisher with the same four values. Check that the run was started from `main` and approved in `vscode-publish`.
- **The log shows something else, or a second run fails the same way.** Publish by hand from the same files. A hand upload is the way that `0.1.0` took, and it needs no token:
  1. Run the workflow on `main` with `publish` **not ticked**. Download and unzip the five artifacts `vsix-<target>` (or run `npm ci && npm run package:all` on Linux or WSL, on the Linux file system). The files are named `zycode-<target>-<version>.vsix`.
  2. Open VSX: log in at <https://open-vsx.org>, open the Publish page, and drop the five files on it. If the page refuses a file, upload the five one by one.
  3. Marketplace: <https://marketplace.visualstudio.com/manage>, publisher `zycode`, the extension `zycode`, **Update** (the `...` menu). Upload one VSIX for each platform.
  4. Check the pages as in step 3 of "Publish a release". Tell the team what the log said, so that the registration can be fixed before the next release.
- **A token is the last resort.** Only if the web pages do not take a file: make a token, type it in your terminal only (not in a file, not in the chat), publish, and **revoke it at once**. The workflow never needs a token. The token list of each registry must be empty at the end.

A failed run publishes nothing for the registry that failed. A registry that published stays published: do not change the version to repeat the run.

## Never

- Do not make a long-lived token for either registry, and do not add a secret to the environment `vscode-publish`.
- Do not start a publish from a branch other than `main`: the environment refuses it.
- Do not rename `vscode-extension.yml` or the environment `vscode-publish` without changing the registry entries.
- Do not publish a release with a pre-release flag, and do not publish a pre-release with an even minor.
