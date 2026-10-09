# TUI（Rust）基线与门禁

对应 [`PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md) 阶段 0 的 0.1（Rust CI job）、0.2（基线报告）、0.4（deny-list）。权威环境是 CI 的 Linux runner（ADR6：`ubuntu-24.04` + `tui/rust-toolchain.toml` 钉的 1.94.0）。

> **状态（r5）**：门禁与 CI job 已在 Linux 上跑通并阻塞，release 基线也有了数。最近一次完整的 Linux 结果是 PR #7 的 `ac1d3ff`（入口审计、阶段 A 与欢迎页残留清理之后）：`TUI CI` run [37389447358](https://github.com/zy1233/pi-python/actions/runs/37389447358)（PR）与手动触发的 run [37389455793](https://github.com/zy1233/pi-python/actions/runs/37389455793)（带 `release_baseline`）全绿——门禁全过，依赖图 **980** 个包，消费者构建 **0** 条告警，`--workspace --tests` 0 个错误，8 个 suite 共 **7,437 通过 / 0 失败 / 20 忽略**；`release-dist` 的 `zypi` **422,034,120 B**（比拆除前小 10.3 %），冷缓存构建 **1,155 s**（拆除前 1,647 s，快 29.9 %），`--version` 中位数 **23.1 ms**。清单已按 Linux 数收紧（依赖图上限 995 → 980，`max_warnings` 20 → 0，各 suite 的 `min_passed` 下限）。权威数字以 Linux 一栏为准；macOS 一栏是本机实测，用于对照。r8 又在 WSL2（Ubuntu 24.04、内核 5.10）里跑了同一组命令。第一轮去掉 `doctor_cmd::` 的 `skip`，8 个 suite 共 7,456 通过 / 0 失败 / 20 忽略，比 CI（`33374a3`）的 7,444 多出的正是那 12 个；之后按用户的选择把清单扩成 10 个 suite（加了 `pi-pager-render`、`pi-pager-minimal`，见 §3.2），基线执行器在 WSL2 上的结果是 **8,633 通过 / 1 个已知失败 / 22 忽略**。这些数都还没有 CI 运行证实。详见计划 §10.10。

## 1. 文件与命令

| 文件 | 作用 |
|---|---|
| `scripts/tui_baseline.toml` | 清单：工具链、deny-list、依赖图上限、测试 suite、release 参照值。改数字只动这里 |
| `scripts/tui_baseline.py` | 执行器：`gates` / `check` / `test` / `release`，输出 `tui-baseline/<命令>.{md,json}`，在 CI 里同时写入 job summary |
| `scripts/tests/test_tui_baseline.py` | 执行器的单测（解析、棘轮判定、清单与 workspace / 工作流的一致性），随 `pytest` 跑 |
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
| 依赖图上限 | `cargo tree -p pi-pager-bin --target x86_64-unknown-linux-gnu` 的唯一包数 ≤ 980（Linux 实测 980） | 是 |
| 消费者构建 | `cargo check --locked -p pi-pager-bin -p pi-pager-minimal -p pi-update` | 是 |
| 全工作区类型检查 | `cargo check --workspace --tests --keep-going`（排除 `pi-fast-worktree`，其 lib 测试在 HEAD 上就编译不过） | 是，`enforce_workspace_tests = true` |
| 告警数 | 消费者构建的 `dead_code` 等告警，按 lint / crate 汇总 | 是，`max_warnings = 0` |
| 测试 | 10 个 suite，见 §3.2 | 是，`[test] enforce = true`（意外失败、超时、构建错误都会失败；每个 suite 有 `min_passed` 下限） |
| release 体积 | `release-dist` 的 `zypi` 不大于 v0.4.0 | 是，仅手动 job |

## 3. 基线数据

### 3.1 构建与依赖图

「拆除前」= 发布版 v0.4.0（`tui/` 与提交 `07a3574` 完全相同）。「r3」= 提交 `9add266`（拆除 Rust runtime）。「r5」= 入口审计与阶段 A 之后：行数、包数与 macOS 一栏测于 `ac1d3ff`（依赖图只取决于 `Cargo.lock`，`fe1fc65` 之后它没变），Linux 一栏来自 `ac1d3ff` 的两次 CI 运行（PR run 37389447358；手动 run 37389455793，带 release 基线）。

| 指标 | 拆除前 | r3（macOS） | r5（macOS） | r5（Linux CI） |
|---|---|---|---|---|
| `tui/crates` 下 `.rs` 行数 | 1,620,612 | 1,254,693 | 1,019,041 | 同左（同一份源码） |
| `Cargo.lock` 包数 | 1,311 | 1,285 | 1,257 | 同左 |
| 依赖图唯一包数 `x86_64-unknown-linux-gnu` | 1,026 | 995 | 980 | 980 |
| `aarch64-unknown-linux-gnu` | 1,025 | 994 | 979 | — |
| `aarch64-apple-darwin` | 990 | 957 | 943 | — |
| `x86_64-apple-darwin` | 991 | 958 | 944 | — |
| `x86_64-pc-windows-msvc` | 972 | 939 | 926 | — |
| 消费者构建告警 | 未测 | 20（全是 `pi-shell` 的 `dead_code`） | 0 | 0 |
| `cargo check` 消费者构建耗时 | 未测 | 94 s（本机，缓存状态不受控，只作参考） | — | 204 s（runner；缓存状态不受控，第一次冷缓存运行是 272 s） |
| `cargo check --workspace --tests` 耗时 | 未测 | 162 s（同上），0 个错误 | — | 164 s，0 个错误（第一次冷缓存运行是 229 s） |
| `release-dist` 的 `zypi`（Linux x86_64） | 470,461,776 B（448.7 MiB） | — | — | **422,034,120 B（402.5 MiB，−10.3 %）** |
| `release-dist` 冷缓存构建耗时 | 27 m 27 s（`ubuntu-24.04`，v0.4.0 的发布 job） | — | — | **19 m 15 s（1,155 s，−29.9 %）** |
| `zypi --version` 延迟（5 次中位数） | 未测 | — | — | 23.1 ms |

macOS 环境：Apple Silicon，Homebrew `cargo` / `rustc` 1.96.1（**不是**钉的 1.94.0）。依赖图节点数只取决于 `Cargo.lock` 与目标三元组，与工具链版本无关；告警数与耗时会随工具链漂移，所以只当参考。

历史：第一次 Linux 运行（run 37263077643，`e20af45`，入口审计之前）是依赖图 995、告警 20、消费者构建 272 s、`--workspace --tests` 229 s、8 个 suite 10,851 通过。r4 的 leader 残留清理把 `.rs` 行数从 1,254,693 降到 1,252,374，依赖图与告警不变。

### 3.2 测试

**Linux（权威）**：手动 run 37389455793（`ac1d3ff`），`test` job 约 27 m；同一提交的 PR run 37389447358 结果相同。`macOS` 一栏的数是 `ac1d3ff` 上的本机轻量运行（`--test-threads=2`，只跑了 `pi-pager`）；命令：`env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor`（沙箱默认的 `NO_COLOR=1`、`TERM=dumb` 会让约 10 个颜色 / 光标断言失败）；`PI_HOME` 等变量由清单的 `unset_env` 清掉（`PI_HOME` 的优先级高于配置隔离测试自己设的 `GROK_HOME`，不清掉会让 `test_config_update_isolation` 失败）。**在 WSL 里跑**：克隆放在 ext4 上，不要放在 `/mnt/<盘>`（9p，权限位与时间戳的行为不是 Linux 的）；登录脚本常会导出 `PI_HOME`（指向 Windows 一侧的配置目录），绕过执行器直接跑 `cargo test` 或测试二进制会读写那个目录——要么走执行器，要么先 `unset PI_HOME GROK_HOME` 并把 `HOME` 指到一次性目录。

| suite（`cargo test -p …`） | 目标 | 通过（Linux） | 失败 | 忽略 | 耗时（含增量编译） | `min_passed` | 说明 |
|---|---|---|---|---|---|---|---|
| `pi-shell` | `--lib` + 2 个集成 | 1,056 | 0 | 1 | 383 s | 1,000 | |
| `pi-pager` | `--lib` + 3 个集成 | 5,552 | 0 | 13 | 509 s | 5,300 | macOS：lib 5,307 + `settings_e2e` 251 + 2 + 2，0 失败（Alt/Opt 渲染的 2 个已知失败随 dashboard 一起删了，清单里的 `known_failures` 也去掉了） |
| `pi-pager-bin` | `--bin zypi` | 20 | 0 | 0 | 238 s | 20 | 含 6 个 `-p` 守卫用例 |
| `pi-acp-lib` | `--lib` | 21 | 0 | 0 | 19 s | 21 | 跳过 1 个会永久挂起的用例 |
| `pi-http` | `--lib` | 13 | 0 | 0 | 189 s | 13 | |
| `pi-telemetry` | `--lib` | 239 | 0 | 0 | 72 s | 230 | macOS 多 5 个平台相关用例 |
| `pi-file-utils` | `--lib` | 216 | 0 | 6 | 68 s | 210 | macOS 多 1 个 |
| `pi-sampling-types` | `--lib` | 320 | 0 | 0 | 107 s | 310 | |
| **合计** | | **7,437** | **0** | **20** | | | |

**`ac1d3ff` 之后的变化（r8，WSL2 实测，未经 CI）**：CI 最近一次（`33374a3`）是这 8 个 suite 共 7,444。r8 去掉了 `doctor_cmd::` 的 `skip`（`pi-pager` +12），新增 6 个 `agent_stderr` 用例（`pi-pager` +6，5,559 → 5,577）与 1 个 `-p` 用例（`pi-pager-bin` 20 → 21），并把 `pi-pager-render`（带一个 `known_failures`）与 `pi-pager-minimal` 加进清单。10 个 suite 在基线执行器里的结果（退出码 0，1,469 s，含增量编译）：`pi-shell` 1,056、`pi-pager` 5,577、`pi-pager-render` 1,091（另有 1 个已知失败、2 个忽略）、`pi-pager-minimal` 79、`pi-pager-bin` 21、`pi-acp-lib` 21、`pi-http` 13、`pi-telemetry` 239、`pi-file-utils` 216、`pi-sampling-types` 320，**合计 8,633 通过 / 1 个已知失败 / 22 忽略**。清单里 `pi-pager`（5,577）、`pi-pager-bin`（21）的 `reference_passed` 随之更新，`pi-pager-render`（1,092）与 `pi-pager-minimal`（79）是新增的；`min_passed` 都取实测值下方几个百分点。

对比第一次 Linux 运行（10,851 通过 / 75 忽略）：少了 3,414 个通过，是随被删功能一起删的用例（`0e71894` 时是 7,478，即 −3,373：`pi-pager` −3,281，`pi-shell` −93，`pi-telemetry` −5；另有 `-p` 沙箱守卫新增的 9 个用例——`pi-shell` +3、`pi-pager-bin` +6——已含在净数里；其后 `5fcec14` 删 `chat_mode` 世界又少 21 个，`ac1d3ff` 删 worktree 对话框又少 20 个，都在 `pi-pager`）；`min_passed` 取实测值下方 3–6 %，让今后无意的丢失会失败，有意的删除要改清单。

**清单里的已知失败 / 跳过，及原因**

| 项 | 处理 | 原因 |
|---|---|---|
| `terminal::tmux_probe::tests::successful_near_deadline_exit_still_returns_captured_output`（`pi-pager-render`） | `known_failures` | 在 1.5 s 的预算里用 shell 脚本烧 1.2 s，只给 `sh` 与 `perl` 的启动留 0.3 s，机器一卡就失败。WSL2 上失败过两次，都是测试二进制刚链接完的第一次运行（逐 crate 那一遍；清单的第一次完整运行——整个 crate 用了 8.6 s，平时是 1.3 s），之后重复 94 次（空闲与 12 个忙循环占满 CPU 各一半）都没复现，所以猜是刚链接完磁盘写回拖慢了进程启动，没有证实。容忍而不是跳过：用例照跑，报告里点它的名，天天失败的话看得出来，但不阻塞 |
| `vendor_x_ai_ext_is_dropped_without_sending`（`pi-acp-lib`） | `skip` | **永久挂起**：`acp::ExtRequest` 序列化时丢了 `method`，丢弃逻辑不触发（计划 §10.4） |
| `pi-pager-bin/tests/update_never_blocked_by_config.rs` | 未选入 | 按 `CARGO_BIN_EXE_pi-pager` 找二进制，而二进制叫 `zypi`（fork 改名遗留） |

**去掉的 `skip`**：`doctor_cmd::`（`pi-pager`，12 个）。它原先探测音频输入设备，在 macOS 上卡在 CoreAudio（疑似等麦克风授权），所以清单里一直跳过。r5 删掉了 `diagnostics::apply_voice_probe`，之后在 macOS 本机不加 `skip` 时这 12 个用例几秒内全部通过；r8 又在没有音频设备的 WSL2 上跑了一遍，也都通过，于是去掉了 `skip`（`pi-pager` 的 `reference_passed` 加 12）。CI 的 runner 上还没跑过；若第一次运行里它们出问题，把 `skip = ["doctor_cmd::"]` 放回清单并写明原因。

**没有选入 suite 的 crate**：r8 在 Linux（WSL2，计划 §10.10）上逐个跑过它们的 `--lib --tests`：74 个 crate，68 个全过（含 `pi-sandbox`、`pi-tools`、`pi-pager-minimal`，其中 5 个没有测试），共 11,807 通过 / 13 失败 / 23 忽略。有失败的都与 runtime 拆除无关：`pi-agent`（1 项：加密模板字节过期）、`pi-shell-base`（2 项：按进程名 `grok` 判断，函数没有调用者）、`pi-workspace`（`session::git::*` 7 项：`xai-org` 改名遗留与 OID 断言）、`pi-hooks`（1 项：`test_hook_child_cannot_open_dev_tty` 假定 `sh` 的 `exec` 重定向失败后会接着往下走，dash 不会；它只在有控制终端时才跑，CI 上会自己跳过）、`pi-fast-worktree`（lib 测试编译不过）；另有两个偶发（`pi-pager-render` 的 tmux 探测、`pi-workspace` 的 `hub_auth::proactive`），之后各重复 60 次都没复现。

- 已加进清单（r8，用户选了做）：`pi-pager-render`（整个 crate 1,092 通过 / 2 忽略，带上面那个 `known_failures`）与 `pi-pager-minimal`（79 通过）。后者有个用例缺 `test_lock()`，默认线程数下 30 次失败 25 次，r8 已补上，补后 40/40。它们和 `doctor_cmd::` 的 `skip` 一样，是在 WSL2 里量的，没有 CI 运行证实过；第一次 CI 运行如果在 runner 上有不同的结果，就按 §5 的办法分诊——要么修，要么（`doctor_cmd::`）把 `skip` 放回去并写明原因。
- `pi-sandbox` 的端到端用例（`deny_paths_e2e`、`read_write_trailing_glob_e2e`）在内核不支持 Landlock 时直接 `return`，「通过」是空转；要在带 Landlock 的 runner（内核 ≥ 5.13）上并设 `SANDBOX_E2E_REQUIRE_ENFORCEMENT=1` 才算数。这一项还没做，仍会改 CI 门禁（要换 runner 或加一个 job）。

### 3.3 PTY 载体

`scripts/tui_pty/`（r8，Linux；README 在同目录）用 pyte 当屏幕，在真 PTY 里跑真的 `zypi`，查两件事：画面（欢迎页、一轮对话、`/model`、重启后 `/resume`、`--sandbox` 时状态栏的标签）和进程树（以六种方式离开 zypi，之后不能有进程留下；`-p` 下只杀 zypi，agent 与工具也得停）。它们要一个已构建的 `zypi`，所以不在 `pytest` 或 CI 里；WSL2 上的结果是 `smoke.py` 23/23（加 `--sandbox workspace` 26/26）、`exit_matrix.py` 12/12（沙箱下 14/14）、`print_exit.py` 4/4。脚本本身的辅助逻辑有 `scripts/tests/test_tui_pty.py`（Windows 上跳过依赖 `pty` 的 5 个）。

## 4. deny-list 与保留理由（0.4）

硬性禁止：`pi-sampler`（crate、`Cargo.lock`、依赖图）；词级禁止：`MvpAgent`、`acp_session_impl`、`SamplerActor`。

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

## 5. 第一次 Linux 运行之后要做的事

**进度（r5）**：第 1–6 步都已完成（见上方状态与 §3）：`min_passed` 按 `0e71894` 的 Linux 数设置，release 基线由手动 run 37389455793 补齐。第 3 步没有需要分诊的失败：Linux 上 8 个 suite 全部通过。第 2 步的系统库预查也得到验证——只装 `protoc` 就能在 `ubuntu-24.04` 上编译并链接全部测试二进制。

1. 触发 `TUI CI`（push / PR 命中 `tui/**` 即自动运行；或手动 `workflow_dispatch`）。看 job summary 或下载 artifact `tui-baseline-check` / `tui-baseline-test`。
2. `check`：看工具链是否按钉安装（`rustc --version --verbose` 应为 1.94.0）、依赖图节点数、`--workspace --tests` 的错误列表（macOS 上从未编译过的 `cfg(target_os = "linux")` 代码会在这里暴露）。系统库已预查：`cargo tree -p pi-pager-bin --target x86_64-unknown-linux-gnu` 里的 `-sys` crate 要么内置源码（`libsqlite3-sys` 开了 `bundled`，另有 `zstd-sys`、`aws-lc-sys`、`libgit2-sys`、`tikv-jemalloc-sys`、`libmimalloc-sys`），要么在没有系统库时回退到源码构建（`libz-sys`），图里没有 `alsa-sys`、`openssl-sys`、`libudev-sys`、`dbus-sys`；所以 workflow 和 `release.yml`（v0.4.0 就是这样在 `ubuntu-24.04` 上构建成功的）一样只装 `protoc`。这只覆盖 `pi-pager-bin` 的图，没有逐个核对其他 suite 的 dev-dependencies；若 `test` job 在链接阶段报缺系统库，在 `Install protoc` 之后加一步 `apt-get install`。
3. `test`：把每个意外失败分诊为「修」或「加入 `known_failures` / `skip` 并写明原因」；用 Linux 的通过数设置各 suite 的 `reference_passed` 与 `min_passed`。
4. 翻开开关：`[test] enforce = true`、`[check] enforce_workspace_tests = true`；按实测设置 `max_warnings`，并把 `[graph.max_nodes]` 的 `PROVISIONAL` 值换成 Linux 实测值。
5. 手动运行一次 `release_baseline`（`gh workflow run tui-ci.yml --ref <分支> -f release_baseline=true`），把 `zypi` 体积、构建耗时与 `--version` 延迟填进 §3.1。实测：手动 run 的 `concurrency` 组是 `TUI CI-refs/heads/<分支>`，与 PR 运行的 `refs/pull/<n>/merge` 不是同一组，所以之后往 PR 上 push **不会**取消它；只有再手动触发同一分支才会取代上一次。整个 workflow 约 27 分钟（release 构建 19 分钟）。
6. 把本页 Linux 一栏补全，并在计划文档把 0.1 / 0.2 勾选为完成。两项都已满足（0.2 的三项 release 数字见 §3.1）。`min_passed` 已在入口审计的删除落地后按 Linux 数设置（`27df705`）；`ac1d3ff` 之后 `pi-pager` 的实测是 5,552，仍高于 5,300 的下限，`reference_passed` 随之更新到 5,552。

## 6. 尚未覆盖

0.3（ACP 契约 / e2e 载体）、0.5（模块级调用图工具化）、0.7（磁盘读取点清单）、0.8（能力矩阵与 ACP 版本配对表）、0.9（实测结案）都不在本页范围；见计划 §7 阶段 0（0.6 入口审计在 r5 按附录 A 的默认做完，结果见计划 §10.7；0.9 的 Linux 一侧 r8 做了，结果见计划 §10.10）。「启动耗时」目前只有 `--version` 延迟，不含「到欢迎页」的时间；PTY 载体有了（§3.3），但还没有加计时。
