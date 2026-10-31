# Open the registry pull request by hand

Nothing here has been sent. The entry is `agent.json` in this folder (binary only) and
the icon is `icon.svg`. Both passed the registry's own checks on 2026-10-31 (registry commit
`6772a52`):

- `build_registry.py --agents zycode` (URL checks on): validated.
- `verify_agents.py --agent zycode --type binary`: passed (started from the release archive).
- `verify_agents.py --auth-check --agent zycode --type binary`: passed (`pi-setup`, type `terminal`).

The registry has no pull request template, and no rule has changed since the first read. Its
open and merged pull requests use the title form `Add <Name> agent` and the branch form
`add-<id>`. There is no open pull request for `zycode`.

Title: `Add zycode agent`

## 1. Web UI

1. Open https://github.com/agentclientprotocol/registry and press **Fork** (owner: your account,
   keep the name `registry`).
2. In your fork, press the branch menu, type `add-zycode` and press **Create branch**.
3. On that branch: **Add file**, **Create new file**. Name it `zycode/agent.json` (typing
   `zycode/` makes the folder). Paste the whole content of `agent.json` from this folder. Commit
   to the branch `add-zycode` with the message `Add zycode agent`.
4. **Add file**, **Upload files**: upload `icon.svg` from this folder into the folder `zycode`
   (or create `zycode/icon.svg` and paste the text). The file name must be exactly `icon.svg`.
   Commit to `add-zycode`.
5. Press **Contribute**, **Open pull request**. Base repository `agentclientprotocol/registry`,
   base `main`; head `<your user>:add-zycode`. Paste the title and the body from the end of this
   file. Press **Create pull request**.
6. The registry CI runs on the pull request (it checks `zycode/agent.json` and `zycode/icon.svg`).
   If a check fails, read the log; the file names and the content are the only things in the
   pull request.

## 2. `gh` (already signed in as the account that owns the fork)

Bash:

```bash
gh repo fork agentclientprotocol/registry --clone --remote
cd registry
git switch -c add-zycode
mkdir zycode
cp /path/to/registry-entry/agent.json /path/to/registry-entry/icon.svg zycode/
git add zycode
git commit -m "Add zycode agent"
git push -u origin add-zycode
gh pr create --repo agentclientprotocol/registry --base main \
  --head "$(gh api user -q .login):add-zycode" \
  --title "Add zycode agent" --body-file /path/to/pr-body.md
```

PowerShell:

```powershell
gh repo fork agentclientprotocol/registry --clone --remote
Set-Location registry
git switch -c add-zycode
New-Item -ItemType Directory -Path zycode | Out-Null
Copy-Item C:\path\to\registry-entry\agent.json, C:\path\to\registry-entry\icon.svg -Destination zycode\
git add zycode
git commit -m "Add zycode agent"
git push -u origin add-zycode
$me = gh api user -q .login
gh pr create --repo agentclientprotocol/registry --base main --head "${me}:add-zycode" `
  --title "Add zycode agent" --body-file C:\path\to\pr-body.md
```

`pr-body.md` is the text of the section "PR body" below, saved as a UTF-8 file. In PowerShell:

```powershell
@'
<paste the PR body here>
'@ | Set-Content -Encoding utf8 C:\path\to\pr-body.md
```

Replace `C:\path\to\` (or `/path/to/`) with the folder that holds these files. Before you push,
you can check the entry on your machine (needs `uv`; run it in the registry clone):

```powershell
uv run --with jsonschema .github/workflows/build_registry.py --agents zycode
```

Do not comment on registry issue #726 (a separate topic).

## Title

```
Add zycode agent
```

## PR body

```markdown
Adds zycode (`pi-python`), a coding agent built on LangChain that speaks ACP over stdio.

- Release: https://github.com/zy1233/pi-python/releases/tag/v0.8.0 (also on PyPI as `pi-agent-cli-lc` 0.8.0)
- `binary` for five targets: linux-x86_64, linux-aarch64, darwin-aarch64, darwin-x86_64, windows-x86_64. `sha256` is set to the digest of each release asset. `cmd` is `./zycode-agent` (`./zycode-agent.exe` on Windows). There is no `windows-aarch64` build.
- The program is a PyInstaller one-file build, made on a native GitHub runner for each target (workflow `Agent binaries`, run 41193119162). Each build runs an ACP smoke test and the ACP contract tests before the archive is attached to the release.
- The program has the OpenAI-format provider. `--setup` (the terminal login that `initialize` offers) writes the model and the key.
- Icon: 16x16, `currentColor` only.

Checks, run locally against the current `main` of this repository:

- `build_registry.py --agents zycode` (URL checks on): validated.
- `verify_agents.py --agent zycode --type binary`: started from the release archive.
- `verify_agents.py --auth-check --agent zycode --type binary`: `Auth OK: pi-setup(terminal)`.
```
