# TUI（Rust）基线与门禁

对应 [`PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md) 阶段 0 的 0.1（Rust CI job）、0.2（基线报告）、0.4（deny-list）。权威环境是 CI 的 Linux runner（ADR6：`ubuntu-24.04` + `tui/rust-toolchain.toml` 钉的 1.94.0）。

> **状态（r5）**：门禁与 CI job 已在 Linux 上跑通（`TUI CI` run [37263077643](https://github.com/zy1233/pi-python/actions/runs/37263077643)，PR #7 的 `e20af45`）：门禁全过，依赖图 995 个包，消费者构建 20 条告警，`--workspace --tests` 0 个错误，8 个 suite 共 **10,851 通过 / 0 失败 / 75 忽略**。据此测试与类型检查改为阻塞（`enforce = true`、`enforce_workspace_tests = true`），依赖图上限收紧到 995，`max_warnings = 20`。**还缺**：release 体积 / 冷编译耗时 / `--version` 延迟（`release-baseline` 要手动触发，见 §5），以及各 suite 的 `min_passed` 下限（等入口审计的删除落地后再按 Linux 数设置）。权威数字以 Linux 一栏为准；macOS 一栏是本机实测，用于对照。

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
| 依赖图上限 | `cargo tree -p pi-pager-bin --target x86_64-unknown-linux-gnu` 的唯一包数 ≤ 995（Linux 实测 995） | 是 |
| 消费者构建 | `cargo check --locked -p pi-pager-bin -p pi-pager-minimal -p pi-update` | 是 |
| 全工作区类型检查 | `cargo check --workspace --tests --keep-going`（排除 `pi-fast-worktree`，其 lib 测试在 HEAD 上就编译不过） | 是，`enforce_workspace_tests = true` |
| 告警数 | 消费者构建的 `dead_code` 等告警，按 lint / crate 汇总 | 是，`max_warnings = 20` |
| 测试 | 8 个 suite，见 §3.2 | 是，`[test] enforce = true`（意外失败、超时、构建错误都会失败；下限 `min_passed` 暂未设） |
| release 体积 | `release-dist` 的 `zypi` 不大于 v0.4.0 | 是，仅手动 job |

## 3. 基线数据

### 3.1 构建与依赖图

「拆除前」= 发布版 v0.4.0（`tui/` 与提交 `07a3574` 完全相同）。「拆除后」= 提交 `9add266`（拆除 Rust runtime）。

| 指标 | 拆除前 | 拆除后（macOS） | 拆除后（Linux CI） |
|---|---|---|---|
| `tui/crates` 下 `.rs` 行数 | 1,620,612 | 1,254,693 | — |
| `Cargo.lock` 包数 | 1,311 | 1,285 | — |
| 依赖图唯一包数 `x86_64-unknown-linux-gnu` | 1,026 | 995 | 995 |
| `aarch64-unknown-linux-gnu` | 1,025 | 994 | — |
| `aarch64-apple-darwin` | 990 | 957 | — |
| `x86_64-apple-darwin` | 991 | 958 | — |
| `x86_64-pc-windows-msvc` | 972 | 939 | — |
| 消费者构建告警 | 未测 | 20（全是 `pi-shell` 的 `dead_code`；crate 根的 `#![allow]` 仍在，见计划 A.4） | 20（同上） |
| `cargo check` 消费者构建耗时 | 未测 | 94 s（本机，缓存状态不受控，只作参考） | 272 s（runner，冷缓存） |
| `cargo check --workspace --tests` 耗时 | 未测 | 162 s（同上），0 个错误 | 229 s，0 个错误 |
| `release-dist` 的 `zypi`（Linux x86_64） | 470,461,776 B（448.7 MiB） | 待回填 | 待回填 |
| `release-dist` 冷缓存构建耗时 | 27 m 27 s（`ubuntu-24.04`，v0.4.0 的发布 job） | — | 待回填 |
| `zypi --version` 延迟（5 次中位数） | 未测 | — | 待回填 |

macOS 环境：Apple Silicon，Homebrew `cargo` / `rustc` 1.96.1（**不是**钉的 1.94.0）。依赖图节点数只取决于 `Cargo.lock` 与目标三元组，与工具链版本无关；告警数与耗时会随工具链漂移，所以只当参考。

「拆除后」一栏是 `9add266` 时的数。之后清理 leader 的 UI 残留和 1.R3 的命名（计划 r4）又让 `tui/crates` 的 `.rs` 行数降到 1,252,374；依赖图节点数（995）、消费者构建告警（20）和 `--workspace --tests`（0 个错误）在末态复测，没有变化。

### 3.2 测试

两列不是同一个提交：**Linux 一栏**测于 `e20af45`（`TUI CI` run 37263077643），**macOS 一栏**测于其后的提交——多了 `-p` 沙箱守卫的 9 个测试（`pi-shell` +3、`pi-pager-bin` +6），删 changelog / What's new 功能让 `pi-pager` 净减 8 个（删掉 changelog 面板与菜单行、`/release-notes` 的用例，新增堆叠信息槽的边界用例；该功能在 `pi-shell-base` 里的 5 个用例不在任何 suite 内）——所以 `pi-shell`、`pi-pager`、`pi-pager-bin` 三行的差里含这些改动。macOS 命令：`env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor`（沙箱默认的 `NO_COLOR=1`、`TERM=dumb` 会让约 10 个颜色 / 光标断言失败）；`PI_HOME` 等变量由清单的 `unset_env` 清掉（`PI_HOME` 的优先级高于配置隔离测试自己设的 `GROK_HOME`，不清掉会让 `test_config_update_isolation` 失败）。

| suite（`cargo test -p …`） | 目标 | 通过（Linux） | 通过（macOS） | 失败（Linux / macOS） | 忽略（Linux / macOS） | 说明 |
|---|---|---|---|---|---|---|
| `pi-shell` | `--lib` + 2 个集成 | 1,149 | 1,143 | 0 / 0 | 3 / 8 | macOS：1,138 + 2 + 3；Linux 多 9 个 `cfg(linux)` 用例 |
| `pi-pager` | `--lib` + 3 个集成 | 8,874 | 8,862 | 0 / 2 | 66 / 66 | macOS：8,581 + 277 + 2 + 2；macOS 的 2 个是 Alt/Opt 渲染，见下 |
| `pi-pager-bin` | `--bin zypi` | 14 | 20 | 0 / 0 | 0 / 0 | macOS 多 6 个 `-p` 守卫用例 |
| `pi-acp-lib` | `--lib` | 21 | 21 | 0 / 0 | 0 / 0 | 跳过 1 个会永久挂起的用例 |
| `pi-http` | `--lib` | 13 | 13 | 0 / 0 | 0 / 0 | |
| `pi-telemetry` | `--lib` | 244 | 244 | 0 / 0 | 0 / 0 | |
| `pi-file-utils` | `--lib` | 216 | 217 | 0 / 0 | 6 / 6 | Linux 少 1 个 |
| `pi-sampling-types` | `--lib` | 320 | 320 | 0 / 0 | 0 / 0 | |
| **合计** | | **10,851** | **10,840** | **0 / 2（已知）** | **75 / 80** | |

Linux 一栏单个 suite 的耗时（含增量编译）：`pi-shell` 383 s、`pi-pager` 554 s、`pi-pager-bin` 252 s、`pi-http` 191 s、`pi-sampling-types` 110 s、`pi-telemetry` 74 s、`pi-file-utils` 67 s、`pi-acp-lib` 19 s；`test` job 整体 28 分钟。

**清单里的已知失败 / 跳过，及原因**

| 项 | 处理 | 原因 |
|---|---|---|
| `render_footer_multiline_empty_create_uses_shift_or_alt_enter`、`render_footer_multiline_mode_send_uses_shift_or_alt_enter`（`pi-pager`） | `known_failures` | macOS 把 Alt 渲染成 `Opt`，断言只认 `Alt+Enter`。**Linux 首跑确认通过**；仍留在 `known_failures` 里，是为了让 macOS 开发机上的本地运行也能跑出绿色结果（清单对「已知项通过」不报错） |
| `doctor_cmd::`（`pi-pager`，17 个） | `skip` | 探测音频输入设备，在 macOS 上卡在 CoreAudio（疑似等麦克风授权）；**Linux 上同样被跳过，所以没有被验证过**，要验证就在一次专门的 CI 运行里去掉 `skip` |
| `vendor_x_ai_ext_is_dropped_without_sending`（`pi-acp-lib`） | `skip` | **永久挂起**：`acp::ExtRequest` 序列化时丢了 `method`，丢弃逻辑不触发（计划 §10.4） |
| `pi-pager-bin/tests/update_never_blocked_by_config.rs` | 未选入 | 按 `CARGO_BIN_EXE_pi-pager` 找二进制，而二进制叫 `zypi`（fork 改名遗留） |

**没有选入 suite 的 crate**：`pi-agent`（1 项失败：加密模板字节过期）、`pi-shell-base`（1 项：按进程名 `grok` 判断）、`pi-workspace`（`session::git::*` 7 项：`xai-org` 改名遗留与 OID 断言）、`pi-fast-worktree`（lib 测试编译不过），以及其余没动过的 crate（`pi-tools`、`pi-hooks` 等）。这些失败与 runtime 拆除无关，各自分诊后再纳入。

## 4. deny-list 与保留理由（0.4）

硬性禁止：`pi-sampler`（crate、`Cargo.lock`、依赖图）；词级禁止：`MvpAgent`、`acp_session_impl`、`SamplerActor`。

下列 crate **允许存在**，但必须有理由与缩减方案；`check` 会把它们在 `pi-pager-bin` 依赖图里的直接依赖者列进报告，便于看出是否有新的依赖方悄悄加入。

| crate | 直接依赖者（r4 实测） | 现状理由 | 缩减方案 |
|---|---|---|---|
| `async-openai` | `pi-sampling-types`、`pi-tools` | 仅作为这两个 crate 的类型来源；TUI 进程里没有发起请求的代码 | 阶段 D.1：把工具展示类型抽到叶子 crate，或对 `async-openai` 做 feature-gate |
| `pi-sampling-types` | `pi-agent`、`pi-chat-state`、`pi-compaction-transcript`、`pi-shell`、`pi-subagent-resolution` | pager 经 `pi_shell::sampling` 使用对话项、reasoning-effort 元数据与错误文案（18 个符号） | 阶段 B.2：把这 18 个符号迁到不依赖 provider 栈的叶子 crate，再做 D.1 |
| `pi-tools` | 15 个 crate（含 `pi-pager`、`pi-pager-render`、`pi-shell`、`pi-workspace`） | pager 用其中约 55 个工具展示类型与少量工具函数；渲染 crate 也依赖它 | 阶段 D.1（`pi-tools-api` 是现成落点） |
| `pi-agent` | `pi-pager`、`pi-plugin-marketplace`、`pi-shell`、`pi-subagent-resolution`、`pi-workspace` | `agents_modal` 之外几乎没有活引用；为将来的 pi-rust 保留 | 阶段 D.4：入口审计（A.3）删除 agents modal 后再评估 |
| `pi-workspace` | `pi-http`、`pi-pager`、`pi-pager-bin`、`pi-pager-render`、`pi-shell`、`pi-shell-terminal` | pager 主要用其中的 `permission` | 阶段 D.4 |
| `pi-hooks` | `pi-agent`、`pi-workspace` | 只被上两者间接带入，pager 不直接依赖 | 随 `pi-agent` / `pi-workspace` 的 D.4 一并处理 |
| `pi-mcp` | `pi-config-types`、`pi-shell`、`pi-workspace` | MCP server 配置结构与 `/mcp` 界面所需；Python 侧 `mcp_servers` 的取舍见计划 1.P4 | 随 1.P4 / D.4 决定 |

## 5. 第一次 Linux 运行之后要做的事

**进度（r5）**：第 1–4 步已完成（见上方状态与 §3），第 5 步（release 基线）和第 6 步里的 `min_passed` 还没做。第 3 步没有需要分诊的失败：Linux 上 8 个 suite 全部通过。第 2 步的系统库预查也得到验证——只装 `protoc` 就能在 `ubuntu-24.04` 上编译并链接全部测试二进制。

1. 触发 `TUI CI`（push / PR 命中 `tui/**` 即自动运行；或手动 `workflow_dispatch`）。看 job summary 或下载 artifact `tui-baseline-check` / `tui-baseline-test`。
2. `check`：看工具链是否按钉安装（`rustc --version --verbose` 应为 1.94.0）、依赖图节点数、`--workspace --tests` 的错误列表（macOS 上从未编译过的 `cfg(target_os = "linux")` 代码会在这里暴露）。系统库已预查：`cargo tree -p pi-pager-bin --target x86_64-unknown-linux-gnu` 里的 `-sys` crate 要么内置源码（`libsqlite3-sys` 开了 `bundled`，另有 `zstd-sys`、`aws-lc-sys`、`libgit2-sys`、`tikv-jemalloc-sys`、`libmimalloc-sys`），要么在没有系统库时回退到源码构建（`libz-sys`），图里没有 `alsa-sys`、`openssl-sys`、`libudev-sys`、`dbus-sys`；所以 workflow 和 `release.yml`（v0.4.0 就是这样在 `ubuntu-24.04` 上构建成功的）一样只装 `protoc`。这只覆盖 `pi-pager-bin` 的图，没有逐个核对其他 suite 的 dev-dependencies；若 `test` job 在链接阶段报缺系统库，在 `Install protoc` 之后加一步 `apt-get install`。
3. `test`：把每个意外失败分诊为「修」或「加入 `known_failures` / `skip` 并写明原因」；用 Linux 的通过数设置各 suite 的 `reference_passed` 与 `min_passed`。
4. 翻开开关：`[test] enforce = true`、`[check] enforce_workspace_tests = true`；按实测设置 `max_warnings`，并把 `[graph.max_nodes]` 的 `PROVISIONAL` 值换成 Linux 实测值。
5. 手动运行一次 `release_baseline`，把 `zypi` 体积、构建耗时与 `--version` 延迟填进 §3.1。**要放在最后一次 push 之后**：workflow 的 `concurrency` 是 `cancel-in-progress`，期间任何新的 push 都会把这个 30 分钟的任务取消掉。
6. 把本页 Linux 一栏补全，并在计划文档把 0.1 / 0.2 勾选为完成。0.1 已满足（Linux 上跑通且阻塞）；0.2 还缺第 5 步的三项数字。`min_passed` 等入口审计的删除落地后，按那时的 Linux 数设置。

## 6. 尚未覆盖

0.3（ACP 契约 / e2e 载体）、0.5（模块级调用图工具化）、0.6（入口审计）、0.7（磁盘读取点清单）、0.8（能力矩阵与 ACP 版本配对表）、0.9（实测结案）都不在本页范围；见计划 §7 阶段 0。「启动耗时」目前只有 `--version` 延迟，不含「到欢迎页」的时间，后者需要 PTY 载体。
