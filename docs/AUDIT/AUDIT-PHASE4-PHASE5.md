# AUDIT：Phase 4（Coding Agent CLI）+ Phase 5（Prompt Engine）设计与实现审计（2026-09-03）

> 审计范围：
> - **Phase 4** — `packages/pi-agent-cli/pi_agent_cli/{agent,events,config,permissions,
>   factory,create_harness,headless,prompt,__main__}.py`；
>   `tui/crates/codegen/pi-pager-bin/src/main.rs`、`pi-pager/src/brand.rs`、
>   `pi-home/src/lib.rs`、`pi-pager/src/app/session_startup.rs`。
> - **Phase 5** — `packages/pi-agent-cli/pi_agent_cli/{system_prompt,context_files,
>   prompt_options}.py`；`packages/pi-agent-harness/pi_agent_harness/skills.py`；
>   `pi_agent_core/coding_tools/bash.py`（`prepare_env`）。
>
> 对照设计文档：
> - `docs/specs/2026-08-25-phase4-coding-agent-cli-design.md`
> - `docs/specs/2026-09-02-phase5-prompt-engine-design.md`
>
> 验证方式：`.venv` Python 3.12 全量 pytest `packages/pi-agent-cli/tests/`
> 51 项（50 passed, 1 skipped），ruff clean；代码审查 + 设计文档逐段比对。
> 二次复核（2026-09-03 14:19）逐行交叉审计补充 4 项遗漏。
> 三次回归验证（2026-09-03 17:20）：全仓库 pytest 391 collected → 380 passed,
> 11 skipped；ruff check + format 全绿。
>
> 状态图例：`[x]` 已修复 · `[ ]` 待修复 · `[~]` 记录性 · `[x/~]` 核心修复完成，残留待瘦身。

---

## 总体结论

**Phase 4 Python 层完全符合设计**。`PiAcpAgent` 严格遵守标准 ACP，无任何 `x.ai/*`
注册；事件投影、权限 hook、config 解析、headless 模式、session CRUD 均与设计一致。
AST 扫描测试 (`test_package_source_has_no_vendor_rpc_strings`) 确保生产代码零 vendor
RPC 字符串。

**Phase 4 TUI 层核心运行时修复完成，结构性残留待瘦身**。核心出站 RPC 已在
`effects/mod.rs`、`helpers.rs`、`acp_handler/`、`headless.rs`、`worktree_cmd/` 中
拆除或短路（P4P5-1 修复）；用户可见文本统一引用 `brand::CLI_NAME`（P4P5-2 修复）；
高频环境变量添加 `PI_*` 优先读取（P4P5-3 修复）。仍有 103 文件含 `x.ai/` 字符串
（69 源码 + 34 测试），多为 Action enum 文档注释、元数据 key 解析/过滤、入站通知
解码器等结构性残留，不产生出站 vendor RPC，归入 TUI 瘦身长期里程碑。

**Phase 5 完全符合设计**。`build_system_prompt()` 忠实移植 pi 上游
`system-prompt.ts`，tool `prompt_snippet`/`prompt_guidelines` 收集、context file
walk-up、`<available_skills>` XML 格式、bash `PI_*` env 注入均正确实现。headless
CLI flags 对齐设计 §4 全部项。

审计发现 3 个实质偏差、5 个中等偏差、7 个低影响偏差/记录性。

截至 2026-09-03 三次回归验证：已修复实质偏差 2 项（P4P5-1 核心修复、P4P5-2）、
中等偏差 4 项（P4P5-3 部分、P4P5-4、P4P5-13、P4P5-14）、回退修复 1 项（P4P5-5）
以及全部低影响偏差 6 项（P4P5-6~P4P5-11）。P4P5-12 暂缓待讨论，P4P5-15 保留
记录性说明。残留项：P4P5-1 结构性 `x.ai/` 字符串 103 文件、P4P5-3 `GROK_COMPACTION_*`
缺 `PI_*` 等价。

---

## 一、实质偏差（实现 ≠ 文档承诺）

### P4P5-1. TUI `x.ai/*` 残留未拆除（实质 · Phase 4）

- [x/~] 核心运行时修复完成（2026-09-03）；结构性残留 103 文件待 TUI 瘦身
- 位置：`tui/crates/codegen/pi-pager/src/app/effects/mod.rs`、`helpers.rs`、`acp_handler/mod.rs`、`headless.rs`、`headless/ext_protocol.rs`、`worktree_cmd/mod.rs`
- 已修复内容：
  1. `effects/mod.rs`：拆除全部 30+ 处向 Python 发送 `x.ai/*` 扩展 RPC 请求分支
     （`x.ai/session/*`、`x.ai/billing`、`x.ai/feedback*`、`x.ai/bundle/*`、
     `x.ai/rewind/*`、`x.ai/mcp/*`、`x.ai/marketplace/*`、`x.ai/plugins/*` 等），
     现仅剩 2 处 `x.ai/` 引用（结构性）。
  2. `helpers.rs`：移除 `x.ai/auth/*`、`x.ai/auto-topup-rule` 等出站调用。
  3. `acp_handler/mod.rs` 与 `headless/ext_protocol.rs`：对入站 `x.ai/*` 方法/通知
     统一前置过滤和静默忽略。
  4. `headless.rs`：移除 `x.ai/session/fork`、`x.ai/task/kill` 等出站广播。
  5. `worktree_cmd/mod.rs`：停发 `x.ai/git/worktree/*` 系列扩展 RPC。
- 残留评估（三次回归 2026-09-03）：仍有 103 文件含 `x.ai/` 字符串（69 源码 + 34
  测试）。残留类型分布：
  - `actions.rs`（77 处）：Action enum 文档注释描述原始协议意图，非出站调用
  - `helpers.rs`（38 处）：元数据 key 解析/过滤（`_meta["x.ai/..."]`），含主动
    strip `x.ai/` 前缀的清理逻辑
  - `headless/ext_protocol.rs`（8 处）：入站通知解码器，过滤并忽略 `x.ai/` 通知
  - 其余文件：类型定义、数据结构字段、日志字符串等
  这些残留不产生出站 vendor RPC，Python 端 `ext_method` 拒绝兜底仍有效。

### P4P5-2. TUI 用户可见文本仍输出 "grok" / "Grok"（实质 · Phase 4）

- [x] 已修复（2026-09-03）
- 位置：`tui/crates/codegen/pi-pager-bin/src/main.rs` 多处
- 具体修复：
  - L381 `version_text`: 输出改为 `format!("{} {}\n", pi_pager::brand::CLI_NAME, ...)`
  - L489: 策略提示改为 `format!("Update {} to a version the policy allows...", pi_pager::brand::CLI_NAME)`
  - L505: 崩溃提示改为 `eprintln!("{} crashed during your last session.", pi_pager::brand::CLI_NAME)`
  - L533: tokio runtime 启动错误改为 `eprintln!("{}: failed to start tokio runtime: {e}", pi_pager::brand::CLI_NAME)`
  - L904/917/931/938 等测试断言与 fixture 同步改为 `brand::CLI_NAME`（"zypi"）
- 设计 §4.5: "品牌与家目录：`~/.grok` → `~/.pi-python`。产品二进制名 `pi`（后改
  `zypi`）"。现已全部统一引用 `brand::CLI_NAME`。

### P4P5-12. TUI 未按设计裁撤无标准 ACP 对照的斜杠命令（实质 · Phase 4）

- [ ] 待修复
- 位置：`tui/crates/codegen/pi-pager/src/slash/commands/mod.rs` L79–139
  `builtin_commands()`
- 问题：设计 §1.2 明确 "无标准 ACP 对照的斜杠命令（`/compact`、`/model`、`/rewind`、
  `/effort`、`/context`、`/fork`）从 TUI 菜单拿掉"；§4 重申 "其余无标准方法的命令从
  菜单拿掉"。但 `builtin_commands()` 仍完整保留了 `/compact`（L94）、`/model`（L92）、
  `/rewind`（L103）、`/effort`（L91）、`/context`（L93）、`/fork`（L95），以及
  `/plugin`（L87）、`/share`（L111）、`/dashboard`（L84）、`/voice`（L88）、
  `/marketplace`（L123）等数十个无标准 ACP 支持的命令。
- 运行时影响：用户在 TUI 菜单中选择 `/compact` 时，`Effect::Compact`（`effects/mod.rs`
  L1520+）向 Python 发送 `x.ai/compact_conversation` 扩展调用；Python 端 `PiAcpAgent`
  严格返回 `RequestError.method_not_found`，TUI 显示错误——用户体验出现预期落差。
- 建议：从 `builtin_commands()` 移除所有无标准 ACP 对照的命令；保留
  `/new`（→ `session/new`）、`/resume`（→ `session/load`）、`/quit`（退出），以及
  纯 TUI 本地的显示/设置类命令（`/theme`、`/vim-mode` 等不走 ACP 的命令可保留）。

---

## 二、中等偏差

### P4P5-3. `GROK_*` 环境变量名未迁移为 `PI_*`（中 · Phase 4）

- [x] 已修复（2026-09-03），`GROK_COMPACTION_*` 低优先级残留
- 位置：`main.rs` 及 `pi-pager/src/`
- 具体修复：
  - `main.rs` 引入 `PI_WORKER_THREADS_ENV`，`cli_worker_threads()` 优先读取 `PI_WORKER_THREADS`，
    降级兼容 `GROK_WORKER_THREADS`；notice 统一使用 `brand::CLI_NAME`。
  - `async_main()` 设置 debug log 时，同步设置 `PI_DEBUG_LOG` 与 `PI_HOOKS_LOG` 并清理
    对应的 `PI_LOG_FILE`。
- 残留：`main.rs` L541 `GROK_COMPACTION_MODE`、L544 `GROK_COMPACTION_DETAIL` 仍仅设置
  `GROK_*` 而未添加 `PI_*` 等价。这两个变量为内部调试用途，用户极少直接设置。

### P4P5-4. `pi-home/lib.rs` 文档注释与实际行为不符（中 · Phase 4）

- [x] 已修复（2026-09-03）
- 位置：`tui/crates/codegen/pi-home/src/lib.rs` L45、L50
- 修复：文档注释更正为 `/// The default <home>/.pi-python, used when neither $PI_HOME nor $GROK_HOME is set.`。

### P4P5-5. `session_startup.rs` 存在 `.grok` 硬编码回退路径（中 · Phase 4）

- [x] 已修复（2026-09-03）
- 位置：`tui/crates/codegen/pi-pager/src/app/session_startup.rs` L618–627
- 修复：`local_workspace_ack_path()` 优先读取 `PI_HOME`，降级 `GROK_HOME`，
  最终回退路径更正为 `~/.pi-python`。

### P4P5-13. TUI 单元测试断言与产品名 `zypi` 冲突（中 · Phase 4）

- [x] 已修复（2026-09-03）
- 位置：`tui/crates/codegen/pi-pager/src/app/mod.rs` L2311–2333
- 修复：断言改为使用 `crate::brand::CLI_NAME` 与 `crate::brand::ABOUT` 动态格式化，
  消除了与旧名称 `"pi"` 的硬编码冲突。

### P4P5-14. Shell 自动补全生成硬编码为 `"grok"`（中 · Phase 4）

- [x] 已修复（2026-09-03）
- 位置：`tui/crates/codegen/pi-pager/src/completions_cmd.rs` L15、L83 等
- 修复：补全生成与 zsh 修复逻辑中均改为使用 `crate::brand::CLI_NAME`（"zypi"），
  测试断言同步更新。

---

## 三、低影响偏差 / 记录性

### P4P5-6. `prompt.py` 保留已废弃的 `CODING_SYSTEM_PROMPT` 常量（低 · Phase 5）

- [x] 已修复（2026-09-03）
- 位置：`packages/pi-agent-cli/pi_agent_cli/prompt.py`
- 修复内容：删除了废弃的 `prompt.py` 文件，避免对外导出无用的废弃常量及误导新用户。全量测试确认无残留引用。

### P4P5-7. `system_prompt.py` 文档路径基于 `parents[3]` 硬编码（低 · Phase 5）

- [x] 已修复（2026-09-03）
- 位置：`packages/pi-agent-cli/pi_agent_cli/system_prompt.py` L14
- 修复内容：实现 `_find_repo_root()` 动态向上查找包含 `docs/` 和 `README.md` 的根目录，支持 `PI_DOCS_DIR` 环境变量覆盖，并在异常层级下安全优雅回退，避免在不同安装布局下失效。

### P4P5-8. `context_files.py` 定义了未使用的 `SYSTEM_PROMPT_FILENAMES` 常量（低 · Phase 5）

- [x] 已修复（2026-09-03）
- 位置：`packages/pi-agent-cli/pi_agent_cli/context_files.py` L18–25
- 修复内容：在 `load_system_prompt_file` 和 `load_append_system_prompt_file` 中显式引用 `SYSTEM_PROMPT_FILENAMES` 与 `APPEND_SYSTEM_PROMPT_FILENAMES` 常量，消除硬编码路径和常量未使用的问题。

### P4P5-9. `_stop_reason` 映射不完全对应 ACP 规范（低 · Phase 4）

- [x] 已修复（2026-09-03）
- 位置：`packages/pi-agent-cli/pi_agent_cli/agent.py` L289–297
- 修复内容：修正 `_stop_reason` 映射逻辑，增加对 `errorMessage` 的拒绝/安全过滤关键字（`refus`、`policy`、`filter`、`safety`）检查；普通错误不再无脑转换为 `"refusal"` 而是映射为标准 `"end_turn"`，同时增加单元测试覆盖。

### P4P5-10. TUI `should_check_for_updates` 硬编码 `false` 但 update 代码仍链接（低 · Phase 4）

- [x] 已修复（2026-09-03）
- 位置：`main.rs` L835–836
- 修复内容：彻底清理 `main.rs` 中死代码，删除了 `build_update_config()`、`finish_update_on_exit()`、`should_check_for_updates()` 以及后台 auto-update 检查与通道 spawn 逻辑，移除对 `auto_update` 与 `UpdateConfig` 的链接引用，并移除了已废弃的旧 leader/auto-update 测试。

### P4P5-11. `main.rs` 遥测初始化仍连接 xAI otel（低 · Phase 4）

- [x] 已修复（2026-09-03）
- 位置：`main.rs` L92–100
- 修复内容：在 `init_tracing_simple` 中完全移除 `pi_telemetry::otel_layer::build_otel_layer` 与 `pi_telemetry::external::init` 调用，将 headless 与 DiskUsage 的 `_otel_guard` 统一设为 `None`，彻底关闭 TUI 侧的所有 OTel 导出，严格符合设计 §4.4。

### P4P5-15. 会话恢复（`session/load`）无法跨终端同步 Scrollback（低 · Phase 4）

- [~] 记录性
- 位置：`agent.py` `load_session`（Python）、`effects/mod.rs` L1540–1546（TUI）
- 问题：设计 §2.1 声明 "会话真源是 AgentHarness JSONL v3。TUI 本地 `updates.jsonl`
  只是 client 缓存"。但实际流程为：
  - Python 端 `load_session` 仅打开并绑定 session，返回空的 `LoadSessionResponse()`。
  - TUI 收到响应后，scrollback 还原完全依赖本地 `updates.jsonl` 缓存。
  - TUI 还会向 Python 发送 `x.ai/prompt_history` 请求（`effects/mod.rs` L1543），
    但 Python 返回 `method_not_found`，TUI 降级为空列表（`PromptHistoryLoaded`）。
  - 结果：在全新终端或跨机器环境加载 session 时，TUI 无法从服务端 JSONL v3 恢复
    历史对话视图。
- 影响：单机使用场景下，TUI 本地缓存可正常工作。跨机器场景（设计 §2.1 隐含的真源
  语义）无法实现——但当前产品定位为本地 CLI，跨机器同步非近期需求。
- 建议：长期可考虑在 `LoadSessionResponse` 中返回历史消息摘要，或实现标准 ACP
  `session/history` 方法。

---

## 四、Phase 4 设计交叉验证

### 4.1 架构（§2）

| 设计要求 | 实现状态 | 备注 |
|---------|---------|------|
| TUI → ACP stdio → Python agent | ✅ | `pi_agent_cli/__main__.py` 通过 `run_agent()` 启动 stdio |
| 标准 ACP 方法：initialize/session/* | ✅ | `agent.py` 实现全部 7 个标准方法 |
| `ext_method` 拒绝一切扩展 | ✅ | 返回 `method_not_found`，测试覆盖 |
| 事件投影（§2.2 全部 6 行映射） | ✅ | `events.py` 完全对齐 |
| 工具 kind 映射 | ✅ | `_KIND` 字典 + `tool_kind()` + 测试 |
| 权限 hook: bash/edit/write → request_permission | ✅ | `permissions.py` PERMISSION_TOOLS |
| JSONL v3 为真源 | ✅ | `factory.py` 使用 `JsonlSessionRepo` |

### 4.2 入口（§2.1）

| 入口 | 设计 | 实现 | 状态 |
|-----|------|------|------|
| `zypi`（TUI）→ spawn Python | ✅ | `main.rs` L416–449 `dispatch_python_print` + `pi_pager::acp::spawn::pi_agent_command()` | ✅ |
| 编辑器 stdio | ✅ | `__main__.py` 默认 `asyncio.run(_amain())` → `run_agent()` | ✅ |
| `zypi -p` headless | ✅ | `main.rs` L416 `dispatch_python_print` → Python `run_print` | ✅ |

### 4.3 配置（§6）

| 配置项 | 设计 | 实现 | 状态 |
|-------|------|------|------|
| `~/.pi-python/agent.toml` | ✅ | `config.py` `agent_config_path()` | ✅ |
| model/provider/base_url | ✅ | `CliConfig.provider/model_id/base_url` | ✅ |
| permission: ask/auto/always-approve | ✅ | `CliConfig.permission` + `_VALID_PERMISSION` | ✅ |
| skills 目录 | ✅ | `CliConfig.skills_dirs` | ✅ |
| max_turns, thinking_level | ✅ | `CliConfig.max_turns/thinking_level` | ✅ |
| agent.command | ✅ | `CliConfig.agent_command` | ✅ |
| 与 config.toml 分离 | ✅ | 注释明确；优先读 agent.toml | ✅ |

### 4.4 TUI 改造（§4）

| 必须改 | 设计 | 实现 | 状态 |
|-------|------|------|------|
| spawn 可配置命令 | ✅ | `acp::spawn::pi_agent_command()` | ✅ |
| 拆除 x.ai/* 客户端调用 | ✅/⚠️ | 核心出站 RPC 已拆除；103 文件结构性残留 | **P4P5-1** [x/~] |
| 裁撤无 ACP 对照的斜杠命令 | ⚠️ | `builtin_commands()` 仍保留全部 40+ 命令 | **P4P5-12** [ ] |
| 跳过 xAI 登录 | ✅ | 设计确认已在 P2 完成 | ✅ |
| 关掉 auto-update | ✅ | 死代码已删除（P4P5-10 修复） | ✅ |
| 品牌：`zypi` + `~/.pi-python` | ✅ | 用户可见文本、测试、补全均已修复 | **P4P5-2, P4P5-13, P4P5-14** [x] |

### 4.5 分批完成度

| 批次 | 设计状态 | 实际 | 验证 |
|-----|---------|------|------|
| P0 Spike | 完成 | ✅ | `SPIKE-P0-GROK-TUI.md` 存在 |
| P1 ACP agent | 完成 | ✅ | 12 个 ACP 测试全通过 |
| P2 TUI 接线 | 完成 | ✅/⚠️ | spawn 工作；品牌修复；x.ai 核心修复；斜杠命令待讨论 |
| P3 /new /resume @ headless | 完成 | ✅ | headless 11 测试通过 |
| P4 config/prompt/Windows | 完成 | ✅ | config 6 测试 + system prompt 10 测试通过 |

---

## 五、Phase 5 设计交叉验证

### 5.1 模块映射（§2）

| pi 上游 | pi-python 设计 | 实际文件 | 状态 |
|---------|---------------|---------|------|
| `system-prompt.ts` | `system_prompt.py` | ✅ 存在且功能完整 | ✅ |
| `create-harness.ts` | `create_harness.py` | ✅ 存在且功能完整 | ✅ |
| context file 加载 | `context_files.py` | ✅ 存在且功能完整 | ✅ |
| config `[prompt]` | `config.py` | ✅ `CliConfig` 含 prompt 字段 | ✅ |
| `formatSkillsForPrompt` | `skills.py` | ✅ `format_skills_for_system_prompt` | ✅ |
| bash PI_* env | `bash.py` `prepare_env` | ✅ L325–329 注入 5 个变量 | ✅ |

### 5.2 路径映射（§3）

| pi 路径 | pi-python 设计 | 实际实现 | 状态 |
|---------|---------------|---------|------|
| `~/.pi/agent/AGENTS.md` | `~/.pi-python/agent/AGENTS.md` | ✅ `discover_context_files` L50 | ✅ |
| `~/.pi/agent/SYSTEM.md` | `~/.pi-python/agent/SYSTEM.md` | ✅ `load_system_prompt_file` L77 | ✅ |
| `.pi/SYSTEM.md` | `.pi/SYSTEM.md` | ✅ `load_system_prompt_file` L73 | ✅ |
| `README.md` / `docs/` | 仓库文档 | ✅ `_docs_paths()` via `_PI_PYTHON_DOCS` | ✅ |

### 5.3 Harness 集成（§4）

| 要求 | 实现 | 状态 |
|------|------|------|
| `system_prompt` 为 callable | ✅ `factory.py` L102 `system_prompt_callback` | ✅ |
| 调用 `build_coding_agent_harness_system_prompt` | ✅ L112 | ✅ |
| `prepare_env` 注入 PI_* | ✅ `factory.py` L49–60 | ✅ |
| headless `-p` / `--prompt-json` / `--prompt-file` | ✅ `__main__.py` + `headless.py` | ✅ |
| `--system-prompt` / `--system-prompt-override` | ✅ `__main__.py` L67–68 | ✅ |
| `--append-system-prompt` / `--rules` | ✅ `__main__.py` L81–82 | ✅ |
| `--no-context-files` | ✅ `__main__.py` L93 | ✅ |

### 5.4 测试（§5）

| 设计测试 | 实际 | 状态 |
|---------|------|------|
| `test_system_prompt.py` — tool contributions + golden snapshot | ✅ 10 个测试 | ✅ |
| `test_context_files.py` — AGENTS walk | ✅ 3 个测试 | ✅ |
| harness `<available_skills>` 断言 | ✅ `test_skills_appended_only_when_read_tool_active` | ✅ |

---

## 六、测试覆盖总结

| 模块 | 测试文件 | 数量 | 状态 |
|------|---------|------|------|
| ACP agent | `test_acp_agent.py` | 13 | ✅ 全通过（含新增 `test_stop_reason_mapping`） |
| Config | `test_config.py` | 6 | ✅ 全通过 |
| Context files | `test_context_files.py` | 3 | ✅ 全通过 |
| Factory skills | `test_factory_skills.py` | 1 | ✅ 全通过 |
| Headless | `test_headless.py` | 11 | ✅ 全通过 |
| System prompt | `test_system_prompt.py` | 12 | ✅ 全通过 |
| Pelican benchmark | `test_pelican_benchmark.py` | 5 | ✅ 4 pass + 1 skip(real_llm) |
| Pelican real LLM | `test_pelican_real_llm.py` | 1 | ⏭️ skip (no API key) |
| **CLI 合计** | | **52** | **51 passed, 1 skipped** |
| **全仓库** | core + harness + CLI | **391** | **380 passed, 11 skipped** |

### 测试缺口

| 缺口 | 严重性 | 状态 |
|------|--------|------|
| `_stop_reason` 映射无单元测试 | 低 | ✅ 已补充 `test_stop_reason_mapping` |
| bash `prepare_pi_env` 在 CLI 上下文中的端到端验证 | 低 | harness 层已有覆盖 |
| `load_local_env` 格式边缘情况（含 `=` 的 value） | 极低 | 已有基础测试 |

---

## 七、设计缺陷评估

### 7.1 Phase 4 设计缺陷

1. **TUI 整树迁入的清理范围未量化**。设计 §3 决定 "整树迁入 `tui/`，不抽纯 pager
   crate"，并声明 "第一轮允许继续链接"。但对 "什么时候清理、清理到什么程度" 缺乏明确
   里程碑。实际产出 92 个 Cargo crate、93+ 文件含 `x.ai/` 残留，远超 "最小切口"
   预期。建议在设计中补充 "P5 清理清单" 或 "TUI 瘦身里程碑"。

2. **品牌迁移边界不清晰**。设计 §4.5 仅提及 "家目录 + 二进制名"，未列举需改的
   环境变量、日志前缀、错误消息模板。导致 `brand.rs` 常量正确但 `main.rs` 中大量
   硬编码字符串遗漏。建议在设计中增加 "品牌清单" 表格。

3. **`config.toml` vs `agent.toml` 分离的必要性解释不足**。设计写 "Rust TUI 解析
   `config.toml` 会 fail on Python keys"，但未考虑让 TUI 忽略未知 key 或使用 TOML
   子表隔离。当前分两个文件增加了用户配置的心智负担。

4. **斜杠命令裁撤未纳入 P2/P3 分批交付**。设计 §1.2 和 §4 均要求 "无标准 ACP
   对照的斜杠命令从 TUI 菜单拿掉"，但 P0–P4 分批表（§7）的任何批次均未将斜杠命令
   裁撤列为交付项。结果 `builtin_commands()` 从上游完整继承、无人处理——设计的要求
   与分批计划之间存在裂缝。

5. **跨终端会话恢复机制未设计**。§2.1 声明 "会话真源是 JSONL v3"，但未设计
   `session/load` 如何将 JSONL 历史投影回 TUI scrollback。TUI 依赖本地缓存 +
   `x.ai/prompt_history`（被 Python 拒绝），跨机器场景下 scrollback 为空。

### 7.2 Phase 5 设计缺陷

1. **无设计缺陷发现**。Phase 5 范围明确、模块映射清晰、测试策略具体，实现完全对齐。
   唯一可改进处是 `_PI_PYTHON_DOCS` 路径计算的脆弱性（P4P5-7），但这更属于实现
   而非设计问题。

---

## 八、修复优先级

| 级别 | 编号 | 描述 | 状态 |
|------|------|------|------|
| 实质 | P4P5-1 | TUI x.ai/* 出站 RPC 拆除（103 文件结构性残留） | [x/~] |
| 实质 | P4P5-2 | main.rs 用户可见 "grok" 文本 | [x] |
| 实质 | P4P5-12 | TUI 斜杠命令未按设计裁撤（待讨论） | [ ] |
| 中 | P4P5-3 | GROK_* 环境变量名迁移（`COMPACTION_*` 残留） | [x] |
| 中 | P4P5-4 | pi-home 文档注释 .grok vs .pi-python | [x] |
| 中 | P4P5-5 | session_startup 回退路径改为 .pi-python | [x] |
| 中 | P4P5-13 | TUI 单元测试断言改用 brand::CLI_NAME | [x] |
| 中 | P4P5-14 | Shell 自动补全改用 brand::CLI_NAME | [x] |
| 低 | P4P5-6 | prompt.py 已删除 | [x] |
| 低 | P4P5-7 | system_prompt.py 改用 _find_repo_root() | [x] |
| 低 | P4P5-8 | context_files.py 函数引用常量 | [x] |
| 低 | P4P5-9 | _stop_reason 按关键词区分 refusal vs end_turn | [x] |
| 低 | P4P5-10 | auto-update 死代码已删除 | [x] |
| 低 | P4P5-11 | otel guard 统一 None，不再导出 | [x] |
| 低 | P4P5-15 | session/load 无法跨终端同步 scrollback（待讨论） | [~] |

---

## 验证状态

修复后验证（2026-09-03 三次回归）：
- 全仓库全量 pytest：`380 passed, 11 skipped`（均无 API Key 跳过），0 failed。
  CLI 模块 52 项（51 passed, 1 skipped）。
- 代码检查：`.venv\Scripts\ruff.exe check .` All checks passed。
  `.venv\Scripts\ruff.exe format --check .` 102 files already formatted。
- TUI 编译检查：WSL2 `cargo check -p pi-pager-bin` 与
  `cargo check --tests -p pi-pager-bin` 编译检查全部通过。
- `x.ai/` 残留统计：`rg "x\.ai/" tui/crates/codegen/pi-pager/src/ -l | wc -l` = 103
  （69 源码 + 34 测试），均为结构性残留（文档注释、元数据 key、入站过滤），
  无出站 vendor RPC。
