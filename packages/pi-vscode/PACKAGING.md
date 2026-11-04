# Packaging the zycode extension

One VSIX per platform. Each VSIX holds the `zycode-agent` program of one release (`agent-assets.json`). Work from the `packages/pi-vscode` folder. Use Linux. The Linux and macOS programs must keep their execute bit in the VSIX, and only Linux is tested for that.

## Make the files

```
npm ci
npm run package linux-x64      # one target
npm run package:all            # all five
```

The script does this for each target:

1. Downloads the agent archive from the GitHub Release named in `agent-assets.json`, and stops if its sha256 is not the pinned one. The download is kept in `.vsix-cache/`.
2. Builds `dist/extension.js` and `dist/webview.js` (minified), and writes `THIRD-PARTY-NOTICES` from the Python packages and the npm packages that are inside.
3. Runs `vsce package --target <target> --pre-release --no-dependencies`.
4. Opens the VSIX and checks it (`scripts/check-vsix.mjs`): the one agent program for the target with the execute bit, `TargetPlatform`, the pre-release flag, the licence, the notices, the icon, and no other file.
5. Writes `out/zycode-<target>-<version>.vsix` and `out/SHA256SUMS`, and prints path, size and sha256.

The files in `out/` are ready for a hand upload. Do not edit them.

## Test a VSIX

```
node scripts/smoke-vsix.mjs out/zycode-linux-x64-0.1.0.vsix
```

It unpacks the agent from the VSIX, starts it with an empty home folder, sends ACP `initialize`, checks the agent name and version (the pin), and sends `session/new`. With no model set, the correct answer is the error `auth_required`. Use a VSIX of the platform that you run on.

To try a VSIX in VS Code: `code --install-extension out/zycode-linux-x64-0.1.0.vsix --pre-release`.

## Upload by hand (first upload of each registry)

The first upload of a registry is a manual upload of one VSIX. After it, the workflow `vscode-extension.yml` can publish (see `docs/specs/2026-11-04-vscode-extension-design.md`).

1. **Open VSX first.** Upload `zycode-linux-x64-<version>.vsix` on the extension page of <https://open-vsx.org>. Then register the workflow `vscode-extension.yml` as the Trusted Publisher of the extension.
2. **Then the Marketplace.** Upload the same file on <https://marketplace.visualstudio.com/manage>.
3. Upload the other four files the same way, or let the workflow do it with its `targets` input.

Check before you upload: `sha256sum -c out/SHA256SUMS`. Each VSIX is between 39 and 73 MiB.

## Change the agent version

1. Publish the agent Release first (`agent-binaries.yml`).
2. Change `zycodeAgentVersion` in `package.json`, and `agent-assets.json` (version, tag, `pythonPackages`, and for each target `size` and `sha256` of the asset). `test/packaging.test.ts` fails when these disagree.
3. Make the files again.
