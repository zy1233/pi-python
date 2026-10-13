# Rust Agent Runtime 剥离计划

> **状态：r12（2026-10-12）。** r3–r11 的工作已全部合入 `main`（PR [#7][pr-7]、[#8][pr-8]、[#9][pr-9]、[#10][pr-10]、[#11][pr-11]、[#12][pr-12]、[#13][pr-13]）。r12（[§10.15][l10-15]）：按剥离的现状定下 V0.5 的发布里程碑。这个 PR（[#16][pr-16]）只改文档；里程碑的条件由三个别的 PR（[#14][pr-14] 发布演练，[#15][pr-15] 发布说明，[#17][pr-17] 版本号；三个都已合入 `main`）和两次 CI 运行去做。做到哪了，见[当前状态](#当前状态)；V0.5 要做什么，见[发布里程碑 V0.5](#发布里程碑-v05)。
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
| 有哪些事在等用户决定 | [待决策](#待决策) |
| 为什么这样设计 | §1–§3（目标、架构、与现有文档的关系）、§6（ADR1–ADR7） |
| r2 核对过的源码事实、改动规模的估计 | §4、§5（只剩索引，原文在[归档的附录 E][logE]） |
| 每个阶段还剩什么 | §7（每项一行状态） |
| 某一轮怎么做的、测了什么、数字是多少 | [执行记录（归档）][log]；§10 有索引 |
| 能力矩阵、ACP 版本配对 | 附录 A |

## 当前状态

截至 r12：`origin/main` 在 `0efd400`（PR [#14][pr-14] 的合并提交；它前面是 PR [#17][pr-17] 的 `d57fd9b` 与 PR [#15][pr-15] 的 `59545c1`）。[`CI`][ci-run-0efd400] 在 `0efd400` 上通过；[`TUI CI`][tui-ci-run-d57fd9b] 在 `d57fd9b` 上通过（`0efd400` 与它只差 `release.yml` 的 7 行）。在 r11 的 PR [#13][pr-13] 的合并提交 `4fc028a` 上，[`CI`][ci-run-4fc028a] 与 [`TUI CI`][tui-ci-run-4fc028a] 也都通过；两次 `TUI CI` 的 10 个 suite 都合计 8,665 通过 / 0 失败 / 22 忽略（`PI_E2E_REQUIRED=1`），与 r11 在 WSL2 上的本地数相同；比 r10 的 8,683 少的 18 个在 `pi-pager`，是 C.2 随教程叠层与旧用户指南一起删掉的用例。r3–r11 都已合入 `main`，r12 的 PR #14、#15、#17 也是。V0.5 的发布里程碑见[发布里程碑 V0.5](#发布里程碑-v05)。

| 阶段 | 状态 | 已完成 | 还差 |
|---|---|---|---|
| 0 基线与门禁 | 基本完成 | Linux CI 与基线（0.1、0.2）；ACP 契约与 e2e（0.3）；deny-list（0.4）；调用图工具化（0.5，r11）；入口审计（0.6）；读盘调用点与 ADR1 落地设计（0.7）；能力矩阵与版本配对（0.8）；Linux 上的实测（0.9 的大部分） | 0.9 的 Landlock 层（要内核 ≥ 5.13），以及 macOS、Windows 一侧 |
| A 死代码先行 | 完成 | A.1–A.4 | — |
| 1 协议与 Python | 完成一半 | 1.P1 会话列表分页；1.P2 优雅退出；1.P4 显式忽略 `mcp_servers`；1.P7 契约测试；1.R1 的 PR 1；1.R3 的大部分；1.P3 的 (a) | 1.P3 的 (b)、(c)；1.P5；1.P6；1.R1 的 PR 2、PR 3；1.R2；1.R4；1.R5；1.R3 的 MCP 子进程与 Windows |
| B 拆除 runtime | 删除完成，迁移没有完成 | B.1、B.5、B.6：`pi-sampler` 与 `pi-shell` 的 runtime 单元已删 | B.2、B.3、B.4：r3 换了做法，没有抽出叶子 crate |
| C 验收与文档 | 完成（r11，PR [#13][pr-13]） | C.1–C.4：`zypi` 没有到 LLM provider 的外连（实测，含一次真模型）；内嵌指南缩成 7 页，启动不再解压；`NOTICE` 重写，`THIRD-PARTY-NOTICES` 有工具对账；`grok agent` 清零，`leader`、`x.ai` 有上限 | 退出条件里的 `leader`、`x.ai` 清不到 0，改成了棘轮（§7 的 C） |
| D 依赖图瘦身（可选） | 没有开始 | — | D.1–D.9；等基线数据再定 |

### 发布里程碑 V0.5

**是什么。** 第一个不带 Rust agent runtime 的发布：`zypi` 只做终端界面和 ACP client，Python 是唯一的 agent。它把两条线一起发：Python 一侧的 Phase 6/7（0.5.0 的版本号与 `CHANGELOG.md` 的 `[0.5.0]` 段已经在 `main` 上，没有打 tag），和 TUI 一侧的 r3–r11（原来在 `CHANGELOG.md` 的 `[Unreleased]` 段，PR [#15][pr-15] 把它并进 `[0.5.0]`）。相对 v0.4.0：23 个提交；`tui/` 里 1,512 个文件、+12,275 / −641,224 行；`pi-sampler` 的 31 个文件变成 0 个。GitHub 里程碑：[v0.5.0][ms-v050]，没有挂 PR 与 issue（条件在这里记，不记两处）。依据与没有验证的见 [§10.15][l10-15]。

**范围按现状定。** 阶段 0、A、C 完成，阶段 B 的删除完成，阶段 1 完成一半，阶段 D 没有开始（见上表）。V0.5 按这个状态发，不等阶段 1 的余项、ADR1 的 PR 2 与 PR 3、阶段 D。下面「已知问题」里的各项，有的 v0.4.0 就有，有的是剥离带来的，没有逐项分开量过；条件 4 本来是为了分开它们，用户决定不做（见条件 4）。

**退出条件。** 打 tag 之前，六项都要有结果（第 4 项的结果是用户决定不做）。

1. **CI 绿。** `CI` 与 `TUI CI` 在要打 tag 的提交上通过。现状：`CI` 在 `0efd400` 上通过，`TUI CI` 在 `d57fd9b` 上通过（`0efd400` 与它只差 `release.yml` 的 7 行）。
2. **再量一次 release 体积。** 手动触发 `TUI CI` 的 `release_baseline`（约 30 分钟），把结果回填 [`docs/baselines/tui.md`](../baselines/tui.md)。条件：Linux 的 `zypi` 不大于 v0.4.0 的 470,461,776 B。上一次是 r5 的 422,034,120 B；#7 合入之后 `tui/` 又合入 5 个提交（#8、#9、#10、#12、#13）：115 个文件，+4,481 / −16,045 行。**结果（r12）**：[run 38221952524][run-baseline]，在 `4fc028a` 上：**420,767,096 B**（比 v0.4.0 小 10.6 %，比 r5 小 1,267,024 B），构建 1,195 s（v0.4.0：1,647 s），`--version` 中位数 23.4 ms。条件满足，数字已回填 [`docs/baselines/tui.md`](../baselines/tui.md)。之后到 tag 之间，`tui/` 若有改动，要再量一次。
3. **发布构建演练。** `release.yml` 只在 `v*` tag 上触发，没有手动入口；macOS arm64 的 job（2026-10-01 加的）从来没有在发布里跑过。`github-release` 等它，`publish-pypi` 不等：它失败时，PyPI 上的包已经发出，GitHub 上没有 Release。Linux CI 编不到 macOS 专属的代码：`tui/crates` 里 75 个文件带 `cfg(target_os = "macos")`，最后一次在 macOS 上构建是 r7，之后 `tui/` 改了 88 个文件，其中 8 个带这样的代码。v0.3.0 的发布工作流跑了三次才通过。做法：给 `release.yml` 加 `workflow_dispatch`，构建 job 照跑并上传产物，`publish-pypi` 与 `github-release` 只在 tag 上跑。这改的是发布工作流，所以先问了用户：用户同意了（r12），PR [#14][pr-14] 就是这一改，已合入（`0efd400`）。两个发布 job 的 `if` 不只看 ref，还要求事件是 `push`，因为手动运行可以把一个 tag 当作 ref。条件：Linux 与 macOS arm64 的 `release-dist` 构建、六个 Python 包的构建都成功。**结果（r12）**：演练 [run 38223353755][run-dry-run] 从 PR 分支 `ci/release-dry-run`（`04cea48`）手动触发；GitHub 接受了分支上的 `workflow_dispatch`，不用先合入 `main`。结果：成功，共 27 分钟。测试 97 s，Python 包的构建 29 s，Linux x86_64 的 `release-dist` 构建 16 m 26 s，macOS arm64 的 `release-dist` 构建 25 m 27 s；`publish-pypi` 与 `github-release` 跳过，没有发布任何东西。三个产物（`dist`、`tui-binary`、`tui-binary-macos-arm64`）保留到 2027-01-10。macOS 的 job 第一次在发布工作流里跑，它编译的 `cfg(target_os = "macos")` 代码 Linux 的 CI 编译不到，现在通过了。**没有验证**：没有人运行过那个 macOS 二进制，我也没有下载它（试过 `gh run download`，太慢，见 [§10.15][l10-15]）；`if` 为真的一侧（tag 的 `push`）要等真正的 tag 才跑得到。
4. **与 v0.4.0 对照。** 用 `scripts/tui_pty/` 的 `smoke.py`、`exit_matrix.py`、`resume_matrix.py`、`mode_probe.py`（都有 `--zypi PATH`）和同一个 Python agent，分别跑 v0.4.0 的 `zypi-linux-x86_64` 与新构建，比较结果。有差别的，要么在 `CHANGELOG.md` 里有一条，要么是缺陷。只在 Linux（WSL2）上做。**结果（r12）**：用户决定不做，V0.5 不等它。所以「已知问题」里的各项哪些 v0.4.0 就有，仍是推断，没有测量。
5. **发布说明。** 发布页的正文只取 `CHANGELOG.md` 里与 tag 同版本的一段，所以：把 `[Unreleased]` 并进 `[0.5.0]`，日期改成发布日（现在是 2026-10-02）；补上 `CHANGELOG.md` 里还没有的条目——Rust runtime 的删除与随之删掉的界面和命令（r3、r5）、`/model` 经 ACP `configOptions` 恢复（r3）、`-p` 在要求沙箱时拒绝运行（r5）、品牌清理（r6）；写「已知问题」（下）；用 `release.yml` 里同一段 `awk` 在本地预览。`docs/TUI-AND-CODE-AGENT.md` 只写了 Linux x86_64 有预编译二进制；`zypi-macos-arm64` 随版本发的话要补上。**状态（r12）**：PR [#15][pr-15] 把 `[Unreleased]` 并进了 `[0.5.0]`，补了 20 条（Added 2、Changed 2、Removed 2、Fixed 6、已知问题 8），日期写 2026-10-12（打 tag 那天不是这天，就改）。用 `release.yml` 里那段 `awk` 预览过（Git for Windows 的 `awk`，输入是 LF 文本）：正文 45,076 B，五个分组。演练成功之后，PR [#15][pr-15] 又加了 `zypi-macos-arm64` 的条目（Added），并改了 `README.md` 与 `docs/TUI-AND-CODE-AGENT.md`：三处都写明这个二进制由 CI 构建，没有人运行过。PR [#17][pr-17] 再加 1 条 Changed（版本号，见决策 16），并去掉 1 条已知问题；那之后正文是 45,855 B。PR [#15][pr-15]（`59545c1`）与 PR [#17][pr-17]（`d57fd9b`）都已合入。
6. **打 tag（用户）。** 先按 `pre-push-ruff` 的规则在 Windows 与 WSL 跑 ruff 和 pytest；`git tag v0.5.0`、`git push origin v0.5.0`；再核对 Release 的运行：PyPI 上六个包（核心、harness、cli 是 0.5.0，三个扩展包是 0.2.0），GitHub Release 上的 wheel、sdist 与 `zypi-*` 二进制。

**带着发布的已知问题**，除非用户在打 tag 之前决定。用户在 r12 续 5 定了决策 6 与 15，8 与 14 仍待定。6 与 15 各做成了一个 PR（r12 续 6、续 7；15 在复核后从 (a) 改成了 (b)），没有合入 `main`，所以下面四条在 V0.5 里都还是已知问题，除非那两个 PR 在打 tag 之前合入（合入时要同时改 `CHANGELOG.md` 与这四条）。决策 16 已做并已合入（PR [#17][pr-17]，`d57fd9b`），所以「已知问题」里没有版本号一条。

- `zypi export <id>` 对 Python agent 的会话恒报 `Session '<id>' not found.`，退出码 1（决策 6：已定 (a)，已做成 PR，合入之前它仍是已知问题；见 [§10.15][l10-15] 续 6）。
- `--resume <标题>` 找不到 Python 会话（`no session id or title matched`，退出码 1）。`--session-id <已用的 id>` 被 agent 拒绝，TUI 却显示 `Session creation failed: Invalid params: …` 并留在界面里，不退出（ADR1 的 PR 2）。`--resume <未知 id>` 进了 TUI 才报错（决策 5）。`--continue` 取最近创建的会话，不是最近活动的（决策 4）。
- 交互模式下 `--system-prompt-override` 与 `--rules` 无效，`--allow` 与 `--deny` 到不了 agent，`zypi --help` 还列着没有逐个核对过的 Grok 时代旗标（决策 8，仍待定）。
- 设置页里权限模式以外的项对 Python agent 可能没有效果；`remember_tool_approvals` 与 `default_selected_permission` 没有测过（决策 14，仍待定）。
- `[ui.status_line]` 在 Python agent 下永远不画（决策 15：已定 (a)，还没做）；内嵌指南已照实写了。
- 内嵌的七页指南没有入口可以打开（D.6）。
- Python 一侧：Phase 6/7 审计的 F7-02 至 F7-06（低危：扩展 API 的几处小缺陷）与 F7-07 至 F7-09（规格的文档备注）没有修复批次（[`AUDIT-PHASE6-PHASE7-2026-09-30.md`](../AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md) 第八节）。
- 上面「还没有验证的」里的各项：平台（只在 Linux 上验证过）、Landlock 层、延迟、跨目录的 `--resume <id>`、发行构建。

**不在 V0.5。** ADR1 的 PR 2 与 PR 3，和决策 2、4、5；决策 7 的 (b)、(c)；决策 9–11、13；阶段 D 全部；B.2–B.4；1.P5、1.R2、1.R5；0.9 的余项。它们归哪个里程碑，等用户排 V0.6 时再定。

### 下一步

- **要等用户决定：** ADR1 的 PR 2（决策 4、5）与 PR 3（决策 2；决策 6 的实现已做成 PR，它合入之后才能删旧存储层）；1.P6 的旗标契约（决策 8）；设置页里其余的项（决策 14）；真的 Plan 模式（决策 7 的 (b)）。
- **已定，已做成 PR，等用户合入：** 决策 6 的 (a)（`zypi export` 经 ACP 回放）与决策 15 的 (b)（pager 用本地已有的数据画状态行；原选的 (a) 在复核时没有过 DESIGN §8.5 的五条），r12 续 6、续 7。合入就进 V0.5，除非那时已打 tag；不合入，它们就作为已知问题带着发（[发布里程碑 V0.5](#发布里程碑-v05)）。
- **不用等决定：** V0.5 的退出条件 1–5。条件 2 已有结果；条件 3、5 各有一个 PR（[#14][pr-14]、[#15][pr-15]，都已合入），条件 3 的演练已经跑过；条件 4（与 v0.4.0 对照）用户决定不做；条件 6（打 tag）归用户。发行构建的版本号（决策 16）做在 PR [#17][pr-17] 里，已合入（先合 #15，再把 #17 改基到 `main` 并合入）。打 tag 之前，条件 1 要在那个提交上再看一次。阶段 D 做不做，看 [`docs/baselines/tui.md`](../baselines/tui.md) 的数据；r11 又提出 D.5–D.9 五个候选。

### 还没有验证的

- **平台。** Rust 的 e2e 用例与 `scripts/tui_pty/` 的 PTY 脚本只在 Linux（WSL2）上跑过；macOS 与 Windows 上没有。Windows 上只量了 Python 一侧的延迟，TUI 没有参与。
- **真实 LLM。** r9、r10 的实测都用 mock。r11 用真模型跑了一轮网络审计（C.1，只看 `zypi` 有没有外连），没有用它验证别的功能。
- **沙箱的 Landlock 层。** WSL 的内核 5.10 没有 Landlock；`pi-sandbox` 的端到端用例在那里空转（软跳过）。
- **延迟。** 只量了合成的会话（12 / 120 / 2,000 条消息）。冷磁盘、网络文件系统、超过 1,000 个会话、真实形状的会话没有量。
- **跨目录的 `--resume <id>`。** TUI 的当前目录与会话保存的目录不同时，`@` 搜索、diff、git 信息用哪个目录，没有验证。
- **旗标。** 除 `--always-approve`、`--permission-mode`、`--continue`、`--resume`、`--session-id` 外，没有逐个验证旗标在交互模式下是否生效。`-m` / `--model` 尤其没有查。
- **设置页里其余的权限项**（`remember_tool_approvals`、`default_selected_permission` 等）对 Python 是否有效，没有验证（待决策 14）。
- **同一个 id 的并发。** 锁只在进程内。两个 agent 进程共用一个 home 时，同一个 id 仍可能各建一个会话。
- **发行构建。** 最近一次 `release-dist` 构建是 r12 的 [run 38221952524][run-baseline]（`4fc028a`，体积见条件 2）；`release.yml` 的 macOS arm64 job 只在演练里跑过（条件 3），没有在真正的发布里跑过；PR [#17][pr-17] 改了 `zypi --version` 的数字（决策 16）：在 WSL2 里用 `0efd400` 的开发构建跑过，打印 `zypi 0.5.0 (0efd40071ca0)`；那不是 CI 的 `release-dist` 产物，那个产物没有人运行过（[§10.15][l10-15] 续 3）。`TUI CI` 在 PR 上不运行 `zypi --version`；手动的 `release_baseline` 为了量耗时跑它，但丢掉输出，也不看退出码。

出处：执行记录 [§10.10][l10-10]–[§10.15][l10-15] 各节的「没有验证」。

## 待决策

共十六项。能给出倾向的，都写了倾向。没有一项会在用户确认之前动手。编号沿用 r9 的提议（[§10.11][l10-11] 末尾「留给用户决定的」十二项，原文留在那里）；13、14 是 r10 加的，15 是 r11 加的，16 是 r12 加的。附录 A 的「决定 N」指这里的第 N 项。

- **已做：** 1 的 PR 1、3、7 的 (a)、12、16 的 (a)（PR [#17][pr-17]）。
- **已定，已做成 PR，等用户合入：** 6 的 (a)、15 的 (b)（用户，2026-10-13；[§10.15][l10-15] 续 5 起；15 原选 (a)，复核后改为 (b)，见该条）。
- **待定：** 1 的 PR 2 与 PR 3（等 2、4、5 的决定，和 6 的 PR 合入）、2、4、5、7 的 (b)(c)、8、9、10、11、13、14。

1. **ADR1 的落地。** PR 1：Python；PR 2：Rust，只加不删；PR 3：Rust，删。
    - 倾向：开工，先做 PR 1。
    - 状态：**PR 1 已做**（r10，PR [#11][pr-11]）。PR 2、PR 3 等决策 2、4、5，和决策 6 的 PR 合入（6 已定 (a)，已做成 PR，r12 续 6）。
    - 详情：[§10.11][l10-11]、[§10.12][l10-12]
2. **沙箱顺序。**
    - 倾向：去掉「恢复时沿用保存的沙箱 profile」（对 Python 会话没有行为变化）。备选：先起查询用的 agent（冷启动翻倍，还有有无沙箱的窗口）。
    - 状态：待定。
    - 详情：[§10.11][l10-11]
3. **`--session-id` 的契约。** ACP v1 的 `session/new` 没有 session id 参数，`_meta.sessionId` 是 pager 与 agent 之间的约定。
    - 倾向：Python 兑现，重复由 agent 以 `invalid_params` 拒绝。备选：删掉这个旗标。
    - 状态：**已定，已做**（r10，PR [#11][pr-11]）。
    - 详情：[§10.12][l10-12]
4. **`--continue` 的「最近」。** Python 的列表按创建时间倒序（ACP 对排序只说「默认排序」）。
    - 倾向：最近活动（`updatedAt` 最大；要翻完所有页，1,000 个会话在 Windows 上约 0.8 s）。备选：最近创建（第一页第一条，最快）。
    - 状态：待定。
    - 详情：[§10.11][l10-11]
5. **未知 id 的报错时机。** `--resume <未知 id>` 现在进了 TUI 才报错，状态栏的模型名还是 `unknown`。
    - 倾向：进 TUI 之前报错，退出码 1（`resume_matrix.py` 的用例 8 随之收紧）。
    - 状态：待定。
    - 详情：[§10.11][l10-11]
6. **`zypi export`。** 现在对 Python 会话恒报 not found，是一个静默坏掉的命令。r8 时用户选了不动。
    - 倾向：(a) 经 ACP 回放导出：起 agent、`session/load`、收 `session/update`，复用 `render_blocks_to_markdown`，约 100 行，多 1–2 s 的 agent 启动；(b) 删掉子命令。不要 (c) 维持现状。
    - 状态：**已定 (a)，已做成 PR，等用户合入**（PR [#18](https://github.com/zy1233/pi-python/pull/18)；用户，2026-10-13，r12 续 5；记录 [§10.15][l10-15] 续 6）。做法按倾向里写的：`export_cmd` 起 agent、发 `session/load`、收 `session/update`，经 `AcpUpdateTracker` 与 `render_blocks_to_markdown` 写成 Markdown，与 `/export` 同一条渲染路径；命令结束时（失败时也一样）agent 被停掉。最后几条回放可能比 `session/load` 的响应晚到，所以响应之后静默 500 ms 才算收齐；agent 60 s 不出声算错误。代码约 300 行（不是估计的 100 行：收集、静默期、对 agent 的请求逐一回答占了大头），测试 18 个加一个对着真 agent 的 e2e。ADR1 的 PR 3 删旧存储层之前要先做完这一项：做完了，`export_cmd` 不再引用 `pi_shell::session::storage`（`tui-support.md` 里它的外部引用 1 → 0）。合入之前，V0.5 里 `zypi export` 仍是已知问题。
    - 详情：[§10.11][l10-11]、[§10.15][l10-15]
7. **模式映射（1.P3）。** (a) 只提供 agent 会照做的模式；(b) 做 `session/set_mode`，Plan = 把工具集裁到只读；(c) 定义 Python 的 `auto`（今天等于 always-approve，设置页却说是分类器：改名、删除，或真做分类器）。
    - 倾向：(a) 先行，因为 Plan 现在给用户的是虚假的安全感；(b) 另议。
    - 状态：(a) **已做**（r10，PR [#12][pr-12]）；(b)、(c) 待定。
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
    - 状态：待定。
    - 详情：[§10.11][l10-11]
11. **选择器边翻边显示。** 现在等所有页回来再画（1,000 个会话约 0.8 s，Windows）。
    - 倾向：不做，除非有人遇到。
    - 状态：待定。
    - 详情：[§10.11][l10-11]
12. **推送与 PR。**
    - 状态：**已做**：PR [#9][pr-9]–[#12][pr-12] 已合入 `main`。
    - 详情：[§10.12][l10-12]
13. **会话目录的列举太慢。** `LocalExecutionEnv.list_dir` 给每个条目造一个 `FileInfo`：5,000 个文件要 286–372 ms，`os.listdir` 只要 2–11 ms。`session/list` 的每一页、`find`、`has_session` 都付这笔钱。
    - 倾向：给仓库一条只列名字的路径（约 25 行加测试）。5,000 个会话时首页估计从 404 / 319 ms 降到约 80 / 40 ms（Windows / WSL2）；这是估算，没有实现也没有量。
    - 状态：待定。
    - 详情：[§10.12][l10-12]
14. **设置页里其余对 Python 没有效果的项。**
    - 倾向：像对模式做的那样，逐个对着真 agent 测，再决定藏起来还是接线。先测 `remember_tool_approvals` 与 `default_selected_permission`（离权限最近，错了代价最高）。还没有证据。
    - 状态：待定（用户，2026-10-13：待定；r12 续 5）。V0.5 带着它发，已知问题里已经写了。
    - 详情：[§10.13][l10-13]
15. **自定义状态行（`[ui.status_line]`）。** 这一行在 Python agent 下永远不画，配了命令脚本也不会跑：它的数据来自一条 `session_status` 通知，只有旧的 Rust agent 发过，Python 从不发（`status_line_probe.py` 实测）。内嵌指南的 `25-status-line.md` 已照实写了这一点。
    - 选项：(a) Python 发这条通知——要走 `_pi/session_notification` 载体（`x.ai/` 开头的方法在入口被丢），是新的 `pi/` 扩展，要先过 DESIGN §8.5 的五条；(b) pager 用本地已有的东西画（工作目录、模型、权限模式、git 分支），不靠 agent；(c) 删掉这一行的设置、代码与指南页。
    - 倾向：(b) 或 (c)，不倾向 (a)：行里的字段大多是 pager 自己就知道的。没有证据说有人在用它。
    - 状态：**已定 (a)，还没做**（用户，2026-10-13，r12 续 5）。用户选了 (a)，不是上面写的倾向。做法按选项里写的：Python 发这条通知，走 `_pi/session_notification` 载体，是新的 `pi/` 扩展，先过 DESIGN §8.5 的五条。做的时候，内嵌指南的 `25-status-line.md` 和 `status_line_probe.py` 的预期也要改，今天它们写的是「画不出」。实现还没开始；V0.5 里这一行仍画不出，是已知问题。
    - 详情：[§10.14][l10-14]、[§10.15][l10-15]
16. **发行构建的版本戳。** r12 时，`zypi --version` 里的版本号是 `1.0.8`（后面跟提交的前 12 位）：`pi-pager-bin/build.rs` 取环境变量 `GROK_VERSION`，没有就取 crate 的版本，而 `pi-pager-bin` 与 `pi-version` 的版本都是继承自上游的 1.0.8。仓库里没有任何文件设 `GROK_VERSION`，`release.yml` 在 v0.3.0、v0.4.0 与 `main` 上都没设。ACP 的 `agentInfo.version` 写死 `0.1.0`（`pi_agent_cli/agent.py` 的 `_AGENT_INFO`），Phase 6/7 审计记过，没改。
    - **不要靠在 `release.yml` 里设 `GROK_VERSION` 来修。** 同一个变量还决定「是不是发行构建」（`pi_version::IS_DEV_BUILD`）：没有它就是开发构建，发行二进制至今都是开发构建。开发构建里文件夹信任整个是惰性的（`folder_trust_inert()`）：从不提示，不读写 `trusted_folders.toml`，不管配置怎么写。盖了戳，它按默认开启，会对仓库里的本地配置（`.envrc`、`.claude`、hooks、插件、MCP、LSP）问「信任这个文件夹吗」。这些配置里，MCP 的加载代码在 1.P4 之后没有调用者（决策 10）；其余没有逐项核对。遥测客户端的 `dev_build` 字段也随戳翻转。
    - 选项：(a) 不盖戳；把 `pi-pager-bin` 与 `pi-version` 的 crate 版本改成发行版本，`_AGENT_INFO` 读已装包的版本；(b) 盖戳，再处理文件夹信任（删掉，或查清它还管着什么）与遥测；(c) 维持现状，写进已知问题。
    - 倾向：(a)。
    - 核对（r12）：版本号从 1.0.8 改小，会碰到三处按版本比较的代码。① `pi-config` 的 `version_overrides`：配置里的补丁用 `minimum_version` / `maximum_version` 限定版本范围，比较的是 `pi_version::installed_semver()`；版本改小之后，要求 `minimum_version` 高于新版本的补丁不再生效。仓库里没有默认配置带这样的补丁（只有代码与测试）；用户自己写的，无从查起。② `pi-update` 的 `version_policy.rs` 与 `auto_update.rs` 按 semver 比较：自动更新没有启动（`pi-pager-bin/src/main.rs` 里是 `let bg_update_rx = None;`，子命令里也没有 `update`），这些比较不会跑。③ `pi-shell` 的模型缓存（`agent/models/cache.rs`）只判断版本字符串是否相等。没有发现必须先改的逻辑。
    - 状态：**已做 (a)，已合入 `main`**（r12 续 2，PR [#17][pr-17]，叠在 #15 上；合入后是 `d57fd9b`）。用户先选了 (a)，那是倾向；后来他要求现在做。改了：`pi-pager-bin` 与 `pi-version` 的 `Cargo.toml`，和 `Cargo.lock` 里这两个包，`1.0.8` → `0.5.0`（`pi-agent-cli-lc` 的版本）；`pi_agent_cli/agent.py` 的 `_AGENT_INFO` 读已装包的版本（没有包元数据时是 `0.0.0+unknown`）。两个新的测试文件：`packages/pi-agent-cli/tests/test_agent_info.py`、`scripts/tests/test_release_versions.py`：agent 报告已装包的版本；两个 crate 与 `Cargo.lock` 必须等于 `pi-agent-cli-lc` 的版本（所以以后每次发布要一起抬高）；没有工作流设 `GROK_VERSION`（最后一条用户没有要，是为了守住 (a)；不想要就删）。`pi-pager` 与 `pi-shell`（也是 1.0.8）不在决策里，没有改。**行为变化**：版本号比以前小，`minimum_version` 高于 `0.5.0` 的 `[[version_overrides]]` 补丁不再生效（见上面的核对）。验证：Windows 的 `pytest` 全量；Linux 上 `CI` 的 `pytest`（Python 3.11–3.13）与 `TUI CI`（`cargo check`，`cargo test` 8,665 通过 / 0 失败 / 22 忽略）都通过。没有测试检查 `zypi --version` 的输出；续 3 在 WSL2 里跑过 `0efd400` 的开发构建，打印 `zypi 0.5.0 (0efd40071ca0)`。做法与数字见 [§10.15][l10-15]。
    - 详情：[§10.15][l10-15]

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

- [x] 0.1 **Rust CI job**：`cargo check -p pi-pager-bin` + 选定 crate 的 `cargo test`（Linux，ADR6）。**状态**：r4 写好 `.github/workflows/tui-ci.yml`。r5 在 Linux 上跑通，门禁与测试都是阻塞的，`TUI CI` [run 37379203134][ci-run-r5] 全绿。记录 [§10.6][l10-6]、[§10.7][l10-7]。
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
- [x] 1.P4 `mcp_servers`：**显式忽略并在文档声明**（Phase 4 非目标），或实现。**状态**（r9）：做了前者。pager 不再发 `mcp_servers`；Python 收到非空列表时记一条警告，并发一条只含名字的 agent 消息。`packages/pi-agent-cli/AGENTS.md` 与 `docs/TUI-AND-CODE-AGENT.md` 写明这是对 ACP「agent 必须支持 stdio 服务器」的有意偏离。记录 [§10.11][l10-11]。
- [ ] 1.P5 `initialize` 的 capabilities / `_meta`：只公布 pager 实际需要且已登记（P2）的键。**状态**：r5 做了一部分：`initialize` 公布了 `session/close` 与 `session/resume`，但 `run_agent` 没开 `use_unstable_protocol`，SDK 对这两个方法回「Method not found」，已改。其余公布项与 pager 需求的对照没做。
- [ ] 1.P6 `-p` 旗标契约：以 `__main__` 接受的旗标为权威；每个 `PagerArgs` 旗标逐项「支持｜非 0 退出并报错｜文档删除」。**状态**：没做。待决策 8。
- [x] 1.P7 契约测试：mock `stream_fn` 驱动 ACP stdio，断言方法面（含既有的「不注册 `x.ai/`」）。**状态**（r5）：`packages/pi-agent-cli/tests/test_acp_stdio_contract.py` 起子进程走 stdio，8 个用例。r9：补上权限往返与 cancel 的线上用例（`test_acp_stdio_tools.py`）。记录 [§10.7][l10-7]、[§10.11][l10-11]。

Rust（pager）：

- [ ] 1.R1 ADR1 落地：移除对 session 文件的读取；`export` 的去向。**状态**（r9）：读盘调用点清单、落地设计、PR 切分、验收矩阵都在 [§10.11][l10-11]。r10 只做了 PR 1，即 Python 一侧的 `_meta.sessionId` 与 `pi/cwd`（PR [#11][pr-11]，记录 [§10.12][l10-12]）。PR 2（Rust，只加不删）与 PR 3（Rust，删）没有动手，等决策 2、4、5，和决策 6 的 PR 合入（6 已定 (a)，已做成 PR，r12 续 6：`export_cmd` 不再引用 `pi_shell::session::storage`）。
- [ ] 1.R2 按能力隐藏 Python 不路由的方法的入口（`session/set_model`、`session/set_mode` 等）；method-not-found 不 panic、不重复报错；`/model` 按 ADR3 恢复（r3 已做，不在隐藏之列）。
- [ ] 1.R3 子进程生命周期（P6）。**状态**：做了大半。r4：命名与提示语。r5：先关 agent 的 stdin 再杀；做了 macOS 的退出实测。r8：Linux 的退出矩阵；沙箱下补了 `--die-with-parent`；agent 的 `stderr` 落到 `<home>/logs/agent.stderr.log`；`-p` 模式下 Python 一侧监视父进程。r9：agent 自己退出时，pager 说得出原因。没做：MCP 子进程；Windows 上的行为。记录 [§10.6][l10-6]、[§10.7][l10-7]、[§10.10][l10-10]、[§10.11][l10-11]。
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

- [x] C.1 运行时验收：阶段 0.3 的 mock-ACP e2e 全绿；一次可选的真实模型 smoke，用进程级网络连接核对 `zypi` 进程无到 LLM provider 的外连。**状态**（r11，Linux / WSL2）：e2e（`python_agent_e2e`，`PI_E2E_REQUIRED=1`，不是跳过）在本分支上全绿。网络审计是 `scripts/tui_pty/net_audit.py`：在 PTY 里跑一轮对话，约每 9 ms 读一次 `zypi` 与它之下每个进程的 socket（`/proc/<pid>/fd` 对 `/proc/<pid>/net/*`），查 13 项。mock LLM 13/13，放进没有网络的命名空间（`bwrap --unshare-net`）再跑一次也是 13/13；真模型一次（本机配置的 provider）13/13：`zypi` 没有到非本机地址的连接，也没有监听；agent 有到两个远端地址的连接，这是正向对照，说明采样看得见外连。另有一个发现：配了 `[ui.status_line]` 也画不出那一行，因为 Python agent 不发画它所需的消息（待决策 15；r12 续 5 已定 (a)，还没做）。记录 [§10.14][l10-14]。
- [x] C.2 内嵌 user-guide 裁剪或重写（同步 `status_line_tests.rs` 夹具），并停止每次启动的 `extract_user_guide_docs`。**状态**（r11）：`pi-pager/docs/` 原有 33 个文件（用户指南 23 页加 README，教程 9 页，全是 Grok 原文），删了 25 个（用户指南 16 页、教程 9 页），现在只剩 `user-guide/` 里的 7 页加一份 README（入门、斜杠命令、主题、会话、终端支持、权限与安全、状态行），按代码和 `agent.toml` 重写。教程叠层（`views/tutorial.rs`、`tutorial_docs.rs`）删了：A.3 之后没有入口，页面讲的是 zypi 没有的命令。`extract_user_guide_docs` 与它在 `pi-pager-bin` 里的调用删了，启动不再往 home 写文档。`status_line_tests.rs` 里把 `25-status-line.md` 当夹具的三个用例没动，对重写后的页面仍通过。两个守卫用例（页面里没有旧产品名；目录里每个文件都在 `USER_GUIDE` 里）。`pi-pager` 的测试 5,626 → 5,608。记录 [§10.14][l10-14]。
- [x] C.3 `tui/NOTICE`（产品二进制名、声明 `tui/` 为 hard fork、不再跟随 upstream）；`tui/THIRD-PARTY-NOTICES`（18,898 行，含 `async-openai`）在依赖变化后重新生成并记录方式。**状态**（r11）：`NOTICE` 重写：产品是 `zypi`，`tui/` 是 hard fork、不再跟随 upstream，并列出改动。`THIRD-PARTY-NOTICES` 不是整份重新生成：`scripts/tui_notices.py` 取 `pi-pager-bin` 的 normal 与 build 依赖闭包（五个目标三元组）里会进二进制的包，与文件对账——去掉 222 条过期的、补上 44 条缺的，手写的段落保留；文件从 18,898 行变成 16,548 行（`wc -l`）。新条目的许可证措辞抄自同一 SPDX 表达式的旧条目，工具自己不加许可证政策；没有可抄的就写成朴素格式，并在工具的输出里列为 `needs review`，要人读过。`check` 不一致就退出 1，`update --write` 来改，62 个单测。`check` 还没有接进 CI（它要 cargo；接上要改 `tui-ci.yml`，本分支没有 CI 运行，没法先试）。`third_party/` 里过期的 `nfsserve` 条目也去掉了。记录 [§10.14][l10-14]。
- [x] C.4 `packages/pi-agent-cli/AGENTS.md` 的 config 约束；`docs/TUI-AND-CODE-AGENT.md`（MCP 归属、ACP 链接应为 [agentclientprotocol.com](https://agentclientprotocol.com)）；Phase 4 spec 增补修订记录（§1.3 #2）；DESIGN §8 增补 P2 准入规则。**状态**（r11）：四项都做了。`pi-agent-cli/AGENTS.md` 写了实测的 `config.toml` 约束：`zypi` 认得的键按类型严格解析（顶层的 `permission` 写成字符串、`[[models]]` 写成数组、TOML 本身有误，都会让它以 1 退出），不认得的键忽略；Python agent 只在没有 `agent.toml` 时才回落到 `config.toml`。`TUI-AND-CODE-AGENT.md` 改了 ACP 链接、`[model]` 的真实键、斜杠命令，并写明 zypi 里没有 MCP 客户端。Phase 4 spec 的 §1.3 #2 与 §3 加了修订说明。DESIGN §8.5 写了 `pi/` 扩展的五条准入条件。另外清掉 `grok agent` 的 13 处过时指针（`pi-shell/README.md` 原是上游的用户手册，换成一页说明）。记录 [§10.14][l10-14]。

**退出条件**：`grok agent`、`leader`、`x.ai`、`MvpAgent` 在文档与代码中的 grep 结果为 0（历史文档除外）。（r11 对照：`MvpAgent` 与 `grok agent` 没有命中 ✓；只有门禁自己写出这两个词的地方除外（清单、门禁的单测、`tui.md`），`docs/PLAN` 也除外。`leader`（245 行）、`x.ai`（304 行）清不到 0 ✗：这些行在活着的代码里，是已删除的 leader 进程的名字、xAI 的服务地址，以及「不实现 `x.ai/*`」的过滤本身。所以改成棘轮：`tui_baseline.py` 的 `[gates.residue]` 给每个词一个上限，只降不升，`pytest` 也查；每一类是什么、去哪里，见 [`tui.md` §4](../baselines/tui.md)。文档里剩下的 `x.ai` 是「不实现 `x.ai/*`」的说明或历史审计，没有改。）

### 阶段 D（可选，决策门控）：依赖图与 support 层瘦身

入口条件：阶段 B 完成，且基线对比显示编译时间 / 体积 / 维护成本值得继续。

- [ ] D.1 `async-openai` 离开 `cargo tree`：需处理 `pi-tools`（139k 行，pager 仅用约 55 个类型 / 小工具路径，但渲染 crate 也依赖它）与 `pi-sampling-types`（经 `pi-agent`）。二选一：把「工具展示类型」抽到叶子 crate（`pi-tools-api` 仅 715 行，是现成落点），或对 `async-openai` 的用法做 feature-gate。
- [ ] D.2 session 数据层（`session::{storage,persistence,unified_list}`、`relay`，约 16.6k 行）。
- [ ] D.3 xAI auth 层（约 18.1k 行）。
- [ ] D.4 视 `pi-workspace`（101k 行，pager 主要用 `permission`）、`pi-agent`（`agents_modal` 删除后几乎无活引用）的剩余耦合决定是否继续。
- [ ] D.5 `pi-test-support`（7,384 行）：外部只有 `EnvGuard`、`scaled`、`resources`（`pi-shell`、`pi-tools` 的测试），和一个 `pi-pager` 测试里的 `TestProcess`、`TestSandbox` 在用；mock 推理服务器、SSE 生成器、ACP stdio 客户端、headless 运行器、计数服务器与脚本化响应共 5,003 行没有外部用户（模块之间的相互引用没有查）。出处：r11 的搜索，[§10.14][l10-14]。
- [ ] D.6 内嵌指南的查看器：`ActiveModal::DocPicker` 与 `DocViewer` 没有入口（非测试代码里，`howto_list_modal` 只被查看器的 Esc 调用，`welcome_doc_viewer` 只在测试里被赋值），启动时的解压又在 C.2 删了，所以七页指南现在没有读者。二选一：接一个入口（斜杠命令或快捷键），或连同选择器、查看器一起删。出处：[§10.14][l10-14]。
- [ ] D.7 发行与更新的遗留：`pi-pager/npm/` 下 7 个打包目录与 `pi-update` 里还写着 npm 包名 `@pi-official/grok` 和 `x.ai/cli/install.*` 的安装提示。自动更新在 Phase 4 §4.4 已禁用（`pi-pager-bin/src/main.rs` 里 `bg_update_rx = None`），这些不会跑，只是遗留。出处：[§10.14][l10-14]。
- [ ] D.8 删掉调用图工具报死、编译器也认可的 16 个模块（被删文件共 6,143 行）：`pi-hooks` 的 `dispatcher`、`result`、`runner`、`runner::command`、`runner::http`（4,339 行）；`pi-shell` 的 `session::events`、`session::telemetry`、`session::unified_list`、`config::reloader`、`auth::flow`、`upload`、`upload::turn`（1,006 行）；`pi-workspace` 的 `publish`（462 行）；`pi-tools` 的 `implementations::editor_infra`（276 行）；`pi-mcp` 的 `wire`（39 行）；`pi-agent` 的 `prompt::browser_verification`（24 行）。第 17 个报死的模块，`pi-tools` 的 `types::error`（44 行），**不能删**：里面是 `?` 要用的 `impl From<…> for ToolError`。要一起删的还有 `pi-hooks/tests/integration.rs`（822 行，测的就是被删的模块），以及删完没人用的 `env_expand::expand_env_vars_with_extra`（`pi-hooks`）。证据：在副本里这样删，三个产品 crate 的 `cargo check` 与 `cargo check --workspace --tests` 都通过，只多那一条告警。没有真的删；副本里没有跑测试，所以不知道会少几个测试。`session::events`、`session::unified_list` 与 D.2 有重叠。出处：[§10.14][l10-14]。
- [ ] D.9 `tui_baseline.py test` 在开发者自己的 `HOME` 下跑 `cargo test`，有测试会往真实的 `~/.pi-python` 与 `/tmp` 写东西。脚本只清了 `PI_HOME`、`GROK_HOME`（好让测试自己设的 `GROK_HOME` 生效），没有隔离 `HOME`。r11 在 WSL 里跑 `cargo test` 之后，`~/.pi-python` 多了 `agent_id`、`worktrees.db`、`config.toml`、`trusted_folders.toml`（18 条，全是已经不存在的 `/tmp/.tmpXXXX` 目录）、`last-copy.txt`、`logs/`；`/tmp` 里有 46 个 `grok-unified-log-test-*` 目录，r11 的运行留下 13 个，其余是更早的运行留下的。18 条信任记录里，16 条的时间与测试建这些目录的时间相差不到 5 秒；另 2 条没有对上的目录。具体是哪个测试写的，没有查。办法：测试运行时把 `HOME` 设成临时目录，并先钉住 `CARGO_HOME` 与 `RUSTUP_HOME`（不然 cargo 找不到工具链）；CI 的 runner 本来就是空 home，这样本地与 CI 更接近。没有做：要把整套测试重跑一遍（本次 22 m）才能验证，而且它改的脚本 TUI CI 也在用。出处：r11 收尾时的检查，[§10.14][l10-14]。

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
- 新增 TUI 功能（MCP、queue、subagent UI 等）——另立计划并过 P2 准入。（`/model` 不在此列：r3 已按 ADR3 经 Session Config Options 恢复。）
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
| [§10.15][l10-15] | r12 | V0.5 的发布里程碑：范围与退出条件的依据、`GROK_VERSION` 的发现（决策 16）、用户的三个回答、PR 合入与 WSL 验证（续 3）、`smoke.py` 的修复与收尾（续 4）、决策 6、8、14、15 的回答（续 5） | PR [#16][pr-16]、[#17][pr-17] |
| [附录 A.4][logA4] | r5 | 入口审计的 r5 执行结果 | [#7][pr-7] |
| [附录 B][logB] | r2–r11 | 复现方法（依赖图、模块级闭包、各轮的测量与测试命令） | — |
| [附录 C][logC] | r10 | §7 的逐项注释（原文） | — |
| [附录 D][logD] | r10 | 计划头部的状态文字（原文） | — |
| [附录 E][logE] | r2 | §4、§5 的原文（源码事实基线与改动规模估计） | — |
| [修订记录][logRev] | r1–r11 | 逐轮的完整说明 | — |

## 附录 A：能力矩阵与 ACP 版本配对（阶段 0.8，r9 定稿）

「状态」一列：**已定**＝按 r2 的默认决策做完，或有实测支撑；**提议**＝会改变行为，列在本文的「待决策」里，等用户确认；**未定**＝阶段 1 的待办，r9 没有动。r5 对初稿的执行结果在[执行记录 A.4][logA4]。测试名与脚本名都在仓库里能搜到。

### A.1 能力矩阵

| 能力 | pager 侧 | Python 现状（r9 实测） | 决定 | 状态 | 证据 |
|---|---|---|---|---|---|
| session new / load / resume / close | 标准 ACP；pager 只调用 new / load，没有 resume / close 的调用点 | 已覆盖：`new_session`、`load_session`、`resume_session`、`close_session`；`initialize` 公布 `load_session` 与 `session_capabilities.{list, resume, close}`；`run_agent(use_unstable_protocol=True)` 让 unstable 的 `close` / `resume` 在线上可用 | 保留 | 已定（r5） | `test_acp_stdio_contract.py`；`connect_runs_the_handshake_and_stop_leaves_nothing_behind` |
| session list | `ListSessionsRequest{cwd, cursor}`；选择器跟 `nextCursor`，最多 100 页 | 已覆盖：每页 50，不透明无状态游标，非法游标 `invalid_params`；`title` = 第一条 user message，`updated_at` = 文件 mtime；顺序是创建时间倒序 | 保留；「最近」的语义见决定 4，30 天截止见决定 9 | 已定（1.P1，r9） | `test_session_list_paging.py`；`session_list_pages_are_followed_by_cursor`；`session_list_bench.py` |
| session delete | `pi/session/delete`（扩展请求） | 已覆盖（`ext_method`）；SDK 0.12.1 没有标准的 `session/delete` 路由 | 保留并登记（ADR4）；SDK 补路由后迁到标准方法 | 已定 | `test_session_lifecycle.py` |
| session rename | 入口与整条链路已删 | 无 | 删 | 已定（r5） | [执行记录 A.4][logA4] |
| 恢复的入口：`--continue`、`--resume <id｜标题>` | 现在读盘解析（13 处读盘调用，[§10.11][l10-11]） | `session/list` 加 `session/load` 够用；延迟实测没有触发 ADR1 的覆盖条件 | 改为经 ACP 解析（ADR1） | 已定（ADR1）；落地设计与细节待确认（决定 1–5） | `resume_matrix.py`（9 PASS / 4 GAP）；`session_list_bench.py` |
| `zypi export` | 读旧布局的会话文件；PR（r12 续 6）合入后改为经 ACP 回放：起 agent、`session/load`、收 `session/update` | 没有专门的方法，用已有的 `session/load` 回放 | 决定 6 的 (a)：经 ACP 回放 | **已定 (a)，已做成 PR**（r12 续 5、续 6） | `resume_matrix.py` 用例 12、13；`export_cmd::tests`；`python_agent_e2e` |
| prompt / cancel | 标准 ACP | 已覆盖；取消后正在跑的 `bash` 进程被回收（Linux 的用例） | 保留 | 已定 | `a_prompt_streams_the_reply_and_ends_the_turn`；`cancel_ends_a_running_tool_as_cancelled_and_reaps_it` |
| 权限请求 | 应答 `session/request_permission`；yolo 模式下 pager 自己代答（选 `AllowOnce`，`acp_handler/permissions.rs`） | 已覆盖：`ask` 模式下问 `bash` / `edit` / `write` / `workflow`（没声明无害的工具，AGENTS.md 不变量 8）；工具调用的选项只有 allow-once / reject-once（`permissions.py`）；项目信任的问题另带 `allow_always`（`trust_prompt.py`） | 保留；工具调用不做 allow-always 与规则 | 已定：pager 不依赖 allow-always（PTY 里两个选项的问题框能画、能答）；提议：不做 | `a_permission_question_reaches_the_client_and_its_answer_decides`；`mode_probe.py` |
| 模型选择 | 读 `configOptions`（`category: "model"`），`session/set_config_option`；没有该配置项时退回 `session/set_model` | `configOptions`（`model`）与 `set_config_option`（只认 `model`，其他 `invalid_params`）；`[[models]]` 提供候选；`session/set_model` 没有路由，但 Python 总是公布 `model` 配置项，退回路径用不到 | 恢复（ADR3） | 已定（r3） | [§10.2][l10-2]、[§10.3][l10-3]；`test_acp_agent.py` |
| 模式切换 | **r10 起**：Shift+Tab 在 Normal ↔ Always-Approve 之间切（r9 时是 Normal → Always-Approve → Normal → Plan → Auto）、`--always-approve`、`--permission-mode default｜bypassPermissions`（r9 时六个值）；`pi/yolo_mode_changed` 通知；`session/new` 的 `_meta.yoloMode`（`autoMode` 不再为真）。Plan / Auto / 设置页的 Default 藏在 `app/agent_modes.rs` 的三个常量后面，`session/set_mode` 不再发出（[§10.13][l10-13]） | 只认 `pi/yolo_mode_changed` 里的 `ask` / `auto` / `always-approve`；`session/set_mode` → `method_not_found`；`_meta` 不读；`auto` 等于 `always-approve`；`default` 被丢弃 | 隐藏 Plan / Auto / Default；`--permission-mode` 只收有效的值；做不做真的 Plan 另议（决定 7） | (a) **已做**（r10，PR [#12][pr-12]）；真的 Plan（`session/set_mode` 加只读工具集）与真的 Auto 是提议（决定 7 的 (b)、(c)），Python 一侧的行为没变 | `mode_probe.py`（[§10.13][l10-13] 的表是改后的；改前的 12 行表在 [§10.11][l10-11]）；`agent_modes::tests` |
| MCP | UI 已删（r5）；不再发 `mcp_servers`（`7becb49`） | 忽略 `mcp_servers`，会话开始时说一次哪些被忽略（`mcp_notice.py`） | 降级为显式忽略；ACP v1 要求 agent 支持 stdio MCP，这是有文档的偏离 | 已定（默认决策，r9 实现） | `test_mcp_notice.py`；`session_new_and_load_send_no_mcp_servers` |
| queue / interjection | 已删（r5，含 steer、send-now） | 无 | 删 | 已定（r5） | [执行记录 A.4][logA4] |
| compaction | 触发与展示 | 部分：`auto_compact` 由 `max_turns >= 30` 推导 | 降级 | 未定（r9 没有重新核对） | — |
| subagents / 后台任务 | 已删（r5） | 无 | 删（Phase 4 非目标） | 已定（r5） | [执行记录 A.4][logA4] |
| skills | slash 命令与 `available_commands_update` | 已覆盖；`available_commands_update` 在响应之后发（Zed 要收到响应才登记会话，`new_session` 里有注释） | 保留 | 已定 | `test_acp_agent.py` |
| hooks / plugins / marketplace | 已删（r5） | 无 | 删（Phase 4 非目标） | 已定（r5） | [执行记录 A.4][logA4] |
| image | 图片附件 | 代码有（`prompt_capabilities.image`、`_prompt_to_text_images`），**`packages/pi-agent-cli/tests` 里没有走这条路径的用例** | 保留 | 已定；缺测试 | — |
| status line（`[ui.status_line]`） | pager 本地画一行（命令脚本或内置字段，`pi-status-line`），数据来自 agent 发的 `session_status` 通知，经 `_pi/session_notification` 载体；`ConnectFlags.status_line` 已不再公布给 agent（`client_capabilities_meta` 返回空） | **从不发**那条通知，所以这一行在 Python agent 下不画，命令脚本也不跑（r11 实测） | 决定 15：Python 发（要过 P2）、pager 本地画、或删除；那个没人读的字段是清理候选 | **提议**（r11，决定 15）；其余不挡阶段 1 | `status_line_probe.py`（4/4，[§10.14][l10-14]） |
| 历史回放 | `session/load` | 已覆盖（`isReplay` 的 `session/update`）；`session/resume` 不回放 | 保留 | 已定 | `a_session_is_listed_with_a_title_and_replayed_by_a_later_agent` |
| 登录 | `auth_methods` 为空时不要求登录 | `auth_methods=[]`；`authenticate` 没有处理器 | 保留 | 已定 | `connect_runs_the_handshake_and_stop_leaves_nothing_behind` |
| `initialize._meta`：响应里的键 | 解 `modelState`、`grokShell`、`availableCommands`、`cancelRewind`、`sessionRecap`、`feedbackTraceOffer` | Python 的响应没有 `_meta`，都落到缺省 | 逐键删解码（1.R5） | 未定（阶段 1 待办） | — |
| `initialize._meta`：请求里的键 | 发 `clientType`、`clientVersion`；带 `--system-prompt-override` / `--rules` 时再发 `systemPromptOverride`、`rules` | 一个都不读 | 要么 Python 读，要么 pager 拒绝这两个旗标（决定 8） | **提议** | [§10.11][l10-11]「旗标」 |
| `--session-id` | `session/new` 的 `_meta.sessionId`；占用检查读旧布局 | **r10：兑现 `_meta.sessionId`，已用的 id 以 `invalid_params` 拒绝**（r9 实测时是忽略） | Python 兑现，重复由 agent 拒绝（决定 3） | Python 一侧**已做**（r10，PR [#11][pr-11]）；pager 的占用检查仍读旧布局，所以重复的 id 要进了 TUI 才看到 agent 的报错（PR 2） | `resume_matrix.py` 用例 10（PASS）、11（GAP）；`test_session_chosen_id.py` |

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
| `session/new` | `cwd`，空的 `mcp_servers`；`_meta`：`yoloMode`、`autoMode` 总有，`agentProfile`、`askUserQuestion: false`、`modelId`、`sessionId` 视情形（`SessionFlags::to_meta`） | 建会话；响应带 `configOptions`（`model`）与 `_meta`（`pi/currentModelId`、`pi/currentModelDisplayName`、`pi/provider`，r10 起还有 `pi/cwd`）；`mcp_servers` 非空时通知一次；**请求的 `_meta` 只读 `sessionId`**（r10；r9 实测时一个都不读）：SDK 把它摊平成关键字参数，`yoloMode`、`autoMode`、`agentProfile`、`askUserQuestion`、`modelId` 仍然没有人取用 | `resume_matrix.py`；`test_session_chosen_id.py` |
| `session/load` | `session_id`、`cwd`、空的 `mcp_servers`、同样的 `_meta` | 先回放历史（`isReplay` 的 `session/update`）再回应；工作目录用会话保存的 `cwd`（没有才用请求的）；未知 id → `resource_not_found`；响应带 `_meta["pi/cwd"]`（r10；r9 实测时不带），也就是上面那个工作目录，`session/resume` 同 | `a_session_is_listed_with_a_title_and_replayed_by_a_later_agent` |
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
- r12：V0.5 的发布里程碑（分支 `docs/v0.5-milestone`，只改文档）。按剥离的现状定范围：阶段 0、A、C 完成，阶段 1 完成一半，阶段 D 没有开始，V0.5 不等它们；写六项退出条件（CI、体积再量、发布构建演练、与 v0.4.0 对照、发布说明、打 tag）、带着发布的已知问题、不在 V0.5 的项；建 GitHub 里程碑 [v0.5.0][ms-v050]；新增待决策 16（发行构建的版本戳：`zypi --version` 与「是不是发行构建」共用 `GROK_VERSION`，不能靠在 `release.yml` 里设它来改版本号）；状态更新到 `main` 的 `4fc028a`。里程碑建好之后，用户选了四件事，都在别的 PR 或 CI 运行里做了，本 PR 只记录：并入 `[Unreleased]` 并补条目（PR [#15][pr-15]，条件 5）；给 `release.yml` 加手动演练（PR [#14][pr-14]，条件 3），并从分支跑了一次（[run 38223353755][run-dry-run]：Linux 与 macOS arm64 都构建成功，什么也没发布）；`release_baseline`（[run 38221952524][run-baseline]，420,767,096 B，条件 2）；推送本分支并开 PR。决策 16 选了 (a)。之后用户又答了三个问题：决策 16 现在做，另开 PR（[#17][pr-17]，叠在 #15 上：crate 版本改成 0.5.0，`agentInfo.version` 读已装包的版本，两个新测试文件）；macOS 二进制的条目写进 #15（`CHANGELOG.md`、`README.md`、`docs/TUI-AND-CODE-AGENT.md`，注明「CI 构建，没有人运行过」）；条件 4 不做，V0.5 不等它。续 3：用户让先把 PR 合入；#15、#17、#14 依次 squash 合入（`59545c1`、`d57fd9b`、`0efd400`），本分支改基到 `0efd400`。用户也修好了 WSL：在 `0efd400` 上跑了 Linux 的 `pytest`（2,539 通过 / 16 跳过 / 31 取消选择）、`zypi --version`（`zypi 0.5.0 (0efd40071ca0)`）和 `scripts/tui_pty/` 的脚本（`smoke.py` 22/23，其余符合预期；`smoke.py` 里失败的那一项检查过期了，不是 TUI 的退化）。续 4：这项过期的检查修好了（用户说不另开 PR，直接提交到 `main`）：`smoke.py` 23/23，加 `--sandbox workspace` 26/26；GitHub 里程碑的描述改到合入之后。续 5：用户答了决策 6、8、14、15：6 与 15 选 (a)（都还没做），8 与 14 待定；最后一个本地旧分支也删了。续 6：决策 6 的 (a) 做成了 PR（分支 `feat/export-via-acp-replay`，没有合入）：`zypi export <id>` 改问 agent，经 `session/load` 回放；18 个单元测试、一个端到端测试；`resume_matrix.py` 11 PASS / 2 GAP / 0 FAIL；调试构建下，2,000、5,000、20,000 条的会话要 2.5、5.7、63 s；发现三处没有修的问题（agent 读到条目 id 重复的文件会死循环；基准脚本在 4,096 条之后写出这样的文件；`isReplay` 在 agent 与 pager 里放的 `_meta` 层级不同）。没做：运行 CI 的 `release-dist` 产物。见 [§10.15][l10-15]。

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
[ci-run-r5]: https://github.com/zy1233/pi-python/actions/runs/37379203134
[ci-run-4fc028a]: https://github.com/zy1233/pi-python/actions/runs/38196042958
[tui-ci-run-4fc028a]: https://github.com/zy1233/pi-python/actions/runs/38196042998
[ci-run-0efd400]: https://github.com/zy1233/pi-python/actions/runs/38233821853
[tui-ci-run-d57fd9b]: https://github.com/zy1233/pi-python/actions/runs/38233778549
[ms-v050]: https://github.com/zy1233/pi-python/milestone/1
[run-baseline]: https://github.com/zy1233/pi-python/actions/runs/38221952524
[run-dry-run]: https://github.com/zy1233/pi-python/actions/runs/38223353755
