# Rust Agent Runtime 剥离计划

> 状态：r9。r2 的计划稿之上，**已拆除 Rust runtime 并恢复 `/model`（r3）；r4 补了阶段 0 的 Linux CI 与基线工具、清掉 leader 的 UI 状态残留、完成 1.R3 的命名；r5 在 Linux CI 上跑通并启用了基线，执行入口审计（0.6）并完成阶段 A（A.3、A.4）——又删了约 23 万行 Rust、8 个 crate，消费者构建 0 告警**。分支已 push，PR [#7](https://github.com/zy1233/pi-python/pull/7) 已转 ready for review；**r6 清理了品牌残留（把还写着 Grok、或指向不存在的命令 / 目录的提示改成真的东西，见 §10.8），在叠加分支 `codex/branding-cleanup` 上；r7 合并了 `main` 上另一台机器推的 7 个提交，6 个文件的冲突已解，并修了 macOS 上 `AGENTS.md` 被读两遍的 bug（见 §10.9）**；**r8 在 WSL2 / Linux 上补测：退出行为（含沙箱下）、0.9 的真机检查、CI 没跑的 Rust 测试，并修了沙箱 re-exec 缺 `--die-with-parent`、会在 shell 里留下整棵进程树的问题；之后又按用户的选择做了六件事：`-p` 只杀 zypi 时 agent 不再残留、没生效的沙箱现在在屏幕上说一声、agent 的 `stderr` 落到日志文件、CI 清单加了两个 suite 并去掉 `doctor_cmd::` 的 `skip`、PTY 驱动脚本入库、清理 WSL（见 §10.10）**；**r9 在 PR #9 之上补阶段 0 的收尾与阶段 1 里不需要拍板的部分（用户选了 e2e、阶段 0 的门、阶段 1 的免决策项、盯 PR #9 的 CI，没有选「替我拍板」）：0.3 的 Rust 一侧（pager 的 ACP client 对着真的 Python agent 的 10 个 e2e 用例，接进 `tui-ci.yml`；它第一次跑就抓出两个产品 bug——agent 起不来时的原因被吞成「channel closed」、agent 一启动就退出时 `connect` 挂住——都修了）、0.7（会话列表与 `session/load` 的延迟实测并修掉读盘的慢点、pager 一侧的读盘调用点清单、13 个旗标 / 子命令的 PTY 验收矩阵、ADR1 的落地设计）、0.8（附录 A 定稿，新增 ACP 版本配对表）、1.P1（pager 跟 `nextCursor`，Python 分页）、1.P4（显式忽略 `mcp_servers` 并说一声）、1.P7 的余项；每一项会改变产品行为的决定（沙箱顺序、`--session-id`、`export`、模式映射……）都只写成提议，见 §10.11 末尾「留给用户决定的」**；执行记录见 §10。§1–§9 保留 r2 原文作为决策依据，与 §10 冲突处以 §10 和 ADR3（r3）为准。负责人：待指派。
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
- [x] 0.2（r5 已做：Linux 一栏的依赖图、告警、测试数已回填，告警清零；手动 run [37389455793](https://github.com/zy1233/pi-python/actions/runs/37389455793) 补齐了 release 数：`zypi` 422,034,120 B，比拆除前小 10.3 %，冷缓存构建 1,155 s，比 1,647 s 快 29.9 %，`--version` 中位数 23.1 ms）**基线报告**入库：测试通过 / 失败 / 忽略数、`cargo tree -p pi-pager-bin` 节点数、release 二进制体积、冷编译与启动耗时、去掉全局 allow 后的告警数。（r4 已入库 [`docs/baselines/tui.md`](../baselines/tui.md)：拆除前（v0.4.0）与拆除后（macOS）的依赖图节点数、告警数、8 个 suite 的测试数、v0.4.0 的 release 体积与构建耗时；**Linux 一栏、拆除后的 release 体积 / 冷编译耗时 / `--version` 延迟在 r5 回填**，「到欢迎页」的启动耗时需要 PTY 载体，未测。「去掉全局 allow 后的告警数」随 A.4 做。）
- [x] 0.3（r5：Python 侧的 stdio 契约套件，见 1.P7；**r9：Rust 一侧做完**——`tui/crates/codegen/pi-pager/tests/python_agent_e2e.rs`，10 个用例，接进 `tui-ci.yml`，见 §10.11）**ACP 契约 / e2e 载体**：Rust client ↔ Python mock agent（脚本化 `stream_fn` 的 `PiAcpAgent` stdio 进程），覆盖 initialize、new、prompt 流式、权限往返、cancel、list、load、Python 缺失时报错、子进程退出；不依赖 PTY（PTY 烟测可选）。
- [x] 0.4（r4 已做）**deny-list**：硬性 `pi-sampler`；条件性 `async-openai`、`pi-sampling-types`（由阶段 D 决定）；对 `pi-tools`、`pi-agent`、`pi-workspace`、`pi-hooks`、`pi-mcp` 逐个写「保留理由或缩减方案」。（`pi-sampler` 及 `MvpAgent` / `acp_session_impl` / `SamplerActor` 三个词是 `tui_baseline.py gates` 的阻塞门禁；七个条件性 crate 的直接依赖者、理由与缩减方案见 [`docs/baselines/tui.md`](../baselines/tui.md) §4，依赖者列表每次 `check` 重新生成。）
- [ ] 0.5 **模块级调用图 → support 保留清单**：把附录 B.2 的脚本入库为工具，取代「举例」式清单。
- [x] 0.6（r5 已做：用户选「直接按附录 A 默认执行，不逐项确认」；结果是 A.3 的删除，分类见 §10.7，附录 A.4 的 r5 执行结果）**入口审计**：从 `Effect` / `Action` 枚举与键位表出发，逐入口标注可达性、所需 ACP 方法、决策（保留｜隐藏｜删除）；覆盖斜杠、键位、模态、欢迎页、dashboard、状态栏，并复用现有 19 处「standard ACP」降级点。
- [x] 0.7（r9，§10.11：pager 一侧 13 处读盘调用、8 个文件——r2 估的「约 19 个文件」把实现层也算在内；冷启动延迟实测；13 例 PTY 验收矩阵 `scripts/tui_pty/resume_matrix.py`；落地设计含沙箱顺序与 PR 切分。**设计成文，落地没动**，等用户确认 §10.11 末尾的提议）**磁盘读取点清单**与 ADR1 的落地设计。
- [x] 0.8（r9，附录 A：A.1 能力矩阵逐行标了「已定」「提议」或「未定」，A.2 版本配对，A.3 逐方法的两端支持情况；「提议」的行改行为，等用户确认）**能力矩阵定稿**（附录 A）与 **ACP 版本配对表**（两端各方法的支持情况、升级策略；Python 侧不得依赖 Rust 端 unstable 方法）。
- [ ] 0.9（r8 部分：**Linux / WSL2 上六项都有了记录**，见 §10.10；**没做**：沙箱的 Landlock 层——WSL 的内核 5.10 没有 Landlock，要在内核 ≥ 5.13 的机器上做，`pi-sandbox` 的端到端用例在这里软跳过；macOS 与 Windows 上对应各项）**实测结案**：子进程是否继承沙箱；`zypi export <python-session-id>`；`/model` 经 `session/set_config_option` 的真机切换（r3 已用 OpenRouter 实测 Python 侧，见 §10.3；TUI 全屏交互仍需 PTY 实测）；子进程 `stderr` 对全屏 TUI 的影响；退出后是否残留 Python / bash 进程；`config.toml` 的解析严格性。**Linux 上的结论**：沙箱——bwrap 层被 agent 与工具继承，Landlock 层不存在时 `zypi` 照常启动，原先屏幕上没有任何提示（现在启动时 `stderr` 有一行警告，欢迎页与状态栏标 `sandbox:<profile> (not enforced)`）；`zypi export <Python 会话>`——`Session '…' not found.`，退出码 1；`/model` 与全屏交互——PTY 烟测 18/18（入库后的脚本是 23/23）；`stderr`——TUI 模式下 agent 的 stderr 是 `/dev/null`，对全屏界面没有影响（原先一个字也看不到，现在落到 `agent.stderr.log`）；残留进程——TUI 的退出矩阵全部干净（沙箱下补了 `--die-with-parent` 之后），`-p` 模式下只杀 zypi 会留下 agent（已修：Python 一侧监视父进程）；`config.toml`——未知的小节与顶层键被忽略；TOML 不合法、Python 风格的顶层 `permission = "ask"`、`[[models]]` 数组这三种会让 `zypi` 起不来（退出码 1，错误信息直接打印）；`[agent] command` 写错类型不报错。

**退出条件**：CI 绿且基线入库；矩阵、入口审计、deny-list 无「未决」行（有则标 owner 与日期）；0.9 每项有记录。

### 阶段 A：死代码先行（不依赖 Python，零行为变化）

- [x] A.1（r3 已做）删除 8 个无入口模块（4.6k 行）、`headless.rs` + `headless/`（约 7.5k 行）及 `main.rs:667-731`、`spawn_agent_thread_direct`、`warm_async_http_client()`、`main.rs` 的 `run_*` / leader 导入。
- [x] A.2（r3 主体已做，r4 清掉 pager 的 UI 状态残留；仍带 leader 字样的代码是别的东西，见 §10.4）leader 全链路：pager 侧 `--leader` / `--no-leader` / `--leader-socket`、`[cli].use_leader`、`resolve_leader_mode`（`app/mod.rs:435-506`，单测在 `:1917-1937` 一带）、`kill_stale_reachable_leaders`、`connect_via_leader`（`acp/mod.rs:288-420`）及 `app/mod.rs:954-976` 的 leader→embedded 回退、`acp/leader_bridge.rs`、`acp/version_mismatch.rs`、`AcpConnection.leader_status_rx`、`app/leader_cluster`；shell 侧 `leader/`（8.7k 行）与 5 个 `test_leader_*.rs`；内嵌文档中的 leader 段落（8 个文件 22 处）。
- [x] A.3（r5 已做；r3 没做：没有执行入口审计，只删了 runtime 与连带死代码）白名单外的斜杠命令模块（`6983c99`，77 个文件）及 `builtin_commands()` 注册；入口审计判为「删除」的 dashboard（`2bef92d`）、agents / extensions / persona 模态、tasks / 后台 / 定时任务、subagents / workflows / goals、共享 prompt 队列与 steer、MCP 模态 / elicitation / Claude 导入、hooks / plugins / marketplace、rewind / fork / jump / 外部会话、recap / feedback / consent 等，以及 session rename 的整条死链路和恒假的 `chat_mode` 世界。**没做**（有意留着，见 §10.7）：认证 / 计费界面（3d-4，归阶段 D.3）、CLI 旗标瘦身（`--worktree`、`--restore-code` 等，3d-6）、plan 审批 / btw / cta（3f）。
- [x] A.4（r5 已做；r3 没做：消费者构建还有 20 条 `dead_code` 警告）去掉四个 crate 根的 `#![allow(unused_imports, …, dead_code)]` 并清零告警（`4eb9f11`）；死 `pub mod` 降为 `pub(crate)`（pi-shell，`ab77740`）；未用依赖（`fbcaf14`）与无人依赖的 crate（`86ea4c9`、`886d5af`）；`cfg(feature = "local-workspace")` 代码与 Cargo feature（`0e71894`）。基线 `max_warnings` 20 → 0。其余 crate 的 `pub` 瘦身没有做（pi-shell-base 等，见 §10.7 的遗留）。

**退出条件（机检）**：`rg -w MvpAgent tui/crates/codegen/pi-pager tui/crates/codegen/pi-pager-bin` 无命中；无全局 allow 的 `cargo check -p pi-pager-bin` 零告警；测试与基线对比不退化（被删代码的测试除外）；CI 绿。（r3 对照：`rg` ✓；零告警 ✗，仍有 20 条 `dead_code`；基线对比与 CI ✗，阶段 0 未做。**r5 对照**：`rg` ✓；零告警 ✓（Linux CI 与 macOS 都是 0）；基线对比 ✓（`min_passed` 下限已设，被删代码的测试已在清单里按 Linux 数重定基线）；CI ✓。）

### 阶段 1：协议与 Python（依赖 Python）

Python（`pi_agent_cli`）：

- [x] 1.P1（r5：`title` = 会话第一条 user message，`updated_at` = 会话文件 mtime，`50ccfb1`；**r9：游标分页做完**——先让 pager 跟游标（`9f7bc35`：`fetch_session_list` 跟 `nextCursor` 到没有，上限 100 页，空游标或重复游标就停），再让 Python 分页（`c1f02c8`：每页 50、无状态游标、非法游标 `invalid_params`），harness 一侧配 `list_page` / `find`（`1b9fb47`）与读头部不读全文件（`54a38fe`）；1000 个会话时首页 2.0 s → 0.11 s，见 §10.11）`list_sessions`：真实 title、`updated_at`（最后活动）、`cursor` 分页（ACP `session/list` 字段，[RFD](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/docs/rfds/session-list.mdx)）。
- [x] 1.P2 优雅退出（r5，`f14e321`）：处理 stdin EOF / SIGTERM，回收工具进程组。实测（macOS，脚本化的 bash 调用）：stdin EOF 本来就是优雅的——`run_agent` 返回，`asyncio.run` 取消在途 turn，bash 工具杀进程组，退出码 0、工具不残留；SIGTERM 则立刻终止进程（−15），工具残留。现在 `__main__.serve()` 把 SIGTERM 与 SIGHUP（`421d497`：终端消失、或 zypi 作为会话首进程被杀时，内核把 SIGHUP 发给前台进程组，agent 在其中，默认动作让它立刻死掉）接到同一条路径（POSIX；Windows 没有 `add_signal_handler` 与 SIGHUP，只靠 EOF）。处理器只生效一次（第一个信号后就摘掉），所以停到一半卡住的 agent（比如 `asyncio.run` 在等一个取消不了的线程）仍可被第二个 SIGTERM 杀掉。SIGKILL 无法处理，工具仍会残留，所以 Rust 一侧不能一上来就 SIGKILL（1.R3）。契约：`tests/test_acp_shutdown.py`（EOF、SIGTERM、SIGHUP 都要退出码 0 且回收工具；重复的信号要能杀掉卡住的 agent；去掉 SIGTERM 处理器后第一个用例以 −15 失败，去掉自摘除后最后一个用例超时）。
- [ ] 1.P3 `set_session_mode`（或 `configOptions` 的 `mode`）取代仅靠 `ext_notification` 的权限模式切换，映射由矩阵定。
- [x] 1.P4（r9，`935e4b8`、`7becb49`、`9798e1d`）`mcp_servers`：**显式忽略并在文档声明**（Phase 4 非目标），或实现。做了前者：pager 不再发（`session/new` / `load` 的 `mcp_servers` 恒为空，有单测）；Python 收到非空列表时记一条警告并发一条只含名字的 agent 消息（`mcp_notice.py`、`tests/test_mcp_notice.py`）；`packages/pi-agent-cli/AGENTS.md` 的「MCP servers」与 `docs/TUI-AND-CODE-AGENT.md` 写明这是对 ACP「agent 必须支持 stdio 服务器」的有意偏离。`pi_shell::util::config::load_mcp_servers` 与 `mcp.rs` 现在没有调用者（清理候选，见 §10.11）。
- [ ] 1.P5（r5 部分：`initialize` 公布了 `session/close` 与 `session/resume`，但 `run_agent` 没开 `use_unstable_protocol`，SDK 对这两个方法回「Method not found」，`5163592` 已改；其余公布项与 pager 需求的对照没做）`initialize` 的 capabilities / `_meta`：只公布 pager 实际需要且已登记（P2）的键。
- [ ] 1.P6 `-p` 旗标契约：以 `__main__` 接受的旗标为权威；每个 `PagerArgs` 旗标逐项「支持｜非 0 退出并报错｜文档删除」。
- [x] 1.P7（**r9：权限往返与 cancel 的线上用例补上了**——`28fed94`：`tests/test_acp_stdio_tools.py`，用 `_tool_agent.py` 让一次脚本化的 tool call 走完真实 stdio，Rust 一侧的同类用例见 0.3；r5 部分：`packages/pi-agent-cli/tests/test_acp_stdio_contract.py` 起子进程走 stdio，8 个用例——initialize、new、流式 prompt、`model` 配置项、重启后 list / load 回放 / resume、close、未知方法与连接存活、stdin EOF 退出码 0；`PI_ACP_CONTRACT_COMMAND` 可对准别的 stdio ACP agent。当时没有权限往返与 cancel 的线上用例，因为 mock 流不会产生 tool call，r9 的 `_tool_agent.py` 补上了）契约测试：mock `stream_fn` 驱动 ACP stdio，断言方法面（含既有的「不注册 `x.ai/`」）。

Rust（pager）：

- [ ] 1.R1（r9：读盘调用点清单、落地设计、PR 切分、验收矩阵与延迟实测都在 §10.11；**没有动手**——沙箱顺序、`--session-id` 契约、`export` 的去向要用户确认，见该节末尾）ADR1 落地：移除对 session 文件的读取；`export` 的去向。
- [ ] 1.R2 按能力隐藏 Python 不路由的方法的入口（`session/set_model`、`session/set_mode` 等）；method-not-found 不 panic、不重复报错；`/model` 按 ADR3 恢复（r3 已做，不在隐藏之列）。
- [ ] 1.R3 子进程生命周期（P6）。**r4 已做（命名与提示语，行为不变）**：`SpawnedAgent` → `AgentProcess`（`thread_handle` → `bridge_thread`，`AcpConnection.agent_thread` → `bridge_thread`）、`AgentShutdownGuard` → `AgentProcessGuard`、`spawn_grok_shell` → `spawn_agent_process`（顺手去掉没人用的 `_memory_config` 参数）、`SESSION_FLUSH_GRACE` / `AGENT_JOIN_SLACK` → `AGENT_EXIT_GRACE` / `BRIDGE_JOIN_SLACK`（仍是 10 s + 2 s，`exit_timeout` 的 20 s 预算不变）、`join_agent_thread` → `join_bridge_thread`；慢退出提示从「Finishing session…」改为「Stopping agent…」，超时告警不再声称 SessionEnd teardown 可能不完整，只说 agent 进程可能还在；守卫的文档改为只有 `app::run` 持有。**r5 已做（`e8557e5`）**：② 优雅退出——取消时先关 agent 的 stdin，等它自己退出（`AGENT_EOF_GRACE` = 3 s，编译期断言小于 `AGENT_EXIT_GRACE`），超时才 `start_kill()`；退出期间继续读（并丢弃）agent 的 stdout，免得它写到已关闭的管道；③ 的大部分——用脚本化 agent 在 bash 工具（`exec sleep 300`）运行时从 PTY 里退出 zypi，见 §10.7 的表：双击 Ctrl+Q 旧版残留、新版回收；`kill -9 zypi`（zypi 是会话首进程）要 Python 也处理 SIGHUP 才回收；SIGTERM、关终端、单按 Ctrl-C 都不残留。**r8 补（Linux / WSL2，§10.10）**：③ 里 Linux 的部分做完了——zypi 是会话首进程、以及只是 shell 里的一个作业（`kill -9` 时 agent 只看到 stdin 的 EOF）两种启动方式，乘以六种退出方式，含沙箱下，都不留进程；这一遍测出沙箱 re-exec 缺 `--die-with-parent`（只杀 bwrap 的做法——`kill $!`、`timeout`、IDE 的停止按钮——会把整棵树留下），已补。① 的前提也变了：agent 的 `stderr` 在 TUI 里是 `/dev/null`（`app::run` 一开头的 `redirect_native_stderr()` 把 fd 2 指过去，agent 继承的就是它），不是「写在 TUI 所在的终端上」，所以对全屏界面没有影响，代价是 agent 的 stderr 一个字也看不到、也没处可查；`-p` 路径不做这个重定向，继承终端。**随后用户选了做**：① 的去向——agent 的 `stderr` 现在追加到 `<home>/logs/agent.stderr.log`（0600，每次启动一行标记，超过 1 MiB 在下次启动时轮转；§10.10 发现 2）；`-p` 模式下只杀 zypi 时 agent 子进程残留——Python 一侧监视父进程（`PI_AGENT_PARENT_PID`，§10.10 发现 6）。**r9 补（`affda9e`，§10.11）**：agent 自己退出了，pager 现在知道也说得出原因——桥在等取消的同时等子进程，退出后排空它已写出的输出，拼一条消息（程序、退出状态、`agent.stderr.log` 的末尾几行），让在途请求以失败结束，`connect()` 把失败的 `initialize` 解释成这条消息，TUI 起来之后由 `AgentProcessGuard` 在还原的终端上打印；e2e 里 agent 起不来、一启动就退出、回合中途被杀都有用例。`zypi` 自己的退出状态没动。**未做**：MCP 子进程；Windows 上的行为。
- [ ] 1.R4 `-p` 派发顺序与沙箱（ADR7）。
- [ ] 1.R5 解码精简：`initialize._meta` 只读 Python 实际公布的键；`pi/*` 解码按 ADR4 清理。

**退出条件**：ACP 契约 / e2e 通过；矩阵无「未决」行；`-p` 每个旗标都有去向；`zypi` 正常退出、崩溃、Ctrl-C 之后无残留 Python 与 bash 子进程（Linux、macOS 实测，Windows 记录现状）。（r8 对照：Linux、macOS 的实测都有了，Linux 见 §10.10；Windows 仍未测。）

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
| R5 | 子进程生命周期：孤儿 bash 进程、Windows 行为未知 | 阶段 1 的优雅退出协议 + 实测；Windows 先记录现状。r5：macOS 上已实测并修掉退出 zypi 后残留的 bash 进程（§10.7）；zypi 不是会话首进程时 `kill -9` 的端到端情形与 Windows 当时仍未测。r8：Linux（WSL2）上两种启动方式 × 六种退出方式都实测了，含沙箱下，并补上沙箱 re-exec 的 `--die-with-parent`（§10.10）；`-p` 模式（只杀 zypi 时 agent 子进程残留）随后也修了：Python 一侧监视父进程（§10.10 发现 6）；Windows 仍未处理 |
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

## 10. 执行记录（r3、r4、r5、r6、r7、r8、r9）

r3 执行了「拆除 Rust runtime + 恢复 `/model`」；r4 在其上补了阶段 0 的 CI / 基线、清理 leader 的 UI 残留和生命周期命名；r5 把分支推上远端、在 Linux 上启用基线，执行入口审计并完成阶段 A（§10.7）；r6 清理品牌残留（§10.8）；r7 合并 `main`（§10.9）；r8 在 WSL2 上补 Linux 一侧的实测（§10.10）；r9 补阶段 0 的收尾与阶段 1 里不需要拍板的部分（§10.11）。下表是 r3、r4 的提交，按当时的状态保留（当时都是本地提交、**未 push**），分支是 `codex/rust-agent-runtime-removal-plan`：

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
- **子进程生命周期（1.R3）：命名与提示语 r4 已改（见 §10.6），行为没动（r5 起 pager 先关 stdin 再杀，见 §10.7）**。r4 的实测只覆盖正常退出（`/exit` 后 Python agent 随 stdin 关闭退出，无残留）；`stderr` 去向、优雅退出（1.P2）、崩溃 / 中途 Ctrl-C 之后是否残留 Python 或 bash 子进程都还没做 / 没测，见 §7 的 1.R3。
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
| `dbd4de6` | `pi-pager` 的普通依赖 `pi-agent` 降成 dev-dependency（agents modal 删后只剩测试在用） | 1 |
| `cca48b0` | 文档：计划 r5、基线报告 | 3 |
| `ac1d3ff` | 删欢迎页「New worktree」菜单项、ctrl+w、New Worktree 对话框、会话选择器的 ctrl+w「在 worktree 里恢复」，以及英雄区宣传已删除 `/feedback` 的副标题；顺手删 `cloud_modal_open`（恒 false）与随之无人调用的 `LineEditor::insert_paste_with_byte_limit` | 13 / +15 −790 |
| `50ccfb1` | Python：`session/list` 的 `title` 取会话第一条 user message、`updated_at` 取文件 mtime（1.P1 的一半） | 5 |
| `5163592` | Python：stdio ACP 契约套件；`run_agent` 开 `use_unstable_protocol`，让已公布的 `session/close`、`session/resume` 真正可用 | 3 / +208 |
| `d775a54` | 文档：release 基线与 `ac1d3ff` 的 Linux 数字、PTY 烟测的发现、阶段 1 的开头 | 3 |
| `f14e321` | Python：SIGTERM 与 EOF 走同一条停止路径（1.P2）；`tests/test_acp_shutdown.py` + `tests/_tool_agent.py` | 4 / +200 −3 |
| `e8557e5` | Rust：退出时先关 agent 的 stdin、等 `AGENT_EOF_GRACE`、超时才 SIGKILL；退出期间排空 stdout（1.P2 / 1.R3） | 1 / +119 −29 |
| `259b056` | 文档：计划记下优雅退出与残留实测 | 1 |
| `421d497` | Python：SIGHUP 与 SIGTERM、EOF 同路径（`kill -9 zypi` 时内核发给前台进程组的 SIGHUP 会让 agent 立刻死掉）；关停测试对 SIGTERM / SIGHUP 参数化 | 3 |
| `52a787f` | 文档：计划记下退出方式矩阵（哪些退出路径会留下工具进程） | 1 |
| `b617575` | Python：停止信号的处理器只生效一次，第二个信号走默认动作（停到一半卡住的 agent 仍可被杀）；关停测试加「卡住的线程」模式 | 5 |
| `34fbd48` | 文档：计划记下 `b617575` 的 Linux CI 结果 | 1 |

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
| Linux CI，`cca48b0` | `TUI CI` run [37385642711](https://github.com/zy1233/pi-python/actions/runs/37385642711)；`CI` run 37385642721 | ✓ 依赖图 980、0 告警；7,457 通过 / 0 失败 / 20 忽略（`pi-pager` 5,572） |
| Linux CI，`ac1d3ff` | `TUI CI` PR run [37389447358](https://github.com/zy1233/pi-python/actions/runs/37389447358)；`CI` run 37389447665；手动 run [37389455793](https://github.com/zy1233/pi-python/actions/runs/37389455793)（带 `release_baseline`） | ✓ 依赖图 980、0 告警、`--workspace --tests` 0 个错误；8 个 suite 共 7,437 通过 / 0 失败 / 20 忽略（`pi-pager` 5,552）；release：见 0.2 |
| macOS 本机，`ac1d3ff` | `errs.py`（pi-pager、pi-pager-bin，全 target）；`cargo test -p pi-pager --lib`（`--test-threads=2`）与 `--test settings_e2e` / `grok_home_paths` / `selection_model_public_api` | ✓ 0 错误 0 告警；5,307 通过 / 0 失败 / 13 忽略；251；2；2 |
| 本机 PTY，`ac1d3ff`（`PI_USE_MOCK=1`） | 欢迎页 → `/help`、`/settings`、`/theme`、`/resume`、`/model` 开合 → `/exit`；一轮对话 → `/exit` → 同一 `PI_HOME` 重启 → `/resume` 选回上一个会话 → 历史回放 → `/exit` | ✓ 全部走通，退出码 0，无 panic，`/exit` 之后没有残留 `pi_agent_cli` 进程；欢迎页只剩「Resume session / Quit」，ctrl+w 不再弹对话框 |
| 本机，`5163592` | `pytest -m "not real_llm"`；`ruff check .` / `ruff format --check .` | ✓ 617 通过（r4 的 598 之外新增 `session_list` 11 个、stdio 契约 8 个）；通过 |
| 本机，`f14e321` | `pytest -m "not real_llm"`；`ruff check .` / `ruff format --check .`；`test_acp_shutdown.py` 连跑 3 次；去掉 SIGTERM 处理器再跑（变异检查） | ✓ 619 通过；通过；3 次都稳定；SIGTERM 用例以 −15 失败，EOF 用例仍通过 |
| 本机，`421d497` | `pytest packages/pi-agent-cli -m "not real_llm"`；`ruff check .` / `ruff format --check .` | ✓ 116 通过（SIGHUP 用例 +1，全仓 620）；通过 |
| 本机，信号处理器自摘除 | `test_acp_shutdown.py` 4 个用例；去掉自摘除再跑（变异检查） | ✓ 通过；「重复的信号」用例超时失败 |
| macOS 本机，`e8557e5` | `cargo test -p pi-pager --lib acp::spawn`（含 2 个新测试）；`cargo build -p pi-pager-bin`（`CARGO_INCREMENTAL=0`、2 个 job） | ✓ 10 通过 / 0 失败；构建 0 告警（之后只有 rustfmt 的折行，未重编） |
| 本机 PTY，`e8557e5` / `421d497` | `/tmp/rr/tui_orphan.py <模式>`：`PI_AGENT_COMMAND` 指向 `tests/_tool_agent.py`，发一句话让 agent 跑 `exec sleep 300`，工具运行时按模式退出 zypi，查 `sleep` 是否还在；另跑一遍 mock 的 `/exit` | 见下表；mock 的 `/exit`：退出码 0，无残留进程，终端上没有 Traceback / BrokenPipe |
| Linux CI，`b617575` | `TUI CI` PR run [37394965723](https://github.com/zy1233/pi-python/actions/runs/37394965723)（check + test 26 m 39 s）；`CI`（Python 3.11–3.13）run 37394965713 | ✓ 依赖图 980、0 告警、`--workspace --tests` 0 个错误；8 个 suite 共 7,439 通过 / 0 失败 / 20 忽略（`pi-pager` 5,554，比 `ac1d3ff` 多 `spawn.rs` 的 2 个单元测试）；Python 全绿（含 stdio 契约套件与关停测试） |

**这一轮没有验证的**：Windows；zypi 不是会话首进程时被 `kill -9`（agent 只看到 EOF）的端到端情形；MCP 子进程；退出行为的 PTY 实测只在 macOS 上做过（Linux CI 跑了 Python 的关停测试与 Rust 的单元测试，没有 PTY 烟测）；用真实模型跑 PTY——OpenRouter 的免费 slug `qwen/qwen3.8-27b:free` 现在回 404（付费 slug 要花钱，由用户定），所以 `ac1d3ff` 的 PTY 烟测用的是 agent 自带的 mock LLM。

**退出时工具进程是否残留（agent 在跑 `exec sleep 300`，本机 PTY，zypi 是会话首进程）。**

| 退出方式 | 旧 Rust（`ac1d3ff`） | 新 Rust（`e8557e5`）+ Python 只处理 SIGTERM | 新 Rust + Python 处理 SIGTERM 与 SIGHUP（`421d497`） |
|---|---|---|---|
| 双击 Ctrl+Q | 残留（退出码 0，0.3 s） | 回收（2 次，0.3 s） | 回收（退出码 0，0.4 s） |
| `kill -9 zypi` | 未测 | 残留（内核给前台进程组发 SIGHUP，agent 立刻死掉） | 回收 |
| `kill -TERM zypi` | 未测 | 回收（退出码 0，0.1 s） | 回收（退出码 0，0.1 s） |
| 关闭 PTY master（关终端窗口） | 未测 | 回收（退出码 0，0.1 s） | 回收（退出码 0，0.1 s） |
| 单按 Ctrl+C | 未测 | 回收（取消当前 turn，zypi 继续运行） | 回收（同左） |

每格一次运行，除注明的；工具是否还在，是退出后最多等 6 s 轮询 `kill -0` 的结果。

**PTY 烟测的发现（已处理 / 待处理）。**

- 已处理（`ac1d3ff`）：欢迎页的「New worktree」菜单项与 ctrl+w 弹出的对话框是死入口——回车后只会显示「Cannot create worktree: Git worktree sessions are not supported in standard ACP mode」；英雄区副标题还在宣传已删除的 `/feedback`。
- 已处理（`50ccfb1`）：`/resume` 选择器里每个会话的标题都是「ISO 时间戳 (短 id)」，`updated_at` 是创建时间。
- 已处理（`5163592`）：契约测试发现 `initialize` 公布的 `session/close`、`session/resume` 在线上回「Method not found」。pager 没有调用点，所以此前没人发现。
- 已处理（`f14e321`、`e8557e5`）：运行 bash 工具时退出 zypi 会留下工具进程——Rust 一退出就 SIGKILL agent，agent 来不及回收工具的进程组（PTY：双击 Ctrl+Q 之后 `sleep 300` 还在）。同时量到：stdin EOF 本来就能让 Python agent 干净退出（退出码 0、工具回收），SIGTERM 不行（−15、工具残留），`kill -9 zypi` 时 agent 因 SIGHUP 立刻死掉、工具残留。修法：Python 把 SIGTERM 与 SIGHUP 接到 EOF 那条路径；Rust 先关 stdin，等 3 s，超时才 SIGKILL。
- 待处理：冷启动后 `/resume` 选择器的第一项是当前这个刚创建的空会话（标题现在是「(no messages)」），真正想恢复的要按一下 ↓；pager 不跟 `nextCursor`（见 1.P1）。

**遗留与建议的顺序。**

1. ~~PTY 烟测~~：已做（上表与上面的发现）。
2. ~~`release_baseline`~~：已做（0.2 勾选）。
3. **A.3 的尾巴**（可选，同样的折叠器办法）：3d-6 CLI 旗标瘦身——欢迎页与选择器里的 worktree 入口已在 `ac1d3ff` 删掉，但 `-w/--worktree`、`--worktree-ref`、`--restore-code`、`/new` 的 worktree 模式（`new_session_worktree_mode`、`Action::NewWorktreeSession`、`Action::ChooseNewSessionMode`）和 `allow_remote_restore` / `suppress_code_restore` 的「远端恢复」世界仍在，标准 ACP 下 `Effect::CreateWorktreeSession` 恒返回 `WorktreeSessionFailed`，这些路径恒假；它们归 1.P6 的旗标契约一起定。欢迎页隐私横幅（`views/privacy_banner.rs`，「Help improve Grok」数据共享广告）按代码读只有设了环境变量 `GROK_PRIVACY_NOTICE_ROLLOUT` 或远端设置才会出现（没有实测），同属「靠数据恒假」的世界，约 1k 行、15 个文件。3f plan 审批 / btw / cta；3d-4 认证 / 计费界面归阶段 D.3。
4. **A.4 的尾巴**（可选）：`pi-shell-base` 等其余 crate 的 `pub` 瘦身（没做过）；`pi-shell-base/src/env.rs` 里死掉的 gateway-bridge 常量；约 25 处局部 `#[allow(dead_code | unused*)]`（有的是平台门控，要看 Linux）；`pi-shell/src/session/storage` 的 `relocation`（`#[allow(dead_code)]`，归 D.2）。
5. **阶段 1 才开了个头**：做了 1.P1 的一半（标题与 `updated_at`）、1.P5 的一个 bug（`session/close` / `resume` 的路由）、0.3 / 1.P7 的 Python 一侧（stdio 契约套件）。余下的需要拍板：① 优雅退出（1.P2、1.R3 ②）**已做**（`f14e321`、`e8557e5`），宽限取了 3 s（`AGENT_EOF_GRACE`，一个常量，要换数字由用户定）；还剩 ~~`stderr` 去向（1.R3 ①）~~（r8 随后做了：落到 `agent.stderr.log`，§10.10）、~~zypi 不是会话首进程时被 `kill -9` 的端到端情形、Linux 上的退出行为~~（r8 已做，§10.10）、Windows 上的退出行为；② pager 跟 `nextCursor`（才能在 Python 端分页）；③ 1.R1 ADR1（`--continue` / `--resume` 的磁盘读取）；④ 1.P6 旗标契约。阶段 0 的 0.3 的 Rust 一侧、0.5、0.7–0.9 仍未做。
6. **文档与声明**：C.2 内嵌 user-guide；C.3 `tui/NOTICE` 与 `THIRD-PARTY-NOTICES`（依赖已少了 28 个包，需要重新生成；对外发布二进制前由用户定措辞）。**品牌残留**：~~用户看得见的~~ r6 已清理（§10.8；用户没给新名字，沿用 zypi）。没清的是用户看不见的标识，列在 §10.8 的「有意没动的」。

### 10.8 r6 追加：品牌残留清理

**起点与决定。** §10.7 遗留第 6 项把品牌残留留给用户定名字。用户选了「清理 Grok 品牌残留」，没有给新名字；r6 沿用产品里本来就有的名字 **zypi**（`brand.rs` 的 `PRODUCT_TITLE`、终端标题、英雄区、`--help` 早就是它）。要换名字，改 `brand.rs` 的常量和下面两个主题 id 即可。工作在分支 `codex/branding-cleanup` 上，作为叠在 PR #7 之上的 PR（base 是 `codex/rust-agent-runtime-removal-plan`），不动 #7。

**改了什么。** 只改用户看得见的。其中三处不只是名字问题，而是提示指向了不存在的东西：

1. **`zypi doctor fix ssh-wrap` 会写出坏掉的 alias。** 受管块里写的是 `alias ssh='grok wrap ssh'`，`grok` 这个命令不存在，用户一执行 fix，之后每次 `ssh` 都会失败。现在写 `alias ssh='zypi wrap ssh'`，块标记从 `# >>> grok doctor >>>` 改为 `# >>> zypi doctor >>>`（以前跑过 fix 的人要手动删掉旧块；旧块不会被新版识别）。
2. **路径提示指向不存在的目录。** 配置目录在消息里叫 `~/.grok` / `$GROK_HOME`，实际是 `~/.pi-python` / `$PI_HOME`（`pi-home` crate）。设置页脚、复制提示里的备份文件路径、doctor 对 `config.toml` / `sandbox.toml` 的指引，都会让用户去打开一个不存在的文件。现在 `display_grok_home_prefix_for` 返回 `~/.pi-python` / `$PI_HOME`。
3. **提示里的命令不存在。** 内置斜杠命令里没有 `/doctor`、`/minimal`、`/fullscreen`、`/copy`；CLI 里没有 `grok wrap`、`grok worktree gc|rm|db rebuild`。改成真有的：`zypi doctor`、`zypi wrap ssh <host>`、`zypi --minimal` / `--fullscreen`；`zypi du` 里指向 `worktree gc / rm / db rebuild` 的提示删掉，只留「删掉上面列出的、不再需要的 worktree」。

| 位置 | 之前 | 之后 |
|---|---|---|
| `/theme`、设置里的主题 | `Grok Night` / `Grok Day`，落盘 id `groknight` / `grokday` | `zypi Night` / `zypi Day`，落盘 id `zypinight` / `zypiday`。旧 id 与别名（`groknight`、`grok-night`、`GrokNight`、`grokday`、`grok-day`、`dark`、`light`、`day`）仍可解析，读到旧值会规范成新 id，已有的 `config.toml` 不用改 |
| 桌面通知标题 | `Grok`（审批请求、会话就绪、回合结束） | `zypi`（`brand::PRODUCT_TITLE`）；通知命令模板的注释同改 |
| `zypi doctor` | 标题 `Grok Doctor`，结尾指向不存在的 `/doctor` | `zypi Doctor`，说明某些检查只在运行中的 zypi 会话里做 |
| 启动提示、SSH 提示 | 「Run /doctor for details and fixes.」 | 「Run zypi doctor for details and fixes.」 |
| 设置页 | 「Switch this session only with /minimal or /fullscreen」「Show a /doctor tip…」「(Grok STT)」 | 「…by starting zypi with --minimal or --fullscreen」「Show a zypi doctor tip…」，去掉 Grok STT |
| 复制提示 | 「use grok wrap or /minimal」「Try /doctor or /minimal」 | 「use zypi wrap or --minimal」「Try zypi doctor or --minimal」 |
| `--minimal` 模式 | 欢迎页标题 `Grok Build`；信任提示「Grok Build may run or modify…」 | `zypi` |
| 其他 | 启动超时「Couldn't start Grok」、语音「Restart Grok」、状态栏脚本「encode Grok's payload」、`zypi wrap` 的报错前缀 `grok wrap:` | `zypi` |

**提交**（都在 `codex/branding-cleanup`，叠在 `ea5a60d` 之上）：`dbc8d7d` 路径标签与复制提示；`a08f12f` 主题改名与设置页文案；`1f3006b` doctor 与 ssh-wrap fix；`111d46d` 通知标题；`665b539` `zypi du`；`0f05f83` 其余字符串。每个提交带自己的测试，改动里没有重排或格式化。

**有意没动的。**

- 用户设置的接口：`GROK_*` 环境变量名（`GROK_HOME` 一个名字就被引用 241 次，`GROK_SANDBOX` 还出现在 `--help` 里）、项目级路径 `.grok/sandbox.toml`、`zypi du --json` 的 `grok_home` 字段（机器可读，改了要升 schema 版本）。改名是破坏性变更，要用户另行决定。
- 内部标识：`ThemeKind::GrokNight` / `GrokDay`、`Theme::groknight()` / `grokday()`、`theme/groknight.rs` / `grokday.rs`、`assets/grok-night.tmTheme` / `grok-day.tmTheme`、`grok_*` 函数与字段名（`default_grok_home`、`display_user_grok_path`）、HTTP 头 `x-grok-*`、`xai_grok_pager__*.snap` 快照文件名。
- 死世界里的字符串：计费 / SuperGrok（`app/dispatch/billing.rs`）、tutorial（`views/tutorial.rs`）、隐私横幅（`views/privacy_banner.rs`）、`pi-update`（`grok update`、`version_policy.rs`）、`pi-auth` / `pi-shell` / `pi-workspace` 里的 `grok login` 提示、`pi-tools` 里给模型看的参数说明（例如 workflow 工具的 `~/.grok/workflows/`）、ACP 的 auth method「Grok」。它们归 A.3 的尾巴 / 阶段 D；现在改只会和之后的删除互相冲突。
- `tui/crates/codegen/pi-pager/docs/user-guide/`：整套是上游文档（C.2），主题表里还写着 `GrokNight`。旧名字仍是有效别名，文档不会误导，只是旧。

**验证。**

- **编译**：`cargo check -p pi-pager-render -p pi-pager -p pi-pager-minimal --all-targets` 0 错误 0 告警；debug `zypi` 构建 0 告警。
- **单测**（macOS 本机，`--test-threads=2`，只跑相关过滤）：pi-pager-render 的 theme / util / clipboard 265 通过；pi-pager-minimal 79；pi-pager `--lib` 的 theme / settings / doctor / diagnostics / disk_usage / notifications / startup / tips / wrap / status_line 1,068；`settings_e2e` 251；`grok_home_paths` 2；`doctor_early_dispatch`（真二进制，`--ignored`）13，其中包括往 rc 里写 alias 的那一条。
- **新增的回归测试**：旧主题 id 与别名仍可解析、主题的落盘 id 与显示名不含 grok（`pre_rename_theme_names_still_resolve`、`house_themes_are_named_after_the_product`、`current_value_for_theme_reads_pre_rename_ids`）；路径标签指向真实目录（`home_labels_name_the_real_home_directory`）；fix 命令与 alias 用 `brand::CLI_NAME`（`fix_commands_and_alias_name_this_binary`）；`DOCTOR_ACTION` 不带斜杠；复制提示、设置页文案、剪贴板修复提示不含 grok 和不存在的斜杠命令；`zypi du` 不再提示 `worktree gc|rm`、`db rebuild`。
- **PTY**（debug `zypi` + mock agent）：全屏模式扫了欢迎页、命令面板、`/help`、`/theme`、`/settings`（逐行走完整个列表，共 16 屏）、`/model`、一个回合、`/resume`、Ctrl+C 提示；`--minimal` 扫了欢迎页、`/help`、一个回合——含 `grok` 或不存在的斜杠命令的行：0。`config.toml` 里写旧值 `theme = "grokday"`，`/theme` 把 `zypiday` 标成 active；默认家目录时设置页脚写 `~/.pi-python/config.toml`，`PI_HOME` 指到别处时写 `$PI_HOME/config.toml`。
- **CLI**：顶层与每个子命令（`completions`、`doctor`、`doctor fix`、`du`、`export`、`help`、`version`、`wrap`）的 `--help`，加上 `zypi doctor`、`du`、`version` 的输出：只剩顶层 `--help` 里的 `[env: GROK_SANDBOX=]`（环境变量名，有意不动）。
- **已知的本机波动**：有一次过滤运行里 `session_startup::tests::remote_miss_restore_code_with_worktree_defers` 失败。它在 macOS 上读真实的 `~/.pi-python/sessions/<临时仓库路径>`（`/var` 与 `/private/var` 的差别），目录不存在就报错，跟本次改动无关；之后的运行里通过。
- **Linux CI**（`33374a3`，PR [#8](https://github.com/zy1233/pi-python/pull/8)）：`TUI CI` run [37408839608](https://github.com/zy1233/pi-python/actions/runs/37408839608)（check 6 m 27 s，test 19 m 11 s）✓ 门禁全过；依赖图 980、消费者构建 0 告警、`cargo check --workspace --tests` 0 个错误；8 个 suite 共 7,444 通过 / 0 失败 / 20 忽略（`pi-pager` 5,559，比 `b617575` 的 5,554 多 5；上面那条 macOS 本机波动的测试在 Linux 上通过）；`CI`（Ruff + Python 3.11–3.13）run 37408839645 ✓。
- **CI 没覆盖到的**：`TUI CI` 的 8 个 suite 里没有 `pi-pager-render` 和 `pi-pager-minimal`（`scripts/tui_baseline.toml`），所以本次在这两个 crate 里新增和改动的测试——主题别名、路径标签、剪贴板提示、`--minimal` 欢迎页——只在 macOS 上跑过，Linux 上只做了编译检查（`cargo check --workspace --tests`）。把这两个 crate 加进基线是个小改动，但要先在 Linux 上量出各自的 `reference_passed`，而且会改 CI 门禁（`enforce = true`），所以没有顺手做，留给用户决定。（r8 已在 Linux 上量出：`pi-pager-render` 1,092 通过 / 2 忽略、`pi-pager-minimal` 79 通过；后者有个用例缺 `test_lock()`，默认线程数下大概率失败，r8 已补上，并按用户的选择把这两个 crate 加进了清单，见 §10.10。）

### 10.9 r7 追加：合并 `main`（另一台机器推的 7 个提交）

**背景。** 用户另一台机器上的功能提交推到了 `main`：`09fc38d..7e7d4d4`，7 个提交、122 个文件（+21,174 / −809），是 Phase 6 / 7 审计的实现——项目信任（`extension_trust`、`trust_prompt`、`trust_store`）、按 provider 限定的 API key、`Model.reasoning`、workflow 沙箱、工具注解与权限、后台 turn 之后排队的 prompt、0.5.0 发版。**没有一个文件在 `tui/` 下**，Rust 一侧不受影响。两个 PR 相对 `main` 是 61 个提交。

**冲突。** 先用 `git merge-tree` 查，再动手：两边都改过的有 10 个文件，其中 6 个文本冲突、共 17 处——`pi_agent_cli/agent.py`（6）、`config.py`（4）、`factory.py`（3）、`packages/pi-agent-cli/AGENTS.md`（2）、`__main__.py`（1）、`pyproject.toml`（1）。其余 4 个自动合并。冲突全在 Python 一侧，#7 和 #8 的冲突一样（#8 没有新增）。选 merge 而不是 rebase：61 个提交会把同样的冲突逐个重放，而且最终要 squash。

**怎么合。** 两边的功能都留，真正需要决定的只有接缝处的三件事：

- **API key 的归属。** main 规定 `api_key_env` 只给自己的 provider 用（请求别的 provider 时返回 `None`）。我们的 `/model` 里每个模型有自己的 `api_key_env` 和 `provider`，所以改成 `api_key_getter(env_name, provider)`：`make_get_api_key(config)` 是 `[model]` 默认项的特例，`factory.api_key_for_choice(choice)` 给 `[[models]]` 里的某一项用。
- **`Model.reasoning`。** main 在构造 `Model` 时按 `CliConfig.model_reasoning` 设它；我们的 `model_for_choice` 不带，切换模型会把它丢掉。现在 `model_for_choice(choice, reasoning=…)`，值对所有 choice 相同（它是会话级的设置，不是模型的属性）。
- **会话启动。** main 把 `_schedule_deferred_advertise` 扩成了 `_schedule_deferred_setup`（问信任、广播命令、报告被跳过或加载失败的扩展），合并后沿用 main 的。`_bind_session` 先决定信任，再恢复持久化的模型选择，两个都交给 `create_session_harness(trust=…, model_choice=…)`；会话响应仍带 `configOptions` 和每会话的 `pi/*` 提示。

新增测试 `test_a_chosen_model_keeps_the_reasoning_setting_and_gets_only_its_own_key` 守住这个接缝；`packages/pi-agent-cli/AGENTS.md` 的「Model selection」一节记了同样的规则。

**验证（macOS 本机）。** `ruff check` 与 `ruff format --check` 通过（204 个文件）。pytest 全量 1,999 通过、6 失败、33 跳过、31 取消选择（需要真实 API 的测试，main 新加的 `addopts` 默认排除）。**这 6 个失败在纯 `origin/main`（`7e7d4d4`，临时 worktree）上原样复现，与合并无关**，都是 macOS 专属（修掉第一个之后，macOS 上全量是 2,001 通过、5 失败）：

- `test_context_files_are_loaded_whatever_the_trust`：`context_files.py` 的候选名同时有 `AGENTS.md` 和 `AGENTS.MD`（`CLAUDE.md` / `CLAUDE.MD` 同理）。大小写不敏感的文件系统上两个名字命中同一个文件，而 `Path.resolve()` 在 macOS 上保留写法，按路径字符串去重失效，项目的 `AGENTS.md` 会被读两遍、进提示词两次。不是测试的问题，是 Phase 5 起就有的真 bug（`f96ccb1`），main 新加的测试第一次在 macOS 上暴露它。**已在 #8 修掉**：`context_files.py` 改按文件身份（设备号 + inode）去重，不再按路径字符串，硬链接也一并算作同一个文件；新测试 `test_one_file_under_two_names_is_loaded_once` 在大小写不敏感的文件系统上靠文件系统本身、在 Linux 上靠硬链接造出「一个文件两个名字」（拿掉修复，它和 main 的那条测试一起失败）。
- `test_a_path_that_is_not_valid_text_can_still_be_saved`：macOS 不允许非 UTF-8 的文件名（`Errno 92`），测试自己建不出那个目录。
- `test_sandbox_isolation.py` 的 4 个：macOS 上沙箱只报告 `process`、`rlimit-core`、`rlimit-cpu`、`rlimit-nofile`、`rlimit-fsize` 几层，没有 `rlimit-as`；「不靠审计钩子，内核也拦得住子进程和大内存」这一组在 macOS 上不成立；子进程还多了 macOS 自己补的 `__CF_USER_TEXT_ENCODING`。

**PTY 冒烟**（debug `zypi` + 合并后的 agent，`PI_USE_MOCK`）：启动、一个回合、`/model` 选择器列出两个模型、切到第二个并发一轮、切回、`/exit` 退出码 0 且没有残留的 agent 进程。Linux CI 的结果见 PR。

**合入 `main`。** 用户要求 squash、不保留逐个提交。#7 以一个提交合入（`455527c`，树与 #7 的头 `9b71a57` 完全一致）。#8 的基底是 #7 的分支，squash 之后它的历史里还带着那 54 个提交，直接合会在两边都改过的行上冲突，所以先 `git rebase --onto origin/main 9b71a57 codex/branding-cleanup`，只重放 #8 自己的提交（合并提交被丢掉）；重放后的树与重放前（`00df03f`）逐字节一致。再把 PR 的基底改到 `main`，强推，等 CI，squash。

### 10.10 r8 追加：Linux 补测（WSL2）

**起点与范围。** macOS 一侧的工作（r3–r7）已经合入 `main`（PR #6 / #7 / #8）。r8 在 WSL2 里补 Linux 一侧。计划留给 Linux 的有四件，逐项做了：① 阶段 1 退出条件里的「Linux、macOS 实测」——1.R3 没做完的 ③：zypi 不是会话首进程时被 `kill -9`，以及沙箱下的退出行为；② 0.9 的六项真机检查；③ CI 没跑到的 Rust 测试：`doctor_cmd::`（r5 起被 `skip`）、不在 8 个 suite 里的 `pi-pager-render` / `pi-pager-minimal`（§10.8）、其余没选入 suite 的 crate、`pi-fast-worktree`；④ Linux 上的 PTY 烟测（§10.7 写的是「没有 PTY 烟测」）。**已拆成 8 个提交（分支 `codex/linux-wsl-r8`，走 PR 合入，CI 的结果看该 PR）**；仓库里的改动见本节末尾。第一轮测完之后，用户又选了六件事（发现 2、5、6 里的三处行为、CI 清单、PTY 驱动脚本入库、清理 WSL 里留下的东西），做在同一批改动里，在相应条目里标「已改」。

**环境，以及它证明不了什么。**

- WSL2，Ubuntu 24.04.5，内核 `5.10.16.3-microsoft-standard-WSL2`，glibc 2.39，bubblewrap 0.9.0，git 2.43.0，`/bin/sh` 是 dash；rustc 1.94.0（钉的版本）、protoc 28.3、Python 3.12.3。构建与测试的环境变量照 `tui-ci.yml`（`CARGO_INCREMENTAL=0`、`CARGO_PROFILE_DEV_DEBUG=0`），`HOME` 是空目录，`NO_COLOR`、`PI_HOME` 等由清单的 `unset_env` 清掉。
- 在 ext4 上的克隆里跑（`~/pi-linux`，`main` 的 `eafc578`），不在 `/mnt/d`：那里是 9p，权限位、inode 和时间戳的行为都与真正的 Linux 文件系统不同。
- **内核没有 Landlock**（要 ≥ 5.13；LSM 列表是空的）。沙箱的结论因此只覆盖 bwrap 那一层，Landlock 那一层在这台机器上验证不了。`pi-sandbox` 的两个端到端文件（`deny_paths_e2e`、`read_write_trailing_glob_e2e`）在内核不支持时直接 `return`，**在这里的「通过」是空转**；设了 `SANDBOX_E2E_REQUIRE_ENFORCEMENT` 才会把跳过变成 panic。
- 内核是 `CONFIG_HZ=100`：粗粒度时间戳一个刻度 10 ms，这是发现 8 里 `media` 用例失败的原因。
- **WSL 的登录脚本导出了 `PI_HOME`，指向 Windows 一侧的真实配置目录。** 基线执行器（`unset_env`）与 pytest（根目录的 `conftest.py`）会清掉它，CI 与正常流程不受影响；直接跑测试二进制、或拿 `zypi` 做实验的人会读写那个目录。r8 自己踩到过一次，之后所有手工命令都先 `unset` 并换一次性的 `HOME`；`docs/baselines/tui.md` 加了一条提醒。

**验证。**

| 检查 | 结果 |
|---|---|
| `tui_baseline.py gates` | ✓ 依赖图 980（与 CI 一致） |
| debug `zypi` 构建 | ✓ 0 告警；冷构建 4 m 53 s（机器 12 核 / 24 GB，`CARGO_BUILD_JOBS=8`） |
| 8 个 suite，`doctor_cmd::` 不再被跳过（基线执行器，清环境；第一轮） | ✓ **7,456 通过 / 0 失败 / 20 忽略**：`pi-shell` 1,056、`pi-pager` 5,571、`pi-pager-bin` 20、`pi-acp-lib` 21、`pi-http` 13、`pi-telemetry` 239、`pi-file-utils` 216、`pi-sampling-types` 320。与 CI（`33374a3`，7,444）对账：多出的 12 个正是 `doctor_cmd::`，其余 7 个 suite 与 CI 逐项相同 |
| 10 个 suite（同上；做了六件事之后的清单，含 `pi-pager-render`、`pi-pager-minimal`） | ✓ 执行器退出码 0，1,469 s：**8,633 通过 / 1 个已知失败 / 22 忽略**。`pi-shell` 1,056、`pi-pager` 5,577（再 +6 个 `agent_stderr` 用例）、`pi-pager-render` 1,091（另有 1 个已知失败，见发现 8）、`pi-pager-minimal` 79、`pi-pager-bin` 21（+1 个 `-p` 用例）、`pi-acp-lib` 21、`pi-http` 13、`pi-telemetry` 239、`pi-file-utils` 216、`pi-sampling-types` 320 |
| `doctor_cmd::`（12 个，r5 起 CI 一直跳过） | ✓ 12 通过——**但只在装着 `pw-record` / `parec` / `arecord` 的 WSL2 上**；「不需要音频设备」不对：PR #9 的第一次 CI 运行里 runner 没有这些程序，`fake_standalone_facts_compose_through_shared_view` 失败（`voice.no-input-device` 多出一个 issue），已修，见本节末尾的「CI 结果」 |
| `pi-pager-render`、`pi-pager-minimal`（当时不在清单里，现在在）整个 crate | ✓ `pi-pager-render` 1,092 通过 / 0 失败 / 2 忽略；`pi-pager-minimal` 79 通过（其中一个用例缺锁、整个 crate 默认线程数下 30 次失败 25 次，已修，见发现 8） |
| 其余 crate 逐个 `--lib --tests`（含 `pi-fast-worktree`） | 74 个：68 个全过（其中 5 个没有测试），5 个有失败用例（共 13 个），1 个编译不过；11,807 通过 / 13 失败 / 23 忽略；见发现 8 |
| Python 全量 pytest（Linux，3.12.3，`main` 的 `eafc578`） | ✓ 2,135 通过 / 0 失败 / 30 跳过（缺 `langchain_deepseek`、Windows 专属）/ 31 取消选择（`real_llm`），123.9 s。r7 记的 6 个 macOS 专属失败在 Linux 上都通过 |
| Python 全量 pytest（六件事做完之后，含 `test_print_shutdown.py` 的 15 个与 `test_tui_pty.py` 的 10 个） | ✓ Linux：2,160 通过 / 0 失败 / 30 跳过 / 31 取消选择，97 s。Windows：2,156 通过 / 0 失败 / 34 跳过 / 31 取消选择，273 s（`-p` 的 15 个与依赖 `pty` 的 5 个跳过）。`ruff check .` 与 `ruff format --check .` 干净 |
| PTY 烟测（`PI_USE_MOCK=1`，`pyte`） | ✓ 第一轮 18/18：欢迎页、无 panic、恰好一个 agent 进程、一轮对话、状态栏的模型名、`/model` 选择器、切换后状态栏跟着变、切换后再来一轮、`/exit` 退出码 0 且无残留进程、一个会话文件且带 `model_change`、重启后 `/resume` 列出旧会话并回放历史、恢复后保留所选模型、第二次 `/exit` 干净。入库成 `scripts/tui_pty/smoke.py` 并加上 stderr 日志的检查（在 `PI_HOME/logs` 下、权限 0600、第二次启动追加到同一个文件、替身 agent 写的 `STDERR-MARK` 进了日志而没画在屏幕上）后：**23/23**；加 `--sandbox workspace`：**26/26**（多出的 3 项：欢迎页与状态栏上有 `(not enforced)`，终端里有那行警告） |
| 入库的 PTY 脚本（`scripts/tui_pty/`） | ✓ `exit_matrix.py` 12/12（无沙箱）、14/14（`--sandbox workspace`）；`print_exit.py` 4/4；`scripts/tests/test_tui_pty.py` 与 `packages/pi-agent-cli/tests/test_print_shutdown.py` 在 Linux 上 25/25 |
| 沙箱（5 个 profile）、`stderr`、`config.toml`、`zypi export` | 见发现 2–5 |
| 退出矩阵 | 见下 |

**退出矩阵（Linux，debug 构建的 zypi；agent 在跑 `exec sleep 300`，退出后最多等 12 s 轮询 `kill -0`，表里的秒数是各进程消失的时间）。** 两种启动方式：zypi 是会话首进程（PTY 的会话 leader），或只是交互 shell 里的一个前台作业（IDE 的终端、`tmux` 里的 shell 就是这样）；沙箱时 zypi 先 re-exec 成 `bwrap … -- zypi`，所以多一个 `kill` 的目标。

| 退出方式 | 无沙箱（两种启动方式都干净） | `--sandbox workspace`（两种启动方式都干净） | 沙箱、补丁前（shell 作业） |
|---|---|---|---|
| 双击 Ctrl+Q | zypi 退出码 0；agent 0.5 s、工具 0.4 s | 退出码 0；agent 0.5 s、工具 0.4 s | 干净 |
| `kill -9`（沙箱下是给外层 bwrap） | agent 0.2 s、工具 0.1 s | bwrap 与 zypi 0.1 s，agent 0.1–0.2 s、工具 0.1 s | **zypi、agent、工具全部残留** |
| `kill -TERM`（同上） | zypi 优雅退出（0）；agent 0.2 s、工具 0.1 s | bwrap 被信号杀死，zypi 随即被 SIGKILL；agent 0.1–0.2 s、工具 0.1 s | **全部残留** |
| `kill -HUP`（同上） | 同 `-TERM` | 同 `-TERM` | **全部残留** |
| 关闭 PTY master（关终端） | 退出码 0；agent 0.2 s、工具 0.0 s | agent 0.1–0.2 s、工具 0.0 s | 干净 |
| 单按 Ctrl+C | 取消当前 turn，工具 0.1 s 内回收；zypi 与 agent 继续运行 | 同左 | 干净 |
| 只杀沙箱里的 zypi | — | bwrap 随之退出（退出码 137）；agent 0.1–0.2 s、工具 0.1 s | 干净 |

「补丁前」一栏是修之前同一套用例的结果（shell 作业 × 沙箱，7 例里 4 例干净）。修后：无沙箱 12/12，`workspace` 14/14（上表）；沙箱的全部组合共 35/35（5 组 × 7 例，含上表的 14 例）。**一个代价**：给外层 bwrap 发 SIGTERM / SIGHUP 时，zypi 收到的是 SIGKILL，来不及恢复终端（备用屏幕、原始模式）；补丁前是整棵树继续在终端上跑，所以取这个。

**发现。**

1. **沙箱的 re-exec 缺 `--die-with-parent`（已修）。** 启用沙箱时 zypi 先 re-exec 成 `bwrap … -- zypi`。bwrap 默认只是等它的子进程，所以只杀 bwrap 的做法——`kill $!`、`timeout`、IDE 的停止按钮、只给自己启动的那个进程发信号的 supervisor——会让 zypi、agent 与工具继续在终端上跑。实测（zypi 是 shell 的一个作业，`--sandbox workspace`）：给 bwrap 发 SIGKILL、SIGTERM、SIGHUP，三例都留下整棵进程树（zypi、agent、`sleep 300`），其余四例（双击 Ctrl+Q、关终端、Ctrl+C、只杀内层的 zypi）干净；单独拿 bwrap 验证，结果一样：没有 `--die-with-parent` 时子进程在三种信号之后都活着，加上之后都死。zypi 是会话首进程时不受影响，因为内核会给整个前台进程组发 SIGHUP。修法：`pi-sandbox` 的 `bwrap_reexec_command_ex` 加这一个选项（bwrap 一死，子进程收到 SIGKILL；agent 随后在 stdin 上看到 EOF，走原有的优雅退出路径回收工具），并新增单测 `bwrap_reexec_dies_with_parent`（要求它出现在 `--` 之前，是 bwrap 的选项而不是被执行命令的参数；拿掉那一行，它按预期失败）。复测：5 组（shell 作业 × `workspace` / `read-only` / `strict`，会话首进程 × `workspace` / `read-only`）× 7 例，35/35 干净。这一条只在 Linux 上有：macOS 用 Seatbelt，没有 bwrap。
2. **TUI 模式下 agent 的 `stderr` 是 `/dev/null`。** `app::run` 一开头调 `pi_tty_utils::redirect_native_stderr()`，把 fd 2 指向 `/dev/null`，agent 继承的就是它。实测：`/proc/<pid>/fd/2` 对 zypi 和 agent 都是 `/dev/null`（agent 的 stdin / stdout 是 ACP 的两根管道）；`-p` 路径不做这个重定向，子进程的 stderr 与 stdin 都是终端。让 agent 往 `stderr` 持续打带标记的行，在启动时、一个回合进行中、agent 安静之后、以及两次改变窗口大小（整屏重绘）之后，屏幕上都是 0 行。所以 1.R3 ① 的前提错了——不是「写在 TUI 所在的终端上」，也就没有污染全屏界面的风险；代价是 agent 打到 `stderr` 的任何东西（Python 的 traceback、告警）都没地方看。`acp/spawn.rs` 里同一处注释也是错的，已改。**已改（用户选了落到文件）**：拉起 agent 时 `spawn.rs` 打开 `<home>/logs/agent.stderr.log`，把 agent 的 `stderr` 接到它——只追加，Unix 上权限 0600（traceback 里可能有提示词、路径和密钥），每次启动写一行 `--- agent started <时间> (zypi pid N) ---`，启动时发现它超过 1 MiB 就挪成 `agent.stderr.log.1`（覆盖旧的），所以最多留下两个文件；打不开日志就退回丢弃，不挡 agent 启动；运行中不限大小（那是 agent 自己的输出，除非它卡在打印的死循环里，否则很小）。屏幕上仍然什么都没有。实测：一个启动就往 `stderr` 打 `STDERR-MARK` 的替身 agent，日志里有这一行，屏幕上没有（`smoke.py` 的最后一步）；真 agent 在正常情况下不往 `stderr` 打东西，日志里只有那一行起始标记。`-p` 不受影响，仍然是终端。
3. **`config.toml` 的解析严格性。** Rust 的配置文件是 `$PI_HOME/config.toml`，Python 的是 `agent.toml`。把 Python 一侧的写法放进 `config.toml`，试了 9 种内容：

   | `config.toml` 的内容 | 结果 |
   |---|---|
   | 没有这个文件；未知小节 `[zzz]`；未知顶层键 | 正常启动（未知的忽略） |
   | Python 的 `[model]` 表（`provider`、`id`） | 正常启动 |
   | `[agent] command = "…"`（合法）；`command = 5`（类型错） | 正常启动（类型错也不报错） |
   | Python 风格的顶层 `permission = "ask"` | **起不来**，退出码 1：`Failed to create agent config: invalid type: string "ask", expected struct PermissionKnownKeys`（在 `permission` 处） |
   | Python 的 `[[models]]` 数组 | **起不来**，退出码 1：`invalid type: map, expected a string`（在 `models` 处） |
   | TOML 不合法 | **起不来**，退出码 1：`Failed to load config: TOML parse error at line 1, column 8…`（同一条错误打印了两遍） |

   Rust 对它认识的键类型检查是严格的，对不认识的键是宽容的；两边的配置不要混在 `config.toml` 里。
4. **`zypi export` 读不到 Python 写的会话（ADR1，没做；第一轮之后用户选了不动）。** 四种写法——会话 id、文件名去掉扩展名，各带或不带输出文件——都是 `Error: Session '…' not found.`，退出码 1，stdout 为空。`export` 是又一个读磁盘上旧格式会话的地方，0.7 的读取点清单里要有它。
5. **内核没有 Landlock 时，沙箱静默降级。** `--sandbox workspace` / `read-only` / `strict` 照常启动，原先启动前、退出后、屏幕上都没有任何提示（现在见本条末尾的「已改」）；唯一的记录是 `$PI_HOME/sandbox-events.jsonl` 里的一行 `{"event_type":"ApplyFailed","profile":"workspace","platform":"linux/landlock","enforced":false,"error":"Landlock not available. …"}`。这与内嵌文档 `18-sandbox.md` 写的一致（「记一条警告，不带强制继续」），只是这条「警告」实际落在一个没人会看的文件里。bwrap 那一层照常生效，并被 agent 与工具继承（zypi 与 agent 在同一个新的 mount namespace 里，`NoNewPrivs=1`、`Seccomp=2`）；它做的是丢能力、关特权、拦嵌套 namespace、把 hooks 目录只读绑定，**不限制写入范围**（`--bind / /`）——那是 Landlock 的事。agent 的工具进程里实测：

   | profile | 进程树 | 工作区可写 | 工作区外可写 | hooks 目录可写 | 嵌套 user / mount ns | `NoNewPrivs` / `Seccomp` |
   |---|---|---|---|---|---|---|
   | 无、`off` | zypi → agent | 是 | 是 | 是 | 可以 | 0 / 0 |
   | `workspace` | bwrap → zypi → agent | 是 | **是** | 否（只读） | 否（EPERM） | 1 / 2 |
   | `read-only` | 同上 | **是** | **是** | 否 | 否 | 1 / 2 |
   | `strict` | 同上 | **是** | **是** | 否 | 否 | 1 / 2 |
   | `devbox` | 同上 | 是 | 是 | 是 | 可以 | 1 / 0 |

   所以在这样的内核上，`read-only` 与 `strict` 并不限制写，原先却没有任何提示。Landlock 那一层要在内核 ≥ 5.13 的机器上，用 `SANDBOX_E2E_REQUIRE_ENFORCEMENT=1` 跑 `pi-sandbox` 的端到端用例才算验证过。

   **已改（用户选了在屏幕上说）**：`SandboxManager::apply` 在这两种情况（平台不支持、`Sandbox::apply` 出错）都继续运行并返回 `Ok`，所以调用方原本无从知道。现在 `SandboxManager` 记下「没生效」和原因（`not_enforced()`，`install()` 之后是 `pi_sandbox::not_enforced()`），三处用它：① 启动时 `apply_sandbox`（`pi-shell`）往 `stderr` 打一行警告——`warning: the 'workspace' sandbox could not be put in force (Landlock not available. Requires Linux kernel 5.13+ with Landlock enabled). Its file and network limits do not apply: the agent can read, write and reach whatever it could without a sandbox.`；② 欢迎页顶栏、③ 会话状态栏，在已生效的 `sandbox:workspace` 标签所在的位置画 `sandbox:workspace (not enforced)`（`accent_error` 红色）——原先没生效时那个位置什么都没有，读起来像「没要求沙箱」。警告在备用屏幕之前，进入 TUI 后看不到，所以屏幕上的两处才是要紧的。`sandbox-events.jsonl` 里的 `ApplyFailed` 照旧。实测（WSL2，无 Landlock）：`smoke.py --sandbox workspace` 26/26，含欢迎页与状态栏上有这个标签、终端里有这行警告；内核支持 Landlock 时不出现（`not_enforced` 为空，靠单测，没在真机上看过）。
6. **`-p` 模式下只杀 zypi，agent 子进程会留下（已改）。** `run_python_print` 用 `.status()` 等子进程，Python 的 `-p` 路径没有信号处理。把 agent 换成 `bash -c 'sleep 300'` 的替身，只给 zypi 发信号：SIGTERM、SIGKILL、SIGHUP、SIGINT 四例，zypi 都退了，`sleep 300` 都还在（给整个进程组发信号、关终端没有测）。它属于 1.R3 的范围。

   **已改（用户选了 Python 一侧监视父进程）**。没用 `PR_SET_PDEATHSIG`：它只在 Linux 上有（macOS 没有），管的是创建子进程的那个*线程*；信号选 SIGKILL，agent 来不及回收它的工具（bash 的进程组成了孤儿），选 SIGTERM 又得 Python 一侧自己处理——所以逻辑本来就要放在 Python 里。做法：zypi 起 agent 时设 `PI_AGENT_PARENT_PID=<自己的 pid>`（`print_mode::agent_command`，`pi-pager-bin`）；Python 的 `main()` 一进来就把这个变量从环境里取走——之后起的工具与嵌套的 `-p` 都继承不到——并且只在它等于真正的父进程、或指向一个已经不存在的进程（agent 还在启动时父进程就死了，立刻停）时才启用；指向一个活着但不是父进程的 pid（中间隔了包装脚本）就不启用。启用后每 0.5 s 查一次 `os.getppid()`，变了就走 `serve()` 同一条停止路径：取消主任务，asyncio 取消回合，bash 工具回收各自的进程组。`-p` 同时装上 SIGTERM / SIGHUP 处理器（与 `serve()` 共用 `_StopRequest`），退出码 `128 + 信号号`（父进程死掉引起的停止是 143）。只在 POSIX 上生效：Windows 上父进程死后 `getppid()` 仍报它的 pid。用环境变量而不用命令行旗标，是为了新 zypi 配旧 agent、旧 zypi 配新 agent 都不出错。复测：`scripts/tui_pty/print_exit.py`（真 zypi + 真 `main()`，LLM 换成一轮脚本化的长 bash 调用）只给 zypi 发 SIGTERM / SIGKILL / SIGHUP / SIGINT，四例 zypi 0.1 s、工具 0.4 s、agent 0.5 s 内都消失，agent 拿到的 `PI_AGENT_PARENT_PID` 等于 zypi 的 pid，工具看不到它；Python 一侧 `test_print_shutdown.py` 15 个用例（停止信号、启动者被 `kill -9`、无关的活进程、已经不在的父进程、变量取值）。把监视关掉再跑（变异检查），「启动者被杀」与「启动者已不在」两例按预期失败。
7. **退出耗时离 3 s 的宽限还有余量。** 双击 Ctrl+Q 时 agent 退出最慢，空闲 0.5 s；12 个忙循环占满 CPU 时 0.7–0.8 s；cargo 在后台编译（`CARGO_BUILD_JOBS=12`）时 0.8 s；cargo 加每核两个忙循环（负载升到 24，核数的 2 倍）时无沙箱 1.0 s、沙箱 1.3 s。这三种负载下的矩阵（每种 26 例，共 78 例）全部干净。更早有一次在机器被构建与大量测试同时压着时量到过 3.5–3.9 s，没能复现，当时的清理结果也没有单独记录；超过 `AGENT_EOF_GRACE`（3 s），zypi 会 `start_kill()` agent，工具进程就可能留下（r5 之前的情形），所以只当作提醒。
8. **Rust 测试在 Linux 上的分诊。** 8 个 suite 之外的 74 个 crate 里，5 个有失败用例，1 个编译不过，都与 runtime 拆除无关（`pi-pager-minimal` 在那一轮碰巧全过，它的问题见表后）：

   | crate | 失败用例 | 判断 |
   |---|---|---|
   | `pi-pager-render` | `terminal::tmux_probe::tests::successful_near_deadline_exit_still_returns_captured_output` | 偶发：靠近 deadline 的 tmux 探测（1.5 s 的预算里脚本烧 1.2 s，只留 0.3 s 给进程启动），只在分诊那一遍失败；之后空闲与满载各重复 30 次，0 次复现，整个 crate 在最终复测里 1,092 全过。清单的第一次完整运行里它又失败了一次（那次整个 crate 用了 8.6 s，平时 1.3 s），同样是刚链接完的第一次运行；之后又重复 34 次（空闲 17、12 个忙循环占满 CPU 17）都没复现。猜是刚链接完磁盘写回拖慢了进程启动，没有证实；已放进清单的 `known_failures` |
   | `pi-workspace` | `hub_auth::proactive::tests::disabled_flag_does_not_refresh_or_spawn` | 偶发，同上（0/60） |
   | `pi-workspace` | `session::git::*` 共 7 项：`normalize_*`（5 个）、`resolve_normalized_remote_urls_deduplicates_across_transports`、`restore_code_tests::ensure_binding_forks_conv_branch_off_base_and_is_idempotent` | `docs/baselines/tui.md` 已记的那 7 项（`xai-org` → `pi-org` 改名遗留与 OID 断言），macOS 上也失败 |
   | `pi-agent` | `prompt::template::tests::test_encrypted_templates_not_stale` | 加密模板字节过期（已记） |
   | `pi-shell-base` | `util::tests::is_grok_process_self_true_impossible_pid_false`、`…is_grok_process_strict_self_true_impossible_pid_false` | 按进程名 `grok` 判断（已记）；这个函数没有调用者 |
   | `pi-hooks` | `runner::command::tests::test_hook_child_cannot_open_dev_tty` | 用例没有控制终端时自己跳过（CI 就是这样），有终端时跑 `sh -c 'exec 3>/dev/tty 2>/dev/null && exit 1 \|\| exit 0'`。`/bin/sh` 是 dash 时，`exec` 的重定向失败会让 shell 直接以 2 退出，走不到 `\|\| exit 0`：是用例对 `sh` 的假设，不是 hook 的行为（子进程确实打不开 `/dev/tty`） |
   | `pi-fast-worktree` | 编译不过：`failed to resolve: could not find 'tests' in 'confined'` | 与 macOS 相同（已记） |

   8 个 suite 里那 1 个失败是 `pi-pager` 的 `app::agent_view::paste::paste_key_tests::tool_media_same_length_same_mtime_rewrite_retries_failed_load`，在干净环境里 30 次失败 26 次（空闲）/ 27 次（满载）。`MediaFileStamp` 的 `ctime` 取自内核的粗粒度时钟，`CONFIG_HZ=100` 时一个刻度是 10 ms，两次写入落在同一刻度就得到相同的 `ctime`，缓存认为文件没变；CI 与 macOS 上没出现过。真实的「慢写」不会那么快，所以这是用例的时间假设，不是产品缺陷。修法：用例在重写前等 25 ms，并把注释里的「一次重写总会推进 `ctime`」改成「推进到内核时钟的刻度」。修后在干净环境里空闲 40/40、满载（12 个 CPU 全占）40/40，完整的 `pi-pager` suite 里也通过。

   `pi-pager-minimal`（不在清单里）的 `commit::tests::committed_edit_keeps_diff_line_backgrounds`：整个 crate 用默认线程数跑，30 次失败 25 次（12 核空闲；CPU 全占时 2/30）；`--test-threads=1` 或单独跑这个用例，30/30 通过。`terminal_native_lock_paints_only_native_colors` 与 `committed_thinking_paints_a_dim_rail_in_column_zero` 在持有 `theme_cache::test_lock()` 期间把进程级的 `set_terminal_native_lock(true)` 打开，而这个用例没拿那把锁，撞上这个窗口就拿到原生配色、画不出 diff 背景。是用例缺锁，不是产品缺陷（§10.8 里 macOS 本机 79 个全过，应是没撞上这个窗口）。修法：补上同样的 `test_lock()` 守卫。修后整个 crate 默认线程数 40/40、单线程 40/40、单独 40/40、CPU 全占 40/40。这一条决定了 `pi-pager-minimal` 能不能放进清单：不修就是个高概率的抖动 suite。

**仓库里的改动。**

| 文件 | 改动 |
|---|---|
| `tui/crates/codegen/pi-sandbox/src/lib.rs` | 发现 1：`bwrap_reexec_command_ex` 加 `--die-with-parent`，单测 `bwrap_reexec_dies_with_parent`。发现 5：全局状态与 `SandboxManager` 记下「没生效」及原因，`not_enforced()`、`not_enforced_warning()`、`not_enforced_label()`，加单测 |
| `tui/crates/codegen/pi-shell/src/config/mod.rs` | 发现 5：`apply_sandbox` 在沙箱没生效时往 `stderr` 打一条警告 |
| `tui/crates/codegen/pi-pager/src/app/agent_view/render.rs`、`views/welcome/top_bar.rs` | 发现 5：状态栏与欢迎页顶栏在没生效时画 `sandbox:<profile> (not enforced)`（红色） |
| `tui/crates/codegen/pi-pager/src/acp/spawn.rs` | 发现 2：agent 的 `stderr` 落到 `<home>/logs/agent.stderr.log`（追加、0600、启动标记、超过 1 MiB 在下次启动时轮转），6 个单测；改了错的注释 |
| `tui/crates/codegen/pi-pager-bin/src/print_mode.rs`、`main.rs` | 发现 6：`-p` 起 agent 时设 `PI_AGENT_PARENT_PID`（`agent_command`），1 个单测 |
| `tui/crates/codegen/pi-pager/src/app/agent_view/paste.rs` | 发现 8 的 `media` 用例：重写前等 25 ms，并改注释（只动测试） |
| `tui/crates/codegen/pi-pager-minimal/src/commit_tests.rs` | 发现 8：`committed_edit_keeps_diff_line_backgrounds` 补 `test_lock()` 守卫（只动测试） |
| `packages/pi-agent-cli/pi_agent_cli/__main__.py` | 发现 6：`-p` 取走 `PI_AGENT_PARENT_PID`、每 0.5 s 查父进程、处理 SIGTERM / SIGHUP；`serve()` 与 `-p` 共用 `_StopRequest`（只在 POSIX 上起作用） |
| `packages/pi-agent-cli/tests/test_print_shutdown.py`、`_print_agent.py`（新） | 发现 6 的 15 个用例（POSIX 才跑；Windows 上整个文件跳过） |
| `scripts/tui_baseline.toml` | 去掉 `doctor_cmd::` 的 `skip`；加 `pi-pager-render`（带一个 `known_failures`）与 `pi-pager-minimal` 两个 suite；`pi-pager` 与 `pi-pager-bin` 的 `reference_passed` 随之更新。现在是 10 个 suite |
| `scripts/tui_pty/`（新：`smoke.py`、`exit_matrix.py`、`print_exit.py`、驱动 `pty_term.py`、`zypi_env.py`、README）、`scripts/tests/test_tui_pty.py`（新） | 本节用到的 PTY 驱动入库；每次都用一次性的 `PI_HOME` / `HOME` / 工作目录，环境从头构建。辅助逻辑有单测（Linux 10 个；Windows 上依赖 `pty` 的 5 个跳过） |
| `AGENTS.md`、`packages/pi-agent-cli/AGENTS.md`、`CHANGELOG.md`、`docs/TUI-AND-CODE-AGENT.md` | 新行为的说明（stderr 日志、`PI_AGENT_PARENT_PID`、没生效的提示）、基线执行器与 `PI_HOME` 的提醒、PTY 脚本的位置 |
| 本文件、`docs/baselines/tui.md` | r8 的记录；基线页加了 `doctor_cmd::` 的 Linux 结果、新 suite、未选入 suite 的 crate 的分诊、PTY 载体（§3.3）、WSL 的 `PI_HOME` 提醒 |

「仓库里的改动」里的 Rust 与 Python 改动都在 `main` 的 `eafc578` 之上，拆成 8 个提交（分支 `codex/linux-wsl-r8`）。`docs/baselines/tui.md` 里的数是 WSL2 量的，不是 CI 的；清单里 `doctor_cmd::`、`pi-pager-render`、`pi-pager-minimal` 的结果要等第一次 CI 运行证实——**现在有了，见下一段「CI 结果」**。

**CI 结果（PR [#9](https://github.com/zy1233/pi-python/pull/9) 的第一次运行，`cargo test (Linux x86_64)`，24 m 44 s）。** 10 个 suite：`pi-shell` 1,056、`pi-pager` 5,576（另有 1 个失败、13 个忽略）、`pi-pager-render` 1,092（2 个忽略；`known_failures` 里的 tmux 用例在 runner 上没有失败）、`pi-pager-minimal` 79、`pi-pager-bin` 21、`pi-acp-lib` 21、`pi-http` 13、`pi-telemetry` 239、`pi-file-utils` 216（6 个忽略）、`pi-sampling-types` 320，合计 8,633 通过 / 1 失败 / 22 忽略。除了那 1 个失败，每个 suite 都等于 WSL2 的数（`reference_passed` 不用改）；门禁与 `cargo check`、Ruff、三个版本的 pytest 都通过。**失败的那个**是 `doctor_cmd::tests::fake_standalone_facts_compose_through_shared_view`：它断言 `issue_count() == 1`，runner 上是 2。原因：`collect_report_with` 每次都调 `apply_voice_probe`；`pi_voice::AUDIO_SUPPORTED` 在 Linux 上表示「编进了子进程录音后端」（默认开），该后端在 `PATH` 里找 `pw-record`、`parec`、`arecord`，找不到就多出一条 `voice.no-input-device` 的 issue。WSL2（WSLg）三个都装了，所以 r8 里的 12 个全过——r8 写的「不需要音频设备」是错的，它只对有录音程序的机器成立；`docs/baselines/tui.md` 里「r5 删掉了 `apply_voice_probe`」也不对（函数还在，`doctor_cmd` 每次都调）。复现：把测试二进制用 `PATH=/nonexistent` 直接运行，修前 11 通过 / 1 失败，报同样的 `left: 2, right: 1`。修法：这个用例把 `voice.no-input-device` 排除出计数（它测的是自己合成的那些事实，与宿主机有没有麦克风无关）；修后 `PATH=/nonexistent` 与正常 `PATH` 下都是 12/12。其余 11 个用例不受影响（它们不数 issue，只查某条 finding 不存在）。`skip` 不用放回清单。

**没有验证的。** Landlock 那一层（内核 ≥ 5.13；见发现 5）；GitHub 的 `ubuntu-24.04` runner 默认限制非特权 user namespace，bwrap 要先放开 `kernel.apparmor_restrict_unprivileged_userns`（凭文档，没试）；bwrap 缺失或被禁用时的行为；Windows；MCP 子进程；用真实模型跑 PTY（只用了 mock）；`/bin/sh` 不是 dash 的发行版、musl；`-p` 模式下给整个进程组发信号；`PI_AGENT_COMMAND` 指向包装脚本时的 `-p`（agent 的父进程不是 zypi，监视不启用，只杀 zypi 仍会留下 agent）。「内核支持 Landlock 时不出现没生效的提示」只有单测，没在真机上看过。Windows 上的 `PI_AGENT_PARENT_PID`（监视只在 POSIX 上启用）与 `agent.stderr.log` 的轮转（`rename` 的语义不同）都没跑过。CI 里去掉 `skip` 的 `doctor_cmd::` 与两个新 suite 在 GitHub runner 上的结果——已有，见「CI 结果」（两个新 suite 与 WSL2 的数一致；`doctor_cmd::` 一个用例失败，已修）。macOS 一侧没有重测：r8 的 Rust 改动都是平台无关或 Unix 通用的代码，只在 Linux 上编译、测过，Python 的父进程监视在 macOS 上应当同样可用（`os.getppid()`），也没跑过；`--die-with-parent` 只在 Linux 上存在。

**留给用户决定的。**

1. `zypi export` 要不要读 Python 的会话（ADR1，发现 4）。第一轮之后用户选了不动它，仍是 `Session '…' not found.`。
2. CI：`pi-sandbox` 的端到端用例要在带 Landlock 的 runner（内核 ≥ 5.13）上并设 `SANDBOX_E2E_REQUIRE_ENFORCEMENT=1` 才有意义——这要换 runner 或加一个 job。去掉 `skip` 的 `doctor_cmd::` 与两个新 suite：第一次 CI 运行已经看过（见「CI 结果」），不需要把 `skip` 放回去。
3. 发现 1 的代价要不要接受：给外层 bwrap 发 SIGTERM / SIGHUP 时，zypi 收到的是 SIGKILL，来不及恢复终端；补丁前则是整棵树留在终端上继续跑。
4. 提交与推送：已拆成 8 个提交，推到分支 `codex/linux-wsl-r8` 并开了 PR。`main` 上的 `eafc578`（phase7 的修复）原本也没推，PR 会一并带上；`--die-with-parent` 单独是一个提交（发现 1），不想要这个代价时可以单独 revert，但 CHANGELOG 与本节里对它的描述要一起改。
5. WSL 的清理：r8 在最后一步清掉了自己留下的东西——`~/pi-linux`（克隆加构建产物，34 GB）、`~/zypi-bin`、`~/pi-linux-logs`、`~/pi-test-home`，以及 `/tmp` 下这次任务期间创建的 182 个条目（测试留下的临时目录与输出、下载的压缩包、临时脚本）和 pytest 的基础目录。**留着没删**的：`~/.local/protoc` 与 `~/.local/bin/{protoc,rg}`（为构建装的，要删就删这三个）、`~/.cargo/registry` 里新下载的约 27 MB crate 与 `~/.cache/uv` 里的几个条目（共享缓存，删了只会让下次构建重新下载）；用户自己的 `CARGO_TARGET_DIR`（`/mnt/d/work/cargo-target`）没有碰过。`~/.profile` / `~/.bashrc` 里对 `PI_HOME` 的导出是用户自己的配置，没有动——建议删掉，免得再有人直接跑 `zypi` 时读写 Windows 一侧的配置目录。

### 10.11 r9 追加：阶段 0 收口（0.3 / 0.7 / 0.8）与阶段 1 里不需要拍板的部分

**起点与范围。** PR [#9](https://github.com/zy1233/pi-python/pull/9) 之后，用户问：接下来处理什么，阶段 1 何时开始，有没有必须先完成的阶段 0 项。我的回答是：阶段 1 没有被技术挡住；挡住它的是几个只有用户能定的行为——模式切换怎么映射、`zypi export` 的去向、`-p` 与交互模式的旗标契约、恢复会话时要不要沿用保存的沙箱 profile；阶段 0 还欠 0.3 的 Rust 一侧、0.7、0.8。用户选了四件：`e2e`（0.3 的 Rust 一侧）、`p0_gate`（0.7 与 0.8 收口）、`free_p1`（阶段 1 里不需要拍板的部分）、`ci_pr`（盯 PR #9 的 CI 并报告）；`decide` 那一项**没有选**，也就是没有授权我替他定会改变行为的决定。所以这些决定本节一律只写成**提议**，集中在末尾的「留给用户决定的」，没有动手。做了的是 0.3、0.7 的测量与清单、0.8 的定稿，以及 1.P1、1.P4、1.P7 的余项。本分支的 16 个提交（末尾有表，最后一个是本节的文档）叠在 PR #9 的 `d84d98a` 之上，分支 `codex/phase1-contract-docs`，**只在本地，没有推送**。验证环境：WSL2（同 §10.10）与 Windows 10（Python 3.12.7）。

**CI 结果（PR #9 的第二次运行，`d84d98a`）。** [run 38052332014](https://github.com/zy1233/pi-python/actions/runs/38052332014) 全绿：`cargo test (Linux x86_64)` 32 m 40 s（第一次 24 m 44 s，差了 8 分钟，原因没有查，缓存命中的情况与 runner 的波动都可能；job 上限 150 分钟），`Gates + cargo check` 8 m 21 s，其余 job 也通过。10 个 suite 合计 **8,634 通过 / 0 失败 / 22 忽略**，每个都等于清单里的 `reference_passed`：`pi-shell` 1,056、`pi-pager` 5,577（13 忽略）、`pi-pager-render` 1,092（2 忽略）、`pi-pager-minimal` 79、`pi-pager-bin` 21、`pi-acp-lib` 21、`pi-http` 13、`pi-telemetry` 239、`pi-file-utils` 216（6 忽略）、`pi-sampling-types` 320。r8 留下的几个问题因此有了答案：① 去掉 `skip` 的 `doctor_cmd::` 在 runner 上通过，包括 `d84d98a` 修掉的那个；② 两个新 suite 的数与 WSL2 逐项相同；③ `pi-pager-render` 的 `known_failures`（tmux 探测）在 runner 上没有失败，合计因此比 WSL2 的 8,633 多 1；④ 清单的数字不用改。

**0.3：pager 的 ACP client 对着真的 Python agent（Rust 一侧，已做）。**

- **是什么。** `tui/crates/codegen/pi-pager/tests/python_agent_e2e.rs`，10 个用例，用 pager 自己的 `pi_pager::acp::connect`——`zypi` 启动时调的同一个函数，含 stdio 桥与 agent 进程的管理——对着真的 `python -m pi_agent_cli`（`PI_USE_MOCK=1`）说话，不经过 PTY。要工具调用的用例换成 `packages/pi-agent-cli/tests/_tool_agent.py`（第一回合按脚本调一次 `write` 或 `bash` 的替身，r5 随 PR #7 引入）。每个用例用一次性的 `PI_HOME`，起的进程都带同一个标记（`PI_E2E_MARK`），结束时扫 `/proc` 确认没有带标记的进程留下。
- **怎么跑。** `PI_E2E_PYTHON` 指向装了 `pi_agent_cli` 的 Python。没设时用例打印一行说明并通过——没有这样的 Python 的开发者照样能跑清单；`PI_E2E_REQUIRED=1` 把「没有」变成失败。`tui-ci.yml` 的 `cargo test` job 在跑 suite 之前 `pip install` 六个包并设这两个变量（步骤名 `Install the Python agent for the end-to-end tests`），CI 里丢了 Python 不会假绿。workflow 的路径过滤加了 `packages/pi-agent-cli/pi_agent_cli/**` 与 `packages/pi-agent-cli/tests/_tool_agent.py`：改 agent 现在也会触发 TUI CI（约多 30 分钟 runner 时间）；harness 与 core 的改动不触发，由 Python CI 与 stdio 契约套件兜底。这是取舍，不是白送的。
- **用例。**

| 用例 | 证明什么 |
|---|---|
| `connect_runs_the_handshake_and_stop_leaves_nothing_behind` | `acp::connect` 完成握手；`stop` 之后没有带标记的进程 |
| `a_prompt_streams_the_reply_and_ends_the_turn` | 一个提示得到流式回复，回合正常结束 |
| `a_session_is_listed_with_a_title_and_replayed_by_a_later_agent` | 会话出现在 `session/list` 里且带标题；另起一个 agent 后 `session/load` 回放历史 |
| `session_list_pages_are_followed_by_cursor` | 51 个会话分两页，pager 跟着 `nextCursor` 取全；非法游标得到 `InvalidParams` |
| `a_permission_question_reaches_the_client_and_its_answer_decides` | 权限问题到达 client，答复决定工具执行与否 |
| `cancel_ends_a_running_tool_as_cancelled_and_reaps_it`（仅 Linux） | 取消让运行中的工具以 cancelled 结束，工具进程被回收 |
| `a_missing_agent_is_an_error_that_says_what_to_do` | 找不到 agent 程序：错误里有程序名，并说明 `PI_AGENT_COMMAND` 怎么设 |
| `an_agent_that_exits_at_once_is_an_error_with_what_it_wrote`（Unix） | agent 一启动就退出：错误里有退出状态（`exit status: 3`）与它写在 stderr 的字，`agent.stderr.log` 里也有 |
| `a_python_without_the_agent_says_so` | `python` 没装 `pi_agent_cli`：错误说明这一点 |
| `an_agent_that_dies_mid_turn_ends_the_requests_and_fires_cancel`（仅 Linux） | agent 在回合中途被杀：在途请求以失败结束，连接的 `cancel` 触发，消息里有 `exited on its own` 与 `signal: 9` |

- **它第一次跑就抓到两个产品 bug，都修了（`affda9e`）。** ① agent 起不来时，用户看到的是 `channel closed … recv_failed`；真正的原因（程序不存在、`PI_AGENT_COMMAND` 该怎么设）在桥线程的错误里被丢掉了。② agent 一启动就退出（比如 Python 没装 `pi_agent_cli`）时 `connect()` 一直等：没有人等子进程，`initialize` 既没有回答也没有错误，30 s 之后还挂着。修法：桥在等取消的同时等子进程（`select! { biased; cancel, child.wait() }`），退出后先排空它已经写出的输出（`EXIT_OUTPUT_DRAIN` 2 s、再等 100 ms），拼一条消息（程序、退出状态、`agent.stderr.log` 末尾 12 行 / 4 KiB），让在途请求以失败结束，触发连接的 `cancel`，把消息作为桥的结果；`connect()` 在 `initialize` 失败时返回这条消息（`explain_failed_start`）；TUI 已经起来之后 agent 自己死掉，`AgentProcessGuard` 在还原后的终端上打印 `Error: …`。`zypi` 自己的退出状态没变。8 个单测在 `acp::spawn::agent_exit_tests`。**变异检查**：把桥改回只等取消，依赖这条修复的三个用例里 `an_agent_that_exits_at_once…`（挂住）与 `an_agent_that_dies_mid_turn…`（取消不触发）按预期失败，`a_missing_agent…` 仍通过（它走的是 spawn 失败的路径，由 `explain_failed_start` 覆盖，不依赖桥等子进程）。
- **没覆盖的。** 真 LLM（只用 mock）；pager 的事件循环与界面（e2e 到 `acp::connect` 为止，界面归 PTY 脚本）；Windows 与 macOS（macOS 上应当会跑其中 8 个，另两个读 `/proc`，都没跑过）；CI 里那一步安装——还没在 GitHub 上跑过（没有推送）。

**0.7：磁盘读取点、冷启动延迟与 ADR1 的落地设计。**

*(1) 延迟——ADR1 的覆盖条件触发了吗？* ADR1 把会话来源改成纯 ACP：列表不再是本地读盘，agent 要先起来，再每个会话开一个文件。覆盖条件是「冷启动列表延迟不可接受」（§6），所以先量，脚本是 `scripts/session_list_bench.py`：合成的 `PI_HOME`，真 agent（`python -m pi_agent_cli`，mock LLM）走 stdio；每三个会话里一个属于被查的目录，其余分散在 20 个别的目录；每项取 3 次的中位数；OS 文件缓存是热的（冷盘没模拟）。`list` 是该目录的第一页，`pages` 是按 `nextCursor` 翻完，`load` 是 `session/load` 该目录最新的会话（120 条记录）。改前是 `d84d98a` 的 agent，改后是本分支（`54a38fe` 只读文件头、`1b9fb47` 分页与按 id 查、`c1f02c8` Python 分页）。

| 平台（会话 × 条目） | `list` 改前 → 改后 | `load` 改前 → 改后 | 逐页翻完（改后） |
|---|---|---|---|
| Windows 10 / NTFS，100 × 120 | 203 → 45 ms | 246 → 68 ms | 45 ms（1 页） |
| Windows 10 / NTFS，1,000 × 120 | 2,026 → 113 ms | 2,041 → 117 ms | 806 ms（7 页） |
| WSL2 / ext4，100 × 120 | 55 → 27 ms | 89 → 43 ms | 26 ms（1 页） |
| WSL2 / ext4，1,000 × 120 | 570 → 81 ms | 626 → 84 ms | 607 ms（7 页） |

agent 自己的启动（spawn 到 `initialize` 有回答）与会话数无关：WSL2 约 0.9 s，Windows 约 1.6–1.9 s（Python 的导入；今天的 TUI 在第一个提示之前本来就要付）。改前的代价随会话数线性增长，因为每次 `list` 读每个文件的全文，`session/load` 还要先列一遍目录找 id；现在首页只读文件头，按 id 查只读一个文件头。每个会话约 1 MB（2,000 条）、100 个会话时：Windows `list` 667 → 46 ms、`load` 752 → 240 ms；WSL2 172 → 28 ms、372 → 217 ms。**结论：覆盖条件没有触发。** 最坏的一项——1,000 个会话、`--resume <标题>` 要翻完 7 页——在 Windows 上多花 0.8 s，加在已经要 1.6 s 的 agent 启动上；`--continue` 只要首页（0.1 s）。没量的：冷磁盘、网络文件系统、超过 1,000 个会话。

*(2) 读盘调用点。* 范围：`pi-pager` 与 `pi-pager-bin` 的生产代码里读「会话文件」的地方，按 `persistence::`、`session::storage`、`grok_home().join("sessions")` 与 `resolve_local_session*` 检索（`pi-pager-bin`、`pi-pager-minimal`、`pi-pager-render`、`pi-acp-lib` 没有；`recent_dirs.rs` 只剩一行文档注释）。**13 处，8 个文件。** r2 估的「约 19 个文件」把 `pi-shell` 里的实现层（`session/persistence.rs`、`session/storage/**`）也算进去了；这里只数 pager 一侧的调用点，实现层随 ADR1 整块删。`session_notification.rs` 与 `views/session_title.rs` 只用 `persistence` 里的纯函数（清洗标题），不读盘，不在表里。

| # | 位置 | 读什么 | 为谁 | 对 Python 会话 | ADR1 之后 |
|---|---|---|---|---|---|
| 1 | `session_startup.rs` `most_recent_session_id` → `list_summaries(Some(cwd))` | 旧布局 `sessions/<编码后的 cwd>/<id>/summary.json` | `--continue` | 读不到，落到 #2 | `session/list{cwd}` |
| 2 | `session_startup.rs` `find_most_recent_jsonl_session` | 顶层 `sessions/*.jsonl` 的首行，按文件名倒序，直到 `cwd` 相同 | `--continue` 的退路——**唯一碰巧读得懂 Python 会话的一处** | 能用，但没有标题 | 删 |
| 3 | `session_startup.rs` `resolve_existing_session` → `resolve_local_session`、`resolve_local_session_any_cwd` | 旧布局 | `--resume <id>` | 命中不了，走「UUID 直接交给 agent」那一支，`original_cwd` 为 `None` | 在 `session/list`（不带 `cwd`）里找 id，取它的 `cwd` |
| 4 | `session_startup.rs` `resolve_session_by_title` → `list_summaries(Some(cwd))` | 旧布局 | `--resume <标题>` | 读不到：`no session id or title matched`（GAP） | `session/list{cwd}` 翻页，`select_by_title` 匹配 |
| 5 | `session_startup.rs` `ensure_session_id_available` → `session_exists_for_cwd` | 旧布局 | `--session-id` 查重 | 看不见 Python 会话（GAP） | 交给 agent 拒绝重复 |
| 6 | `session_startup.rs` `parent_session_is_worktree` | 旧布局的 `summary.json`，再退到 git 检查 | `--worktree` 与被恢复的父会话 | 文件不存在，只剩 git 检查 | 删读文件的那一段 |
| 7 | `session_title_resolve.rs` `presandbox_resume_target`（由 `cli.rs` 的 `pin_local_resume_target` 调用） | 旧布局（`resolve_local_session*`、`local_summaries_for_cwd_sync`） | 沙箱施加前把 `--resume` 的目标钉成 id | 恒为 `Unresolved` | 删（见下「沙箱顺序」） |
| 8 | `cli.rs` `saved_resume_profile_for_cwd` → `resumed_session_sandbox_profile` | 旧会话保存的沙箱 profile | 恢复时沿用沙箱 | 恒为 `None` | 删 |
| 9 | `app/mod.rs`（`materialize_startup` 之后）`list_summaries(None)` | 全部旧布局 summary | 给被恢复的会话补标题 | 读不到，没有标题 | 用 `session/list` 带回的标题 |
| 10 | `dispatch/session/load.rs`（选择器 / `/resume` 加载） | `resolve_local_session*` | 本地命中就带上 `original_cwd` | 命中不了，走 ACP 那一支，用选择器那一行的 `cwd`——**已经是 ADR1 的形状** | 删前两个判断 |
| 11 | `effects/helpers.rs` `parse_session_picker_entries` | `extract_first_user_prompt` 读 `chat_history.jsonl`；对 `source == "remote"` 的行再调 `resolve_local_session_any_cwd` | 选择器行没有摘要时的退路；把远程行改标本地 | ACP 的行永远有标题（缺省用 id）、`source` 恒为 `local`，走不到 | 删 |
| 12 | `export_cmd.rs` `load_updates_for_replay` | 旧布局的 `updates.jsonl` | `zypi export <id>` | 找不到：`Session '<id>' not found.`，退出 1（GAP） | 见「决定 6」 |
| 13 | `app/agent_view/plan.rs` `plan_file_path` | `sessions/<编码后的 cwd>/<id>/plan.md` | Plan 预览 | 没有人写这个文件 | 随 Plan 的去留（决定 7） |

*(3) 现状实测：13 例 PTY 验收矩阵。* `scripts/tui_pty/resume_matrix.py`（`babd75f`）用 TUI 做出一个会话（mock 模型，第一条消息 `remember this`），再带着各旗标重新起 `zypi`，看它有没有做帮助文字承诺的事。WSL2 上 **9 PASS、4 GAP、0 FAIL**；GAP 不让脚本失败，`--strict` 让它们失败——落地 ADR1 的改动要让 `--strict` 全绿。

| # | 用例 | 结果 | 现状 |
|---|---|---|---|
| 1 | `--continue` 恢复本目录最近的会话 | PASS | 只因为 #2 的退路碰巧读得懂 Python 的文件头；标题丢了 |
| 2 | `--continue`，本目录没有会话 | PASS | `No session found`，退出 1 |
| 3 | `--resume <id>`，在会话的目录 | PASS | 本地读不到，交给 agent 的 `session/load` |
| 4 | `--resume <id>`，换一个目录 | PASS | 同上；Python 绑定的是会话保存的 cwd（`metadata.cwd or cwd`），TUI 一侧的目录见「没有验证的」 |
| 5 | `--resume <标题>`，在会话的目录 | **GAP** | 只匹配旧 agent 的摘要：`no session id or title matched`，退出 1 |
| 6 | `--resume <标题>`，在没有该会话的目录 | PASS | 同样的报错，退出 1（标题按目录匹配，期望如此） |
| 7 | `--resume`（无参数） | PASS | 选择器，本来就是 ACP |
| 8 | `--resume <未知 id>` 报错并点名这个 id | PASS | 但进了 TUI 才报 `Turn failed: Couldn't load session: Resource not found`，状态栏的模型名是 `unknown`；用例按「屏幕上或退出状态里有」放行，提议 5 要把它收紧成「进 TUI 之前就退出 1」 |
| 9 | `--resume <未知标题>` | PASS | 退出 1 |
| 10 | `--session-id <新 uuid>` 用这个 id 开新会话 | **GAP** | Python 忽略 `session/new` 的 `_meta.sessionId`，自己挑 id |
| 11 | `--session-id <已用的 id>` 被拒绝 | **GAP** | pager 的占用检查读旧布局，看不见 Python 的会话，于是用另一个 id 开了新会话 |
| 12 | `zypi export <id>` 输出会话 | **GAP** | `Session '<id>' not found.`，退出 1 |
| 13 | `zypi export <未知 id>` 失败且没有输出 | PASS | |

矩阵之外看到的两件事：选择器丢掉 `updatedAt` 早于 30 天的行（`parse_session_picker_entries`，Grok 留下的）——Python 会话 30 天没动就从 `/resume` 里消失，`--resume <id>` 仍能恢复；选择器要等所有页回来才画（`fetch_session_list`，最多 `MAX_SESSION_LIST_PAGES` = 100 页，即 5,000 个会话；重复游标与空游标当作结束，中途某页失败则整体失败，不会悄悄少一半）。

*(4) 落地设计（提议；改变行为的地方标「需确认」）。*

**流程。** 今天：`main.rs` 的 `apply_sandbox`（不可逆）→ `app::run` → `materialize_startup`（读盘，约 544 行）→ … → `bounded_connect(acp::connect)`（约 716 行）。ADR1 之后：`apply_sandbox` → `app::run` → `bounded_connect(acp::connect)` → **`resolve_startup(&AcpAgentTx, intent, cwd)`**（只用 `session/list`）→ 起 TUI。`SessionStartupIntent`（纯函数，不读盘）原样保留；`materialized`、`session_title`、`session_cwd` 在 `app/mod.rs` 里只有两处用到（`connect` 之前设终端标题的 691 行，与交给 App 的 766–769 行），挪到 `connect` 之后不难。注意终端在 `connect` 之前就准备好了（`terminal`、`restore_terminal`）：解析出错要走 `connect` 失败的那条路径——还原终端、返回 `Err`、由 `main` 打印并退出 1——而不是进了 TUI 才报。

**逐旗标。**

| 旗标 | 解析方式 | 需要 Python 做的 |
|---|---|---|
| `--continue` | `session/list{cwd}`；没有 → `No session found for current directory`，退出 1。「最近」按哪个排序见决定 4（需确认） | 无 |
| `--resume <id>` | `session/list`（不带 `cwd`）翻页找 id；找到就取它的 `cwd` 作 `session_cwd`（选择器已经这样做）；找不到 → 进 TUI 之前退出 1（需确认，决定 5） | 无 |
| `--resume <标题>` | `session/list{cwd}` 翻页，用现成的 `select_by_title`（纯函数）匹配 | 无 |
| `--resume`（无参数） | 选择器，不变 | — |
| `--session-id <uuid>` | 带 `_meta.sessionId` 发 `session/new`；agent 拒绝重复，pager 按错误退出 1（需确认，决定 3） | **兑现 `_meta.sessionId`**：校验是 UUID，不存在就用它做会话 id（`JsonlSessionRepo.create` 已经接受 `options["id"]`），已存在则 `invalid_params`；约 10 行加测试 |
| `zypi export <id>` | 决定 6 | 看选项 |

**沙箱顺序（落地设计里最硬的一处）。** 今天 `--continue` 与 `--resume <标题或 id>` 在 `apply_sandbox` 之前，由 `PagerArgs::pin_local_resume_target`（`cli.rs`；调用 `session_title_resolve::presandbox_resume_target`）把目标钉成会话 id，再由 `saved_resume_profile` → `resumed_session_sandbox_profile` 去看该会话**保存的**沙箱 profile，让恢复的会话沿用创建时的沙箱；与 `--sandbox` 冲突时 `resolve_startup_sandbox` 报 `SandboxStartup::Conflict`（`pi-pager-bin/src/main.rs`）。这与 ADR1 正面冲突：沙箱不可逆，必须在 agent 起来之前施加；而按 ADR1 解析目标，要先起 agent。三条路：

1. **去掉「恢复时沿用保存的 profile」**：沙箱只由 `--sandbox` 与配置决定。
2. 先起一个不带沙箱的「查询用」agent 解析目标，再施加沙箱起真的那个：冷启动翻倍，还有一段没有沙箱的窗口。
3. 让 Python 把 profile 存进会话，经 `session/list` 的 `_meta` 公布：仍然要先起 agent，同 2。

**提议 1。** Python 会话从来不存 profile，`resumed_session_sandbox_profile` 对它恒返回 `None`（矩阵里 `--continue` 与 `--resume <id>` 两例都没读到过 profile），所以对现有会话**没有行为变化**，只是删掉一条对 Python 永远不生效的分支。可以整块删：`pin_local_resume_target`、`PinnedResumeTarget`、`resume_target_pinned`、`pinned_resume_profile`、`saved_resume_profile`、`SandboxStartup::Conflict`、`TitleResolution::PinnedPreSandbox`；「钉住」要防的竞争（两次标题查找之间被改名）在只查一次时不存在。代价：将来想要「会话记住自己的沙箱」，要另想办法（那也该是 agent 的事，并且要先解决「沙箱先于 agent」的顺序）。

**Python 一侧的两项要求。** ① `session/new` 兑现 `_meta.sessionId`（见上）。② `session/load` 的响应带上会话的 cwd（放在 `_meta` 里）：今天 `load_session` 的响应只有 `configOptions` 与 `pi/*` 模型提示。选择器那一条路径已经把选中行的 `cwd` 传给 `session/load`；CLI 的 `--resume <id>` 今天传 `None`（表 #3），所以从别的目录 `--resume <id>` 时，agent 在会话的目录里干活，TUI 却在启动目录里（影响 `@` 搜索、diff、git 信息——**没有验证**，见下）。按本设计，`resolve_startup` 找到会话时就带着它的 `cwd`，两条路径一致，第 ② 项就只是保险。

**验收。** ① `resume_matrix.py --strict` 13/13 PASS（用例 8 收紧后）。② 门禁：把 `session::persistence`、`session::storage`、`resolve_local_session`、`list_summaries` 加进 `tui_baseline.py gates` 的 deny-list（和 `MvpAgent` 同一类），`pi-pager` 与 `pi-pager-bin` 的非测试代码里零命中。③ `python_agent_e2e.rs` 加用例：对一个有会话的 agent 解析 `--continue`、跨页的 `--resume <标题>`、未知 id 的错误、`_meta.sessionId` 被兑现与重复被拒。

**PR 切分（提议）。** PR 1（Python，不动 TUI）：`_meta.sessionId` 与 `session/load` 带 cwd，加到 `test_acp_stdio_contract.py`。PR 2（Rust，只加不删）：`resolve_startup` 与它的 e2e，接上 `--continue`、`--resume`、`--session-id`；此时旧的读盘路径还在。PR 3（Rust，删）：沙箱顺序那一整块、`find_most_recent_jsonl_session`、#6–#11 的读盘、`list_summaries(None)` 补标题；`export_cmd` 按决定 6；再之后（阶段 B）删 `pi-shell` 的 `session::{persistence, storage}` 实现层。

**覆盖条件：没有触发**（见 (1)），ADR1 的默认值不变。

**模式切换与权限的实测（0.8 的证据、1.P3 的输入）。** `scripts/tui_pty/mode_probe.py`（`679cccb`；`d426253` 又加了 `--permission-mode` 的六个值）用真 zypi 与 `_tool_agent.py`（权限模式 `ask`，脚本化地让模型调一次 `write`，写 `probe.txt`），对每种选模式的办法看：状态栏写什么，问不问，没人回答时文件写了没有，答「允许」后写了没有。它不判对错，只记录。WSL2：

| 选了什么 | 状态栏 | 问了吗 | 没人回答就写了吗 | 答「允许」后写了吗 |
|---|---|---|---|---|
| Normal（什么都没选） | （无） | 问 | 否 | 是 |
| `--always-approve` | `· always-approve` | 不问 | **是** | — |
| Shift+Tab ×1：Always-Approve | `mock · always-approve` | 不问 | **是** | — |
| Shift+Tab ×2：回到 Normal | `mock` | 问 | 否 | 是 |
| Shift+Tab ×3：Plan | `mock · plan` | 问 | 否 | **是** |
| Shift+Tab ×4：Auto | `mock · auto` | **不问** | **是** | — |
| `--permission-mode default` | （无） | 问 | 否 | 是 |
| `--permission-mode acceptEdits` | （无） | 问 | 否 | 是 |
| `--permission-mode auto` | （无） | 问 | 否 | 是 |
| `--permission-mode dontAsk` | （无） | 问 | 否 | 是 |
| `--permission-mode bypassPermissions` | `· always-approve` | 不问 | **是** | — |
| `--permission-mode plan` | （无） | 问 | 否 | 是 |

四个事实，都是提议的依据：

1. **Plan 不限制任何东西。** 它只是 UI：`Effect::SetSessionMode` 发 `session/set_mode`，失败只记日志（`effects/mod.rs`）；Python 没有 `set_session_mode`，SDK 的路由登记了这个方法但没有处理器，对方得到 `method_not_found`（`acp/router.py` 的 `Route.handle`）。界面照样显示 `plan`，用户以为在只读规划，agent 按 Normal 的规则照写（表第 5 行：问了，答「允许」就写出）。`--permission-mode plan` 连 Plan 都不进（表末行：状态栏什么也没有）。
2. **Auto 在 Python 里等于 Always-Approve。** `needs_permission` 对 `auto` 与 `always-approve` 都返回「不问」（`permissions.py`）；而 pager 对 Auto 的描述是「LLM classifier」（设置页 `settings/defs.rs`、切换时的提示 `Permission mode: Auto (classifier)`）。分类器随 Rust runtime 一起没了，界面却还在承诺它。Always-Approve 则另有一层：pager 在 yolo 模式下自己代答 agent 的权限请求（`acp_handler/permissions.rs`：选 `AllowOnce` 选项立即答复），所以屏幕上不出现问题；Auto 没有这一层，Auto 的静默完全靠 Python 收到了模式变更。
3. **设置里的「Default」被 Python 丢掉。** `pi/yolo_mode_changed` 的 `permission_mode` 是 `default` 时，Python 只认 `ask` / `auto` / `always-approve`（`_permission_mode_from_notification` 对其他值返回 `None`），agent 的模式不变：在 Always-Approve 之后选 Default，界面说 Default，agent 仍不问（这一条是读代码加 Python 一侧的断言，没有用 PTY 走设置菜单）。Shift+Tab 回到 Normal 发的是 `ask`，没有这个问题（表第 4 行）。
4. **启动时选的模式，agent 并不知道。** pager 在 `session/new` / `session/load` 的 `_meta` 里放 `yoloMode`、`autoMode`（连同 `agentProfile`、`askUserQuestion`，以及设了才有的 `modelId`、`sessionId`；`SessionFlags::to_meta`），Python 一个都不读（SDK 把 `_meta` 摊平成关键字参数交给 `new_session(**kwargs)`，没有人取用）。所以 `--always-approve` 与 `--permission-mode bypassPermissions` 能生效，靠的是上面说的 pager 代答，不是 agent 被告知了；而 `--permission-mode` 的其余五个值（`default`、`acceptEdits`、`auto`、`dontAsk`、`plan`）与不带旗标一模一样：不显示，照样问——`acceptEdits` 并没有自动接受编辑，`dontAsk` 并没有不问。`--help` 把六个值都列为可选。

另外，权限模式有两个所有者：pager 的 `config.toml`（`[ui].permission_mode`，每次切换都写盘）与 Python 的 `agent.toml`（`permission`），运行中靠 `pi/yolo_mode_changed` 单向同步，启动时没有同步（见事实 4）。

**旗标（1.P6 的输入，读代码加上面的 PTY）。** 交互模式下，`--system-prompt-override` 与 `--rules` 经 `initialize._meta`（`systemPromptOverride`、`rules`，连同 `clientType`、`clientVersion`）发出，Python 一个都不读（`pi_agent_cli` 里搜不到这四个键）；`--allow` / `--deny` 进了 `AgentConfig.cli_agent_overrides.permission_rules`（`acp/mod.rs`），而 `spawn.rs` 起子进程时只带命令行参数与 `PI_HOME`，所以到不了 Python。`-m` / `--model` 的 `modelId` 同样放在 `session/new` 的 `_meta` 里，Python 不读；是否另有路径让它生效（例如会话建好之后的 `session/set_config_option`）没有查。已经看到有效的只有 `--always-approve`、`--permission-mode bypassPermissions`，以及 `--continue` / `--resume` / `--session-id` 的一部分（矩阵）。**其余旗标没有逐个实测。**契约要用户定（决定 8）。

**1.P1 / 1.P4 / 1.P7（阶段 1 里不需要拍板的部分，都已提交）。**

- **1.P1 的余项。** pager 的选择器跟着 `nextCursor` 翻页（`9f7bc35`，`fetch_session_list`，6 个单测）；Python 的 `session/list` 分页（`c1f02c8`）：每页 50，游标是不透明、无状态的（里面是下一页接着往下的文件名，agent 不记任何状态，重启后游标照样有效），不是自己发的游标得 `invalid_params`，响应里没有 `nextCursor` 就是到头——与 ACP 对 `session/list` 的要求一致（游标不透明、客户端不解析、缺省 `nextCursor` 表示结束、agent 自己限页大小、非法游标应报错；经 Context7 核对 ACP 文档）。harness 一侧：`JsonlSessionRepo.list_page` / `find`（`1b9fb47`），读文件头不读全文（`54a38fe`）。数字见 (1)。ACP 文档对 `session/list` 的排序只说「默认排序」；Python 按文件名（创建时间）倒序，`updated_at` 是文件修改时间。
- **1.P4。** pager 不再往 `session/new` / `session/load` 里放 `mcp_servers`（`7becb49`，单测 `session_new_and_load_send_no_mcp_servers`）；Python 忽略 `mcp_servers`，并在会话开始时说一次哪些被忽略了（`mcp_notice.py`，`935e4b8`）。这是一处**有文档的偏离**：ACP v1 要求 agent 支持 stdio 传输的 MCP，而本项目不接 MCP（Phase 4 非目标）。`docs/TUI-AND-CODE-AGENT.md`（`9798e1d`）与 `packages/pi-agent-cli/AGENTS.md`（`935e4b8`、`c1f02c8`）已写明。`load_mcp_servers` 与 `mcp.rs` 现在没有调用者，是清理候选（决定 10）。
- **1.P7。** 权限往返与 `session/cancel` 的线上用例（`28fed94`，`test_acp_stdio_tools.py`；`_tool_agent.py` 可由 `PI_TEST_TOOL_CALL`、`PI_TEST_PERMISSION`、`PI_TEST_TOOL_PIDFILE`、`PI_TEST_STUCK_THREAD` 配置）。

**仓库里的改动。** 15 个代码与脚本提交（31 个文件，+3,701 / −88），叠在 `d84d98a` 之上；第 16 个提交是本节与附录的文档。

| 提交 | 内容 |
|---|---|
| `28fed94` | 1.P7：权限往返与取消的线上用例，`_tool_agent.py` 可配置 |
| `54a38fe`、`1b9fb47` | harness：读文件头不读全文；`list_page` / `find` |
| `e944ed5` | `scripts/session_list_bench.py` |
| `9f7bc35`、`c1f02c8` | 1.P1：pager 跟 `nextCursor`；Python 分页 |
| `935e4b8`、`7becb49`、`9798e1d` | 1.P4：Python 说明被忽略的 MCP；pager 不发 `mcp_servers`；文档 |
| `affda9e` | agent 自己退出时的提示、启动失败的原因（e2e 发现的两个 bug） |
| `3fc182b` | 0.3：`python_agent_e2e.rs`，`tui-ci.yml`，清单 |
| `babd75f` | 0.7：`resume_matrix.py` |
| `bee3a36` | 清单里 `pi-pager` 的 `reference_passed` 按一次真实运行改成 5,602 |
| `679cccb`、`d426253` | 1.P3 的输入：`mode_probe.py`（Shift+Tab 的循环与 `--always-approve`；再加 `--permission-mode` 的六个值） |

**验证。**

| 检查 | 结果 |
|---|---|
| 10 个 suite 整套（基线执行器，清环境，`PI_E2E_PYTHON` 与 `PI_E2E_REQUIRED=1`，WSL2，增量编译，退出码 0，整条命令约 26 分钟） | ✓ **合计 8,659 通过 / 0 失败 / 22 忽略**（= CI 的 8,634 + 25）。`pi-pager` 5,602（13 忽略），比 `d84d98a` 的 5,577 多 25 个：10 个 e2e、8 个 `agent_exit_tests`、6 个 `fetch_session_list_*`、1 个 `session_new_and_load_send_no_mcp_servers`；10 个 e2e 用例真的对着 Python agent 跑了（`PI_E2E_REQUIRED=1`，该目标用时 27.9 s）。其余 9 个 suite 都等于各自的 `reference_passed`（`pi-shell` 1,056、`pi-pager-render` 1,092、`pi-pager-minimal` 79、`pi-pager-bin` 21、`pi-acp-lib` 21、`pi-http` 13、`pi-telemetry` 239、`pi-file-utils` 216、`pi-sampling-types` 320）；`pi-pager-render` 的 `known_failures`（tmux 探测）这一轮没有失败。更早只跑 `pi-pager` 的那次：5,602 / 0 / 13，223 s |
| Python 全量 pytest（`real_llm` 除外） | ✓ Linux（WSL2 的 ext4 克隆，Python 3.12.3）：**2,352 通过 / 0 失败 / 35 跳过 / 31 取消选择**，105 s；Windows（Python 3.12.7）：**2,351 通过 / 0 失败 / 36 跳过 / 31 取消选择**，296 s。比 r8 多 192 / 195 个，都是本分支新增的用例。Linux 的 35 个跳过里有 5 个是这个 venv 没装 `pyte`，带上 `pyte` 单独跑 `scripts/tests/test_tui_pty.py`：19 通过；其余是 `langchain_deepseek` 缺失与 Windows 专属。Windows 的跳过多数是 POSIX 专属 |
| `resume_matrix.py`（WSL2） | 9 PASS / 4 GAP / 0 FAIL |
| `mode_probe.py`（WSL2） | 12 个场景，见「模式切换与权限的实测」的表 |
| `ruff check .`、`ruff format --check .` | ✓ 项目 venv 的 Ruff 0.16.10 与 CI 钉的 0.16.0（`uvx --from ruff==0.16.0`）：`ruff check .` 全过，`ruff format --check .` 226 个文件已格式化 |

**没有验证的。**

- 新的 CI 步骤（装 Python agent、`PI_E2E_*`）与新增的路径过滤，在 GitHub 上没跑过；清单里 `pi-pager` 的 5,602 与整套的 8,659 是 WSL2 上本地运行的数，**要等第一次 CI 运行证实**。
- Rust 的 e2e 与 PTY 脚本只在 Linux（WSL2）上跑过；macOS 与 Windows 没有。Windows 的延迟数只量了 Python 一侧，TUI 没有参与。
- 延迟：冷磁盘、网络文件系统、超过 1,000 个会话、真实形状的会话（量的是合成的 12 / 120 / 2,000 条消息）。
- **跨目录的 `--resume <id>`**：TUI 的当前目录与 agent 绑定的会话目录可能不同，`@` 搜索、diff、git 信息用哪个目录没有验证；选择器那条路径传了 `cwd`，CLI 那条传 `None`。
- 设置菜单里选 Default 的那条路径只有代码与 Python 一侧的断言，没有 PTY 走过；旗标除了 `--always-approve`、`--permission-mode`（`mode_probe.py`）与 `--continue` / `--resume` / `--session-id`（矩阵），没有逐个验证交互模式下是否生效，`-m` / `--model` 尤其没有查。
- 落地设计（`resolve_startup`、沙箱顺序、两项 Python 要求）是纸面设计，没有写一行代码。
- 全部用 mock，没有真实 LLM。

**留给用户决定的。** 十二项，每项给出我的倾向；没有一项已经动手。

1. **ADR1 的落地要不要开工，按上面的 PR 切分。** 覆盖条件没有触发。倾向：开工，先做 PR 1（Python 两项，不动 TUI，风险最小）。
2. **沙箱顺序。** 倾向：去掉「恢复时沿用保存的沙箱 profile」（对 Python 会话没有行为变化）。备选是先起查询用的 agent（冷启动翻倍、有无沙箱的窗口）。
3. **`--session-id` 的契约。** ACP v1 的 `session/new` 没有 session id 参数，`_meta.sessionId` 是 pager 与 agent 之间的约定。倾向：Python 兑现它，重复由 agent 以 `invalid_params` 拒绝。也可以直接删掉这个旗标。
4. **`--continue` 的「最近」。** Python 的列表按创建时间倒序（ACP 对排序只说「默认排序」）。选项：最近创建（取第一页第一条，最快）；最近活动（`updatedAt` 最大，要翻完所有页，1,000 个会话在 Windows 上约 0.8 s）。倾向：最近活动——用户续的通常是上次用过的，不是上次建的。
5. **未知 id 的报错时机。** `--resume <未知 id>` 现在进了 TUI 才报，状态栏的模型名还是 `unknown`。倾向：进 TUI 之前报错退出 1（`resume_matrix.py` 的用例 8 随之收紧）。
6. **`zypi export`。** (a) 经 ACP 回放导出：起 agent、`session/load`、收 `session/update` 回放，复用现成的 `render_blocks_to_markdown`，约 100 行，多 1–2 s 的 agent 启动；(b) 删掉这个子命令；(c) 维持现状——对 Python 会话恒报 not found，一个静默坏掉的命令。r8 时用户选了不动。倾向：(a) 或 (b)，不要 (c)。
7. **模式映射（1.P3）。** 依据是上面的四个事实。(a) 立刻隐藏 Plan、Auto、Default（Shift+Tab 只剩 Normal ↔ Always-Approve），并让 `--permission-mode` 只接受有效果的值（`bypassPermissions`；`default` 等于不带旗标），其余值报错说明，不动 agent；(b) 做 `session/set_mode`：Python 实现 `set_session_mode` 并公布 `modes`，Plan = 把工具集裁到只读；(c) 其他。Python 的 `auto` 也要定义：今天等于 always-approve，设置页却说是分类器——改名、删除，或真做分类器。倾向：(a) 先行，因为 Plan 现在给用户的是虚假的安全感；(b) 另议。
8. **旗标契约（1.P6）。** `--system-prompt-override`、`--rules` 在交互模式下无效；`--allow` / `--deny` 没有传给 agent；`--permission-mode` 的六个值里只有 `bypassPermissions` 有效；`-m` 的路径没有查清。选项：Python 读 `initialize._meta`（前两项各几行）；或 pager 在对着 Python agent 时拒绝这些旗标并说明；`-p` 另行定义。要用户定哪些旗标属于产品契约。
9. **选择器的 30 天截止。** 倾向：去掉（agent 已分页，不怕多）；或者改成可配置。
10. **清理。** `load_mcp_servers` / `mcp.rs`（1.P4 之后没有调用者）、`recent_dirs.rs`（只剩一行文档注释）；ADR1 落地后的 `pi-shell` 的 `session::{persistence, storage}` 实现层（阶段 B）。
11. **选择器边翻边显示。** 现在等所有页回来再画（1,000 个会话约 0.8 s，Windows）。倾向：不做，除非有人遇到。
12. **推送与 PR。** 分支 `codex/phase1-contract-docs`（16 个提交，叠在 PR #9 上）要不要推、怎么切。倾向：两个 PR——代码（1.P1 / 1.P4 / 1.P7、e2e、退出提示）与脚本加文档，或者一个。推之前按仓库规则先跑 Ruff 与两个平台的 pytest（见「验证」）。注意 `tui-ci.yml` 现在改 `pi_agent_cli/**` 也会跑；新的 e2e 步骤与 5,602 要等第一次 CI 运行证实。PR #9 若先被 squash 合并，用 `git rebase --onto origin/main <PR #9 的旧头> codex/phase1-contract-docs` 摘下来。

## 附录 A：能力矩阵与 ACP 版本配对（阶段 0.8，r9 定稿）

「状态」一列：**已定**＝按 r2 的默认决策做完，或有实测支撑；**提议**＝会改变行为，写在 §10.11 末尾「留给用户决定的」里，等用户确认；**未定**＝阶段 1 的待办，r9 没有动。r5 对初稿的执行结果留作 A.4。测试名与脚本名都在仓库里能搜到。

### A.1 能力矩阵

| 能力 | pager 侧 | Python 现状（r9 实测） | 决定 | 状态 | 证据 |
|---|---|---|---|---|---|
| session new / load / resume / close | 标准 ACP；pager 只调用 new / load，没有 resume / close 的调用点 | 已覆盖：`new_session`、`load_session`、`resume_session`、`close_session`；`initialize` 公布 `load_session` 与 `session_capabilities.{list, resume, close}`；`run_agent(use_unstable_protocol=True)` 让 unstable 的 `close` / `resume` 在线上可用 | 保留 | 已定（r5） | `test_acp_stdio_contract.py`；`connect_runs_the_handshake_and_stop_leaves_nothing_behind` |
| session list | `ListSessionsRequest{cwd, cursor}`；选择器跟 `nextCursor`，最多 100 页 | 已覆盖：每页 50，不透明无状态游标，非法游标 `invalid_params`；`title` = 第一条 user message，`updated_at` = 文件 mtime；顺序是创建时间倒序 | 保留；「最近」的语义见决定 4，30 天截止见决定 9 | 已定（1.P1，r9） | `test_session_list_paging.py`；`session_list_pages_are_followed_by_cursor`；`session_list_bench.py` |
| session delete | `pi/session/delete`（扩展请求） | 已覆盖（`ext_method`）；SDK 0.12.1 没有标准的 `session/delete` 路由 | 保留并登记（ADR4）；SDK 补路由后迁到标准方法 | 已定 | `test_session_lifecycle.py` |
| session rename | 入口与整条链路已删 | 无 | 删 | 已定（r5） | A.4 |
| 恢复的入口：`--continue`、`--resume <id｜标题>` | 现在读盘解析（13 处读盘调用，§10.11） | `session/list` 加 `session/load` 够用；延迟实测没有触发 ADR1 的覆盖条件 | 改为经 ACP 解析（ADR1） | 已定（ADR1）；落地设计与细节待确认（决定 1–5） | `resume_matrix.py`（9 PASS / 4 GAP）；`session_list_bench.py` |
| `zypi export` | 读旧布局的会话文件 | 无 | 决定 6：经 ACP 回放，或删子命令 | **提议** | `resume_matrix.py` 用例 12、13 |
| prompt / cancel | 标准 ACP | 已覆盖；取消后正在跑的 `bash` 进程被回收（Linux 的用例） | 保留 | 已定 | `a_prompt_streams_the_reply_and_ends_the_turn`；`cancel_ends_a_running_tool_as_cancelled_and_reaps_it` |
| 权限请求 | 应答 `session/request_permission`；yolo 模式下 pager 自己代答（选 `AllowOnce`，`acp_handler/permissions.rs`） | 已覆盖：`ask` 模式下问 `bash` / `edit` / `write` / `workflow`（没声明无害的工具，AGENTS.md 不变量 8）；工具调用的选项只有 allow-once / reject-once（`permissions.py`）；项目信任的问题另带 `allow_always`（`trust_prompt.py`） | 保留；工具调用不做 allow-always 与规则 | 已定：pager 不依赖 allow-always（PTY 里两个选项的问题框能画、能答）；提议：不做 | `a_permission_question_reaches_the_client_and_its_answer_decides`；`mode_probe.py` |
| 模型选择 | 读 `configOptions`（`category: "model"`），`session/set_config_option`；没有该配置项时退回 `session/set_model` | `configOptions`（`model`）与 `set_config_option`（只认 `model`，其他 `invalid_params`）；`[[models]]` 提供候选；`session/set_model` 没有路由，但 Python 总是公布 `model` 配置项，退回路径用不到 | 恢复（ADR3） | 已定（r3） | §10.2、§10.3；`test_acp_agent.py` |
| 模式切换 | Shift+Tab 循环（Normal → Always-Approve → Normal → Plan → Auto）、`--always-approve`、`--permission-mode`（六个值）；`pi/yolo_mode_changed` 通知；Plan 另发 `session/set_mode`；`session/new` 的 `_meta.yoloMode` / `autoMode` | 只认 `pi/yolo_mode_changed` 里的 `ask` / `auto` / `always-approve`；`session/set_mode` → `method_not_found`；`_meta` 不读；`auto` 等于 `always-approve`；`default` 被丢弃 | 隐藏 Plan / Auto / Default；`--permission-mode` 只收有效的值；做不做真的 Plan 另议（决定 7） | **提议** | `mode_probe.py`（§10.11 的 12 行表） |
| MCP | UI 已删（r5）；不再发 `mcp_servers`（`7becb49`） | 忽略 `mcp_servers`，会话开始时说一次哪些被忽略（`mcp_notice.py`） | 降级为显式忽略；ACP v1 要求 agent 支持 stdio MCP，这是有文档的偏离 | 已定（默认决策，r9 实现） | `test_mcp_notice.py`；`session_new_and_load_send_no_mcp_servers` |
| queue / interjection | 已删（r5，含 steer、send-now） | 无 | 删 | 已定（r5） | A.4 |
| compaction | 触发与展示 | 部分：`auto_compact` 由 `max_turns >= 30` 推导 | 降级 | 未定（r9 没有重新核对） | — |
| subagents / 后台任务 | 已删（r5） | 无 | 删（Phase 4 非目标） | 已定（r5） | A.4 |
| skills | slash 命令与 `available_commands_update` | 已覆盖；`available_commands_update` 在响应之后发（Zed 要收到响应才登记会话，`new_session` 里有注释） | 保留 | 已定 | `test_acp_agent.py` |
| hooks / plugins / marketplace | 已删（r5） | 无 | 删（Phase 4 非目标） | 已定（r5） | A.4 |
| image | 图片附件 | 代码有（`prompt_capabilities.image`、`_prompt_to_text_images`），**`packages/pi-agent-cli/tests` 里没有走这条路径的用例** | 保留 | 已定；缺测试 | — |
| status line | pager 本地功能（用户脚本画状态栏，`pi-status-line`）；`ConnectFlags.status_line` 已不再公布给 agent（`client_capabilities_meta` 返回空） | 与 agent 无关 | 保留功能；那个没人读的字段是清理候选；payload 里的字段在 Python 会话下是否为空没有查 | 提议（清理）；其余未定，不挡阶段 1 | — |
| 历史回放 | `session/load` | 已覆盖（`isReplay` 的 `session/update`）；`session/resume` 不回放 | 保留 | 已定 | `a_session_is_listed_with_a_title_and_replayed_by_a_later_agent` |
| 登录 | `auth_methods` 为空时不要求登录 | `auth_methods=[]`；`authenticate` 没有处理器 | 保留 | 已定 | `connect_runs_the_handshake_and_stop_leaves_nothing_behind` |
| `initialize._meta`：响应里的键 | 解 `modelState`、`grokShell`、`availableCommands`、`cancelRewind`、`sessionRecap`、`feedbackTraceOffer` | Python 的响应没有 `_meta`，都落到缺省 | 逐键删解码（1.R5） | 未定（阶段 1 待办） | — |
| `initialize._meta`：请求里的键 | 发 `clientType`、`clientVersion`；带 `--system-prompt-override` / `--rules` 时再发 `systemPromptOverride`、`rules` | 一个都不读 | 要么 Python 读，要么 pager 拒绝这两个旗标（决定 8） | **提议** | §10.11「旗标」 |
| `--session-id` | `session/new` 的 `_meta.sessionId`；占用检查读旧布局 | 忽略 `_meta.sessionId`，自己挑 id | Python 兑现，重复由 agent 拒绝（决定 3） | **提议** | `resume_matrix.py` 用例 10、11 |

### A.2 ACP 版本配对与升级策略

| | Rust（pager） | Python（agent） |
|---|---|---|
| 包 | `agent-client-protocol` 0.10.4（`tui/Cargo.toml`，feature `unstable`；`Cargo.lock` 固定）；`agent-client-protocol-schema` 0.11.4（`Cargo.lock`） | `agent-client-protocol` `>=0.12.0`（`packages/pi-agent-cli/pyproject.toml`；开发环境装的是 0.12.1）；SDK 里的 schema 记为 `schema-v1.19.0`（`acp/meta.py` 头部的 `Schema ref`） |
| 线上版本 | `initialize` 发 `ProtocolVersion::V1`（`acp/mod.rs`） | `acp.PROTOCOL_VERSION` = 1；`initialize` 回 `min(请求的版本, 1)`（`agent.py`） |
| unstable | feature `unstable` 开着 | `run_agent(use_unstable_protocol=True)`；SDK 里 `session/close`、`session/resume`、`session/fork` 的路由标着 `unstable=True` |

规则（ACP 文档，经 Context7 核对；本仓库没有为 v2 写过任何东西）：

1. **线上兼容只看 `initialize` 协商出的整数 `protocolVersion`，与 crate、schema、SDK 的发行版本号无关**；同一个 `protocolVersion` 之内，用交换的 capabilities 判断可选的消息与特性。上表里 Rust 的 0.10.4 / 0.11.4 与 Python 的 0.12.1 / `schema-v1.19.0` 是不同的编号，不能互相比大小。
2. 协商：client 发它支持的最高版本；agent 支持就确认，不支持就回它自己支持的最高版本；client 不能接受就应当断开。
3. ACP 文档里已经有 **v2**，是破坏性升级：`initialize` 的 `clientCapabilities` / `clientInfo` 变成 `capabilities` / `info`；agent 的能力标记（`loadSession`、`list`、`resume`、`close`）去掉，prompt 与 MCP 的能力挪到 `session` 下；`session/request_permission` 多了必填的 `title`。**只支持 v1 的 agent 收到 v2 的 `initialize` 会回 `protocolVersion: 1`**，由 client 决定继续还是断开。本仓库里已有的痕迹：`agent.py` 的 `_run_prompt` 记着 v2 草案里的 `state_update` 两边都解析不了，所以 agent 从不声称自己是协议 2。
4. 现在两侧都是 v1，配对成立；`connect_runs_the_handshake_and_stop_leaves_nothing_behind` 每次 CI 都在验证这一点。
5. **升级策略（提议）**：Rust 一侧由 `Cargo.lock` 固定，不会自己动；Python 一侧只有下限 `>=0.12.0`，SDK 还是 0.x，次版本号的升级可以带破坏性改动。提议给 SDK 依赖加上限（`<0.13`），升级成为一次有意的改动：在同一个分支里升，跑 `test_acp_stdio_*` 与 `python_agent_e2e.rs`，再合。两边都有 v2 之后，Rust crate 与 Python SDK 一起升，并且 pager 与 agent 各自保留对 v1 的回退，直到旧版本不再需要。
6. 到 v2 时，「忽略 `mcp_servers`」是否仍算偏离，取决于 v2 是否还要求 agent 支持 stdio MCP——文档里 MCP 的能力已经是 agent 公布的项，**没有查证**要求有没有变。

### A.3 逐方法：pager 发什么，Python 怎么答

| 方法 | pager 发送 | Python 的回答 | 备注 / 证据 |
|---|---|---|---|
| `initialize` | `protocolVersion: 1`；`_meta`：`clientType`、`clientVersion`，可选 `systemPromptOverride`、`rules`；client 能力里 `fs`、`terminal` 由隐藏旗标 `--fs-read`、`--fs-write`、`--terminal` 决定（默认关），能力的 `_meta` 为空 | 公布 `load_session`、`prompt_capabilities.image`、`session_capabilities.{list, resume, close}`；`auth_methods=[]`；`agent_info`；响应没有 `_meta`；请求的 `client_capabilities`、`client_info`、`_meta` 存下或丢掉，不读 | 握手的 e2e |
| `authenticate` | 不发（没有 auth 方法） | 路由有，agent 没有处理器：`method_not_found` | — |
| `session/new` | `cwd`，空的 `mcp_servers`；`_meta`：`yoloMode`、`autoMode` 总有，`agentProfile`、`askUserQuestion: false`、`modelId`、`sessionId` 视情形（`SessionFlags::to_meta`） | 建会话；响应带 `configOptions`（`model`）与 `_meta`（`pi/currentModelId`、`pi/currentModelDisplayName`、`pi/provider`）；`mcp_servers` 非空时通知一次；**请求的 `_meta` 一个都不读**（SDK 把它摊平成关键字参数，没有人取用） | `resume_matrix.py` |
| `session/load` | `session_id`、`cwd`、空的 `mcp_servers`、同样的 `_meta` | 先回放历史（`isReplay` 的 `session/update`）再回应；工作目录用会话保存的 `cwd`（没有才用请求的）；未知 id → `resource_not_found`；响应不带 cwd | `a_session_is_listed_with_a_title_and_replayed_by_a_later_agent` |
| `session/list` | 当前目录的 `cwd`、`cursor`（跟 `nextCursor`，最多 100 页） | 每页 50，不透明游标；非法游标 `invalid_params`；`title`、`updated_at`、`cwd` | 与 ACP 文档对分页的要求一致；`session_list_pages_are_followed_by_cursor` |
| `session/resume`、`session/close` | 不发 | 已实现（`resume` 不回放） | `test_acp_stdio_contract.py` |
| `session/fork` | 不发 | 路由有（unstable），agent 没有处理器：`method_not_found` | — |
| `session/prompt` | 文本与图片块；`_meta.screenMode`（遥测） | 流式 `session/update`，回合结束给 `stopReason`；`_meta` 不读 | `a_prompt_streams_the_reply_and_ends_the_turn` |
| `session/cancel`（通知） | 取消当前回合 | 取消回合；正在跑的 `bash` 进程被回收 | `cancel_ends_a_running_tool_as_cancelled_and_reaps_it`（仅 Linux） |
| `session/request_permission`（agent → client） | 应答；yolo 模式下 pager 自己选 `AllowOnce` | 工具调用的选项 allow-once / reject-once；被拒时工具调用返回 `User denied permission` | `a_permission_question_reaches_the_client_and_its_answer_decides` |
| `session/set_config_option` | `model`（`/model`） | 只认 `model`，其他 `invalid_params` | r3 的 PTY |
| `session/set_model` | 仅当 agent 没公布 `model` 配置项时（兼容路径） | **SDK 没有这个路由**；Python 总是公布 `model`，用不到 | `acp/agent/router.py` |
| `session/set_mode` | Plan（Shift+Tab 第三档）；失败只记日志 | 路由有，agent 没有处理器：`method_not_found` | `mode_probe.py` |
| `pi/yolo_mode_changed`（通知） | 每次切换发，带 `yolo_mode`、`auto_mode`、`permission_mode`（要有会话才发） | `ext_notification`：只认 `ask` / `auto` / `always-approve`，其他值忽略 | `mode_probe.py` |
| `pi/session/delete`（扩展请求） | 删除会话 | 已实现；幂等（会话不存在也返回成功） | `test_session_lifecycle.py` |
| `fs/*`、`terminal/*`（agent → client） | 旗标开了才公布 | Python 一个都不调用（工具在本地执行）；`terminal_output` 只出现在 tool-call 更新的 `_meta` 里 | `events.py` |

### A.4 r5 执行结果（对照 r5 时初稿里的「默认决策」；r9 的更新见 A.1）

- **已按默认执行**：MCP——UI 已删（模态、init seed、elicitation 卡片、Claude 导入；`pi/mcp/*` 只剩 `pi-mcp` 自己的测试里出现）；queue / interjection——已删（含 steer、send-now）；subagents / 后台任务——已删（tasks 窗格、定时任务、workflows、goals）；hooks / plugins / marketplace——已删（含 `pi-plugin-marketplace` 与 `pi-hooks-plugins-types` 两个 crate）；session rename——入口与整条链路已删。
- **仍是阶段 1 的待办，r5 没动**：session list 的 Python 补齐（1.P1）；模式切换的映射（1.P3）；compaction 的降级展示；权限 allow-always；status line；`initialize._meta` 的解码精简（1.R5：`acp/mod.rs` 仍在解 `grokShell`、`modelState`、`cancelRewind`、`availableCommands`，其中 `cancelRewind` 对应的功能已删）。
- **新增的删除（表里没有）**：dashboard、recap / feedback / consent / coding-data-sharing、rewind / fork / jump / 外部会话、changelog、`--chat` 世界、`local-workspace`。它们都是入口审计里「Python 不路由、pager 也无法触发」的类别。
- **r9 的更新（0.8 定稿）**：1.P1（pager 跟 `nextCursor`，Python 分页）与 1.P4（pager 不再发 `mcp_servers`，Python 说明被忽略的）已做，1.P7 的余项补上了，见 §10.11。上面「仍是阶段 1 的待办」里剩下的：模式切换的映射（1.P3）、权限 allow-always、status line，r9 做了实测或核对并写成提议，没有改代码（A.1 的状态列）；`initialize._meta` 的解码精简（1.R5）与 compaction 的降级展示没动。`--session-id`、`--system-prompt-override` / `--rules`、`zypi export` 是 r9 新发现的待决项（A.1 里标「提议」的行）。

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

**B.4 r9 的复现**（Linux 或 WSL2；克隆放在 ext4 上，先 `cd tui && cargo build -p pi-pager-bin`，PTY 脚本的前提见 `scripts/tui_pty/README.md`）

```bash
# 0.7 的读盘调用点（§10.11 的表）。期望 12 个文件：表里的 8 个，加上不读会话文件的 4 个——
# session_notification.rs 与 views/session_title.rs（只用清洗标题的纯函数）、test_util.rs 与
# disk_usage_cmd/tests.rs（测试代码）；pi-pager-bin 没有命中。
# 模式里用 . 代替引号：Windows PowerShell 5.1 会吃掉传给 rg 的内层引号。
rg -c 'persistence::|session::storage|resolve_local_session|list_summaries|local_summaries_for_cwd_sync|extract_first_user_prompt|join\(.sessions.\)' \
  tui/crates/codegen/pi-pager/src tui/crates/codegen/pi-pager-bin/src

# 0.7 的延迟（§10.11 (1) 的表是 --entries 120；每个会话约 1 MB 的那组用 --entries 2000 --sessions 100）。
# 「改前」的数：让 pi_agent_cli 解析到 d84d98a 的检出，其余相同。在会话所在的文件系统上跑
python scripts/session_list_bench.py --sessions 100 1000 --entries 120 --runs 3

# 13 例恢复矩阵（今天 9 PASS / 4 GAP；落地 ADR1 之后 --strict 要全绿）
python scripts/tui_pty/resume_matrix.py --zypi tui/target/debug/zypi --python .venv/bin/python [--strict]

# 权限模式的 12 个场景（只记录，不判对错）
python scripts/tui_pty/mode_probe.py --zypi tui/target/debug/zypi --python .venv/bin/python

# 0.3 的 e2e：先装 agent（与 tui-ci.yml 同一组包），再走基线执行器
pip install -e ".[dev]" -e ./packages/pi-agent-harness -e ./packages/pi-agent-cli \
  -e ./packages/pi-web-access -e ./packages/pi-goal-x -e ./packages/pi-dynamic-workflows
PI_E2E_PYTHON="$(command -v python)" PI_E2E_REQUIRED=1 python scripts/tui_baseline.py test --suite pi-pager
```

附录 A.2 里的版本号：Rust 一侧看 `tui/Cargo.toml` 与 `tui/Cargo.lock`（`agent-client-protocol`、`agent-client-protocol-schema`），Python 一侧看 `python -c "import acp, importlib.metadata as m; print(m.version('agent-client-protocol'), acp.PROTOCOL_VERSION)"` 与 `acp/meta.py` 头部的 `Schema ref`；SDK 的路由面（哪些方法有路由、哪些是 `unstable`）在 `acp/agent/router.py`。ACP 的版本协商与 v2 的变化来自 ACP 文档（经 Context7 查）。

## 修订记录

- r1：初稿（仅建分支并新增计划文档，未改 runtime 实现）。
- r2：吸收源码核查与架构评估。主要变化：终态拆成行为层 / 依赖图层；新增原则 P1–P7、与既有架构的关系（§3）、决策记录（§6）、改动规模（§5）；阶段重排为「基线 → 死代码先行 → 协议与 Python → 拆 runtime → 收口 →（可选）依赖图瘦身」；`pi/` 扩展改准入制、会话数据改纯 ACP、leader 直接放弃；更正 `-p` / headless、证据失真（`*_cmd` 是死代码）、文档目标等事实；退出条件改为可机检。
- r3：在工作区执行 Rust runtime 拆除并恢复 `/model`（当时未提交，现已本地提交，见 §10）。主要变化：新增 §10 执行记录；ADR3 推翻 r2 的「`/model` 从白名单移除」，改为经 ACP Session Config Options 恢复（Python 公布 `configOptions` 并路由 `session/set_config_option`，pager 读 `configOptions`、无该配置项时退回旧 `session/set_model`）；P1 增补「任何 runtime（含将来的 pi-rust）都必须作为独立 ACP agent 位于 ACP 之后」；§7 勾选 A.1、A.2（主体）、B.1、B.5、B.6，A.3、A.4、B.3 未做，B.2、B.4 换了做法或只做了一部分；§4.4 关于「出站 `x.ai/*` 被 `channel.rs` 丢弃」的判断被实测推翻（§10.4）。验证以消费者构建、单测、PTY 真机 `/model` 切换与 OpenRouter 真实会话为准，没有 Linux / Windows 结果，阶段 0（CI、基线、契约 / e2e）仍未做。
- r4：把 r3 的工作区改动提交到本地分支（**未 push**），并补三件事：① 阶段 0 的 0.1 / 0.2 / 0.4——Linux CI workflow、基线执行器与清单、基线报告 `docs/baselines/tui.md`（0.4 勾选；0.1 / 0.2 因为 workflow 没在 Linux 上跑过而不勾选）；② 清掉 leader 的 UI 状态残留（A.2 的尾巴），生产行为不变，测试夹具改用生产默认值；③ 1.R3 的命名与提示语（行为不变，`stderr` 去向、优雅退出、残留进程实测仍未做）。同时把 §10.4 的 leader 残留改写为四类「不是 UI 残留」的剩余（273 行 / 80 个文件），新增 §10.6，并更正测试基线（清理 leader 时随被删代码删掉 18 个测试，10,857 → 10,839）。验证仍是 macOS 一台机器：门禁、`--workspace --tests`、8 个 suite、PTY 烟测；没有 Linux / Windows 结果。
- r5：推送分支、开 draft PR [#7](https://github.com/zy1233/pi-python/pull/7)，并完成阶段 A 与阶段 0 的 0.1 / 0.6。① 第一次 Linux `TUI CI` 全绿后把基线转为阻塞，依赖图上限 995 → 980、告警上限 20 → 0、各 suite 设 `min_passed`；② 入口审计（0.6）按附录 A 的默认执行，A.3 删除 dashboard、白名单外的斜杠命令、agents / extensions / persona 模态、tasks / 后台 / 定时任务、subagents / workflows / goals、共享 prompt 队列、MCP / hooks / plugins / marketplace 入口、rewind / fork / jump、recap / feedback / consent、changelog、`--chat` 世界、session rename 的死链路；③ A.4 去掉各 crate 根的 `#![allow]`（消费者构建 0 告警）、`pi-shell` 的 `pub mod` 降级、删未用依赖与 8 个无人依赖的 crate、删 `cfg(feature = "local-workspace")` 代码；④ `-p` 无沙箱时拒绝运行（ADR7 的最小版本）。净效果：`tui/crates` 的 `.rs` 从 1,252,374 行降到 1,019,041 行，`Cargo.lock` 1,285 → 1,257 个包，Linux 依赖图 995 → 980。新增 §10.7 与附录 A 的 r5 结果。⑤ 收尾：PTY 烟测发现并删掉欢迎页与选择器里的死 worktree 入口（`ac1d3ff`）；手动 `release_baseline` 回填了 release 数（`zypi` 422,034,120 B，−10.3 %；冷缓存构建 1,155 s，−29.9 %；0.2 勾选）；阶段 1 开了头——`session/list` 的标题与 `updated_at`（1.P1 一半）、stdio 契约套件并修了 `session/close` / `resume` 的路由（0.3 / 1.P7 的 Python 一侧、1.P5 的一个 bug）、优雅退出（1.P2 完成，1.R3 ② 完成：退出 zypi 时 bash 工具进程不再残留——双击 Ctrl+Q、`kill -9 zypi`、SIGTERM、关终端都量过，前因后果见 §10.7）。验证：Linux CI（`0e71894`、`cca48b0`、`ac1d3ff`）、macOS 本机、PTY。阶段 1 的其余部分和 0.5 / 0.7–0.9 未做，见 §10.7 的遗留。
- r6：清理品牌残留（用户选了「清理 Grok 品牌残留」，没给新名字，沿用 zypi），分支 `codex/branding-cleanup`，叠在 PR #7 之上；PR #7 同时转为 ready for review。改动：主题改名为 `zypi Night` / `zypi Day`（落盘 id `zypinight` / `zypiday`，旧 id 与别名仍可解析）、桌面通知标题、`zypi doctor` 的标题与全部提示、启动 / SSH 提示、设置页描述、复制提示、`--minimal` 欢迎页与信任提示、`zypi wrap` 报错前缀。顺带修了三处「提示指向不存在的东西」：`zypi doctor fix ssh-wrap` 写进 shell rc 的是 `alias ssh='grok wrap ssh'`（`grok` 不存在）；配置目录在消息里叫 `~/.grok` / `$GROK_HOME`（实际是 `~/.pi-python` / `$PI_HOME`）；`/doctor`、`/minimal`、`/copy`、`grok worktree gc|rm|db rebuild` 都不存在。有意没动的见 §10.8。验证见 §10.8。
- r7：合并 `main` 上另一台机器推的 7 个提交（Phase 6 / 7 审计的实现，没有一个文件在 `tui/` 下）。6 个文件、17 处冲突，都在 `packages/pi-agent-cli` 与 `pyproject.toml`；接缝处的三个决定：`api_key_getter(env_name, provider)`（key 只给自己的 provider，`/model` 里的每个模型都适用）、`model_for_choice(choice, reasoning=…)`（切换模型不丢 `Model.reasoning`）、`_bind_session` 先决定信任再恢复模型选择。纯 `origin/main` 上就失败的 6 个 macOS 专属测试里，一个是真 bug（macOS 上项目的 `AGENTS.md` 会被读两遍），已按文件身份去重修掉；其余 5 个没有动。见 §10.9。
- r8：在 WSL2（Ubuntu 24.04，内核 5.10，没有 Landlock）上补 Linux 一侧的实测，拆成 8 个提交（分支 `codex/linux-wsl-r8`，走 PR 合入）。做了：退出矩阵（会话首进程 / shell 作业 × 双击 Ctrl+Q、`kill -9`、SIGTERM、SIGHUP、关终端、Ctrl-C，含沙箱下；空闲、CPU 占满、cargo 构建三种负载；1.R3 ③ 的 Linux 部分）、0.9 的六项、PTY 烟测 18/18、`doctor_cmd::` 去掉 `skip`（12 个通过；8 个 suite 7,456 通过，等于 CI 的 7,444 加这 12 个）、Python 全量 pytest（第一轮 Linux 2,135 通过；六件事做完之后 Linux 2,160、Windows 2,156）、74 个未选入 suite 的 crate 的分诊。修了三处：沙箱的 bwrap re-exec 缺 `--die-with-parent`（只杀 bwrap 会把 zypi、agent 与工具整棵树留下）；`media` 用例对 10 ms 时钟刻度的时间假设（`CONFIG_HZ=100` 上 30 次失败 26 次）；`pi-pager-minimal` 的一个用例缺 `test_lock()`（默认线程数下 30 次失败 25 次）。更正了 1.R3 ① 的前提：agent 的 `stderr` 在 TUI 里是 `/dev/null`，不是继承终端。第一轮留下了几处（`-p` 孤儿、沙箱静默降级、`stderr` 去向、`zypi export`），用户随后选了前三处，加上 CI 清单、PTY 脚本入库、清理 WSL，都做了：`-p` 只杀 zypi 时 agent 与工具不再残留（`PI_AGENT_PARENT_PID` + Python 一侧监视父进程，`-p` 也处理 SIGTERM / SIGHUP；`print_exit.py` 4/4）；Landlock 缺失时沙箱不再静默降级（启动时 `stderr` 一行警告，欢迎页与状态栏标 `sandbox:<profile> (not enforced)`）；agent 的 `stderr` 落到 `<home>/logs/agent.stderr.log`（0600、追加、超过 1 MiB 轮转）；CI 清单去掉 `doctor_cmd::` 的 `skip` 并加 `pi-pager-render`、`pi-pager-minimal`（10 个 suite，8,633 通过 / 1 个已知失败）；PTY 驱动脚本入库为 `scripts/tui_pty/`（`smoke.py` 23/23，沙箱下 26/26，退出矩阵 12/12 与 14/14）；WSL 里的克隆、构建产物与日志清掉了。没做：`zypi export` 读不到 Python 会话（用户选了不动）；Landlock 层、Windows 与 MCP 子进程未验证；清单的新内容要等第一次 CI 运行证实；PR #9 的第一次运行已证实：8,633 通过，两个新 suite 的数与 WSL2 一致，只有 `doctor_cmd::` 的一个用例在没有录音程序的 runner 上失败（已修，r8 写的「`doctor_cmd::` 不需要音频设备」不对）。见 §10.10 的「CI 结果」。
- r9：在 PR #9 之上补阶段 0 的收尾与阶段 1 里不需要拍板的部分，分支 `codex/phase1-contract-docs`（16 个提交，叠在 PR #9 的 `d84d98a` 之上，**没有推送**）。用户在「接下来处理什么」之后选了四件：`e2e`、`p0_gate`、`free_p1`、`ci_pr`；没有选 `decide`，所以每一项会改变行为的决定都只写成提议（§10.11 末尾「留给用户决定的」十二项），一项也没有动手。做了：① PR #9 的第二次 CI 运行（`d84d98a`）全绿，10 个 suite 合计 8,634 通过 / 0 失败 / 22 忽略，每个都等于清单里的 `reference_passed`；② 0.3 的 Rust 一侧——`python_agent_e2e.rs`（10 个用例，pager 的 ACP client 对着真的 Python agent），接进 `tui-ci.yml`；它第一次跑就抓出两个产品 bug（agent 起不来时的原因被吞成 `channel closed`；agent 一启动就退出时 `connect()` 挂住），修在 `affda9e`；③ 0.7——会话列表与 `session/load` 的延迟实测并修掉读盘的慢点（1,000 个会话、Windows：首页 2.0 s → 0.11 s，`session/load` 2.0 s → 0.12 s），pager 一侧 13 处读盘调用、8 个文件的清单，13 例 PTY 验收矩阵（9 PASS / 4 GAP），ADR1 的落地设计（流程、逐旗标、沙箱顺序、PR 切分、验收），覆盖条件没有触发；④ 0.8——附录 A 定稿（A.1 能力矩阵逐行标「已定」「提议」「未定」，A.2 ACP 版本配对与升级策略，A.3 逐方法的两端支持情况，A.4 是 r5 的执行结果），其间实测了权限模式（12 个场景）：Plan 不限制任何东西，Auto 在 Python 里等于 Always-Approve，设置里的 Default 被 Python 丢掉，`--permission-mode` 的六个值里只有 `bypassPermissions` 有效，`--system-prompt-override` / `--rules` / `--allow` / `--deny` 到不了 Python；⑤ 阶段 1 的免决策项——1.P1（pager 跟 `nextCursor`、Python 分页）、1.P4（pager 不再发 `mcp_servers`，Python 说明被忽略的）、1.P7 的余项（权限往返与 cancel 的线上用例）。验证：Python 全量 pytest（Linux 2,352 通过、Windows 2,351 通过）、10 个 suite 整套 8,659 通过 / 0 失败 / 22 忽略（WSL2，其中 `pi-pager` 5,602）、Ruff。没做：所有改变行为的决定；本分支的 CI 运行（清单里 `pi-pager` 的 5,602 与新的 e2e 安装步骤要等第一次运行证实）；Windows 与 macOS 上的 e2e 与 PTY 脚本。见 §10.11。
