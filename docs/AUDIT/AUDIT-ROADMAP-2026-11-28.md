# AUDIT：zycode 重构 Roadmap 与 App 架构设计复审（2026-11-28）

> **审计日期**：2026-11-28。
>
> **审计对象**：[`docs/specs/2026-11-25-zycode-refactor-and-roadmap.md`](../specs/2026-11-25-zycode-refactor-and-roadmap.md)，`main` @ `0ae5ddc`（633 行）。本文的"第 N 行"都指这一版。上一次审计 [`AUDIT-ROADMAP-2026-11-27.md`](AUDIT-ROADMAP-2026-11-27.md)（下称"11-27 审计"）用的是 607 行的旧版，所以两份文件的行号不同。
>
> **与 11-27 审计的关系**：
> 1. 11-27 审计之后，规划只加了 §2.5.1（改名的执行记录，第 250–274 行）。§3 到 §7（架构、`pi-rust`、Roadmap、风险、决策）没有改。RM-1 至 RM-8 的状态见 §四。
> 2. 本文的新问题从 RM-9 开始编号，接着 11-27 审计的编号。11-27 审计已写的内容，本文不重复。
>
> **方法**：
> 1. 通读规划的 §3–§7。能对照代码、发布产物或仓库里已有决定的陈述，逐条核对。
> 2. 对照三份已有的决定：[Rust Agent Runtime 剥离计划](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md)（下称"剥离计划"）的原则 P1–P7 与 ADR1–ADR7；[`docs/DESIGN.md`](../DESIGN.md) §8；[ACP 扩展登记](../specs/2026-11-05-acp-extension-registry.md)（下称"登记"）的五条准入规则。
> 3. 读 `v1.2.0` 的发布产物。只读字节：ELF 节头用 HTTP range 请求读，PyInstaller 归档读目录。不运行下载的程序（见 §七 第 1 条）。
> 4. 在本机（云端 Linux VM，4 vCPU）用 `packaging/agent/constraints.txt` 装源码版 agent，量启动时间与内存；写一个两个写者同写一个会话文件的复现脚本（§八）。
>
> **级别**（与 11-27 审计相同）：高 = 设计达不到它自己写的验收门槛，或会重新打开一条已经关闭的安全边界；中 = 会造成难以排查的错误行为或返工；低 = 文档不准确，不影响行为。
>
> **状态图例**：`[ ]` 未处理；`[~]` 部分处理；`[x]` 已处理。本文不改规划，也不改代码。

---

## 一、结论

1. 规划的方向可以成立：先做 daemon 与桌面端，底层跑现有的 Python agent；`pi-rust` 用实测决定去留。但 Phase 1 开工之前，有三件事要先定。不定的话，会重新打开已经关闭的安全边界：
   - **RM-9**：Phase 3 让 TUI 在进程内直连 `pi-rust`，不经 ACP。这推翻了剥离计划的 P1、P4、P5 与 ADR1。规划没有提这些决定，也没有给 ADR1 要求的推翻依据。
   - **RM-10**：daemon 的"Worker Pool"没有写隔离单位。现有 agent 有进程级状态：`pi/yolo_mode_changed` 一次改掉进程里所有会话的权限模式；受信任项目的扩展代码进入同一个 Python 进程。多个客户端或多个项目的会话放进同一个 agent 进程，就会互相影响。
   - **RM-11**：项目信任的问题走的是 `session/request_permission`。规划的跨端审批仲裁与手机推送审批，没有把它和工具审批分开。
2. 有两项工作现在就能做，而且会改变 Phase 2 门禁的结论：
   - **RM-15**：原生 Windows `zypi` 的编译阻塞只是一处 `/dev/stdout`（`pi-proto-build`，只用来打印 `cargo:rerun-if-changed`）。规划把它绑到 Phase 3 的 13 万行替换上，README 与网站因此把它从"计划中"降成了"提案"。
   - **RM-16**：§0.1 的"体积大、冷启动慢"多半来自打包设置。Linux `zypi` 的 362 MB 里有 313 MB（86.5%）是调试信息。Linux agent 每次启动解压 183 MB，其中 `libpython` 带调试信息，并且装了两份。不先修这些，Phase 2 的对比量到的是打包，不是引擎。
3. 11-27 审计的 RM-1 至 RM-8 都还开着，RM-8 部分处理。

---

## 二、问题总览

### 2.1 本次的新问题

| ID | 级别 | 位置（规划的第 N 行） | 问题摘要 | 状态 |
|----|------|------|----------|------|
| RM-9 | 高 | 10、35、49、330、359、434、449、473、590 | 进程内直连推翻了 P1、P4、P5、ADR1、ADR2 与 D.4，规划没有声明，也没有给推翻依据 | `[ ]` |
| RM-10 | 高 | 296、323、345、559 | Worker Pool 没有隔离单位；agent 的进程级状态（X8、`os.environ`、`sys.modules`）会跨会话生效 | `[ ]` |
| RM-11 | 高 | 344、381、603 | 项目信任问题经 `session/request_permission` 发出，会被审批仲裁与推送当成普通审批 | `[ ]` |
| RM-12 | 中 | 331、347–348、473–475、564 | 会话文件没有单一写者；两个写者同写一个会话，树悄悄分叉（已复现） | `[ ]` |
| RM-13 | 中 | 286、343、559 | 每任务一个 worktree：信任记录与会话列表都按精确路径；现有 worktree 建在临时目录 | `[ ]` |
| RM-14 | 中 | 23、360、368、558、564 | `vscode/src/core/` 不是"零宿主依赖"：23 个模块里 6 个用 Node 内置模块，包括 2,132 行的 `chat-controller.ts`；CI 的路径过滤会漏测共享核心 | `[ ]` |
| RM-15 | 中 | 478、592；README；`web/src/i18n/*.ts` | Windows `zypi` 被绑到 Phase 3，实际阻塞是一处 build script | `[ ]` |
| RM-16 | 中 | 21–22、397–404、578 | Phase 2 的对照组是未优化的打包 | `[ ]` |
| RM-17 | 中 | 339–346、412、416、439 | `zycode/*` 控制面在登记之外；X9、X10 会有第二个名字与第二份 Rust 类型，违反准入规则 4 | `[ ]` |
| RM-18 | 中 | 290、296、559–560 | daemon 拉起的 agent 用什么环境（`PATH`、密钥），规划没有写 | `[ ]` |
| RM-19 | 中 | 308、351、379–381、559、602 | 本机与远程的网络面没有威胁模型：本机 WebSocket、设备撤销与权限范围、推送元数据、托管中继 | `[ ]` |
| RM-20 | 低 | 361–362、561、631 | 桌面端体积与内存的数字只对 `pi-rust` 成立；Phase 1 要带 Python agent；Windows arm64 没有原生 agent | `[ ]` |
| RM-21 | 低 | 3、5、22、38–43、508–520 | 规划内部不一致：版本、状态、两张依赖图、工期 | `[ ]` |

### 2.2 11-27 审计的问题（复核）

| ID | 级别 | 状态 | 当前位置（第 N 行） | 本次补充 |
|----|------|------|------|------|
| RM-1 | 高 | `[ ]` | 316、341、559、605 | 快照要从唯一的写者取（RM-12） |
| RM-2 | 高 | `[ ]` | 296、371、474、603 | worker 的环境属于同一个生命周期设计（RM-18） |
| RM-3 | 高 | `[ ]` | 286、343、350、559 | worktree 的信任身份（RM-13） |
| RM-4 | 中 | `[ ]` | 445、545、574–575、581、632 | 现有的线协议契约测试可以作为起点（§六 第 5 条） |
| RM-5 | 中 | `[ ]` | 397–408、578–582、594 | 对照组本身要先优化（RM-16） |
| RM-6 | 中 | `[ ]` | 469、473 | 进程内后端本身的去留见 RM-9 |
| RM-7 | 中 | `[ ]` | 231、319、544、629 | README 把它列为 Phase 0 的"Planned"，规则仍未写 |
| RM-8 | 低 | `[~]` | 18–24、154 | 第 265 行（§2.5.1）补了 Open VSX 拒签的真实原因（`workflow_ref`）；第 154 行原文没改；§0.1 的数字与计数方法没改；第 3 点随 C1 的修复成立 |

---

## 三、新问题详情

### RM-9. `[ ]` 进程内直连 `pi-rust` 推翻了已记录的架构决定，规划没有说明

- **位置**：目标 3（第 10 行）；§0.2（第 35、49 行）；§3.2 图（第 311、324、330 行）；§3.3.1（第 359 行，"Phase 2 可直接进程内嵌 `pi-rust`"）；§4.2 图（第 434 行）；§4.3（第 449、473 行）；Phase 3 交付 2（第 590 行）。
- **已有的决定**（剥离计划 §2.2、§6、§7）：
  1. **P1**（第 231 行）："任何 runtime（包括将来的 pi-rust）都必须作为独立的 ACP agent 位于 ACP 之后，不得回到 TUI 进程内——ACP 是唯一接缝。"
  2. **P4**（第 234 行）："Rust 不读写 session 文件（JSONL v3 是唯一真源）；session 列表、恢复、标题一律走 ACP。"
  3. **P5**（第 235 行）："工具权限决策走 ACP `session/request_permission`；LLM 凭据只存在于 Python 环境。"
  4. **ADR1**（第 283 行）：会话数据"纯 ACP"。推翻条件是"阶段 0 基线实测冷启动列表延迟不可接受且无法预热缓解"。
  5. **ADR2**（第 284 行）：放弃 leader；"将来要多会话共享进程另立设计"。
  6. **D.4**（第 371 行，用户 2026-11-03 定）："`pi-agent` 留给将来的 pi-rust"。
- **问题**：
  1. `InProcessPiRustBackend`"进程内持有 `Arc<AgentHarness>`"，提供"0 延迟会话检索"（第 473 行）。这与 P1、P4 正相反。规划没有提 P1–P7、ADR1、ADR2。它把"冷启动、序列化"列为痛点（第 22 行），给了"<30ms 冷启动"的目标（第 590 行），但没有 ACP 开销的实测，也就没有 ADR1 要求的推翻依据。
  2. **P5 的两条安全边界会被打开。** P5 写"LLM 凭据只存在于 Python 环境"。`pi-rust` 作为独立的 ACP agent 时，这一条可以改成"只在 agent 进程里"。进程内直连时，凭据进入 TUI 进程，权限决定也不再经过 `session/request_permission`，要由 TUI 进程自己做。`tui/` 里还有 `pi-crash-handler`、`pi-diag-server`、`pi-telemetry`、`pi-mixpanel` 等 crate（本次只核对了目录存在，没有核对它们在 `zypi` 里是否启用）。持有凭据的进程变成整个 TUI 进程，这些 crate 都要重新审。
  3. **失去崩溃隔离。** `tui/Cargo.toml` 的 `[profile.release]` 是 `panic = "abort"`（第 329 行），发布用的 `release-dist` 继承它。进程内的 agent 代码（工具、LLM 流解析、MCP 客户端）只要 panic 一次，整个 TUI 就结束，用户的输入草稿也没了。现在 agent 崩溃时，pager 还活着，并报告 agent 已退出。
  4. **与 D.4 相反。** 规划要求 `crates/`"零 `grok-build` 历史依赖"（第 412 行），并在批次 1 里替换 `pi-agent`（第 477 行）。D.4 留下 `pi-agent`（22,877 行）的理由就不成立了。规划没有说 D.4 改成什么。
  5. **一致性测试测不到进程内路径。** 仓库已有面向线协议、可以换被测程序的契约测试：`packages/pi-agent-cli/tests/test_acp_stdio_contract.py` 第 7–10 行写明，可以用 `PI_ACP_CONTRACT_COMMAND` 对"将来的 `pi-rust`"运行同一组断言；`agent-binaries.yml` 已用它测冻结的二进制。进程内后端不经 ACP，这组测试和 `python_agent_e2e.rs` 都测不到 TUI 实际走的路径。TUI 还要同时维护两条路径（`InProcessPiRustBackend` 与 `ExternalAcpBackend`），测试矩阵翻倍。
- **建议**：
  1. 在 §7 加一项决策（§五 的 D6）：Phase 3 是否保留 ACP 作为唯一接缝。推荐保留：`pi-rust` 做成独立的 ACP agent，TUI 不变，冷启动的目标由 `pi-rust` 自己达到。
  2. 如果仍要进程内直连，规划要：(a) 写明修订 P1、P4、P5、ADR1、ADR2 与 D.4，并给出 ADR1 要求的实测；(b) 重写 P5 的两条边界：凭据在哪个进程，权限由谁决定；(c) 写崩溃隔离的方案，例如改用 `panic = "unwind"` 并在边界上 `catch_unwind`，或者把 agent 放回子进程；(d) 让一致性测试也能驱动进程内后端。
  3. §3.3.1 的"Phase 2 可直接进程内嵌 `pi-rust`"（桌面端）适用同一个决定。

### RM-10. `[ ]` daemon 的 Worker Pool 没有隔离单位；现有 agent 有进程级状态

- **位置**：§3.2（第 296 行"可插拔双引擎"；第 323 行"经内部 ACP stdio 池驱动"；第 345 行 `zycode/config/*` 读写"权限模式"）；Phase 1 交付 2（第 559 行"Worker Pool（管理 `zycode-agent` 进程）"）。
- **现状**：一个 `zycode-agent` 进程可以开多个会话（`PiAcpAgent._harnesses`，`agent.py` 第 254 行）。规划没写一个 agent 进程里放哪些会话。现有 agent 有下面这些进程级状态：
  1. **`pi/yolo_mode_changed`（登记 X8）改掉进程里所有会话的模式。** `ext_notification`（`agent.py` 第 971–982 行）把每个打开的会话设成通知里的模式，并用 `self._config = replace(self._config, permission=mode)` 改掉之后新会话的起始模式。登记写明："The notification has no session id… This notification moves all sessions."
  2. **`always-approve` 没有按会话切换的途径。** `session/set_mode` 与 `mode` 配置项只接受 `ask` 和 `auto`（决策 D6，`packages/pi-agent-cli/AGENTS.md` 第 122 行）。会话开着时进入 `always-approve`，只有 X8 一条路。VS Code 插件也是这样做的（`AcpConnection.notifyPermissionMode`）。
  3. **项目扩展的代码进同一个 Python 进程。** 受信任项目的扩展被加载进 `sys.modules`（`pi_agent_core/extensions/loader.py` 第 425–442 行）。同一进程里别的项目的会话与它共享地址空间。
  4. **环境变量是进程级的。** `local.env` 写进 `os.environ`（`pi_agent_cli/config.py` 第 74–78 行）。`--trust-project-extensions` 设 `PI_TRUST_PROJECT_EXTENSIONS=1`，对进程里的所有项目生效（`__main__.py` 第 265–268 行）。
- **问题**：
  1. 如果池子按会话复用进程，只要有一个客户端发出 X8（VS Code 切到 `always-approve`，或 TUI 按 Shift+Tab），同一进程里属于别的客户端、别的项目的会话会悄悄进入 `always-approve`。VS Code 插件对 `always-approve` 的限制（先弹警告；新对话、过去的对话与重启都不恢复）对那些会话不起作用。
  2. 项目信任（Invariant 9）失去进程边界：一个项目被信任后，它的扩展代码与别的项目的会话在同一个进程里运行。
  3. 规划还让 `zycode/config/*` 结构化读写"权限模式"（第 345 行）。如果写到 `agent.toml` 或进程的起始模式，就是 X8 的第二个副作用（改掉之后所有会话的起始模式）。
- **建议**：
  1. 在 §3.2 写明隔离单位。最低限度：一个进程只放同一个项目（信任域）的会话；daemon 不转发 X8，只在 `session/new` 用 X7（`_meta.yoloMode`）设起始模式；会话中途要进入 `always-approve` 时，给它换一个新进程，或者先给 agent 加一个按会话生效的扩展（过准入规则）。
  2. 把内存算进 Phase 1。本机源码版 agent 空闲时 RSS 约 74 MiB（§八 8.3）。10 个并发会话、每会话一个进程，约 0.7 GiB，还不算会话的增长。
  3. 在 `tests/conformance/` 加用例：同一个 daemon 里两个会话，一个切到 `always-approve`，另一个的模式不变。

### RM-11. `[ ]` 项目信任问题走 `session/request_permission`，会被当成普通审批

- **位置**：§3.2 `zycode/approval/respond`（第 344 行"任一端响应即原子消费"）；§3.3.2 离线审批推送（第 381 行）；Phase 4 交付 2（第 603 行"推送唤醒审批（含生物识别确认）"）。
- **现状**（`packages/pi-agent-cli/pi_agent_cli/trust_prompt.py` 第 1–23 行）：信任问题以 ACP `session/request_permission` 发出，挂在一个合成的工具调用上，因为这是"每个客户端都有的唯一交互通道"。为了不被"自动批准工具调用"的模式顺手答掉，它做了三件事：没有 `allow_once` 选项；拒绝排在第一；只有确切的信任选项 id 算"是"。问题的说明不在请求里，而在请求之前的一条普通 agent 消息里。这是审计 P7-02 关上的边界。
- **问题**：
  1. 规划的审批仲裁不区分这种请求。daemon 会把每个 `session/request_permission` 当成"审批"，推给桌面与手机，"任一端响应即原子消费"。
  2. 推送只带"最小脱敏提示"（第 381 行）。手机上通用的"批准"按钮，很可能映射到第一个非拒绝选项，也就是"信任并记住"。说明在另一条消息里，审批卡片不一定带上它。
  3. 信任一个项目，会在桌面上运行该项目的 Python 扩展。规划没写手机能不能回答这种问题。
- **建议**：
  1. 在 `zycode/approval/*` 的规范里，把"项目信任"定为单独的请求类型（daemon 认 `trust-project` 选项 id，或者 agent 在 `_meta` 里标明），并带上说明的全文。
  2. 默认只允许本机客户端回答信任问题。手机只显示"有一个项目在等信任"，不能批准。
  3. 加一个一致性用例：手机端发出"批准"时，信任问题不被接受。

### RM-12. `[ ]` 会话文件没有单一写者：两个写者同写一个会话，树会悄悄分叉

- **位置**：§3.2 原则 2"会话真源不变"（第 347–348 行）；图中编辑器经"标准 ACP stdio"连到 daemon（第 331 行）；§4.3 的三种后端（第 473–475 行）；Phase 1 门禁（第 564 行）。
- **现状**：
  1. `JsonlSessionStorage` 打开文件时把整棵树读进内存，之后只追加（`pi_agent_harness/session/jsonl_storage.py` 第 290–294 行）。新条目的 `parentId` 取自本进程内存里的叶子。条目 id 只与本进程内存里的 id 比较（`memory_storage.py` 第 34–41 行）。
  2. 没有跨进程的锁。`DeferredJsonlSessionStorage` 的锁是 `asyncio.Lock`（`jsonl_storage.py` 第 351–353 行），只管一个进程。`PiAcpAgent._reserved_ids` 也只在一个进程里。
- **复现**（§八 8.4）：两个存储对象打开同一个会话文件，各追加一条消息。结果：两条消息的父节点相同，树分叉了；一方看不到另一方的条目；重新加载后，上下文只沿最后写入的那一支走，另一方的消息不在上下文里。整个过程没有报错。
- **问题**：
  1. 规划让多个进程共用会话：daemon 的 worker、TUI 的 `InProcessPiRustBackend` 与 `ExternalAcpBackend`（第 473、475 行），都直接打开 `sessions/` 里的文件。
  2. 规划没写 VS Code 插件在 Phase 1 之后是继续自己启动 agent，还是经过 daemon。图（第 331 行）画的是经过 daemon，但缺一个组件：stdio 是编辑器启动一个子进程，daemon 是常驻进程，两者之间要有一个转接程序。规划的组件图与 crate 表里都没有它。没有它，编辑器就还是各自启动 agent，回到第 1 点。
  3. 今天就可能出现这种情况（例如两个编辑器窗口打开同一个过去的对话，或者在终端里 `zypi --resume` 一个 VS Code 正开着的会话）。本次没有在真实客户端里复现。
- **建议**：
  1. 在 §3.2 原则 2 里加"单一写者"：一个会话同一时间只有一个进程能写。用锁文件或租约（带进程 id 与心跳）实现；第二个进程只读打开，或者经过 daemon 连接。
  2. 写明编辑器怎么连到 daemon（例如一个 stdio 转接程序），或者明确写"编辑器不经过 daemon"，再按第 1 条处理冲突。
  3. 把 §八 8.4 的复现改成测试，放进 `tests/conformance/`。

### RM-13. `[ ]` 每任务一个 worktree：信任与会话列表都按精确路径；现有 worktree 建在临时目录

- **位置**：§3.1"每个任务可绑定独立 Git Worktree"（第 286 行）；`zycode/workspace/*`（第 343 行）；Phase 1 交付 2（第 559 行）。
- **现状**：
  1. 信任记录"只管它保存时的那一个目录"，子目录也要单独决定（`pi_agent_cli/trust_store.py` 第 17–20 行）。键是规范化后的真实路径（`project_key`，第 55–61 行），值里有该目录资源的指纹。
  2. 会话列表按 `cwd` 精确比较（`pi_agent_harness/session/jsonl_repo.py` 第 147、205 行）。VS Code 的过去对话视图按文件夹列出。
  3. 现有的 `WorktreeManager.create` 把 worktree 建在系统临时目录（`pi_dynamic_workflows/worktree.py` 第 86 行：`tempfile.gettempdir()` 下的 `pi-wt-<tag>`）。这适合 `workflow` 子代理这种短任务。
- **问题**：daemon 给每个任务建的 worktree 是一个新路径。所以：
  1. 每个任务都是一个没有信任记录的项目。要么每个任务都问一次信任；要么任务会话里悄悄没有项目的扩展、skills、`.pi/SYSTEM.md` 与 `<git_status>`（Invariant 9），表现与主目录不同。直接把主目录的信任复制给 worktree，又会绕过指纹：worktree 可能检出另一个分支，文件不同。
  2. 任务会话不出现在主目录的过去对话列表里。worktree 删掉以后，这些会话的 `cwd` 指向一个不存在的目录。
  3. 如果沿用临时目录，桌面端的长任务（可能几天）会碰上临时目录的清理（重启或定期清理，取决于系统）。未提交的改动会丢。
- **建议**：
  1. 写明 worktree 的位置（持久目录，例如 `<home>/worktrees/<repo>/<task>`）与清理规则。
  2. 写明 worktree 的信任规则：按 worktree 自己的指纹判定。指纹与主目录的记录相同时，可以沿用主目录的决定（文件相同）；不同时要问。
  3. 会话元数据里记下"所属仓库"（主目录路径），让列表能按仓库归组；写明 worktree 删除之后，它的会话怎么处理。

### RM-14. `[ ]` `vscode/src/core/` 不是"零宿主依赖"；抽成共享包还缺 CI 的路径

- **位置**：§0.1（第 23 行"可直接抽取为跨端共享 TS Client Core"）；§3.3.1（第 360 行"直接复用 23 个纯 TS 核心模块"，第 368 行"零宿主依赖"）；Phase 1 交付 1（第 558 行）与门禁（第 564 行）。
- **现状**：23 个模块都不导入 `vscode`（11-27 审计核对过，本次又核对一次）。但其中 6 个导入 Node 的内置模块：

  | 模块 | 导入 |
  |------|------|
  | `agent-process.ts` | `node:child_process`、`node:stream` |
  | `chat-controller.ts`（2,132 行） | `node:fs`；第 587 行用 `AgentProcess.start(...)` 启动 agent 子进程 |
  | `diff-tracker.ts` | `node:path`；diff 靠在本地读文件 |
  | `first-run.ts` | `node:os`、`node:path` |
  | `locate.ts` | `node:fs/promises`、`node:path` |
  | `thinking.ts` | `node:fs`、`node:path`；旧 agent 时改写 `agent.toml` |

- **问题**：
  1. Tauri 的 WebView 与 React Native 都没有 Node 运行时。核心状态机 `chat-controller.ts` 直接启动子进程、读本地文件，它的连接模型是"一个对话启动一个 agent"，正是 daemon 要取代的模式。"直接抽取"不成立：要先把传输（stdio 或 daemon）、文件访问与进程生命周期从控制器里拆出去。这是对 2,132 行主状态机的重构，而 Phase 1 门禁要求 VS Code"0 回归"。
  2. diff：VS Code 现在在本地读文件算 diff。手机读不到开发机上的文件，diff 要由 daemon 提供（`zycode/workspace/*`），`diff-tracker.ts` 不能原样复用。
  3. CI：`vscode-extension.yml` 只在 PR 改了 `vscode/**` 或它自己时运行（第 17–20 行；另可手动运行）。核心搬到 `clients/core/` 以后，只改 `clients/core/` 的 PR 不会跑 VS Code 的测试。`main` 上没有必需的检查（`ci.yml` 开头的注释与 `AGENTS.md` 都这样写），所以这样的 PR 可以不经测试合入。反过来，`ci.yml` 的过滤是"除 `vscode/` 与 `docs/` 外的所有路径"（第 10–22 行），纯 TypeScript 的改动会触发 Python 测试。
- **建议**：
  1. 把第 368 行的"零宿主依赖"改成一条能检查的规则：`clients/core/` 不导入 `node:*` 与 `vscode`，用 lint 或测试守住；列出要拆的 6 个模块。
  2. 在 Phase 1 的交付里单列"拆分 `ChatController` 的传输与文件访问"，并估计工作量。
  3. 在搬迁的同一个 PR 里更新 `vscode-extension.yml` 与 `ci.yml` 的 `paths`，让 `clients/core/**` 触发 VS Code（以后还有桌面端）的检查。

### RM-15. `[ ]` 原生 Windows `zypi` 被绑到 Phase 3，实际阻塞是一处 build script

- **位置**：§4.3 批次 2（第 478 行"同步解决 `/dev/stdout` 阻塞，打通原生 Windows `zypi` 编译"）；Phase 3 交付 4（第 592 行）；README 的 Roadmap 表（"Phase 3 … It starts only if Phase 2 says yes"）；`web/src/i18n/en.ts` 与 `zh.ts` 的 `next`。
- **现状**：
  1. 阻塞点：`tui/crates/codegen/pi-tools-api/build.rs` 调 `pi_proto_build::configure()`；`emit_rerun_if_changed`（`tui/crates/build/pi-proto-build/src/lib.rs` 第 129 行起）让 `protoc` 把依赖清单写到 `/dev/stdout`（第 148–149 行）。剥离计划执行记录 §10.50（r47）写明：这个函数"只用来打印 `cargo:rerun-if-changed` 的行（一个优化）"；"补一处之后，非测试代码编得过"。余下的是：两处测试代码编不过；跑过的单元测试里有 37 个在 Windows 上失败，审计前后逐个相同（当时只跑了一部分）。
  2. 规划把这件事放进批次 2，即替换 13 万行的 `pi-tools` 与 `pi-tools-api`。批次在 Phase 2 门禁通过之后才做（第 476 行），而 Phase 3 只在门禁选 Rust 时才开始（README）。
  3. 规划之前，README 与网站把 Windows `zypi` 写成"计划中，没有日期"。提交 `0ae5ddc` 把网站的说法改成了"只是提案"。
- **问题**：一个工作量有限、用户看得见的交付，被绑到一个不确定、要几个月的门禁上。门禁如果选路线 B，Windows 用户就一直没有 `zypi`。
- **建议**：把 Windows `zypi` 从 Phase 3 拿出来，作为独立的一步，可以放进 Phase 0 的"计划中"：给 `emit_rerun_if_changed` 加 Windows 分支（或在 Windows 上跳过它）；修两处测试代码；逐个处理 37 个失败；加 Windows 的 CI 与发布构建。§4.3 批次 2 改为"不再是 Windows 的前提"。

### RM-16. `[ ]` Phase 2 门禁的对照组是未优化的打包

- **位置**：§0.1（第 21 行：`zycode-agent`"体积大、冷启动慢"；第 22 行：`zypi`"约 407 MB"）；§4.1 对比表（第 399–400 行"启动与响应延迟""资源与体积"）；Phase 2 M2.5（第 578 行）。
- **事实**（v1.2.0 的发布产物，只读字节；§八 8.1、8.2、8.3）：
  1. `zypi-linux-x86_64` 是 362,130,992 B，其中 `.debug_*`、`.symtab`、`.strtab` 共 313,069,024 B（86.5%）。`zypi-macos-arm64` 是 45,678,232 B。原因：`release-dist` 配置是 `strip = false`、`debug = 1`（`tui/Cargo.toml` 第 340–346 行）。它的注释说 CI 应在构建后提取 `.debug` 再 strip，但 `release.yml` 直接复制二进制（第 106–111 行），没有这一步。
  2. `zycode-agent-linux-x86_64`（压缩包 77,027,814 B）是 PyInstaller one-file。每次启动，它把 295 个条目、共 182,634,147 B 解压到临时目录。`libpython3.12.so.1.0` 解压后 30,084,752 B，其中 23,112,233 B（76.8%）是调试信息与符号表；它装了两份，主程序一份，冻结的沙箱子进程（`frozen-child/_internal/`）一份。`zstandard` 的两个扩展模块各约 12 MB，91% 以上是调试信息。`packaging/agent/zycode-agent.spec` 第 65 行是 `strip=False`。
  3. 源码版 agent（本机，`constraints.txt` 的版本）：`initialize` 用 0.84–1.07 s，空闲 RSS 约 74 MiB。启动时导入的自身耗时里，`acp` 占 0.34 s（36%），`langsmith` 占 0.16 s（18%）；`langchain_openai` 与 `openai` 不在启动时导入。
- **问题**：
  1. §0.1 把体积与冷启动列为 Python 栈的痛点，§4.1 用它们比较两个引擎。上面的数字说明，相当一部分来自打包设置：不 strip、库装两份、one-file 每次解压。这些与语言无关。用现在的打包做对照组，结论会偏向 `pi-rust`，而这些差距改起来很便宜。
  2. 在 daemon 架构里（Phase 1），worker 常驻，冷启动只影响直接启动 agent 的客户端（VS Code、Zed、单独运行的 `zypi`）。§4.1 没有按场景给冷启动定权重。
- **建议**：
  1. 先做低成本的打包修正，再定门禁的基线：Linux 构建后 strip（或 `strip = "debuginfo"`，调试信息单独上传）；沙箱子进程复用主程序的 `_internal`；只收集用到的 `zstandard` 后端；VSIX 与桌面端用 one-dir（安装时解压一次）。每一项都要量体积与启动时间，并跑 `packaging/agent/smoke.py`。
  2. §4.1 的对照组写成"优化后的 `pi-python`"，并给出各场景（daemon 常驻、编辑器直接启动）的权重。与 RM-5 的阈值一起，在 Phase 2 开始前定下。

### RM-17. `[ ]` `zycode/*` 控制面在登记之外；X9、X10 会有第二个名字与第二份 Rust 类型

- **位置**：§3.2 原则 1（第 339–346 行）；"在 `crates/`（MIT 协议）中构建干净、零 `grok-build` 历史依赖的 `pi-rust` crate 集合"（第 412 行）；§4.2 的 `zycode-protocol`（第 416、439 行）。
- **现状**：
  1. 准入规则 4（[`DESIGN.md`](../DESIGN.md) §8.5；登记第 39 行）："wire 类型只定义一次，放在 `pi-acp-lib`（或同级的协议 crate），不留在 runtime crate 里。"X9、X10 的 Rust 类型在 `pi-acp-lib/src/extensions/steer.rs` 与 `btw.rs`。
  2. `pi-acp-lib` 在 `tui/`（Apache-2.0 的 grok-build 硬分叉）里，依赖 `pi-tools`、`pi-sampling-types`、`pi-chat-state` 与 `prod-mc-cli-chat-proxy-types`（`tui/crates/codegen/pi-acp-lib/Cargo.toml`）。
- **问题**：
  1. daemon 要对 Python agent 说 X9、X10，又要对桌面端说 `zycode/session/steer` 与 `zycode/session/btw`（"原样透传"，第 342 行）。`crates/` 不能依赖 `pi-acp-lib`（它带着旧支持层），所以 `zycode-protocol` 里会出现第二份 X9、X10 的类型，违反规则 4。同一个语义有两个名字和两份 schema，它们会各自演化。
  2. 规划没写 `zycode/*` 是 ACP 扩展（线上名 `_zycode/...`，要过五条规则并登记），还是另一套 JSON-RPC 协议（那就要有自己的登记处和版本规则）。
- **建议**：
  1. 先定协议类型的唯一位置：把 X1–X10 的类型搬进一个不依赖旧支持层的 crate（例如 `crates/zycode-protocol`），让 `pi-acp-lib` 反过来依赖它；同时改规则 4 的文字。
  2. `zycode/*` 只放 daemon 独有的能力（attach、workspace、approval、config）。steer 与 `/btw` 直接复用 X9、X10 的 schema，不另起名字。
  3. 给 `zycode/*` 建登记（可以是现有登记的新一节），带版本协商。

### RM-18. `[ ]` daemon 拉起的 agent 用什么环境，规划没有写

- **位置**：§3.1 配置与治理（第 290 行"`local.env` + 系统 Keychain"）；§3.2（第 296 行）；Phase 1 交付 2、3（第 559–560 行）。
- **现状**：VS Code 插件把 `process.env` 传给 agent（`vscode/src/core/agent-process.ts` 第 60 行），也就是 VS Code 自己的环境；终端里的 `zypi` 用终端的环境。`bash` 工具与 Auto 的上下文检查都读这个环境（Invariant 9d：`PATH` 里不能有相对路径，`pi_agent_cli/auto_mode.py` 第 153–162 行）。agent 从环境变量与 `local.env` 读密钥。
- **问题**：
  1. 从桌面图标启动的应用（以及它拉起的 daemon）拿到的是桌面会话的环境，常常不是用户 shell 的环境（例如 macOS 上 `PATH` 只有系统目录）。`bash` 工具会找不到用户装的 `node`、`cargo`、`uv`，而同一个会话在 VS Code 或终端里正常。这种差别很难排查。
  2. 规划写"系统 Keychain"保存密钥，但 Python agent 只从环境变量与 `local.env` 读。daemon 要在启动 worker 时把 Keychain 里的密钥放进环境，规划没写。
- **建议**：写明 daemon 怎么构造 worker 的环境：是否运行一次用户的登录 shell 来取环境（以及它对 Auto 检查的影响）；Keychain 的密钥怎么交给 worker；环境变了以后 worker 怎么重启。在 Phase 1 门禁里加一条："从桌面图标启动时，`bash` 工具看到的 `PATH` 与终端里相同。"

### RM-19. `[ ]` 本机与远程的网络面没有威胁模型

- **位置**：§3.2 传输层（第 308 行"Localhost WS（Token 鉴权）"）与原则 3（第 351 行）；§3.3.2（第 379–381 行）；Phase 1 交付 2（第 559 行"Local IPC + Token 鉴权 WS"）；Phase 4（第 602、606 行）。
- **问题**：
  1. **本机 WebSocket（Phase 1 就有）。** 浏览器里的任何网页都能尝试连接 `ws://127.0.0.1:<port>`，WebSocket 不受同源策略限制。令牌能挡住它，但规划没写：检查 `Origin`；检查 `Host`（防 DNS rebinding）；令牌不放进 URL（会进日志与浏览历史）；令牌文件的权限。桌面端本身也不需要本机 WS：Tauri 的 WebView 与 Rust 后端之间有自己的 IPC。需要网络监听的只有局域网直连。
  2. **配对的手机就是一把远程执行的钥匙。** 规划写了扫码配对（Ed25519，第 379 行），没写：撤销设备；设备的权限范围（只能审批工具调用，还是也能切到 `always-approve`、信任项目、改配置，见 RM-10、RM-11）；会话过期；手机丢失怎么办。
  3. **"零知识"与推送。** 推送的示例内容含仓库名（第 381 行），它要经过 Apple 与 Google 的服务器。中继本身看不到明文，但推送服务能看到元数据。规划应写明这一点。
  4. **托管服务。** 中继与推送网关要持有 APNs、FCM 的凭据，是项目要运营的服务。现在项目没有自己的服务器（`web/` 是 Cloudflare 的静态资源）。运营、费用、隐私说明与滥用处理都要另写。
- **建议**：Phase 1 之前写一页本机部分的威胁模型（本机 IPC 与 WS）；Phase 4 之前补远程部分（设备、权限范围、撤销、元数据、托管）。把"手机不能回答信任问题，不能切 `always-approve`"写进 Phase 4 的验收。

### RM-20. `[ ]` 桌面端体积、内存与平台覆盖的数字只对 `pi-rust` 成立

- **位置**：§3.3.1（第 361–362 行）；Phase 1 交付 4（第 561 行）；D3（第 631 行）。
- **问题**：
  1. "15–30 MB（直连 `pi-rust` 时）"。Phase 1 跑 Python agent，安装包要带上它：v1.2.0 的 agent 压缩包从 41.4 MB（darwin-aarch64）到 77.0 MB（linux-x86_64）。D3 的主要数字论据（Tauri 15–30 MB，Electron 150 MB 以上）只对 Phase 2 以后成立。
  2. "常驻内存 ~80–150 MB"没有算 agent worker（RM-10：每个约 74 MiB）。
  3. "Windows (`x64`/`arm64`)"：`tiktoken`（`langchain-openai` 的依赖）没有 `win_arm64` 的 wheel（2026-11-28 核对，0.14.0 只有 `win_amd64`）。所以 Phase 1 在 Windows arm64 上没有原生的 agent，只能用 x64 版加系统的模拟，或从源码编 `tiktoken`。
  4. 公证：macOS 公证要求包里每个可执行文件都签名（hardened runtime）。Phase 1 的包里至少有两个 PyInstaller 程序：agent 与冻结的沙箱子进程（`pi-sandbox-child`）。规划只写"规划 Apple Developer ID 公证"（第 561 行），风险表里也没有。
- **建议**：把 §3.3.1 的数字分成两列：Phase 1（带 Python agent）与 Phase 2 以后。D3 按 Phase 1 的数字重新比较。风险表加一行"PyInstaller 程序的签名与公证"。

### RM-21. `[ ]` 规划内部的不一致

1. 第 3 行写 Pre-Refactor"已在 PR #157 实现"。它已随 agent 1.2.0 与插件 1.14.0 发布（README 的 Roadmap 表）。
2. 第 5 行的基线是仓库 `zy1233/pi-python`、1.1.0、插件 1.12.0、`zypi` 1.1.0。现在是 `zy1233/zycode`、1.2.0、1.14.0、`zypi` 1.2.0。
3. 第 22 行"`zypi-linux-x86_64` 二进制约 407 MB"是 v0.6.0 的大小（406,911,320 B，剥离计划的 V0.6）。v1.2.0 是 362,130,992 B。
4. 两张依赖图不一致。§0.2（第 38–43 行）里，Phase 4 只依赖 Phase 1，Phase 3 只依赖门禁。§5.1（第 515–520 行）里，Phase 4 还依赖 Phase 2，Phase 3 还依赖 Phase 1，而且没有门禁。另外，Phase 2 的 M2.4 要接进 daemon（第 576 行），所以 Phase 2 也依赖 Phase 1，两张图都没画这条边。手机端要不要等 `pi-rust`，要定一种说法。
5. 第 508–513 行给了每个阶段的工期（周），README 写"No step has a date"。Pre-Refactor 写 2–3 周，实际从规划（11-25）到发布（11-27）约两天。

---

## 四、11-27 审计问题的复核

规划的 §3–§7 在 11-27 之后没有改，所以 RM-1 至 RM-7 的问题原样存在，只是行号移动了（见 §2.2 的表）。逐项说明：

1. **RM-1**（`event_seq` 没有缺口规则）：第 316、341、559、605 行的文字没变。补充：快照要从唯一的写者取，所以它依赖 RM-12 的"单一写者"。
2. **RM-2**（daemon 的形态与生命周期）：第 296 行仍是"常驻/按需拉起"，第 371 行仍是"`src-tauri/` 嵌入 `zycode-daemon`"。补充：worker 的环境（RM-18）属于同一个设计。
3. **RM-3**（daemon 运行 git 的信任门）：没变。补充：worktree 本身的信任身份见 RM-13。
4. **RM-4**（`pi-rust` 的范围与黄金测试集）：没变。补充：§六 第 5 条的契约测试可以作为 `tests/conformance/` 的起点。
5. **RM-5**（门禁没有阈值）：没变。补充：对照组本身要先优化（RM-16）。
6. **RM-6**（`broadcast` 会丢事件）：没变。`InProcessPiRustBackend` 本身的去留见 RM-9。
7. **RM-7**（配置目录规则）：没变。README 的 Roadmap 表把它列为 Phase 0 的"Planned"。
8. **RM-8**（三处文档不准确）：部分处理。§2.5.1 的更正（第 265 行）写了 Open VSX 比对的是 `workflow_ref`，但 §2.2 表格的原文（第 154 行）还是"`repository` claim 变为…"。§0.1 的数字与计数方法没改（新的不一致见 RM-21 第 3 条）。第 3 点（steer 的回报）随 C1 的修复成立。

---

## 五、建议补充的决策项与处理顺序

### 5.1 建议加入规划 §7 的决策

| # | 决策点 | 推荐 | 相关 |
|---|--------|------|------|
| D6 | Phase 3 的 TUI 是否保留 ACP 作为唯一接缝 | 保留：`pi-rust` 是独立的 ACP agent，TUI 不变 | RM-9、RM-6 |
| D7 | daemon 的 worker 隔离单位 | 一个进程只放一个项目的会话；daemon 不转发 X8 | RM-10 |
| D8 | 会话由谁写 | 每个会话一个写者（锁或租约）；写明编辑器是否经过 daemon | RM-12、RM-1 |
| D9 | 远程客户端的权限范围 | 手机只能回答工具审批；不能信任项目，不能切 `always-approve`，不能改配置 | RM-11、RM-19 |
| D10 | Windows `zypi` 是否独立于 Phase 3 | 独立，现在做 | RM-15 |

### 5.2 处理顺序（建议）

| 时间点 | 先定的项 | 原因 |
|--------|----------|------|
| 现在（Phase 0 内） | RM-15、RM-16、RM-21 | 不依赖新代码；RM-16 会改变 Phase 2 的基线 |
| Phase 1 开始前 | RM-9（D6）、RM-10、RM-11、RM-12、RM-13、RM-14、RM-17、RM-18、RM-19 的本机部分、RM-20；以及 11-27 审计的 RM-1、RM-2、RM-3 | 它们决定 daemon 的形态、协议与客户端核心的边界。D6 也决定桌面端会不会进程内嵌 `pi-rust` |
| Phase 2 开始前 | 11-27 审计的 RM-4、RM-5 | 黄金测试集与阈值要在 `pi-rust` v1 做完之前定 |
| Phase 4 开始前 | RM-19 的远程部分 | — |

另外建议把这些项写进 Phase 1 的退出门禁（第 563–564 行现在只有"真实模型端到端实测"与"VS Code 0 回归"）：未受信任的项目上不启动 git（RM-3）；一个会话切到 `always-approve` 不影响别的会话（RM-10）；手机不能回答信任问题（RM-11）；两个客户端打开同一个会话时没有分叉（RM-12）。

---

## 六、核对后正确的部分

1. §0.1 的 agent 体积与 v1.2.0 的发布产物一致：linux-x86_64 77,027,814 B，darwin-aarch64 41,422,729 B，windows-x86_64 42,736,246 B。
2. §0.1"缺 `windows-aarch64`（`tiktoken` 无 wheel）"成立。2026-11-28 核对：`tiktoken` 0.14.0 有 `win_amd64` 的 wheel，没有 `win_arm64`；`cryptography`、`pydantic-core`、`zstandard`、`regex` 都有 `win_arm64`。
3. §4.3 批次 2 对 Windows 阻塞点（`/dev/stdout`）的描述成立。问题只在于把修它的代价写成了整个批次（RM-15）。
4. agent 不用 ACP 客户端的 `fs/*` 与 `terminal/*` 方法（`vscode/src/core/acp-connection.ts` 第 156–157 行），工具都在 agent 所在的机器上运行。daemon 夹在 agent 与客户端之间，不必实现这些方法，与规划的设计相容。
5. Phase 0 交付 5（`tests/conformance/`）有现成的起点：`test_acp_stdio_contract.py`（13 个测试）设计为可以用 `PI_ACP_CONTRACT_COMMAND` 对"将来的 `pi-rust`"运行，`agent-binaries.yml` 已用它测冻结的二进制。它只测线协议，换语言不用改（这也是 RM-9 建议保留 ACP 接缝的理由之一）。
6. §3.2 原则 2"与 `pi` v3 字节兼容"有代码依据：`_entry_to_json` 按 pi 的字段顺序重排键，`_json_dumps` 用 `separators=(",", ":")` 与 `ensure_ascii=False`（`jsonl_storage.py` 第 64–65、141–154 行）。限制：Python 与 JavaScript 对同一个浮点数的写法可能不同，本机核对：`json.dumps` 写 `1.2e-05`、`1.0`，`JSON.stringify` 写 `0.000012`、`1`。pi-python 现在不填 `UsageCost`（非测试代码里没有赋值），所以它写的文件里没有这种数。规划里"JSONL v3 双向互读 0 差异"的门禁应比较解析后的值，不比较字节。
7. `vscode/src/core/` 有 23 个模块，没有一个导入 `vscode`（与 11-27 审计一致）。
8. §2.5.1 的执行记录与仓库一致：8 个 `pyproject.toml`、`vscode/agent-assets.json`、`vscode/package.json`、`web/src/data/site.ts` 都已指向 `zy1233/zycode`；v1.2.0 的 Release 在 `zy1233/zycode` 下，共 27 个资产。

---

## 七、未核实的部分与限制

1. **没有运行下载的发布程序。** 本会话的自动模式安全检查拒绝运行从网上下载的 `zycode-agent`，本文没有绕过它。所以冻结程序的冷启动没有在本机量。§八 8.1、8.2 的体积分析只读字节：ELF 节头、PyInstaller 归档的目录，以及解压后的 ELF 节头。
2. **测量环境。** §八 8.3 是云端 Linux VM（4 vCPU）、Python 3.12、`constraints.txt` 的版本，从源码运行（不是冻结程序），用 `mock` 与一个不联网的 OpenAI 格式配置，各 5 次。它不能与 §0.1 的 1.7 s 或 11-27 审计的 Windows 数字直接比较。
3. **没有验证改动的效果。** 本文不改代码，没有运行 `pytest`、`npm run check` 或 Rust 构建。RM-15、RM-16 的修正效果（Windows 编译、strip 之后的体积、启动时间）是推断，要在实现时量。strip 后的体积按节头估算（362.1 MB 减 313.1 MB，约 49 MB），没有真的 strip。
4. **设计审查。** daemon、`pi-rust`、桌面端、手机端还不存在。RM-9 至 RM-21 是设计审查，证据是现有的代码、产物与决定。RM-12 的复现在存储层做，不是两个真实的客户端。
5. **第三方平台的行为。** 下面几条是平台的一般行为，本次没有逐项查文档或实测：Tauri WebView 与 React Native 没有 Node 运行时；浏览器允许网页连本机 WebSocket；macOS 公证要求所有可执行文件签名；从桌面启动的应用拿不到 shell 的环境。临时目录的清理规则因系统而异，没有逐个核对。
6. **`tui/` 里的遥测与诊断 crate**（RM-9 第 2 点）：只核对了目录存在，没有核对它们在 `zypi` 里是否启用。
7. **VS Code 插件在 Phase 1 之后怎么连 agent**（RM-12 第 2 点）：规划没写，本文按两种可能分别写了后果。

---

## 八、证据与复现

脚本写在审计者的临时目录里，不在仓库内。每节写了做法，可以照着重写。

### 8.1 `zypi-linux-x86_64` 的节（v1.2.0）

做法：用 HTTP range 请求读 ELF 头（前 64 字节），得到节头表的位置；再读节头表与节名表。不下载整个文件。

```text
 121,400,563  .debug_str
  92,154,529  .debug_info
  37,073,648  .text
  33,065,191  .debug_line
  32,667,712  .debug_ranges
  25,980,209  .debug_loc
   6,056,448  .rodata
   4,660,169  .strtab
   ...
   1,370,928  .symtab
     840,800  .debug_aranges
sections: 45; .debug_* + .symtab + .strtab = 313,069,024 bytes（文件 362,130,992 B）
```

### 8.2 `zycode-agent-linux-x86_64` 的归档（v1.2.0）

做法：在文件末尾找 PyInstaller 的 cookie（`MEI\x0c\x0b\x0a\x0b\x0e`），读归档目录（每项：偏移、存储大小、解压大小、压缩标志、类型、名字）；对选中的条目 `zlib` 解压，读 ELF 节头。没有执行程序。

```text
python 312, lib libpython3.12.so.1.0, entries 295, archive 77,703,087 B
最大的条目（存储 / 解压）：
  16,955,507 /  16,955,507  z  PYZ.pyz
  12,140,501 /  30,084,752  b  libpython3.12.so.1.0
  12,140,501 /  30,084,752  b  pi_agent_core/sandbox/frozen-child/_internal/libpython3.12.so.1.0
   4,565,636 /  13,849,312  b  cryptography/hazmat/bindings/_rust.abi3.so
   2,738,695 /  12,138,320  b  zstandard/_cffi.cpython-312-x86_64-linux-gnu.so
   2,727,995 /  11,552,544  b  zstandard/backend_c.cpython-312-x86_64-linux-gnu.so
   2,178,194 /   2,224,984  b  pi_agent_core/sandbox/frozen-child/pi-sandbox-child
   ...
解压后合计 182,634,147 B

解压后的调试信息与符号表（.debug_*、.symtab、.strtab）：
  30,084,752 B，其中 23,112,233 B（76.8%）  libpython3.12.so.1.0
  13,849,312 B，其中  2,344,624 B（16.9%）  cryptography/hazmat/bindings/_rust.abi3.so
  12,138,320 B，其中 11,103,555 B（91.5%）  zstandard/_cffi
  11,552,544 B，其中 10,592,557 B（91.7%）  zstandard/backend_c
   5,040,632 B，其中          0 B（ 0.0%）  pydantic_core
   3,728,512 B，其中    925,591 B（24.8%）  tiktoken
```

### 8.3 源码版 agent 的启动与内存

做法：`uv venv --python 3.12`，再 `uv pip install -c packaging/agent/constraints.txt -e . -e ./packages/pi-agent-harness -e ./packages/pi-agent-cli`。每次运行用一个新的临时 `HOME` 与 `PI_HOME`，在一个空目录里启动 `python -m pi_agent_cli`，计时到 `initialize` 的回答，再发 `session/new`，读 `/proc/<pid>/status` 的 `VmRSS`。

```text
PI_USE_MOCK=1：
run 1: initialize 1.07 s; session/new +0.01 s; RSS 75 MiB after initialize, 75 MiB after session/new
run 2: initialize 0.84 s; ... 74 MiB / 74 MiB
run 3: initialize 0.94 s; ... 74 MiB / 74 MiB
run 4: initialize 1.00 s; ... 74 MiB / 74 MiB
run 5: initialize 1.06 s; ... 73 MiB / 74 MiB

agent.toml 设 provider = "openai"，base_url 指向 127.0.0.1 上不存在的端口（不联网）：
run 1–5: initialize 0.94 / 1.04 / 0.94 / 0.94 / 0.96 s；RSS 73 MiB / 74 MiB
```

`python -X importtime` 的结果（同一配置），按顶层包汇总自身耗时，共 0.93 s：`acp` 0.336 s（36.3%）、`langsmith` 0.163 s（17.6%）、`pi_agent_core` 0.070 s、`pydantic` 0.067 s、`pi_agent_harness` 0.036 s、`pi_agent_cli` 0.030 s、`langchain_core` 0.029 s。`langchain_openai` 与 `openai` 没有在启动时导入。

### 8.4 两个写者同写一个会话（RM-12）

```python
import asyncio, os, tempfile
from pi_agent_harness.session.jsonl_storage import JsonlSessionStorage
from pi_agent_harness.session.session import iso_now
from pi_agent_harness.types import MessageEntry

async def append(storage, text):
    leaf = await storage.get_leaf_id()
    entry = MessageEntry(id=await storage.create_entry_id(), parentId=leaf, timestamp=iso_now(),
                         message={"role": "user", "content": text, "timestamp": 0})
    await storage.append_entry(entry)
    return entry.id, leaf

async def main():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.jsonl")
        s0 = await JsonlSessionStorage.create_path(path, cwd=d, session_id="s1")
        await append(s0, "first message")
        a = await JsonlSessionStorage.open_path(path)  # 例如编辑器自己的 agent
        b = await JsonlSessionStorage.open_path(path)  # 例如 daemon 的 worker
        ida, pa = await append(a, "from A")
        idb, pb = await append(b, "from B")
        print("A", ida, "parent", pa, "| B", idb, "parent", pb)
        print("A sees B's entry:", await a.get_entry(idb) is not None)
        r = await JsonlSessionStorage.open_path(path)
        chain = await r.get_path_to_root(await r.get_leaf_id())
        print("reload context:", [e.message["content"] for e in chain if e.type == "message"])
        print("A's message on the path:", ida in [e.id for e in chain])

asyncio.run(main())
```

输出（`main` @ `0ae5ddc`）：

```text
A appended 4fd7a4a9 with parent 457a1168
B appended 23a7a79a with parent 457a1168 (same parent: the tree forks)
A still sees B's entry: False
reload: leaf 23a7a79a context: ['first message', 'from B']
A's message on the context path: False
```

### 8.5 其他核对

- `win_arm64` 的 wheel：`python3 -m pip download <包> --only-binary :all: --platform win_arm64 --python-version 3.12 --implementation cp --no-deps`。`tiktoken`：`No matching distribution found`；同样的命令用 `--platform win_amd64` 得到 `tiktoken-0.14.0-cp312-cp312-win_amd64.whl`。
- `vscode/src/core/` 的 Node 依赖：`grep -n 'from "node:' vscode/src/core/*.ts`，6 个文件。
- 各工作流的触发路径：读每个 `.github/workflows/*.yml` 的 `on:` 一节。
- 浮点数的写法：`python3 -c "import json; print(json.dumps({'a':0.000012,'c':1.0}, separators=(',',':')))"` 与 `node -e 'console.log(JSON.stringify({a:0.000012,c:1.0}))'`。
