# TUI（Rust）基线与门禁

对应 [`PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md) 阶段 0 的 0.1（Rust CI job）、0.2（基线报告）、0.4（deny-list）、0.5（调用图），以及阶段 C 的残留词与许可证声明。权威环境是 CI 的 Linux runner（ADR6：`ubuntu-24.04` + `tui/rust-toolchain.toml` 钉的 1.94.0）。

## 当前基线（r11）

- **测试**：10 个 suite 合计 8,665 通过 / 0 失败 / 22 忽略。出处：分支 `chore/phase-c-closeout` 的 `4fadcf3`，WSL2 上基线执行器的一次本地运行（22 m 22 s，清环境，`PI_E2E_PYTHON` 与 `PI_E2E_REQUIRED=1`）；**还没有 CI 运行**。比 r10 的 8,683 少 18 个，都在 `pi-pager`（5,626 → 5,608：C.2 删了教程叠层与上游用户指南的用例，加了两个守卫用例）。各 suite 见 §3.2。r10 的 CI 对账（PR [#12](https://github.com/zy1233/pi-python/pull/12) 的 `bd44289`，`TUI CI` [run 38074038191](https://github.com/zy1233/pi-python/actions/runs/38074038191)）在[基线历史][hist-tests]。
- **门禁**：依赖图 980 个包，消费者构建 0 条告警，`--workspace --tests` 0 个错误，残留词 `grok agent` 0 / `leader` 245 / `x.ai` 304（§2、§4）。出处：同一分支的 `24907ef`，`check` 的一次本地运行，5 m 24 s。`4fadcf3` 之后 `tui/` 只改了文档注释、一个标签和两份 README，测试没有重跑。
- **耗时**（CI，r10 的 `bd44289`）：`cargo test` job 29 m 13 s，`Gates + cargo check` 5 m 46 s。r11 的分支还没有 CI 运行，上面两条本地耗时与它们不可比。
- **release**：`zypi` 420,667,200 B（比拆除前小 10.6 %），冷缓存构建 1,194 s（比 1,647 s 快 27.5 %），`--version` 中位数 22.9 ms。出处：r12 续 8，`1fa18cf`（#18 与 #19 合并之后的树，也是 V0.5 的树），手动触发的 `TUI CI`（带 `release_baseline`）[run 38353042677](https://github.com/zy1233/pi-python/actions/runs/38353042677)。r12 开头（`4fc028a`）是 420,767,096 B / 1,195 s / 23.4 ms，r5（`ac1d3ff`）是 422,034,120 B / 1,155 s / 23.1 ms。该 job 只手动触发，三次之间没有别的测量。
- **历史**：每一轮数字怎么变的、CI 对账的 run 号、已经做完的事，在[基线历史][hist]。

## 1. 文件与命令

| 文件 | 作用 |
|---|---|
| `scripts/tui_baseline.toml` | 清单：工具链、deny-list、依赖图上限、测试 suite、release 参照值。改数字只动这里 |
| `scripts/tui_baseline.py` | 执行器：`gates` / `check` / `test` / `release`，输出 `tui-baseline/<命令>.{md,json}`，在 CI 里同时写入 job summary |
| `scripts/tests/test_tui_baseline.py` | 执行器的单测（解析、棘轮判定、清单与 workspace / 工作流的一致性），随 `pytest` 跑 |
| `scripts/tui_callgraph.py` | 模块级调用图（0.5，r11）：`crates`（crate 的依赖闭包）、`units`（一个或几个 crate 里，哪些模块还有活代码在引用）、`keep`（support crate 的保留清单，写成 [`tui-support.md`](tui-support.md)）。只读源码文本，不编译；方法与局限在脚本的 docstring 里 |
| `scripts/tests/test_tui_callgraph.py` | 它的单测（39 个；对脚本做 17 处改动，每一处都让测试失败） |
| [`docs/baselines/tui-support.md`](tui-support.md) | `tui_callgraph.py keep` 的输出，**生成的，不手改**；取代原来「举例」式的保留清单 |
| `scripts/tui_notices.py` | `tui/THIRD-PARTY-NOTICES` 与 `Cargo.lock` 对账（C.3，r11）：`check` 不一致就退出 1，`update --write` 来改；新条目标 `needs review`，要人读过。还没有接进 CI（要 cargo）；依赖变了之后手动跑一次 |
| `scripts/tests/test_tui_notices.py` | 它的单测（62 个） |
| `.github/workflows/tui-ci.yml` | 三个 job：`check`、`test`、`release-baseline`（仅 `workflow_dispatch` 且勾选 `release_baseline`） |

本机复现（需要 `protoc`；macOS 已验证，Linux 由 CI 验证，Windows 没跑过——脚本只用显式 UTF-8 读写，并用 `PYTHONUTF8=0 LC_ALL=C` 模拟过 Windows 的默认编码）：

```bash
python scripts/tui_baseline.py gates             # 5 秒，不编译
python scripts/tui_baseline.py check             # 门禁 + 消费者构建 + --workspace --tests
python scripts/tui_baseline.py test              # 全部 suite；--suite pi-pager 只跑一个
python scripts/tui_baseline.py release           # release-dist 构建 + 体积 + --version 延迟（约 30 分钟）
```

**棘轮规则**：上限（依赖图节点、告警）只降不升，下限（通过数）只升不降；清单里没写的键 = 只报告。带 `PROVISIONAL` 的值是 macOS 或跨平台解析出来的，第一次 Linux 运行后替换。

## 2. 门禁

| 门禁 | 判定 | 阻塞 |
|---|---|---|
| 工具链钉 | 清单 `channel` = `tui/rust-toolchain.toml` | 是 |
| deny crate | `pi-sampler` 不在 `tui/Cargo.lock`，`cargo tree -p pi-pager-bin -i pi-sampler` 报 `did not match any packages` | 是 |
| deny word | `MvpAgent`、`acp_session_impl`、`SamplerActor` 在 `tui/crates` 下整词零命中 | 是 |
| 残留词上限 | `tui/crates` 下含 `grok agent`、`leader`、`x.ai` 的行数（忽略大小写，词前不紧跟字母或数字）各不超过清单 `[gates.residue]` 的上限：0 / 245 / 304，见 §4。`pytest` 里也跑一遍（`test_the_residue_ceilings_hold_in_this_tree`） | 是 |
| 依赖图上限 | `cargo tree -p pi-pager-bin --target x86_64-unknown-linux-gnu` 的唯一包数 ≤ 980（Linux 实测 980） | 是 |
| 消费者构建 | `cargo check --locked -p pi-pager-bin -p pi-pager-minimal -p pi-update` | 是 |
| 全工作区类型检查 | `cargo check --workspace --tests --keep-going`（排除 `pi-fast-worktree`，其 lib 测试在 HEAD 上就编译不过） | 是，`enforce_workspace_tests = true` |
| 告警数 | 消费者构建的 `dead_code` 等告警，按 lint / crate 汇总 | 是，`max_warnings = 0` |
| 测试 | 10 个 suite，见 §3.2 | 是，`[test] enforce = true`（意外失败、超时、构建错误都会失败；每个 suite 有 `min_passed` 下限） |
| release 体积 | `release-dist` 的 `zypi` 不大于 v0.4.0 | 是，仅手动 job |

## 3. 基线数据

### 3.1 构建与依赖图

「拆除前」= 发布版 v0.4.0（`tui/` 与提交 `07a3574` 完全相同）。「r3」= 提交 `9add266`（拆除 Rust runtime）。「r11」= 分支 `chore/phase-c-closeout` 的 `24907ef`（WSL2 上本地；依赖图与告警来自 `check`，行数与包数是在工作树上数的，没有测耗时）。「r5」= 入口审计与阶段 A 之后：行数、包数与 macOS 一栏测于 `ac1d3ff`（依赖图只取决于 `Cargo.lock`，`fe1fc65` 之后它没变），Linux 一栏来自 `ac1d3ff` 的两次 CI 运行（PR run 37389447358；手动 run 37389455793，带 release 基线）。

| 指标 | 拆除前 | r3（macOS） | r5（macOS） | r5（Linux CI） | r11（WSL2，本地） |
|---|---|---|---|---|---|
| `tui/crates` 下 `.rs` 行数 | 1,620,612 | 1,254,693 | 1,019,041 | 同左（同一份源码） | 1,021,067（1,755 个文件） |
| `Cargo.lock` 包数 | 1,311 | 1,285 | 1,257 | 同左 | 1,257 |
| 依赖图唯一包数 `x86_64-unknown-linux-gnu` | 1,026 | 995 | 980 | 980 | 980 |
| `aarch64-unknown-linux-gnu` | 1,025 | 994 | 979 | — | — |
| `aarch64-apple-darwin` | 990 | 957 | 943 | — | — |
| `x86_64-apple-darwin` | 991 | 958 | 944 | — | — |
| `x86_64-pc-windows-msvc` | 972 | 939 | 926 | — | — |
| 消费者构建告警 | 未测 | 20（全是 `pi-shell` 的 `dead_code`） | 0 | 0 | 0 |
| `cargo check` 消费者构建耗时 | 未测 | 94 s（本机，缓存状态不受控，只作参考） | — | 204 s（runner；缓存状态不受控，第一次冷缓存运行是 272 s） | — |
| `cargo check --workspace --tests` 耗时 | 未测 | 162 s（同上），0 个错误 | — | 164 s，0 个错误（第一次冷缓存运行是 229 s） | — |
| `release-dist` 的 `zypi`（Linux x86_64） | 470,461,776 B（448.7 MiB） | — | — | **422,034,120 B（402.5 MiB，−10.3 %）** | — |
| `release-dist` 冷缓存构建耗时 | 27 m 27 s（`ubuntu-24.04`，v0.4.0 的发布 job） | — | — | **19 m 15 s（1,155 s，−29.9 %）** | — |
| `zypi --version` 延迟（5 次中位数） | 未测 | — | — | 23.1 ms | — |

r12（Linux CI，`4fc028a`，[run 38221952524](https://github.com/zy1233/pi-python/actions/runs/38221952524)）：`release-dist` 的 `zypi` 420,767,096 B（401.3 MiB，比拆除前小 10.6 %，比 r5 小 1,267,024 B）；冷缓存构建 1,195 s（19 m 55 s，比 1,647 s 快 27.4 %，比 r5 慢 40 s）；`zypi --version` 中位数 23.4 ms。同一次运行的 `check` 与 `test` 与 `4fc028a` 的 PR 运行一致：依赖图 980、0 条告警、8,665 通过 / 0 失败 / 22 忽略。

r12 续 8（Linux CI，`1fa18cf`，[run 38353042677](https://github.com/zy1233/pi-python/actions/runs/38353042677)）：`release-dist` 的 `zypi` 420,667,200 B（401.2 MiB，比拆除前小 10.6 %，比 r12 开头的 420,767,096 B 小 99,896 B；这一段里 #18 把 `zypi export` 从读旧存储改成问 agent，#19 加了状态行的本地数据，净效果是更小，原因没有查）；冷缓存构建 1,194 s（19 m 54 s，比 1,647 s 快 27.5 %）；`zypi --version` 中位数 22.9 ms。同一次运行的 `check` 与 `test`：依赖图 980、0 条告警、8,700 通过 / 0 失败 / 22 忽略（清单里 `reference_passed` 的和仍是 8,665，没改；多出的 35 个测试来自 #18 的 19 个与 #19 的 16 个）。

macOS 环境：Apple Silicon，Homebrew `cargo` / `rustc` 1.96.1（**不是**钉的 1.94.0）。依赖图节点数只取决于 `Cargo.lock` 与目标三元组，与工具链版本无关；告警数与耗时会随工具链漂移，所以只当参考。

### 3.2 测试

**怎么跑**：`env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor`（沙箱默认的 `NO_COLOR=1`、`TERM=dumb` 会让约 10 个颜色 / 光标断言失败）；`PI_HOME` 等变量由清单的 `unset_env` 清掉（`PI_HOME` 的优先级高于配置隔离测试自己设的 `GROK_HOME`，不清掉会让 `test_config_update_isolation` 失败）。**在 WSL 里跑**：克隆放在 ext4 上，不要放在 `/mnt/<盘>`（9p，权限位与时间戳的行为不是 Linux 的）；登录脚本常会导出 `PI_HOME`（指向 Windows 一侧的配置目录），绕过执行器直接跑 `cargo test` 或测试二进制会读写那个目录——要么走执行器，要么先 `unset PI_HOME GROK_HOME` 并把 `HOME` 指到一次性目录。

| suite（`cargo test -p …`） | 目标 | 通过 | 忽略 | `min_passed` | 说明 |
|---|---|---|---|---|---|
| `pi-shell` | `--lib` + 2 个集成 | 1,056 | 1 | 1,000 |  |
| `pi-pager` | `--lib` + 4 个集成 | 5,608 | 13 | 5,300 | 含 `python_agent_e2e`：pager 的 ACP client 对着真的 Python agent（要 `PI_E2E_PYTHON`） |
| `pi-pager-render` | `--lib` | 1,092 | 2 | 1,040 | r8 起在清单里；另有 1 个 `known_failures`（见下） |
| `pi-pager-minimal` | `--lib` | 79 | 0 | 75 | r8 起在清单里 |
| `pi-pager-bin` | `--bin zypi` | 21 | 0 | 21 | 含 `-p` 守卫用例 |
| `pi-acp-lib` | `--lib` | 21 | 0 | 21 | 跳过 1 个会永久挂起的用例（见下） |
| `pi-http` | `--lib` | 13 | 0 | 13 |  |
| `pi-telemetry` | `--lib` | 239 | 0 | 230 |  |
| `pi-file-utils` | `--lib` | 216 | 6 | 210 |  |
| `pi-sampling-types` | `--lib` | 320 | 0 | 310 |  |
| **合计** | | **8,665** | **22** | | |

「通过」是清单里的 `reference_passed`，「`min_passed`」是下限（棘轮规则见 §1）。每一轮数字的变化（r8、r9、r10 的 CI 对账，含 run 号；r11 的 −18）在[基线历史][hist-tests]。

**清单里的已知失败 / 跳过，及原因**

| 项 | 处理 | 原因 |
|---|---|---|
| `terminal::tmux_probe::tests::successful_near_deadline_exit_still_returns_captured_output`（`pi-pager-render`） | `known_failures` | 在 1.5 s 的预算里用 shell 脚本烧 1.2 s，只给 `sh` 与 `perl` 的启动留 0.3 s，机器一卡就失败。WSL2 上失败过两次，都是测试二进制刚链接完的第一次运行（逐 crate 那一遍；清单的第一次完整运行——整个 crate 用了 8.6 s，平时是 1.3 s），之后重复 94 次（空闲与 12 个忙循环占满 CPU 各一半）都没复现，所以猜是刚链接完磁盘写回拖慢了进程启动，没有证实。容忍而不是跳过：用例照跑，报告里点它的名，天天失败的话看得出来，但不阻塞 |
| `vendor_x_ai_ext_is_dropped_without_sending`（`pi-acp-lib`） | `skip` | **永久挂起**：`acp::ExtRequest` 序列化时丢了 `method`，丢弃逻辑不触发（计划 §10.4） |
| `pi-pager-bin/tests/update_never_blocked_by_config.rs` | 未选入 | 按 `CARGO_BIN_EXE_pi-pager` 找二进制，而二进制叫 `zypi`（fork 改名遗留） |

**去掉的 `skip`**：`doctor_cmd::`（`pi-pager`，12 个）。它原先探测音频输入设备，在 macOS 上卡在 CoreAudio（疑似等麦克风授权），所以清单里一直跳过。r8 去掉了这个 `skip`；一个用例在没有录音程序的 runner 上失败，已修。`skip` 不用放回清单。经过与复现方法在[基线历史][hist-skip]。

**没有选入 suite 的 crate**：r8 在 Linux（WSL2，计划 §10.10）上逐个跑过它们的 `--lib --tests`：74 个 crate，68 个全过（含 `pi-sandbox`、`pi-tools`、`pi-pager-minimal`，其中 5 个没有测试），共 11,807 通过 / 13 失败 / 23 忽略。有失败的都与 runtime 拆除无关：`pi-agent`（1 项：加密模板字节过期）、`pi-shell-base`（2 项：按进程名 `grok` 判断，函数没有调用者）、`pi-workspace`（`session::git::*` 7 项：`xai-org` 改名遗留与 OID 断言）、`pi-hooks`（1 项：`test_hook_child_cannot_open_dev_tty` 假定 `sh` 的 `exec` 重定向失败后会接着往下走，dash 不会；它只在有控制终端时才跑，CI 上会自己跳过）、`pi-fast-worktree`（lib 测试编译不过）；另有两个偶发（`pi-pager-render` 的 tmux 探测、`pi-workspace` 的 `hub_auth::proactive`），之后各重复 60 次都没复现。

- 已加进清单（r8，用户选了做）：`pi-pager-render`（整个 crate 1,092 通过 / 2 忽略，带上面那个 `known_failures`）与 `pi-pager-minimal`（79 通过）。后者有个用例缺 `test_lock()`，默认线程数下 30 次失败 25 次，r8 已补上，补后 40/40。第一次 CI 运行（PR #9）在 runner 上证实了这两个 suite 的数：`pi-pager-render` 1,092 通过 / 2 忽略（`known_failures` 里的那个用例在 runner 上没有失败），`pi-pager-minimal` 79 通过。
- `pi-sandbox` 的端到端用例（`deny_paths_e2e`、`read_write_trailing_glob_e2e`）在内核不支持 Landlock 时直接 `return`，「通过」是空转；要在带 Landlock 的 runner（内核 ≥ 5.13）上并设 `SANDBOX_E2E_REQUIRE_ENFORCEMENT=1` 才算数。这一项还没做，仍会改 CI 门禁（要换 runner 或加一个 job）。

### 3.3 PTY 载体

`scripts/tui_pty/`（r8，Linux；README 在同目录）用 pyte 当屏幕，在真 PTY 里跑真的 `zypi`，查两件事：画面（欢迎页、一轮对话、`/model`、重启后 `/resume`、`--sandbox` 时状态栏的标签）和进程树（以六种方式离开 zypi，之后不能有进程留下；`-p` 下只杀 zypi，agent 与工具也得停）。它们要一个已构建的 `zypi`，所以不在 `pytest` 或 CI 里；WSL2 上的结果是 `smoke.py` 23/23（加 `--sandbox workspace` 26/26）、`exit_matrix.py` 12/12（沙箱下 14/14）、`print_exit.py` 4/4。脚本本身的辅助逻辑有 `scripts/tests/test_tui_pty.py`（Windows 上跳过依赖 `pty` 的 5 个）。r9 又加了两个：`resume_matrix.py`（`--continue`、`--resume <id｜标题>`、`--session-id`、`zypi export` 对 Python 写的会话做了什么，13 例，WSL2 上 9 PASS / 4 GAP，GAP 不让脚本失败，`--strict` 才让）与 `mode_probe.py`（权限模式，只记录、不判对错：r9 时 12 个场景，结果见计划 §10.11；r10 起是 10 个场景、4 个被拒绝的 `--permission-mode` 值与设置页的一项检查，结果见 §10.13）。r11 又加了三个：`net_audit.py`（`zypi` 有没有外连，13 项检查：mock LLM 13/13，放进没有网络的命名空间再跑一次 13/13，真模型一次 13/13，含 agent 确有外连的正向对照）、`status_line_probe.py`（`[ui.status_line]` 画不画：r11 时 4 个运行都与指南页所写一致，Python agent 下不画；r12 续 7 之后 pager 用本地已有的事实画这一行，探针的预期随之改了，见计划 §10.15 续 7）与它用的替身 agent `status_proxy_agent.py`；结果见计划 §10.14。

## 4. deny-list 与保留理由（0.4）

硬性禁止：`pi-sampler`（crate、`Cargo.lock`、依赖图）；词级禁止：`MvpAgent`、`acp_session_impl`、`SamplerActor`。

### 残留词（计划阶段 C 的退出条件）

计划 C 的退出条件原文是「`grok agent`、`leader`、`x.ai`、`MvpAgent` 在文档与代码中的 grep 结果为 0（历史文档除外）」。r11 的结果：

- `MvpAgent`：0，它本来就是 deny word（§2）。
- `grok agent`：**0**（`docs/PLAN` 之外的所有文件）。13 处过时的指针都改了：`pi-shell/README.md` 原来是上游 Grok CLI 的用户手册（2,682 行，含会装上官方 CLI 的安装脚本），换成一页说明这个 crate 现在是什么；`pi-test-support` 与 `pi-agent`、`pi-http`、`pi-tty-utils` 的文档注释不再提这条命令。
- `leader`（245 行）与 `x.ai`（304 行）**清不到零**：阶段 D 之前，这些行还在活着的代码里。原文的「0」改成棘轮：`[gates.residue]` 给每个词一个上限，只降不升（§1 的执行器检查它，`pytest` 也检查）。下表说这些行是什么、去哪里。

| 词 | 行数 | 是什么 | 去向 |
|---|---|---|---|
| `leader` | 236 | 已删除的 leader 进程这个概念的名字与注释：遥测的 `LeaderMode`、`leader_converge`（36 行）；自动更新的「leader 重启」「leader 每小时检查」（25）；`pi-workspace` 的 `leader_reaped`（26）；`pi-mcp` 的「旧 leader」（19）；`pi-pager` 里的注释（42）等 | 阶段 D：遥测与更新的清理，D.4 |
| `leader` | 9 | POSIX 的 session leader / 进程组 leader（`pi-shell-terminal`、`pi-mermaid`、`pi-workspace-daemon` 等） | 留下：这是正确的用词 |
| `x.ai` | 170 | xAI 的服务地址与网页：`api.x.ai/v1`、`auth.x.ai`、`docs.x.ai`、语音的 `wss://api.x.ai/v1/stt`、`x.ai/cli/install.sh` 等，在认证、语音、远程、更新与 `pi-tools` 的代码和测试里 | 阶段 D.3（auth 层）、D.1（`pi-tools`），更新与语音随各自的清理 |
| `x.ai` | 118 | 旧 Rust agent 的扩展方法名（`x.ai/mcp/…`、`x.ai/fs/…`、`x.ai/tool…`、`x.ai/search/fuzzy/…`），出现在注释、文档与测试里；pager 不再发它们 | 随所在代码一起删（D.4） |
| `x.ai` | 16 | 过滤本身：入口处丢弃 `x.ai/` 与 `_x.ai/` 开头的扩展通知（`acp/vendor.rs`、`channel.rs` 与它们的测试）；其余是对 `x.ai` 域名的判断和测试夹具 | 过滤留下，它是「不实现 `x.ai/*`」的执行点；其余随 D.3 |

分类用的是同一条匹配规则（先判地址与网页，再判方法名，剩下的算其他）；`leader` 的 9 行是行里同时有 `session leader`、`process group leader` 或 `setsid` 的。行数是 r11 的末态；行数降了，就同时降清单里的上限。

下列 crate **允许存在**，但必须有理由与缩减方案；`check` 会把它们在 `pi-pager-bin` 依赖图里的直接依赖者列进报告，便于看出是否有新的依赖方悄悄加入。

| crate | 直接依赖者（r5 实测，Linux CI `ac1d3ff`；与 `0e71894` 相比只有 `pi-agent` 一行变了） | 现状理由 | 缩减方案 |
|---|---|---|---|
| `async-openai` | `pi-sampling-types`、`pi-tools` | 仅作为这两个 crate 的类型来源；TUI 进程里没有发起请求的代码 | 阶段 D.1：把工具展示类型抽到叶子 crate，或对 `async-openai` 做 feature-gate |
| `pi-sampling-types` | `pi-agent`、`pi-chat-state`、`pi-compaction-transcript`、`pi-shell`、`pi-subagent-resolution` | pager 经 `pi_shell::sampling` 使用对话项、reasoning-effort 元数据与错误文案（18 个符号） | 阶段 B.2：把这 18 个符号迁到不依赖 provider 栈的叶子 crate，再做 D.1 |
| `pi-tools` | 14 个 crate（含 `pi-pager`、`pi-pager-render`、`pi-shell`、`pi-workspace`） | pager 用其中约 55 个工具展示类型与少量工具函数；渲染 crate 也依赖它 | 阶段 D.1（`pi-tools-api` 是现成落点） |
| `pi-agent` | `pi-shell`、`pi-subagent-resolution`、`pi-workspace`（r5 末态；Linux CI `0e71894` 时还多一个 `pi-pager`，该普通依赖随后降成了 dev-dependency） | agents modal 已在 r5 删除，pager 里只剩测试在用它；`pi-shell` 的配置与 `pi-workspace` 的发现、目录信任仍用它的类型；为将来的 pi-rust 保留 | 阶段 D.4 再评估 |
| `pi-workspace` | `pi-http`、`pi-pager`、`pi-pager-bin`、`pi-pager-render`、`pi-shell`、`pi-shell-terminal` | pager 主要用其中的 `permission` | 阶段 D.4 |
| `pi-hooks` | `pi-agent`、`pi-workspace` | 只被上两者间接带入，pager 不直接依赖 | 随 `pi-agent` / `pi-workspace` 的 D.4 一并处理 |
| `pi-mcp` | `pi-config-types`、`pi-workspace` | MCP server 配置结构（`pi-config-types::mcp`）与 `pi-workspace` 的 MCP 接入；`/mcp` 界面与 `pi-shell` 的依赖已在 r5 删除；Python 侧 `mcp_servers` 的取舍见计划 1.P4 | 随 1.P4 / D.4 决定 |

### 保留清单（0.5）

上面的表是 r5 手写的，只举例。r11 起有生成的 [`tui-support.md`](tui-support.md)（`scripts/tui_callgraph.py keep`）：support 的 7 个 crate 共 305,581 行非测试代码，其中 17 个模块（6,190 行，2 %）没有活代码引用。最大的一块是 `pi-hooks`：9,026 行里有 4,339 行（48 %），是 `dispatcher`、`runner` 和它们的结果类型。

「没有活代码引用」只是读源码得出的估计，所以用编译器查过：把 `tui/` 拷到副本里，删掉这些模块的文件，再跑 `cargo check`。

| 在副本里删了什么 | 结果 |
|---|---|
| 17 个全删 | 三个产品 crate 的 `cargo check` 失败：5 个错误，都在 `pi-tools` 的 `types::error`（`?` 要用的 `impl From<…> for ToolError` 在里面）。清单把它标在「check first」里 |
| 留下 `types::error`，删另外 16 个 | 三个产品 crate 的 `cargo check` 通过。再删 `pi-hooks/tests/integration.rs`（它测的就是被删的模块）后，`cargo check --workspace --tests`（与执行器的 `check` 是同一条命令）也通过。只多一条告警：`pi-hooks` 的 `expand_env_vars_with_extra` 没人用了 |

工具的第一版报了 21 个模块，其中 4 个是活的；这些是按编译器的反馈改了工具之后才降到 17 个的（经过，见执行记录 §10.14）。工具不看模块里面（活模块里的死函数它找不到），也看不见没有名字的 `impl`。所以清单是候选，不是判决；真的要删，以编译器为准。**没有删**：这是阶段 D 的候选，计划里是 D.8。

## 5. 第一次 Linux 运行之后要做的事（已完成，已归档）

r5 做完了这六步（工具链、依赖图、测试分诊、开关、release 基线、本页补全）。原文在[基线历史][hist-first-run]。

## 6. 尚未覆盖

0.9（实测结案）不在本页范围（0.5 在 r11 做了，见 §1）；0.3 的 Rust 一侧（`python_agent_e2e.rs`）r9 起在清单里（§3.2），0.7（磁盘读取点清单）与 0.8（能力矩阵、ACP 版本配对表）r9 做了，见计划 §10.11 与附录 A；见计划 §7 阶段 0（0.6 入口审计在 r5 按附录 A 的默认做完，结果见计划 §10.7；0.9 的 Linux 一侧 r8 做了，结果见计划 §10.10）。「启动耗时」目前只有 `--version` 延迟，不含「到欢迎页」的时间；PTY 载体有了（§3.3），但还没有加计时。

<!-- 链接定义 -->
[hist]: archive/tui-history.md
[hist-tests]: archive/tui-history.md#3-测试数字
[hist-skip]: archive/tui-history.md#4-去掉的-skipdoctor_cmd
[hist-first-run]: archive/tui-history.md#5-第一次-linux-运行之后要做的事
