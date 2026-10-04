# TUI（Rust）基线与门禁

对应 [`PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md) 阶段 0 的 0.1（Rust CI job）、0.2（基线报告）、0.4（deny-list）。权威环境是 CI 的 Linux runner（ADR6：`ubuntu-24.04` + `tui/rust-toolchain.toml` 钉的 1.94.0）。

> **状态（r4）**：门禁与 CI job 已落库，本机（macOS）全部跑通；**还没有在 Linux 上跑过**。下面「拆除后」一栏是 macOS 实测值，Linux 一栏留空，等第一次 `TUI CI` 运行后按 §5 回填。在回填之前，测试与 `--workspace --tests` 类型检查只报告、不阻塞。

## 1. 文件与命令

| 文件 | 作用 |
|---|---|
| `scripts/tui_baseline.toml` | 清单：工具链、deny-list、依赖图上限、测试 suite、release 参照值。改数字只动这里 |
| `scripts/tui_baseline.py` | 执行器：`gates` / `check` / `test` / `release`，输出 `tui-baseline/<命令>.{md,json}`，在 CI 里同时写入 job summary |
| `scripts/tests/test_tui_baseline.py` | 执行器的单测（解析、棘轮判定、清单与 workspace / 工作流的一致性），随 `pytest` 跑 |
| `.github/workflows/tui-ci.yml` | 三个 job：`check`、`test`、`release-baseline`（仅 `workflow_dispatch` 且勾选 `release_baseline`） |

本机复现（任何平台，需要 `protoc`）：

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
| 依赖图上限 | `cargo tree -p pi-pager-bin --target x86_64-unknown-linux-gnu` 的唯一包数 ≤ 1000（实测 995） | 是 |
| 消费者构建 | `cargo check --locked -p pi-pager-bin -p pi-pager-minimal -p pi-update` | 是 |
| 全工作区类型检查 | `cargo check --workspace --tests --keep-going`（排除 `pi-fast-worktree`，其 lib 测试在 HEAD 上就编译不过） | 否，`enforce_workspace_tests = false` |
| 告警数 | 消费者构建的 `dead_code` 等告警，按 lint / crate 汇总 | 否，没有设 `max_warnings` |
| 测试 | 8 个 suite，见 §3.2 | 否，`[test] enforce = false` |
| release 体积 | `release-dist` 的 `zypi` 不大于 v0.4.0 | 是，仅手动 job |

## 3. 基线数据

### 3.1 构建与依赖图

「拆除前」= 发布版 v0.4.0（`tui/` 与提交 `07a3574` 完全相同）。「拆除后」= 提交 `9add266`（拆除 Rust runtime）。

| 指标 | 拆除前 | 拆除后（macOS） | 拆除后（Linux CI） |
|---|---|---|---|
| `tui/crates` 下 `.rs` 行数 | 1,620,612 | 1,254,693 | — |
| `Cargo.lock` 包数 | 1,311 | 1,285 | — |
| 依赖图唯一包数 `x86_64-unknown-linux-gnu` | 1,026 | 995 | 待回填 |
| `aarch64-unknown-linux-gnu` | 1,025 | 994 | — |
| `aarch64-apple-darwin` | 990 | 957 | — |
| `x86_64-apple-darwin` | 991 | 958 | — |
| `x86_64-pc-windows-msvc` | 972 | 939 | — |
| 消费者构建告警 | 未测 | 20（全是 `pi-shell` 的 `dead_code`；crate 根的 `#![allow]` 仍在，见计划 A.4） | 待回填 |
| `cargo check` 消费者构建耗时 | 未测 | 94 s（本机，缓存状态不受控，只作参考） | 待回填 |
| `cargo check --workspace --tests` 耗时 | 未测 | 162 s（同上），0 个错误 | 待回填 |
| `release-dist` 的 `zypi`（Linux x86_64） | 470,461,776 B（448.7 MiB） | 待回填 | 待回填 |
| `release-dist` 冷缓存构建耗时 | 27 m 27 s（`ubuntu-24.04`，v0.4.0 的发布 job） | — | 待回填 |
| `zypi --version` 延迟（5 次中位数） | 未测 | — | 待回填 |

macOS 环境：Apple Silicon，Homebrew `cargo` / `rustc` 1.96.1（**不是**钉的 1.94.0）。依赖图节点数只取决于 `Cargo.lock` 与目标三元组，与工具链版本无关；告警数与耗时会随工具链漂移，所以只当参考。

### 3.2 测试

macOS 实测，`env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor`（沙箱默认的 `NO_COLOR=1`、`TERM=dumb` 会让约 10 个颜色 / 光标断言失败）。

| suite（`cargo test -p …`） | 目标 | 通过 | 失败 | 忽略 | 说明 |
|---|---|---|---|---|---|
| `pi-shell` | `--lib` + 2 个集成 | 1,141 | 0 | 8 | 1,136 + 2 + 3 |
| `pi-pager` | `--lib` + 3 个集成 | 8,887 | 2 | 66 | 8,606 + 277 + 2 + 2；失败项见下 |
| `pi-pager-bin` | `--bin zypi` | 14 | 0 | 0 | |
| `pi-acp-lib` | `--lib` | 21 | 0 | 0 | 跳过 1 个会永久挂起的用例 |
| `pi-http` | `--lib` | 13 | 0 | 0 | |
| `pi-telemetry` | `--lib` | 244 | 0 | 0 | |
| `pi-file-utils` | `--lib` | 217 | 0 | 6 | |
| `pi-sampling-types` | `--lib` | 320 | 0 | 0 | |
| **合计** | | **10,857** | **2（已知）** | **80** | |

**清单里的已知失败 / 跳过，及原因**

| 项 | 处理 | 原因 |
|---|---|---|
| `render_footer_multiline_empty_create_uses_shift_or_alt_enter`、`render_footer_multiline_mode_send_uses_shift_or_alt_enter`（`pi-pager`） | `known_failures` | macOS 把 Alt 渲染成 `Opt`，断言只认 `Alt+Enter`；预期只在 macOS 失败。清单与平台无关地容忍它们，Linux 首跑若确认通过就从 `known_failures` 移除 |
| `doctor_cmd::`（`pi-pager`，17 个） | `skip` | 探测音频输入设备，在 macOS 上卡在 CoreAudio（疑似等麦克风授权）；Linux runner 上是否可跑待首次运行确认 |
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

1. 触发 `TUI CI`（push / PR 命中 `tui/**` 即自动运行；或手动 `workflow_dispatch`）。看 job summary 或下载 artifact `tui-baseline-check` / `tui-baseline-test`。
2. `check`：看工具链是否按钉安装（`rustc --version --verbose` 应为 1.94.0）、依赖图节点数、`--workspace --tests` 的错误列表（macOS 上从未编译过的 `cfg(target_os = "linux")` 代码会在这里暴露）。
3. `test`：把每个意外失败分诊为「修」或「加入 `known_failures` / `skip` 并写明原因」；用 Linux 的通过数设置各 suite 的 `reference_passed` 与 `min_passed`。
4. 翻开开关：`[test] enforce = true`、`[check] enforce_workspace_tests = true`；按实测设置 `max_warnings`，并把 `[graph.max_nodes]` 的 `PROVISIONAL` 值换成 Linux 实测值。
5. 手动运行一次 `release_baseline`，把 `zypi` 体积、构建耗时与 `--version` 延迟填进 §3.1。
6. 把本页 Linux 一栏补全，并在计划文档把 0.1 / 0.2 勾选为完成。

## 6. 尚未覆盖

0.3（ACP 契约 / e2e 载体）、0.5（模块级调用图工具化）、0.6（入口审计）、0.7（磁盘读取点清单）、0.8（能力矩阵与 ACP 版本配对表）、0.9（实测结案）都不在本页范围；见计划 §7 阶段 0。「启动耗时」目前只有 `--version` 延迟，不含「到欢迎页」的时间，后者需要 PTY 载体。
