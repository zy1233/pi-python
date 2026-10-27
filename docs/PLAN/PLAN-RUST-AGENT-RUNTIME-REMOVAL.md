# Rust Agent Runtime 剥离计划

> **状态：截至 r23（2026-10-27）。** v0.5.0（2026-10-13）与 v0.6.0（2026-10-25）都已发布。阶段 A、C 完成；阶段 0 基本完成；阶段 B 的删除完成，迁移没有完成；阶段 1 完成一半；阶段 D 没有开始（[当前状态](#当前状态)）。PR [#42][pr-42]（钉 `build-tui` 的运行器，补 glibc 的下限）已由用户合入 `main`（`e65af4d`）。PR [#43][pr-43]（三个 Actions 升到 Node 24 的版本）与 [#44][pr-44]（扩展 API 的审计批 9）也已合入 `main`（`969062b`、`e860fd9`）；`main` 上的 `CI` 与 `TUI CI` 通过。在等用户决定的事见[待决策](#待决策)；发布之后留下的事见[下一步](#下一步)。每一轮的过程在[执行记录（归档）][log]。
>
> **这份文档只写计划和状态。** 每一轮做了什么、怎么测的、数字是多少，在[执行记录（归档）][log]里。§1–§3、§6–§9 保留 r2 原文作为决策依据（§7 的每一项后面加了一行状态）。§4、§5 是 r2 的源码事实与规模估计，描述拆除之前的代码，原文在归档里。与执行记录冲突处，以执行记录和 ADR3（r3）为准。
>
> 与既有文档的关系：承接 [Phase 4 设计](../specs/2026-08-25-phase4-coding-agent-cli-design.md) §3「第一轮允许 pager 继续链接 `xai-grok-shell`……变瘦不是迁入前提」和 [`AUDIT-PHASE4-PHASE5.md`](../AUDIT/AUDIT-PHASE4-PHASE5.md) 的「TUI 瘦身长期里程碑」，是 Phase 4 的第二轮。与既有决定的张力见 §3。
>
> 负责人：待指派。

**怎么读**

| 想知道 | 看哪里 |
|---|---|
| 做到哪了，下一步做什么 | [当前状态](#当前状态) |
| V0.5 发布要做什么、带着什么问题发 | [发布里程碑 V0.5](#发布里程碑-v05) |
| V0.6 发布要做什么、带着什么问题发 | [发布里程碑 V0.6](#发布里程碑-v06) |
| 有哪些事在等用户决定 | [待决策](#待决策) |
| 为什么这样设计 | §1–§3（目标、架构、与现有文档的关系）、§6（ADR1–ADR7） |
| r2 核对过的源码事实、改动规模的估计 | §4、§5（只剩索引，原文在[归档的附录 E][logE]） |
| 每个阶段还剩什么 | §7（每项一行状态） |
| 某一轮怎么做的、测了什么、数字是多少 | [执行记录（归档）][log]；§10 有索引 |
| 整理前的状态文字（逐轮的过程、里程碑的证据） | [归档的附录 F][logF] |
| 能力矩阵、ACP 版本配对 | 附录 A |

## 当前状态

这一节只写现在是什么。一轮做了什么，写在[执行记录][log]的 §10.x 里，不要写回这里；发布里程碑与「下一步」也一样。

截至 r23：**v0.5.0 与 v0.6.0 都已发布。** 计划的 PR 都已合入 `main`。v0.6.0 之后合入的有：PR [#40][pr-40]（`mode_probe.py` 的探针修复）、[#41][pr-41]（r21、r22 的文档）、[#42][pr-42]（`release.yml` 的 `build-tui` 钉 `ubuntu-24.04`；`docs/TUI-AND-CODE-AGENT.md` 补 glibc 的下限）、[#43][pr-43]（`release.yml` 与 `tui-ci.yml` 里三个 action 升到 Node 24 的版本）、[#44][pr-44]（扩展 API 的审计批 9：F7-02 至 F7-09）。它们都不在 v0.6.0 里。没有 V0.7 的里程碑。整理前逐轮的状态文字（r12 续 8 到 r22 续 1）在[归档的附录 F.2][logF2]。

| 阶段 | 状态 | 已完成 | 还差 |
|---|---|---|---|
| 0 基线与门禁 | 基本完成 | Linux CI 与基线（0.1、0.2）；ACP 契约与 e2e（0.3）；deny-list（0.4）；调用图工具化（0.5，r11）；入口审计（0.6）；读盘调用点与 ADR1 落地设计（0.7）；能力矩阵与版本配对（0.8）；Linux 上的实测（0.9 的大部分） | 0.9 的 Landlock 层（要内核 ≥ 5.13），以及 macOS、Windows 一侧 |
| A 死代码先行 | 完成 | A.1–A.4 | — |
| 1 协议与 Python | 完成一半 | 1.P1 会话列表分页；1.P2 优雅退出；1.P4 显式忽略 `mcp_servers`（r20：装了 `pi-mcp-py` 就连接，决策 19，[#35][pr-35]、[#36][pr-36]）；1.P7 契约测试；1.R1 ADR1 落地（PR 1；PR 2、PR 3 在 [#23][pr-23]、[#24][pr-24]，r13 做成，r14 合入 `main`）；会话文件在第一条消息时才写（决策 17，r14，[#25][pr-25]，r16 合入 `main`；r20 的更正：Zed 重启后空白对话能再打开，[#34][pr-34]）；1.R3 的大部分；1.P3 的 (a) | 1.P3 的 (b)、(c)；1.P5；1.P6；1.R2；1.R4；1.R5；1.R3 的 MCP 子进程与 Windows |
| B 拆除 runtime | 删除完成，迁移没有完成 | B.1、B.5、B.6：`pi-sampler` 与 `pi-shell` 的 runtime 单元已删；r15：`pi-shell` 的旧存储层 `session::{storage, persistence}` 也删了（决策 10，[#26][pr-26]，r16 合入）；r16：`session::worktree`、`session::helpers` 等死代码与 10 个源码没点名的依赖也删了（[#27][pr-27]，r19 合入 `main`） | B.2、B.3、B.4：r3 换了做法，没有抽出叶子 crate |
| C 验收与文档 | 完成（r11，PR [#13][pr-13]） | C.1–C.4：`zypi` 没有到 LLM provider 的外连（实测，含一次真模型）；内嵌指南缩成 7 页，启动不再解压；`NOTICE` 重写，`THIRD-PARTY-NOTICES` 有工具对账；`grok agent` 清零，`leader`、`x.ai` 有上限 | 退出条件里的 `leader`、`x.ai` 清不到 0，改成了棘轮（§7 的 C） |
| D 依赖图瘦身（可选） | 没有开始 | — | D.1–D.9；等基线数据再定 |

### 发布里程碑 V0.5

**状态：已发布。** v0.5.0 在 2026-10-13 发布：tag `v0.5.0` 在 `be3f8a8`，[GitHub Release](https://github.com/zy1233/pi-python/releases/tag/v0.5.0)，GitHub 里程碑 [v0.5.0][ms-v050]（已关闭）。它是第一个不带 Rust agent runtime 的发布：`zypi` 只做终端界面和 ACP client，Python 是唯一的 agent。范围按当时的剥离状态定：不等阶段 1 的余项、ADR1 的 PR 2 与 PR 3、阶段 D。

六项退出条件都有结果：

| 条件 | 结果 |
|---|---|
| 1. CI 绿 | 满足。`CI` 与 `TUI CI` 在 `main` 上通过（`cargo test`：8,700 通过 / 0 失败 / 22 忽略）；`TUI CI` 没有为只改文档的 tag 提交 `be3f8a8` 运行，按 `9058ab3` 上通过的那次算（`tui/` 与 `packages/` 的树相同）。 |
| 2. 再量 release 体积 | 满足。发布件 `zypi-linux-x86_64` 是 420,720,608 B，不大于 v0.4.0 的 470,461,776 B（小 10.6 %）。 |
| 3. 发布构建演练 | 满足。`release.yml` 加了手动入口（PR [#14][pr-14]）；演练两次，Linux 与 macOS arm64 的构建都成功。 |
| 4. 与 v0.4.0 对照 | 用户决定不做。「已知问题」里哪些 v0.4.0 就有，仍是推断，没有测量。 |
| 5. 发布说明 | 满足。`CHANGELOG.md` 的 `[0.5.0]`；发布页的正文与它逐字节相同（PR [#15][pr-15]、[#17][pr-17]、[#21][pr-21]）。 |
| 6. 打 tag | 满足。`Release` 的运行跑了三次才全部成功：macOS 的 `Install protoc` 被 GitHub 的 API 限流，失败两次（`release.yml` 当时没有 `repo-token`；PR [#29][pr-29] 补上了）。PyPI 六个包，GitHub Release 14 个文件。 |

没有验证的：macOS 的二进制没有人运行过。V0.5 的已知问题写在 `CHANGELOG.md` 的 `[0.5.0]`。每项的证据、数字与 run 链接：[§10.15][l10-15]，和[归档的附录 F.3][logF3]（整理前的原文）。

### 发布里程碑 V0.6

**状态：已发布。** v0.6.0 在 2026-10-25 发布：tag `v0.6.0` 在 `5f73e07`，[GitHub Release][rel-v060]，GitHub 里程碑 [v0.6.0][ms-v060]（已关闭）。它是第二个不带 Rust agent runtime 的发布，发 `main` 上 v0.5.0 之后的 16 个提交（#22–#31、#33–#36、#38、#39；#22、#33、#38 只改文档）：会话经 ACP 找，`zypi` 不读会话文件（ADR1 的 PR 2、PR 3；决策 2、4、5；[#23][pr-23]、[#24][pr-24]）；没人发过消息的会话没有文件，Zed 重启后这样的对话能再打开（决策 17；[#25][pr-25]、[#34][pr-34]）；删 `pi-shell` 的旧存储层与死代码（决策 10；[#26][pr-26]、[#27][pr-27]）；测试与 CI 的加固（[#28][pr-28]–[#31][pr-31]）；给扩展的挂钩与新包 `pi-mcp-py` 0.1.0（决策 19；[#35][pr-35]、[#36][pr-36]）；版本号与发布说明（决策 20；[#39][pr-39]）。用户能看到的变化：`CHANGELOG.md` 的 `[0.6.0]`。

六项退出条件都有结果：

| 条件 | 结果 |
|---|---|
| 1. CI 绿 | 满足。在 `5f73e07`（tag 的提交）上，[`CI`][ci-run-5f73e07] 通过（`pytest` 在 Python 3.11、3.12、3.13 上都是 2,908 通过 / 39 跳过 / 31 取消选择），[`TUI CI`][tui-ci-run-5f73e07] 通过（`cargo nextest` 8,490 通过 / 0 失败 / 22 跳过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297）。 |
| 2. 再量 release 体积 | 满足。`zypi-linux-x86_64` 406,911,320 B，比 v0.5.0 发布件的 420,720,608 B 小 13,809,288 B（3.3 %）。 |
| 3. 发布构建演练 | 满足。`Release` 的[演练][run-dry-run-5f73e07]（`5f73e07`）全部成功：测试、七个 Python 包、Linux 与 macOS arm64 的构建；macOS 第一次就成功。 |
| 4. 与 v0.5.0 对照 | 做了（用户答「做」）。每个差别都有解释：三个差别在 `CHANGELOG.md` 里有一条说明（`resume_matrix.py`：13 通过 / 3 失败 → 16 通过 / 0 失败）；一个是 `mode_probe` 的探针竞争，不是构建的差别（修复是 PR [#40][pr-40]）。 |
| 5. 发布说明与版本号 | 满足。PR [#39][pr-39]：核心、harness、cli 升到 0.6.0；三个已发布的扩展包 0.3.0，`pi-mcp-py` 0.1.0；四个扩展包的核心下限 `>=0.6.0`；`CHANGELOG.md` 的 `[0.6.0]`。 |
| 6. 打 tag | 满足。`Release` 的[运行][run-release-v060]一次就全部成功；PyPI 七个包是预期的版本（`pi-mcp-py` 第一次上传）；GitHub Release 16 个资产；发布后的核对没有发现缺陷（[§10.25][l10-25]）。 |

没有验证的与留下的事：见[下一步](#下一步)与[还没有验证的](#还没有验证的)。每项的证据、数字与 run 链接：[§10.22][l10-22]、[§10.23][l10-23]、[§10.24][l10-24]、[§10.25][l10-25]，和[归档的附录 F.4][logF4]（整理前的原文）。

**带着发布的已知问题**（已写进 `CHANGELOG.md` 的 `[0.6.0]`，随 v0.6.0 发布）：

- 交互模式下 `--system-prompt-override` 与 `--rules` 无效，`--allow` 与 `--deny` 到不了 agent，`zypi --help` 还列着没有逐个核对过的 Grok 时代旗标（决策 8，待定）。
- 设置页里权限模式以外的项对 Python agent 可能没有效果；`remember_tool_approvals` 与 `default_selected_permission` 没有测过（决策 14，待定）。
- `[ui.status_line]` 在 Python agent 下没有 `context` 与 `cost` 两项，脚本也拿不到 `context_window.*`、`cost.total_cost_usd`、`workspace.branch`、`workspace.repo_root`（决策 15 的 (b)；agent 不告诉 `zypi` 上下文窗口、已用的 token 与费用）。
- 内嵌的七页指南没有入口可以打开（D.6）。
- Python 一侧：Phase 6/7 审计的 F7-02 至 F7-09 没有修复批次（[`AUDIT-PHASE6-PHASE7-2026-09-30.md`](../AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md) 第八节）。r23 的 PR [#44][pr-44] 处理了它们（已合入 `main`，在 v0.6.0 之后）；已发布的 v0.6.0 仍带着它们。
- 平台：只在 Linux（WSL2）上验证过；macOS 的二进制没有人运行过；Windows 没有二进制；Landlock 层没有测过（WSL 的内核 5.10 没有 Landlock）。
- 翻页的代价（决策 18，已定不做）：agent 给每一页都重新列一次会话目录，所以读完所有页的代价随会话数快速增长。`--session-id` 在 5,000 个会话时：Linux 28 s、Windows 34 s（Windows 上超过 30 s 的限额）；1,000 个会话时 1.3 s、1.8 s。1,000 个会话以内够用。
- 同一个 id 的并发没有关死：两个 agent 进程共用一个 home 时，同一个 id 仍可能各建一个会话；先存盘的赢，另一个的第一条消息被拒（`a session with this id already exists`）。
- `pi-mcp-py` 是 Alpha（[#36][pr-36]）：没做 OAuth，MCP 的 resources、prompts、sampling、roots、elicitation、`tools/list_changed`，websocket 的 server；对一个已死的 SSE server 发调用，要等 120 s 的时限；Zed 在第一个工作树加载之前发的 `session/new`，`mcpServers` 是空的（zed#64611），那样的对话里没有 MCP 工具，也没有提示；`zypi` 里没有配 server 的办法；server 声明的 `annotations` 照单全收（决策 21 的 Q3）；发布时没有在真的 Zed 的界面里验证过，用户在 2026-10-27 答复核对通过（[§10.26][l10-26] 续 2）。
- 从没存在过的 UUID 也能 `session/load`（[#34][pr-34]）：agent 分不出它和空白对话的 id，所以会开一个空会话；`zypi export <uuid>` 对它说「没有可导出的内容」。

**不在 V0.6。** 决策 7 的 (b)(c)、8、9、10 的 `mcp.rs` 与 `recent_dirs.rs` 两项、14（决策 11、13 已定，不做，r23；决策 20 已定并已做：条件 5，[#39][pr-39]；决策 21 已定，没有要做的）；阶段 0 的余项（0.9 的 Landlock 层，macOS 与 Windows 一侧）；阶段 1 的余项（1.P3 的 (b)(c)、1.P5、1.P6、1.R2、1.R4、1.R5、1.R3 的 MCP 子进程与 Windows）；B.2–B.4；阶段 D 全部（D.1–D.9）；`protoc_ci_env`（在 `tui-ci.yml` 里设 `PROTOC`）。它们归哪个里程碑，由用户排；现在没有 V0.7 的里程碑。决策 14 的倾向里，第一步是对着真 agent 测 `remember_tool_approvals` 与 `default_selected_permission`；这一步只量不改，不用等用户定。

### 下一步

- **已发布：** v0.5.0（2026-10-13）与 v0.6.0（2026-10-25）。V0.6 的六项退出条件都有结果，发布后的核对没有发现缺陷（[§10.25][l10-25]）。GitHub 里程碑 [v0.5.0][ms-v050] 与 [v0.6.0][ms-v060] 都已关闭。
- **发布之后留下的（r22；①② 在 PR [#42][pr-42] 里做了，用户已合入，`e65af4d`）：** ① `release.yml` 的 `build-tui` 钉 `ubuntu-24.04`：`ubuntu-latest` 要迁到 Ubuntu 26，迁了之后 Linux 的二进制可能要更新的 glibc（现在要 glibc 2.39 或更新）。② glibc 的下限写进 `docs/TUI-AND-CODE-AGENT.md`。③ Node.js 20 弃用的警告：PR [#43][pr-43]（r23，已合入 `main`）把 `upload-artifact` 升到 v6、`download-artifact` 升到 v7、`cache` 升到 v5；警告现在只点名 `arduino/setup-protoc`，它没有 Node 24 的版本，留着。**没做**：PyPI 一侧的 Trusted Publishing（要账号所有人在 PyPI 上设）；现在都不影响发布。
- **要等用户决定：** 1.P6 的旗标契约（决策 8）；设置页里其余的项（决策 14）；真的 Plan 模式（决策 7 的 (b)）；「不在 V0.6」里的各项归哪个里程碑（现在没有 V0.7 的里程碑）。
- **不用等决定：** 阶段 D 做不做，看 [`docs/baselines/tui.md`](../baselines/tui.md) 的数据；D.5–D.9 是候选（§7）。D.8 还剩新候选：`pi-workflow`、`Config.storage_mode`、没人加载的文件。
- **已定，不做：** 翻页的代价（决策 18；用户在 2026-10-18 答复：不做，1,000 个会话以内够用）；选择器边翻边显示（决策 11）与会话目录的列举太慢（决策 13）（用户，2026-10-27：不做）。

### 还没有验证的

- **平台。** Rust 的 e2e 用例与 `scripts/tui_pty/` 的 PTY 脚本只在 Linux（WSL2）上跑过；macOS 与 Windows 上没有。Windows 上只量了 Python 一侧的延迟，TUI 没有参与。
- **真实 LLM。** r9、r10 的实测都用 mock。r11 用真模型跑了一轮网络审计（C.1，只看 `zypi` 有没有外连），没有用它验证别的功能。
- **沙箱的 Landlock 层。** WSL 的内核 5.10 没有 Landlock；`pi-sandbox` 的端到端用例在那里空转（软跳过）。
- **延迟。** 只量了合成的会话（12 / 120 / 2,000 条消息）。冷磁盘、网络文件系统、超过 1,000 个会话、真实形状的会话没有量。
- **跨目录的 `--resume <id>`。** TUI 的当前目录与会话保存的目录不同时，`@` 搜索、diff、git 信息用哪个目录，没有验证。
- **旗标。** 除 `--always-approve`、`--permission-mode`、`--continue`、`--resume`、`--session-id` 外，没有逐个验证旗标在交互模式下是否生效。`-m` / `--model` 尤其没有查。
- **设置页里其余的权限项**（`remember_tool_approvals`、`default_selected_permission` 等）对 Python 是否有效，没有验证（待决策 14）。
- **同一个 id 的并发。** 锁只在进程内。两个 agent 进程共用一个 home 时，同一个 id 仍可能各建一个会话。
- **测试之间的竞争。** r15 修了一个，r17 查了同类：19 个偶发失败的测试和 5 个不还原环境变量的测试都修了（[§10.18][l10-18]、[§10.20][l10-20]）。**还在的：** ① 约 140 个测试（21 个文件）写外观缓存里的别的设置，没有隔离；没有已知的受害者，但有两个测试各失败过一次，原因没有证实。探针还列出读 `GROK_HOME`、`GROK_TEST_VERSION` 又不拿锁的测试（`pi-pager` 6 组，`pi-shell` 25 组）；我没有逐个读。② 别的 7 个 suite 的 lib 只各跑了 10 遍；清单外的 crate 与集成测试二进制没有查。③ `in_own_process` 有限制，见 [§10.20][l10-20]。④ 只在 Linux 查过。竞争只在测试线程多、顺序不同的时候出现（CI 是 4 个线程，顺序固定），所以 **CI 通过不能证明它们不存在**。⑤ `clippy` 还没有跑过。
- **发行构建。** 发布的两个二进制由 `Release` 的运行构建（v0.6.0：[run][run-release-v060]，`5f73e07`；v0.5.0：[run 38358144687](https://github.com/zy1233/pi-python/actions/runs/38358144687)，`be3f8a8`）。Linux 的那个下载后跑过：v0.6.0 打印 `zypi 0.6.0 (5f73e07964f1)`，`smoke`、`exit_matrix`、`resume_matrix`、`mode_probe`、`status_line_probe` 对着 PyPI 的 agent 都通过，二进制与构建产物逐字节相同（[§10.25][l10-25]）；它要 glibc 2.39 或更新。**macOS 的二进制没有人运行过**：构建通过过（演练与发布各几次），那只证明它能编译与链接；v0.6.0 的只看了文件头（Mach-O arm64）。**Windows 没有二进制。** `TUI CI` 在 PR 上不运行 `zypi --version`；手动的 `release_baseline` 为了量耗时跑它，但丢掉输出，也不看退出码。
- **`download-artifact@v7`。** PR [#43][pr-43] 把它从 v4 升到 v7（输入 `name`、`path` 不变）。它只在两个发布的 job 里用，`Release` 的演练不运行它；下一次真的发布才第一次运行。

出处：执行记录 [§10.10][l10-10]–[§10.15][l10-15] 各节的「没有验证」。

## 待决策

共二十一项。能给出倾向的，都写了倾向。没有一项会在用户确认之前动手。编号沿用 r9 的提议（[§10.11][l10-11] 末尾「留给用户决定的」十二项，原文留在那里）；13、14 是 r10 加的，15 是 r11 加的，16 是 r12 加的，17、18 是 r13 加的，19–21 是 r20 加的。附录 A 的「决定 N」指这里的第 N 项。

- **已做：** 1 的 PR 1（PR [#11][pr-11]）、PR 2（PR [#23][pr-23]）、PR 3（PR [#24][pr-24]；后两个 r14 合入 `main`）、2、3、4、5、6 的 (a)（PR [#18][pr-18]）、7 的 (a)、12、15 的 (b)（PR [#19][pr-19]）、16 的 (a)（PR [#17][pr-17]）、17（PR [#25][pr-25]，r16 合入；r20 的更正：PR [#34][pr-34]）、10 的存储层一项（PR [#26][pr-26]，r16 合入）与它的收尾（PR [#27][pr-27]，r19 合入）、19（PR [#35][pr-35]、[#36][pr-36]，r20 合入）、20（PR [#39][pr-39]，r21 合入）、21（用户，2026-10-25：按倾向，即全按默认；默认值已在 PR [#36][pr-36] 里）。
- **已定，不做：** 18（用户，2026-10-18）、11 与 13（用户，2026-10-27）。
- **待定：** 7 的 (b)(c)、8、9、10 的 `mcp.rs` 与 `recent_dirs.rs` 两项、14。

1. **ADR1 的落地。** PR 1：Python；PR 2：Rust，只加不删；PR 3：Rust，删。
    - 倾向：开工，先做 PR 1。
    - 状态：**PR 1 已做**（r10，PR [#11][pr-11]）。**PR 2、PR 3 已做**（r13：用户在 2026-10-16 答复了决策 2、4、5、7；PR [#23][pr-23]、[#24][pr-24]）；r14 合入 `main`（`7e0ca5a`、`0476ea4`）。PR 3 没有删 `pi-shell` 里的旧存储层，那是决策 10。
    - 详情：[§10.11][l10-11]、[§10.12][l10-12]、[§10.16][l10-16]
2. **沙箱顺序。**
    - 倾向：去掉「恢复时沿用保存的沙箱 profile」（对 Python 会话没有行为变化）。备选：先起查询用的 agent（冷启动翻倍，还有有无沙箱的窗口）。
    - 状态：**已定，已做**（用户，2026-10-16：按倾向；r13，PR [#24][pr-24]）。读保存的 profile 的代码删了：沙箱只由 `--sandbox`、`GROK_SANDBOX` 与配置决定，恢复的会话与新会话一样。对 Python 会话没有行为变化（Python 从不存 profile）。`--sandbox X` 恢复一个保存时用 `Y` 的会话，以前被拒绝，现在照 `X` 执行；只有旧 Rust agent 的会话有保存的 profile，Python agent 读不了它们。
    - 详情：[§10.11][l10-11]、[§10.16][l10-16]
3. **`--session-id` 的契约。** ACP v1 的 `session/new` 没有 session id 参数，`_meta.sessionId` 是 pager 与 agent 之间的约定。
    - 倾向：Python 兑现，重复由 agent 以 `invalid_params` 拒绝。备选：删掉这个旗标。
    - 状态：**已定，已做**（r10，PR [#11][pr-11]）。
    - 详情：[§10.12][l10-12]
4. **`--continue` 的「最近」。** Python 的列表按创建时间倒序（ACP 对排序只说「默认排序」）。
    - 倾向：最近活动（`updatedAt` 最大；要翻完所有页，1,000 个会话在 Windows 上约 0.8 s）。备选：最近创建（第一页第一条，最快）。
    - 状态：**已定，已做**（用户，2026-10-16：按倾向；r13，PR [#23][pr-23]）。`--continue` 与不带值的 `--resume` 取本目录里 `updatedAt` 最大的会话，翻完所有页。代价随会话数增长得比它快：5,000 个会话时 `--continue` 要 10–12 s（Windows、WSL2），因为每一页 agent 都重新列一遍目录（待决策 18）。
    - 详情：[§10.11][l10-11]、[§10.16][l10-16]
5. **未知 id 的报错时机。** `--resume <未知 id>` 现在进了 TUI 才报错，状态栏的模型名还是 `unknown`。
    - 倾向：进 TUI 之前报错，退出码 1（`resume_matrix.py` 的用例 8 随之收紧）。
    - 状态：**已定，已做**（用户，2026-10-16：按倾向；r13，PR [#23][pr-23]）。`--resume <未知 id>`、`--resume <未知标题>`、没有会话时的 `--continue`、已用的 `--session-id`，都在进事件循环之前退出，退出码 1，终端先还原、agent 先停。`resume_matrix.py --strict` 13/13 通过（用例 8 要求退出码 1）。
    - 详情：[§10.11][l10-11]、[§10.16][l10-16]
6. **`zypi export`。** 现在对 Python 会话恒报 not found，是一个静默坏掉的命令。r8 时用户选了不动。
    - 倾向：(a) 经 ACP 回放导出：起 agent、`session/load`、收 `session/update`，复用 `render_blocks_to_markdown`，约 100 行，多 1–2 s 的 agent 启动；(b) 删掉子命令。不要 (c) 维持现状。
    - 状态：**已做 (a)，已合入 `main`，进 V0.5**（PR [#18][pr-18]，`ec7d455`；用户，2026-10-13：r12 续 5 定，续 8 合入；[§10.15][l10-15]）。`export_cmd` 起 agent、发 `session/load`、收 `session/update`，与 `/export` 同一条渲染路径写成 Markdown，不再引用 `pi_shell::session::storage`（ADR1 的 PR 3 要先做完这一项）。
    - 详情：[§10.11][l10-11]、[§10.15][l10-15]
7. **模式映射（1.P3）。** (a) 只提供 agent 会照做的模式；(b) 做 `session/set_mode`，Plan = 把工具集裁到只读；(c) 定义 Python 的 `auto`（今天等于 always-approve，设置页却说是分类器：改名、删除，或真做分类器）。
    - 倾向：(a) 先行，因为 Plan 现在给用户的是虚假的安全感；(b) 另议。
    - 状态：(a) **已做**（r10，PR [#12][pr-12]；用户在 2026-10-16 确认倾向）；(b)、(c) 另议，仍待定。Plan 在界面里已经看不到，所以 PR [#24][pr-24] 把 Plan 预览读 `plan.md` 的代码一起删了（r13）：预览只用审批请求带来的内容。以后做 (b) 时，Plan 的内容要走 ACP，不读盘。
    - 详情：[§10.11][l10-11]、[§10.13][l10-13]
8. **旗标契约（1.P6）。** `--system-prompt-override`、`--rules` 在交互模式下无效；`--allow` / `--deny` 没有传给 agent；`-m` 的路径没有查清。`--permission-mode` 已在 r10 收窄到 `default` 与 `bypassPermissions`。
    - 选项：Python 读 `initialize._meta`（前两项各几行）；或 pager 对着 Python agent 拒绝这些旗标并说明；`-p` 另行定义。要定哪些旗标属于产品契约。
    - 状态：待定（用户，2026-10-13：待定；r12 续 5）。V0.5 带着它发，已知问题里已经写了。
    - 详情：[§10.11][l10-11]、[§10.13][l10-13]
9. **选择器的 30 天截止。**
    - 倾向：去掉（agent 已分页，不怕多）；或改成可配置。
    - 状态：待定。
    - 详情：[§10.11][l10-11]
10. **清理。** `load_mcp_servers` / `mcp.rs`（1.P4 之后没有调用者）；`recent_dirs.rs`（只剩一行文档注释）；ADR1 落地后 `pi-shell` 的 `session::{persistence, storage}` 实现层（阶段 B）。
    - 状态：**部分已做**（用户，2026-10-18：做存储层这一项）。**做了：** `pi-shell` 的 `session::storage` 与 `session::persistence`，和只有它们在用的代码（PR [#26][pr-26]，`7d82c27`）；收尾的死代码、`[storage]` 设置、10 个源码没点名的依赖（PR [#27][pr-27]，`7542062`；[§10.18][l10-18]）。**没做：** `load_mcp_servers` / `mcp.rs`（`pi-shell/src/util/config/mcp.rs`）与 `recent_dirs.rs`（`pi-pager/src/recent_dirs.rs`）：用户的答复只说了存储层，这两项没有问过。
    - 详情：[§10.11][l10-11]、[§10.18][l10-18]
11. **选择器边翻边显示。** 现在等所有页回来再画（1,000 个会话约 0.8 s，Windows）。
    - 倾向：不做，除非有人遇到。
    - 状态：**已定，不做**（用户，2026-10-27；r23）。
    - 详情：[§10.11][l10-11]
12. **推送与 PR。**
    - 状态：**已做**：PR [#9][pr-9]–[#12][pr-12] 已合入 `main`。
    - 详情：[§10.12][l10-12]
13. **会话目录的列举太慢。** `LocalExecutionEnv.list_dir` 给每个条目造一个 `FileInfo`：5,000 个文件要 286–372 ms，`os.listdir` 只要 2–11 ms。`session/list` 的每一页、`find`、`has_session` 都付这笔钱。
    - 倾向：给仓库一条只列名字的路径（约 25 行加测试）。5,000 个会话时首页估计从 404 / 319 ms 降到约 80 / 40 ms（Windows / WSL2）；这是估算，没有实现也没有量。
    - 状态：**已定，不做**（用户，2026-10-27；r23）。要改主意时，倾向里的做法仍然可用。
    - 详情：[§10.12][l10-12]
14. **设置页里其余对 Python 没有效果的项。**
    - 倾向：像对模式做的那样，逐个对着真 agent 测，再决定藏起来还是接线。先测 `remember_tool_approvals` 与 `default_selected_permission`（离权限最近，错了代价最高）。还没有证据。
    - 状态：待定（用户，2026-10-13：待定；r12 续 5）。V0.5 带着它发，已知问题里已经写了。
    - 详情：[§10.13][l10-13]
15. **自定义状态行（`[ui.status_line]`）。** 这一行在 Python agent 下永远不画，配了命令脚本也不会跑：它的数据来自一条 `session_status` 通知，只有旧的 Rust agent 发过，Python 从不发（`status_line_probe.py` 实测）。内嵌指南的 `25-status-line.md` 已照实写了这一点。
    - 选项：(a) Python 发这条通知——要走 `_pi/session_notification` 载体（`x.ai/` 开头的方法在入口被丢），是新的 `pi/` 扩展，要先过 DESIGN §8.5 的五条；(b) pager 用本地已有的东西画（工作目录、模型、权限模式、git 分支），不靠 agent；(c) 删掉这一行的设置、代码与指南页。
    - 倾向：(b) 或 (c)，不倾向 (a)：行里的字段大多是 pager 自己就知道的。没有证据说有人在用它。
    - 状态：**已做 (b)，已合入 `main`，进 V0.5；做法从 (a) 改成了 (b)**（PR [#19][pr-19]；用户，2026-10-13：r12 续 5 选 (a)，续 7 复核后改选 (b)，续 8 合入）。(a) 要加新的 `pi/` 扩展，但过不了 DESIGN §8.5 的第 ① 条（没有标准对应物）与第 ④ 条（线上类型要放在旧运行时的 crate 里），所以 pager 用本地已有的数据画这一行，Python 不动；agent 发来的状态仍然优先。`context` 与 `cost` 在 Python agent 下仍然空，没有造数：这是一条已知问题（`CHANGELOG.md` 里也有）。
    - 详情：[§10.14][l10-14]、[§10.15][l10-15]
16. **发行构建的版本戳。** r12 时，`zypi --version` 里的版本号是 `1.0.8`（后面跟提交的前 12 位）：`pi-pager-bin/build.rs` 取环境变量 `GROK_VERSION`，没有就取 crate 的版本，而 `pi-pager-bin` 与 `pi-version` 的版本都是继承自上游的 1.0.8。仓库里没有任何文件设 `GROK_VERSION`，`release.yml` 在 v0.3.0、v0.4.0 与 `main` 上都没设。ACP 的 `agentInfo.version` 写死 `0.1.0`（`pi_agent_cli/agent.py` 的 `_AGENT_INFO`），Phase 6/7 审计记过，没改。
    - **不要靠在 `release.yml` 里设 `GROK_VERSION` 来修。** 同一个变量还决定「是不是发行构建」（`pi_version::IS_DEV_BUILD`）：没有它就是开发构建，发行二进制至今都是开发构建。开发构建里文件夹信任整个是惰性的（`folder_trust_inert()`）：从不提示，不读写 `trusted_folders.toml`，不管配置怎么写。盖了戳，它按默认开启，会对仓库里的本地配置（`.envrc`、`.claude`、hooks、插件、MCP、LSP）问「信任这个文件夹吗」。这些配置里，MCP 的加载代码在 1.P4 之后没有调用者（决策 10）；其余没有逐项核对。遥测客户端的 `dev_build` 字段也随戳翻转。
    - 选项：(a) 不盖戳；把 `pi-pager-bin` 与 `pi-version` 的 crate 版本改成发行版本，`_AGENT_INFO` 读已装包的版本；(b) 盖戳，再处理文件夹信任（删掉，或查清它还管着什么）与遥测；(c) 维持现状，写进已知问题。
    - 倾向：(a)。
    - 核对（r12）：版本号从 1.0.8 改小，会碰到三处按版本比较的代码（`pi-config` 的 `version_overrides`、`pi-update`、`pi-shell` 的模型缓存）；没有发现必须先改的逻辑。三处的细节在[归档的附录 F.7][logF7]。
    - 状态：**已做 (a)，已合入 `main`**（r12 续 2，PR [#17][pr-17]，`d57fd9b`）。`pi-pager-bin` 与 `pi-version` 的 crate 版本（和 `Cargo.lock` 里这两个包）改成 `pi-agent-cli-lc` 的版本，`_AGENT_INFO` 读已装包的版本；`scripts/tests/test_release_versions.py` 要求它们一致（所以每次发布要一起抬高），并要求没有工作流设 `GROK_VERSION`。**行为变化**：版本号比以前小，`minimum_version` 高于新版本的 `[[version_overrides]]` 补丁不再生效（[§10.15][l10-15]）。
    - 详情：[§10.15][l10-15]
17. **空会话。** Python 在 `session/new` 时就建会话文件，没发过消息的会话以标题 `(no messages)` 留在列表里。`zypi --continue` 在它是本目录最近活动的会话时会取到它（r13 的已知限制，[§10.16][l10-16]）。
    - 倾向：Python 在第一条用户消息时才写会话文件。要查的：已发出 id、文件还没有时，`session/load`、`has_session`（重复 id 检查）与列表各怎么答。备选：pager 在 `--continue` 里跳过没有标题的会话——这把 agent 建文件的时机带进 pager，也会漏掉真的空会话；不倾向。
    - 状态：**已定，已做**（用户，2026-10-18：做；r14，PR [#25][pr-25]，`2b0baf3`）。agent 在 `session/new` 时只在内存里建会话，第一条用户消息才写文件（`DeferredJsonlSessionStorage`）：没发过消息的会话没有文件，不在列表里；客户端指定的 id 由 agent 记着（`_reserved_ids`）；两个 agent 进程之间的空隙缩小了，没有堵死。**r20 的更正**（PR [#34][pr-34]，`fa4887b`；[§10.23][l10-23]）：Zed 重启后用 `session/load` 要同一个 id，答 `resource_not_found` 会让它显示 *Failed to Launch*；现在没有文件、id 是规范 UUID 时，`session/load` 与 `session/resume` 在内存里再开一次这个 id（空会话）。副作用：从没存在过的 UUID 也会打开；再打开的空会话占着自己的 id。当时没在真的 Zed 里验证过；用户在 2026-10-27 答复核对通过（[§10.26][l10-26] 续 2）。
    - 详情：[§10.16][l10-16]、[§10.17][l10-17]、[§10.23][l10-23]
18. **翻页的代价。** agent 对每次 `session/list` 都重新列一遍目录，并为每个文件建一个 `FileInfo`（`JsonlSessionRepo._session_names` → `LocalExecutionEnv.list_dir`），所以翻完所有页的代价是「页数 × 目录大小」，增长得比会话数快（实测见 [§10.16][l10-16]）。1,000 个会话以内在 2 s 以内；5,000 个会话时，`--continue`（带 `cwd`）要 10–12 s，`--session-id`（不带 `cwd`，翻完）要 28–34 s，在 Windows 上超过 30 s 的上限，会超时。
    - 倾向：Python 一侧让 `_session_names` 只取文件名（`os.listdir`），不 `stat`、不建 `FileInfo`。一个没有提交的草稿（Windows，每项 1 次运行）：5,000 个会话时，带 `cwd` 翻完 12.3 s → 2.2 s，不带 `cwd` 翻完 33.9 s → 3.6 s，带 id 的 `session/new` 325 → 33 ms。改动在 `pi-agent-harness`，约十行；要查的：`list_dir` 的其他调用者，测试里的假 fs（没有 `list_names` 时要退回 `list_dir`）。备选：`--session-id` 不预检，直接 `session/new`，由 agent 拒绝重复的 id（要把 `session/new` 挪到进事件循环之前，改动大）；或者什么也不做（1,000 个会话以内没有问题）。
    - 状态：**已定，不做**（用户，2026-10-18：不做；r14）。r13 时是待定。1,000 个会话以内够用（在 2 s 以内）；5,000 个会话时 `--session-id` 在 Windows 上会超过 30 s 的上限，这是已知的，`CHANGELOG.md` 的 `[Unreleased]` 里写了数字。上面的草稿没有提交。要改主意时，倾向里的做法仍然可用。
    - 详情：[§10.16][l10-16]、[§10.17][l10-17]
19. **MCP 的方向。** 编辑器（Zed 等）在 `session/new`、`session/load`、`session/resume` 里传来 MCP server。agent 没有 MCP 客户端：Phase 6（2026-09-24）用户定的是「core 与 CLI 不连 server」，与上游 pi 的 "No MCP" 一致；r9 把「静默丢弃」改成「明说」。「编辑器经 ACP 传来 server 时怎么办」没有单独问过用户。ACP v1 要求 agent 支持 stdio 的 MCP server，所以这是有文档的偏离。
    - 选项（用户在 2026-10-23 被问）：(a) 做可选扩展包，连接客户端传来的 server，core 与 CLI 仍不内置；(b) 内置进 `pi-agent-cli`（要改 2026-09-24 的决定）；(c) 保持不支持，提示每个进程只出现一次；(d) 保持不支持，提示照旧。
    - 状态：**已定，已做**（用户，2026-10-23：(a)；r20，PR [#35][pr-35]、[#36][pr-36]；合入 `main`，`34c11d9`、`e0f2a36`）。#35 给扩展挂钩（`ClientMcpServer`、`pi.claim_client_mcp_servers`、`pi.notify`、`pi.register_cleanup`、`pi.add_turn_barrier`；`HarnessBridge` 多 4 个成员）；agent 本身仍没有 MCP 客户端。MCP 提示改成只写没人接手的 server，并告诉用户装 `pi-mcp-py`。#36 是新包 `pi-mcp-py` 0.1.0（`packages/pi-mcp`，入口点 `pi-mcp`）：连接 stdio、Streamable HTTP、SSE 三种 server，工具名 `mcp__<server>__<tool>`，有 `/mcp` 命令；core 与 CLI 不导入它。没装这个包时，agent 与 r9 一样：忽略并说明（所以 1.P4 的「忽略并说明」仍是没装扩展时的行为）。`zypi` 本身不传 server（pager 没有 MCP 界面），所以对 `zypi` 用户没有直接的效果；`zypi` 里也没有配 server 的办法（设计文档 Q5）。设计：[`docs/specs/2026-10-23-mcp-extension-design.md`](../specs/2026-10-23-mcp-extension-design.md)。发布：`release.yml` 装它做发布前测试并构建它，所以下一个 tag 会把它与别的包一起发出去（V0.6 的条件 6）。
    - 详情：[§10.23][l10-23]
20. **扩展包的版本号与核心下限。** `pi_agent_core/tests/test_extension_packaging.py` 要求每个扩展包为工作区里的包写的下限，等于那个包在工作区里的版本（「扩展与核心一起发」）。V0.6 把核心升到 0.6.0 时，四个扩展包的核心下限都要从 `>=0.5.0` 变成 `>=0.6.0`，否则这个测试失败。四个包是：`pi-web-access-py`、`pi-goal-x-py`、`pi-dynamic-workflows-py`（都是 0.2.0）和 `pi-mcp-py`（0.1.0）。`pi-mcp-py` 需要这个下限，因为它用 #35 的挂钩，核心 0.5.0 没有。三个已发布的扩展包自 v0.5.0 起没有代码变化。
    - 倾向：(a) 照 V0.5 的做法（`7e7d4d4`：下限从 `>=0.4.0` 抬到 `>=0.5.0`，版本从 0.1.0 抬到 0.2.0）：升版本号时，四个下限一起抬到 `>=0.6.0`，三个已发布的扩展包也各抬一个小版本（0.2.0 到 0.3.0；下限抬了，新版本就装不进核心 0.5.x，在 0.x 里这用小版本表示）。原因：`publish-pypi` 用 `skip-existing: true`，版本不抬的话，PyPI 上留着旧的元数据（`>=0.5.0`），而仓库里同一个版本号写的是 `>=0.6.0`。备选：(b) 只抬下限，不抬三个包的版本：PyPI 上的 0.2.0 与仓库里的 0.2.0 元数据不同；不倾向。(c) 三个已发布的包保持 `>=0.5.0`，改测试只管 `pi-mcp-py`：这改了「一起发」的规则；不倾向。
    - 状态：**已定，已做**（用户，2026-10-25：「按你的倾向来」；r20 定，r21 做：PR [#39][pr-39]，合入 `main`，`5f73e07`；r22 随 v0.6.0 发布）：(a)。四个扩展包的核心下限都抬到 `>=0.6.0`（`pi-dynamic-workflows-py` 的 `pi-agent-harness-lc` 下限也是）；`pi-web-access-py`、`pi-goal-x-py`、`pi-dynamic-workflows-py` 从 0.2.0 抬到 0.3.0；`pi-mcp-py` 保持 0.1.0（第一次上传）。抬哪一位，倾向里原来没有写；用户没有单独说，0.3.0 是照 V0.5 的先例选的。
    - 详情：[§10.23][l10-23]、[§10.24][l10-24]
21. **MCP 扩展设计里的 11 个默认值。** [设计文档](../specs/2026-10-23-mcp-extension-design.md)的 §10 列了 Q1–Q11：包名、工具名、`annotations`、多会话是否共享 server、`zypi` 里怎么配 server、`http` 与 `sse` 能力的声明、超时、通知渠道、本机 server 是否走系统代理、server 空闲时死了怎么办、重连次数。默认值是我定的，已在 #36 里实现；用户没有逐个确认，2026-10-25 答「按你的倾向来」（见状态）。
    - 倾向：全按默认。要先看的一项是 Q3（`annotations`）：默认照单全收 server 声明的 `annotations`，所以一个 server 把破坏性的工具标成 `readOnlyHint`，在 `ask` 模式下就不会被问（规则见 `AGENTS.md` 的不变量 8：按声明决定要不要问）。备选：默认丢弃，让每个 MCP 工具都问；或按 server 设开关。另一项是 Q5：`zypi` 里没有配 server 的办法，所以 `zypi` 用户用不上这个包；备选是 `~/.pi-python/mcp.toml`，要先让 TUI 支持把它交给 agent，那会改 TUI，要另立计划并过 P2 准入。
    - 状态：**已定，不用再做**（用户，2026-10-25：「按你的倾向来」；r20）：Q1–Q11 全按默认，包括 Q3（照单全收 server 声明的 `annotations`）与 Q5（`zypi` 里没有配 server 的办法）。代码不改；设计文档 §10 加了一句话，说明用户已确认。`pi-mcp-py` 仍标 Alpha（这是成熟度，与默认值无关）。要改某一项时，设计文档 §10 的备选还在。
    - 详情：[§10.23][l10-23]

## 1. 目标与结论

**目标**：`zypi` 的 Rust 代码只承担终端 UI、ACP client / stdio transport、输入输出呈现和 UI 所需的本地服务。模型请求、agent turn、工具调度、session transcript、compaction、skills 与 system prompt 全部由 Python（`pi_agent_cli` → `pi_agent_harness` → `pi_agent_core`）负责。

剥离边界以**产品运行路径与依赖图**为准，不按 crate 名直接删除。

| 问题 | 结论 |
|---|---|
| 目标架构是否合理 | **合理。** 「Rust = ACP Client + UI，Python = ACP Agent + runtime」就是 Phase 4 已确立的边界，本计划只是把它落实为代码层面的瘦身。需要补的不是方向，而是四件事：把终态拆成「行为层 / 依赖图层」两层（§2.1）；补上协议所有权、能力驱动 UI、数据真源、安全边界等七条原则 P1–P7（§2.2）；`pi/` 扩展由「保留或迁移」改为**准入制**（P2、ADR4）；会话数据改为**纯 ACP**（ADR1）。 |
| 是否与现有架构冲突 | **方向不冲突**，有 2 处既有决定需要修订、3 处需要落实（§3）：支持面范围（原稿「保留或迁移」会重开 Phase 4 已划为非目标的范围）；Phase 4 §1.3 #2「不抽纯 pager crate」被部分取代；会话真源、`config.toml` 兼容、method-not-found 三项已有的口径需要落实到代码。Python 的 `pi_agent_core` / harness 与 AGENTS.md 的不变量不受影响。 |
| 改动如何 | **行为层**：删除约 14.5 万行 Rust（`pi-shell` 非测试代码的 43%、`pi-sampler`、pager 死代码），编辑 24 个存活的 `pi-shell` 单元；Python 只动 `pi_agent_cli` 的 ACP 投影。**依赖图层**（`async-openai` 离开 `cargo tree`）要拆 `pi-tools` / `pi-sampling-types` 的耦合，量级更大、收益待测，列为可选阶段 D。详见 [§5][l5]。 |

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
| 2 | 斜杠菜单只留 `/new` `/resume` `/quit` 与本地 chrome；`/model` 等无标准对照者拿掉（Phase 4 §1.2 / §4，DESIGN §8.3）——**r3：`/model` 有标准对照（Session Config Options），按用户决定恢复，见 ADR3**；`TUI-AND-CODE-AGENT.md`「What is NOT included」已声明移除 xAI 登录、`x.ai/*`、`agent` 子命令、`/loop`、`/share`、dashboard、auto-update、marketplace | 原稿「`pi/` 扩展保留或迁移」「补齐 MCP / queue / compaction / subagent」会**重开**已划出的范围。代码与文档也已不一致：白名单含 `model`，r2 时 Python 无法服务它（[§4.4][l4-4]；r3 已补齐）；dashboard 文档称已移除，`views/dashboard/` 仍在 | **需修订**：默认「降级 / 隐藏 / 删除」，新增能力须过 P2；`/model` 见 ADR3 |
| 3 | §1.3 #2「整树迁入 `tui/`，不抽纯 pager crate」；§3「第一轮允许 pager 继续链接 `xai-grok-shell`」 | 本计划即第二轮。#2 的理由（pager 依赖 shell / tools / agent / workspace）对 shell 的 runtime 部分不再成立，对 `pi-tools` / `pi-workspace` 仍成立 | **需修订**：在 Phase 4 spec 增补修订记录，标明 #2 被部分取代 |
| 4 | 会话真源 = AgentHarness JSONL v3，TUI 本地缓存不是真源（DESIGN §8.1） | 一致，但 pager 仍按 Rust 布局 `updates.jsonl` 读本地 session，而其写入方（Rust session actor）正是要删的部分 | **需落实**：ADR1 |
| 5 | `pi -p` = 纯 Python headless，不复用 grok headless（DESIGN §8.3） | 一致。原稿「`pi -p` 与 headless 委托 Python ACP」措辞不准：`-p` 是直接子进程、不走 ACP；真问题是旗标被静默丢弃、`--sandbox` 在派发之后才生效（[§4.1][l4-1]） | 一致；措辞更正，落实为 `-p` 旗标契约 |
| 6 | 配置分属：`agent.toml`（Python）/ `config.toml`（仅 Rust 的 grok 格式）（DESIGN §8.4） | 删除 `agent::config`（约 90 个字段）会影响既有用户的 `config.toml` | **需落实**：ADR5 |
| 7 | 「TUI 不得在 method-not-found 上 panic」（Phase 4 §4） | pager 会发送 Python 不路由的方法（`session/set_model`、`session/set_mode`） | **需落实**：阶段 1 + 契约测试 |
| 8 | AGENTS.md 不变量（事件契约、终止语义、StreamFn、Usage 等） | 不触及；`pi_agent_core` / harness 不改 | 一致 |

## 4. 源码事实基线（已归档）

r2 在动手之前核对了源码。它描述的是拆除之前的代码，不是现状。原文在[执行记录（归档）][log]的[附录 E][logE]。拆除后的规模见 [§10.1][l10-1]，残留见 [§10.4][l10-4]。

- [4.1 运行路径][l4-1]
- [4.2 Rust runtime 与耦合面][l4-2]
- [4.3 Python 侧][l4-3]
- [4.4 协议与版本][l4-4]
- [4.5 验证基础设施与文档][l4-5]

## 5. 改动规模（已归档）

r2 的静态估计：口径、各对象的行数、需要编辑的 cut point、第二梯队。原文在[附录 E][logE]的 [5. 改动规模][l5]。实际规模见 [§10.1][l10-1]。

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

每一项的行首是 `[x]`（做完）或 `[ ]`（没做完）。「状态」是该项的结果摘要，后面是执行记录的节号。每一项的完整注释（r10 版）在[执行记录][log]的[附录 C][logC]。

### 阶段 0：基线与门禁（先于任何删除，不改产品行为）

- [x] 0.1 **Rust CI job**：`cargo check -p pi-pager-bin` + 选定 crate 的 `cargo test`（Linux，ADR6；r18 起是 `cargo nextest`，见 [§10.21][l10-21]）。**状态**：r4 写好 `.github/workflows/tui-ci.yml`。r5 在 Linux 上跑通，门禁与测试都是阻塞的，`TUI CI` [run 37379203134][ci-run-r5] 全绿。记录 [§10.6][l10-6]、[§10.7][l10-7]。
- [x] 0.2 **基线报告**入库：测试通过 / 失败 / 忽略数、`cargo tree -p pi-pager-bin` 节点数、release 二进制体积、冷编译与启动耗时、去掉全局 allow 后的告警数。**状态**（r5）：Linux 一栏已回填，告警清零。`zypi` release 422,034,120 B，比拆除前小 10.3 %；冷缓存构建 1,155 s，比 1,647 s 快 29.9 %。报告在 [`docs/baselines/tui.md`](../baselines/tui.md)。记录 [§10.6][l10-6]、[§10.7][l10-7]。
- [x] 0.3 **ACP 契约 / e2e 载体**：Rust client ↔ Python mock agent，不依赖 PTY。**状态**：r5 做了 Python 一侧的 stdio 契约套件（见 1.P7）。r9 做了 Rust 一侧：`tui/crates/codegen/pi-pager/tests/python_agent_e2e.rs` 的 10 个用例，已接进 `tui-ci.yml`。记录 [§10.11][l10-11]。
- [x] 0.4 **deny-list**：硬性 `pi-sampler`；条件性 `async-openai`、`pi-sampling-types`（由阶段 D 决定）；对 `pi-tools`、`pi-agent`、`pi-workspace`、`pi-hooks`、`pi-mcp` 逐个写「保留理由或缩减方案」。**状态**（r4）：`pi-sampler` 与 `MvpAgent` / `acp_session_impl` / `SamplerActor` 三个词是 `tui_baseline.py gates` 的阻塞门禁。条件性 crate 的理由与缩减方案在 [`docs/baselines/tui.md`](../baselines/tui.md) §4。记录 [§10.6][l10-6]。
- [x] 0.5 **模块级调用图 → support 保留清单**：把附录 B.2 的脚本入库为工具，取代「举例」式清单。**状态**（r11）：工具是 `scripts/tui_callgraph.py`（`crates`、`units`、`keep` 三个子命令；只读源码文本，不要 cargo），39 个单测，对工具做 17 处改动、每一处都让单测失败。保留清单是它生成的 [`docs/baselines/tui-support.md`](../baselines/tui-support.md)：support 的 7 个 crate 共 305,581 行，报死的 17 个单元 6,190 行，其中 `pi-hooks` 占 4,339 行（48 %）。准确度用编译器查过：在副本里删掉这 17 个单元的文件，`cargo check` 只在一个单元上失败（`pi-tools` 的 `types::error`，里面是 `?` 要用的 `impl From<…> for ToolError`，报告把它标在「check first」里）；留下它则通过。第一版报了 21 个，其中 4 个是活的，已改工具。工具只到模块一级：没有名字的 `impl` 它看不见，活模块里的死函数它找不到。记录 [§10.14][l10-14]。
- [x] 0.6 **入口审计**：逐入口标注可达性、所需 ACP 方法、决策（保留｜隐藏｜删除）。**状态**（r5）：用户选了「直接按附录 A 默认执行，不逐项确认」；结果是 A.3 的删除。记录 [§10.7][l10-7]，以及[执行记录 A.4][logA4]。
- [x] 0.7 **磁盘读取点清单**与 ADR1 的落地设计。**状态**（r9）：pager 一侧有 13 处读盘调用，分布在 8 个文件；冷启动延迟已实测，没有触发 ADR1 的覆盖条件；13 例 PTY 验收矩阵在 `scripts/tui_pty/resume_matrix.py`；落地设计含沙箱顺序与 PR 切分。落地见 1.R1。记录 [§10.11][l10-11]。
- [x] 0.8 **能力矩阵定稿**（附录 A）与 **ACP 版本配对表**（两端各方法的支持情况、升级策略；Python 侧不得依赖 Rust 端 unstable 方法）。**状态**（r9）：A.1 逐行标了「已定」「提议」或「未定」；A.2 是版本配对；A.3 是逐方法的两端支持情况。记录 [§10.11][l10-11]。
- [ ] 0.9 **实测结案**：子进程是否继承沙箱；`zypi export <python-session-id>`；`/model` 经 `session/set_config_option` 的真机切换（r3 已用 OpenRouter 实测 Python 侧，见 [§10.3][l10-3]；TUI 全屏交互仍需 PTY 实测）；子进程 `stderr` 对全屏 TUI 的影响；退出后是否残留 Python / bash 进程；`config.toml` 的解析严格性。**状态**（r8）：Linux / WSL2 上六项都有了记录，记录 [§10.10][l10-10]。没做：沙箱的 Landlock 层（WSL 内核 5.10 没有 Landlock，要在内核 ≥ 5.13 的机器上做）；macOS 与 Windows 上对应各项。

**退出条件**：CI 绿且基线入库；矩阵、入口审计、deny-list 无「未决」行（有则标 owner 与日期）；0.9 每项有记录。

### 阶段 A：死代码先行（不依赖 Python，零行为变化）

- [x] A.1 删除 8 个无入口模块（4.6k 行）和 `headless.rs` + `headless/`（约 7.5k 行），以及 `spawn_agent_thread_direct`、`warm_async_http_client()` 和 `main.rs` 里相关的代码。**状态**：r3。记录 [§10.1][l10-1]。
- [x] A.2 leader 全链路：pager 侧 `--leader` / `--no-leader` / `--leader-socket`、`[cli].use_leader`、`resolve_leader_mode`、`connect_via_leader`、`acp/leader_bridge.rs` 等；shell 侧 `leader/`（8.7k 行）与 5 个 `test_leader_*.rs`；内嵌文档中的 leader 段落。**状态**：r3 做了主体，r4 清掉 pager 的 UI 状态残留。仍带 leader 字样的代码是别的东西，见 [§10.4][l10-4]、[§10.6][l10-6]。
- [x] A.3 白名单外的斜杠命令模块；入口审计判为「删除」的入口（dashboard、agents / extensions / persona 模态、tasks / 后台 / 定时任务、subagents / workflows / goals、共享 prompt 队列与 steer、MCP、hooks / plugins / marketplace、rewind / fork / jump / 外部会话、recap / feedback / consent 等）；session rename 的死链路与恒假的 `chat_mode` 世界。**状态**：r5。有意没做：认证 / 计费界面（归阶段 D.3）；CLI 旗标瘦身（`--worktree`、`--restore-code` 等）；plan 审批 / btw / cta。记录 [§10.7][l10-7]。
- [x] A.4 去掉四个 crate 根的 `#![allow(unused_imports, …, dead_code)]` 并清零告警；死 `pub mod` 降为 `pub(crate)`（pi-shell）；未用依赖与无人依赖的 crate；`local-workspace` 的 Cargo feature 与代码。**状态**：r5。基线 `max_warnings` 20 → 0。其余 crate 的 `pub` 瘦身没有做。记录 [§10.7][l10-7]。

**退出条件（机检）**：`rg -w MvpAgent tui/crates/codegen/pi-pager tui/crates/codegen/pi-pager-bin` 无命中；无全局 allow 的 `cargo check -p pi-pager-bin` 零告警；测试与基线对比不退化（被删代码的测试除外）；CI 绿。（r5 对照：四项都满足。记录 [§10.7][l10-7]）

### 阶段 1：协议与 Python（依赖 Python）

Python（`pi_agent_cli`）：

- [x] 1.P1 `list_sessions`：真实 title、`updated_at`（最后活动）、`cursor` 分页（ACP `session/list` 字段，[RFD](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/docs/rfds/session-list.mdx)）。**状态**（r5）：`title` = 会话第一条 user message，`updated_at` = 会话文件 mtime。r9：pager 跟 `nextCursor`，Python 每页 50 条；1,000 个会话时首页 2.0 s → 0.11 s。记录 [§10.7][l10-7]、[§10.11][l10-11]。
- [x] 1.P2 优雅退出：处理 stdin EOF / SIGTERM，回收工具进程组。**状态**（r5）：SIGTERM 与 SIGHUP 接到 EOF 的同一条退出路径。POSIX 上生效；Windows 只靠 EOF。契约在 `tests/test_acp_shutdown.py`。记录 [§10.7][l10-7]。
- [ ] 1.P3 `set_session_mode`（或 `configOptions` 的 `mode`）取代仅靠 `ext_notification` 的权限模式切换，映射由矩阵定。**状态**（r10）：先做了不碰 agent 的 (a)：pager 只提供 agent 真正会做的两个模式（PR [#12][pr-12]，记录 [§10.13][l10-13]）。`set_session_mode` 本身没做，所以不勾。待决策 7。
- [x] 1.P4 `mcp_servers`：**显式忽略并在文档声明**（Phase 4 非目标），或实现。**状态**（r9）：做了前者。pager 不再发 `mcp_servers`；Python 收到非空列表时记一条警告，并发一条只含名字的 agent 消息。`packages/pi-agent-cli/AGENTS.md` 与 `docs/TUI-AND-CODE-AGENT.md` 写明这是对 ACP「agent 必须支持 stdio 服务器」的有意偏离。记录 [§10.11][l10-11]。**r20 的更正与后续：**「Phase 4 非目标」的说法不准确：Phase 4 的文档没有讨论 MCP，决定来自 Phase 6（`AGENTS.md` 与 `CHANGELOG.md` 在 [#35][pr-35] 里更正了）。用户在 2026-10-23 选了可选扩展包（决策 19）：装了 `pi-mcp-py`，agent 连接客户端传来的 server；没装，行为与 r9 一样（忽略并说明，提示的文字改了）。记录 [§10.23][l10-23]。
- [ ] 1.P5 `initialize` 的 capabilities / `_meta`：只公布 pager 实际需要且已登记（P2）的键。**状态**：r5 做了一部分：`initialize` 公布了 `session/close` 与 `session/resume`，但 `run_agent` 没开 `use_unstable_protocol`，SDK 对这两个方法回「Method not found」，已改。其余公布项与 pager 需求的对照没做。
- [ ] 1.P6 `-p` 旗标契约：以 `__main__` 接受的旗标为权威；每个 `PagerArgs` 旗标逐项「支持｜非 0 退出并报错｜文档删除」。**状态**：没做。待决策 8。
- [x] 1.P7 契约测试：mock `stream_fn` 驱动 ACP stdio，断言方法面（含既有的「不注册 `x.ai/`」）。**状态**（r5）：`packages/pi-agent-cli/tests/test_acp_stdio_contract.py` 起子进程走 stdio，8 个用例。r9：补上权限往返与 cancel 的线上用例（`test_acp_stdio_tools.py`）。记录 [§10.7][l10-7]、[§10.11][l10-11]。

Rust（pager）：

- [x] 1.R1 ADR1 落地：移除对 session 文件的读取；`export` 的去向。**状态**（r9）：读盘调用点清单、落地设计、PR 切分、验收矩阵都在 [§10.11][l10-11]。r10 只做了 PR 1，即 Python 一侧的 `_meta.sessionId` 与 `pi/cwd`（PR [#11][pr-11]，记录 [§10.12][l10-12]）。r13：用户答复了决策 2、4、5、7，都按倾向；PR 2（Rust，只加不删，[#23][pr-23]）与 PR 3（Rust，删，[#24][pr-24]）做成了，r14 合入 `main`（决策 6 的 PR 已合入，r12 续 6、续 8：`export_cmd` 不再引用 `pi_shell::session::storage`）。pager 不再读会话文件，`gates.session_disk_reads` 守着；`resume_matrix.py --strict` 13/13（r14 按决策 17 加了 3 例，16/16）。r15：删了 `pi-shell` 里的旧存储层（决策 10 的存储层一项，PR [#26][pr-26]，r16 合入）。记录 [§10.16][l10-16]、[§10.17][l10-17]、[§10.18][l10-18]。
- [ ] 1.R2 按能力隐藏 Python 不路由的方法的入口（`session/set_model`、`session/set_mode` 等）；method-not-found 不 panic、不重复报错；`/model` 按 ADR3 恢复（r3 已做，不在隐藏之列）。
- [ ] 1.R3 子进程生命周期（P6）。**状态**：做了大半。r4：命名与提示语。r5：先关 agent 的 stdin 再杀；做了 macOS 的退出实测。r8：Linux 的退出矩阵；沙箱下补了 `--die-with-parent`；agent 的 `stderr` 落到 `<home>/logs/agent.stderr.log`；`-p` 模式下 Python 一侧监视父进程。r9：agent 自己退出时，pager 说得出原因。没做：Windows 上的行为；MCP 子进程（r20：`pi-mcp-py` 起的 MCP 子进程，由扩展在会话结束时结束，测过 `close_session` 之后没有留下进程；agent 被强杀或 `zypi` 退出时它们的下场没有测）。记录 [§10.6][l10-6]、[§10.7][l10-7]、[§10.10][l10-10]、[§10.11][l10-11]。
- [ ] 1.R4 `-p` 派发顺序与沙箱（ADR7）。
- [ ] 1.R5 解码精简：`initialize._meta` 只读 Python 实际公布的键；`pi/*` 解码按 ADR4 清理。

**退出条件**：ACP 契约 / e2e 通过；矩阵无「未决」行；`-p` 每个旗标都有去向；`zypi` 正常退出、崩溃、Ctrl-C 之后无残留 Python 与 bash 子进程（Linux、macOS 实测，Windows 记录现状）。（r8 对照：Linux、macOS 的实测都有了，Linux 见 [§10.10][l10-10]；Windows 仍未测。）

### 阶段 B：拆除 Rust runtime（行为层收口）

- [x] B.1 迁出 `OriginClientInfo`，使 `pi-telemetry` / `pi-http` 不再依赖 `pi-sampler`。**状态**（r3）：`OriginClientInfo` 在 `pi-telemetry/src/http.rs`。
- [ ] B.2 把 pager 用到的 18 个 `sampling` 符号迁到不依赖 provider 栈的叶子 crate：对话项类型（`ConversationItem` / `UserItem` / `AssistantItem`）、reasoning-effort 元数据（`ReasoningEffort`、`ReasoningEffortOption`、`REASONING_EFFORT_META_KEY`、`parse_*`、`supports_reasoning_effort_meta` 等）、错误分类与文案（`RATE_LIMITED_ERROR_CODE`、`error_detail_from_data`、`format_rate_limited_user_message`、`http_status_from_error`、`is_free_usage_exhausted_error`、`prompt_usage_from_error`、`stop_reason_for_turn_error`）。**状态**：r3 换了做法，没有新建叶子 crate：`pi_shell::sampling` 留作薄壳，符号仍从 `pi-sampling-types` 重导出。provider 栈没有因此离开依赖图，见阶段 D。
- [ ] B.3 ACP 扩展 wire 类型（`extensions::notification` / `session_search` 等）迁入 `pi-acp-lib` 或同级协议 crate（P2）。**状态**：r3 没做。
- [ ] B.4 从 `agent::config` 抽出 pager 实际使用的部分（含 `SandboxSettingsConfig`、`grok_com_config`），其余字段按 ADR5 废弃。**状态**：r3 做了一部分：`agent::config` 就地瘦身并接收了 `AuthScheme`，没有抽成独立模块。ADR5 的首次启动告警没做。
- [x] B.5 处理 [§5][l5] 列出的 24 个 cut point 里对 runtime / provider 栈的引用。**状态**（r3）：编译器验证。
- [x] B.6 删除 94 个单元（113.0k 行）与 `pi-sampler` crate；清理依赖声明、feature、生成配置、测试 harness 与遗留名称。**状态**：r3。实际规模与「94 个单元 / 113.0k 行」的静态估计不同，见 [§10.1][l10-1]。

**PR 切分**：B.1–B.4 是纯迁移（行为不变）先合；B.5 次之；B.6 的大删除最后。

**退出条件（机检）**：`rg -w 'MvpAgent|acp_session_impl|SamplerActor' tui/crates` 无命中；`cargo tree -p pi-pager-bin -i pi-sampler` 返回「did not match any packages」；`cargo check -p pi-pager-bin` 零告警；测试与基线对比不退化；CI 绿；release 二进制体积不高于基线。（r3 对照：前两项 ✓；零告警 ✗；基线、CI、release 体积 ✗，没有基线可比，也没有测。r5 起零告警、基线、CI、release 体积都有了数据，见 0.1、0.2、A.4。）

### 阶段 C：验收与文档收口

- [x] C.1 运行时验收：阶段 0.3 的 mock-ACP e2e 全绿；一次可选的真实模型 smoke，用进程级网络连接核对 `zypi` 进程无到 LLM provider 的外连。**状态**（r11，Linux / WSL2）：e2e（`python_agent_e2e`，`PI_E2E_REQUIRED=1`）全绿。网络审计 `scripts/tui_pty/net_audit.py` 在 PTY 里跑一轮对话，查 13 项：mock LLM、无网络的命名空间、真模型一次，都是 13/13；`zypi` 没有到非本机地址的连接，也没有监听（agent 有到两个远端地址的连接，这是正向对照）。记录 [§10.14][l10-14]。
- [x] C.2 内嵌 user-guide 裁剪或重写（同步 `status_line_tests.rs` 夹具），并停止每次启动的 `extract_user_guide_docs`。**状态**（r11）：`pi-pager/docs/` 的 33 个文件删了 25 个，剩 `user-guide/` 的 7 页加一份 README（入门、斜杠命令、主题、会话、终端支持、权限与安全、状态行），按代码和 `agent.toml` 重写；教程叠层与 `extract_user_guide_docs` 删了，启动不再往 home 写文档。`pi-pager` 的测试 5,626 → 5,608。记录 [§10.14][l10-14]。
- [x] C.3 `tui/NOTICE`（产品二进制名、声明 `tui/` 为 hard fork、不再跟随 upstream）；`tui/THIRD-PARTY-NOTICES`（18,898 行，含 `async-openai`）在依赖变化后重新生成并记录方式。**状态**（r11）：`NOTICE` 重写：产品是 `zypi`，`tui/` 是 hard fork、不再跟随 upstream。`THIRD-PARTY-NOTICES` 不是整份重新生成：`scripts/tui_notices.py` 取 `pi-pager-bin` 的依赖闭包里会进二进制的包，与文件对账（去掉 222 条过期的，补上 44 条缺的，手写的段落保留；文件从 18,898 行变成 16,548 行）；`check` 不一致就退出 1，`update --write` 来改。`check` 还没有接进 CI（它要 cargo；接上要改 `tui-ci.yml`）。记录 [§10.14][l10-14]。
- [x] C.4 `packages/pi-agent-cli/AGENTS.md` 的 config 约束；`docs/TUI-AND-CODE-AGENT.md`（MCP 归属、ACP 链接应为 [agentclientprotocol.com](https://agentclientprotocol.com)）；Phase 4 spec 增补修订记录（§1.3 #2）；DESIGN §8 增补 P2 准入规则。**状态**（r11）：四项都做了：`pi-agent-cli/AGENTS.md` 写了实测的 `config.toml` 约束；`TUI-AND-CODE-AGENT.md` 改了 ACP 链接、`[model]` 的真实键、斜杠命令，并写明 zypi 里没有 MCP 客户端；Phase 4 spec 的 §1.3 #2 与 §3 加了修订说明；DESIGN §8.5 写了 `pi/` 扩展的五条准入条件。另外清掉 `grok agent` 的 13 处过时指针。记录 [§10.14][l10-14]。

**退出条件**：`grok agent`、`leader`、`x.ai`、`MvpAgent` 在文档与代码中的 grep 结果为 0（历史文档除外）。**状态**：`MvpAgent` 与 `grok agent` 为 0（只有门禁自己写出这两个词的地方除外）。`leader`、`x.ai` 清不到 0：这些行在活着的代码里，是已删除的 leader 进程的名字、xAI 的服务地址，以及「不实现 `x.ai/*`」的过滤本身。所以改成棘轮：`tui_baseline.py` 的 `[gates.residue]` 给每个词一个上限，只降不升，`pytest` 也查（现在 `leader` 243、`x.ai` 297）；每一类是什么、去哪里，见 [`tui.md` §4](../baselines/tui.md)。

### 阶段 D（可选，决策门控）：依赖图与 support 层瘦身

入口条件：阶段 B 完成，且基线对比显示编译时间 / 体积 / 维护成本值得继续。

- [ ] D.1 `async-openai` 离开 `cargo tree`：需处理 `pi-tools`（139k 行，pager 仅用约 55 个类型 / 小工具路径，但渲染 crate 也依赖它）与 `pi-sampling-types`（经 `pi-agent`）。二选一：把「工具展示类型」抽到叶子 crate（`pi-tools-api` 仅 715 行，是现成落点），或对 `async-openai` 的用法做 feature-gate。
- [ ] D.2 session 数据层（`session::{storage,persistence,unified_list}`、`relay`，约 16.6k 行）。**状态**（r15）：做了一部分：`session::storage` 与 `session::persistence` 删了（决策 10，PR [#26][pr-26]，r16 合入；清洗标题的两个函数留在 `session::title`）。`session::unified_list`（103 行）还在，调用图报死；`relay` 这一轮没有查。记录 [§10.18][l10-18]。
- [ ] D.3 xAI auth 层（约 18.1k 行）。
- [ ] D.4 视 `pi-workspace`（101k 行，pager 主要用 `permission`）、`pi-agent`（`agents_modal` 删除后几乎无活引用）的剩余耦合决定是否继续。
- [ ] D.5 `pi-test-support`（7,384 行）：外部只有 `EnvGuard`、`scaled`、`resources`（`pi-shell`、`pi-tools` 的测试），和一个 `pi-pager` 测试里的 `TestProcess`、`TestSandbox` 在用；mock 推理服务器、SSE 生成器、ACP stdio 客户端、headless 运行器、计数服务器与脚本化响应共 5,003 行没有外部用户（模块之间的相互引用没有查）。出处：r11 的搜索，[§10.14][l10-14]。
- [ ] D.6 内嵌指南的查看器：`ActiveModal::DocPicker` 与 `DocViewer` 没有入口（非测试代码里，`howto_list_modal` 只被查看器的 Esc 调用，`welcome_doc_viewer` 只在测试里被赋值），启动时的解压又在 C.2 删了，所以七页指南现在没有读者。二选一：接一个入口（斜杠命令或快捷键），或连同选择器、查看器一起删。出处：[§10.14][l10-14]。
- [ ] D.7 发行与更新的遗留：`pi-pager/npm/` 下 7 个打包目录与 `pi-update` 里还写着 npm 包名 `@pi-official/grok` 和 `x.ai/cli/install.*` 的安装提示。自动更新在 Phase 4 §4.4 已禁用（`pi-pager-bin/src/main.rs` 里 `bg_update_rx = None`），这些不会跑，只是遗留。出处：[§10.14][l10-14]。
- [ ] D.8 删掉调用图工具报死、编译器也认可的 16 个模块（被删文件共 6,143 行）：`pi-hooks` 的 `dispatcher`、`result`、`runner`、`runner::command`、`runner::http`（4,339 行）；`pi-shell` 的 `session::events`、`session::telemetry`、`session::unified_list`、`config::reloader`、`auth::flow`、`upload`、`upload::turn`（1,006 行）；`pi-workspace` 的 `publish`（462 行）；`pi-tools` 的 `implementations::editor_infra`（276 行）；`pi-mcp` 的 `wire`（39 行）；`pi-agent` 的 `prompt::browser_verification`（24 行）。第 17 个报死的模块，`pi-tools` 的 `types::error`（44 行），**不能删**：里面是 `?` 要用的 `impl From<…> for ToolError`。要一起删的还有 `pi-hooks/tests/integration.rs`（822 行，测的就是被删的模块），以及删完没人用的 `env_expand::expand_env_vars_with_extra`（`pi-hooks`）。证据：在副本里这样删，三个产品 crate 的 `cargo check` 与 `cargo check --workspace --tests` 都通过，只多那一条告警。没有真的删；副本里没有跑测试，所以不知道会少几个测试。`session::events`、`session::unified_list` 与 D.2 有重叠。出处：[§10.14][l10-14]。 **状态**（r16）：PR [#27][pr-27] 照这一条做了一部分：`session::helpers`、`session::worktree` 与 `util::config::worktree` 删了。现在 `pi-shell` 里调用图报死的单元是 7 个（1,008 行）：`auth::flow`、`config::reloader`、`session::events`、`session::telemetry`、`session::unified_list`、`upload`、`upload::turn`；`session::events`（591 行）在「check first」里（`GoalClassifierVerdictTelemetry` 的 `impl`）。r16 的扫描又找到几个候选，都没有删，只有文本搜索的证据：没有用户的 crate `pi-workflow`（9 个文件，3,570 行；`tui/Cargo.toml` 的成员列表里有它，没有 `Cargo.toml` 依赖它）；`Config.storage_mode` 与隐藏的 `--storage-mode`（在 `.rs` 里没有找到读 `Config.storage_mode` 的代码）；约 88 个没人加载的文件。要删，就先在副本里删、再编译：rustc 不告警（`pub(crate)` 项在死模块里不告警）。清单见 [§10.19][l10-19]。
- [ ] D.9 `tui_baseline.py test` 在开发者自己的 `HOME` 下跑测试（r18 起用 `cargo nextest`，以前是 `cargo test`；这一条不变），有测试会往真实的 `~/.pi-python` 与 `/tmp` 写东西（`trusted_folders.toml`、`config.toml`、`worktrees.db`、`/tmp/grok-unified-log-test-*` 等）。脚本只清了 `PI_HOME`、`GROK_HOME`（好让测试自己设的 `GROK_HOME` 生效），没有隔离 `HOME`。具体是哪个测试写的，大多没有查；`worktrees.db` 的一个来源（`session::worktree` 的两个测试）随 r16 的删除没有了。办法：测试运行时把 `HOME` 设成临时目录，并先钉住 `CARGO_HOME` 与 `RUSTUP_HOME`（不然 cargo 找不到工具链）；CI 的 runner 本来就是空 home，这样本地与 CI 更接近。没有做：要把整套测试重跑一遍（本次 22 m）才能验证，而且它改的脚本 TUI CI 也在用。出处：r11 收尾时的检查，[§10.14][l10-14]；[§10.18][l10-18]。

## 8. 风险与处理

| # | 风险 | 处理方式 |
|---|---|---|
| R1 | `pi-shell` 是 runtime 与 TUI service 的大杂烩 | 模块级闭包 + cut point 清单；纯迁移 PR 先于大删除（阶段 B 的 PR 切分） |
| R2 | 没有 Rust 回归安全网，验收全部依赖它 | 阶段 0 的 CI、基线、ACP 契约测试先于任何删除 |
| R3 | 残留 UI 入口调用已删 runtime，或发送 Python 不路由的方法 | 入口审计 + 能力驱动 UI（P3）+ method-not-found 契约测试；去掉全局 allow 后由编译器验证 |
| R4 | 误把有入口的代码当死代码 | 入口审计 + A.4 去 allow + e2e；每类删除单独 PR |
| R5 | 子进程生命周期：孤儿 bash 进程、Windows 行为未知 | 阶段 1 的优雅退出协议 + 实测；Windows 先记录现状。macOS（r5）与 Linux（r8）已实测并修掉残留进程，见 [§10.7][l10-7]、[§10.10][l10-10]；Windows 仍未处理 |
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
- 新增 TUI 功能（MCP、queue、subagent UI 等）——另立计划并过 P2 准入。（`/model` 不在此列：r3 已按 ADR3 经 Session Config Options 恢复。MCP 也不在此列：r20 把 Python 一侧的 MCP 做成了可选扩展包，那不是 TUI 功能，见决策 19；TUI 里仍没有 MCP 界面。）
- 多会话共享进程（leader 的替代方案）。

## 10. 执行记录（已归档）

执行记录在 [`archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md`][log]。下表的 §10.x 是那个文件里的节，编号沿用原文。记新的一轮时：在那个文件末尾追加一节，在本表加一行，在 §7 和「当前状态」里改状态。本文不写过程。

| 节 | 轮 | 内容 | PR |
|---|---|---|---|
| [§10.1][l10-1] | r3 | 范围、方法与规模：拆除 `pi-sampler` 与 `pi-shell` 的 runtime 单元（682 个文件，Rust 行数 −22.6 %） | [#7][pr-7] |
| [§10.2][l10-2] | r3 | `/model` 恢复（ADR3）：Session Config Options | [#7][pr-7] |
| [§10.3][l10-3] | r3 | 验证：构建、测试、真实 LLM 的协议层与 PTY（macOS） | [#7][pr-7] |
| [§10.4][l10-4] | r3、r4 | 未验证与残留：已知失败、残留、建议的顺序 | [#7][pr-7] |
| [§10.5][l10-5] | r3 | 为 pi-rust 铺路：ACP 是唯一接缝，接入点，保留的 crate | [#7][pr-7] |
| [§10.6][l10-6] | r4 | 阶段 0 的 CI 与基线，leader 残留，1.R3 的命名 | [#7][pr-7] |
| [§10.7][l10-7] | r5 | Linux CI，入口审计，阶段 A 收口 | [#7][pr-7] |
| [§10.8][l10-8] | r6 | 品牌残留清理 | [#8][pr-8] |
| [§10.9][l10-9] | r7 | 合并 `main`（另一台机器推的 7 个提交） | [#7][pr-7]、[#8][pr-8] |
| [§10.10][l10-10] | r8 | Linux 补测（WSL2）：退出矩阵，0.9，CI 没跑的 Rust 测试 | [#9][pr-9] |
| [§10.11][l10-11] | r9 | 阶段 0 收口（0.3、0.7、0.8）与阶段 1 里不需要拍板的部分；末尾是十二项待决策的原文 | [#10][pr-10] |
| [§10.12][l10-12] | r10 | ADR1 的 PR 1（Python 一侧） | [#11][pr-11] |
| [§10.13][l10-13] | r10 | 1.P3 的 (a)：`zypi` 只提供 agent 会照做的权限模式 | [#12][pr-12] |
| [§10.14][l10-14] | r11 | 0.5 调用图工具化与阶段 C 收口：网络审计（C.1）、内嵌指南（C.2）、NOTICE（C.3）、文档与残留词门禁（C.4） | [#13][pr-13] |
| [§10.15][l10-15] | r12 | V0.5 的发布里程碑：范围与退出条件的依据、`GROK_VERSION` 的发现（决策 16）、决策 6 与 15 的两个 PR、合入、打 tag 与发布（续 1–续 8） | PR [#16][pr-16]、[#17][pr-17]、[#18][pr-18]、[#19][pr-19]、[#20][pr-20]、[#21][pr-21] |
| [§10.16][l10-16] | r13 | ADR1 的 PR 2 与 PR 3：`resolve_startup`、读盘代码的删除、新门禁；决策 2、4、5、7 的答复；13 处读盘调用点的去向 | PR [#23][pr-23]、[#24][pr-24] |
| [§10.17][l10-17] | r14 | #23、#24 合入 `main`；决策 17：会话文件在第一条消息时才写（Python 一侧，PTY 恢复矩阵加 3 例）；决策 18（不做）与决策 10（做）的答复 | PR [#25][pr-25] |
| [§10.18][l10-18] | r15 | 决策 10 的存储层一项：删 `pi-shell` 的 `session::{storage, persistence}` 与只有它们在用的代码（编译器的 41 处告警），`session::title`，数字、门禁与验证 | PR [#26][pr-26] |
| [§10.19][l10-19] | r16 | 合入 #25 与 #26；删 `pi-shell` 里剩下的死代码（没人加载的文件、`session::worktree` 与 `session::helpers`、`[storage]`、10 个没点名的依赖）；数字、门禁、验证；D.8 的新候选 | PR [#27][pr-27] |
| [§10.20][l10-20] | r17 | 检查测试之间的竞争（只改测试与工具）：环境变量探针与压力脚本；19 个偶发失败的测试与 5 个不还原环境变量的测试；进程级 `static` 的 6 族与 `in_own_process`（57 个测试）；`cargo nextest` 的对照；`protoc` 重新构建的问题 | PR [#28][pr-28] |
| [§10.21][l10-21] | r18 | `release.yml` 里 `setup-protoc` 的令牌（#29）；`protoc` 的绝对路径，和 r17 一句话的更正（#30）；`TUI CI` 与基线执行器改用 `cargo nextest`（#31） | PR [#29][pr-29]、[#30][pr-30]、[#31][pr-31] |
| [§10.22][l10-22] | r19 | 用 `gh stack` 合入 #27–#31；V0.6 的发布里程碑；开发暂停 | PR [#27][pr-27]–[#31][pr-31]、[v0.6.0][ms-v060] |
| [§10.23][l10-23] | r20 | MCP 的方向（可选扩展包，决策 19）与两个问题的原因；#34 空白对话再打开、#35 给扩展的挂钩、#36 `pi-mcp-py`；用 `gh stack` 合入 #33–#36；决策 20、21 | PR [#33][pr-33]–[#36][pr-36] |
| [§10.24][l10-24] | r21 | V0.6 的条件 5（#39）；合入 #38 与 #39；条件 1–4 的结果（`mode_probe` 的探针竞争与 #40） | PR [#39][pr-39]、[#40][pr-40] |
| [§10.25][l10-25] | r22 | 打 tag `v0.6.0`（`5f73e07`）、`Release` 的运行、PyPI 与 GitHub Release、两个二进制、PTY 脚本与 `pi-mcp` 测试的核对、合入 #40；留下的与没验证的；续 1（#42、glibc 的下限、删本地分支） | PR [#40][pr-40]、[#41][pr-41]、[#42][pr-42] |
| [§10.26][l10-26] | r23 | 整理计划的分支 squash 进本地 `main`，删三个分支；#43：三个 action 升到 Node 24 的版本，`Release` 的演练；#44：扩展 API 的审计批 9（F7-02 至 F7-09），测试与变异检查；决策 11、13 记成不做；`docs/PLAN` 的 Cursor 规则；续 1：推送 `main`，合入 #43 与 #44，`main` 上的 `CI` 与 `TUI CI`；续 2：用户答复 Zed 里的核对通过 | PR [#43][pr-43]、[#44][pr-44] |
| [附录 A.4][logA4] | r5 | 入口审计的 r5 执行结果 | [#7][pr-7] |
| [附录 B][logB] | r2–r17 | 复现方法（依赖图、模块级闭包、各轮的测量与测试命令） | — |
| [附录 C][logC] | r10 | §7 的逐项注释（原文） | — |
| [附录 D][logD] | r10 | 计划头部的状态文字（原文） | — |
| [附录 E][logE] | r2 | §4、§5 的原文（源码事实基线与改动规模估计） | — |
| [附录 F][logF] | r22 续 1 | 计划整理前的状态文字（原文）：头部、「当前状态」逐轮、两个里程碑、「下一步」，「还没有验证的」「待决策」「§7」「§10 索引」里改短的条目，「修订记录（摘要）」的长条目 | — |
| [修订记录][logRev] | r1–r23 | 逐轮的完整说明 | — |

## 附录 A：能力矩阵与 ACP 版本配对（阶段 0.8，r9 定稿）

「状态」一列：**已定**＝按 r2 的默认决策做完，或有实测支撑；**提议**＝会改变行为，列在本文的「待决策」里，等用户确认；**未定**＝阶段 1 的待办，r9 没有动。r5 对初稿的执行结果在[执行记录 A.4][logA4]。测试名与脚本名都在仓库里能搜到。

### A.1 能力矩阵

| 能力 | pager 侧 | Python 现状（r9 实测） | 决定 | 状态 | 证据 |
|---|---|---|---|---|---|
| session new / load / resume / close | 标准 ACP；pager 只调用 new / load，没有 resume / close 的调用点 | 已覆盖：`new_session`、`load_session`、`resume_session`、`close_session`；`initialize` 公布 `load_session` 与 `session_capabilities.{list, resume, close}`；`run_agent(use_unstable_protocol=True)` 让 unstable 的 `close` / `resume` 在线上可用 | 保留 | 已定（r5） | `test_acp_stdio_contract.py`；`connect_runs_the_handshake_and_stop_leaves_nothing_behind` |
| session list | `ListSessionsRequest{cwd, cursor}`；选择器跟 `nextCursor`，最多 100 页 | 已覆盖：每页 50，不透明无状态游标，非法游标 `invalid_params`；`title` = 第一条 user message，`updated_at` = 文件 mtime；顺序是创建时间倒序 | 保留；「最近」的语义见决定 4，30 天截止见决定 9 | 已定（1.P1，r9） | `test_session_list_paging.py`；`session_list_pages_are_followed_by_cursor`；`session_list_bench.py` |
| session delete | `pi/session/delete`（扩展请求） | 已覆盖（`ext_method`）；SDK 0.12.1 没有标准的 `session/delete` 路由 | 保留并登记（ADR4）；SDK 补路由后迁到标准方法 | 已定 | `test_session_lifecycle.py` |
| session rename | 入口与整条链路已删 | 无 | 删 | 已定（r5） | [执行记录 A.4][logA4] |
| 恢复的入口：`--continue`、`--resume <id｜标题>` | 经 ACP 解析：agent 起来之后 `session/list`（翻页）加 `session/load`，pager 不读会话文件（r13，PR [#23][pr-23]、[#24][pr-24]；以前读盘，13 处读盘调用，[§10.11][l10-11]） | `session/list` 加 `session/load` 够用；延迟实测没有触发 ADR1 的覆盖条件 | 经 ACP 解析（ADR1） | **已做**（r13；决定 2、4、5 按倾向，见 [§10.16][l10-16]） | `resume_matrix.py --strict`（13/13 PASS；r14：16/16）；`session_list_bench.py`；`tui_baseline.py gates` 的 `session_disk_reads` |
| `zypi export` | 经 ACP 回放：起 agent、`session/load`、收 `session/update`（PR [#18][pr-18]，r12 续 6，续 8 合入；之前读旧布局的会话文件） | 没有专门的方法，用已有的 `session/load` 回放 | 决定 6 的 (a)：经 ACP 回放 | **已做 (a)，已合入**（r12 续 5、续 6、续 8；PR [#18][pr-18]） | `resume_matrix.py` 用例 12、13；`export_cmd::tests`；`python_agent_e2e` |
| prompt / cancel | 标准 ACP | 已覆盖；取消后正在跑的 `bash` 进程被回收（Linux 的用例） | 保留 | 已定 | `a_prompt_streams_the_reply_and_ends_the_turn`；`cancel_ends_a_running_tool_as_cancelled_and_reaps_it` |
| 权限请求 | 应答 `session/request_permission`；yolo 模式下 pager 自己代答（选 `AllowOnce`，`acp_handler/permissions.rs`） | 已覆盖：`ask` 模式下问 `bash` / `edit` / `write` / `workflow`（没声明无害的工具，AGENTS.md 不变量 8）；工具调用的选项只有 allow-once / reject-once（`permissions.py`）；项目信任的问题另带 `allow_always`（`trust_prompt.py`） | 保留；工具调用不做 allow-always 与规则 | 已定：pager 不依赖 allow-always（PTY 里两个选项的问题框能画、能答）；提议：不做 | `a_permission_question_reaches_the_client_and_its_answer_decides`；`mode_probe.py` |
| 模型选择 | 读 `configOptions`（`category: "model"`），`session/set_config_option`；没有该配置项时退回 `session/set_model` | `configOptions`（`model`）与 `set_config_option`（只认 `model`，其他 `invalid_params`）；`[[models]]` 提供候选；`session/set_model` 没有路由，但 Python 总是公布 `model` 配置项，退回路径用不到 | 恢复（ADR3） | 已定（r3） | [§10.2][l10-2]、[§10.3][l10-3]；`test_acp_agent.py` |
| 模式切换 | **r10 起**：Shift+Tab 在 Normal ↔ Always-Approve 之间切（r9 时是 Normal → Always-Approve → Normal → Plan → Auto）、`--always-approve`、`--permission-mode default｜bypassPermissions`（r9 时六个值）；`pi/yolo_mode_changed` 通知；`session/new` 的 `_meta.yoloMode`（`autoMode` 不再为真）。Plan / Auto / 设置页的 Default 藏在 `app/agent_modes.rs` 的三个常量后面，`session/set_mode` 不再发出（[§10.13][l10-13]） | 只认 `pi/yolo_mode_changed` 里的 `ask` / `auto` / `always-approve`；`session/set_mode` → `method_not_found`；`_meta` 不读；`auto` 等于 `always-approve`；`default` 被丢弃 | 隐藏 Plan / Auto / Default；`--permission-mode` 只收有效的值；做不做真的 Plan 另议（决定 7） | (a) **已做**（r10，PR [#12][pr-12]）；真的 Plan（`session/set_mode` 加只读工具集）与真的 Auto 是提议（决定 7 的 (b)、(c)），Python 一侧的行为没变 | `mode_probe.py`（[§10.13][l10-13] 的表是改后的；改前的 12 行表在 [§10.11][l10-11]）；`agent_modes::tests` |
| MCP | UI 已删（r5）；不再发 `mcp_servers`（`7becb49`）；r20 之后仍是这样：`zypi` 不传 server | 忽略 `mcp_servers`，会话开始时说一次哪些被忽略（`mcp_notice.py`）。**r20**：装了 `pi-mcp-py`，它连接这些 server；没装，仍是忽略并说明，提示的文字改了（只写没人接手的 server，并告诉用户装这个包） | 降级为显式忽略；ACP v1 要求 agent 支持 stdio MCP，这是有文档的偏离。**r20**：用户选了可选扩展包（决策 19），连接客户端传来的 server；core 与 CLI 仍不内置 MCP | 已定（r9：默认决策，忽略并说明；r20：用户在 2026-10-23 选了可选扩展包，已做，[#35][pr-35]、[#36][pr-36]） | `test_mcp_notice.py`；`session_new_and_load_send_no_mcp_servers`；`packages/pi-mcp/tests`（192 个） |
| queue / interjection | 已删（r5，含 steer、send-now） | 无 | 删 | 已定（r5） | [执行记录 A.4][logA4] |
| compaction | 触发与展示 | 部分：`auto_compact` 由 `max_turns >= 30` 推导 | 降级 | 未定（r9 没有重新核对） | — |
| subagents / 后台任务 | 已删（r5） | 无 | 删（Phase 4 非目标） | 已定（r5） | [执行记录 A.4][logA4] |
| skills | slash 命令与 `available_commands_update` | 已覆盖；`available_commands_update` 在响应之后发（Zed 要收到响应才登记会话，`new_session` 里有注释） | 保留 | 已定 | `test_acp_agent.py` |
| hooks / plugins / marketplace | 已删（r5） | 无 | 删（Phase 4 非目标） | 已定（r5） | [执行记录 A.4][logA4] |
| image | 图片附件 | 代码有（`prompt_capabilities.image`、`_prompt_to_text_images`），**`packages/pi-agent-cli/tests` 里没有走这条路径的用例** | 保留 | 已定；缺测试 | — |
| status line（`[ui.status_line]`） | pager 本地画一行（命令脚本或内置字段，`pi-status-line`）。r11 时数据全来自 agent 发的 `session_status` 通知，经 `_pi/session_notification` 载体；PR [#19][pr-19]（r12 续 7，续 8 合入）之后，agent 发的仍然优先，没发的字段由 pager 用自己知道的补上（工作目录、会话 id、模型、版本、会话名、两个钟）；`ConnectFlags.status_line` 已不再公布给 agent（`client_capabilities_meta` 返回空） | **从不发**那条通知；`context`、`cost`、分支等只有 agent 才填得了的字段，Python agent 没有，所以不显示（r11 实测：行不画，命令脚本不跑） | 决定 15：改选 (b) pager 本地画（原选 (a) 没过 §8.5 的第 1、4 条）；那个没人读的字段是清理候选 | **已做 (b)，已合入**（r12 续 7、续 8；PR [#19][pr-19]）；其余不挡阶段 1 | `status_line_probe.py`（r11 是 4/4「不画」；改后查「画出来，用的是对的数据」，[§10.14][l10-14]、[§10.15][l10-15]） |
| 历史回放 | `session/load` | 已覆盖（`isReplay` 的 `session/update`）；`session/resume` 不回放 | 保留 | 已定 | `a_session_is_listed_with_a_title_and_replayed_by_a_later_agent` |
| 登录 | `auth_methods` 为空时不要求登录 | `auth_methods=[]`；`authenticate` 没有处理器 | 保留 | 已定 | `connect_runs_the_handshake_and_stop_leaves_nothing_behind` |
| `initialize._meta`：响应里的键 | 解 `modelState`、`grokShell`、`availableCommands`、`cancelRewind`、`sessionRecap`、`feedbackTraceOffer` | Python 的响应没有 `_meta`，都落到缺省 | 逐键删解码（1.R5） | 未定（阶段 1 待办） | — |
| `initialize._meta`：请求里的键 | 发 `clientType`、`clientVersion`；带 `--system-prompt-override` / `--rules` 时再发 `systemPromptOverride`、`rules` | 一个都不读 | 要么 Python 读，要么 pager 拒绝这两个旗标（决定 8） | **提议** | [§10.11][l10-11]「旗标」 |
| `--session-id` | `session/new` 的 `_meta.sessionId`；占用检查先经 `session/list` 翻完所有页（r13，PR [#23][pr-23]），agent 仍然拒绝重复（r14：文件还没写的会话，它的 id 也由 agent 记着，`_reserved_ids`） | **r10：兑现 `_meta.sessionId`，已用的 id 以 `invalid_params` 拒绝**（r9 实测时是忽略） | Python 兑现，重复由 agent 拒绝（决定 3） | **已做**：Python 一侧 r10（PR [#11][pr-11]）；pager 一侧 r13（PR [#23][pr-23]、[#24][pr-24]）：已用的 id 在进 TUI 之前报错，退出 1 | `resume_matrix.py` 用例 10、11（都 PASS）；`test_session_chosen_id.py` |

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
| `session/new` | `cwd`，空的 `mcp_servers`；`_meta`：`yoloMode`、`autoMode` 总有，`agentProfile`、`askUserQuestion: false`、`modelId`、`sessionId` 视情形（`SessionFlags::to_meta`） | 建会话；响应带 `configOptions`（`model`）与 `_meta`（`pi/currentModelId`、`pi/currentModelDisplayName`、`pi/provider`，r10 起还有 `pi/cwd`）；`mcp_servers` 非空时：没人接手的 server 通知一次（r20：装了 `pi-mcp-py`，它接手，见决策 19）；**请求的 `_meta` 只读 `sessionId`**（r10；r9 实测时一个都不读）：SDK 把它摊平成关键字参数，`yoloMode`、`autoMode`、`agentProfile`、`askUserQuestion`、`modelId` 仍然没有人取用 | `resume_matrix.py`；`test_session_chosen_id.py` |
| `session/load` | `session_id`、`cwd`、空的 `mcp_servers`、同样的 `_meta` | 先回放历史（`isReplay` 的 `session/update`）再回应；工作目录用会话保存的 `cwd`（没有才用请求的）；未知 id → `resource_not_found`（r20：没有文件的规范 UUID 除外，开一个空会话，见决策 17 的更正）；响应带 `_meta["pi/cwd"]`（r10；r9 实测时不带），也就是上面那个工作目录，`session/resume` 同 | `a_session_is_listed_with_a_title_and_replayed_by_a_later_agent` |
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

## 修订记录（摘要）

每一轮的完整说明在[执行记录][log]的[修订记录][logRev]。

- r1：初稿。
- r2：吸收源码核查与架构评估。写入原则 P1–P7、决策记录、改动规模；阶段重排；`pi/` 扩展改准入制；会话数据改纯 ACP；leader 放弃。
- r3：拆除 Rust runtime；恢复 `/model`（ADR3 推翻 r2 的「从白名单移除」）。
- r4：阶段 0 的 Linux CI 与基线工具；清掉 leader 的 UI 状态残留；1.R3 的命名。
- r5：推送并开 PR [#7][pr-7]；Linux CI 跑通，基线转为阻塞；入口审计；阶段 A 完成。
- r6：品牌残留清理（[#8][pr-8]）。
- r7：合并 `main` 上另一台机器推的 7 个提交。
- r8：WSL2 / Linux 补测（[#9][pr-9]）。
- r9：阶段 0 收尾；阶段 1 里不需要拍板的部分（[#10][pr-10]）。
- r10：ADR1 的 PR 1（[#11][pr-11]）；1.P3 的 (a)（[#12][pr-12]）。
- r11：0.5 与阶段 C（PR [#13][pr-13]）。调用图工具化（`tui_callgraph.py`）；`zypi` 的网络审计（含一次真模型）；内嵌指南缩成七页并停止启动时解压；`NOTICE` 重写、`THIRD-PARTY-NOTICES` 与 `Cargo.lock` 对账（`tui_notices.py`）；`grok agent` 的残留清零，`leader`、`x.ai` 改成棘轮上限；新增待决策 15 与阶段 D 的 D.5–D.9。
- 整理（r10 之后，2026-10-11）：执行记录移到[执行记录（归档）][log]；本文只留计划、状态、待决策和 §10 索引；§7 改成每项一行状态；§4、§5（r2 的源码事实与规模估计）也移到归档的附录 E，只留索引。
- r12：V0.5 的发布里程碑（分支 `docs/v0.5-milestone`，只改文档）：按剥离的现状定范围，写六项退出条件，建 GitHub 里程碑 [v0.5.0][ms-v050]，新增待决策 16（发行构建的版本戳）。条件由别的 PR 与运行去做：发布演练（[#14][pr-14]）、发布说明（[#15][pr-15]）、版本号（决策 16，[#17][pr-17]）。续 3–续 8（2026-10-13）：合入这三个 PR；决策 6 与 15 的两个 PR（[#18][pr-18]、[#19][pr-19]）；发布 v0.5.0（tag 在 `be3f8a8`）。见 [§10.15][l10-15]。
- r13：ADR1 落地（2026-10-16）。决策 2、4、5、7 按倾向；PR 2（[#23][pr-23]）与 PR 3（[#24][pr-24]）；新增待决策 17、18。见 [§10.16][l10-16]。
- r14：空会话（2026-10-19）。#23、#24 合入 `main`；决策 18 不做；决策 17 做成 PR [#25][pr-25]。见 [§10.17][l10-17]。
- r15：删 `pi-shell` 的旧存储层（决策 10 的存储层一项；PR [#26][pr-26]；2026-10-19）。见 [§10.18][l10-18]。
- r16：合入 #25 与 #26；删 `pi-shell` 里剩下的死代码（PR [#27][pr-27]；2026-10-19）。见 [§10.19][l10-19]。
- r17：检查测试之间的竞争（PR [#28][pr-28]，只改测试与工具；2026-10-19）。见 [§10.20][l10-20]。
- r18：`release.yml` 的令牌（[#29][pr-29]）、`protoc` 的绝对路径（[#30][pr-30]）、`cargo nextest`（[#31][pr-31]）（2026-10-20）。见 [§10.21][l10-21]。
- r19：用 `gh stack` 合入 #27–#31；V0.6 的发布里程碑；开发暂停（2026-10-20）。见 [§10.22][l10-22]。
- r20：MCP 的方向（可选扩展包，决策 19–21）、空白对话的修复、合入 #33–#36：[#34][pr-34]（空白对话再打开）、[#35][pr-35]（给扩展的挂钩）、[#36][pr-36]（`pi-mcp-py`）（2026-10-25）。见 [§10.23][l10-23]。
- r21：V0.6 的版本号（[#39][pr-39]）；合入 #38 与 #39；条件 1–4 的结果（2026-10-25）。见 [§10.24][l10-24]。
- r22：打 tag `v0.6.0`、发布、发布后的核对；合入 #40（2026-10-25）。见 [§10.25][l10-25]。
- r22 续 1：钉 `build-tui` 的运行器，补 glibc 的下限（PR [#42][pr-42]；2026-10-25；用户已合入）；删 7 个已合入的本地分支。见 [§10.25][l10-25] 的「续 1」。
- 整理（r22 续 1 之后，2026-10-27）：照 r10 之后那次的做法再整理一次。被缩短的各段原文存入归档的[附录 F][logF]：头部的状态文字、「当前状态」里逐轮的状态文字、两个发布里程碑、「下一步」、「还没有验证的」与「待决策」里改短的条目、§7 里阶段 C 与 D 的长状态、§10 索引里的长行，和本节 r12 到 r22 续 1 的长条目。计划里改成现在的状态，一轮一行。章节编号与锚点没有变。PR [#42][pr-42] 已由用户合入，状态据此更新。
- r23：三个 action 升到 Node 24 的版本（PR [#43][pr-43]）；扩展 API 的审计批 9（PR [#44][pr-44]）；决策 11、13 已定，不做；新增 `docs/PLAN` 的 Cursor 规则（2026-10-27）。续 1：推送 `main`；用户让合入 #43 与 #44，都已合入（`969062b`、`e860fd9`）。续 2：用户答复 Zed 里的核对通过（核对两项：空白对话再打开；`pi-mcp-py`）。见 [§10.26][l10-26]。

<!-- 链接定义 -->
[log]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md
[l10-1]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#101-范围方法与规模
[l10-2]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#102-model-恢复adr3-r3
[l10-3]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#103-验证r3-的实测
[l10-4]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#104-未验证与残留
[l10-5]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#105-为-pi-rust-铺路
[l10-6]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#106-r4-追加阶段-0-的-ci-与基线leader-残留1r3-命名
[l10-7]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#107-r5-追加linux-ci入口审计与阶段-a-收口
[l10-8]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#108-r6-追加品牌残留清理
[l10-9]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#109-r7-追加合并-main另一台机器推的-7-个提交
[l10-10]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1010-r8-追加linux-补测wsl2
[l10-11]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1011-r9-追加阶段-0-收口03--07--08与阶段-1-里不需要拍板的部分
[l10-12]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1012-r10-追加adr1-的-pr-1python-一侧
[l10-13]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1013-r10-追加1p3-azypi-只提供-agent-真会照做的权限模式
[l10-14]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1014-r11-追加调用图工具化05与阶段-c-收口
[l10-15]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1015-r12-追加v05-的发布里程碑
[l10-16]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1016-r13-追加adr1-的-pr-2-与-pr-3
[l10-17]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1017-r14-追加空会话决策-17与四个答复
[l10-18]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1018-r15-追加删-pi-shell-的旧存储层决策-10
[l10-19]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1019-r16-追加合入-25-与-26删-pi-shell-里剩下的死代码
[l10-20]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1020-r17-追加检查测试之间的竞争只改测试与工具
[l10-21]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1021-r18-追加改用-cargo-nextestprotoc-的路径releaseyml-的令牌
[l10-22]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1022-r19-追加用-gh-stack-合入-2731v06-的发布里程碑开发暂停
[l10-23]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1023-r20-追加mcp-的方向可选扩展包空白对话的修复发布内容合入-3336
[l10-24]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1024-r21-追加v06-的版本号合入-38-与-39条件-14-的结果
[l10-25]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1025-r22-追加打-tag-v060发布发布后的核对
[l10-26]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1026-r23-追加三个-action-升到-node-24扩展-api-的审计批-9决策-11-与-13
[l4-1]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#41-运行路径
[l4-2]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#42-rust-runtime-与耦合面
[l4-3]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#43-python-侧pi_agent_cli共-4114-行agentpy-472-行
[l4-4]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#44-协议与版本
[l4-5]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#45-验证基础设施与文档
[l5]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#5-改动规模
[logA4]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#a4-r5-执行结果对照-r5-时初稿里的默认决策r9-的更新见-a1
[logB]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#附录-b复现方法
[logC]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#附录-c计划-7-的逐项注释r10-原文
[logD]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#附录-d计划头部的状态文字r10-原文
[logE]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#附录-e计划-45r2-的基线原文
[logRev]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#修订记录
[pr-7]: https://github.com/zy1233/pi-python/pull/7
[pr-8]: https://github.com/zy1233/pi-python/pull/8
[pr-9]: https://github.com/zy1233/pi-python/pull/9
[pr-10]: https://github.com/zy1233/pi-python/pull/10
[pr-11]: https://github.com/zy1233/pi-python/pull/11
[pr-12]: https://github.com/zy1233/pi-python/pull/12
[pr-13]: https://github.com/zy1233/pi-python/pull/13
[pr-14]: https://github.com/zy1233/pi-python/pull/14
[pr-15]: https://github.com/zy1233/pi-python/pull/15
[pr-16]: https://github.com/zy1233/pi-python/pull/16
[pr-17]: https://github.com/zy1233/pi-python/pull/17
[pr-18]: https://github.com/zy1233/pi-python/pull/18
[pr-19]: https://github.com/zy1233/pi-python/pull/19
[pr-20]: https://github.com/zy1233/pi-python/pull/20
[pr-21]: https://github.com/zy1233/pi-python/pull/21
[pr-23]: https://github.com/zy1233/pi-python/pull/23
[pr-24]: https://github.com/zy1233/pi-python/pull/24
[pr-25]: https://github.com/zy1233/pi-python/pull/25
[pr-26]: https://github.com/zy1233/pi-python/pull/26
[pr-27]: https://github.com/zy1233/pi-python/pull/27
[pr-28]: https://github.com/zy1233/pi-python/pull/28
[pr-29]: https://github.com/zy1233/pi-python/pull/29
[pr-30]: https://github.com/zy1233/pi-python/pull/30
[pr-31]: https://github.com/zy1233/pi-python/pull/31
[pr-33]: https://github.com/zy1233/pi-python/pull/33
[pr-34]: https://github.com/zy1233/pi-python/pull/34
[pr-35]: https://github.com/zy1233/pi-python/pull/35
[pr-36]: https://github.com/zy1233/pi-python/pull/36
[pr-39]: https://github.com/zy1233/pi-python/pull/39
[pr-40]: https://github.com/zy1233/pi-python/pull/40
[pr-41]: https://github.com/zy1233/pi-python/pull/41
[pr-42]: https://github.com/zy1233/pi-python/pull/42
[pr-43]: https://github.com/zy1233/pi-python/pull/43
[pr-44]: https://github.com/zy1233/pi-python/pull/44
[ci-run-5f73e07]: https://github.com/zy1233/pi-python/actions/runs/40172668652
[tui-ci-run-5f73e07]: https://github.com/zy1233/pi-python/actions/runs/40172721728
[run-dry-run-5f73e07]: https://github.com/zy1233/pi-python/actions/runs/40172723794
[run-release-v060]: https://github.com/zy1233/pi-python/actions/runs/40180264121
[rel-v060]: https://github.com/zy1233/pi-python/releases/tag/v0.6.0
[ci-run-r5]: https://github.com/zy1233/pi-python/actions/runs/37379203134
[ms-v050]: https://github.com/zy1233/pi-python/milestone/1
[ms-v060]: https://github.com/zy1233/pi-python/milestone/2

[logF]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#附录-f计划整理前的状态文字r22-续-1-原文
[logF2]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#f2-当前状态里逐轮的状态文字
[logF3]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#f3-发布里程碑-v05
[logF4]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#f4-发布里程碑-v06
[logF7]: archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#f7-待决策里被缩短的七项