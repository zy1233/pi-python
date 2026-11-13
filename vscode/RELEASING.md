# Releasing the zycode extension

This file is the checklist for a release. How the files are made is in `PACKAGING.md`. The design is in `docs/specs/2026-11-04-vscode-extension-design.md`.

## Release record

### 1.0.0 (2026-11-12)

- Published by the workflow `.github/workflows/vscode-extension.yml`: run `43181052994` on `main`, commit `8b0e50c`. The run finished green at 08:20Z.
- The user approved the run in the environment `vscode-publish` at 08:18Z.
- Open VSX took the five VSIX files by Trusted Publishing (OIDC), from 08:18:55Z to 08:19:17Z. Then the Marketplace took them with `vsce publish --oidc`, from 08:19:24Z to 08:19:57Z. Both registries accepted the first try with no token. So the OIDC path and both trusted publisher registrations are proven.
- Time until the version was visible: Open VSX 08:23Z to 08:25Z (target by target, the scan queue). Marketplace 08:26Z to 08:28Z (validation, about 7 to 9 minutes after the upload).
- Checked with the public APIs: five targets at 1.0.0 on both registries. `preRelease` is false on Open VSX. The Marketplace has no PreRelease property for 1.0.0 (0.1.0 had it). The sha256 of the five Open VSX files equals the `sha256sum` in the Publish log (the CI artifacts).
- Not verified: an install of 1.0.0 in a real VS Code, the update from 0.1.0 to 1.0.0, and the Windows and macOS flows.

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
- The next pre-release line is `1.1.x`, and the next stable release is `1.2.0`. A pre-release needs a new input in the workflow and the flag in `scripts/package.mjs` first. Do that in its own pull request.
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
