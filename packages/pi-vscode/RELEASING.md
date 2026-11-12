# Releasing the zycode extension

This file is the checklist for the **first release** (`0.1.0`, a pre-release), then for later releases. How the files are made is in `PACKAGING.md`. The design is in `docs/specs/2026-11-04-vscode-extension-design.md`.

Why the first release is by hand: Open VSX registers a trusted publisher for one extension, and only after that extension exists and is active. So the first upload to Open VSX cannot use the workflow. The first Marketplace publish with the workflow (`vsce publish --oidc`) for an extension that does not exist yet is not documented, so this checklist does not depend on it.

## What is already set up (checked on 2026-11-12)

- GitHub environment `vscode-publish`: required reviewer `zy1233` (self-review is allowed), deployments from `main` only, no secret.
- Marketplace publisher `zycode`, with a Trusted Publishing policy `zycode release`: repository `zy1233/pi-python`, workflow `vscode-extension.yml`, environment `vscode-publish`.
- Open VSX namespace `zycode` (you are the owner). It has no extension and no trusted publisher yet.
- The workflow file `.github/workflows/vscode-extension.yml`. Its name must stay as it is: both registries name it.

## Before step 1

1. Merge the pull request that adds this file. Work from `main` after that.
2. Get the five VSIX files. Use **one** of these:
   - **From GitHub (no tools needed).** Actions > "VS Code extension" > Run workflow > branch `main`, `publish` **not ticked**, targets as they are. When it is green, download the five artifacts `vsix-<target>` from the run page and unzip each one. Nothing is published by this run.
   - **On your machine (Linux or WSL, on the Linux file system, not `/mnt/c`):** `cd packages/pi-vscode && npm ci && npm run package:all`. The files are in `out/`, and `out/SHA256SUMS` has their checksums.
3. Check the names: `zycode-linux-x64-0.1.0.vsix`, `zycode-linux-arm64-0.1.0.vsix`, `zycode-darwin-arm64-0.1.0.vsix`, `zycode-darwin-x64-0.1.0.vsix`, `zycode-win32-x64-0.1.0.vsix`. They are 39 to 73 MiB each.

## Step 1: first upload to Open VSX (by hand)

1. Open <https://open-vsx.org> and log in. Open the Publish page.
2. Drop all five files on it together. The page queues them one by one. If the page refuses a file, upload the five one by one.
3. Wait about 10 seconds for each file. The extension `zycode.zycode` should show **active**. Its page should list five platforms and the pre-release label. No "not a verified publisher" label should show (you own the namespace). If a platform or the label is missing, stop and tell the team.
4. **Only if the web page does not take the files:** Settings > Access Tokens > Generate new token. Run `npx ovsx publish <file> -p <token>` for each file (type the token in your terminal; do not write it in a file or send it to anyone). Then **Revoke** the token at once. The token list must be empty at the end. The workflow never needs a token.

## Step 2: register the Open VSX trusted publisher

1. Open <https://open-vsx.org/user-settings/trusted-publishers> (Settings > Trusted Publishers).
2. Pick the namespace `zycode` and the extension `zycode`. Provider: GitHub Actions. Fill in exactly:

   | Field | Value |
   |---|---|
   | Organization or User name | `zy1233` |
   | Repository name | `pi-python` |
   | Workflow filename | `vscode-extension.yml` |
   | Environment name | `vscode-publish` |

3. Save. A registration cannot be edited. To change it, delete it and make a new one.
4. **Check that it works before you rely on it.** Run the workflow on `main`: `publish` ticked, `registries` = `open-vsx`, targets as they are. Approve the run in the environment `vscode-publish`. Each file must end with "already published. Skipping publish.": the OIDC login worked, and nothing new was uploaded. If a file says "No trusted publisher matches the presented token", the registration does not match: compare the four values above.

## Step 3: first publish to the Marketplace

1. Open <https://marketplace.visualstudio.com/manage>, publisher `zycode` > New extension > Visual Studio Code. Upload `zycode-linux-x64-0.1.0.vsix` (one file). Wait for the check of the Marketplace to finish. This can take some minutes.
2. Check that the page of `zycode.zycode` shows the pre-release label and the platform `linux-x64`.
3. Run the workflow on `main`: `publish` ticked, `registries` = `marketplace`, `targets` = `linux-arm64 darwin-arm64 darwin-x64 win32-x64`. Approve it in the environment. The job uses `vsce publish --oidc`, with no token. The Marketplace policy shows "Last used" after that.
4. If the web page does not take a platform file, or step 3 of this list fails: tell the team what the page or the log said. Do not make a token. A run with all five targets (`registries` = `marketplace`) tests if the Marketplace creates a new extension through OIDC. That path is untried, and a failed run publishes nothing.
5. Check on a machine: `code --install-extension zycode.zycode --pre-release` installs, and the status bar item starts the agent.

## Later releases (the workflow only)

1. Change `version` in `package.json` (the pre-release line uses an odd minor: `0.1.1`, `0.1.2`, then `0.3.0`) and add `## <version>` to `CHANGELOG.md`. A test fails if the entry is missing. Merge to `main`.
2. Run the workflow on `main`: `publish` ticked, `registries` = `both`, all targets. Approve it in the environment. Open VSX goes first, then the Marketplace.
3. If one registry fails and the other did not, run it again for the one that failed (`registries`). `--skip-duplicate` makes a repeat safe.
4. The workflow publishes pre-releases only (the VSIX is built with `--pre-release`, and `vsce` is called with `--pre-release`). A regular release needs a change in `scripts/package.mjs` and in the workflow first.

## Never

- Do not make a long-lived token for either registry, and do not add a secret to the environment `vscode-publish`.
- Do not start a publish from a branch other than `main`: the environment refuses it.
- Do not rename `vscode-extension.yml` or the environment `vscode-publish` without changing the registry entries.
