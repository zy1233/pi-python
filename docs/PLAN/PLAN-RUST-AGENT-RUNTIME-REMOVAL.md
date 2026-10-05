# Rust Agent Runtime 剥离计划

> 状态：r5。r2 的计划稿之上，**已拆除 Rust runtime 并恢复 `/model`（r3）；r4 补了阶段 0 的 Linux CI 与基线工具、清掉 leader 的 UI 状态残留、完成 1.R3 的命名；r5 在 Linux CI 上跑通并启用了基线，执行入口审计（0.6）并完成阶段 A（A.3、A.4）——又删了约 23 万行 Rust、8 个 crate，消费者构建 0 告警**。分支已 push，draft PR [#7](https://github.com/zy1233/pi-python/pull/7)；执行记录见 §10。§1–§9 保留 r2 原文作为决策依据，与 §10 冲突处以 §10 和 ADR3（r3）为准。负责人：待指派。
> 基线：分支 `codex/rust-agent-runtime-removal-plan`；r2 / r3 的比较基线是 HEAD `07a3574`，r4 / r5 的提交见 §10。
> 验证方式：r2 为静态分析（源码阅读、`Cargo.toml` 解析、模块级引用统计，复现方法见附录 B）；r3 起拆除结果由 `cargo check`（macOS 本机，消费者构建 `pi-pager-bin` / `pi-pager-minimal` / `pi-update` 及 `pi-shell` / `pi-pager` 的 `--tests` 类型检查）、`cargo test`（触及的 crate）、Python 侧 pytest，以及伪终端里 `zypi` ↔ `pi_agent_cli` ↔ OpenRouter 的真实会话（含 `/model` 切换）验证。标「需实测」的其余结论仍是静态推断；Linux / Windows 的 `cfg` 代码本机无法编译，见 §10.4。
> 与既有文档的关系：承接 [Phase 4 设计](../specs/2026-08-25-phase4-coding-agent-cli-design.md) §3「第一轮允许 pager 继续链接 `xai-grok-shell`……变瘦不是迁入前提」和 [`AUDIT-PHASE4-PHASE5.md`](../AUDIT/AUDIT-PHASE4-PHASE5.md) 的「TUI 瘦身长期里程碑」，是 Phase 4 的第二轮。与既有决定的张力见 §3。

## 1. 目标与结论

**目标**：`zypi` 的 Rust 代码只承担终端 UI、ACP client / stdio transport、输入输出呈现和 UI 所需的本地服务。模型请求、agent turn、工具调度、session transcript、compaction、skills 与 system prompt 全部由 Python（`pi_agent_cli` → `pi_agent_harness` → `pi_agent_core`）负责。

剥离边界以**产品运行路径与依赖图**为准，不按 crate 名直接删除。

| 问题 | 结论 |
|---|---|
| 目标架构是否合理 | **合理。** 「Rust = ACP Client + UI，Python = ACP Agent + runtime」就是 Phase 4 已确立的边界，本计划只是把它落实为代码层面的瘦身。需要补的不是方向，而是四件事：把终态拆成「行为层 / 依赖图层」两层（§2.1）；补上协议所有权、能力驱动 UI、数据真源、安全边界等七条原则 P1–P7（§2.2）；`pi/` 扩展由「保留或迁移」改为**准入制**（P2、ADR4）；会话数据改为**纯 ACP**（ADR1）。 |
| 是否与现有架构冲突 | **方向不冲突**，有 2 处既有决定需要修订、3 处需要落实（§3）：支持面范围（原稿「保留或迁移」会重开 Phase 4 已划为非目标的范围）；Phase 4 §1.3 #2「不抽纯 pager crate」被部分取代；会话真源、`config.toml` 兼容、method-not-found 三项已有的口径需要落实到代码。Python 的 `pi_agent_core` / harness 与 AGENTS.md 的不变量不受影响。 |
| 改动如何 | **行为层**：删除约 14.5 万行 Rust（`pi-shell` 非测试代码的 43%、`pi-sampler`、pager 死代码），编辑 24 个存活的 `pi-shell` 单元；Python 只动 `pi_agent_cli` 的 ACP 投影。**依赖图层**（`async-openai` 离开 `cargo tree`）要拆 `pi-tools` / `pi-sampling-types` 的耦合，量级更大、收益待测，列为可选阶段 D。详见 §5。 |

## 2. 目标架构

### 2.1 两层终态

| 层 | 终态 | 判定 | 本计划 |
|---|---|---|---|
| 行为层 | 运行时不存在 Rust agent、模型请求、session actor、leader；Python 是唯一 agent | 静态调用图 + 运行时 agent child 为 Python | **承诺**（阶段 A、1、B） |
| 依赖图层 | `cargo tree -p pi-pager-bin` 不含 provider 栈 | `cargo tree -i <crate>` 逐项无匹配 | `pi-sampler` 承诺（阶段 B）；`async-openai` 可选（阶段 D） |

行为层大半已经成立（默认路径已是 embedded Python ACP），剩下的主要是删除不可达的 Rust runtime 及其引用。依赖图层更贵：provider 栈的类型渗透在 `pi-shell` 的 28 个存活单元（46 个文件、169 处）、`pi-tools`、`pi-sampling-types` 里。

### 2.2 架构与原则

```text
zypi（Rust）                                     pi_agent_cli（Python，通用 ACP agent）
  ├─ 终端、编辑器、scrollback、渲染                 └─ pi_agent_harness → pi_agent_core
  ├─ ACP client、stdio 子进程桥、权限弹窗                  → LangChain StreamFn
  ├─ OS 沙箱（spawn 子进程之前施加）
  └─ TUI 本地服务（config.toml、主题、trust、        ◄── 标准 ACP + 准入登记的 pi/ 扩展 ──►
       剪贴板、@ 本地列目录）
```

| # | 原则 | 要求 |
|---|---|---|
| P1 | 单一运行时 | 今天 Python 是唯一 agent runtime。**源自 grok 的 Rust runtime 已拆除**：`zypi` 进程内不创建 Agent、不发起模型请求、不执行工具循环，也不提供 fallback；Python 启动失败必须清楚报错。**任何 runtime（包括将来的 pi-rust）都必须作为独立的 ACP agent 位于 ACP 之后**，不得回到 TUI 进程内——ACP 是唯一接缝，TUI 对 agent 实现保持无感（P3 能力驱动）。 |
| P2 | 协议所有权 | 线上协议 = 标准 ACP + 登记在册的 `pi/` 扩展。准入须同时满足：① 无标准等价（以 ACP 现行 schema / RFD 为准，如模型选择走 Session Config Options，`session/set_model` 已被上游移除）；② Python 侧有生产者与测试；③ 在 `docs/specs/` 登记方法名、方向、schema、能力键；④ wire 类型只定义一次，放 `pi-acp-lib`（或同级协议 crate），不留在 runtime crate；⑤ 经 `initialize` 协商，对 Zed 等第三方客户端无害——Python agent 仍是通用 ACP agent。 |
| P3 | 能力驱动 UI | 入口由 agent 公布的能力决定（`initialize` capabilities、`configOptions`、`available_commands_update`）；未公布则入口不出现，而不是出现后报「not supported in standard ACP」。现状的静态白名单（`PI_STANDARD_SLASH_NAMES`）与 19 处降级 stub 是过渡形态。 |
| P4 | 数据真源在 Python | Rust 不读写 session 文件（JSONL v3 是唯一真源）；session 列表、恢复、标题一律走 ACP。TUI 本地缓存若保留，必须可丢弃。 |
| P5 | 安全边界 | OS 沙箱由 Rust 进程在 spawn 子进程**之前**施加（子进程继承，需实测）；工具权限决策走 ACP `session/request_permission`；LLM 凭据只存在于 Python 环境。`pi-sandbox` 与沙箱配置解析必须保留。 |
| P6 | 生命周期显式 | 子进程启动失败、取消、退出有明确协议（`session/close` → 关闭 stdin → 宽限 → SIGTERM → SIGKILL），不残留 Rust session actor 的 flush 语义。 |
| P7 | 契约可测 | Rust ACP crate / schema 与 Python SDK / schema 的配对写入文档并随升级复核；双边契约由不依赖 PTY 的 ACP 层测试（Rust client ↔ Python mock agent）守住；任何删除之前先有基线。 |

### 2.3 职责归属

| 领域 | Rust `zypi` | Python `pi_agent_cli` |
|---|---|---|
| 渲染、输入、键位、主题 | ✔ | — |
| ACP | Client、子进程桥 | Agent（通用，同时服务 Zed 等） |
| 模型与凭据 | 只展示 Python 公布的信息 | ✔ `agent.toml`、环境变量 |
| agent turn、工具、compaction、skills、system prompt | — | ✔ |
| session 数据 | 经 ACP 访问 | ✔ JSONL v3 |
| 权限 | 弹窗与回传 | 触发与策略（bash / edit / write） |
| OS 沙箱 | ✔（spawn 前） | — |
| 配置文件 | `config.toml`（仅 Rust） | `agent.toml`（仅 Python） |

## 3. 与现有架构的关系

| # | 既有决定（出处） | 本计划的影响 | 性质 / 处理 |
|---|---|---|---|
| 1 | Rust = ACP Client，Python = ACP Agent；线上只走标准 ACP，不实现 `x.ai/*`（Phase 4 §1.1 / §1.2，DESIGN §8.1 / §8.3） | 一致。`pi/` 扩展已在用（`pi/session/delete`、`_meta` 的 `pi/currentModelId` 等），缺的是准入规则 | 一致；P2 写入 DESIGN §8 |
| 2 | 斜杠菜单只留 `/new` `/resume` `/quit` 与本地 chrome；`/model` 等无标准对照者拿掉（Phase 4 §1.2 / §4，DESIGN §8.3）——**r3：`/model` 有标准对照（Session Config Options），按用户决定恢复，见 ADR3**；`TUI-AND-CODE-AGENT.md`「What is NOT included」已声明移除 xAI 登录、`x.ai/*`、`agent` 子命令、`/loop`、`/share`、dashboard、auto-update、marketplace | 原稿「`pi/` 扩展保留或迁移」「补齐 MCP / queue / compaction / subagent」会**重开**已划出的范围。代码与文档也已不一致：白名单含 `model`，r2 时 Python 无法服务它（§4.4；r3 已补齐）；dashboard 文档称已移除，`views/dashboard/` 仍在 | **需修订**：默认「降级 / 隐藏 / 删除」，新增能力须过 P2；`/model` 见 ADR3 |
| 3 | §1.3 #2「整树迁入 `tui/`，不抽纯 pager crate」；§3「第一轮允许 pager 继续链接 `xai-grok-shell`」 | 本计划即第二轮。#2 的理由（pager 依赖 shell / tools / agent / workspace）对 shell 的 runtime 部分不再成立，对 `pi-tools` / `pi-workspace` 仍成立 | **需修订**：在 Phase 4 spec 增补修订记录，标明 #2 被部分取代 |
| 4 | 会话真源 = AgentHarness JSONL v3，TUI 本地缓存不是真源（DESIGN §8.1） | 一致，但 pager 仍按 Rust 布局 `updates.jsonl` 读本地 session，而其写入方（Rust session actor）正是要删的部分 | **需落实**：ADR1 |
| 5 | `pi -p` = 纯 Python headless，不复用 grok headless（DESIGN §8.3） | 一致。原稿「`pi -p` 与 headless 委托 Python ACP」措辞不准：`-p` 是直接子进程、不走 ACP；真问题是旗标被静默丢弃、`--sandbox` 在派发之后才生效（§4.1） | 一致；措辞更正，落实为 `-p` 旗标契约 |
| 6 | 配置分属：`agent.toml`（Python）/ `config.toml`（仅 Rust 的 grok 格式）（DESIGN §8.4） | 删除 `agent::config`（约 90 个字段）会影响既有用户的 `config.toml` | **需落实**：ADR5 |
| 7 | 「TUI 不得在 method-not-found 上 panic」（Phase 4 §4） | pager 会发送 Python 不路由的方法（`session/set_model`、`session/set_mode`） | **需落实**：阶段 1 + 契约测试 |
| 8 | AGENTS.md 不变量（事件契约、终止语义、StreamFn、Usage 等） | 不触及；`pi_agent_core` / harness 不改 | 一致 |

## 4. 源码事实基线

### 4.1 运行路径

- **默认路径**：`acp::connect()` → `spawn_grok_shell()` → `pi_agent_command()`（`PI_AGENT_COMMAND` / `[agent].command` / `PI_PYTHON` / `python3 -m pi_agent_cli`）→ stdio 桥（`acp/mod.rs:181-227`，`acp/spawn.rs:179,235,383`）。子进程只注入 `PI_HOME`，`stderr` 继承，`kill_on_drop(true)`（`spawn.rs:411-413`），取消后 `start_kill()`（`:503`）。`spawn_grok_shell`、`SpawnedAgent` 等名称与注释仍是「in-process grok-shell」语义（r4 已改名并重写注释，见 1.R3）。
- **`pi -p`**：直接子进程，不走 ACP（`pi-pager-bin/src/main.rs:404-458`，`:474` 在 `async_main` 之前派发并退出）。只转发 `-p`、`--prompt-json`、`--prompt-file`、`--cwd`、`--system-prompt(-file)`、`--rules`、`--append-system-prompt-file`、`--no-context-files`；`PagerArgs` 的 `--model`、`--output-format`、`--json-schema`、`--reasoning-effort`、`--resume` / `--session-id`、`--yolo`、`--allow` / `--deny`、`--permission-mode`、`--agents-json`、`--max-turns`、`--worktree`（`app/cli.rs:117-373`）被静默忽略；`apply_sandbox`（`main.rs:612`）在派发之后，`-p --sandbox` 实际**没有沙箱**。因此 `main.rs:667-731` 的 `headless::run_single_turn` 整块与 `headless.rs` + `headless/`（约 7.5k 行）在产品内不可达。
- **leader**：默认关闭，且**从未随已发布版本暴露**——`agent` / `leader` 子命令只存在于未发布提交 `730fa18`–`ace8852`（2026-08-26 至 08-28）；`v0.2.0`–`v0.4.0` 的 `Command` 均只有 6 个变体（`Doctor / Wrap / Export / Version / Completions / DiskUsage`，`app/cli.rs:8-49`），`main.rs` 无 `run_*` 调用。`--leader` 只会 spawn 一个必然失败的 `zypi agent leader`（`pi-shell/src/leader/mod.rs:1687-1692`），超时后回退 embedded（`app/mod.rs:955-980`）。
- **死代码**：`spawn_agent_thread_direct`（`spawn.rs:514-515`，`#[allow(dead_code)]`，构造 `MvpAgent`）；8 个无入口模块 `sessions_cmd` / `memory_cmd` / `plugin_cmd` / `mcp_cmd` / `trace_cmd` / `share_cmd` / `worktree_cmd` / `models`（合计 4,648 行）；`main.rs:34` 导入的 `run_headless` / `run_leader` / `run_stdio_agent`。`pi-pager-bin`、`pi-pager`、`pi-shell`、`pi-workspace` 四个 crate 根都有 `#![allow(unused_imports, unused_variables, unused_mut, unreachable_code, dead_code)]`，且 `pi-pager` 是库 crate、死模块为 `pub mod`，编译器**无法**验证清理。
- **斜杠菜单**：`builtin_commands()`（`slash/commands/mod.rs:79-165`）仍注册约 70 个命令，但三处构造点无条件启用白名单（`app/app_view.rs:1502`、`app/agent_view/session.rs:404`、`views/dashboard/state.rs:1336`）：`PI_STANDARD_SLASH_NAMES` = `new resume quit help theme settings multiline model home`（`slash/registry.rs:33-43`）；白名单外的命令既无触发器也不可派发（`registry.rs:615-620`）。ACP 公布的命令（skills）不受过滤。

### 4.2 Rust runtime 与耦合面

- **provider 栈**：`async-openai`（`tui/Cargo.toml:4` 的 `[patch.crates-io]` 指向 GitHub fork 的固定 rev）的直接使用者是 `pi-sampler`、`pi-sampling-types`、`pi-shell`、`pi-tools`；`pi-sampler` 的直接使用者是 `pi-shell`、`pi-telemetry`、`pi-http`——后两者只为 re-export `OriginClientInfo`（`pi-telemetry/Cargo.toml:39` + `src/http.rs:8`，`pi-http/Cargo.toml:17` + `src/lib.rs:100`）。自 `pi-pager-bin` 可达 82 个内部 crate，其中 67 个不经过 `pi-shell`；整体剪掉 `pi-shell` 最多释放 14 个。
- **pager 对其它 crate 的真实用量**（非测试代码）：`pi_shell::util` 249 处 / 44 文件（`util::config` 159、`with_locked_stderr` 45、`grok_home` 22）、`session` 110 / 37、`agent` 72 / 30（`agent::config` 47 / 26）、`extensions` 61 / 28（`notification` 28、`session_search` 18——ACP 扩展的 **wire 类型定义在 shell 里**）、`config` + `auth` 43 / 30、`sampling` 26 / 17（实际只用 18 个符号）。`pi_tools` 126 处 / 56 文件，约 55 个不同路径，几乎全是类型与小工具（`grok_home`、`ask_user_question::*`、`SkillInfo` / `SkillScope`、`SessionMode`、`ConfigSource`、`output::*`）；`pi_workspace` 60 / 28（`permission` 43）；`pi_agent` 16 / 3（10 处在死的 `plugin_cmd`）；`pi_sampling_types` / `pi_hooks` / `pi_mcp` 无直接引用。`pi-pager-render`、`pi-pager-diff` 等渲染 crate 也直接依赖 `pi-tools`。
- **`MvpAgent` 的非 spawn 引用**：`app/mod.rs:658` `warm_async_http_client()`（每次启动预热 Rust provider HTTP 客户端）；`views/agents_modal.rs:739` `MvpAgent::resolve_agent_definition`。
- **`agent::config`**：`Config` 约 90 个 pub 字段（`agent/config.rs` 5.5k 行），`spawn_grok_shell` 只读其中的 `grok_com_config`（`spawn.rs:186`），`acp::connect()` 却先构建并 resolve 整个 `AgentConfig`（`acp/mod.rs:181-210`）；沙箱配置 `SandboxSettingsConfig` 也在其中（`config/mod.rs:1421` `apply_sandbox`）。
- **session 存储**：Rust 布局 `sessions/<encoded_cwd>/<session_id>/updates.jsonl`，写入方是 Rust session actor（`session/storage/mod.rs:1040-1054`、`storage/jsonl/mod.rs:523`）；Python 布局 `sessions/<timestamp>-<session_id>.jsonl`（JSONL v3，`jsonl_repo.py:54-55`）。pager 约 19 个非测试文件按 Rust 布局**读取**：`export_cmd.rs:25`（无 Python 布局 fallback）、`app/session_startup.rs:775,861,1246`、`app/mod.rs:763`、`recent_dirs.rs:7`、`app/session_title_resolve.rs:140`、`app/effects/mod.rs:2314`、`app/subagent.rs`；只有 `session_startup.rs:788-822` 有一个 Python 布局 fallback。`zypi export <python-session-id>` 静态上大概率不可用（需实测）。

### 4.3 Python 侧（`pi_agent_cli`，共 4,114 行，`agent.py` 472 行）

- **已有**：`initialize`（`load_session`、`session_capabilities.{list,resume,close}`、`prompt_capabilities.image`、`auth_methods=[]`，无 `_meta`）、`new_session` / `load_session`（回放）/ `resume_session`（不回放）/ `list_sessions` / `close_session` / `prompt` / `cancel`、`ext_method("pi/session/delete")`、`ext_notification`（权限模式）、`available_commands_update`（skills）、`session/request_permission`（仅 bash / edit / write，选项仅 allow-once / reject-once，`permissions.py:11-23`）；`_meta` 键 `pi/currentModelId`、`pi/currentModelDisplayName`、`pi/provider`（`agent.py:259-270`）。
- **缺口**：`set_session_mode`、`set_config_option`、`authenticate`；`mcp_servers` 形参被忽略（`agent.py:127,147,184`）；`list_sessions` 的 title 是合成的 `"{created_at} ({id8})"`、`updated_at = createdAt`、忽略 `cursor`（`:164-177,388-390`）；无 queue / interjection、无 ACP 级 compaction（仅 `factory.py:147,156` 由 `max_turns >= 30` 推导 `auto_compact`）、无 subagent、无 status line；`__main__` 无 EOF / 信号收尾。
- **harness 已具备、只缺 ACP 投影**：`steer`、`follow_up`、`set_model`、`set_thinking_level`、`abort`、`close`、`compact`、`navigate_tree`。补齐只发生在 `pi_agent_cli`。

### 4.4 协议与版本

- **配对**：Rust `agent-client-protocol 0.10.4`（`unstable`）+ schema `0.11.4`（`tui/Cargo.toml:107`、`Cargo.lock`）；Python SDK `0.12.1`，捆绑 schema `v1.19.0`（`acp/meta.py`）。
- **模型选择**（r2 基线；r3 已落地，见 §10.2）：上游已移除从未稳定的 `session/set_model`，模型选择走 Session Config Options（`configOptions` 中 `category: "model"` + `session/set_config_option`，[RFD](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/docs/rfds/session-config-options.mdx)）。r2 基线时 pager 仍发 `SetSessionModelRequest`（`effects/mod.rs:1126`），候选项来自 `initialize` 响应的 `_meta.modelState`（`acp/mod.rs:562-566`）；Python 既不路由该方法也不提供 `modelState`，pager 对 `configOptions` 零引用，所以 `/model` 可见却不可用。
- **模式**：`SetSessionModeRequest`（`effects/mod.rs:1005,1025`）同样没有 Python 实现。
- **路由面**：SDK 0.12.1 的 router 路由 `session/{new,load,list,close,set_mode,set_config_option,prompt,fork,resume}`、`authenticate`、`session/cancel`，扩展方法按 ACP 规范以 `_` 前缀分发给 `ext_method`；`session/delete` 虽有 schema 与能力位 `sessionCapabilities.delete`，SDK **没有路由**，所以 Python 用 `pi/session/delete` 扩展（`agent.py:236`）。
- **`pi/*` 没有生产者**：`pi-shell/src` 有 716 处 `x.ai/…` 字面量、0 处 `pi/…`；pager 非测试代码用到 8 个 `pi/*` 名称（`currentModelId`、`currentModelDisplayName`、`provider`、`session`、`session/delete`、`session/update`、`tool`、`yolo_mode_changed`），另有 36 个只出现在测试里（`pi/queue/changed`、`pi/mcp/*`、`pi/scheduled_task_*`、`pi/session/interjection`、`pi/task_*` 等）；Python 只涉及 4 个。出站 `x.ai/*` 被 `pi-acp-lib/src/channel.rs:18,72` 丢弃，入站被 `pi-pager/src/acp/vendor.rs:8-11` 过滤。每一个 `pi/*` 的 Python 实现都是**新设计**，不是「补齐」。
- **`initialize._meta`**：pager 读取 `modelState`、`grokShell`、`availableCommands`、`cancelRewind`、`sessionRecap`、`feedbackTraceOffer`（`acp/mod.rs:527-590`），Python 一个都不给。

### 4.5 验证基础设施与文档

- `.github/workflows/ci.yml` 只有 `lint`（ruff）与 `test`（pytest），**没有 cargo job**；`release.yml` 仅 `v*` tag 触发 `cargo build --profile release-dist -p pi-pager-bin`（Linux + macOS arm64），不跑测试。PTY e2e crates 在 fork 时已删（`tui/NOTICE:39-40`）；`pi-pager/tests/scenarios/` 的 46 个 YAML 场景中 45 个依赖 Rust 侧 `mock:` 模型且无 runner；Rust 代码里没有任何测试 spawn Python agent 或 mock ACP agent。没有删除前的基线。Rust 测试量级（`#[test]` 计数，近似）：`pi-pager` 9.3k（93 ignored）、`pi-shell` 6.6k（122 ignored）、`pi-tools` 3.1k、`pi-workspace` 1.9k。toolchain 固定 1.94.0（`tui/rust-toolchain.toml`），AGENTS.md 约定在 WSL 验证。
- 陈旧文档：内嵌 user-guide（`pi-pager/docs/`，38 个 md，仍是 Grok 原文，命令名 `grok`，`15-agent-mode.md` 描述 `grok agent … leader`）经 `include_str!` 编入二进制，每次启动由 `extract_user_guide_docs`（`main.rs:508`）写入 `<grok_home>/docs/user-guide/`，Python 侧没有消费者，`app/status_line_tests.rs:358,403,450` 还把 `25-status-line.md` 当测试夹具；`tui/NOTICE` 仍写「Product binary is `pi` (`pi-grok-pager-bin`)」；`packages/pi-agent-cli/AGENTS.md` 称 `config.toml` 含 Python-only 键会破坏 TUI 解析（Rust 侧未找到 `deny_unknown_fields`，需实测）；`docs/TUI-AND-CODE-AGENT.md` 把 MCP 列在 TUI 能力里（Python 忽略 `mcp_servers`），且 ACP 链接指向 `anthropics/agent-protocol`。

## 5. 改动规模

**口径**：`pi-shell/src` 非测试代码 265.6k 行 / 234 个单元（深度 ≤ 2 的模块路径）；含测试文件共 379.6k 行（其中测试文件 113.8k），另有集成测试 20.3k 行 / 55 文件。「可删」= runtime 禁用集 + 禁用后不再被 pager 根引用可达的单元。**模块级、类型引用也算边，是上界估计**；须由阶段 A / B 的编译结果校正。

| 对象 | 规模（非测试） | 处理 |
|---|---|---|
| runtime 禁用集（24 个单元） | 78.6k 行：`session::acp_session_impl` 40.5k、`agent::mvp_agent` 15.8k、`leader` 8.7k（8 个单元）、`agent::subagent` 8.1k、`agent::app` 2.1k、shell `tools` 2.4k、`sampling` 1.0k | 删除 |
| 连带不可达（70 个单元） | 34.4k 行：`extensions::suggest` 3.8k、`session::worktree_pool` 2.4k、`inspect` 2.3k、`session::compaction` 2.3k、`session::acp_conversion` 1.3k、`session::goal_strategist` 1.3k 等 | 删除 |
| **`pi-shell` 可删合计** | **113.0k 行（43%）**；存活 152.6k 行（57%） | |
| `pi-sampler` | 12.5k 行（另有测试 1.8k） | 删除 |
| pager 死代码 | 约 19.8k 行：8 个无入口模块 4.6k、`headless*` 7.5k、白名单外斜杠命令模块约 7.7k | 删除 |
| **Rust 删除合计** | **约 145k 行**，另有与之对应的测试（含 5 个 `test_leader_*.rs`、`app/leader_cluster`） | |
| 需编辑的 cut point | 存活的 `pi-shell` 单元里引用 runtime 禁用集的有 **24 个**：`agent`、`agent::config`、`agent::config_model_override_parse`、`agent::init`、`agent::model_providers`、`agent::models`、`agent::server`、`config`、`extensions::notification`、`remote::client`、`remote::pull`、`session::acp_session`、`session::agent_rebuild`、`session::feedback_manager`、`session::goal_planner`、`session::handle`、`session::helpers`、`session::image_describe`、`session::memory`、`session::persistence`、`session::storage`、`session::summary`、`upload::trace`、`util::config`。其中 provider 栈类型渗透 28 个单元 / 46 文件 / 169 处。阶段 A 删除 pager 死代码后 cut point 仍是 24 个（只多释放约 1.0k 行），所以阶段 B 的工作量不会被阶段 A 摊薄 | 编辑 |
| pager 侧需编辑的引用 | `MvpAgent` 8 文件 / 12 处、`run_*` 1 / 5、leader 7 / 27、`sampling` 17 / 26、shell `tools` 4 / 4 | 编辑 |
| Python | 仅 `pi_agent_cli` 的 ACP 投影（§7 阶段 1），不改 `pi_agent_core` / harness | 新增 / 修改 |

**第二梯队（阶段 D，决策门控）**：存活的 152.6k 行里，`session`（68.3k）与 `auth`（18.0k）最大。ADR1（纯 ACP）落地后 `session::{storage,persistence,unified_list}` 及 `relay` 约 16.6k 行不再可达；放弃 xAI auth 层（Phase 4 §4 已要求跳过登录，但 pager 仍有 `GateInfo` / `AuthMeta` / `AuthManager` 约 30 处 / 14 文件引用）约 18.1k 行。两者合计存活 152.6k → 117.9k 行。

## 6. 决策记录

| ID | 决策 | 默认值 | 可推翻条件 |
|---|---|---|---|
| ADR1 | 会话数据源 | **纯 ACP**：pager 不读 session 文件，`--continue` / `--resume` / `--session-id` 冲突检查 / 按标题 resume / recent dirs 全走 `session/list` + `session/load`；`zypi export` 改为经 ACP 回放导出，或删除该子命令（阶段 1 定）。代价是列表要等 Python `initialize` 完成（冷启动延迟），先渲染欢迎页再填充。备选：pager 读 JSONL v3（重复格式知识）；Python 另写 Rust 布局 sidecar（耦合最强）——均不推荐 | 阶段 0 基线实测冷启动列表延迟不可接受且无法预热缓解 |
| ADR2 | leader | **放弃**。从未随版本发布；将来要多会话共享进程另立设计 | 无 |
| ADR3 | 支持面基线 | 以 Phase 4 §1.2 / §4、DESIGN §8.3 和 `TUI-AND-CODE-AGENT.md`「What is NOT included」为准，默认**降级 / 隐藏 / 删除**；新增须过 P2。具体：**`/model` 恢复（r3，推翻 r2 的「从白名单移除」）**——用户明确要求保留；它有标准对照（ACP Session Config Options），不属于 Phase 4 §1.2 所说「无标准对照者」，所以不违反 P2。实现：Python `PiAcpAgent` 在 `session/new|load|resume` 响应里公布 `category: "model"` 的 `select` 配置项并路由 `session/set_config_option`；pager 读 `configOptions` 生成模型目录、以 `session/set_config_option` 切换，agent 未公布该配置项时退回旧的 `session/set_model`（仅作为兼容路径，不是目标形态）；文档已声明移除的 dashboard、`/loop`、`/share`、marketplace、auto-update、xAI 登录相关 UI 与模块，仍有入口的一律删除；其余入口（键位、状态栏、模态、欢迎页）由阶段 0 入口审计逐项判定 | 入口审计发现用户必需功能 |
| ADR4 | `pi/` 扩展 | **先冻结，再准入**。① Python 已有生产者（`pi/session/delete`；`_meta` 键 `pi/currentModelId`、`pi/currentModelDisplayName`、`pi/provider`）→ 保留并登记；② pager 非测试代码在用但无生产者 → 逐项「实现（过准入）｜降级｜删 UI」；③ 仅测试里出现的 36 个 → 默认删除解码与测试。`session/delete` 待 SDK 补路由后迁到标准方法 | 有准入申请 |
| ADR5 | 旧 `config.toml` | **忽略未知键 + 首次启动告警一次**；删除 `agent::config` 字段前先出「保留 / 废弃字段清单」 | 阶段 0 实测发现 Rust 侧严格解析 |
| ADR6 | Rust 验证环境 | 权威环境 = CI Linux runner（`tui/rust-toolchain.toml` 的 1.94.0，带缓存）；本地仍按 AGENTS.md 用 WSL；macOS 仅 release 构建验证 | 无 |
| ADR7 | 沙箱 | **保留**，spawn 前施加；`SandboxSettingsConfig` 从 `agent::config` 抽成独立小模块；`-p` 路径要么在沙箱施加后派发，要么对 `--sandbox` 明确报错——不允许静默无沙箱 | 无 |

## 7. 分阶段计划

依赖关系：`0 → {A, 1} → B → C →（可选）D`。A 与 1 互不依赖，可并行；B 依赖 A 和 1 中的 ADR1。每个阶段拆成若干 PR，每个 PR 独立可回滚；纯删除型 PR 以 `git revert` 回滚。

### 阶段 0：基线与门禁（先于任何删除，不改产品行为）

- [x] 0.1（r5 已做：Linux 上跑通且阻塞——`TUI CI` run [37379203134](https://github.com/zy1233/pi-python/actions/runs/37379203134) 全绿，门禁、消费者构建、`--workspace --tests`、8 个 suite；`enforce = true`、`enforce_workspace_tests = true`）**Rust CI job**：`cargo check -p pi-pager-bin` + 选定 crate 的 `cargo test`（Linux，ADR6）。（r4 已写好 `.github/workflows/tui-ci.yml`：`check`、`test`、手动的 `release-baseline` 三个 job，执行器是 `scripts/tui_baseline.py`，actionlint 与单测通过，本机 macOS 全部跑通；**尚未在 Linux 上运行**，所以不勾选，第一次运行之后的收尾步骤见 [`docs/baselines/tui.md`](../baselines/tui.md) §5。）
- [ ] 0.2（r5：Linux 一栏的依赖图、告警、测试数已回填，告警清零；**仍缺** release 体积 / 冷编译耗时 / `--version` 延迟，要手动触发 `release_baseline`）**基线报告**入库：测试通过 / 失败 / 忽略数、`cargo tree -p pi-pager-bin` 节点数、release 二进制体积、冷编译与启动耗时、去掉全局 allow 后的告警数。（r4 已入库 [`docs/baselines/tui.md`](../baselines/tui.md)：拆除前（v0.4.0）与拆除后（macOS）的依赖图节点数、告警数、8 个 suite 的测试数、v0.4.0 的 release 体积与构建耗时；**Linux 一栏、拆除后的 release 体积 / 冷编译耗时 / `--version` 延迟待首次运行回填**，「到欢迎页」的启动耗时需要 PTY 载体，未测。「去掉全局 allow 后的告警数」随 A.4 做。）
- [ ] 0.3 **ACP 契约 / e2e 载体**：Rust client ↔ Python mock agent（脚本化 `stream_fn` 的 `PiAcpAgent` stdio 进程），覆盖 initialize、new、prompt 流式、权限往返、cancel、list、load、Python 缺失时报错、子进程退出；不依赖 PTY（PTY 烟测可选）。
- [x] 0.4（r4 已做）**deny-list**：硬性 `pi-sampler`；条件性 `async-openai`、`pi-sampling-types`（由阶段 D 决定）；对 `pi-tools`、`pi-agent`、`pi-workspace`、`pi-hooks`、`pi-mcp` 逐个写「保留理由或缩减方案」。（`pi-sampler` 及 `MvpAgent` / `acp_session_impl` / `SamplerActor` 三个词是 `tui_baseline.py gates` 的阻塞门禁；七个条件性 crate 的直接依赖者、理由与缩减方案见 [`docs/baselines/tui.md`](../baselines/tui.md) §4，依赖者列表每次 `check` 重新生成。）
- [ ] 0.5 **模块级调用图 → support 保留清单**：把附录 B.2 的脚本入库为工具，取代「举例」式清单。
- [x] 0.6（r5 已做：用户选「直接按附录 A 默认执行，不逐项确认」；结果是 A.3 的删除，分类见 §10.7，附录 A 的「r5 结果」列）**入口审计**：从 `Effect` / `Action` 枚举与键位表出发，逐入口标注可达性、所需 ACP 方法、决策（保留｜隐藏｜删除）；覆盖斜杠、键位、模态、欢迎页、dashboard、状态栏，并复用现有 19 处「standard ACP」降级点。
- [ ] 0.7 **磁盘读取点清单**（约 19 个文件）与 ADR1 的落地设计。
- [ ] 0.8 **能力矩阵定稿**（附录 A）与 **ACP 版本配对表**（两端各方法的支持情况、升级策略；Python 侧不得依赖 Rust 端 unstable 方法）。
- [ ] 0.9 **实测结案**：子进程是否继承沙箱；`zypi export <python-session-id>`；`/model` 经 `session/set_config_option` 的真机切换（r3 已用 OpenRouter 实测 Python 侧，见 §10.3；TUI 全屏交互仍需 PTY 实测）；子进程 `stderr` 对全屏 TUI 的影响；退出后是否残留 Python / bash 进程；`config.toml` 的解析严格性。

**退出条件**：CI 绿且基线入库；矩阵、入口审计、deny-list 无「未决」行（有则标 owner 与日期）；0.9 每项有记录。

### 阶段 A：死代码先行（不依赖 Python，零行为变化）

- [x] A.1（r3 已做）删除 8 个无入口模块（4.6k 行）、`headless.rs` + `headless/`（约 7.5k 行）及 `main.rs:667-731`、`spawn_agent_thread_direct`、`warm_async_http_client()`、`main.rs` 的 `run_*` / leader 导入。
- [x] A.2（r3 主体已做，r4 清掉 pager 的 UI 状态残留；仍带 leader 字样的代码是别的东西，见 §10.4）leader 全链路：pager 侧 `--leader` / `--no-leader` / `--leader-socket`、`[cli].use_leader`、`resolve_leader_mode`（`app/mod.rs:435-506`，单测在 `:1917-1937` 一带）、`kill_stale_reachable_leaders`、`connect_via_leader`（`acp/mod.rs:288-420`）及 `app/mod.rs:954-976` 的 leader→embedded 回退、`acp/leader_bridge.rs`、`acp/version_mismatch.rs`、`AcpConnection.leader_status_rx`、`app/leader_cluster`；shell 侧 `leader/`（8.7k 行）与 5 个 `test_leader_*.rs`；内嵌文档中的 leader 段落（8 个文件 22 处）。
- [x] A.3（r5 已做；r3 没做：没有执行入口审计，只删了 runtime 与连带死代码）白名单外的斜杠命令模块（`6983c99`，77 个文件）及 `builtin_commands()` 注册；入口审计判为「删除」的 dashboard（`2bef92d`）、agents / extensions / persona 模态、tasks / 后台 / 定时任务、subagents / workflows / goals、共享 prompt 队列与 steer、MCP 模态 / elicitation / Claude 导入、hooks / plugins / marketplace、rewind / fork / jump / 外部会话、recap / feedback / consent 等，以及 session rename 的整条死链路和恒假的 `chat_mode` 世界。**没做**（有意留着，见 §10.7）：认证 / 计费界面（3d-4，归阶段 D.3）、CLI 旗标瘦身（`--worktree`、`--restore-code` 等，3d-6）、plan 审批 / btw / cta（3f）。
- [x] A.4（r5 已做；r3 没做：消费者构建还有 20 条 `dead_code` 警告）去掉四个 crate 根的 `#![allow(unused_imports, …, dead_code)]` 并清零告警（`4eb9f11`）；死 `pub mod` 降为 `pub(crate)`（pi-shell，`ab77740`）；未用依赖（`fbcaf14`）与无人依赖的 crate（`86ea4c9`、`886d5af`）；`cfg(feature = "local-workspace")` 代码与 Cargo feature（`0e71894`）。基线 `max_warnings` 20 → 0。其余 crate 的 `pub` 瘦身没有做（pi-shell-base 等，见 §10.7 的遗留）。

**退出条件（机检）**：`rg -w MvpAgent tui/crates/codegen/pi-pager tui/crates/codegen/pi-pager-bin` 无命中；无全局 allow 的 `cargo check -p pi-pager-bin` 零告警；测试与基线对比不退化（被删代码的测试除外）；CI 绿。（r3 对照：`rg` ✓；零告警 ✗，仍有 20 条 `dead_code`；基线对比与 CI ✗，阶段 0 未做。**r5 对照**：`rg` ✓；零告警 ✓（Linux CI 与 macOS 都是 0）；基线对比 ✓（`min_passed` 下限已设，被删代码的测试已在清单里按 Linux 数重定基线）；CI ✓。）

### 阶段 1：协议与 Python（依赖 Python）

Python（`pi_agent_cli`）：

- [ ] 1.P1 `list_sessions`：真实 title、`updated_at`（最后活动）、`cursor` 分页（ACP `session/list` 字段，[RFD](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/docs/rfds/session-list.mdx)）。
- [ ] 1.P2 优雅退出：处理 stdin EOF / SIGTERM → 关闭所有 harness，回收工具进程组。
- [ ] 1.P3 `set_session_mode`（或 `configOptions` 的 `mode`）取代仅靠 `ext_notification` 的权限模式切换，映射由矩阵定。
- [ ] 1.P4 `mcp_servers`：显式忽略并在文档声明（Phase 4 非目标），或实现。
- [ ] 1.P5 `initialize` 的 capabilities / `_meta`：只公布 pager 实际需要且已登记（P2）的键。
- [ ] 1.P6 `-p` 旗标契约：以 `__main__` 接受的旗标为权威；每个 `PagerArgs` 旗标逐项「支持｜非 0 退出并报错｜文档删除」。
- [ ] 1.P7 契约测试：mock `stream_fn` 驱动 ACP stdio，断言方法面（含既有的「不注册 `x.ai/`」）。

Rust（pager）：

- [ ] 1.R1 ADR1 落地：移除对 session 文件的读取；`export` 的去向。
- [ ] 1.R2 按能力隐藏 Python 不路由的方法的入口（`session/set_model`、`session/set_mode` 等）；method-not-found 不 panic、不重复报错；`/model` 按 ADR3 恢复（r3 已做，不在隐藏之列）。
- [ ] 1.R3 子进程生命周期（P6）。**r4 已做（命名与提示语，行为不变）**：`SpawnedAgent` → `AgentProcess`（`thread_handle` → `bridge_thread`，`AcpConnection.agent_thread` → `bridge_thread`）、`AgentShutdownGuard` → `AgentProcessGuard`、`spawn_grok_shell` → `spawn_agent_process`（顺手去掉没人用的 `_memory_config` 参数）、`SESSION_FLUSH_GRACE` / `AGENT_JOIN_SLACK` → `AGENT_EXIT_GRACE` / `BRIDGE_JOIN_SLACK`（仍是 10 s + 2 s，`exit_timeout` 的 20 s 预算不变）、`join_agent_thread` → `join_bridge_thread`；慢退出提示从「Finishing session…」改为「Stopping agent…」，超时告警不再声称 SessionEnd teardown 可能不完整，只说 agent 进程可能还在；守卫的文档改为只有 `app::run` 持有。**未做**：① 确定 `stderr` 去向（现为继承，agent 打印的任何东西都会写在 TUI 所在的终端上）；② 优雅退出（1.P2；现在是取消即 `start_kill()`，没有 flush 窗口，所以那 10 s 宽限实际只兜底卡死的回收）；③ 崩溃 / 中途 Ctrl-C / `kill -9 zypi` 之后是否残留 Python 与 bash / MCP 子进程的实测——SIGKILL 不给 Python 回收自己子进程的机会，它们会不会残留没验证过。
- [ ] 1.R4 `-p` 派发顺序与沙箱（ADR7）。
- [ ] 1.R5 解码精简：`initialize._meta` 只读 Python 实际公布的键；`pi/*` 解码按 ADR4 清理。

**退出条件**：ACP 契约 / e2e 通过；矩阵无「未决」行；`-p` 每个旗标都有去向；`zypi` 正常退出、崩溃、Ctrl-C 之后无残留 Python 与 bash 子进程（Linux、macOS 实测，Windows 记录现状）。

### 阶段 B：拆除 Rust runtime（行为层收口）

- [x] B.1（r3 已做：`OriginClientInfo` 在 `pi-telemetry/src/http.rs`）迁出 `OriginClientInfo`，使 `pi-telemetry` / `pi-http` 不再依赖 `pi-sampler`。
- [ ] B.2（r3 换了做法，未新建叶子 crate：`pi_shell::sampling` 留作薄壳，符号仍从 `pi-sampling-types` 重导出；provider 栈没有因此离开依赖图，见阶段 D）把 pager 用到的 18 个 `sampling` 符号迁到不依赖 provider 栈的叶子 crate：对话项类型（`ConversationItem` / `UserItem` / `AssistantItem`）、reasoning-effort 元数据（`ReasoningEffort`、`ReasoningEffortOption`、`REASONING_EFFORT_META_KEY`、`parse_*`、`supports_reasoning_effort_meta` 等）、错误分类与文案（`RATE_LIMITED_ERROR_CODE`、`error_detail_from_data`、`format_rate_limited_user_message`、`http_status_from_error`、`is_free_usage_exhausted_error`、`prompt_usage_from_error`、`stop_reason_for_turn_error`）。
- [ ] B.3（r3 未做）ACP 扩展 wire 类型（`extensions::notification` / `session_search` 等）迁入 `pi-acp-lib` 或同级协议 crate（P2）。
- [ ] B.4（r3 部分：`agent::config` 就地瘦身并接收了 `AuthScheme`，没有抽成独立模块；ADR5 的首次启动告警未做）从 `agent::config` 抽出 pager 实际使用的部分（含 `SandboxSettingsConfig`、`grok_com_config`），其余字段按 ADR5 废弃。
- [x] B.5（r3 已做：编译器验证）处理 §5 列出的 24 个 cut point 里对 runtime / provider 栈的引用。
- [x] B.6（r3 已做，实际规模见 §10.1，与「94 个单元 / 113.0k 行」的静态估计不同）删除 94 个单元（113.0k 行）与 `pi-sampler` crate；清理依赖声明、feature、生成配置、测试 harness 与遗留名称。

**PR 切分**：B.1–B.4 是纯迁移（行为不变）先合；B.5 次之；B.6 的大删除最后。

**退出条件（机检）**：`rg -w 'MvpAgent|acp_session_impl|SamplerActor' tui/crates` 无命中；`cargo tree -p pi-pager-bin -i pi-sampler` 返回「did not match any packages」；`cargo check -p pi-pager-bin` 零告警；测试与基线对比不退化；CI 绿；release 二进制体积不高于基线。（r3 对照：前两项 ✓；零告警 ✗；基线、CI、release 体积 ✗，没有基线可比，也没有测。）

### 阶段 C：验收与文档收口

- [ ] C.1 运行时验收：阶段 0.3 的 mock-ACP e2e 全绿；一次可选的真实模型 smoke，用进程级网络连接核对 `zypi` 进程无到 LLM provider 的外连。
- [ ] C.2 内嵌 user-guide 裁剪或重写（同步 `status_line_tests.rs` 夹具），并停止每次启动的 `extract_user_guide_docs`。
- [ ] C.3 `tui/NOTICE`（产品二进制名、声明 `tui/` 为 hard fork、不再跟随 upstream）；`tui/THIRD-PARTY-NOTICES`（18,898 行，含 `async-openai`）在依赖变化后重新生成并记录方式。
- [ ] C.4 `packages/pi-agent-cli/AGENTS.md` 的 config 约束；`docs/TUI-AND-CODE-AGENT.md`（MCP 归属、ACP 链接应为 [agentclientprotocol.com](https://agentclientprotocol.com)）；Phase 4 spec 增补修订记录（§1.3 #2）；DESIGN §8 增补 P2 准入规则。

**退出条件**：`grok agent`、`leader`、`x.ai`、`MvpAgent` 在文档与代码中的 grep 结果为 0（历史文档除外）。

### 阶段 D（可选，决策门控）：依赖图与 support 层瘦身

入口条件：阶段 B 完成，且基线对比显示编译时间 / 体积 / 维护成本值得继续。

- [ ] D.1 `async-openai` 离开 `cargo tree`：需处理 `pi-tools`（139k 行，pager 仅用约 55 个类型 / 小工具路径，但渲染 crate 也依赖它）与 `pi-sampling-types`（经 `pi-agent`）。二选一：把「工具展示类型」抽到叶子 crate（`pi-tools-api` 仅 715 行，是现成落点），或对 `async-openai` 的用法做 feature-gate。
- [ ] D.2 session 数据层（`session::{storage,persistence,unified_list}`、`relay`，约 16.6k 行）。
- [ ] D.3 xAI auth 层（约 18.1k 行）。
- [ ] D.4 视 `pi-workspace`（101k 行，pager 主要用 `permission`）、`pi-agent`（`agents_modal` 删除后几乎无活引用）的剩余耦合决定是否继续。

## 8. 风险与处理

| # | 风险 | 处理方式 |
|---|---|---|
| R1 | `pi-shell` 是 runtime 与 TUI service 的大杂烩 | 模块级闭包 + cut point 清单；纯迁移 PR 先于大删除（阶段 B 的 PR 切分） |
| R2 | 没有 Rust 回归安全网，验收全部依赖它 | 阶段 0 的 CI、基线、ACP 契约测试先于任何删除 |
| R3 | 残留 UI 入口调用已删 runtime，或发送 Python 不路由的方法 | 入口审计 + 能力驱动 UI（P3）+ method-not-found 契约测试；去掉全局 allow 后由编译器验证 |
| R4 | 误把有入口的代码当死代码 | 入口审计 + A.4 去 allow + e2e；每类删除单独 PR |
| R5 | 子进程生命周期：孤儿 bash 进程、Windows 行为未知 | 阶段 1 的优雅退出协议 + 实测；Windows 先记录现状 |
| R6 | ACP 两端版本偏差 | 配对表 + 升级同步 + 契约测试；不依赖 Rust 端 unstable 方法 |
| R7 | 删除 `agent::config` 字段破坏用户 `config.toml` | ADR5 |
| R8 | 沙箱随 `agent::config` 被误删 | ADR7 + B.4 明确保留 + 测试（含 `-p`） |
| R9 | `release.yml` 仅 tag 触发，中途无法保证 main 可发布 | 阶段 0 的 CI；每个 PR 合入前 `cargo check` |
| R10 | ADR1 之后遗留的 Rust 布局旧会话（`updates.jsonl`）不再可见 | 它们本就没有写入方；在发布说明中记录，不迁移 |

## 9. 不在范围

- 改写 Rust TUI 的渲染或终端交互。本计划只**删减 / 隐藏入口**，不重做 UI。
- 一次性删除整个 `pi-shell`、`pi-agent`、`pi-tools`、`pi-workspace`。
- 移植 `x.ai/*` vendor 私有 RPC 到 Python。
- 改变 `pi_agent_core` 的 agent loop 语义或 LangChain 边界。
- 新增 TUI 功能（MCP、queue、subagent UI 等）——另立计划并过 P2 准入。（`/model` 不在此列：r3 已按 ADR3 经 Session Config Options 恢复。）
- 多会话共享进程（leader 的替代方案）。

## 10. 执行记录（r3、r4、r5）

r3 执行了「拆除 Rust runtime + 恢复 `/model`」；r4 在其上补了阶段 0 的 CI / 基线、清理 leader 的 UI 残留和生命周期命名；r5 把分支推上远端、在 Linux 上启用基线，执行入口审计并完成阶段 A（§10.7）。下表是 r3、r4 的提交，按当时的状态保留（当时都是本地提交、**未 push**），分支是 `codex/rust-agent-runtime-removal-plan`：

| 提交 | 内容 |
|---|---|
| `9d3b4b3` | `pi-agent-cli`：经 ACP session config options 公布模型选项（`/model` 的 Python 侧） |
| `9add266` | 拆除 Rust runtime（682 个文件），`/model` 的 Rust 侧 |
| `bf4b79f` | 本文件 r3 |
| `3117858` | 阶段 0 的 Linux CI、基线工具与报告（0.1 / 0.2 / 0.4） |
| `a648e92` | 清理 leader 的 UI 状态残留（A.2 的尾巴，见 §10.6） |
| `aeaf9b6` | 修掉 3 处已失效的 leader 注释，删 1 个无引用的 leader 常量 |
| `0703d6a` | 1.R3 的命名与提示语（见 §7 与 §10.6） |
| 本文件所在的提交 | r4 的计划与基线文档 |

§10.1–§10.5 是 r3 的记录，数字是当时的实测值（§10.4 的残留清单已按 r4 更新）；r4 的变化与重测见 §10.6。验证环境：本机 macOS，Homebrew `cargo` / `rustc` 1.96.1（不是 `tui/rust-toolchain.toml` 钉的 1.94.0），Python 3.14 的 `.venv`。下列数字都是该环境的实测值，**没有 Linux / Windows 的编译或测试结果**。

### 10.1 范围、方法与规模

| 区域 | 拆除内容 |
|---|---|
| `pi-sampler` | 整个 crate 删除（14.3k 行 / 31 文件）。`OriginClientInfo` 迁到 `pi-telemetry/src/http.rs`，`AuthScheme` 迁到 `pi-shell/src/agent/config.rs`（即 B.1） |
| `pi-shell` | `agent::{mvp_agent, subagent, handlers, app, server, init, proxy, relay, …}`、`session::{acp_session_impl, acp_session_tests, helpers, testkit, workflow}`、`leader`、`relay`、`inspect`、`extensions::suggest`，以及 `tools` / `remote` / `upload` / `auth` 的一部分；55 个集成测试只剩 2 个 |
| `pi-pager` | `headless*`、`mcp_cmd` / `memory_cmd` / `plugin_cmd` / `sessions_cmd` / `share_cmd` / `trace_cmd` / `worktree_cmd` / `models`、`acp/leader_bridge`、`acp/version_mismatch*`、`app/leader_cluster`、`--leader` / `--no-leader` / `--leader-socket`；其余文件只去掉对 runtime 的引用 |
| 依赖 | `pi-shell` / `pi-pager` / `pi-pager-bin` / `pi-http` 的未用依赖 |
| **保留不动** | `pi-tools`、`pi-workspace`、`pi-agent`、`pi-sandbox`、`pi-hooks`、`pi-mcp` 等非 runtime crate（为将来的 pi-rust 留着，见 §10.5） |

**方法**：编译器驱动的死代码消除，而不是按文件名手工删。① 先删明确的 runtime 入口；② 跑消费者构建 `cargo check -p pi-pager-bin -p pi-pager-minimal -p pi-update`，按 `dead_code` / `unused` 诊断逐轮删除，直到只剩 20 条 `dead_code` 警告；③ 用 rustc 的 `-W unused-crate-dependencies`（独立 target 目录）找未用依赖，`cfg(linux / windows / test)` 下的用法用源码 grep 交叉核对，并比较前后 `cargo metadata --locked` 的 feature 并集（只有收缩）；④ 清扫 85 个空壳模块、陈旧注释，以及随被删代码失效的测试。驱动脚本在 `/tmp/rr`，**不在仓库中**。

| 指标 | HEAD | 现在 |
|---|---|---|
| `git diff --shortstat HEAD -- tui` | — | 682 个文件，+2,680 / −371,910（492 个删除，190 个修改） |
| `tui/crates` 下 `.rs` 行数 | 1,620,612 | 1,254,693（−365,919，−22.6%） |
| `pi-shell` / `pi-pager` 行数 | ≈404k / 501,798 | 65,879 / 485,564 |
| `Cargo.lock` 包数 | 1,311 | 1,285（`pi-sampler` + 25 个只被已删代码使用的三方 crate，如 `jsonschema`、`moka`、`kanal`、`cached`、`bm25`、`fancy-regex`） |
| `x.ai/` 字面量（`pi-shell/src`，含测试） | 1,100 | 52 |
| 随被删代码一起删除的测试 | — | `pi-shell` 约 6,100 项、`pi-pager` 约 23 项；失去调用者的测试辅助函数 197+ 个 |

### 10.2 `/model` 恢复（ADR3 r3）

协议是标准 ACP Session Config Options：agent 在 `session/new|load|resume` 的响应里公布 `configOptions: [{id: "model", category: "model", type: "select", currentValue, options: [{value, name, description?}]}]`，客户端用 `session/set_config_option {sessionId, configId, value}` 切换，响应带回完整 `configOptions`。

| 侧 | 改动 |
|---|---|
| Python `pi_agent_cli` | `config.py`：`[model]` 是默认项，`[[models]]` 追加候选（`ModelChoice`、`CliConfig.model_choices()`）；`factory.py`：`model_for_choice`；`agent.py`：`MODEL_CONFIG_ID = "model"`、`set_config_option`，三个 session 响应都带 `configOptions`；切换写入 `model_change` 并在 `load` / `resume` 时恢复。用法写在 `agent.example.toml` 与 `packages/pi-agent-cli/AGENTS.md` |
| `pi-acp-lib` | 带类型的 `SetSessionConfigOption` 消息与网关分发（`message.rs`、`gateway.rs`） |
| `pi-pager` | `parse_session_response_models(models, config_options, resp_meta)` 优先从 `configOptions` 生成模型目录；`ModelState.config_option_id` 记下 agent 公布的配置项 id；`Effect::SwitchModel` 带 `config_option_id`，执行器有 id 时发 `session/set_config_option`，没有（agent 未公布）时退回旧的 `session/set_model`——仅作兼容路径，不是目标形态 |
| 测试 | Python +5（`test_acp_agent.py` +3、`test_config.py` +2）；Rust `app/effects/tests.rs` 新增 4 个、更新 3 个 |

### 10.3 验证（r3 的实测）

| 检查 | 命令 / 方式 | 结果 |
|---|---|---|
| 消费者构建 | `cd tui && cargo check -p pi-pager-bin -p pi-pager-minimal -p pi-update` | ✓ 通过，`pi-shell` 有 20 条 `dead_code` 警告 |
| 全工作区类型检查 | `cargo check --workspace --tests --keep-going` | ✓ 通过，唯一例外是 `pi-fast-worktree` 的 lib 测试（`crate::nfs::confined::tests::plant_journal` 编译错误，HEAD 上已存在） |
| runtime 残留 | `rg -w 'MvpAgent\|acp_session_impl\|SamplerActor' tui/crates` | ✓ 无命中 |
| 依赖图 | `cargo tree -p pi-pager-bin -i pi-sampler` | ✓ `did not match any packages` |
| `pi-shell` lib | `cargo test -p pi-shell --lib` | ✓ 1,136 通过 / 0 失败 / 8 忽略 |
| `pi-shell` 集成 | `--test sandbox_requirements_pin --test test_config_update_isolation` | ✓ 2 + 3 通过 |
| `pi-pager` lib | `env -u NO_COLOR COLORTERM=truecolor TERM=xterm-256color cargo test -p pi-pager --lib -- --skip doctor_cmd::` | 8,606 通过 / 2 失败 / 66 忽略 / 17 跳过（失败项见 §10.4） |
| `pi-pager` 集成 | `--test settings_e2e --test grok_home_paths --test selection_model_public_api` | ✓ 277 + 2 + 2 通过 |
| `pi-pager-bin` 集成 | `--test update_never_blocked_by_config` | ✗ 1 失败，与本次改动无关：测试按 `CARGO_BIN_EXE_pi-pager` 找二进制，而二进制现名 `zypi`（fork 改名遗留，`Cargo.toml` 的 bin 名本次未动） |
| 其他触及的 crate（lib） | `pi-http` 13、`pi-telemetry` 244、`pi-file-utils` 217、`pi-sampling-types` 320、`zypi`（pager-bin）14、`pi-workspace-server` 26、`pi-acp-lib` 21 | ✓ 通过；`pi-acp-lib`、`pi-agent`、`pi-shell-base`、`pi-workspace` 另有与本次改动无关的失败，见 §10.4 |
| Python | `.venv/bin/python -m pytest -m "not real_llm"` | ✓ 579 通过（32 个 `real_llm` 用例按标记排除） |
| 真实 LLM（协议层） | OpenRouter `qwen/qwen3.8-27b:free`，直接对 `python -m pi_agent_cli` 发 JSON-RPC | ✓ `session/new` 公布 `model` 配置项；prompt、`session/set_config_option` 切换、prompt、切回、prompt 全程无协议错误；`model_change` 落盘。第二个免费模型被 OpenRouter 账号策略拒绝（HTTP 404，非代码问题），所以「切到另一个真实模型后完成一轮对话」**没有**验证 |
| TUI（PTY） | `zypi` debug 二进制跑在伪终端里（`pty` + `pyte` 当终端模拟器），`PI_AGENT_COMMAND` 指向 `python -m pi_agent_cli`（外面套一个只记 ACP 方法名的透明代理），模型走 OpenRouter。脚本在 `/tmp/rr/tui_pty.py`，不在仓库里 | ✓ 欢迎页 3 s 内出现，状态行显示 agent 公布的 `qwen/qwen3.8-27b:free · auto`；`/model` 出现在斜杠菜单（「Switch the active model」），选择器列出 `qwen/qwen3.8-27b:free (current)` 与 `Second model`。完整走了一遍 A→B→A：第一轮 prompt 返回 `PONG`；选 `Second model` 后状态行变为 `Second model · auto`，线上抓到 `session/set_config_option {configId: "model", value: "google/gemma-4-26b-a4b-it:free"}`，响应带回更新后的 `configOptions`；随后的 prompt 以第二个模型发出（被 OpenRouter 账号策略 404 拒绝，反过来证明请求确实走了新模型）；切回第一个后 prompt 返回 `PANG`。`/exit` 以 0 退出，Python agent 与代理都没有残留。覆盖面：macOS、单会话、一个干净的 `PI_HOME` |

### 10.4 未验证与残留

**已知失败，均与本次改动无关**（相关代码本次未改，或只改了注释；`pi-agent` / `pi-shell-base` / `pi-workspace` 的改动已逐行核对为纯注释）：

- `pi-pager` 2 个：`views::dashboard::render::tests::render_footer_multiline_{empty_create,mode_send}_uses_shift_or_alt_enter`——macOS 把 Alt 渲染成 `Opt`，断言只认 `Alt+Enter` / `Shift+Enter`。
- `pi-pager` 环境敏感：沙箱默认的 `NO_COLOR=1`、`TERM=dumb` 会让约 10 个颜色 / 光标测试失败（取消 `NO_COLOR` 并设 `TERM=xterm-256color` 后通过）；`doctor_cmd::` 的 17 个测试在本机不返回——用 `sample` 抓到卡在 `diagnostics::apply_voice_probe` → `pi_voice::probe::input_device_info` → `cpal` / CoreAudio 的 `AudioUnit::set_property`（探测音频输入设备，疑似在等麦克风授权），已跳过，没有结果。
- `pi-acp-lib::channel::acp_send_failure_tests::vendor_x_ai_ext_is_dropped_without_sending`：**永久挂起**（已杀掉，其余 21 个通过）。`acp::ExtRequest` 是 `#[serde(transparent)]` 且 `method` 被 `#[serde(skip)]`，`vendor_ext_method` 序列化后读不到 `method`，丢弃逻辑从不触发，请求入队后没人应答。`channel.rs` 与依赖版本都没变，HEAD 上同样如此。**这推翻了 §4.4 的「出站 `x.ai/*` 被 `channel.rs:18,72` 丢弃」**——那条丢弃从未生效。PTY 抓包也印证了这一点：pager 仍会向 agent 周期性发出 `_x.ai/log` 通知（`pi-telemetry/src/unified_log.rs`，原本由 Rust shell 接收落盘），Python 直接忽略；这类通知要在 1.R5 / ADR4 里处理（本地落盘，或删除）。
- `pi-agent::prompt::template::tests::test_encrypted_templates_not_stale`（加密模板字节过期）、`pi-shell-base::util::tests::is_grok_process_strict_self_true_impossible_pid_false`（按进程名 `grok` 判断）、`pi-workspace::session::git::*` 7 个（远端 URL 规范化的测试：实际值是 `pi-org`、期望值仍写 `xai-org`，是改名遗留；另有一个提交 OID 断言不符，未查）。
- `pi-fast-worktree` 的 lib 测试编译不过（见 §10.3）。

**没有验证的**：

- Linux / Windows 的 `cfg` 代码本机无法编译。被删模块里的 `cfg(target_os)` 分支，以及依赖修剪时的 `cfg` 用法只做了源码 grep 交叉核对，需要 Linux CI（ADR6、阶段 0.1）。**r4 补注**：workflow 已写好（`.github/workflows/tui-ci.yml`），但还没有在 Linux 上运行过，这一条仍然成立，直到第一次运行得到结果。
- r3 时阶段 0 全部未做：没有 Rust CI job、基线报告、ACP 契约 / e2e 载体、入口审计。所以「无回归」只能说成：编译通过，现有单测通过，上面列出的失败与改动无关。**r4 补注**：0.1 / 0.2 / 0.4 的工具与文档已补（见 §7 阶段 0 与 [`docs/baselines/tui.md`](../baselines/tui.md)）；0.3（ACP 契约 / e2e）、0.5–0.9（调用图工具化、入口审计、磁盘读取点、能力矩阵、实测结案）仍未做。
- 没有跑全工作区的 `cargo test`：只跑了触及的 crate 与 `pi-pager` 的 3 个集成测试目标；`doctor_early_dispatch`、`mermaid_render_subprocess`、`signal_errno_preservation` 以及未触及的 crate（`pi-tools`、`pi-hooks` 等）都没有跑。**r4 补注**：基线清单固定了 8 个 suite（清理 leader 残留之后合计 10,839 通过 / 2 已知失败 / 80 忽略，macOS；清理之前是 10,857），其余 crate 的取舍见 `docs/baselines/tui.md` §3.2。
- release 二进制体积、冷编译与启动耗时没有测。**r4 补注**：拆除前（v0.4.0）的体积与构建耗时已取得（470,461,776 B / 27 m 27 s）；拆除后的数字要等 `release-baseline` job 在 Linux 上跑一次。

**残留**：

- **leader（r4 更新）**：pager 的 leader UI 状态与流程已清掉（做法与范围见 §10.6）。`.rs` 里的 `leader` 字样从 684 行 / 121 个文件降到 **273 行 / 80 个文件**（HEAD 是 2,999 行）。剩下的不是 UI 残留，分四类：① 多客户端共享会话协议的词汇（pager 61 行：viewer 模式、`shared_prompt_queues`、`session_load_barrier`、`acp_handler/queue.rs` 的 `running_prompt_id` 采纳；注释里的「leader」指托管共享会话的 agent）——ADR4 / A.3 决定去留，不在 r4 范围；② 与本事无关的通用含义：进程组 / 会话 leader（`pi-tty-utils`、`pi-workspace` 的 `restore_fetch`、`pi-hooks`、`pi-workspace-daemon`、`pi-mermaid`）、单飞里的 leader（`pi-mcp` 的 OAuth）；③ `pi-update` 的自更新收敛与 `LeaderConverge` 遥测；④ 遥测的进程身份（`Entrypoint::Leader`、`LeaderMode`、`is_leader_mode`，`pi-pager-bin` 与 `acp/mod.rs` 恒报 `Standalone`）——这是遥测 schema，改它是产品决定。另有零星的「in leader mode」注释留在 `pi-shell`（config watcher、campaigns、`extensions::notification`）、`pi-shell-base`（`cpu_profile`）与 `pi-tools`（monitor），随各自模块的取舍处理。
- **子进程生命周期（1.R3）：命名与提示语 r4 已改（见 §10.6），行为没动**。实测只覆盖正常退出（`/exit` 后 Python agent 随 stdin 关闭退出，无残留）；`stderr` 去向、优雅退出（1.P2）、崩溃 / 中途 Ctrl-C 之后是否残留 Python 或 bash 子进程都还没做 / 没测，见 §7 的 1.R3。
- **「Starting session…」要转 30 s**：每个会话创建都会种下一个 `McpInitProgress` seed（`app/dispatch/session/lifecycle.rs:466`），只会被 agent 的 `*/mcp/init_progress` 通知清掉（`acp_handler/mcp.rs`），Python 不发，只能等 `SEED_EXPIRE`（30 s）到期。纯展示问题，不阻塞输入与 prompt；种下与清除的代码和 HEAD 一致（本次没改），属于 P3（能力驱动 UI）的待办。
- **配置兼容**：已移除的配置段（例如 `[toolset.web_search]`）现在会被报为「未识别」，由 `removed_web_search_section_is_reported_unused` 守住；ADR5 的「首次启动告警一次」没做。
- **依赖图**：`async-openai` 仍在 `cargo tree -p pi-pager-bin` 里（经 `pi-tools` / `pi-sampling-types` / `pi-agent`），阶段 D 没做；`pi-shell/build.rs` 的 ripgrep 打包是死代码（真正的使用者在 `pi-tools/build.rs`），没动。
- **文档**：内嵌 user-guide 仍描述已移除的命令（C.2）；`tui/NOTICE`、`THIRD-PARTY-NOTICES` 未更新（C.3）。
- **其他**：`tests/test_pelican_real_llm.py` 在该机环境下失败，与本次改动无关，未深查；rustdoc 的 intra-doc 链接没有用 `cargo doc` 核对（`tier.rs` 里一处指向已删函数的悬空链接已手工改成纯文字）。

**建议的后续顺序**（r4 更新；r5 的现状与顺序见 §10.7 末尾）：① 在 Linux 上跑第一次 `TUI CI`（0.1 / 0.2 已写好、未运行），把 `cfg` 代码的错误和失败清单跑出来，按 [`docs/baselines/tui.md`](../baselines/tui.md) §5 收尾；② 1.R3 剩下的行为部分：`stderr` 去向、优雅退出（1.P2）、残留进程实测，配 PTY 烟测；③ 入口审计（0.6 → A.3），ADR4 的去留决定顺带处理共享会话协议的词汇；④ A.4 去掉全局 `#![allow]`；⑤ 文档（C.2 / C.3）；⑥ 视基线决定阶段 D。

### 10.5 为 pi-rust 铺路

- **ACP 是唯一接缝**：TUI 进程里不再有能创建 agent 或发起模型请求的代码路径。`pi-shell` 只剩 TUI 服务（配置、auth、trust、session registry client、模型目录、sampling 类型薄壳 `pi_shell::sampling`，后者只是对 `pi-sampling-types` 的重导出，不含请求逻辑）。
- **接入点**：agent 命令由 `pi-pager/src/acp/spawn.rs` 解析，顺序为 `PI_AGENT_COMMAND` → `$PI_HOME/{agent,config}.toml` 的 `[agent].command` → `$PI_PYTHON -m pi_agent_cli`。pi-rust 只需是一个讲标准 ACP（stdio JSON-RPC）的可执行文件，把命令指过去即可，不必改 TUI。
- **`-p` 不经过 Rust ACP client**：`pi-pager-bin` 的 `dispatch_python_print` 把 `-p` / `--prompt-json` / `--prompt-file` 及相关旗标原样转给 agent 命令，所以 pi-rust 要么实现同一组旗标，要么先定 1.P6 的旗标契约。
- **能力驱动的 `/model`**：agent 公布 `category: "model"` 的 select 配置项，TUI 就有 `/model` 并用 `session/set_config_option` 切换；没公布时只剩 legacy 的 `session/set_model` 兼容路径。pi-rust 实现这个配置项即可获得 `/model`。
- **保留的 crate**：`pi-tools`（141k 行）、`pi-workspace`（101k）、`pi-agent`（22.9k）仍在 pager 的依赖图里，因为 pager 用的是它们的渲染类型、权限模型与工具展示；它们不是 runtime。pi-rust 若要复用，应作为**独立 agent 进程**的依赖，而不是链回 `pi-pager`（P1）。

### 10.6 r4 追加：阶段 0 的 CI 与基线、leader 残留、1.R3 命名

**阶段 0.1 / 0.2 / 0.4（`3117858`）**：`.github/workflows/tui-ci.yml`（`check`、`test`、手动的 `release-baseline`）、执行器 `scripts/tui_baseline.py`、清单 `scripts/tui_baseline.toml`、报告 [`docs/baselines/tui.md`](../baselines/tui.md)。0.4 的 deny-list 是阻塞门禁；0.1 / 0.2 没勾选，因为 workflow **从未在 Linux 上运行**——本机（macOS）上 `gates`、`check`、`test` 都跑通，工作流只经过 actionlint 与执行器的 18 个单测。测试与 `--workspace --tests` 在首次 Linux 结果出来之前只报告、不阻塞（清单里 `enforce = false`）。

**leader 的 UI 状态残留（`a648e92`，72 个文件，+346 / −2,684）**：
- 删除：`leader_mode`、`leader_roster`、`reconnect_pending`（生产里都恒为 false）及全部读取点；`Effect::FetchRoster`、`TaskResult::Roster*`；`StartupPhase::LeaderConnect`、`AgentKind::Leader`；`NextStep::RestartSharedLeader`（提示一个不存在的 `zypi leader kill`）；启动失败屏的尝试计数与 leader 行；`[cli].use_leader`、`RemoteSettings.leader_mode`、`AgentMode::Leader`；`pi-test-support` 的 leader 夹具与 UDS 故障代理（连带 3 个依赖）；内嵌 user-guide 与场景 YAML 里的 leader 段落。
- **生产行为不变**。唯一的逻辑改动是后台 follow-up 的路由：`immediate_server_send_eligible` 从 `(leader_mode || steer) && …` 变成 `steer && …`，而 `leader_mode` 恒为 false，等价于 `[ui].follow_up_behavior = "steer"`；默认仍是 `Queue`。
- **测试约定变了**：以前 pager 的测试夹具隐含 leader 的「立即发送」语义。现在夹具用生产默认值（Queue）；要验证「服务端权威的立即发送」的 15 个用例通过 `agent_view::test_fixtures::SteerFollowUp`（RAII，drop 时恢复缓存）显式切到 Steer。
- **旧配置**：已有的 `use_leader` / `leader_mode` 被静默忽略（`CliConfig` / `RemoteSettings` 没有 `deny_unknown_fields`），`merge_section` 保留未建模的键，`pi-config-types` 有回归测试。ADR5 的「首次启动告警一次」仍未做。
- 随被删代码一起删掉 18 个测试（`pi-pager` −17：重连守卫、名册、`leader_mode` 路径；`pi-shell` −1：`[cli].use_leader` 写入），基线数字相应下调，见 [`docs/baselines/tui.md`](../baselines/tui.md) §3.2。
- `aeaf9b6` 随后又修掉三处已失效的注释（`pi-acp-lib` 的 `normalize.rs` 说有 leader socket 与 leader bridge 的 replay sniff，`pi-status-line`，`pi-http`），并删了 `pi-http::STARTUP_AUTH_TIMEOUT`（leader 进程的启动认证上限，全仓库无引用）。`pi-acp-lib` 的 `spawn_stdin_line_reader` / `normalize` 现在没有调用者，留给 A.3 决定去留。

**1.R3 的命名与提示语（`0703d6a`）**：内容见 §7 的 1.R3。行为没改：同样的 10 s + 2 s 预算，同样的 `start_kill()` + `wait()`；只有慢退出提示的文字变了。

**验证（macOS，r4 末态；Homebrew `cargo` 1.96.1，不是钉的 1.94.0）**

| 检查 | 结果 |
|---|---|
| `tui_baseline.py gates` / `check` | ✓ 工具链钉一致；`pi-sampler` 与三个 runtime 词零命中；依赖图 995 个包（暂定上限 1000）；消费者构建通过，20 条 `dead_code` 警告（与 r3 相同）；`cargo check --workspace --tests` 0 个错误 |
| `tui_baseline.py test`（8 个 suite） | ✓ 10,839 通过 / 2 已知失败 / 80 忽略：`pi-shell` 1,140、`pi-pager` 8,870（+ 2 个 macOS 上的 `Opt`/`Alt` 渲染失败）、`pi-pager-bin` 14、`pi-acp-lib` 21、`pi-http` 13、`pi-telemetry` 244、`pi-file-utils` 217、`pi-sampling-types` 320 |
| `pytest scripts/tests/test_tui_baseline.py` | ✓ 18 通过 |
| TUI 烟测（PTY；重建的 debug `zypi`，`pi_agent_cli` + OpenRouter；脚本 `/tmp/rr/tui_pty.py`，不在仓库里） | ✓ 3 s 内会话就绪；真实 prompt 返回 `PONG`；`/model` 选择器列出 `qwen/qwen3.8-27b:free (current)` 与 `Second model`，选后出现「Default model: Second model」、状态行变为 `Second model · auto`；第二个模型被 OpenRouter 账号策略 404 拒绝（预期），切回后 prompt 返回 `PANG`。退出：`/exit` 以 0 退出；连按两次 Ctrl+C（第一次出现「press again to quit」）0.3 s 内以 0 退出；两种情况都没有残留 `pi_agent_cli` 进程，屏幕上都没有「Stopping agent…」。另一次观测：对 `zypi` 发 SIGTERM、0.5 s 后 SIGKILL，Python agent 随后也不在了（单次观测，那时没有 bash / MCP 孙进程在跑）。单按一次 Ctrl+Q / Ctrl+D 只会进入「再按一次退出」的确认，不会退出——这是设计，单测 `ctrl_*_double_press_quits` 覆盖 |
| `.rs` 行数（`tui/crates`） | 1,252,374（`9add266` 时是 1,254,693） |

**这一轮没有验证的**：Linux、Windows；`tui-ci.yml` 在 GitHub 上的真实运行；`stderr` 去向、优雅退出（1.P2）；agent 正在跑 bash / MCP 子进程时 `/exit`、崩溃、`kill -9 zypi` 之后，Python 的孙进程会不会残留（上面只有一次没有孙进程的观测）。

### 10.7 r5 追加：Linux CI、入口审计与阶段 A 收口

**起点与决定。** r4 的末态推到远端并开了 draft PR [#7](https://github.com/zy1233/pi-python/pull/7)；第一次 Linux 运行（`TUI CI` run [37263077643](https://github.com/zy1233/pi-python/actions/runs/37263077643)，`e20af45`）全绿：依赖图 995，消费者构建 20 条告警，`--workspace --tests` 0 个错误，8 个 suite 10,851 通过 / 0 失败 / 75 忽略。用户的四个决定：① 豁免 Windows / WSL 的 pytest，推送前以 CI（Linux，Python 3.11–3.13）为准；② 入口审计「直接按附录 A 默认执行（删 dashboard / MCP / 队列 / 子代理 / hooks / plugins 入口，隐藏 session rename），不逐项确认」；③ changelog / What's new 功能删除；④ `-p` 带 `--sandbox` 立即报错退出，其余未支持的旗标先在 stderr 警告，完整契约留给 1.P6。

**方法（r3 的延续，加两样）。** 仍是编译器驱动：删入口 → 全 target 的 `cargo check` → 按 `dead_code` / `unused` 诊断逐轮删 → 清扫空壳模块与失效测试。新增的两样：

- **rustc 看不见的「恒假」代码**——靠数据或常量恒定、而不是靠可达性死掉的：`PagerArgs::chat()` / `process_chat_mode_enabled()` 恒为 false 的 `chat_mode` 世界（ACP `session/list` 的行恒为 `"source": "local"`，所以 `"conversation"` 行也是死的）；没有任何 crate 开启的 `cfg(feature = "local-workspace")`。做法是一个基于 `syn` 的布尔常量折叠器（`if false`、`x || false`、`!false` 等，迭代到不动点）先把它们折掉，再让编译器收尾。
- **平台门控的代码**在 macOS 上编译不到，靠「被删名字在未参与编译的文件里是否还有残留」的检查，再由 Linux CI 兜底（没有交叉编译）。

驱动脚本与折叠器在 `/tmp/rr`，**不在仓库中**（与 r3 一致）。

**提交**（`61126ed` 之后；文件 / 行数是各提交的 `--shortstat`，只写了有意义的数字）：

| 提交 | 内容 | 文件 / 行 |
|---|---|---|
| `e20af45` `0a58b81` | 基线工具不再依赖默认文本编码；`-p` 拒绝无沙箱运行并报告被丢弃的旗标（ADR7 的最小版本） | 3 / +84 −20；4 / +292 |
| `57c0d80` | 删 changelog / release-notes 功能及其 x.ai 取数 | 281 / −10,988 |
| `56550ac` | 首次 Linux 全绿后，基线转为阻塞 | 2 |
| `6983c99` | 删白名单外、本就不可达的内置斜杠命令 | 77 / −9,142 |
| `2bef92d` | 删 agent dashboard | 97 / −42,418 |
| `2273c3e` `c90e6fc` `a6aeccc` | pi-pager 去掉 crate 根 allow，按 rustc 诊断级联删死代码（`Action` / `Effect` / `TaskResult`） | −133；135 / −23,205；64 / −11,871 |
| `c92e170` `0c07c52` | 删 tasks / background / interject / extensions / rewind 的 ActionId 与键位；删 extensions / agents / persona 模态 | −2,191；44 / −18,627 |
| `936c7f7` `078a2b1` `9389daa` | 删 tasks 窗格与后台 / 定时任务；subagents / workflows / goals / catalog 窗格；取消轮次的 subagent 面板与等待链路 | −11,369；−16,446；−2,226 |
| `63f0e3b` | 删共享 prompt 队列、interject、send-now、steer | 82 / −8,784 |
| `056085f` `5652d3a` `0ffd8d3` | 删 plugin CTA 与 marketplace 更新；MCP 模态、MCP init seed、elicitation 卡片、Claude 导入；桩 effect、recap、feedback、consent、coding-data-sharing | −3,956；−5,647；−12,433 |
| `25dbe3c` | 删 rewind / fork / jump / 外部会话与会话选择器的来源机制 | 85 / −16,844 |
| `86ea4c9` `98e29c9` | 删 5 个无人依赖的 crate；删 hooks / plugins UI、`HookDenied` 取消路径与 `pi-hooks-plugins-types` | −16,848；−6,035 |
| `7ff908b` | 删 session rename 的死链路 | 12 / −408 |
| `4eb9f11` `ab77740` | 去掉其余 crate 根的 `#![allow(unused/dead_code)]` 并删被藏起来的死代码；`pi-shell` 的 `pub mod` 降级、删其后的死代码（A.4） | −362；62 / −6,079 |
| `fbcaf14` `886d5af` | 删未用依赖；删 `pi-memory`、`pi-fsnotify` 与 20 个无人使用的 workspace 依赖 | −267；−17,876 |
| `0e71894` | 删 `cfg(feature = "local-workspace")` 代码与 Cargo feature | 27 / −3,684 |
| `5fcec14` | 删恒假的 `chat_mode` 世界（`--chat`、`chat_kind`、`pending_chat`、`_meta.kind = "chat"`、`conversation` 行、`pi-shell::agent::chat_modes`） | 33 / −1,063 |
| `27df705` | 基线按 Linux 实测收紧：图 995 → 980，告警 20 → 0，`min_passed` 下限 | 2 |
| `fe1fc65` | rustfmt，只动本分支改过的 216 个文件 | +776 −1,360 |

**入口审计的结果（0.6，按附录 A 的默认）。**

- **删除**：dashboard；agents / extensions / persona 模态；tasks、后台与定时任务；subagents、workflows、goals、catalog 窗格；共享 prompt 队列、interject、send-now、steer；MCP 模态 / init seed / elicitation 卡片 / Claude 导入；hooks、plugins、marketplace、plugin CTA；rewind、fork、jump、外部会话；recap、feedback、consent、coding-data-sharing；changelog / What's new；白名单外的全部斜杠命令；`--chat` 世界；`local-workspace` 代码。
- **隐藏 / 删死链路**：session rename（pager 侧恒返回「not supported in standard ACP」，入口与整条链路已删，不新增）。
- **保留**：斜杠命令 `/settings`、`/new`、`/model`、`/resume`、`/theme`、`/multiline`、`/home`、`/help`、`/exit`；会话 new / load / resume / list / delete、prompt / cancel、权限请求、模型选择（`configOptions`）、skills、image、状态栏。
- **有意没动**：认证 / 计费界面（3d-4，归阶段 D.3）；CLI 旗标瘦身（3d-6）；plan 审批 / btw / cta（3f）。它们在标准 ACP 下要么恒不触发、要么还没有对应的 Python 能力，等阶段 1 的协议决定后再删更稳。

**规模（r4 末态 → r5 末态）。**

| 指标 | r4 | r5 |
|---|---|---|
| `tui/crates` 下 `.rs` 行数 | 1,252,374 | 1,019,816（−232,558，−18.6%；格式化后） |
| `crates/codegen` 下的 crate 数 | 73 | 65（删 `pi-agent-lifecycle`、`pi-foreign-sessions`、`pi-fsnotify`、`pi-hooks-plugins-types`、`pi-memory`、`pi-plugin-marketplace`、`pi-session-search`、`pi-shell-session-support`） |
| `Cargo.lock` 包数 | 1,285 | 1,257 |
| 依赖图唯一包数（`x86_64-unknown-linux-gnu`） | 995 | 980（`aarch64-unknown-linux-gnu` 979；`aarch64-apple-darwin` 943；`x86_64-apple-darwin` 944；`x86_64-pc-windows-msvc` 926） |
| 消费者构建告警 | 20 | 0（Linux CI 与 macOS 一致） |
| 测试通过（Linux，8 个 suite） | 10,851 | 7,478（`0e71894`） |
| `git diff --shortstat 0a081f4..HEAD -- tui` | — | 914 个文件，+4,868 / −249,996 |

测试数掉了 31%，是随被删功能（dashboard、队列、subagent、tasks、rewind / fork、MCP 模态……）一起删掉的用例，不是静默丢失：清单里每个 suite 现在都有 `min_passed` 下限，今后少于下限必须改清单。

**验证。**

| 环境 | 检查 | 结果 |
|---|---|---|
| Linux CI，`0e71894` | `TUI CI` run [37379203134](https://github.com/zy1233/pi-python/actions/runs/37379203134)（check 8 m 51 s，test 26 m 38 s） | ✓ 门禁全过；依赖图 980；消费者构建 0 告警；`cargo check --workspace --tests` 0 个错误；8 个 suite 共 7,478 通过 / 0 失败 / 20 忽略（`pi-shell` 1,056、`pi-pager` 5,593、`pi-pager-bin` 20、`pi-acp-lib` 21、`pi-http` 13、`pi-telemetry` 239、`pi-file-utils` 216、`pi-sampling-types` 320） |
| Linux CI，`0e71894` | `CI`（Python）run 37379203149 | ✓ |
| macOS 本机，`5fcec14` | 消费者构建与 `pi-pager` / `pi-pager-bin` / `pi-pager-minimal` / `pi-shell` 全 target 检查 | ✓ 0 错误 0 告警 |
| macOS 本机，`5fcec14` | `cargo test -p pi-pager --lib`（`--test-threads=2`）与 `--test settings_e2e` / `grok_home_paths` / `selection_model_public_api` | ✓ 5,327 通过 / 0 失败 / 13 忽略；251；2；2 |
| macOS 本机，`fe1fc65` | rustfmt 之后重跑消费者构建 | ✓ 0 错误 0 告警 |
| 本机 | `pytest scripts/tests/test_tui_baseline.py`；`ruff check .` / `ruff format --check .` | ✓ 19 通过；通过 |

**这一轮没有验证的**：Windows；`5fcec14` 之后的 Linux 运行（`fe1fc65` 推送后的 CI 结果见 `docs/baselines/tui.md` 状态块）；删完 UI 之后的 PTY 烟测（见下一条）；`release_baseline`（体积 / 冷编译耗时 / `--version` 延迟）。

**遗留与建议的顺序。**

1. **PTY 烟测**：A.3、A.4 合计删了约 23 万行（大头是 UI），单测之外要在伪终端里跑一遍欢迎页 → prompt → `/model` → `/resume` → `/exit`，对着 `pi_agent_cli` + OpenRouter。
2. **`release_baseline`**：最后一次 push 之后手动触发一次，回填 `docs/baselines/tui.md` §3.1，0.2 勾选。
3. **A.3 的尾巴**（可选，同样的折叠器办法）：3d-6 CLI 旗标瘦身——标准 ACP 下 `Effect::CreateWorktreeSession` 恒返回 `WorktreeSessionFailed`，`--worktree`、`--restore-code`、`allow_remote_restore` / `suppress_code_restore` 的「远端恢复」世界同样恒假；3f plan 审批 / btw / cta；3d-4 认证 / 计费界面归阶段 D.3。
4. **A.4 的尾巴**（可选）：`pi-shell-base` 等其余 crate 的 `pub` 瘦身（没做过）；`pi-shell-base/src/env.rs` 里死掉的 gateway-bridge 常量；约 25 处局部 `#[allow(dead_code | unused*)]`（有的是平台门控，要看 Linux）；`pi-shell/src/session/storage` 的 `relocation`（`#[allow(dead_code)]`，归 D.2）。
5. **阶段 1 一点没动**：1.P1–P7、1.R1–R5；阶段 0 的 0.3（ACP 契约 / e2e）、0.5、0.7–0.9。这些是功能工作，不是删除；下一步该做它们，而不是继续挖死代码。
6. **文档与声明**：C.2 内嵌 user-guide；C.3 `tui/NOTICE` 与 `THIRD-PARTY-NOTICES`（依赖已少了 28 个包，需要重新生成；对外发布二进制前由用户定措辞）。

## 附录 A：能力矩阵（阶段 0.8 的初稿）

| 能力 | pager 侧 | Python 现状 | 默认决策 |
|---|---|---|---|
| session new / load / resume / close | 标准 ACP | 已覆盖（`agent.py:123-200`） | 保留 |
| session list | `ListSessionsRequest`（`effects/mod.rs:472,528`） | 部分：title 合成、`updated_at = createdAt`、忽略 cursor | 阶段 1 补齐 |
| session delete | `pi/session/delete`（`effects/mod.rs:1905`） | 已覆盖（`agent.py:236-251`） | 保留并登记；SDK 补路由后迁标准方法 |
| session rename | 恒返回「not supported in standard ACP」（`effects/mod.rs:2291-2302`） | 无 | 隐藏入口，不新增 |
| prompt / cancel | 标准 ACP | 已覆盖 | 保留 |
| 权限请求 | `request_permission` | 部分：仅 bash / edit / write；选项仅 allow-once / reject-once | 保留；阶段 0 确认 pager 是否依赖 allow-always / 规则 |
| 模型选择 | r3：读 `configOptions`（`category: "model"`）+ `SetSessionConfigOptionRequest`（`effects/mod.rs`）；无该配置项时退回 `SetSessionModelRequest` | r3：`configOptions` + `set_config_option`（`agent.py`），`[[models]]` 提供候选 | **恢复**（ADR3，r3 已做） |
| 模式切换（yolo / auto） | `SetSessionModeRequest`（`effects/mod.rs:1005,1025`）、`pi/yolo_mode_changed` | 部分：仅 `ext_notification` | 阶段 1 定映射 |
| MCP | `pi/mcp/*`（仅测试代码） | 无（`mcp_servers` 被忽略） | 降级，删 UI |
| queue / interjection | `pi/queue/changed`、`pi/session/interjection`（仅测试代码） | 无 | 删解码 |
| compaction | 触发与展示 | 部分：`auto_compact` 由 `max_turns >= 30` 推导 | 降级 |
| subagents / 后台任务 | `pi/task_*`、`AgentSession.bg_tasks` | 无 | 删（Phase 4 非目标） |
| skills | slash / `available_commands` | 已覆盖 | 保留 |
| hooks / plugins / marketplace | `pi/marketplace/*`、`pi/plugins/notify-updates`（仅测试代码） | 无 | 删（Phase 4 非目标） |
| image | 剪贴板图片 | 已覆盖 | 保留 |
| status line | `ConnectFlags.status_line` | 无 | 阶段 0 决策 |
| 历史回放 | `load_session` | 已覆盖（`resume` 不回放） | 保留 |
| `initialize._meta` 键 | `modelState`、`grokShell`、`availableCommands`、`cancelRewind`、`sessionRecap`、`feedbackTraceOffer` | 无 `_meta` | 逐键：删除解码或登记 |

**r5 执行结果（对照上表的「默认决策」）**：

- **已按默认执行**：MCP——UI 已删（模态、init seed、elicitation 卡片、Claude 导入；`pi/mcp/*` 只剩 `pi-mcp` 自己的测试里出现）；queue / interjection——已删（含 steer、send-now）；subagents / 后台任务——已删（tasks 窗格、定时任务、workflows、goals）；hooks / plugins / marketplace——已删（含 `pi-plugin-marketplace` 与 `pi-hooks-plugins-types` 两个 crate）；session rename——入口与整条链路已删。
- **仍是阶段 1 的待办，r5 没动**：session list 的 Python 补齐（1.P1）；模式切换的映射（1.P3）；compaction 的降级展示；权限 allow-always；status line；`initialize._meta` 的解码精简（1.R5：`acp/mod.rs` 仍在解 `grokShell`、`modelState`、`cancelRewind`、`availableCommands`，其中 `cancelRewind` 对应的功能已删）。
- **新增的删除（表里没有）**：dashboard、recap / feedback / consent / coding-data-sharing、rewind / fork / jump / 外部会话、changelog、`--chat` 世界、`local-workspace`。它们都是入口审计里「Python 不路由、pager 也无法触发」的类别。

## 附录 B：复现方法

均在仓库根目录执行；B.1、B.2 只需 `python3`（≥ 3.11，用到 `tomllib`），不依赖 cargo、不联网（本机 `cargo tree --offline` 无法解析 git 依赖 `async-openai` fork）。

**B.1 内部依赖图**（期望输出 `82 67`、仅经 `pi-shell` 可达的 14 个 crate、`['pi-http', 'pi-shell', 'pi-telemetry']`）：

```python
import pathlib
import tomllib

crates = {}
for p in pathlib.Path("tui/crates").rglob("Cargo.toml"):
    d = tomllib.loads(p.read_text())
    if "package" in d:
        crates[d["package"]["name"]] = d


def deps(name):
    d = crates[name]
    tables = [d.get("dependencies", {})]
    tables += [t.get("dependencies", {}) for t in d.get("target", {}).values()]
    out = set()
    for t in tables:
        for dep, spec in t.items():
            real = spec.get("package", dep) if isinstance(spec, dict) else dep
            if real in crates:
                out.add(real)
    return out


def reach(start, skip=()):
    seen, stack = set(), [start]
    while stack:
        for n in deps(stack.pop()):
            if n not in seen and n not in skip:
                seen.add(n)
                stack.append(n)
    return seen


ALL = reach("pi-pager-bin")
NO_SHELL = reach("pi-pager-bin", skip={"pi-shell"})
print(len(ALL), len(NO_SHELL))
print(sorted(ALL - NO_SHELL - {"pi-shell"}))
print(sorted(n for n in crates if "pi-sampler" in deps(n)))
```

**B.2 `pi-shell` 模块级闭包**（§5 的数字来源；期望输出：非测试 265,627 行 / 234 单元、根引用 50 个单元、存活 152,609 行 / 可删 113,018 行、cut point 24 个、provider 栈渗透 28 单元 / 46 文件 / 169 处）：

```python
import collections
import pathlib
import re

CG = pathlib.Path("tui/crates/codegen")
SRC = CG / "pi-shell/src"
BAN = ("leader", "agent::mvp_agent", "agent::subagent", "agent::app", "session::acp_session_impl", "tools", "sampling")
SAMPLER = re.compile(r"\b(pi_sampler|async_openai|pi_sampling_types)::|\bcrate::sampling\b")


def is_test(rel):
    return "/tests/" in rel or rel.endswith(("_tests.rs", "/tests.rs")) or "_tests/" in rel or rel.startswith("test_support")


def segs(rel):  # 文件 → 模块路径
    p = list(rel.parts)
    return p[:-1] if p[-1] == "mod.rs" else [] if p[-1] == "lib.rs" else p[:-1] + [p[-1][:-3]]


def strip_comments(t):
    return re.sub(r"(?m)^\s*//.*$", "", t)


def expand_use(body):  # a::{b, c::d} → [[a,b],[a,c,d]]
    def rec(prefix, s):
        items, cur, depth = [], "", 0
        for ch in s:
            depth += ch == "{"
            depth -= ch == "}"
            if ch == "," and depth == 0:
                items.append(cur)
                cur = ""
            else:
                cur += ch
        items += [cur] if cur else []
        out = []
        for it in items:
            m = re.match(r"^(.*?)::\{(.*)\}$", it)
            if m:
                out += rec(prefix + [x for x in m.group(1).split("::") if x], m.group(2))
            elif it.startswith("{") and it.endswith("}"):
                out += rec(prefix, it[1:-1])
            else:
                out.append(prefix + [x for x in it.split("::") if x])
        return out

    return rec([], re.sub(r"\s+", "", body))


# 1. 单元（深度 ≤ 2 的模块路径）与 LoC
files = {pathlib.PurePosixPath(f.relative_to(SRC).as_posix()): f for f in SRC.rglob("*.rs")}
loc = collections.Counter()
for rel, f in files.items():
    if not is_test(rel.as_posix()):
        loc["::".join(segs(rel)[:2]) or "<root>"] += sum(1 for _ in f.open(errors="ignore"))
UNITS = set(loc)


def to_unit(path):
    for k in (2, 1):
        if len(path) >= k and "::".join(path[:k]) in UNITS:
            return "::".join(path[:k])


# 2. 单元间的边（crate:: / super:: / self:: / mod.rs 内相对 use）与 provider 栈渗透
USE = re.compile(r"\buse\s+([^;]+);", re.S)
PATH = re.compile(r"\b(crate|super|self)((?:::[A-Za-z_]\w*)+)")
edges, leak = collections.defaultdict(set), collections.defaultdict(lambda: [0, 0])
for rel, f in files.items():
    if is_test(rel.as_posix()):
        continue
    mp, text = segs(rel), strip_comments(f.read_text(errors="ignore"))
    me = "::".join(mp[:2]) or "<root>"
    paths = [p for m in USE.finditer(text) for p in expand_use(m.group(1))]
    paths += [[m.group(1)] + [x for x in m.group(2).split("::") if x] for m in PATH.finditer(text)]
    for p in filter(None, paths):
        if p[0] == "crate":
            ab = p[1:]
        elif p[0] == "self":
            ab = mp + p[1:]
        elif p[0] == "super":
            n = next((i for i, x in enumerate(p) if x != "super"), len(p))
            ab = (mp[:-n] if n <= len(mp) else []) + p[n:]
        elif rel.name in ("mod.rs", "lib.rs"):
            ab = mp + p
        else:
            continue
        if (u := to_unit(ab)) and u != me:
            edges[me].add(u)
    if n := len(SAMPLER.findall(text)):
        leak[me][0] += 1
        leak[me][1] += n

# 3. pager / pager-bin 对 pi_shell 的根引用
roots = set()
for crate in ("pi-pager", "pi-pager-bin"):
    for f in (CG / crate).rglob("*.rs"):
        if is_test("/" + f.relative_to(CG / crate).as_posix()):
            continue
        text = strip_comments(f.read_text(errors="ignore"))
        paths = [p for m in re.finditer(r"\buse\s+pi_shell::([^;]+);", text, re.S) for p in expand_use(m.group(1))]
        paths += [m.group(1).split("::") for m in re.finditer(r"\bpi_shell::(\w+(?:::\w+)*)", text)]
        roots |= {u for p in paths if (u := to_unit(p))}


# 4. 闭包：禁用 runtime 单元后，从 pager 根引用仍可达的即「存活」
def closure(starts, banned=frozenset()):
    seen, stack = set(), [s for s in starts if s not in banned]
    while stack:
        u = stack.pop()
        if u not in seen:
            seen.add(u)
            stack += [v for v in edges.get(u, ()) if v not in banned]
    return seen


def L(us):
    return sum(loc[u] for u in us)


total = L(UNITS)
banned = {u for u in UNITS if any(u == b or u.startswith(b + "::") for b in BAN)}
surv = closure(roots, banned)
dele = UNITS - surv
cuts = sorted(u for u in surv if edges.get(u, set()) & banned)
lk = {u: v for u, v in leak.items() if u in surv}
print(f"非测试 {total:,} 行 / {len(UNITS)} 单元；根引用 {len(roots)} 个单元")
print(f"禁用集 {len(banned)} 单元 {L(banned):,} 行；连带不可达 {L(dele - banned):,} 行")
print(f"存活 {L(surv):,} 行 ({100 * L(surv) / total:.0f}%)；可删 {len(dele)} 单元 {L(dele):,} 行 ({100 * L(dele) / total:.0f}%)")
print(f"cut point {len(cuts)} 个：{', '.join(cuts)}")
print(f"provider 栈渗透：{len(lk)} 单元 {sum(v[0] for v in lk.values())} 文件 {sum(v[1] for v in lk.values())} 处")
```

**B.3 其余检查命令**

```bash
# 无入口的 *_cmd 模块（期望只命中 lib.rs 的 pub mod）
rg -n '\b(sessions_cmd|memory_cmd|plugin_cmd|mcp_cmd|trace_cmd|share_cmd|worktree_cmd)\b' \
  tui/crates/codegen/pi-pager/src tui/crates/codegen/pi-pager-bin/src \
  | rg -v '^tui/crates/codegen/pi-pager/src/(sessions_cmd|memory_cmd|plugin_cmd|mcp_cmd|trace_cmd|share_cmd|worktree_cmd)'

# leader 从未随已发布版本暴露（Command 肉眼数变体；run_* 调用数为 0）
for t in v0.2.0 v0.3.0 v0.4.0; do
  echo "== $t =="
  git show "${t}:tui/crates/codegen/pi-pager/src/app/cli.rs" | rg -n 'pub enum Command' -A 30
  git show "${t}:tui/crates/codegen/pi-pager-bin/src/main.rs" \
    | rg -c 'run_leader\(|run_stdio_agent\(|run_headless\(' || echo 0
done
git log --format='%h %cs %s' -S'run_leader(' -- tui/crates/codegen/pi-pager-bin/src/main.rs \
  tui/crates/codegen/pi-grok-pager-bin/src/main.rs     # 730fa18（引入）、ace8852（移除）

# 协议字面量：x.ai 716 / pi 0；Python 侧的 pi/ 名称
rg -o '"_?x\.ai/[A-Za-z0-9_/.-]*' tui/crates/codegen/pi-shell/src | wc -l
rg -o '"pi/[A-Za-z0-9_/.-]*"' tui/crates/codegen/pi-shell/src | wc -l
rg -n "[\"']pi/" packages/pi-agent-cli/pi_agent_cli --glob '*.py'

# session 读取点 / 写入点（Python 侧期望无 updates.jsonl）
rg -n 'list_summaries|session_exists_for_cwd|load_updates_for_replay|find_most_recent_jsonl_session' \
  tui/crates/codegen/pi-pager/src
rg -n 'append_update' tui/crates/codegen/pi-shell/src/session/storage/mod.rs
rg -n 'updates\.jsonl' packages/pi-agent-cli packages/pi-agent-harness --glob '*.py'

# Python ACP SDK 的路由面（无 session/delete、无 session/set_model）
rg -n 'route_request|route_notification' .venv/lib/python*/site-packages/acp/agent/router.py

# 斜杠白名单与启用点
rg -n 'PI_STANDARD_SLASH_NAMES|enable_pi_standard_slash_menu' tui/crates/codegen/pi-pager/src

# 内嵌文档
find tui/crates/codegen/pi-pager/docs -name '*.md' | wc -l                 # 38
rg -l -i 'leader' tui/crates/codegen/pi-pager/docs | wc -l                 # 8
rg -n 'user-guide|docs/user' . --glob '*.py'                               # 无命中：Python 不读取该目录
```

LoC 用 `python3` 递归统计 `*.rs` 行数（沙箱内 `xargs wc -l` 可能失败）；测试数以 `#[test]` / `#[tokio::test]` / `#[rstest]` 计数，`#[ignore` 另计，均为近似值。

## 修订记录

- r1：初稿（仅建分支并新增计划文档，未改 runtime 实现）。
- r2：吸收源码核查与架构评估。主要变化：终态拆成行为层 / 依赖图层；新增原则 P1–P7、与既有架构的关系（§3）、决策记录（§6）、改动规模（§5）；阶段重排为「基线 → 死代码先行 → 协议与 Python → 拆 runtime → 收口 →（可选）依赖图瘦身」；`pi/` 扩展改准入制、会话数据改纯 ACP、leader 直接放弃；更正 `-p` / headless、证据失真（`*_cmd` 是死代码）、文档目标等事实；退出条件改为可机检。
- r3：在工作区执行 Rust runtime 拆除并恢复 `/model`（当时未提交，现已本地提交，见 §10）。主要变化：新增 §10 执行记录；ADR3 推翻 r2 的「`/model` 从白名单移除」，改为经 ACP Session Config Options 恢复（Python 公布 `configOptions` 并路由 `session/set_config_option`，pager 读 `configOptions`、无该配置项时退回旧 `session/set_model`）；P1 增补「任何 runtime（含将来的 pi-rust）都必须作为独立 ACP agent 位于 ACP 之后」；§7 勾选 A.1、A.2（主体）、B.1、B.5、B.6，A.3、A.4、B.3 未做，B.2、B.4 换了做法或只做了一部分；§4.4 关于「出站 `x.ai/*` 被 `channel.rs` 丢弃」的判断被实测推翻（§10.4）。验证以消费者构建、单测、PTY 真机 `/model` 切换与 OpenRouter 真实会话为准，没有 Linux / Windows 结果，阶段 0（CI、基线、契约 / e2e）仍未做。
- r4：把 r3 的工作区改动提交到本地分支（**未 push**），并补三件事：① 阶段 0 的 0.1 / 0.2 / 0.4——Linux CI workflow、基线执行器与清单、基线报告 `docs/baselines/tui.md`（0.4 勾选；0.1 / 0.2 因为 workflow 没在 Linux 上跑过而不勾选）；② 清掉 leader 的 UI 状态残留（A.2 的尾巴），生产行为不变，测试夹具改用生产默认值；③ 1.R3 的命名与提示语（行为不变，`stderr` 去向、优雅退出、残留进程实测仍未做）。同时把 §10.4 的 leader 残留改写为四类「不是 UI 残留」的剩余（273 行 / 80 个文件），新增 §10.6，并更正测试基线（清理 leader 时随被删代码删掉 18 个测试，10,857 → 10,839）。验证仍是 macOS 一台机器：门禁、`--workspace --tests`、8 个 suite、PTY 烟测；没有 Linux / Windows 结果。
- r5：推送分支、开 draft PR [#7](https://github.com/zy1233/pi-python/pull/7)，并完成阶段 A 与阶段 0 的 0.1 / 0.6。① 第一次 Linux `TUI CI` 全绿后把基线转为阻塞，依赖图上限 995 → 980、告警上限 20 → 0、各 suite 设 `min_passed`；② 入口审计（0.6）按附录 A 的默认执行，A.3 删除 dashboard、白名单外的斜杠命令、agents / extensions / persona 模态、tasks / 后台 / 定时任务、subagents / workflows / goals、共享 prompt 队列、MCP / hooks / plugins / marketplace 入口、rewind / fork / jump、recap / feedback / consent、changelog、`--chat` 世界、session rename 的死链路；③ A.4 去掉各 crate 根的 `#![allow]`（消费者构建 0 告警）、`pi-shell` 的 `pub mod` 降级、删未用依赖与 8 个无人依赖的 crate、删 `cfg(feature = "local-workspace")` 代码；④ `-p` 无沙箱时拒绝运行（ADR7 的最小版本）。净效果：`tui/crates` 的 `.rs` 从 1,252,374 行降到 1,019,816 行，`Cargo.lock` 1,285 → 1,257 个包，Linux 依赖图 995 → 980。新增 §10.7 与附录 A 的 r5 结果。验证：Linux CI（`0e71894`）与 macOS 本机；`5fcec14` 之后的 Linux 结果、PTY 烟测、`release_baseline` 回填后补。阶段 1（协议与 Python）和 0.3 / 0.5 / 0.7–0.9 仍未开始。
