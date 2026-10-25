# Rust Agent Runtime 剥离计划

> **状态：r22（2026-10-25）。** 用户答复了两个问题：tag 打在 `5f73e07`，#40 与 #41 在打完 tag 之后合入；由我打 tag，再做发布后的核对（[§10.25][l10-25]）。**v0.6.0 已发布；V0.6 的六项退出条件都有结果；核对没有发现缺陷**。① tag：`v0.6.0`（带注释，tag 对象 `6b92a0d`）在 `5f73e07`，2026-10-25 17:20（+08:00）推送。② [`Release` 的运行][run-release-v060]一次就全部成功（测试 266 s，Python 包 32 s，`publish-pypi` 31 s，Linux 构建 584 s，macOS 构建 922 s，`GitHub Release` 25 s；macOS 第一次就过了 `Install protoc`）；v0.5.0 的发布跑了三次。③ PyPI：七个包是预期的版本；`pi-mcp-py` 0.1.0 是新项目，第一次上传成功；14 个文件的 SHA-256 与 GitHub Release 上同名的资产逐个相同。④ [GitHub Release][rel-v060]：16 个资产（14 个 Python 文件、`zypi-linux-x86_64` 406,911,320 B、`zypi-macos-arm64` 46,652,200 B）；正文等于 `CHANGELOG.md` 的 `[0.6.0]`。⑤ 核对（Linux，WSL2）：干净的 venv 里装七个包，`uv pip check` 通过，四个扩展包能加载；`pi-mcp` 的 192 个测试对着从 PyPI 装的包通过；发布的 `zypi`（与 `Release` 运行的构建产物逐字节相同）加 PyPI 的 agent：`smoke` 23/23、`exit_matrix` 12 个用例、`resume_matrix` 16 通过、`mode_probe` 的表如预期、`status_line_probe` 4/4。⑥ 合入：#40（`75f83ae`，探针的 3 行修复）；本 PR（#41）在核对之后合入。tag 在 `5f73e07`，`main` 比它多这两个提交。⑦ 留下的（都没有改）：`release.yml` 的 Linux 构建用 `ubuntu-latest`，GitHub 要把它迁到 Ubuntu 26，而发布的二进制要 glibc 2.39 或更新（要用户定要不要钉 `ubuntu-24.04`）；glibc 的下限没写在文档里；macOS 的二进制没有人运行过；Windows 上没有从 PyPI 装过；真的 Zed 里没试过 `pi-mcp-py`。r21（2026-10-25）：用户说「开始下一步」，然后答复了两个问题：现在合入 #39 与 #38；条件 4（与 v0.5.0 对照）做（[§10.24][l10-24]）。V0.6 的条件 1–5 现在都有结果，只剩条件 6（打 tag；谁来打，由用户定）。① 条件 5：[#39][pr-39]（核心、harness、cli 升到 0.6.0；三个已发布的扩展包升到 0.3.0，`pi-mcp-py` 仍是 0.1.0；四个扩展包的核心下限抬到 `>=0.6.0`；`pi-pager-bin`、`pi-version` 与 `tui/Cargo.lock` 升到 0.6.0；`CHANGELOG.md` 的 `[0.6.0]` 与 9 条已知问题；没有代码）。**更正** r20 的一句话：`pi-dynamic-workflows-py` 还有一个 harness 下限，也要抬到 `>=0.6.0`。② 合入：[#38][pr-38]（`60864af`）与 #39（`5f73e07`），都用 `gh pr merge --squash`；每个提交的树等于它的 PR 分支头的树（`5f73e07` 再加 #38 的四个文档文件）。③ 条件 1–3 在 `5f73e07` 上都通过：[`CI`][ci-run-5f73e07]（`pytest` 在 Python 3.11、3.12、3.13 上都是 2,908 通过 / 39 跳过 / 31 取消选择）；[`TUI CI`][tui-ci-run-5f73e07]（8,490 通过 / 0 失败 / 22 跳过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297；`zypi` **406,911,320 B**，比 v0.5.0 发布件小 13,809,288 B；构建 895 s；`--version` 22.4 ms）；`Release` 的演练 [run][run-dry-run-5f73e07]（全部成功；macOS arm64 第一次就构建成功；不发布）。④ 条件 4：v0.5.0 的 `zypi` 与新的 `zypi`，同一个 Python agent：`smoke`、`exit_matrix`、`status_line_probe` 相同；`resume_matrix` 13 通过 / 3 失败 → 16 通过（三个差别是计划预计的，`CHANGELOG.md` 里有一条）；`mode_probe` 有一行不同，是探针的竞争，不是构建的差别，修复是 PR [#40][pr-40]（没有合入）。⑤ 没有打 tag，没有发布；`CHANGELOG.md` 里的日期 `2026-10-25` 是占位。r20（2026-10-25）：用户在 2026-10-23 回来，做了两件事：定 MCP 的方向，合入 #33–#36（[§10.23][l10-23]）。① MCP：用户选了可选扩展包。core、harness 与 CLI 仍没有 MCP 客户端；新包 `pi-mcp-py` 连接编辑器（Zed 等）传来的 server（决策 19）。做成三个叠在一起的 PR：[#34][pr-34]（Zed 重启后，没人发过消息的对话能再打开；决策 17 有更正）、[#35][pr-35]（给扩展的挂钩）、[#36][pr-36]（`pi-mcp-py` 0.1.0，含发布内容）。② 合入：[#33][pr-33] 用 `gh pr merge --squash`；#34–#36 用 `gh stack link 34 35 36`（stack #37）与 `gh stack merge 37 --yes --squash`。`main` 从 `b33017a` 到 `e0f2a36`，4 个 squash 提交；每个提交的树等于它的 PR 分支头的树加上 #33 的两个文档文件。`CI` 在 `e0f2a36` 上通过（[run][ci-run-e0f2a36]），`TUI CI` 也通过（[run][tui-ci-run-e0f2a36]：8,490 通过 / 0 失败 / 22 跳过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297）。③ V0.6 的范围变了：14 个提交，多一个包（`pi-mcp-py`）。**更正** r19 的一句话：三个扩展包不能「不动」：核心升到 0.6.0 时，四个扩展包的核心下限都要变成 `>=0.6.0`（条件 5；决策 20）。④ 暂停结束；六项退出条件仍都没有满足。⑤ 之后（2026-10-25）用户对决策 20 与 21 答「按你的倾向来」，所以新增的决策 19、20、21 都已定（[§10.23][l10-23]）。决策 20：升版本号时，四个扩展包的核心下限一起抬到 `>=0.6.0`，三个已发布的扩展包从 0.2.0 抬到 0.3.0，`pi-mcp-py` 仍是 0.1.0；决策 21：MCP 设计里的 11 个默认值全按默认。r19（2026-10-20）：用户要三件事：用 `gh stack` 把当前的 5 个 PR（#27–#31）合入 `main`；按现状设 V0.6 的发布里程碑；开发先停 1–2 天（[§10.22][l10-22]）。① 合入：`gh stack link` 在 GitHub 上建了 stack #32，`gh stack merge 32 --yes --squash` 一次原子合入；`main` 从 `7d82c27` 到 `b33017a`，5 个 squash 提交，每个提交的树等于它的 PR 分支头的树。`CI` 与 `TUI CI` 在 `b33017a` 上通过（[`CI`][ci-run-b33017a]、[`TUI CI`][tui-ci-run-b33017a]：8,490 通过 / 0 失败 / 22 跳过）。② 里程碑：GitHub 里程碑 [v0.6.0][ms-v060]；计划新增[发布里程碑 V0.6](#发布里程碑-v06)：发 `main` 上 v0.5.0 之后的 10 个提交；六项退出条件都还没有满足。③ 暂停：开发停 1–2 天；暂停期间不开 PR，不启动发布验证的 CI。r18（2026-10-20）：用户从「接下来做哪些」里选了三项（`release_token`、`protoc_abs`、`nextest_ci`），各做成一个 PR，一个叠在一个上，生产代码没有行为变化，都没有合入（[§10.21][l10-21]）：[#29][pr-29]（`release.yml` 里两处 `arduino/setup-protoc` 加 `repo-token`）；[#30][pr-30]（`find_protoc()` 返回 `PATH` 上 `protoc` 的绝对路径，同一条 `cargo` 命令重复运行时不再每次重新构建 `pi-tools-api`；**更正** r17 的一句话：CI 的 `cargo test` job 里 `pi-tools-api` 编译 7 次不是这个问题造成的）；[#31][pr-31]（`TUI CI` 与基线执行器改用 `cargo nextest`：一个测试一个进程，r17 查的那一类竞争在 CI 里不会再出现）。`TUI CI` 通过：8,490 通过 / 0 失败 / 22 跳过，整棵树第一次在 nextest 下跑（[`CI`][ci-run-1a49eef]、[`TUI CI`][tui-ci-run-1a49eef]）。没选的：在 `tui-ci.yml` 里设 `PROTOC`、决策 10 的另两项、合入 #27 与 #28。r17（2026-10-19）：查了测试之间的竞争（`race_audit`）。这是一个只改测试与工具的 PR：[#28][pr-28]（叠在 [#27][pr-27] 上；没有合入；[§10.20][l10-20]）。压力运行（随机顺序，32 个测试线程）找到 19 个偶发失败的测试：3 个是测试自己的缺陷，16 个由进程级的 `static`（颜色等级、最小模式的标志、双向文字的闩、内嵌弹窗的标志、语音的门、主题）引起。另有 5 个测试改了环境变量却不还原。这些都在测试里修了：改进程级状态的 57 个测试，现在各在自己的进程里跑（`in_own_process`）。`pi-pager` 的 lib，32 线程、随机顺序：6/32 遍有失败 → 0/60。生产代码没有改。最后的提交上没有在本地跑测试（磁盘满了；见 [§10.20][l10-20] 的「验证」），CI 在 `ea9609f`（比最后的代码提交只多一个只改文档的提交）上第一次跑了测试，通过了（[`CI`][ci-run-ea9609f]、[`TUI CI`][tui-ci-run-ea9609f]：8,490 通过 / 0 失败 / 21 忽略）。r16（同一天）：合入了 [#25][pr-25]（`2b0baf3`）与 [#26][pr-26]（`7d82c27`）；`CI` 与 `TUI CI` 在两个 `main` 提交上都通过。随后删了 `pi-shell` 里剩下的死代码：15 个没人加载的文件，调用图报死的 `session::worktree` 与 `session::helpers`，`[storage]` 设置，10 个源码没点名的依赖；`leader`、`x.ai` 的上限降到 243 与 297；依赖图 980 → 970。这是 PR [#27][pr-27]（`CI` 与 `TUI CI` 通过：[`CI`][ci-run-6bd7db5]、[`TUI CI`][tui-ci-run-6bd7db5]；没有合入；[§10.19][l10-19]）。r15（同一天）：决策 10 的存储层一项做成了：`pi-shell` 里的旧存储层 `session::{storage, persistence}`，和只有它们在用的代码，删掉了（决策 10 的另两项没做）。这是 PR [#26][pr-26]（叠在 [#25][pr-25] 上，r16 合入；[§10.18][l10-18]）。r14（同一天）：用户在 2026-10-18 答复了四个问题（[§10.17][l10-17]）：ADR1 的 PR 2（[#23][pr-23]）与 PR 3（[#24][pr-24]）现在合入：两个都已合入 `main`；翻页的代价（决策 18）不做；空会话（决策 17）要做：做成了 PR [#25][pr-25]（r16 合入）；删 `pi-shell` 里的旧存储层（决策 10）要做：r15 做了。r13 在 2026-10-16（[§10.16][l10-16]），r12 在 2026-10-12，续 8 在 2026-10-13。r3–r11 的工作已全部合入 `main`（PR [#7][pr-7]、[#8][pr-8]、[#9][pr-9]、[#10][pr-10]、[#11][pr-11]、[#12][pr-12]、[#13][pr-13]）。r12（[§10.15][l10-15]）：按剥离的现状定下 V0.5 的发布里程碑。这个 PR（[#16][pr-16]）只改文档；里程碑的条件由三个别的 PR（[#14][pr-14] 发布演练，[#15][pr-15] 发布说明，[#17][pr-17] 版本号；三个都已合入 `main`）和两次 CI 运行去做。续 8（2026-10-13）：用户让把决策 6 与 15 的两个 PR（[#18][pr-18]、[#19][pr-19]）合入 `main`、放进 V0.5，再打 tag 发布；**v0.5.0 已在 2026-10-13 发布**（tag 在 `be3f8a8`，[GitHub Release](https://github.com/zy1233/pi-python/releases/tag/v0.5.0)；[§10.15][l10-15] 续 8）。做到哪了，见[当前状态](#当前状态)；V0.5 要做什么，见[发布里程碑 V0.5](#发布里程碑-v05)；V0.6 要做什么，见[发布里程碑 V0.6](#发布里程碑-v06)。
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
| 能力矩阵、ACP 版本配对 | 附录 A |

## 当前状态

r22（2026-10-25）：用户答复了两个问题（tag 打在 `5f73e07`，#40 与 #41 在打完 tag 之后合入；由我打 tag 并做核对）；我打了 tag `v0.6.0`，做了发布后的核对（[§10.25][l10-25]）。① tag：`git tag -a v0.6.0 5f73e07`，`git push origin v0.6.0`；tag 对象 `6b92a0d9834eec22d608f2f4cc22423ace3b56b4`，2026-10-25 17:20:43（+08:00）。`CHANGELOG.md` 的日期 `2026-10-25` 就是这一天，不用改。② [`Release` 的运行][run-release-v060]：20 m 22 s，六个 job 一次全部成功（v0.5.0 跑了三次）。测试 266 s；Python 包 32 s；`publish-pypi` 31 s（09:26:15Z–09:26:28Z 传了 14 个文件）；Linux 构建 584 s、macOS 构建 922 s（都恢复了更早存下的 Cargo 缓存，所以比演练快）；`GitHub Release` 25 s，09:41:12Z 发布。macOS 第一次就过了 `Install protoc`（#29 的 `repo-token` 在真 tag 上的第一个结果）。③ PyPI：核心、harness、cli 是 0.6.0，三个已发布的扩展包是 0.3.0，`pi-mcp-py` 是 0.1.0（新项目；第一次上传成功，令牌能建项目）。14 个文件的 SHA-256 与大小，和 GitHub Release 上同名资产的摘要逐个相同。④ GitHub Release：[v0.6.0][rel-v060]，标成 Latest，16 个资产；正文（33,720 个字符）等于 `CHANGELOG.md` 的 `[0.6.0]` 一段。⑤ 两个二进制：Linux 的 `zypi-linux-x86_64` 406,911,320 B，SHA-256 `3bbabd33…c9d6`，与构建产物逐字节相同，`zypi --version` 打印 `zypi 0.6.0 (5f73e07964f1)`；要 glibc 2.39 或更新。macOS 的 `zypi-macos-arm64` 46,652,200 B，是 Mach-O arm64 的文件，没有人运行过。⑥ 核对（WSL2，Python 3.12.3）：干净的 venv 里装七个包，63 个包，`uv pip check` 通过，四个扩展包的入口点能加载；`pi-mcp` 的 192 个测试对着从 PyPI 装的包通过（89.68 s）；PTY 脚本对着发布的 `zypi` 与 PyPI 的 agent：`smoke` 23/23，`exit_matrix` 12 个用例都没留下进程，`resume_matrix --strict` 16 通过，`mode_probe` 15 行如预期，`status_line_probe` 4/4。第一次运行时 `exit_matrix` 与 `mode_probe` 没有起来：我把脚本复制到了高一层的目录，它们找不到替身 agent；这不是构建的缺陷，放对层次后第二次通过。⑦ 合入：#40（`75f83ae`；树等于它的分支头的树）。`git diff v0.6.0 origin/main` 只有 `scripts/tui_pty/mode_probe.py` 的 3 行。本 PR（#41）在核对之后合入。⑧ GitHub 里程碑 [v0.6.0][ms-v060]：描述改成 RELEASED，关闭（V0.5 的里程碑也是这样关的）。⑨ 留下的（都没有改，要用户定或以后做）：`release.yml` 的 `build-tui` 用 `ubuntu-latest`，运行的通知说它要迁到 Ubuntu 26（`tui-ci.yml` 钉 `ubuntu-24.04`）；glibc 2.39 的下限没写在文档里；Node.js 20 弃用的警告；PyPI 一侧的 Trusted Publishing。没验证：macOS 的二进制没人运行过；从 PyPI 装只在 Linux 上做；核心、harness、cli 的测试没有对着 wheel 跑；真的 Zed 与真的 LLM 没有用；发布后十分钟内，`uv` 或 `pip` 可能装不到新版本（索引缓存）。做法与数字见 [§10.25][l10-25]。

r21（2026-10-25）：用户说「开始下一步」；我做了 V0.6 的条件 5，用户答复了两个问题（现在合入 #39 与 #38；条件 4 做），我合入两个 PR 后做了条件 1–4（[§10.24][l10-24]）。① 条件 5（[#39][pr-39]，分支 `chore/prepare-0.6.0`；11 个文件，+40 / −22 行，没有代码）：`pi-agent-core-lc`、`pi-agent-harness-lc`、`pi-agent-cli-lc` 升到 0.6.0；`pi-web-access-py`、`pi-goal-x-py`、`pi-dynamic-workflows-py` 升到 0.3.0；`pi-mcp-py` 仍是 0.1.0；四个扩展包的核心下限抬到 `>=0.6.0`（决策 20）；`pi-pager-bin`、`pi-version` 与 `tui/Cargo.lock` 里这两项升到 0.6.0；`CHANGELOG.md`：`[0.6.0] - 2026-10-25`、新的空 `[Unreleased]`、开头一段、一条「新版本号」、9 条已知问题。**更正** r20：`pi-dynamic-workflows-py` 还有一个 harness 下限，也要抬（测试对每个扩展包只报一个失败，所以 r20 的「4 个失败」没有露出它）。0.3.0 这一位是我照 V0.5 选的，用户没有单独说。② 合入：[#38][pr-38]（`60864af`）、#39（`5f73e07`），`gh pr merge --squash`，先 #38；`60864af` 的树等于 #38 的分支头的树，`5f73e07` 的树等于 #39 的分支头的树加上 #38 的四个文档文件。③ 条件 1：`5f73e07` 上，[`CI`][ci-run-5f73e07] 通过（`pytest` 在 Python 3.11、3.12、3.13 上都是 2,908 通过 / 39 跳过 / 31 取消选择）；[`TUI CI`][tui-ci-run-5f73e07] 通过（手动触发，勾选 `release_baseline`；推送触发的那次被它取消了）：8,490 通过 / 0 失败 / 22 跳过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297，`grok agent` 0。④ 条件 2：`zypi` **406,911,320 B**，不大于 v0.5.0 发布件的 420,720,608 B（小 13,809,288 B，3.3 %），比拆除前的 470,461,776 B 小 13.5 %；构建 895 s（V0.5：1,194 s）；`--version` 中位数 22.4 ms。⑤ 条件 3：`Release` 的演练 [run][run-dry-run-5f73e07]（`5f73e07`）：全部成功：测试 217 s（2,908 通过），七个 Python 包的构建 29 s，Linux x86_64 的构建 17 m 36 s，macOS arm64 的构建 29 m 51 s（第一次就成功）；`publish-pypi` 与 `github-release` 跳过。⑥ 条件 4（WSL2；v0.5.0 的 `zypi-linux-x86_64` 与演练产出的新 `zypi`，同一个 Python agent）：`smoke` 两边都是 23/23，`exit_matrix` 两边都是 12 个用例都没留下进程，`status_line_probe` 两边都是 4/4；`resume_matrix` 13 通过 / 3 失败 → 16 通过 / 0 失败，三个失败的用例（`--resume <标题>`、`--resume <未知 id>`、`--session-id <用过的 id>`）是计划预计的，`CHANGELOG.md` 里有一条；`mode_probe` 有一行不同（保存的 `always-approve` 的标签），是探针的竞争：同一个 v0.5.0 二进制 17 次里 3 次没有标签，探针加了 1 s 的等待之后，v0.5.0 37 次、新的 `zypi` 13 次都有，两张完整的表相同；修复是 PR [#40][pr-40]（`CI` 通过，没有合入）。⑦ 没有打 tag，没有发布。V0.6 的条件 1–5 都有结果，只剩条件 6（谁来打 tag，由用户定）。`CHANGELOG.md` 里的日期 `2026-10-25` 是占位：在另一天打 tag，就在 tag 的提交里改它。⑧ 没验证 / 留下的：条件 4 只在 Linux 做，macOS 的二进制仍没有人运行过；v0.5.0 的 `zypi` 是对着新的 Python agent 跑的，只量 `zypi` 一侧的差别；条件 1–3 在 `5f73e07` 上做，tag 若打在之后的提交上，要核对 `tui/` 与 `packages/` 的树没变；`mode_probe` 里新旧构建缺标签的概率不同（17 次里 0 次对 3 次），没有查；`PYPI_API_TOKEN` 能不能为 `pi-mcp-py` 建新项目，从这里查不了；macOS 的构建这次一次成功，不证明 `setup-protoc` 的限额问题已经解决；GitHub 里程碑的描述我改写了（它还是 r19 的内容）。做法与数字见 [§10.24][l10-24]。

r20（2026-10-25）：用户在 2026-10-23 回来；先是 Zed 里的两个问题，后来是让我补发布内容并合入 #33–#36。① 两个问题（[§10.23][l10-23]）：每个新对话开头的 MCP 提示；退出 Zed 后，没发过消息的对话打不开。原因：agent 从来没有 MCP 客户端（Phase 6 的决定，r9 只是把「静默丢弃」改成「明说」）；决策 17 让没发过消息的会话没有文件，Zed 重启后用 `session/load` 要同一个 id，得到 `resource_not_found`。用户答复：MCP 做成可选扩展包；空白对话的修复现在发 PR。② 三个叠在一起的 PR：[#34][pr-34]（`session/load` 与 `session/resume` 对没有文件的规范 UUID 开一个空会话；决策 17 的更正；17 + 2 个新测试，旧 agent 上 12 个失败）；[#35][pr-35]（给扩展的挂钩：客户端传来的 server、`pi.notify`、`pi.add_turn_barrier`；MCP 提示改写；再次打开同一个会话时关旧的 harness；103 个新测试，4 处单行变异都被抓住）；[#36][pr-36]（新包 `pi-mcp-py` 0.1.0；192 个测试，28 处单行变异都被抓住；真的 context7 server 与真模型各跑一次，模型调用了 `mcp__mcp-server-context7__resolve-library-id` 并答出库的 id）。③ 发布内容（用户要求在合入前补上；#36 的最后一个提交）：`release.yml` 的 `test` job 装这个包，`build` job 加 `Build pi-mcp`；README、包的 README、CHANGELOG、设计文档 §9 同步。`actionlint`、本地构建与 `twine check --strict` 通过；`Release` 的演练通过（[run][run-dry-run-eda4aac]：发布跳过，`dist` 14 个文件，`twine check --strict` 都通过；两个 TUI 构建 job 我取消了，这个改动不碰它们）。④ 合入：[#33][pr-33] 用 `gh pr merge --squash`（`ca2ab85`）；#34–#36 用 `gh stack link 34 35 36`（stack #37）与 `gh stack merge 37 --yes --squash`（`fa4887b`、`34c11d9`、`e0f2a36`）。每个提交的树等于它的 PR 分支头（`79f41ad`、`d56e95e`、`eda4aac`）的树加上 #33 的两个文档文件。4 个远端分支被 GitHub 删了；本地的 4 个分支我也删了。`CI` 在 `e0f2a36` 上通过（[run][ci-run-e0f2a36]）：`pytest` 在 Python 3.11、3.12、3.13 上都是 2,908 通过 / 39 跳过 / 31 取消选择；`TUI CI` 也通过（[run][tui-ci-run-e0f2a36]）：`cargo nextest` 8,490 通过 / 0 失败 / 22 跳过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297，`cargo check --workspace --tests` 0 个错误。⑤ **更正** r19 的「三个扩展包不动」：`test_extension_packaging.py` 要求扩展包写的核心下限等于工作区里核心的版本，所以核心升到 0.6.0 时，四个扩展包（三个已发布的，加 `pi-mcp-py`）的下限都要变成 `>=0.6.0`（本地验证：4 个失败）；新增决策 20（已定，见 ⑦）。⑥ 计划里改的：决策 17 加更正；新增决策 19（MCP 的方向）、20、21（都已定，见 ⑦）；V0.6 的范围、条件 1、3、5、6 与已知问题改写；暂停结束；`docs/TUI-AND-CODE-AGENT.md` 里「MCP servers are not connected」一段改了（#35、#36 漏改了它）。⑦ 用户的答复（2026-10-25，PR [#38][pr-38] 打开之后）：对决策 20 与 21 答「按你的倾向来」。决策 20：照倾向 (a)：升版本号时（条件 5），四个扩展包的核心下限一起抬到 `>=0.6.0`；`pi-web-access-py`、`pi-goal-x-py`、`pi-dynamic-workflows-py` 从 0.2.0 抬到 0.3.0（小版本，照 V0.5 的 `7e7d4d4`）；`pi-mcp-py` 保持 0.1.0（第一次上传）。倾向里原来没写抬哪一位；0.3.0 是我照 V0.5 选的，用户没有单独说。决策 21：11 个默认值全按默认；代码与默认值都不改，设计文档 §10 只加了一句话，说明用户已确认。两项都没有改代码：决策 20 的改动做在条件 5 的 PR 里，那个 PR 还没开。⑧ 没验证 / 留下的：新项目的第一次上传需要的 PyPI 令牌（从这里查不了）；「0.6.0 起在 PyPI 上」是我对下一个版本号的假设；#35 的提示叫用户装 `pi-mcp-py`，那个包要等发布才在 PyPI 上；没有在真的 Zed 里看过 #34 的修复与 `pi-mcp-py`；用户说的「不重启 Zed，新建对话还是错误」没有找到独立的原因；OAuth 等没做；设计文档的 11 个默认值：用户没有逐个确认，只答了「按你的倾向来」（⑦）；V0.6 的六项退出条件都没有满足。做法与数字见 [§10.23][l10-23]。

r19（2026-10-20）：用户要三件事：用 `gh stack` 合入当前的 5 个 PR（#27–#31）；按现状设 V0.6 的发布里程碑；开发先停 1–2 天。① 合入：`gh stack link 27 28 29 30 31` 在 GitHub 上建了 stack #32；`gh stack merge 32 --yes --squash` 一次合入（GitHub 的原子 stack 合并：5 个 PR 全合或全不合，一个 PR 一个 squash 提交，不绕过合并要求）。`main` 从 `7d82c27` 到 `b33017a`：`7542062`（[#27][pr-27]）、`ce29cd8`（[#28][pr-28]）、`c3dc73e`（[#29][pr-29]）、`d2182d9`（[#30][pr-30]）、`b33017a`（[#31][pr-31]）。每个提交的树等于它的 PR 分支头（`739d2bb`、`bfc59b6`、`81171da`、`e2de4c4`、`464d8c9`）的树，所以 `main` 上的每个状态都是在 PR 上跑过 `CI` 的树。GitHub 只为一次推送的最后一个提交建运行，所以只有 `b33017a` 在 `main` 上有运行：`CI` 通过（[run][ci-run-b33017a]）；`TUI CI` 通过（[run][tui-ci-run-b33017a]）：8,490 通过 / 0 失败 / 22 跳过，每个 suite 通过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297。5 个远端分支被 GitHub 删了（仓库设了合并后删分支）；本地的 5 个分支我也删了（内容在 `main` 上，树相同）。② 里程碑：GitHub 里程碑 [v0.6.0][ms-v060]，没有日期，没有挂 PR 与 issue（和 v0.5.0 一样）；计划新增[发布里程碑 V0.6](#发布里程碑-v06)：范围按现状定（`main` 上 v0.5.0 之后的 10 个提交）、六项退出条件、带着发布的已知问题、不在 V0.6 的项。六项条件现在都没有满足：条件 1 只有 `main` 的头上的一次运行，要打 tag 的提交还没有。③ 暂停：用户让开发先停 1–2 天。暂停期间不开 PR，不改 `main`，不启动发布验证的 CI（条件 2、3 各约 30 分钟）。恢复之后先做条件 5（版本号在 `tui/` 里，条件 2、3 要在带版本号的树上做），再做条件 1–3，再请用户答条件 4 与 6。④ 没验证 / 留下的：`gh stack` 只在这个仓库、Windows 上用过一次；六项退出条件都没有做；V0.6 的版本号与 `CHANGELOG.md` 没有改（条件 5）；暂停之后的顺序是我的建议，没有问过用户。做法与数字见 [§10.22][l10-22]。

r18（2026-10-20）：用户从「接下来做哪些」里选了三项：`release_token`、`protoc_abs`、`nextest_ci`；没选 `protoc_ci_env`（在 `tui-ci.yml` 里设 `PROTOC`）、`d10_rest`（决策 10 的另两项）、`merge_27_28`。三项各做成一个 PR，一个叠在一个上（#28 → #29 → #30 → #31），生产代码没有行为变化，都没有合入。① [#29][pr-29]：`release.yml` 里两处 `arduino/setup-protoc` 加 `repo-token: ${{ secrets.GITHUB_TOKEN }}`（v0.5.0 的发布里，macOS 的构建因 GitHub API 的限额失败了两次）。只改一个工作流文件；`CI` 通过；在分支上手动跑了一次演练（测试与构建都做，不发布）：通过（`Install protoc` 在 Linux 与 macOS 上各 5 s 与 6 s；不发布）。这只说明工作流文件有效、令牌被接受，不证明限额问题已经解决（限额是随机的）。② [#30][pr-30]：`find_protoc()` 在 `PATH` 的各目录里找 `protoc`，返回绝对路径。同一条 `cargo` 命令重复运行时，`pi-tools-api` 不再每次重新构建（WSL2：第 3 遍 2.2 s → 0.3 s）。`CI` 与 `TUI CI` 通过（8,490 通过 / 0 失败 / 21 忽略）。**更正 r17 的一句话**：CI 的 `cargo test` job 里 `pi-tools-api` 编译 7 次（`pi-shell` 4 次、`pi-pager` 3 次）不是这个问题造成的：那个 job 每个 suite 一条 `cargo` 命令，每条命令按自己选的包统一 feature，所以 `pi-tools-api` 在用到它的 7 个 suite 里各编译一次。修了之后还是 7 / 4 / 3，job 用时也没变（22 m 08 s；#28 的三次运行是 22 m 04 s 到 22 m 43 s）。只有 `Gates + cargo check` job 里 `pi-tools-api` 由 2 次变成 1 次。所以好处在开发者的机器上，不在 CI 的用时上。③ [#31][pr-31]：`TUI CI` 与基线执行器改用 `cargo nextest`（清单 `[test] runner = "nextest"`；`--runner cargo` 回到 libtest）。一个测试一个进程，所以 r17 查的那一类竞争（共用环境变量、当前目录与 `static`）在 CI 里不会再出现，以后新写的测试也一样；这个树里的测试本来就是为 nextest 写的（不少测试的注释这么说）。三点要知道：nextest 把 `#[ignore]` 的与 `--skip` 去掉的测试合在一起算「跳过」（整个清单预计 22）；它不跑文档测试，这些 suite 也从来没跑过（执行器给的是明确的 `--lib`、`--test`、`--bin` 目标），所以 r17 说的「要另外跑 `cargo test --doc`」不适用；执行器超时后先发 SIGTERM、20 秒后才发 SIGKILL（nextest 把每个测试放在自己的进程组里，只发 SIGKILL 会留下挂住的测试）。CI 装钉住版本的 nextest（0.9.148，因为脚本读它的输出）；job 改名 `cargo nextest (Linux x86_64)`。`TUI CI` 通过（[`TUI CI`][tui-ci-run-1a49eef]、[`CI`][ci-run-1a49eef]）：8,490 通过 / 0 失败 / 22 跳过，整棵树第一次在 nextest 下跑，每个 suite 等于它的 `reference_passed`；测试自己的时间约 130 s，`cargo test` 约 65 s（多约 66 s，`pi-pager` 占约 48 s）；job 36 m 17 s，对同时开始的 `cargo test` 运行的 33 m 56 s（总用时在运行之间波动很大，不是可靠的度量）。④ 没验证 / 留下的：整棵树在 nextest 下只在 CI 里跑过（第一次就是 #31 的 CI，通过），本地没有跑过整套清单；nextest 只管进程内共享的状态，测试共用的文件、端口与 `/tmp` 它不管；本地的 `cargo test` 仍然会遇到 r17 的那类竞争；#30 的 `-I<前缀>/include` 在 CI 的日志里看不到。做法与数字见 [§10.21][l10-21]。

r17（2026-10-19）：用户选的四项里剩下的一项，`race_audit`（检查同类竞争）。做成了一个只改测试与工具的 PR：[#28][pr-28]（分支 `test/tui-env-race-audit`，叠在 [#27][pr-27] 的分支上，没有合入）。生产代码没有改；清单里 `reference_passed` 的和 8,488 → 8,490（多的 2 个是 `in_own_process` 的自检）。① 工具：`scripts/tui_envspy/`：环境变量探针（`envspy.sh`，一个 `LD_PRELOAD` 的共享库）与压力脚本（`stress.sh`：随机顺序、多个线程；可以只跑点名的测试，做配对与人口实验）；10 个新的 `pytest`。② 压力运行找到 9 个偶发失败的测试；配对与人口实验又找到 10 个，共 19 个。**3 个是测试自己的缺陷**：`pi-shell` 的 `subagents_config_models_with_env_var_enables`（两把互不排斥的锁保护同一个变量）与心跳用例（别的测试 `fork` 出的子进程还拿着锁的文件描述符）；`pi-pager` 的 `memory_trace` 样本用例（别的测试写进了进程级的追踪输出）。另有 5 个测试改了 `GROK_HOME` 等变量却不还原。这些在测试里修了。**16 个由进程级的 `static` 引起**：颜色等级与终端原生调色板、最小模式的标志、双向文字的闩、内嵌弹窗的标志、语音的门、主题。读它们的测试有几百个，都不拿锁，所以加锁修不了。改它们的 57 个测试现在各在自己的进程里跑（`pi-pager` 的 `test_util::in_own_process`：父进程用 `--exact` 再启动同一个测试二进制，子进程跑测试的主体，子进程失败则父进程失败；每个多约 30 ms）。③ 数字（WSL2，本地；32 线程、随机顺序）：`pi-pager` 的 lib，修前 6/32 遍有失败，修后 0/60（种子 1–60）与 0/32（种子 201–232）；`pi-shell` 2/20 → 0/20；4 线程（CI 的线程数，种子 101–120）0/20 → 0/20（中间一次 1/20）；固定顺序（32 线程，10 遍）0/10 → 0/10；其他 7 个 suite 各 0/10。配对（一个受害者与一个写者，100 遍）：族 5 语音的门 100/100 → 0/100，族 6 主题 96/100 与 92/100 → 0/100。整套清单 8,490 通过 / 0 失败 / 21 忽略（`88e04e9`，WSL2）。④ `cargo nextest`（一个测试一个进程）在隔离之前就让 16 遍运行都没有失败；要不要在 `TUI CI` 里改用它，由用户决定：`pi-pager` 的 lib 在 4 个线程下由 29.7 s 变成 79.5 s，nextest 不跑文档测试，基线执行器要改。（r18：改用了，见 [§10.21][l10-21]；「不跑文档测试」不构成代价，基线执行器从来没有跑过文档测试。）⑤ 另一个发现，没有改：`find_protoc()` 在 `PATH` 上找到 `protoc` 时返回不带路径的 `protoc`，构建脚本因此发出 `rerun-if-changed=protoc`，这个路径不存在，所以 `pi-tools-api` 与它上面的 crate 每次调用 cargo 都重新编译；CI 的 `cargo test` job 里 `pi-tools-api` 编译了 7 次。（r18 更正：这 7 次不是这个原因，见 [§10.21][l10-21]。）⑥ 没验证 / 留下的：约 140 个测试写外观缓存里的别的设置（`set_show_thinking_blocks` 等），没有隔离，没有已知的受害者；2 个失败原因没有查明（`blank_quote_line_survives_drag_copy_as_blank` 一次；`command_palette_search_bar_cursor_only_when_focused` 1/60，隔离之后没有再出现）；别的 7 个 suite 只各跑了 10 遍，清单外的 crate 没有查；只在 Linux；`in_own_process` 的限制见 §10.20。⑦ 验证的范围：长运行（整套清单、60 遍、人口、配对）在 `88e04e9` 上；最后的提交 `6438b59` 的 `tui/` 与它只差 `test_util.rs` 里两行。`6438b59` 上只跑了门禁（`check`）、`rustfmt` 和 `stress.sh` 的跳过段，没有跑测试：WSL 的虚拟磁盘把 D: 盘写满了（构建目录 29 GB，之后删了；虚拟磁盘文件还要压缩）。CI 在 `ea9609f`（比 `6438b59` 只多一个只改文档的提交）上第一次跑了测试，通过了：[`TUI CI`][tui-ci-run-ea9609f] 是 8,490 通过 / 0 失败 / 21 忽略，每个 suite 等于它的 `reference_passed`；[`CI`][ci-run-ea9609f] 也通过；最后的提交 `7b27775`（只改文档；`TUI CI` 的路径过滤看整个 PR，所以又跑了）上的第二次运行也通过（[`CI`][ci-run-7b27775]、[`TUI CI`][tui-ci-run-7b27775]，同样 8,490 / 0 / 21）。`pi-pager` 的 lib 在 CI 上 libtest 用时 15.55 s 与 15.79 s（两次运行），之前 5 次 CI 运行（没有隔离）是 8.21–14.83 s：平均多约 3.5 s，约每个隔离的测试 60 ms（本地约 30 ms）；样本少，只是估计。`clippy` 仍没有跑。做法与数字见 [§10.20][l10-20]。

r16（2026-10-19）：用户从「接下来做哪些」里选了四项：合入 #25 与 #26、删调用图报死的单元、一个小清理、检查同类竞争；没选决策 10 的另两项。① 合入：#25（`2b0baf3`）与 #26（`7d82c27`；#25 合入后基改成 `main`，把 `main` 合进它的分支，`git merge -s ours`）；`CI` 与 `TUI CI` 在两个 `main` 提交上都通过（`7d82c27` 上的运行：[`CI`][ci-run-7d82c27]、[`TUI CI`][tui-ci-run-7d82c27]；`TUI CI` 8,514 通过 / 0 失败 / 21 忽略）。② 删 `pi-shell` 里剩下的死代码：PR [#27][pr-27]（分支 `refactor/tui-session-store-leftovers`，基是 `main` 的 `7d82c27`，没有合入）。删了 15 个没人加载的文件（14 个 `goal_*.md` 与 `deep_research.rhai`）、调用图报死的 `session::worktree` 与 `session::helpers`、随后没有用户的 `util::config::worktree`、`[storage]` 设置（`StorageConfig`；旧的配置照常加载，这一段被报成不认识的键）、10 个 `pi-shell` 的源码没点名的依赖。`tui/`：28 个文件，+13 / −1,644 行；`.rs` 的行数 1,007,882 → 1,007,386，文件数 1,723 → 1,718；`pi-shell` 的非测试代码 31,653 → 31,191 行，测试 864 → 838，清单里 `reference_passed` 的和 8,514 → 8,488。门禁（`check`）：告警 0，依赖图 980 → 970，`leader` 243 与 `x.ai` 297（上限降到这两个数）。生产代码没有行为变化。整套测试 8,487 通过 / 1 个已知失败 / 21 忽略（WSL2，本地）；PTY 脚本与 r15 的结果相同（`resume_matrix.py --strict` 16/16）。PR 的 CI 在 `6bd7db5` 上通过（[`CI`][ci-run-6bd7db5]、[`TUI CI`][tui-ci-run-6bd7db5]）：`TUI CI` 8,488 通过 / 0 失败 / 21 忽略，依赖图 970，`leader` 243 与 `x.ai` 297，0 条告警。本地差的那 1 个是已知失败（tmux 探测），在 CI 的 runner 上它通过。③ 没做：同类竞争的检查（下一个只改测试的 PR）；调用图仍报死的 7 个单元（1,008 行）、没有用户的 crate `pi-workflow`（9 个文件，3,570 行）、`Config.storage_mode` 与 `--storage-mode`（在 `.rs` 里没有找到读它的代码）、约 88 个没人加载的候选文件（都并进 D.8）；决策 10 的另两项；Windows 与 macOS 上的构建。做法与数字见 [§10.19][l10-19]。

r15（2026-10-19）：决策 10 的存储层一项：做成了，PR [#26][pr-26]（分支 `refactor/delete-pi-shell-session-store`，叠在 [#25][pr-25] 上；r16 合入）。删了 `pi-shell` 的 `session::storage`（14 个文件）与 `session::persistence`（12 个文件，其中 11 个是测试），再删编译器随后报「没人用」的 41 处：会话信号、plan 模式快照、公告状态、目标与 workflow 的状态类型、回放用的线上标签、加载时的图片检查、`fork_filter_chat`，和只测它们的测试文件。pager 用的两个清洗标题的函数搬到新的 `session::title`，加了 8 个单元测试。`tui/`：53 个文件，+190 / −14,166 行（删 38 个文件，加 1 个；其中 +5 / −1 是一个测试的串行键，见下）；`.rs` 的行数 1,021,858 → 1,007,878（`5a39e09` 的数），文件数 1,760 → 1,723；`pi-shell` 的非测试代码 40,985 → 31,653 行。生产代码没有行为变化。删除之后，本地（12 个测试线程；CI 是 4 个线程，不出现）`pi-shell` 的整个 `--lib` 运行每次都失败一个测试：`consent` 测试与 `session::worktree` 的测试串行键不同，能同时运行，前者结束时删了后者正在用的临时目录。这个竞争在 `c74cc18` 里就有（同样的筛选下 15 次全失败），删除只改了各个测试同时运行的组合。已修，一个属性（`332ba75`），改后 20 次整个运行里它 0 次失败；原因与证据见 [§10.18][l10-18]。`pi-shell` 的测试 1,056 → 864，清单里 `reference_passed` 的和 8,706 → 8,514。门禁（`check`）：告警 0，依赖图 980，读盘 0 处；PTY 脚本与 r14 的结果相同（`resume_matrix.py --strict` 16/16）。#25 与 #26 的 CI 都通过（`TUI CI`：#25 是 8,706 通过 / 0 失败 / 21 忽略，#26 是 8,514 通过 / 0 失败 / 21 忽略；`CI` 的 Ruff 与 `pytest` 3.11、3.12、3.13 也过）。没做：决策 10 的另两项（`load_mcp_servers` / `mcp.rs`、`recent_dirs.rs`；用户的答复只说了存储层）；删可能不再用的依赖；删调用图现在报死的 `session::helpers` 与 `session::worktree`（rustc 不告警）；把 `leader` 与 `x.ai` 的上限降到 243 与 297；`[storage] cleanup_ttl_days` 这个设置（现在没有代码读它）。（后四项 r16 做了，见上。）做法与数字见 [§10.18][l10-18]。

r14（2026-10-19）：用户在 2026-10-18 答复了四个问题。① #23 与 #24 合入 `main`：#23 先 squash 合入（`7e0ca5a`），#24 改基到 `main` 之后再合（`0476ea4`）；`CI` 与 `TUI CI` 在两个提交上都通过（第二个提交上的运行：[`CI`][ci-run-0476ea4]、[`TUI CI`][tui-ci-run-0476ea4]；#24 合入前的分支头 `43c9a88`（把 `main` 合进它的分支之后）上，`TUI CI` 是 8,706 通过 / 0 失败 / 21 忽略）。② 翻页的代价（决策 18）：不做；1,000 个会话以内够用。③ 空会话（决策 17）：做，PR [#25][pr-25]（分支 `fix/write-session-at-first-message`，基 `main`，没有合入）。agent 在 `session/new` 时只在内存里建会话；第一条用户消息才把文件头、之前攒下的条目和这条消息一次写下去。没发过消息的会话没有文件，不在列表里，`session/load` 答 `resource_not_found`，所以 `zypi --continue` 取不到它。pager 的生产代码没有改，只改了 `python_agent_e2e.rs` 这一个测试文件。`resume_matrix.py --strict`：13 例 → 16 例，16/16 通过；把 agent 里两处 `repo.create` 各自改回「立刻写」（变异检查），对应的用例会失败。④ 删 `pi-shell` 里的旧存储层（决策 10）：做，下一个 PR（r15 做了，见上）。做法与数字见 [§10.17][l10-17]。

r13（2026-10-16）：用户答复了决策 2、4、5、7，都按倾向。ADR1 的 PR 2（[#23][pr-23]）与 PR 3（[#24][pr-24]，叠在 #23 上）做成了，两个都还没有合入 `main`（r14 合入了）。`--continue`、`--resume`、`--session-id` 现在在 agent 起来之后只问 agent（`session/list`、`session/load`），pager 里没有读会话文件的代码；`tui_baseline.py` 的门禁 `session_disk_reads` 守着这一点（`main` 上 18 处命中，PR 3 之后 0 处）。`resume_matrix.py --strict`：13/13 通过（r12 续 6 之后是 11 通过 / 2 缺口）。`pi-pager` 的测试 5,643 → 5,675（#23，CI 8,732 通过 / 0 失败）→ 5,649（#24，CI 8,706 通过 / 0 失败），清单里 `reference_passed` 的和 8,665 → 8,706。实测时发现翻页的代价增长得比会话数快（每页都重新列目录）：1,000 个会话以内在 2 s 以内，5,000 个会话时 `--session-id` 要 28–34 s，在 Windows 上超过 30 s 的上限；新增待决策 18。`pi-shell` 里的旧存储层还在：删它是决策 10（阶段 B），用户没有答复。做法与数字见 [§10.16][l10-16]。

r12 续 8（2026-10-13）：PR [#18][pr-18]（决策 6，`zypi export`）与 PR [#19][pr-19]（决策 15，状态行）合入了 `main`，进 V0.5。`tui/` 因此又变了，所以条件 1–3 在合入之后的树上重做（见[发布里程碑 V0.5](#发布里程碑-v05)）。之后 `v0.5.0` 打在 `be3f8a8`（`main` 的头；它与 `9058ab3` 之间只改了文档）上，`Release` 工作流发布了 PyPI 上的六个包与 GitHub Release（见条件 6）。下面「截至 r12」一段里的提交与数字，是合入之前的。

截至 r12：`origin/main` 在 `0efd400`（PR [#14][pr-14] 的合并提交；它前面是 PR [#17][pr-17] 的 `d57fd9b` 与 PR [#15][pr-15] 的 `59545c1`）。[`CI`][ci-run-0efd400] 在 `0efd400` 上通过；[`TUI CI`][tui-ci-run-d57fd9b] 在 `d57fd9b` 上通过（`0efd400` 与它只差 `release.yml` 的 7 行）。在 r11 的 PR [#13][pr-13] 的合并提交 `4fc028a` 上，[`CI`][ci-run-4fc028a] 与 [`TUI CI`][tui-ci-run-4fc028a] 也都通过；两次 `TUI CI` 的 10 个 suite 都合计 8,665 通过 / 0 失败 / 22 忽略（`PI_E2E_REQUIRED=1`），与 r11 在 WSL2 上的本地数相同；比 r10 的 8,683 少的 18 个在 `pi-pager`，是 C.2 随教程叠层与旧用户指南一起删掉的用例。r3–r11 都已合入 `main`，r12 的 PR #14、#15、#17 也是。V0.5 的发布里程碑见[发布里程碑 V0.5](#发布里程碑-v05)。

| 阶段 | 状态 | 已完成 | 还差 |
|---|---|---|---|
| 0 基线与门禁 | 基本完成 | Linux CI 与基线（0.1、0.2）；ACP 契约与 e2e（0.3）；deny-list（0.4）；调用图工具化（0.5，r11）；入口审计（0.6）；读盘调用点与 ADR1 落地设计（0.7）；能力矩阵与版本配对（0.8）；Linux 上的实测（0.9 的大部分） | 0.9 的 Landlock 层（要内核 ≥ 5.13），以及 macOS、Windows 一侧 |
| A 死代码先行 | 完成 | A.1–A.4 | — |
| 1 协议与 Python | 完成一半 | 1.P1 会话列表分页；1.P2 优雅退出；1.P4 显式忽略 `mcp_servers`（r20：装了 `pi-mcp-py` 就连接，决策 19，[#35][pr-35]、[#36][pr-36]）；1.P7 契约测试；1.R1 ADR1 落地（PR 1；PR 2、PR 3 在 [#23][pr-23]、[#24][pr-24]，r13 做成，r14 合入 `main`）；会话文件在第一条消息时才写（决策 17，r14，[#25][pr-25]，r16 合入 `main`；r20 的更正：Zed 重启后空白对话能再打开，[#34][pr-34]）；1.R3 的大部分；1.P3 的 (a) | 1.P3 的 (b)、(c)；1.P5；1.P6；1.R2；1.R4；1.R5；1.R3 的 MCP 子进程与 Windows |
| B 拆除 runtime | 删除完成，迁移没有完成 | B.1、B.5、B.6：`pi-sampler` 与 `pi-shell` 的 runtime 单元已删；r15：`pi-shell` 的旧存储层 `session::{storage, persistence}` 也删了（决策 10，[#26][pr-26]，r16 合入）；r16：`session::worktree`、`session::helpers` 等死代码与 10 个源码没点名的依赖也删了（[#27][pr-27]，r19 合入 `main`） | B.2、B.3、B.4：r3 换了做法，没有抽出叶子 crate |
| C 验收与文档 | 完成（r11，PR [#13][pr-13]） | C.1–C.4：`zypi` 没有到 LLM provider 的外连（实测，含一次真模型）；内嵌指南缩成 7 页，启动不再解压；`NOTICE` 重写，`THIRD-PARTY-NOTICES` 有工具对账；`grok agent` 清零，`leader`、`x.ai` 有上限 | 退出条件里的 `leader`、`x.ai` 清不到 0，改成了棘轮（§7 的 C） |
| D 依赖图瘦身（可选） | 没有开始 | — | D.1–D.9；等基线数据再定 |

### 发布里程碑 V0.5

**是什么。** 第一个不带 Rust agent runtime 的发布：`zypi` 只做终端界面和 ACP client，Python 是唯一的 agent。它把两条线一起发：Python 一侧的 Phase 6/7（0.5.0 的版本号与 `CHANGELOG.md` 的 `[0.5.0]` 段在 `main` 上；tag `v0.5.0` 在续 8 打了，见条件 6），和 TUI 一侧的 r3–r11（原来在 `CHANGELOG.md` 的 `[Unreleased]` 段，PR [#15][pr-15] 把它并进 `[0.5.0]`），再加 r12 续 6、续 7 的两个 PR（[#18][pr-18]：`zypi export`；[#19][pr-19]：状态行；续 8 合入）。相对 v0.4.0：到 `9058ab3`（#19 合入）31 个提交；`tui/` 里 1,518 个文件、+13,566 / −641,276 行（r12 开头在 `4fc028a` 上是 23 个提交、1,512 个文件、+12,275 / −641,224 行）；`pi-sampler` 的 31 个文件变成 0 个。GitHub 里程碑：[v0.5.0][ms-v050]，没有挂 PR 与 issue（条件在这里记，不记两处）。依据与没有验证的见 [§10.15][l10-15]。

**范围按现状定。** 阶段 0、A、C 完成，阶段 B 的删除完成，阶段 1 完成一半，阶段 D 没有开始（见上表）。V0.5 按这个状态发，不等阶段 1 的余项、ADR1 的 PR 2 与 PR 3、阶段 D。下面「已知问题」里的各项，有的 v0.4.0 就有，有的是剥离带来的，没有逐项分开量过；条件 4 本来是为了分开它们，用户决定不做（见条件 4）。

**退出条件。** 打 tag 之前，六项都要有结果（第 4 项的结果是用户决定不做）。

1. **CI 绿。** `CI` 与 `TUI CI` 在要打 tag 的提交上通过。现状：`CI` 在 `0efd400` 上通过，`TUI CI` 在 `d57fd9b` 上通过（`0efd400` 与它只差 `release.yml` 的 7 行）。**状态（r12 续 8）**：#18、#19 合入之后 `tui/` 变了，所以在新的 `main` 上再看一次。合入之前，在 `1fa18cf`（#18 与 #19 合并之后的树，与合入后的 `main` 的树相同）上：[`CI`](https://github.com/zy1233/pi-python/actions/runs/38353039688) 与 [`TUI CI`](https://github.com/zy1233/pi-python/actions/runs/38353039701) 通过。`Gates + cargo check`：警告 0，`leader` 245/245，`x.ai` 304/304，依赖图 980/980；`cargo test`：8,700 通过 / 0 失败 / 22 忽略（`pi-pager` 5,643 通过 / 13 忽略；比 8,665 多的 35 个是 #18 的 19 个与 #19 的 16 个）。合入之后：`main` 上，`ec7d455`（#18 的 squash 提交）：[`CI`](https://github.com/zy1233/pi-python/actions/runs/38352729921) 与 [`TUI CI`](https://github.com/zy1233/pi-python/actions/runs/38352729867) 通过；`9058ab3`（#19 的 squash 提交，`tui/` 的最后一次改动）：[`CI`](https://github.com/zy1233/pi-python/actions/runs/38354903178) 与 [`TUI CI`](https://github.com/zy1233/pi-python/actions/runs/38354903172) 通过，`cargo test` 与合入之前的运行一样：8,700 通过 / 0 失败 / 22 忽略；`Gates`：警告 0、依赖图 980。打 tag 的提交是 `be3f8a8`（`main` 的头；它与 `9058ab3` 之间只改了 `CHANGELOG.md` 与三个文档文件）：[`CI`](https://github.com/zy1233/pi-python/actions/runs/38357817115) 通过。`TUI CI` 没有为它运行（只改文档的提交不触发它），所以这一项按 `9058ab3` 上通过的 `TUI CI` 算：两个提交的 `tui/` 与 `packages/` 的树相同。**条件满足。**
2. **再量一次 release 体积。** 手动触发 `TUI CI` 的 `release_baseline`（约 30 分钟），把结果回填 [`docs/baselines/tui.md`](../baselines/tui.md)。条件：Linux 的 `zypi` 不大于 v0.4.0 的 470,461,776 B。上一次是 r5 的 422,034,120 B；#7 合入之后 `tui/` 又合入 5 个提交（#8、#9、#10、#12、#13）：115 个文件，+4,481 / −16,045 行。**结果（r12）**：[run 38221952524][run-baseline]，在 `4fc028a` 上：**420,767,096 B**（比 v0.4.0 小 10.6 %，比 r5 小 1,267,024 B），构建 1,195 s（v0.4.0：1,647 s），`--version` 中位数 23.4 ms。条件满足，数字已回填 [`docs/baselines/tui.md`](../baselines/tui.md)。之后 `tui/` 又合入了 #18、#19，所以在合入后的树上又量了一次。**结果（r12 续 8）**：[run 38353042677](https://github.com/zy1233/pi-python/actions/runs/38353042677)，在 `1fa18cf` 上：**420,667,200 B**（401.2 MiB，比 v0.4.0 小 10.6 %，比 r12 开头的 420,767,096 B 小 99,896 B），构建 1,194 s（19 m 54 s），`--version` 中位数 22.9 ms。条件满足，数字已回填 [`docs/baselines/tui.md`](../baselines/tui.md)。`tui/` 的树与打 tag 的提交相同（核对过：`git diff 1fa18cf be3f8a8 -- tui packages` 为空）。**发布件（r12 续 8）**：[`Release` 的运行 38358144687](https://github.com/zy1233/pi-python/actions/runs/38358144687) 在 `be3f8a8` 上构建的 `zypi-linux-x86_64` 是 **420,720,608 B**，仍不大于 470,461,776 B（小 10.6 %）。它比 `release_baseline` 量到的多 53,408 B：两次构建的 `tui/` 树相同，差在哪里没有查。`zypi-macos-arm64` 是 49,272,472 B（同一个 `release-dist` 配置；与 Linux 差这么多，原因没有查）。
3. **发布构建演练。** `release.yml` 只在 `v*` tag 上触发，没有手动入口；macOS arm64 的 job（2026-10-01 加的）从来没有在发布里跑过。`github-release` 等它，`publish-pypi` 不等：它失败时，PyPI 上的包已经发出，GitHub 上没有 Release。Linux CI 编不到 macOS 专属的代码：`tui/crates` 里 75 个文件带 `cfg(target_os = "macos")`，最后一次在 macOS 上构建是 r7，之后 `tui/` 改了 88 个文件，其中 8 个带这样的代码。v0.3.0 的发布工作流跑了三次才通过。做法：给 `release.yml` 加 `workflow_dispatch`，构建 job 照跑并上传产物，`publish-pypi` 与 `github-release` 只在 tag 上跑。这改的是发布工作流，所以先问了用户：用户同意了（r12），PR [#14][pr-14] 就是这一改，已合入（`0efd400`）。两个发布 job 的 `if` 不只看 ref，还要求事件是 `push`，因为手动运行可以把一个 tag 当作 ref。条件：Linux 与 macOS arm64 的 `release-dist` 构建、六个 Python 包的构建都成功。**结果（r12）**：演练 [run 38223353755][run-dry-run] 从 PR 分支 `ci/release-dry-run`（`04cea48`）手动触发；GitHub 接受了分支上的 `workflow_dispatch`，不用先合入 `main`。结果：成功，共 27 分钟。测试 97 s，Python 包的构建 29 s，Linux x86_64 的 `release-dist` 构建 16 m 26 s，macOS arm64 的 `release-dist` 构建 25 m 27 s；`publish-pypi` 与 `github-release` 跳过，没有发布任何东西。三个产物（`dist`、`tui-binary`、`tui-binary-macos-arm64`）保留到 2027-01-10。macOS 的 job 第一次在发布工作流里跑，它编译的 `cfg(target_os = "macos")` 代码 Linux 的 CI 编译不到，现在通过了。**没有验证**：没有人运行过那个 macOS 二进制，我也没有下载它（试过 `gh run download`，太慢，见 [§10.15][l10-15]）；`if` 为真的一侧（tag 的 `push`）要等真正的 tag 才跑得到。**再跑（r12 续 8）**：[run 38353038777](https://github.com/zy1233/pi-python/actions/runs/38353038777) 在 `1fa18cf` 上：成功，共 30 分钟 46 秒。测试 2 m 19 s，Python 包的构建 25 s，Linux x86_64 的 `release-dist` 构建 15 m 07 s，macOS arm64 的 `release-dist` 构建 28 m 12 s；`publish-pypi` 与 `github-release` 跳过，没有发布任何东西。三个产物（`dist`、`tui-binary`、`tui-binary-macos-arm64`）保留到 2027-01-11。macOS 的构建又通过了，所以 #18、#19 的 Rust 改动在 macOS 上能编译；仍然没有人在 macOS 上运行过这个二进制。**真正的发布（r12 续 8）**：tag 的 `push` 触发了 `publish-pypi` 与 `github-release`，两个 `if` 都按设计为真（见条件 6）。macOS 的二进制仍然没有人运行过。
4. **与 v0.4.0 对照。** 用 `scripts/tui_pty/` 的 `smoke.py`、`exit_matrix.py`、`resume_matrix.py`、`mode_probe.py`（都有 `--zypi PATH`）和同一个 Python agent，分别跑 v0.4.0 的 `zypi-linux-x86_64` 与新构建，比较结果。有差别的，要么在 `CHANGELOG.md` 里有一条，要么是缺陷。只在 Linux（WSL2）上做。**结果（r12）**：用户决定不做，V0.5 不等它。所以「已知问题」里的各项哪些 v0.4.0 就有，仍是推断，没有测量。
5. **发布说明。** 发布页的正文只取 `CHANGELOG.md` 里与 tag 同版本的一段，所以：把 `[Unreleased]` 并进 `[0.5.0]`，日期改成发布日（现在是 2026-10-02）；补上 `CHANGELOG.md` 里还没有的条目——Rust runtime 的删除与随之删掉的界面和命令（r3、r5）、`/model` 经 ACP `configOptions` 恢复（r3）、`-p` 在要求沙箱时拒绝运行（r5）、品牌清理（r6）；写「已知问题」（下）；用 `release.yml` 里同一段 `awk` 在本地预览。`docs/TUI-AND-CODE-AGENT.md` 只写了 Linux x86_64 有预编译二进制；`zypi-macos-arm64` 随版本发的话要补上。**状态（r12）**：PR [#15][pr-15] 把 `[Unreleased]` 并进了 `[0.5.0]`，补了 20 条（Added 2、Changed 2、Removed 2、Fixed 6、已知问题 8），日期写 2026-10-12（打 tag 那天不是这天，就改）。用 `release.yml` 里那段 `awk` 预览过（Git for Windows 的 `awk`，输入是 LF 文本）：正文 45,076 B，五个分组。演练成功之后，PR [#15][pr-15] 又加了 `zypi-macos-arm64` 的条目（Added），并改了 `README.md` 与 `docs/TUI-AND-CODE-AGENT.md`：三处都写明这个二进制由 CI 构建，没有人运行过。PR [#17][pr-17] 再加 1 条 Changed（版本号，见决策 16），并去掉 1 条已知问题；那之后正文是 45,855 B。PR [#15][pr-15]（`59545c1`）与 PR [#17][pr-17]（`d57fd9b`）都已合入。**状态（r12 续 8）**：续 8 的文档提交把 #18、#19 的两条（Fixed 1、Changed 1）并进 `[0.5.0]`，删掉 `zypi export` 的已知问题，把 `[ui.status_line]` 的那条改窄（agent 不告诉 `zypi` 上下文与费用，这两项仍然空），日期改成 2026-10-13（打 tag 那天）。现在 `[0.5.0]` 有 Added 16、Fixed 32、Changed 14、Removed 2、已知问题 6 条；用同一段 `awk` 预览（输入是 LF 文本）：正文 49,640 B，五个分组。预览之后又查了 `[0.5.0]` 里有没有被 #18、#19 改成过时的条目（查过的词：`export`、`status_line`、`status row`、`session_status`、`resume_matrix`、`GAP`、`probe`、`tui_pty`），找到一条并改了：Added 里状态行探针那条还写着 Python agent 下什么也不画，现在写成「写的时候是这样，现在画得出来，探针查的是这个」（没改之前正文是 49,429 B）。**发布（r12 续 8）**：这个改动在 PR [#21][pr-21] 里合入（`be3f8a8`，也是打 tag 的提交）。GitHub Release 的正文，与那个提交里 `CHANGELOG.md` 的 `[0.5.0]` 段按 `release.yml` 的做法（`awk`，去掉结尾的换行，再加一个）得到的文字逐字节相同：49,639 B（预览时数的 49,640 B 多一个结尾的换行）。
6. **打 tag。** 先前约定归用户；用户在 r12 续 8 让我来打，并说不用再重复本地测试，所以 `pre-push-ruff` 要求的本地 pytest（Windows 与 WSL）这次没有跑，只跑了 ruff；pytest 由 `CI`（Linux，Python 3.11–3.13）与 `Release` 的 `test` job 跑。`git tag -a v0.5.0`（v0.3.0、v0.4.0 的 tag 是带注释的）、`git push origin v0.5.0`；再核对 Release 的运行：PyPI 上六个包（核心、harness、cli 是 0.5.0，三个扩展包是 0.2.0），GitHub Release 上的 wheel、sdist 与 `zypi-*` 二进制。**结果（r12 续 8，2026-10-13）**：tag `v0.5.0`（带注释，tag 对象 `d226977`）指向 `be3f8a8`，14:41（+08:00）推送。打 tag 之前，[`CI`](https://github.com/zy1233/pi-python/actions/runs/38357817115) 在 `be3f8a8` 上通过（含 `Ruff`）；没有跑本地的 pytest 与 cargo 测试（用户说不用）。[`Release` 的运行 38358144687](https://github.com/zy1233/pi-python/actions/runs/38358144687) 最后成功，但跑了三次。第 1 次：测试 2 m 19 s、Python 包的构建 23 s、`publish-pypi` 22 s、Linux x86_64 构建 20 m 49 s 成功；macOS arm64 构建失败在「Install protoc」一步（18 s），没有开始编译，`github-release` 因此跳过。原因：`release.yml` 里的 `arduino/setup-protoc@v3`（Linux 与 macOS 各一处）没有 `repo-token`，它没有认证地调 GitHub 的 API 查 protoc 的版本，macOS 运行器共用出口地址，额度用完了（日志：`API rate limit exceeded`）；`tui-ci.yml` 里同一步的三处都带 `repo-token: ${{ secrets.GITHUB_TOKEN }}`。运行没有结束时，GitHub 不接受只重跑一个 job（403）；运行结束后用 `gh run rerun 38358144687 --failed` 重跑，第 2 次的 macOS 构建在同一步又失败（22 s）；再重跑一次，第 3 次通过（macOS 构建 25 m 57 s），`github-release` 48 s。重跑用的是 tag 提交里的工作流文件，所以没有改 `release.yml`（改了就要移动 tag）。**PyPI**：六个包都在，每个有 wheel 与 sdist，文件大小与 GitHub Release 上的一致；刚发布时包的总览 JSON 还显示旧版本（缓存），逐版本的 JSON 与 `/simple/` 已经是新的，之后总览也更新了。装进干净的 venv（Windows，Python 3.12）：六个包都装得上，`uv pip check` 通过，`python -m pi_agent_cli` 对 `initialize` 回答的 `agentInfo.version` 是 `0.5.0`。**GitHub Release**：[`v0.5.0`](https://github.com/zy1233/pi-python/releases/tag/v0.5.0)，标题 `pi-python v0.5.0`，不是草稿也不是预发布，2026-10-13 07:34 UTC 发布，也是 `releases/latest`（`README.md` 与 `docs/TUI-AND-CODE-AGENT.md` 里的下载命令用它）；14 个文件：6 个 wheel、6 个 sdist、`zypi-linux-x86_64`、`zypi-macos-arm64`。正文见条件 5。**发布的 Linux 二进制**下载后跑过（WSL2；从 Windows 下载 GitHub 的发布文件时连接卡住）：大小对得上，`zypi --version` 打印 `zypi 0.5.0 (be3f8a8e5e20)`；配 PyPI 上的 `pi-agent-cli-lc` 0.5.0 用 `scripts/tui_pty/` 的脚本：`smoke.py` 23/23，`smoke.py --sandbox workspace` 26/26，`status_line_probe.py` 4/4，`resume_matrix.py` 11 通过 / 2 GAP / 0 失败（两个 GAP 是「已知问题」第一条里的 `--resume <标题>` 与 `--session-id <已用的 id>`），`exit_matrix.py` 12/12。**条件满足。** 要不要给 `release.yml` 的两处 `setup-protoc` 加 `repo-token`，见「下一步」；用户在 2026-10-20 选了要加，做在 PR [#29][pr-29] 里（还没合入）。

**带着发布的已知问题**，除非用户在打 tag 之前决定。用户在 r12 续 5 定了决策 6 与 15，8 与 14 仍待定。6 与 15 各做成了一个 PR（r12 续 6、续 7；15 在复核后从 (a) 改成了 (b)）。用户在续 8 让它们合入并进 V0.5：PR [#18][pr-18]、[#19][pr-19] 已合入 `main`，所以 `zypi export` 的一条已经删掉，`[ui.status_line]` 的一条改窄了（`CHANGELOG.md` 的已知问题同步改了）。决策 16 已做并已合入（PR [#17][pr-17]，`d57fd9b`），所以「已知问题」里没有版本号一条。

- `--resume <标题>` 找不到 Python 会话（`no session id or title matched`，退出码 1）。`--session-id <已用的 id>` 被 agent 拒绝，TUI 却显示 `Session creation failed: Invalid params: …` 并留在界面里，不退出（ADR1 的 PR 2）。`--resume <未知 id>` 进了 TUI 才报错（决策 5）。`--continue` 取最近创建的会话，不是最近活动的（决策 4）。（r13：这一条在 PR [#23][pr-23]、[#24][pr-24] 里解决；r14：两个都已合入 `main`，写在 `CHANGELOG.md` 的 `[Unreleased]`；已发布的 v0.5.0 的说明不变。）
- 交互模式下 `--system-prompt-override` 与 `--rules` 无效，`--allow` 与 `--deny` 到不了 agent，`zypi --help` 还列着没有逐个核对过的 Grok 时代旗标（决策 8，仍待定）。
- 设置页里权限模式以外的项对 Python agent 可能没有效果；`remember_tool_approvals` 与 `default_selected_permission` 没有测过（决策 14，仍待定）。
- `[ui.status_line]` 在 Python agent 下没有 `context` 与 `cost` 两项，脚本也拿不到 `context_window.*`、`cost.total_cost_usd`、`workspace.branch`、`workspace.repo_root`：agent 不告诉 `zypi` 上下文窗口、已用的 token 与费用，`zypi` 也不造这些数（决策 15：选了 (b)，已合入，见 [§10.15][l10-15] 续 7）。行的其余部分画得出来；内嵌指南照实写了。
- 内嵌的七页指南没有入口可以打开（D.6）。
- Python 一侧：Phase 6/7 审计的 F7-02 至 F7-06（低危：扩展 API 的几处小缺陷）与 F7-07 至 F7-09（规格的文档备注）没有修复批次（[`AUDIT-PHASE6-PHASE7-2026-09-30.md`](../AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md) 第八节）。
- 上面「还没有验证的」里的各项：平台（只在 Linux 上验证过）、Landlock 层、延迟、跨目录的 `--resume <id>`、macOS 的发行二进制（Linux 的下载后跑过，见「还没有验证的」）。

**不在 V0.5。** ADR1 的 PR 2 与 PR 3，和决策 2、4、5；决策 7 的 (b)、(c)；决策 9–11、13；阶段 D 全部；B.2–B.4；1.P5、1.R2、1.R5；0.9 的余项。它们归哪个里程碑，等用户排 V0.6 时再定。（r13：ADR1 的 PR 2 与 PR 3，和决策 2、4、5，已经做成 PR（[#23][pr-23]、[#24][pr-24]）；r14：已合入 `main`，写在 `CHANGELOG.md` 的 `[Unreleased]`，归哪个里程碑仍由用户定。决策 17 是 PR [#25][pr-25]，r16 合入 `main`。）

### 发布里程碑 V0.6

**是什么。** 第二个不带 Rust agent runtime 的发布。它发 `main` 上 v0.5.0 之后的东西：ADR1 的 PR 2 与 PR 3，和决策 2、4、5（会话经 ACP 找，`zypi` 不读会话文件；[#23][pr-23]、[#24][pr-24]）；决策 17（没人发过消息的会话没有文件；[#25][pr-25]）和它的更正（Zed 重启后，这样的对话能再打开；[#34][pr-34]）；决策 10 的存储层一项与它的收尾（[#26][pr-26]、[#27][pr-27]）；测试与 CI 的加固（[#28][pr-28]–[#31][pr-31]）；决策 19：给扩展的挂钩（[#35][pr-35]）与新包 `pi-mcp-py` 0.1.0（[#36][pr-36]）；版本号与发布说明（决策 20；[#39][pr-39]）。相对 v0.5.0（r21，`5f73e07`）：16 个提交（#22–#31、#33–#36、#38、#39；#22、#33、#38 只改文档），202 个文件，+14,178 / −18,398 行；`tui/` 117 个文件，+2,314 / −18,027 行（r20 是 115 个文件，+2,310 / −18,023：多的是 #39 的两个 `Cargo.toml` 与 `Cargo.lock` 的两行）；`packages/` 45 个文件，+6,757 / −144 行（`pi-mcp` 18 个，都是新文件；`pi-agent-cli` 14 个；`pi-agent-harness` 9 个；`pi-dynamic-workflows` 2 个，一个测试文件与 `pyproject.toml`；`pi-web-access` 与 `pi-goal-x` 各 1 个，只有 `pyproject.toml`）；`pi_agent_core/` 7 个文件，+334 / −7 行（4 个源码文件，都在扩展 API 里；3 个测试文件）。依赖图 980 → 970。GitHub 里程碑：[v0.6.0][ms-v060]，没有挂 PR 与 issue（条件在这里记，不记两处）。依据见 [§10.22][l10-22]、[§10.23][l10-23]。**状态（r22）：已发布。** tag `v0.6.0` 在 `5f73e07`（2026-10-25）；六项退出条件都有结果（见下）；发布后的核对见 [§10.25][l10-25]。

**用户能看到的变化。** 全文在 `CHANGELOG.md` 的 `[0.6.0]`（Added 3、Changed 7、Removed 3、Fixed 5，共 18 条；另有 9 条已知问题）。六件：① `zypi --continue`、`--resume`、`--session-id` 向 agent 问会话（`session/list`、`session/load`），不再读会话文件；`--continue` 取最近有活动的会话，不是最近创建的；不存在的 id 与用过的 id 在界面启动之前就报错，退出码 1。② 没人发过消息的会话没有文件；Zed 重启后，这样的对话能再打开（`session/load`、`session/resume`）。③ 恢复的会话不再沿用保存的沙箱 profile。④ 新的可选包 `pi-mcp-py`：连接编辑器传来的 MCP server，工具名是 `mcp__<server>__<tool>`，有 `/mcp` 命令（发布之后 `pip install pi-mcp-py`）。⑤ MCP 的提示改了：语法对了，只写没人接手的 server，并告诉用户装 `pi-mcp-py`。⑥ 给扩展作者：`ExtensionAPI` 多 5 个成员，`HarnessBridge` 多 4 个成员（自己写 `HarnessBridge` 替身的人要补上）；客户端再次打开同一个会话时，旧的 harness 现在会被关掉（以前被丢掉，扩展的清理不跑）。其余的变化不改行为：删掉 `pi-shell` 的旧存储层与死代码（约 15,800 行；配置里的 `[storage]` 读到时被报成不认识的键）；测试与 CI（`cargo nextest`、测试之间的竞争、`protoc` 的绝对路径、`release.yml` 的 `repo-token`）；版本号（核心、harness、cli 是 0.6.0；三个已发布的扩展包是 0.3.0；`pi-mcp-py` 是 0.1.0；四个扩展包要求核心 `>=0.6.0`）。

**范围按现状定。** 与 V0.5 一样：发 `main` 上已有的东西，加六项退出条件。不等「不在 V0.6」里的任何一项；要把其中一项拉进来，由用户在暂停之后说。

**暂停（已结束）。** 用户在 2026-10-20 让开发先停 1–2 天；2026-10-23 用户回来，先做了 MCP（r20）。里程碑仍没有日期。恢复之后的顺序（我的建议）：先做条件 5，因为版本号在 `tui/` 里，条件 2、3 要在带版本号的树上做（V0.5 也是这样）；再做条件 1–3；再请用户答条件 4 与 6。r21 照这个顺序做了：条件 5（[#39][pr-39]）、合入、条件 1–3；用户对条件 4 答「做」，条件 4 也做了（[§10.24][l10-24]）。条件 6 在 r22 做了（[§10.25][l10-25]）：v0.6.0 已发布。

**退出条件。** 打 tag 之前，六项都要有结果（第 4 项的结果可以是用户决定不做，像 V0.5 那样）。

1. **CI 绿。** `CI` 与 `TUI CI` 在要打 tag 的提交上通过。**结果（r21）**：满足。在 `5f73e07`（#39 的合入提交，带版本号与 `CHANGELOG.md` 的 `[0.6.0]`）上，[`CI`][ci-run-5f73e07] 通过（`Ruff` 9 s；`pytest` 在 Python 3.11、3.12、3.13 上都是 2,908 通过 / 39 跳过 / 31 取消选择），[`TUI CI`][tui-ci-run-5f73e07] 通过（手动触发，勾选 `release_baseline`）：`cargo nextest` 8,490 通过 / 0 失败 / 22 跳过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297，`grok agent` 0。推送触发的那次 `TUI CI` 被这次手动触发取消了（同一个并发组，`cancel-in-progress`）；手动的这次在同一个提交上跑了 `check`、`test` 与 `release-baseline` 三个 job。**r22：tag 打在 `5f73e07` 上，所以这个结果就是 tag 的提交上的结果。** **如果 tag 打在之后的提交上**（比如只改文档与脚本的提交；V0.5 的 tag 就是这样），`CI` 会在那个提交上自己跑；`TUI CI` 按路径过滤，不一定跑，那就要核对 `tui/` 与 `packages/` 的树没变（`git diff 5f73e07 <提交> -- tui packages` 为空），再用这次的结果。更早（r20）：`main` 的头 `e0f2a36` 上，[`CI`][ci-run-e0f2a36] 通过（`pytest` 在 Python 3.11、3.12、3.13 上都是 2,908 通过 / 39 跳过 / 31 取消选择），[`TUI CI`][tui-ci-run-e0f2a36] 也通过（8,490 通过 / 0 失败 / 22 跳过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297）。r19 时 `b33017a` 上两个都通过（[`CI`][ci-run-b33017a]、[`TUI CI`][tui-ci-run-b33017a]：8,490 通过 / 0 失败 / 22 跳过；`Gates`：警告 0，依赖图 970/970，`leader` 243/243，`x.ai` 297/297）。r19 与 r20 各合入一批提交；每一批只有最后一个提交在 `main` 上有跑完的运行（GitHub 只为一次推送的最后一个提交建运行；r20 的 `ca2ab85` 有一次 `CI` 运行，被随后的推送取消）。其余的提交，树都等于一个 PR 分支头的树（r20 的三个：再加 #33 的两个文档文件），那些分支头在 PR 上跑过 `CI`（#27、#28、#30、#31、#34、#35 还跑过 `TUI CI`；#29 只改 `release.yml`、#36 没改 `tui/`，都不触发它）。
2. **再量一次 release 体积。** 手动触发 `TUI CI` 的 `release_baseline`（约 30 分钟），结果回填 [`docs/baselines/tui.md`](../baselines/tui.md)。条件：Linux 的 `zypi` 不大于 v0.5.0 发布件的 420,720,608 B（V0.5 用的上限是 v0.4.0 的 470,461,776 B）。**结果（r21）：满足。**[`TUI CI`][tui-ci-run-5f73e07] 的 `Release build baseline`（手动触发，`5f73e07`）：Linux 的 `zypi` **406,911,320 B**（388.1 MiB），比 v0.5.0 发布件的 420,720,608 B 小 13,809,288 B（3.3 %）；比 V0.5 量到的 420,667,200 B 小 13,755,880 B；比拆除前的 470,461,776 B 小 13.5 %。构建 895 s（v0.4.0：1,647 s；V0.5：1,194 s），`--version` 中位数 22.4 ms。r19 的推断（`tui/` 净减约 15,700 行，所以不会变大）量过了，成立；变小的份额没有逐项量。数字已回填 [`docs/baselines/tui.md`](../baselines/tui.md)。
3. **发布构建演练。** 手动触发 `release.yml`（`workflow_dispatch`，约 30 分钟）：测试、七个 Python 包（r20：`pi-mcp-py` 加进来了）、Linux x86_64 与 macOS arm64 的 `release-dist` 构建都做，`publish-pypi` 与 `github-release` 跳过。条件：全部成功。**结果（r21）：满足。**`Release` 的演练 [run][run-dry-run-5f73e07]（`workflow_dispatch`，`5f73e07`）：全部成功。`Pre-release tests` 217 s（2,908 通过 / 39 跳过 / 31 取消选择）；`Build Python packages` 29 s（七个包，含 `Build pi-mcp`）；`Build TUI binary (Linux x86_64)` 1,056 s；`Build TUI binary (macOS arm64)` 1,791 s（29 m 51 s，第一次就成功）；`Publish to PyPI` 与 `GitHub Release` 跳过，没有发布任何东西。三个产物（`dist`、`tui-binary`、`tui-binary-macos-arm64`）保留到 2027-01-23。macOS 一次成功不证明 `setup-protoc` 的限额问题已经解决（见下）。**r22：真的 tag 的 `Release` 运行（[run][run-release-v060]）里，macOS 第一次就过了 `Install protoc`：这是 `repo-token` 在真 tag 上的第一个结果；一次成功仍不证明限额问题不会再出现**。更早：在 #29 的分支（`81171da`）上做过一次，通过（[run 39360804691][run-dry-run-81171da]，见 [§10.21][l10-21]）：它说明 `repo-token` 被接受，不说明要打 tag 的提交能构建。r20 在 #36 的分支头（`eda4aac`）上又做过一次不完整的（[run 40162795623][run-dry-run-eda4aac]，见 [§10.23][l10-23]）：`Pre-release tests` 通过（这个 job 现在装 `pi-mcp-py` 并跑它的测试），`Build Python packages` 通过（含新的一步 `Build pi-mcp`），`Publish to PyPI` 跳过；`dist` 产物 14 个文件（7 个 wheel 与 7 个 sdist），`twine check --strict` 对 14 个都通过。两个 TUI 构建 job 我取消了（这个改动不碰它们），所以它不证明 TUI 能构建。`setup-protoc` 的限额问题是随机的，所以只有真正的 tag 才证明它已经解决（v0.5.0 的发布里，macOS 的构建在这一步失败了两次）。
4. **与 v0.5.0 对照。** 用 `scripts/tui_pty/` 的 `smoke.py`、`exit_matrix.py`、`resume_matrix.py`、`mode_probe.py`、`status_line_probe.py`（都有 `--zypi PATH`）和同一个 Python agent，分别跑 v0.5.0 的 `zypi-linux-x86_64` 与新构建，比较结果。有差别的，要么在 `CHANGELOG.md` 里有一条，要么是缺陷。只在 Linux（WSL2）上做。预计的差别：`resume_matrix.py` 由 13 个用例变成 16 个；v0.5.0 的两个 GAP（`--resume <标题>`、用过的 `--session-id`）现在通过；用例 8（未知 id）的预期退出码变成 1。做不做，由用户定（V0.5 时用户决定不做）。**结果（r21）：用户答「做」，做完了。每个差别都有解释：三个在 `CHANGELOG.md` 里，一个是探针的竞争**。WSL2 里，v0.5.0 的 `zypi-linux-x86_64`（420,720,608 B）与演练产出的新 `zypi`（406,911,320 B，`zypi 0.6.0 (5f73e07964f1)`），同一个 Python agent（`main`，0.6.0），五个脚本。`smoke.py` 两边都是 23/23；`exit_matrix.py` 两边都是 12 个用例，都没留下进程；`status_line_probe.py` 两边都是 4/4。`resume_matrix.py --strict`：v0.5.0 是 13 通过 / 3 失败，新的是 16 通过 / 0 失败；三个差别就是上面预计的那三个（`--resume <标题>`、`--resume <未知 id>`、`--session-id <用过的 id>`），`CHANGELOG.md` 的 `### Changed` 里有一条说它们。`mode_probe.py` 第一次有一行不同（保存的 `permission_mode = always-approve`：v0.5.0 没有标签，新的有），查下来是探针的竞争，不是构建的差别：同一个 v0.5.0 二进制 17 次里 3 次没有标签；探针在欢迎页出现之后加了 1 s 的等待（PR [#40][pr-40]，r22 合入 `75f83ae`），v0.5.0 37 次、新的 13 次都有标签，两张完整的表逐行相同。只在 Linux（WSL2）做。方法、数字与没有查的见 [§10.24][l10-24]。
5. **发布说明与版本号。** 发布页的正文只取 `CHANGELOG.md` 里与 tag 同版本的一段，所以：把 `[Unreleased]` 改成 `[0.6.0] - <发布日>`，再留一个空的 `[Unreleased]`；写「已知问题」（见下；v0.5.0 的第一条要删，会话的那一条已经解决）；用 `release.yml` 里同一段 `awk` 在本地预览。版本号：`pi-agent-core-lc`、`pi-agent-harness-lc`、`pi-agent-cli-lc` 一起从 0.5.0 抬到 0.6.0。三个包互相用 `==` 钉版本（harness 钉核心，cli 钉核心与 harness，核心的 extras 钉 harness 与 cli），所以只抬 cli 与 harness 的话，核心 0.5.0 的 extras 仍装 0.5.0 的 cli 与 harness。`pi-pager-bin` 与 `pi-version` 的 `Cargo.toml`，和 `tui/Cargo.lock` 里这两个包，要等于 `pi-agent-cli-lc` 的版本（`scripts/tests/test_release_versions.py` 检查）。**四个扩展包的核心下限也要抬**（r20 更正 r19 的「三个扩展包不动」）：`test_extension_packaging.py` 要求扩展包写的核心下限等于工作区里核心的版本，所以核心升到 0.6.0 时，`pi-web-access-py`、`pi-goal-x-py`、`pi-dynamic-workflows-py`（都是 0.2.0）与 `pi-mcp-py`（0.1.0）的下限都要从 `>=0.5.0` 变成 `>=0.6.0`（本地验证过：4 个失败）。三个已发布的扩展包的版本号一起抬，从 0.2.0 到 0.3.0；`pi-mcp-py` 保持 0.1.0（决策 20，已定：用户在 2026-10-25 答「按你的倾向来」；照 V0.5 的 `7e7d4d4`，它把版本从 0.1.0 抬到 0.2.0；`publish-pypi` 用 `skip-existing: true`，不抬版本的话，PyPI 上的 0.2.0 保留旧的元数据，仓库里同一个版本号却写着 `>=0.6.0`）。**结果（r21）：做了，[#39][pr-39]，合入 `5f73e07`**（11 个文件，+40 / −22 行，没有代码）。照上面做：三个核心包 0.6.0；三个已发布的扩展包 0.3.0，`pi-mcp-py` 0.1.0，下限都是 `>=0.6.0`；`pi-pager-bin`、`pi-version`、`tui/Cargo.lock` 的两项 0.6.0；`CHANGELOG.md`：`[0.6.0] - 2026-10-25`（日期是占位）、空的 `[Unreleased]`、开头一段、一条「新版本号」、9 条已知问题；`awk` 预览 33,725 B。**更正** 上面的一句话：`pi-dynamic-workflows-py` 除了核心下限，还有一个 harness 下限（`pi-agent-harness-lc>=0.5.0`），也抬到 `>=0.6.0`；`test_extension_packaging.py` 对每个扩展包只报一个失败（循环里的 `assert` 停在第一个不对的依赖上），所以上面「4 个失败」没有露出它。验证：修之前 7 个失败（四个扩展包的下限，`pi-pager-bin`、`pi-version`、`Cargo.lock`），修之后 16 个通过；整套 `pytest` 在 Windows 与 WSL 通过；七个包构建、`twine check --strict` 通过；干净的 venv 里 `uv pip check` 通过；`cargo metadata --locked --offline` 退出码 0，`Cargo.lock` 没有变。
6. **打 tag。** `git tag -a v0.6.0`（带注释，与 v0.3.0–v0.5.0 一样）与 `git push origin v0.6.0`；再核对 `Release` 的运行：PyPI 上核心、harness、cli 三个包是 0.6.0，`pi-mcp-py` 是 0.1.0（PyPI 上的新项目，第一次上传），三个已发布的扩展包是 0.3.0（决策 20），GitHub Release 上的 wheel、sdist 与 `zypi-*` 二进制；在干净的 venv 里装这些包，`uv pip check` 通过（V0.5 做过）；下载 Linux 的二进制，跑 `zypi --version` 与 `scripts/tui_pty/` 的脚本（V0.5 做过）。**风险（r20）**：新项目的第一次上传，需要一个能建项目的 `PYPI_API_TOKEN`（账号级令牌，或这个名字的 pending publisher）；从这里查不了。`pi-mcp-py` 这个名字现在是空的（2026-10-25，PyPI 的接口回 404）。`publish-pypi` 用 `skip-existing: true`，令牌修好后重跑是安全的。（**r22：令牌能建项目，`pi-mcp-py` 0.1.0 上传成功。**）谁来打 tag，由用户定（V0.5 是用户让我打）。**结果（r22）**：做了，满足。用户定：我来打，打在 `5f73e07`，#40 与 #41 在 tag 之后合入。tag `v0.6.0`（带注释，tag 对象 `6b92a0d`）2026-10-25 17:20（+08:00）推送；`CHANGELOG.md` 的日期就是这一天。[`Release` 的运行][run-release-v060]一次就全部成功。PyPI：七个包是预期的版本；`pi-mcp-py` 0.1.0 是新项目，第一次上传成功（令牌能建项目）。[GitHub Release][rel-v060]：16 个资产；正文等于 `CHANGELOG.md` 的 `[0.6.0]`；14 个 Python 文件的 SHA-256 与 PyPI 上的相同。干净的 venv 里 `uv pip check` 通过，四个扩展包能加载；发布的 `zypi-linux-x86_64`（406,911,320 B，与 `Release` 运行的构建产物逐字节相同）打印 `zypi 0.6.0 (5f73e07964f1)`，五个 PTY 脚本通过（对着 PyPI 的 agent）。没发现缺陷。数字、没验证的与留下的见 [§10.25][l10-25]。

**带着发布的已知问题**，除非用户在打 tag 之前决定。比 v0.5.0 少一条（会话的那一条已经解决，[#23][pr-23]、[#24][pr-24]、[#25][pr-25]），多四条（r21：已写进 `CHANGELOG.md` 的 `[0.6.0]`，共 9 条；下面的十条里，前两条在那里合成了一条）。r22：这 9 条已随 v0.6.0 发布。

- 交互模式下 `--system-prompt-override` 与 `--rules` 无效，`--allow` 与 `--deny` 到不了 agent，`zypi --help` 还列着没有逐个核对过的 Grok 时代旗标（决策 8，待定）。
- 设置页里权限模式以外的项对 Python agent 可能没有效果；`remember_tool_approvals` 与 `default_selected_permission` 没有测过（决策 14，待定）。
- `[ui.status_line]` 在 Python agent 下没有 `context` 与 `cost` 两项，脚本也拿不到 `context_window.*`、`cost.total_cost_usd`、`workspace.branch`、`workspace.repo_root`（决策 15 的 (b)；agent 不告诉 `zypi` 上下文窗口、已用的 token 与费用）。
- 内嵌的七页指南没有入口可以打开（D.6）。
- Python 一侧：Phase 6/7 审计的 F7-02 至 F7-09 没有修复批次（[`AUDIT-PHASE6-PHASE7-2026-09-30.md`](../AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md) 第八节）。
- 平台：只在 Linux（WSL2）上验证过；macOS 的二进制没有人运行过；Windows 没有二进制；Landlock 层没有测过（WSL 的内核 5.10 没有 Landlock）。
- **新：翻页的代价**（决策 18；用户在 2026-10-18 答复：不做）。agent 给每一页都重新列一次会话目录，所以读完所有页的代价随会话数快速增长。`--session-id` 在 5,000 个会话时：Linux 28 s、Windows 34 s（Windows 上超过 30 s 的限额）；1,000 个会话时 1.3 s、1.8 s。`--continue` 在 5,000 个会话时 10 s、12 s。1,000 个会话以内够用。
- **新：同一个 id 的并发没有关死。** 一个 agent 进程里，客户端选定的 id 在会话打开期间保留，第二次 `session/new` 用同一个 id 会被拒。两个 agent 进程共用一个 home 时，同一个 id 仍可能各建一个会话：先存盘的赢，另一个的第一条消息被拒（`a session with this id already exists`）。
- **新：`pi-mcp-py` 是 Alpha**（r20，[#36][pr-36]）。没做：OAuth；MCP 的 resources、prompts、sampling、roots、elicitation、`tools/list_changed`；websocket 的 server。SDK 的旧式 SSE 传输会吞掉它的 POST 请求的错误，所以对一个已死的 SSE server 发调用，要等 120 s 的时限才结束。Zed 在第一个工作树加载之前发的 `session/new`，`mcpServers` 是空的（zed#64611）；agent 分不出这和「没有 server」，那样的对话里没有 MCP 工具，也没有提示。`zypi` 里没有配 server 的办法。server 声明的 `annotations` 照单全收（决策 21 的 Q3；用户在 2026-10-25 答「按你的倾向来」，所以这是认可的默认）。没有在真的 Zed 的界面里验证过。
- **新：从没存在过的 UUID 也能 `session/load`**（r20，[#34][pr-34]）。agent 分不出它和空白对话的 id，所以会开一个空会话；`zypi export <uuid>` 对它说「没有可导出的内容」，不再说「not found」。另一个 agent 进程先存了同一个会话时，这个进程的第一条消息被拒绝；反过来的次序没有保护。

**不在 V0.6。** 决策 7 的 (b)(c)、8、9、10 的 `mcp.rs` 与 `recent_dirs.rs` 两项、11、13、14（决策 20 已定并已做：条件 5，[#39][pr-39]；决策 21 已定，没有要做的）；阶段 0 的余项（0.9 的 Landlock 层，macOS 与 Windows 一侧）；阶段 1 的余项（1.P3 的 (b)(c)、1.P5、1.P6、1.R2、1.R4、1.R5、1.R3 的 MCP 子进程与 Windows）；B.2–B.4；阶段 D 全部（D.1–D.9）；`protoc_ci_env`（在 `tui-ci.yml` 里设 `PROTOC`）。它们归哪个里程碑，等暂停之后由用户排。决策 14 的倾向里，第一步是对着真 agent 测 `remember_tool_approvals` 与 `default_selected_permission`；这一步只量不改，不用等用户定。

### 下一步

- **已发布（V0.6，r22）：** V0.6 的六项退出条件都有结果，v0.6.0 在 r22 发布（条件 6，[§10.25][l10-25]）：tag `v0.6.0` 在 `5f73e07`；[`Release` 的运行][run-release-v060]一次就成功；PyPI 上七个包是预期的版本；[GitHub Release][rel-v060] 有 16 个资产；核对没有发现缺陷。PR [#40][pr-40]（`75f83ae`）已合入；本 PR（[#41][pr-41]）在核对之后合入；GitHub 里程碑 [v0.6.0][ms-v060] 关闭。
- **发布之后留下的（r22，要用户定；都没有改）：** ① `release.yml` 的 `build-tui` 用 `ubuntu-latest`，而 `tui-ci.yml` 钉 `ubuntu-24.04`；运行的通知说 `ubuntu-latest` 要迁到 Ubuntu 26，迁了之后 Linux 的二进制会按更新的 glibc 链接（现在要 glibc 2.39 或更新），Ubuntu 24.04 上就加载不了。我倾向把 `build-tui` 钉到 `ubuntu-24.04`（一行）。② glibc 的下限没有写在 `docs/TUI-AND-CODE-AGENT.md` 里，可以补一句。③ Node.js 20 弃用的警告（`actions/cache`、`upload-artifact`、`download-artifact`、`arduino/setup-protoc`），与 PyPI 一侧的 Trusted Publishing（要账号所有人在 PyPI 上设）：现在都不影响发布。④ 「不在 V0.6」里的各项归哪个里程碑，由用户排；现在没有 V0.7 的里程碑。
- **已合入 `main`，已在 v0.6.0 发布（r22）：** ADR1 的 PR 2（[#23][pr-23]，`7e0ca5a`）与 PR 3（[#24][pr-24]，`0476ea4`）；决策 2、4、5、7 按倾向做（r13，见 [§10.16][l10-16]；r14 合入，见 [§10.17][l10-17]）。决策 17 的 PR [#25][pr-25]（r14 做，`2b0baf3`）与决策 10 的存储层 PR [#26][pr-26]（r15 做，`7d82c27`）：r16 按这个顺序合入（#25 先合，#26 的基改成 `main`，把 `main` 合进它的分支，`git merge -s ours`，树相同时；见 [§10.19][l10-19]）。r19 又合入 #27–#31（`7542062`、`ce29cd8`、`c3dc73e`、`d2182d9`、`b33017a`；用 `gh stack` 一次原子合入；见 [§10.22][l10-22]）：决策 10 的收尾（#27）、测试之间的竞争（#28）、`release.yml` 的 `repo-token`（#29）、`protoc` 的绝对路径（#30）、`cargo nextest`（#31）。r20 又合入 #33–#36（`ca2ab85`、`fa4887b`、`34c11d9`、`e0f2a36`；#34–#36 用 `gh stack` 一次原子合入；见 [§10.23][l10-23]）：V0.6 的里程碑文档（#33）、空白对话再打开（#34，决策 17 的更正）、给扩展的挂钩（#35，决策 19）、`pi-mcp-py`（#36，决策 19）。r21 又合入 #38（`60864af`，r20 的文档）与 #39（`5f73e07`，条件 5：版本号、扩展包的下限、`CHANGELOG.md` 的 `[0.6.0]`；`gh pr merge --squash`；见 [§10.24][l10-24]）。r22 又合入 #40（`75f83ae`，`mode_probe.py` 的探针修复；在 tag 之后，所以不在 v0.6.0 里）；#41（本 PR：r21、r22 的文档）同样在 tag 之后。见 [§10.25][l10-25]。
- **已定，不做：** 翻页的代价（决策 18；用户在 2026-10-18 答复：不做，1,000 个会话以内够用）。
- **已定，已做，已发布（v0.6.0，r22）：** 扩展包的版本号与核心下限（决策 20；用户，2026-10-25：按倾向）：四个下限抬到 `>=0.6.0`（`pi-dynamic-workflows-py` 的 harness 下限也是），三个已发布的扩展包抬到 0.3.0（r21，[#39][pr-39]，`5f73e07`）。MCP 扩展设计里的 11 个默认值（决策 21；用户，2026-10-25：按倾向，全按默认）已定，没有要做的。
- **要等用户决定：** 1.P6 的旗标契约（决策 8）；设置页里其余的项（决策 14）；真的 Plan 模式（决策 7 的 (b)）。
- **已合入，进 V0.5：** 决策 6 的 (a)（`zypi export` 经 ACP 回放，PR [#18][pr-18]）与决策 15 的 (b)（pager 用本地已有的数据画状态行，PR [#19][pr-19]；原选的 (a) 在复核时没有过 DESIGN §8.5 的五条），r12 续 6、续 7、续 8。
- **已发布：** V0.5 的六项退出条件都有结果（条件 4 是用户决定不做），v0.5.0 在 r12 续 8 发布（条件 6）。条件 3、5 各有一个 PR（[#14][pr-14]、[#15][pr-15]，都已合入）；发行构建的版本号（决策 16）做在 PR [#17][pr-17] 里，已合入。
- **不用等决定：** 阶段 D 做不做，看 [`docs/baselines/tui.md`](../baselines/tui.md) 的数据；r11 又提出 D.5–D.9 五个候选。r15 之后的四个小项（[§10.18][l10-18]）r16 都做了（PR [#27][pr-27]，r19 合入；[§10.19][l10-19]）：`pi-shell` 里没人点名的依赖（10 个）；`leader`、`x.ai` 的上限降到 243 与 297；调用图报死的 `session::helpers` 与 `session::worktree`；`[storage] cleanup_ttl_days` 这个设置。r17 做了同类竞争的检查（PR [#28][pr-28]，r19 合入；[§10.20][l10-20]）。还剩：D.8 的新候选（`pi-workflow`、`Config.storage_mode`、没人加载的文件）。

### 还没有验证的

- **平台。** Rust 的 e2e 用例与 `scripts/tui_pty/` 的 PTY 脚本只在 Linux（WSL2）上跑过；macOS 与 Windows 上没有。Windows 上只量了 Python 一侧的延迟，TUI 没有参与。
- **真实 LLM。** r9、r10 的实测都用 mock。r11 用真模型跑了一轮网络审计（C.1，只看 `zypi` 有没有外连），没有用它验证别的功能。
- **沙箱的 Landlock 层。** WSL 的内核 5.10 没有 Landlock；`pi-sandbox` 的端到端用例在那里空转（软跳过）。
- **延迟。** 只量了合成的会话（12 / 120 / 2,000 条消息）。冷磁盘、网络文件系统、超过 1,000 个会话、真实形状的会话没有量。
- **跨目录的 `--resume <id>`。** TUI 的当前目录与会话保存的目录不同时，`@` 搜索、diff、git 信息用哪个目录，没有验证。
- **旗标。** 除 `--always-approve`、`--permission-mode`、`--continue`、`--resume`、`--session-id` 外，没有逐个验证旗标在交互模式下是否生效。`-m` / `--model` 尤其没有查。
- **设置页里其余的权限项**（`remember_tool_approvals`、`default_selected_permission` 等）对 Python 是否有效，没有验证（待决策 14）。
- **同一个 id 的并发。** 锁只在进程内。两个 agent 进程共用一个 home 时，同一个 id 仍可能各建一个会话。
- **测试之间的竞争。** r15 找到并修了一个（`GROK_HOME` 的串行键，[§10.18][l10-18]），r17 查了同类（[§10.20][l10-20]）：19 个偶发失败的测试和 5 个不还原环境变量的测试都修了。**还在的：** ① 约 140 个测试（21 个文件）写外观缓存里的别的设置（`set_show_thinking_blocks` 69、`set_group_tool_verbs` 20、`set_collapsed_edit_blocks` 16、`set_vim_mode` 15 等）；它们没有隔离，没有已知的受害者，但 `command_palette_search_bar_cursor_only_when_focused` 在 1/60 遍里失败过（原因没有证实；隔离之后没有再出现）。`blank_quote_line_survives_drag_copy_as_blank` 失败过一次，没有复现。探针还列出读 `GROK_HOME`、`GROK_TEST_VERSION` 又不拿锁的测试（`pi-pager` 6 组，`pi-shell` 25 组）和 `pi-shell` 里有名字的 `serial` 键（`attribution_emit_count` 保护的计数器有谁在锁外增加，没有查）；上面的运行没有因它们失败，我没有逐个读。② 别的 7 个 suite 的 lib 只各跑了 10 遍；清单外的 crate（`pi-workspace`、`pi-fast-worktree`、`pi-update` 等）没有查；集成测试二进制没有查。③ `in_own_process` 的限制：调用前后的代码在两个进程里都跑；子进程带着启动那一刻的环境变量；取不到测试线程名时主体在当前进程里跑（自检会发现）。④ 只在 Linux。竞争只在测试线程多、顺序不同的时候出现（CI 是 4 个线程，顺序固定），所以 **CI 通过不能证明它们不存在**。⑤ 最后的提交 `6438b59` 上没有在本地跑测试，也没有跑 `clippy`（见 [§10.20][l10-20] 的「验证」）；CI 在 `ea9609f` 上跑了测试，通过。`clippy` 还没有跑过。
- **发行构建。** 发布的两个二进制是 [`Release` 的运行 38358144687](https://github.com/zy1233/pi-python/actions/runs/38358144687) 在 `be3f8a8` 上构建的（体积见条件 2）。Linux 的那个下载后跑过（条件 6）：`zypi --version` 打印 `zypi 0.5.0 (be3f8a8e5e20)`，PTY 脚本通过。**macOS 的二进制没有人运行过**：构建通过了三次（演练两次，发布一次），那只证明它能编译与链接。**Windows 没有二进制。** 更早的 `release-dist` 产物（[run 38353042677](https://github.com/zy1233/pi-python/actions/runs/38353042677)、r12 开头的 [run 38221952524][run-baseline]、两次演练）也没有人运行过。`TUI CI` 在 PR 上不运行 `zypi --version`；手动的 `release_baseline` 为了量耗时跑它，但丢掉输出，也不看退出码。**V0.6（r22）：** 两个二进制是 [`Release` 的运行][run-release-v060]在 `5f73e07` 上构建的。Linux 的那个下载后跑过：`zypi --version` 打印 `zypi 0.6.0 (5f73e07964f1)`；`smoke`、`exit_matrix`、`resume_matrix`、`mode_probe`、`status_line_probe` 对着 PyPI 的 agent 都通过（[§10.25][l10-25]）；它与构建产物逐字节相同。**macOS 的二进制仍没有人运行过**（只看了文件头：Mach-O arm64）。**Windows 没有二进制。** Linux 的二进制要 glibc 2.39 或更新。

出处：执行记录 [§10.10][l10-10]–[§10.15][l10-15] 各节的「没有验证」。

## 待决策

共二十一项。能给出倾向的，都写了倾向。没有一项会在用户确认之前动手。编号沿用 r9 的提议（[§10.11][l10-11] 末尾「留给用户决定的」十二项，原文留在那里）；13、14 是 r10 加的，15 是 r11 加的，16 是 r12 加的，17、18 是 r13 加的，19–21 是 r20 加的。附录 A 的「决定 N」指这里的第 N 项。

- **已做：** 1 的 PR 1（PR [#11][pr-11]）、PR 2（PR [#23][pr-23]）、PR 3（PR [#24][pr-24]；后两个 r14 合入 `main`）、2、3、4、5、6 的 (a)（PR [#18][pr-18]）、7 的 (a)、12、15 的 (b)（PR [#19][pr-19]）、16 的 (a)（PR [#17][pr-17]）、17（PR [#25][pr-25]，r16 合入；r20 的更正：PR [#34][pr-34]）、10 的存储层一项（PR [#26][pr-26]，r16 合入）与它的收尾（PR [#27][pr-27]，r19 合入）、19（PR [#35][pr-35]、[#36][pr-36]，r20 合入）、20（PR [#39][pr-39]，r21 合入）、21（用户，2026-10-25：按倾向，即全按默认；默认值已在 PR [#36][pr-36] 里）。
- **已定，不做：** 18（用户，2026-10-18）。
- **待定：** 7 的 (b)(c)、8、9、10 的 `mcp.rs` 与 `recent_dirs.rs` 两项、11、13、14。

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
    - 状态：**已定，已做**（用户，2026-10-16：按倾向；r13，PR [#23][pr-23]）。`--continue` 与不带值的 `--resume` 取本目录里 `updatedAt` 最大的会话，翻完所有页。实测：（`scripts/session_list_bench.py`，3 次中位数）`--continue` 带 `cwd` 翻完所有页：100 / 1,000 / 5,000 个会话时，Windows 约 50 ms / 0.8 s / 12.3 s，WSL2 约 27 ms / 0.6 s / 10.2 s；`--session-id` 不带 `cwd` 翻完所有页：Windows 90 ms / 1.8 s / 33.9 s，WSL2 42 ms / 1.3 s / 28.2 s。每一页 agent 都重新列一遍目录，所以总代价是页数 × 目录大小，增长得比会话数快；5,000 个会话时在 Windows 上超过 30 s 的上限。修法在 Python 一侧，见新增的待决策 18。
    - 详情：[§10.11][l10-11]、[§10.16][l10-16]
5. **未知 id 的报错时机。** `--resume <未知 id>` 现在进了 TUI 才报错，状态栏的模型名还是 `unknown`。
    - 倾向：进 TUI 之前报错，退出码 1（`resume_matrix.py` 的用例 8 随之收紧）。
    - 状态：**已定，已做**（用户，2026-10-16：按倾向；r13，PR [#23][pr-23]）。`--resume <未知 id>`、`--resume <未知标题>`、没有会话时的 `--continue`、已用的 `--session-id`，都在进事件循环之前退出，退出码 1，终端先还原、agent 先停。`resume_matrix.py --strict` 13/13 通过（用例 8 要求退出码 1）。
    - 详情：[§10.11][l10-11]、[§10.16][l10-16]
6. **`zypi export`。** 现在对 Python 会话恒报 not found，是一个静默坏掉的命令。r8 时用户选了不动。
    - 倾向：(a) 经 ACP 回放导出：起 agent、`session/load`、收 `session/update`，复用 `render_blocks_to_markdown`，约 100 行，多 1–2 s 的 agent 启动；(b) 删掉子命令。不要 (c) 维持现状。
    - 状态：**已做 (a)，已合入 `main`，进 V0.5**（PR [#18][pr-18]，合入后是 `ec7d455`；用户，2026-10-13，r12 续 5 定、续 8 合入；记录 [§10.15][l10-15] 续 6、续 8）。做法按倾向里写的：`export_cmd` 起 agent、发 `session/load`、收 `session/update`，经 `AcpUpdateTracker` 与 `render_blocks_to_markdown` 写成 Markdown，与 `/export` 同一条渲染路径；命令结束时（失败时也一样）agent 被停掉。最后几条回放可能比 `session/load` 的响应晚到，所以响应之后静默 500 ms 才算收齐；agent 60 s 不出声算错误。代码约 300 行（不是估计的 100 行：收集、静默期、对 agent 的请求逐一回答占了大头），测试 18 个加一个对着真 agent 的 e2e。ADR1 的 PR 3 删旧存储层之前要先做完这一项：做完了，`export_cmd` 不再引用 `pi_shell::session::storage`（`tui-support.md` 里它的外部引用 1 → 0）。合入之后，`zypi export` 不再是 V0.5 的已知问题；`CHANGELOG.md` 的 `[0.5.0]` 有一条 Fixed。
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
    - 状态：**部分已做**（用户，2026-10-18：做存储层这一项；r15，PR [#26][pr-26]，叠在 #25 上；r16 合入 `main`，`7d82c27`）。用户 2026-10-16 的答复没有包含这一项，r13 时是待定。**存储层做了：** ADR1 的 PR 3 之后，pager 一侧对 `session::storage` 的引用是 0，对 `session::persistence` 只剩 3 处，都是清洗标题的纯函数，不读盘。r15 删了 `pi-shell` 的 `session::storage`（14 个文件，10,007 行）与 `session::persistence`（12 个文件，2,009 行），再删编译器随后报「没人用」的 41 处。pager 有意用的两个纯函数搬到新的 `session::title`（加了 8 个单元测试）。r14 估的「约 8,200 行」是调用图数的非测试行（7,372 + 803）；算上测试文件是 12,016 行。没有行为变化。`tui/`：+185 / −14,165 行；`pi-shell` 的测试 1,056 → 864。**r16 的收尾**（PR [#27][pr-27]，r19 合入 `main`，`7542062`）：删了存储层删掉之后没有用户的东西：15 个没人加载的模板与脚本文件、调用图报死的 `session::worktree` 与 `session::helpers`、`util::config::worktree`、`[storage]` 设置（`StorageConfig`）、10 个源码没点名的依赖；`tui/` 共 +13 / −1,644 行，`pi-shell` 的测试 864 → 838，依赖图 980 → 970。**没做：** `load_mcp_servers` / `mcp.rs`（`pi-shell/src/util/config/mcp.rs`；pager 只在一个测试里调用它）与 `recent_dirs.rs`（`pi-pager/src/recent_dirs.rs`）：用户的答复只说了存储层，这两项没有问过。做法与数字见 [§10.18][l10-18]。
    - 详情：[§10.11][l10-11]、[§10.18][l10-18]
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
    - 状态：**已做 (b)，已合入 `main`，进 V0.5；做法从 (a) 改成了 (b)**（PR [#19][pr-19]；用户，2026-10-13：r12 续 5 选 (a)，续 7 复核后改选 (b)，续 8 合入）。用户先选了 (a)，不是上面写的倾向。做之前先过 DESIGN §8.5 的五条（缺一就不加），结果两条不过：① 没有标准对应物——不过：Python agent 能如实发的东西，pager 本来都知道（工作目录、会话 id、模型——它读 `configOptions`、会话名、轮的计时），或者已有标准载体（版本在 `agentInfo.version`；上下文与费用在 ACP 的 `usage_update`，v1 已稳定）；Python agent 自己既不知道模型的上下文窗口（CLI 从不设 `Model.context_window`，配置里也没有这个键），也不知道价格，所以 `context` 与 `cost` 哪条路都给不出。④ 线上类型只定义一处——不过：`SessionNotification` 与 `PiSessionUpdate::SessionStatus` 在 `pi-shell`，那是旧运行时的 crate。②③⑤（Python 的生产者与测试、在 `docs/specs/` 登记、经 `initialize` 协商）做得到。所以不加这个扩展，用户改选 (b)：pager 用本地已有的数据画（工作目录、模型、会话 id、版本、会话名，加持有会话的时长与轮的起点两个钟），只改 Rust，线上没有新东西，Python 不动。agent 发来的状态仍然优先（`status_proxy_agent.py` 还在，当那样的替身），客户端的数据只补空的字段；轮开始与结束、换模型、改名都会让行重建。`context` 与 `cost` 在 Python agent 下仍然空，文档里写明了，没有造数；git 分支、仓库、worktree 不填，脚本自己跑 `git`。指南 `25-status-line.md` 与 `status_line_probe.py` 的预期照新的行为改了。合入之后，这一行画得出来；`context` 与 `cost`、分支与仓库在 Python agent 下仍然空，这是 V0.5 的一条已知问题（`CHANGELOG.md` 里也有）。
    - 详情：[§10.14][l10-14]、[§10.15][l10-15]
16. **发行构建的版本戳。** r12 时，`zypi --version` 里的版本号是 `1.0.8`（后面跟提交的前 12 位）：`pi-pager-bin/build.rs` 取环境变量 `GROK_VERSION`，没有就取 crate 的版本，而 `pi-pager-bin` 与 `pi-version` 的版本都是继承自上游的 1.0.8。仓库里没有任何文件设 `GROK_VERSION`，`release.yml` 在 v0.3.0、v0.4.0 与 `main` 上都没设。ACP 的 `agentInfo.version` 写死 `0.1.0`（`pi_agent_cli/agent.py` 的 `_AGENT_INFO`），Phase 6/7 审计记过，没改。
    - **不要靠在 `release.yml` 里设 `GROK_VERSION` 来修。** 同一个变量还决定「是不是发行构建」（`pi_version::IS_DEV_BUILD`）：没有它就是开发构建，发行二进制至今都是开发构建。开发构建里文件夹信任整个是惰性的（`folder_trust_inert()`）：从不提示，不读写 `trusted_folders.toml`，不管配置怎么写。盖了戳，它按默认开启，会对仓库里的本地配置（`.envrc`、`.claude`、hooks、插件、MCP、LSP）问「信任这个文件夹吗」。这些配置里，MCP 的加载代码在 1.P4 之后没有调用者（决策 10）；其余没有逐项核对。遥测客户端的 `dev_build` 字段也随戳翻转。
    - 选项：(a) 不盖戳；把 `pi-pager-bin` 与 `pi-version` 的 crate 版本改成发行版本，`_AGENT_INFO` 读已装包的版本；(b) 盖戳，再处理文件夹信任（删掉，或查清它还管着什么）与遥测；(c) 维持现状，写进已知问题。
    - 倾向：(a)。
    - 核对（r12）：版本号从 1.0.8 改小，会碰到三处按版本比较的代码。① `pi-config` 的 `version_overrides`：配置里的补丁用 `minimum_version` / `maximum_version` 限定版本范围，比较的是 `pi_version::installed_semver()`；版本改小之后，要求 `minimum_version` 高于新版本的补丁不再生效。仓库里没有默认配置带这样的补丁（只有代码与测试）；用户自己写的，无从查起。② `pi-update` 的 `version_policy.rs` 与 `auto_update.rs` 按 semver 比较：自动更新没有启动（`pi-pager-bin/src/main.rs` 里是 `let bg_update_rx = None;`，子命令里也没有 `update`），这些比较不会跑。③ `pi-shell` 的模型缓存（`agent/models/cache.rs`）只判断版本字符串是否相等。没有发现必须先改的逻辑。
    - 状态：**已做 (a)，已合入 `main`**（r12 续 2，PR [#17][pr-17]，叠在 #15 上；合入后是 `d57fd9b`）。用户先选了 (a)，那是倾向；后来他要求现在做。改了：`pi-pager-bin` 与 `pi-version` 的 `Cargo.toml`，和 `Cargo.lock` 里这两个包，`1.0.8` → `0.5.0`（`pi-agent-cli-lc` 的版本）；`pi_agent_cli/agent.py` 的 `_AGENT_INFO` 读已装包的版本（没有包元数据时是 `0.0.0+unknown`）。两个新的测试文件：`packages/pi-agent-cli/tests/test_agent_info.py`、`scripts/tests/test_release_versions.py`：agent 报告已装包的版本；两个 crate 与 `Cargo.lock` 必须等于 `pi-agent-cli-lc` 的版本（所以以后每次发布要一起抬高）；没有工作流设 `GROK_VERSION`（最后一条用户没有要，是为了守住 (a)；不想要就删）。`pi-pager` 与 `pi-shell`（也是 1.0.8）不在决策里，没有改。**行为变化**：版本号比以前小，`minimum_version` 高于 `0.5.0` 的 `[[version_overrides]]` 补丁不再生效（见上面的核对）。验证：Windows 的 `pytest` 全量；Linux 上 `CI` 的 `pytest`（Python 3.11–3.13）与 `TUI CI`（`cargo check`，`cargo test` 8,665 通过 / 0 失败 / 22 忽略）都通过。没有测试检查 `zypi --version` 的输出；续 3 在 WSL2 里跑过 `0efd400` 的开发构建，打印 `zypi 0.5.0 (0efd40071ca0)`。做法与数字见 [§10.15][l10-15]。
    - 详情：[§10.15][l10-15]
17. **空会话。** Python 在 `session/new` 时就建会话文件，没发过消息的会话以标题 `(no messages)` 留在列表里。`zypi --continue` 在它是本目录最近活动的会话时会取到它（r13 的已知限制，[§10.16][l10-16]）。
    - 倾向：Python 在第一条用户消息时才写会话文件。要查的：已发出 id、文件还没有时，`session/load`、`has_session`（重复 id 检查）与列表各怎么答。备选：pager 在 `--continue` 里跳过没有标题的会话——这把 agent 建文件的时机带进 pager，也会漏掉真的空会话；不倾向。
    - 状态：**已定，已做**（用户，2026-10-18：做；r14，PR [#25][pr-25]；r16 合入 `main`，`2b0baf3`）。r13 时是待定。agent 在 `session/new` 时只在内存里建会话，第一条用户消息才把文件头、之前攒下的条目和这条消息一次写下去（`DeferredJsonlSessionStorage`，`JsonlSessionRepo.create({"deferred": True})`）。没发过消息的会话：没有文件，不在列表里，`session/load` 与 `session/resume` 答 `resource_not_found`（**r20 更正了这一点，见本条最后一句**），仓库的 `has_session` 为假；客户端手里那个会话照常能用。客户端指定的 id（`--session-id`）在会话打开期间由 agent 记着（`_reserved_ids`），所以第二个 `session/new` 带同一个 id 仍被拒绝；另一个进程先存了同一个 id 时，这个会话的第一次 `prompt` 被拒绝（`invalid_params`）。这缩小了两个 agent 进程之间的空隙，没有堵死。旧版本的 agent 写的、只有文件头的文件仍在列表里，标题 `(no messages)`。pager 的生产代码没有改。**r20 的更正**（PR [#34][pr-34]，`fa4887b`；[§10.23][l10-23]）：上面说 `session/load` 与 `session/resume` 答 `resource_not_found`，对 Zed 不行。Zed 把每个显示过东西的对话的 id 存下来（agent 发的提示也算，例如 MCP 的提示）；重启后它用 `session/load` 要同一个 id，得到 `resource_not_found`，显示 *Failed to Launch*，*Retry* 用同一个 id，同样失败。现在这两个方法在没有文件、id 是规范 UUID（小写、带连字符）时，在内存里再开一次这个 id：空会话，没有文件，不在列表里；别的 id 仍答 `resource_not_found`；已在本进程打开的 id 直接返回。文件仍在第一条消息时才写，决策 17 的其余部分不变，仓库的 `has_session` 仍为假。两个副作用：① 从没存在过的 UUID 也会打开（agent 分不出它和空白对话的 id），所以 `zypi export <uuid>` 对它说「没有可导出的内容」，不再说「not found」；② 再打开的空会话占着自己的 id（和 `--session-id` 一样）：另一个 agent 进程先存了它时，这个进程的第一条消息被拒绝；反过来的次序没有保护（保护要在每个会话的第一条消息时列一次目录：1,000 个会话 0.07 s，5,000 个 0.3 s）。17 个新测试加 2 个 stdio 测试，旧 agent 上 12 个失败。没在真的 Zed 里验证过。
    - 详情：[§10.16][l10-16]、[§10.17][l10-17]、[§10.23][l10-23]
18. **翻页的代价。** agent 对每次 `session/list` 都重新列一遍目录，并为每个文件建一个 `FileInfo`（`JsonlSessionRepo._session_names` → `LocalExecutionEnv.list_dir`），所以翻完所有页的代价是「页数 × 目录大小」，增长得比会话数快（实测见 [§10.16][l10-16]）。1,000 个会话以内在 2 s 以内；5,000 个会话时，`--continue`（带 `cwd`）要 10–12 s，`--session-id`（不带 `cwd`，翻完）要 28–34 s，在 Windows 上超过 30 s 的上限，会超时。
    - 倾向：Python 一侧让 `_session_names` 只取文件名（`os.listdir`），不 `stat`、不建 `FileInfo`。一个没有提交的草稿（Windows，每项 1 次运行）：5,000 个会话时，带 `cwd` 翻完 12.3 s → 2.2 s，不带 `cwd` 翻完 33.9 s → 3.6 s，带 id 的 `session/new` 325 → 33 ms。改动在 `pi-agent-harness`，约十行；要查的：`list_dir` 的其他调用者，测试里的假 fs（没有 `list_names` 时要退回 `list_dir`）。备选：`--session-id` 不预检，直接 `session/new`，由 agent 拒绝重复的 id（要把 `session/new` 挪到进事件循环之前，改动大）；或者什么也不做（1,000 个会话以内没有问题）。
    - 状态：**已定，不做**（用户，2026-10-18：不做；r14）。r13 时是待定。1,000 个会话以内够用（在 2 s 以内）；5,000 个会话时 `--session-id` 在 Windows 上会超过 30 s 的上限，这是已知的，`CHANGELOG.md` 的 `[Unreleased]` 里写了数字。上面的草稿没有提交。要改主意时，倾向里的做法仍然可用。
    - 详情：[§10.16][l10-16]、[§10.17][l10-17]
19. **MCP 的方向。** 编辑器（Zed 等）在 `session/new`、`session/load`、`session/resume` 里传来 MCP server。agent 没有 MCP 客户端：Phase 6（2026-09-24）用户定的是「core 与 CLI 不连 server」，与上游 pi 的 "No MCP" 一致；r9 把「静默丢弃」改成「明说」。「编辑器经 ACP 传来 server 时怎么办」没有单独问过用户。ACP v1 要求 agent 支持 stdio 的 MCP server，所以这是有文档的偏离。
    - 选项（用户在 2026-10-23 被问）：(a) 做可选扩展包，连接客户端传来的 server，core 与 CLI 仍不内置；(b) 内置进 `pi-agent-cli`（要改 2026-09-24 的决定）；(c) 保持不支持，提示每个进程只出现一次；(d) 保持不支持，提示照旧。
    - 状态：**已定，已做**（用户，2026-10-23：(a)；r20，PR [#35][pr-35]、[#36][pr-36]；合入 `main`，`34c11d9`、`e0f2a36`）。#35 给扩展挂钩（`ClientMcpServer`、`pi.claim_client_mcp_servers`、`pi.notify`、`pi.register_cleanup`、`pi.add_turn_barrier`；`HarnessBridge` 多 4 个成员）；agent 本身仍没有 MCP 客户端。MCP 提示改成只写没人接手的 server，并告诉用户装 `pi-mcp-py`。#36 是新包 `pi-mcp-py` 0.1.0（`packages/pi-mcp`，入口点 `pi-mcp`）：连接 stdio、Streamable HTTP、SSE 三种 server，工具名 `mcp__<server>__<tool>`，有 `/mcp` 命令；core 与 CLI 不导入它。没装这个包时，agent 与 r9 一样：忽略并说明（所以 1.P4 的「忽略并说明」仍是没装扩展时的行为）。`zypi` 本身不传 server（pager 没有 MCP 界面），所以对 `zypi` 用户没有直接的效果；`zypi` 里也没有配 server 的办法（设计文档 Q5）。设计：[`docs/specs/2026-10-23-mcp-extension-design.md`](../specs/2026-10-23-mcp-extension-design.md)。发布：`release.yml` 装它做发布前测试并构建它，所以下一个 tag 会把它与别的包一起发出去（V0.6 的条件 6）。
    - 详情：[§10.23][l10-23]
20. **扩展包的版本号与核心下限。** `pi_agent_core/tests/test_extension_packaging.py` 要求每个扩展包为工作区里的包写的下限，等于那个包在工作区里的版本（「扩展与核心一起发」）。V0.6 把核心升到 0.6.0 时，四个扩展包的核心下限都要从 `>=0.5.0` 变成 `>=0.6.0`，否则这个测试失败。四个包是：`pi-web-access-py`、`pi-goal-x-py`、`pi-dynamic-workflows-py`（都是 0.2.0）和 `pi-mcp-py`（0.1.0）。我在本地把核心的版本改成 0.6.0 验证过：4 个失败，每个包一个。`pi-mcp-py` 需要这个下限，因为它用 #35 的挂钩，核心 0.5.0 没有。r19 在 V0.6 的条件 5 里写的「三个扩展包不动」因此不成立。三个已发布的扩展包自 v0.5.0 起没有代码变化。
    - 倾向：(a) 照 V0.5 的做法（`7e7d4d4`：下限从 `>=0.4.0` 抬到 `>=0.5.0`，版本从 0.1.0 抬到 0.2.0）：升版本号时，四个下限一起抬到 `>=0.6.0`，三个已发布的扩展包也各抬一个版本。原因：`publish-pypi` 用 `skip-existing: true`，版本不抬的话，PyPI 上留着旧的元数据（`>=0.5.0`），而仓库里同一个版本号写的是 `>=0.6.0`。抬哪一位，原来写的是「由用户定」；用户答「按你的倾向来」之后（r20），我选小版本（0.2.0 到 0.3.0），不抬补丁位：V0.5 也是这样（0.1.0 到 0.2.0），而且下限抬了，新版本就装不进核心 0.5.x；在 0.x 里这用小版本表示。备选：(b) 只抬下限，不抬三个包的版本：PyPI 上的 0.2.0 与仓库里的 0.2.0 元数据不同；不倾向。(c) 三个已发布的包保持 `>=0.5.0`，改测试只管 `pi-mcp-py`：这改了「一起发」的规则；不倾向。
    - 状态：**已定，已做**（用户，2026-10-25：「按你的倾向来」；r20 定，r21 做：PR [#39][pr-39]，合入 `main`，`5f73e07`；r22 随 v0.6.0 发布）：(a)。升版本号时（V0.6 的条件 5）：四个扩展包的核心下限都抬到 `>=0.6.0`；`pi-web-access-py`、`pi-goal-x-py`、`pi-dynamic-workflows-py` 从 0.2.0 抬到 0.3.0；`pi-mcp-py` 保持 0.1.0（第一次上传）。倾向里原来没有写抬哪一位；0.3.0 是我照 V0.5 的先例选的（已写进上面的倾向），用户没有单独说。**r21 的更正**：上面写的「4 个失败，每个包一个」漏了一处：`pi-dynamic-workflows-py` 还有一个 harness 的下限（`pi-agent-harness-lc>=0.5.0`），也抬到 `>=0.6.0`；测试对每个扩展包只报一个失败，停在第一个不对的依赖上。
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

- [x] C.1 运行时验收：阶段 0.3 的 mock-ACP e2e 全绿；一次可选的真实模型 smoke，用进程级网络连接核对 `zypi` 进程无到 LLM provider 的外连。**状态**（r11，Linux / WSL2）：e2e（`python_agent_e2e`，`PI_E2E_REQUIRED=1`，不是跳过）在本分支上全绿。网络审计是 `scripts/tui_pty/net_audit.py`：在 PTY 里跑一轮对话，约每 9 ms 读一次 `zypi` 与它之下每个进程的 socket（`/proc/<pid>/fd` 对 `/proc/<pid>/net/*`），查 13 项。mock LLM 13/13，放进没有网络的命名空间（`bwrap --unshare-net`）再跑一次也是 13/13；真模型一次（本机配置的 provider）13/13：`zypi` 没有到非本机地址的连接，也没有监听；agent 有到两个远端地址的连接，这是正向对照，说明采样看得见外连。另有一个发现：配了 `[ui.status_line]` 也画不出那一行，因为 Python agent 不发画它所需的消息（待决策 15；r12 续 5 已定，续 7 做成 PR（#19），续 8 合入：改成 pager 本地画，不再靠那条消息）。记录 [§10.14][l10-14]。
- [x] C.2 内嵌 user-guide 裁剪或重写（同步 `status_line_tests.rs` 夹具），并停止每次启动的 `extract_user_guide_docs`。**状态**（r11）：`pi-pager/docs/` 原有 33 个文件（用户指南 23 页加 README，教程 9 页，全是 Grok 原文），删了 25 个（用户指南 16 页、教程 9 页），现在只剩 `user-guide/` 里的 7 页加一份 README（入门、斜杠命令、主题、会话、终端支持、权限与安全、状态行），按代码和 `agent.toml` 重写。教程叠层（`views/tutorial.rs`、`tutorial_docs.rs`）删了：A.3 之后没有入口，页面讲的是 zypi 没有的命令。`extract_user_guide_docs` 与它在 `pi-pager-bin` 里的调用删了，启动不再往 home 写文档。`status_line_tests.rs` 里把 `25-status-line.md` 当夹具的三个用例没动，对重写后的页面仍通过。两个守卫用例（页面里没有旧产品名；目录里每个文件都在 `USER_GUIDE` 里）。`pi-pager` 的测试 5,626 → 5,608。记录 [§10.14][l10-14]。
- [x] C.3 `tui/NOTICE`（产品二进制名、声明 `tui/` 为 hard fork、不再跟随 upstream）；`tui/THIRD-PARTY-NOTICES`（18,898 行，含 `async-openai`）在依赖变化后重新生成并记录方式。**状态**（r11）：`NOTICE` 重写：产品是 `zypi`，`tui/` 是 hard fork、不再跟随 upstream，并列出改动。`THIRD-PARTY-NOTICES` 不是整份重新生成：`scripts/tui_notices.py` 取 `pi-pager-bin` 的 normal 与 build 依赖闭包（五个目标三元组）里会进二进制的包，与文件对账——去掉 222 条过期的、补上 44 条缺的，手写的段落保留；文件从 18,898 行变成 16,548 行（`wc -l`）。新条目的许可证措辞抄自同一 SPDX 表达式的旧条目，工具自己不加许可证政策；没有可抄的就写成朴素格式，并在工具的输出里列为 `needs review`，要人读过。`check` 不一致就退出 1，`update --write` 来改，62 个单测。`check` 还没有接进 CI（它要 cargo；接上要改 `tui-ci.yml`，本分支没有 CI 运行，没法先试）。`third_party/` 里过期的 `nfsserve` 条目也去掉了。记录 [§10.14][l10-14]。
- [x] C.4 `packages/pi-agent-cli/AGENTS.md` 的 config 约束；`docs/TUI-AND-CODE-AGENT.md`（MCP 归属、ACP 链接应为 [agentclientprotocol.com](https://agentclientprotocol.com)）；Phase 4 spec 增补修订记录（§1.3 #2）；DESIGN §8 增补 P2 准入规则。**状态**（r11）：四项都做了。`pi-agent-cli/AGENTS.md` 写了实测的 `config.toml` 约束：`zypi` 认得的键按类型严格解析（顶层的 `permission` 写成字符串、`[[models]]` 写成数组、TOML 本身有误，都会让它以 1 退出），不认得的键忽略；Python agent 只在没有 `agent.toml` 时才回落到 `config.toml`。`TUI-AND-CODE-AGENT.md` 改了 ACP 链接、`[model]` 的真实键、斜杠命令，并写明 zypi 里没有 MCP 客户端。Phase 4 spec 的 §1.3 #2 与 §3 加了修订说明。DESIGN §8.5 写了 `pi/` 扩展的五条准入条件。另外清掉 `grok agent` 的 13 处过时指针（`pi-shell/README.md` 原是上游的用户手册，换成一页说明）。记录 [§10.14][l10-14]。

**退出条件**：`grok agent`、`leader`、`x.ai`、`MvpAgent` 在文档与代码中的 grep 结果为 0（历史文档除外）。（r11 对照：`MvpAgent` 与 `grok agent` 没有命中 ✓；只有门禁自己写出这两个词的地方除外（清单、门禁的单测、`tui.md`），`docs/PLAN` 也除外。`leader`（245 行）、`x.ai`（304 行）清不到 0 ✗：这些行在活着的代码里，是已删除的 leader 进程的名字、xAI 的服务地址，以及「不实现 `x.ai/*`」的过滤本身。所以改成棘轮：`tui_baseline.py` 的 `[gates.residue]` 给每个词一个上限，只降不升，`pytest` 也查；每一类是什么、去哪里，见 [`tui.md` §4](../baselines/tui.md)。文档里剩下的 `x.ai` 是「不实现 `x.ai/*`」的说明或历史审计，没有改。r15 的删除让这两个词少了 2 行与 7 行：现在是 243 行与 297 行，r16 把上限也降到这两个数。）

### 阶段 D（可选，决策门控）：依赖图与 support 层瘦身

入口条件：阶段 B 完成，且基线对比显示编译时间 / 体积 / 维护成本值得继续。

- [ ] D.1 `async-openai` 离开 `cargo tree`：需处理 `pi-tools`（139k 行，pager 仅用约 55 个类型 / 小工具路径，但渲染 crate 也依赖它）与 `pi-sampling-types`（经 `pi-agent`）。二选一：把「工具展示类型」抽到叶子 crate（`pi-tools-api` 仅 715 行，是现成落点），或对 `async-openai` 的用法做 feature-gate。
- [ ] D.2 session 数据层（`session::{storage,persistence,unified_list}`、`relay`，约 16.6k 行）。**状态**（r15）：做了一部分：`session::storage` 与 `session::persistence` 删了（决策 10，PR [#26][pr-26]，r16 合入；清洗标题的两个函数留在 `session::title`）。`session::unified_list`（103 行）还在，调用图报死；`relay` 这一轮没有查。记录 [§10.18][l10-18]。
- [ ] D.3 xAI auth 层（约 18.1k 行）。
- [ ] D.4 视 `pi-workspace`（101k 行，pager 主要用 `permission`）、`pi-agent`（`agents_modal` 删除后几乎无活引用）的剩余耦合决定是否继续。
- [ ] D.5 `pi-test-support`（7,384 行）：外部只有 `EnvGuard`、`scaled`、`resources`（`pi-shell`、`pi-tools` 的测试），和一个 `pi-pager` 测试里的 `TestProcess`、`TestSandbox` 在用；mock 推理服务器、SSE 生成器、ACP stdio 客户端、headless 运行器、计数服务器与脚本化响应共 5,003 行没有外部用户（模块之间的相互引用没有查）。出处：r11 的搜索，[§10.14][l10-14]。
- [ ] D.6 内嵌指南的查看器：`ActiveModal::DocPicker` 与 `DocViewer` 没有入口（非测试代码里，`howto_list_modal` 只被查看器的 Esc 调用，`welcome_doc_viewer` 只在测试里被赋值），启动时的解压又在 C.2 删了，所以七页指南现在没有读者。二选一：接一个入口（斜杠命令或快捷键），或连同选择器、查看器一起删。出处：[§10.14][l10-14]。
- [ ] D.7 发行与更新的遗留：`pi-pager/npm/` 下 7 个打包目录与 `pi-update` 里还写着 npm 包名 `@pi-official/grok` 和 `x.ai/cli/install.*` 的安装提示。自动更新在 Phase 4 §4.4 已禁用（`pi-pager-bin/src/main.rs` 里 `bg_update_rx = None`），这些不会跑，只是遗留。出处：[§10.14][l10-14]。
- [ ] D.8 删掉调用图工具报死、编译器也认可的 16 个模块（被删文件共 6,143 行）：`pi-hooks` 的 `dispatcher`、`result`、`runner`、`runner::command`、`runner::http`（4,339 行）；`pi-shell` 的 `session::events`、`session::telemetry`、`session::unified_list`、`config::reloader`、`auth::flow`、`upload`、`upload::turn`（1,006 行）；`pi-workspace` 的 `publish`（462 行）；`pi-tools` 的 `implementations::editor_infra`（276 行）；`pi-mcp` 的 `wire`（39 行）；`pi-agent` 的 `prompt::browser_verification`（24 行）。第 17 个报死的模块，`pi-tools` 的 `types::error`（44 行），**不能删**：里面是 `?` 要用的 `impl From<…> for ToolError`。要一起删的还有 `pi-hooks/tests/integration.rs`（822 行，测的就是被删的模块），以及删完没人用的 `env_expand::expand_env_vars_with_extra`（`pi-hooks`）。证据：在副本里这样删，三个产品 crate 的 `cargo check` 与 `cargo check --workspace --tests` 都通过，只多那一条告警。没有真的删；副本里没有跑测试，所以不知道会少几个测试。`session::events`、`session::unified_list` 与 D.2 有重叠。出处：[§10.14][l10-14]。**r15 的补充：** 决策 10 的删除（[§10.18][l10-18]）让 `session::helpers`（21 行）与 `session::worktree`（359 行）也成了调用图报死的单元，另外 `session::events` 的行数由 589 变成 591（`impl From<…GoalClassifierVerdict>` 改指新的位置）。它们没有删：rustc 不告警（`pub(crate)` 项在死模块里不告警），要删就得照这一条先在副本里删、再编译。`session::worktree` 在「check first」里（里面有 `ShellWorktreeType` 与 `WorktreeType` 的 `impl`）。**r16 的补充：** PR [#27][pr-27] 照这一条做了：`session::helpers` 与 `session::worktree` 删了，`check` 报出的 `util::config::worktree`（唯一的用户是 `session::worktree`）也删了。现在 `pi-shell` 里调用图报死的单元是 7 个（1,008 行）：`auth::flow`、`config::reloader`、`session::events`、`session::telemetry`、`session::unified_list`、`upload`、`upload::turn`；`session::events`（591 行）在「check first」里（`GoalClassifierVerdictTelemetry` 的 `impl`）。r16 的扫描又找到几个候选，都没有删，只有文本搜索的证据：没有用户的 crate `pi-workflow`（9 个文件，3,570 行；`tui/Cargo.toml` 的成员列表里有它，没有 `Cargo.toml` 依赖它；删它可能让 `Cargo.lock` 少一些只有它在用的包，没有核实）；`Config.storage_mode` 与隐藏的 `--storage-mode`（`pi-pager` 把旗标一路传到 `StorageMode::resolve`，在 `.rs` 里没有找到读 `Config.storage_mode` 的代码）；约 88 个没人加载的文件（`pi-pager/tests/scenarios/` 43 个、`pi-pager/npm/` 24 个（D.7）、`install-enterprise.ps1`、9 个 logo、11 个旧名的快照）。要删，就照这一条先在副本里删、再编译。清单见 [§10.19][l10-19]。
- [ ] D.9 `tui_baseline.py test` 在开发者自己的 `HOME` 下跑测试（r18 起用 `cargo nextest`，以前是 `cargo test`；这一条不变），有测试会往真实的 `~/.pi-python` 与 `/tmp` 写东西。脚本只清了 `PI_HOME`、`GROK_HOME`（好让测试自己设的 `GROK_HOME` 生效），没有隔离 `HOME`。r11 在 WSL 里跑 `cargo test` 之后，`~/.pi-python` 多了 `agent_id`、`worktrees.db`、`config.toml`、`trusted_folders.toml`（18 条，全是已经不存在的 `/tmp/.tmpXXXX` 目录）、`last-copy.txt`、`logs/`；`/tmp` 里有 46 个 `grok-unified-log-test-*` 目录，r11 的运行留下 13 个，其余是更早的运行留下的。18 条信任记录里，16 条的时间与测试建这些目录的时间相差不到 5 秒；另 2 条没有对上的目录。具体是哪个测试写的，大多没有查；r15 查到 `worktrees.db` 的一个来源：`pi-shell` 的 `session::worktree` 里两个 `resolve_worktree_by_id_or_path_*` 测试。没设 `GROK_HOME` 时，它们在真实的 `~/.pi-python` 里打开（没有就建）`worktrees.db`；设了，就在别的测试的临时目录里打开它（[§10.18][l10-18]）。r16 删了 `session::worktree`，这两个测试没有了，所以这个来源没有了（r16 的整套本地运行之后，WSL 的 `~/.pi-python` 里没有 `worktrees.db`）；别的来源没有查。办法：测试运行时把 `HOME` 设成临时目录，并先钉住 `CARGO_HOME` 与 `RUSTUP_HOME`（不然 cargo 找不到工具链）；CI 的 runner 本来就是空 home，这样本地与 CI 更接近。没有做：要把整套测试重跑一遍（本次 22 m）才能验证，而且它改的脚本 TUI CI 也在用。出处：r11 收尾时的检查，[§10.14][l10-14]。

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
| [§10.15][l10-15] | r12 | V0.5 的发布里程碑：范围与退出条件的依据、`GROK_VERSION` 的发现（决策 16）、用户的三个回答、PR 合入与 WSL 验证（续 3）、`smoke.py` 的修复与收尾（续 4）、决策 6、8、14、15 的回答（续 5）、决策 6 的 PR（续 6）、决策 15 的 PR（续 7）、合入 V0.5、打 tag 与发布（续 8） | PR [#16][pr-16]、[#17][pr-17]、[#18][pr-18]、[#19][pr-19]、[#20][pr-20]、[#21][pr-21] |
| [§10.16][l10-16] | r13 | ADR1 的 PR 2 与 PR 3：`resolve_startup`、读盘代码的删除、新门禁；决策 2、4、5、7 的答复；13 处读盘调用点的去向 | PR [#23][pr-23]、[#24][pr-24] |
| [§10.17][l10-17] | r14 | #23、#24 合入 `main`；决策 17：会话文件在第一条消息时才写（Python 一侧，PTY 恢复矩阵加 3 例）；决策 18（不做）与决策 10（做）的答复 | PR [#25][pr-25] |
| [§10.18][l10-18] | r15 | 决策 10 的存储层一项：删 `pi-shell` 的 `session::{storage, persistence}` 与只有它们在用的代码（编译器的 41 处告警），`session::title`，数字、门禁与验证 | PR [#26][pr-26] |
| [§10.19][l10-19] | r16 | 合入 #25 与 #26；删 `pi-shell` 里剩下的死代码（没人加载的文件、`session::worktree` 与 `session::helpers`、`[storage]`、10 个没点名的依赖）；数字、门禁、验证；D.8 的新候选 | PR [#27][pr-27] |
| [§10.20][l10-20] | r17 | 检查测试之间的竞争（只改测试与工具）：环境变量探针与压力脚本；19 个偶发失败的测试与 5 个不还原环境变量的测试；进程级 `static` 的 6 族与 `in_own_process`（57 个测试）；`cargo nextest` 的对照；`protoc` 重新构建的问题 | PR [#28][pr-28] |
| [§10.21][l10-21] | r18 | `release.yml` 里 `setup-protoc` 的令牌（#29）；`protoc` 的绝对路径，和 r17 一句话的更正（#30）；`TUI CI` 与基线执行器改用 `cargo nextest`：执行器选项、输出解析、先 SIGTERM 后 SIGKILL、文档测试、「跳过」的计数、CI 的数字（#31） | PR [#29][pr-29]、[#30][pr-30]、[#31][pr-31] |
| [§10.22][l10-22] | r19 | 用 `gh stack` 合入 #27–#31（`gh stack` 的安装、stack 的建立与原子合并、树与 PR 分支头相同的核对）；V0.6 的发布里程碑；开发暂停 | PR [#27][pr-27]–[#31][pr-31]、[v0.6.0][ms-v060] |
| [§10.23][l10-23] | r20 | MCP 的方向（可选扩展包，决策 19）：两个问题的原因；#34 空白对话再打开（决策 17 的更正）；#35 给扩展的挂钩；#36 `pi-mcp-py`；发布内容与演练；用 `gh stack` 合入 #33–#36（stack #37）与树的核对；`main` 上的 CI；扩展包下限的发现（决策 20）；用户对决策 20、21 的答复；没验证的 | PR [#33][pr-33]–[#36][pr-36] |
| [§10.24][l10-24] | r21 | V0.6 的条件 5（#39：版本号、扩展包的下限、`CHANGELOG.md` 的 `[0.6.0]`；对 r20 的更正：`pi-dynamic-workflows-py` 的 harness 下限）；合入 #38 与 #39 与树的核对；条件 1（`main` 上的 CI）、2（体积 406,911,320 B）、3（演练）；条件 4（与 v0.5.0 对照：做法、结果、`resume_matrix` 的三个差别、`mode_probe` 的探针竞争与 #40）；没验证的 | PR [#39][pr-39]、[#40][pr-40] |
| [§10.25][l10-25] | r22 | 打 tag `v0.6.0`（`5f73e07`）；`Release` 的运行（一次成功；各 job 的用时；Cargo 缓存；警告）；PyPI（七个包，14 个文件与 GitHub Release 逐个同 SHA-256；新项目 `pi-mcp-py`；索引缓存的延迟）；干净的 venv；`pi-mcp` 的 192 个测试对着从 PyPI 装的包；GitHub Release（16 个资产，正文）；两个二进制（Linux 与构建产物逐字节相同，要 glibc 2.39；macOS 只看了文件头）；PTY 脚本（第一次因复制的层次错了，两个没有起来）；合入 #40；留下的与没验证的 | PR [#40][pr-40]、[#41][pr-41] |
| [附录 A.4][logA4] | r5 | 入口审计的 r5 执行结果 | [#7][pr-7] |
| [附录 B][logB] | r2–r17 | 复现方法（依赖图、模块级闭包、各轮的测量与测试命令） | — |
| [附录 C][logC] | r10 | §7 的逐项注释（原文） | — |
| [附录 D][logD] | r10 | 计划头部的状态文字（原文） | — |
| [附录 E][logE] | r2 | §4、§5 的原文（源码事实基线与改动规模估计） | — |
| [修订记录][logRev] | r1–r17 | 逐轮的完整说明 | — |

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
- r12：V0.5 的发布里程碑（分支 `docs/v0.5-milestone`，只改文档）。按剥离的现状定范围：阶段 0、A、C 完成，阶段 1 完成一半，阶段 D 没有开始，V0.5 不等它们；写六项退出条件（CI、体积再量、发布构建演练、与 v0.4.0 对照、发布说明、打 tag）、带着发布的已知问题、不在 V0.5 的项；建 GitHub 里程碑 [v0.5.0][ms-v050]；新增待决策 16（发行构建的版本戳：`zypi --version` 与「是不是发行构建」共用 `GROK_VERSION`，不能靠在 `release.yml` 里设它来改版本号）；状态更新到 `main` 的 `4fc028a`。里程碑建好之后，用户选了四件事，都在别的 PR 或 CI 运行里做了，本 PR 只记录：并入 `[Unreleased]` 并补条目（PR [#15][pr-15]，条件 5）；给 `release.yml` 加手动演练（PR [#14][pr-14]，条件 3），并从分支跑了一次（[run 38223353755][run-dry-run]：Linux 与 macOS arm64 都构建成功，什么也没发布）；`release_baseline`（[run 38221952524][run-baseline]，420,767,096 B，条件 2）；推送本分支并开 PR。决策 16 选了 (a)。之后用户又答了三个问题：决策 16 现在做，另开 PR（[#17][pr-17]，叠在 #15 上：crate 版本改成 0.5.0，`agentInfo.version` 读已装包的版本，两个新测试文件）；macOS 二进制的条目写进 #15（`CHANGELOG.md`、`README.md`、`docs/TUI-AND-CODE-AGENT.md`，注明「CI 构建，没有人运行过」）；条件 4 不做，V0.5 不等它。续 3：用户让先把 PR 合入；#15、#17、#14 依次 squash 合入（`59545c1`、`d57fd9b`、`0efd400`），本分支改基到 `0efd400`。用户也修好了 WSL：在 `0efd400` 上跑了 Linux 的 `pytest`（2,539 通过 / 16 跳过 / 31 取消选择）、`zypi --version`（`zypi 0.5.0 (0efd40071ca0)`）和 `scripts/tui_pty/` 的脚本（`smoke.py` 22/23，其余符合预期；`smoke.py` 里失败的那一项检查过期了，不是 TUI 的退化）。续 4：这项过期的检查修好了（用户说不另开 PR，直接提交到 `main`）：`smoke.py` 23/23，加 `--sandbox workspace` 26/26；GitHub 里程碑的描述改到合入之后。续 5：用户答了决策 6、8、14、15：6 与 15 选 (a)（都还没做），8 与 14 待定；最后一个本地旧分支也删了。续 6：决策 6 的 (a) 做成了 PR（分支 `feat/export-via-acp-replay`，没有合入）：`zypi export <id>` 改问 agent，经 `session/load` 回放；18 个单元测试、一个端到端测试；`resume_matrix.py` 11 PASS / 2 GAP / 0 FAIL；调试构建下，2,000、5,000、20,000 条的会话要 2.5、5.7、63 s；发现三处没有修的问题（agent 读到条目 id 重复的文件会死循环；基准脚本在 4,096 条之后写出这样的文件；`isReplay` 在 agent 与 pager 里放的 `_meta` 层级不同）。续 7：决策 15 复核后从 (a) 改成 (b)，做成了 PR（分支 `feat/status-line-local-facts`，没有合入）：DESIGN §8.5 的五条里，(a) 有两条不过（没有标准对应物；线上类型要放在旧运行时的 crate 里），用户改选 (b)；`zypi` 用已有的数据（目录、会话 id、模型、会话名、版本，加两个钟）画状态行，agent 发来的状态仍然优先；`context` 与 `cost` 不造数；Python 与线上协议都没动；新增 16 个单元测试、重写 2 个、调整 1 个；`status_line_probe.py` 4/4（r11 时 4 个都画不出）；整套运行时有一个与这次无关的失败，原因是本机真实 home 里一个没跑完的运行留下的目录（见 §10.15 续 7）。续 8：用户让把 #18、#19 合入 `main`、放进 V0.5，不再重复本地测试，再打 tag 发布（这条改了先前的两条约定：「PR 只开不合」与「不自己打 tag」）。#18 先 squash 合入（`ec7d455`）；#19 与它在三个文档文件上冲突，在 #19 的分支里合入 `main` 解决（`1fa18cf`，代码没有冲突）。`tui/` 又变了，所以条件 2、3 在 `1fa18cf` 上重做（[release_baseline](https://github.com/zy1233/pi-python/actions/runs/38353042677)、[发布演练](https://github.com/zy1233/pi-python/actions/runs/38353038777)）。`CHANGELOG.md` 把两条并进 `[0.5.0]`，日期改成 2026-10-13，删去 `zypi export` 的已知问题，改窄 `[ui.status_line]` 的。这些文档改动在两个只改文档的 PR 里合入（[#20][pr-20]；[#21][pr-21] 又改了状态行探针的条目）。`v0.5.0` 打在 `be3f8a8`，`Release` 发布了 PyPI 的六个包与 GitHub Release（macOS 的构建因 `setup-protoc` 被 GitHub 的 API 限流，失败两次，第 3 次通过）；发布的 Linux 二进制下载后跑过 PTY 脚本。没做：本地的 pytest 与 cargo 测试（用户说不用）；macOS 的二进制没有人运行过。见 [§10.15][l10-15]。
- r13：ADR1 落地（2026-10-16）。用户答复决策 2、4、5、7，都按倾向（2：去掉恢复时沿用保存的沙箱 profile；4：`--continue` 取最近活动；5：未知 id 在进 TUI 之前报错、退出 1；7：保持 (a)，(b)(c) 另议）。做成两个 PR，没有合入：PR 2（[#23][pr-23]，分支 `feat/adr1-resolve-startup`，基 `main`）加 `resolve_startup`：agent 起来之后经 `session/list`、`session/load` 解析 `--continue`、`--resume`、`--session-id`，一个共用的翻页器，30 s 的上限，`resume_matrix.py --strict` 13/13；PR 3（[#24][pr-24]，分支 `refactor/adr1-drop-session-disk-reads`，叠在 PR 2 上）删 pager 里读会话文件、钉沙箱、读 `plan.md` 的代码（Rust 1,829 行），加门禁 `gates.session_disk_reads`（`main` 上 18 处命中，现在 0 处），并更新文档与基线。新增待决策 17（空会话）与 18（翻页的代价：5,000 个会话时 `--session-id` 要 28–34 s）。没做：删 `pi-shell` 里的旧存储层（决策 10，用户没有答复）。见 [§10.16][l10-16]。
- r14：空会话（2026-10-19）。用户在 2026-10-18 答复四个问题（合并、决策 18、决策 17、决策 10）。#23、#24 合入 `main`（`7e0ca5a`、`0476ea4`；`CI` 与 `TUI CI` 都通过）。决策 18 不做（1,000 个会话以内够用）。决策 17 做成 PR [#25][pr-25]（分支 `fix/write-session-at-first-message`，基 `main`，没有合入）：agent 在第一条用户消息时才写会话文件（`DeferredJsonlSessionStorage`），没发过消息的会话没有文件、不在列表里，客户端指定的 id 由 agent 记到会话关闭；PTY 恢复矩阵 13 → 16 例，`smoke.py` 不再按 ↓，`python_agent_e2e.rs` 的用例先发一条消息再找会话。决策 10 答复了「做」，还没做（r15 做了）。见 [§10.17][l10-17]。
- r15：删 `pi-shell` 的旧存储层（2026-10-19）。用户在 2026-10-18 答复了决策 10 的存储层一项：做。做成 PR [#26][pr-26]（分支 `refactor/delete-pi-shell-session-store`，叠在 [#25][pr-25] 上，没有合入）：删 `session::storage`（14 个文件）与 `session::persistence`（12 个文件，11 个是测试），再按编译器的 41 条 `dead_code` 告警一轮一轮删只有它们在用的代码（会话信号、plan 模式快照、公告状态、目标与 workflow 的状态类型、回放用的线上标签、加载时的图片检查、`fork_filter_chat`，和只测它们的测试文件）；pager 有意用的两个清洗标题的函数搬到新的 `session::title`，加 8 个单元测试。`tui/`：53 个文件，+190 / −14,166 行；`pi-shell` 的非测试代码 40,985 → 31,653 行，测试 1,056 → 864，清单里 `reference_passed` 的和 8,706 → 8,514。生产代码没有行为变化；分支上另有一个测试的修（`332ba75`）：`consent` 测试的串行键改成默认键，因为它与 `session::worktree` 的测试能同时运行，使后者在整个 `--lib` 运行里失败（这个竞争在 `c74cc18` 里就有）。门禁与 PTY 脚本都过（`check`：告警 0，依赖图 980；`resume_matrix.py --strict` 16/16）；CI 通过（`TUI CI` 8,514 通过 / 0 失败 / 21 忽略）。没做：决策 10 的另两项（`mcp.rs`、`recent_dirs.rs`）；未用的依赖；`session::helpers` 与 `session::worktree`（现在调用图报死）；`leader`、`x.ai` 上限降到 243 与 297。见 [§10.18][l10-18]。
- r16：合入 #25 与 #26，删 `pi-shell` 里剩下的死代码（2026-10-19）。用户从「接下来做哪些」里选了合入 #25 与 #26、删调用图报死的单元、一个小清理、检查同类竞争；没选决策 10 的另两项。#25（`2b0baf3`）与 #26（`7d82c27`）合入 `main`；`CI` 与 `TUI CI` 在两个提交上都通过。PR [#27][pr-27]（分支 `refactor/tui-session-store-leftovers`，基是 `7d82c27`，没有合入）：删 15 个没人加载的文件、`session::worktree`、`session::helpers`、`util::config::worktree`、`[storage]` 设置（旧配置照常加载，这一段被报成不认识的键，1 个新测试）、10 个源码没点名的依赖；`leader`、`x.ai` 的上限降到 243 与 297，依赖图上限 980 → 970，`pi-shell` 的测试 864 → 838。`tui/`：28 个文件，+13 / −1,644 行；生产代码没有行为变化。门禁、整套测试（8,487 通过 / 1 个已知失败 / 21 忽略）、PTY 脚本与 Python 的两遍 `pytest` 都过。PR 的 `CI` 与 `TUI CI` 在 `6bd7db5` 上通过（`TUI CI` 8,488 通过 / 0 失败 / 21 忽略）。分支在本地验证之后、推送之前用 `git cherry-pick` 重建过一次（第一个提交的信息里一个行数写错），每个提交的树没有变。没做：同类竞争的检查（下一个只改测试的 PR）；D.8 的新候选（7 个死单元、`pi-workflow`、`Config.storage_mode`、没人加载的候选文件）；决策 10 的另两项。见 [§10.19][l10-19]。
- r17：检查测试之间的竞争（2026-10-19）。用户选的四项里剩下的一项（`race_audit`）。做成 PR [#28][pr-28]（分支 `test/tui-env-race-audit`，叠在 [#27][pr-27] 的分支上，没有合入），只改测试、脚本与文档：加了环境变量探针与压力脚本（`scripts/tui_envspy/`）；压力运行找到 19 个偶发失败的测试（3 个是测试自己的缺陷，16 个由进程级的 `static` 引起），另有 5 个测试改了环境变量却不还原；缺陷在测试里修了，改 `static` 的 57 个测试各在自己的进程里跑（`in_own_process`）。`pi-pager` 的 lib（32 线程、随机顺序）6/32 遍有失败 → 0/60；清单里 `reference_passed` 的和 8,488 → 8,490。没做：`TUI CI` 改用 `cargo nextest`、`find_protoc()` 让 `pi-tools-api` 一遍遍重新构建（都等用户决定）。最后的提交上没有在本地跑测试（磁盘满了），CI 在 `ea9609f` 上通过（[`CI`][ci-run-ea9609f]、[`TUI CI`][tui-ci-run-ea9609f]：8,490 通过 / 0 失败 / 21 忽略）。见 [§10.20][l10-20]。
- r18：三个 CI 与构建的改动（2026-10-20）。用户选了 `release_token`、`protoc_abs`、`nextest_ci`。三个 PR 一个叠在一个上，都没有合入：[#29][pr-29]（`release.yml` 的 `setup-protoc` 加 `repo-token`）、[#30][pr-30]（`find_protoc()` 返回绝对路径）、[#31][pr-31]（`TUI CI` 与基线执行器改用 `cargo nextest`）。#31 的 `TUI CI` 通过：8,490 通过 / 0 失败 / 22 跳过（整棵树第一次在 nextest 下跑），测试自己的时间约 130 s（`cargo test` 约 65 s）；#29 在分支上手动跑了一次 `Release` 的演练，通过。更正 r17：CI `cargo test` job 里 `pi-tools-api` 的 7 次编译不是 `protoc` 路径的问题。见 [§10.21][l10-21]。
- r19：用 `gh stack` 合入 #27–#31，设 V0.6 的发布里程碑，开发暂停（2026-10-20，分支 `docs/v0.6-milestone`，只改文档）。用户让用 `gh stack` 合入当前的 5 个 PR、按现状设 V0.6 的里程碑、开发先停 1–2 天。`gh stack link` 建 stack #32，`gh stack merge 32 --yes --squash` 一次合入；`main` 在 `b33017a`；5 个 squash 提交的树都等于各自 PR 分支头的树。`CI` 与 `TUI CI` 在 `b33017a` 上通过（`TUI CI`：8,490 通过 / 0 失败 / 22 跳过）。里程碑：[v0.6.0][ms-v060]；计划新增[发布里程碑 V0.6](#发布里程碑-v06)。没验证：六项退出条件都没有满足；`gh stack` 只用过一次；暂停之后的顺序（先条件 5，再 1–3）是建议。见 [§10.22][l10-22]。
- r20：MCP 的方向（可选扩展包）、空白对话的修复、发布内容、合入 #33–#36（2026-10-25，分支 `docs/plan-r20-mcp-extension`，只改文档）。用户在 2026-10-23 报告 Zed 里的两个问题：每个新对话开头的 MCP 提示；退出 Zed 后没发过消息的对话打不开。原因：agent 从来没有 MCP 客户端（Phase 6 的决定），r9 只是把「静默丢弃」改成「明说」；决策 17 让没发过消息的会话没有文件，Zed 重启后用 `session/load` 要这个对话，得到 `resource_not_found`。用户答复：MCP 做成可选扩展包（core 与 CLI 仍不内置）；空白对话的修复现在发 PR。做成三个叠在一起的 PR：[#34][pr-34]（`session/load` 与 `session/resume` 对没有文件的规范 UUID 开一个空会话；17 + 2 个新测试）、[#35][pr-35]（给扩展的挂钩：客户端传来的 server、`pi.notify`、`pi.add_turn_barrier`；MCP 提示改写；再次打开同一个会话时关旧的 harness；103 个新测试）、[#36][pr-36]（新包 `pi-mcp-py` 0.1.0：stdio、Streamable HTTP、SSE；工具名 `mcp__<server>__<tool>`；`/mcp`；192 个测试，28 处单行变异；真的 context7 server 与真模型各跑一次）。用户在 2026-10-25 让先补发布内容，再合入 #33–#36：`release.yml` 加两处（`test` job 装这个包，`build` job 加 `Build pi-mcp`），README、包的 README、CHANGELOG、设计文档 §9 同步；`actionlint`、本地构建与 `twine check --strict` 通过；`Release` 的演练通过（[run 40162795623][run-dry-run-eda4aac]：发布跳过，`dist` 14 个文件）。合入：#33 `gh pr merge --squash`（`ca2ab85`）；#34–#36 用 `gh stack link 34 35 36`（stack #37）与 `gh stack merge 37 --yes --squash`（`fa4887b`、`34c11d9`、`e0f2a36`）；每个提交的树等于 PR 分支头的树加上 #33 的两个文档文件。`CI` 在 `e0f2a36` 上通过（[run][ci-run-e0f2a36]）；`TUI CI` 也通过（[run][tui-ci-run-e0f2a36]：8,490 通过 / 0 失败 / 22 跳过）。本 PR：新增决策 19（MCP 的方向，已定）、20（扩展包的版本号与核心下限）、21（设计文档的 11 个默认值）；用户在 2026-10-25 对 20、21 答「按你的倾向来」，所以都已定（20：四个下限抬到 `>=0.6.0`，三个已发布的扩展包抬到 0.3.0，`pi-mcp-py` 仍是 0.1.0；21：全按默认）；决策 17 加更正；V0.6 的范围、条件 1、3、5、6 与已知问题改写；更正 r19 的「三个扩展包不动」；`docs/TUI-AND-CODE-AGENT.md` 里「MCP servers are not connected」一段改成「需要可选包 `pi-mcp-py`」（#35、#36 漏改了它）。没验证：新项目的第一次上传需要的 PyPI 令牌；「0.6.0 起」是假设；没有在真的 Zed 里看过；OAuth 等没做。见 [§10.23][l10-23]。
- r21：V0.6 的版本号、合入 #38 与 #39、条件 1–4 的结果（2026-10-25，分支 `docs/plan-r21-release-checks`，只改文档）。用户说「开始下一步」；我做了条件 5，成 PR [#39][pr-39]；用户答复：现在合入 #39 与 #38，条件 4 做。① 条件 5（#39）：核心、harness、cli 升到 0.6.0；三个已发布的扩展包升到 0.3.0，`pi-mcp-py` 仍是 0.1.0，四个扩展包的核心下限抬到 `>=0.6.0`；`pi-pager-bin`、`pi-version`、`tui/Cargo.lock` 升到 0.6.0；`CHANGELOG.md` 的 `[0.6.0]`、空的 `[Unreleased]`、9 条已知问题。**更正** r20：`pi-dynamic-workflows-py` 还有一个 harness 下限，也要抬。② 合入 #38（`60864af`）与 #39（`5f73e07`），树的核对。③ 条件 1–3 在 `5f73e07` 上通过：[`CI`][ci-run-5f73e07]、[`TUI CI`][tui-ci-run-5f73e07]（`zypi` 406,911,320 B，构建 895 s）、`Release` 的演练 [run][run-dry-run-5f73e07]（macOS arm64 第一次就成功）。④ 条件 4：五个脚本对照 v0.5.0；`resume_matrix` 13 通过 / 3 失败 → 16 通过，三个差别是预计的，`CHANGELOG.md` 里有一条；`mode_probe` 的一行不同是探针的竞争，修复是 PR [#40][pr-40]。⑤ 计划里改的：状态、当前状态、V0.6 的范围、条件 1–6 的结果与现状、下一步、决策 20 的状态与对它的更正、§10 索引、本条；执行记录加 [§10.24][l10-24]；`docs/baselines/tui.md` 的 release 数字；GitHub 里程碑 [v0.6.0][ms-v060] 的描述（它还是 r19 的内容）。没有打 tag，没有发布。没验证：条件 4 只在 Linux 做；新旧构建缺标签的概率不同，没有查；`PYPI_API_TOKEN`；日期占位。见 [§10.24][l10-24]。
- r22：打 tag `v0.6.0`、发布、发布后的核对（2026-10-25，分支 `docs/plan-r21-release-checks`，只改文档；与 r21 在同一个 PR [#41][pr-41] 里）。用户答复：tag 打在 `5f73e07`，#40 与 #41 在 tag 之后合入；由我来打并核对。① tag `v0.6.0`（带注释，tag 对象 `6b92a0d`）；[`Release` 的运行][run-release-v060]一次全部成功。② PyPI 七个包；`pi-mcp-py` 是新项目，第一次上传成功；14 个文件的 SHA-256 与 GitHub Release 的资产逐个相同。③ [GitHub Release][rel-v060]：16 个资产；正文等于 `CHANGELOG.md` 的 `[0.6.0]`。④ 核对：干净 venv 里 `uv pip check`；`pi-mcp` 的 192 个测试对着 PyPI 的包；PTY 脚本对着发布的 `zypi`。⑤ 合入 #40（`75f83ae`）；GitHub 里程碑 [v0.6.0][ms-v060] 关闭。⑥ 计划里改的：状态、当前状态、V0.6 的状态与条件 1、3、4、6 的结果、已知问题、下一步、决策 20、「还没有验证的」、§10 索引、本条；执行记录加 [§10.25][l10-25]；`docs/baselines/tui.md` 的 release 数字。没发现缺陷。留下的：`release.yml` 的 Linux 构建用 `ubuntu-latest`；glibc 的下限没写在文档里；macOS 的二进制没人运行过。见 [§10.25][l10-25]。

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
[pr-38]: https://github.com/zy1233/pi-python/pull/38
[pr-39]: https://github.com/zy1233/pi-python/pull/39
[pr-40]: https://github.com/zy1233/pi-python/pull/40
[pr-41]: https://github.com/zy1233/pi-python/pull/41
[ci-run-7b27775]: https://github.com/zy1233/pi-python/actions/runs/39244339779
[tui-ci-run-7b27775]: https://github.com/zy1233/pi-python/actions/runs/39244339476
[ci-run-ea9609f]: https://github.com/zy1233/pi-python/actions/runs/39240903417
[tui-ci-run-ea9609f]: https://github.com/zy1233/pi-python/actions/runs/39240903399
[ci-run-1a49eef]: https://github.com/zy1233/pi-python/actions/runs/39359879347
[tui-ci-run-1a49eef]: https://github.com/zy1233/pi-python/actions/runs/39359879537
[ci-run-b33017a]: https://github.com/zy1233/pi-python/actions/runs/39379724651
[tui-ci-run-b33017a]: https://github.com/zy1233/pi-python/actions/runs/39379724664
[run-dry-run-81171da]: https://github.com/zy1233/pi-python/actions/runs/39360804691
[run-dry-run-eda4aac]: https://github.com/zy1233/pi-python/actions/runs/40162795623
[ci-run-e0f2a36]: https://github.com/zy1233/pi-python/actions/runs/40163356860
[tui-ci-run-e0f2a36]: https://github.com/zy1233/pi-python/actions/runs/40163356857
[ci-run-5f73e07]: https://github.com/zy1233/pi-python/actions/runs/40172668652
[tui-ci-run-5f73e07]: https://github.com/zy1233/pi-python/actions/runs/40172721728
[run-dry-run-5f73e07]: https://github.com/zy1233/pi-python/actions/runs/40172723794
[run-release-v060]: https://github.com/zy1233/pi-python/actions/runs/40180264121
[rel-v060]: https://github.com/zy1233/pi-python/releases/tag/v0.6.0
[ci-run-7d82c27]: https://github.com/zy1233/pi-python/actions/runs/39187845874
[tui-ci-run-7d82c27]: https://github.com/zy1233/pi-python/actions/runs/39187845858
[ci-run-6bd7db5]: https://github.com/zy1233/pi-python/actions/runs/39193752450
[tui-ci-run-6bd7db5]: https://github.com/zy1233/pi-python/actions/runs/39193752451
[ci-run-0476ea4]: https://github.com/zy1233/pi-python/actions/runs/39143111042
[tui-ci-run-0476ea4]: https://github.com/zy1233/pi-python/actions/runs/39143111087
[ci-run-r5]: https://github.com/zy1233/pi-python/actions/runs/37379203134
[ci-run-4fc028a]: https://github.com/zy1233/pi-python/actions/runs/38196042958
[tui-ci-run-4fc028a]: https://github.com/zy1233/pi-python/actions/runs/38196042998
[ci-run-0efd400]: https://github.com/zy1233/pi-python/actions/runs/38233821853
[tui-ci-run-d57fd9b]: https://github.com/zy1233/pi-python/actions/runs/38233778549
[ms-v050]: https://github.com/zy1233/pi-python/milestone/1
[ms-v060]: https://github.com/zy1233/pi-python/milestone/2
[run-baseline]: https://github.com/zy1233/pi-python/actions/runs/38221952524
[run-dry-run]: https://github.com/zy1233/pi-python/actions/runs/38223353755
