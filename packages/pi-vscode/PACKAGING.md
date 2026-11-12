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

It unpacks the agent from the VSIX and starts it twice, with a new home folder each time. First with an empty home: ACP `initialize` must return the pinned agent name and version, and `session/new` must fail with `auth_required` (no model). Then with an `agent.toml` that says `permission = "always-approve"` and a model on a closed local port: `session/new` must open, the session must offer the `mode` option with `ask` and `auto`, and the client must be able to change it from `always-approve` to `ask`. The extension does the same for its sessions. Use a VSIX of the platform that you run on.

To try a VSIX in VS Code: `code --install-extension out/zycode-linux-x64-0.1.0.vsix --pre-release`.

## Publish

The first release goes out by hand, in an exact order. Later releases go out from the workflow `.github/workflows/vscode-extension.yml` (Trusted Publishing, started by hand on `main`). The steps are in `RELEASING.md`.

Check before you upload a file made on your machine: `sha256sum -c out/SHA256SUMS`. The sha256 of a VSIX is not the same from build to build, so compare only with the `SHA256SUMS` of the same build. Each VSIX is between 39 and 73 MiB.

## Change the agent version

1. Publish the agent Release first (`agent-binaries.yml`).
2. Change `zycodeAgentVersion` in `package.json`, and `agent-assets.json` (version, tag, `pythonPackages`, and for each target `size` and `sha256` of the asset). `test/packaging.test.ts` fails when these disagree.
3. Make the files again.
