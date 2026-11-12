# Changelog

## 0.1.0 (pre-release)

First version. Published as a pre-release (`--pre-release`).

- Chat view in the activity bar, with the status of the agent, its version, the model and the permission mode.
- Permission questions for every tool call that is not read-only, and the project-trust question of the agent.
- **Show diff** for `write` and `edit` calls, before and after the call. The extension never writes your files.
- First-run setup in a VS Code terminal (`--setup`); no key goes through the chat.
- Setting `zycode.permissionMode` (`ask` or `auto`). The extension sets the mode of each session, and it never sets `always-approve`.
- No telemetry.
- Bundles `zycode-agent` 0.9.0 for `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64` and `win32-x64`. The binaries are not code-signed. Only the OpenAI-format provider is in them.
