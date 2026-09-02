# Phase 5：Coding Agent Prompt Engine

> Scope: 忠实移植 pi 上游 `packages/coding-agent` 的 system prompt 装配。
> 状态：已实施（2026-09-02）。
> 上游参照：[earendil-works/pi](https://github.com/earendil-works/pi) `packages/coding-agent/src/core/system-prompt.ts`、`src/server/create-harness.ts`、`src/core/skills.ts`。

---

## 1. 目标

替换 Phase 4 静态 `CODING_SYSTEM_PROMPT`，实现：

1. `build_system_prompt()` — Available tools / Guidelines / project_context / skills / cwd
2. `build_coding_agent_harness_system_prompt()` — 从 active tools 收集 `prompt_snippet` / `prompt_guidelines`
3. Context files — AGENTS.md / CLAUDE.md / SYSTEM.md / APPEND_SYSTEM.md
4. Skills 格式对齐 pi `<available_skills>`

**不以 grok-build/TUI 模板为参考。**

---

## 2. pi-python 模块映射

| pi 上游 | pi-python |
|---------|-----------|
| `packages/coding-agent/src/core/system-prompt.ts` | `packages/pi-agent-cli/pi_agent_cli/system_prompt.py` |
| `packages/coding-agent/src/server/create-harness.ts` | `packages/pi-agent-cli/pi_agent_cli/create_harness.py` |
| context file 加载 | `packages/pi-agent-cli/pi_agent_cli/context_files.py` |
| config 解析 | `packages/pi-agent-cli/pi_agent_cli/config.py` `[prompt]` |
| `formatSkillsForPrompt` | `packages/pi-agent-harness/pi_agent_harness/skills.py` |
| bash PI_* env | `pi_agent_core/coding_tools/bash.py` `prepare_env` |

---

## 3. 路径映射

| pi | pi-python |
|----|-----------|
| `~/.pi/agent/AGENTS.md` | `~/.pi-python/agent/AGENTS.md` |
| `~/.pi/agent/SYSTEM.md` | `~/.pi-python/agent/SYSTEM.md` |
| `.pi/SYSTEM.md` | `.pi/SYSTEM.md` |
| `getReadmePath()` / docs | 仓库 `README.md` / `docs/` |

---

## 4. Harness 集成

`create_session_harness()`（async）传入 **callable** `system_prompt`，在 turn 时调用 `build_coding_agent_harness_system_prompt()`。`AgentHarness._create_turn_state` 已有 callable 分支；`build_harness_system_prompt()` 仅在 base 无 `<available_skills>` 时补 skills（兼容 shim）。

bash 工具通过 `prepare_env` 注入 `PI_SESSION_ID`、`PI_SESSION_FILE`、`PI_PROVIDER`、`PI_MODEL`、`PI_REASONING_LEVEL`。

Headless（`-p` / `--prompt-json` / `--prompt-file`）额外支持 CLI flags，优先级高于 `agent.toml` `[prompt]`：

- `--system-prompt` / `--system-prompt-override`
- `--system-prompt-file`
- `--append-system-prompt` / `--rules`
- `--append-system-prompt-file`
- `--no-context-files`

---

## 5. 测试

- `packages/pi-agent-cli/tests/test_system_prompt.py` — tool contributions + golden snapshot
- `packages/pi-agent-cli/tests/test_context_files.py` — AGENTS walk
- `packages/pi-agent-harness/tests/test_harness_h4_resources_env.py` — 更新 `<available_skills>` 断言

---

## 6. 明确不在范围

- grok user message 包装（`<user_query>` 等）
- Jinja2 / 模板引擎
- TUI Rust prompt 改造
- Provider 专属长模板
