# Rust Agent Runtime 剥离计划：执行记录（归档）

> 这是[计划](../PLAN-RUST-AGENT-RUNTIME-REMOVAL.md)的执行记录：每一轮做了什么、怎么测的、数字是多少。计划本身（目标、架构、决策、阶段清单、状态）在计划文件里。
>
> **怎么读**
>
> - 编号沿用原文。本文里的 §10.x 是执行记录的节。§1–§3、§6–§9 和附录 A 指计划。附录 A.4（r5 的执行结果）、附录 B（复现方法）和修订记录在本文。
> - 附录 C 是计划 §7 在 r10 时的全文，含每一项的执行细节。附录 D 是计划头部在 r10 时的状态文字。附录 E 是计划 §4、§5 的全文（r2 的源码事实基线与改动规模估计）。三处只为留档；现状以计划为准。
> - 本文是历史记录。数字是当时的实测值，不再更新。要更正明显的错误，改在原处并注明。
> - 记新的一轮时：在「10.」末尾追加一节（`### 10.21 …`），在计划的 §10 索引里加一行，在计划里改状态。不要把过程写回计划。
> - 文中的分支名（`codex/…`）是当时的名字。这些分支在合并后已删除；提交都在对应的 PR 里。

## 目录

- [10. 执行记录（r3、r4、r5、r6、r7、r8、r9、r10、r11、r12、r13、r14、r15、r16、r17、r18）](#10-执行记录r3r4r5r6r7r8r9r10r11r12r13r14r15r16r17r18)
  - [10.1 范围、方法与规模](#101-范围方法与规模)
  - [10.2 `/model` 恢复（ADR3 r3）](#102-model-恢复adr3-r3)
  - [10.3 验证（r3 的实测）](#103-验证r3-的实测)
  - [10.4 未验证与残留](#104-未验证与残留)
  - [10.5 为 pi-rust 铺路](#105-为-pi-rust-铺路)
  - [10.6 r4 追加：阶段 0 的 CI 与基线、leader 残留、1.R3 命名](#106-r4-追加阶段-0-的-ci-与基线leader-残留1r3-命名)
  - [10.7 r5 追加：Linux CI、入口审计与阶段 A 收口](#107-r5-追加linux-ci入口审计与阶段-a-收口)
  - [10.8 r6 追加：品牌残留清理](#108-r6-追加品牌残留清理)
  - [10.9 r7 追加：合并 `main`（另一台机器推的 7 个提交）](#109-r7-追加合并-main另一台机器推的-7-个提交)
  - [10.10 r8 追加：Linux 补测（WSL2）](#1010-r8-追加linux-补测wsl2)
  - [10.11 r9 追加：阶段 0 收口（0.3 / 0.7 / 0.8）与阶段 1 里不需要拍板的部分](#1011-r9-追加阶段-0-收口03--07--08与阶段-1-里不需要拍板的部分)
  - [10.12 r10 追加：ADR1 的 PR 1（Python 一侧）](#1012-r10-追加adr1-的-pr-1python-一侧)
  - [10.13 r10 追加：1.P3 (a)——`zypi` 只提供 agent 真会照做的权限模式](#1013-r10-追加1p3-azypi-只提供-agent-真会照做的权限模式)
  - [10.14 r11 追加：调用图工具化（0.5）与阶段 C 收口](#1014-r11-追加调用图工具化05与阶段-c-收口)
  - [10.15 r12 追加：V0.5 的发布里程碑](#1015-r12-追加v05-的发布里程碑)
  - [10.16 r13 追加：ADR1 的 PR 2 与 PR 3](#1016-r13-追加adr1-的-pr-2-与-pr-3)
  - [10.17 r14 追加：空会话（决策 17）与四个答复](#1017-r14-追加空会话决策-17与四个答复)
  - [10.18 r15 追加：删 `pi-shell` 的旧存储层（决策 10）](#1018-r15-追加删-pi-shell-的旧存储层决策-10)
  - [10.19 r16 追加：合入 #25 与 #26，删 `pi-shell` 里剩下的死代码](#1019-r16-追加合入-25-与-26删-pi-shell-里剩下的死代码)
  - [10.20 r17 追加：检查测试之间的竞争，只改测试与工具](#1020-r17-追加检查测试之间的竞争只改测试与工具)
  - [10.21 r18 追加：改用 `cargo nextest`、`protoc` 的路径、`release.yml` 的令牌](#1021-r18-追加改用-cargo-nextestprotoc-的路径releaseyml-的令牌)
- [附录 A（摘录）：A.4](#附录-a摘录a4)
  - [A.4 r5 执行结果（对照 r5 时初稿里的「默认决策」；r9 的更新见 A.1）](#a4-r5-执行结果对照-r5-时初稿里的默认决策r9-的更新见-a1)
- [附录 B：复现方法](#附录-b复现方法)
- [附录 C：计划 §7 的逐项注释（r10 原文）](#附录-c计划-7-的逐项注释r10-原文)
- [附录 D：计划头部的状态文字（r10 原文）](#附录-d计划头部的状态文字r10-原文)
- [附录 E：计划 §4、§5（r2 的基线，原文）](#附录-e计划-45r2-的基线原文)
  - [4. 源码事实基线](#4-源码事实基线)
  - [4.1 运行路径](#41-运行路径)
  - [4.2 Rust runtime 与耦合面](#42-rust-runtime-与耦合面)
  - [4.3 Python 侧（`pi_agent_cli`，共 4,114 行，`agent.py` 472 行）](#43-python-侧pi_agent_cli共-4114-行agentpy-472-行)
  - [4.4 协议与版本](#44-协议与版本)
  - [4.5 验证基础设施与文档](#45-验证基础设施与文档)
  - [5. 改动规模](#5-改动规模)
- [修订记录](#修订记录)

## 10. 执行记录（r3、r4、r5、r6、r7、r8、r9、r10、r11、r12、r13、r14、r15、r16、r17、r18）

r3 执行了「拆除 Rust runtime + 恢复 `/model`」；r4 在其上补了阶段 0 的 CI / 基线、清理 leader 的 UI 残留和生命周期命名；r5 把分支推上远端、在 Linux 上启用基线，执行入口审计并完成阶段 A（§10.7）；r6 清理品牌残留（§10.8）；r7 合并 `main`（§10.9）；r8 在 WSL2 上补 Linux 一侧的实测（§10.10）；r9 补阶段 0 的收尾与阶段 1 里不需要拍板的部分（§10.11）；r10 做 ADR1 的 PR 1（§10.12）与 1.P3 的 (a)（§10.13）；r11 做 0.5 与阶段 C（§10.14）；r12 定 V0.5 的发布里程碑，并做了决策 16（§10.15）；r13 做 ADR1 的 PR 2 与 PR 3，并加了读盘门禁（§10.16）；r14 合入这两个 PR，并做决策 17：会话文件在第一条用户消息时才写（§10.17）；r15 做决策 10 的存储层一项：删 `pi-shell` 里的旧存储层（§10.18）；r16 合入 #25 与 #26，并删 `pi-shell` 里剩下的死代码（§10.19）；r17 检查测试之间的竞争，只改测试与工具（§10.20）；r18 改用 `cargo nextest`，改 `protoc` 的路径与 `release.yml` 的令牌（§10.21）。下表是 r3、r4 的提交，按当时的状态保留（当时都是本地提交、**未 push**），分支是 `codex/rust-agent-runtime-removal-plan`：

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
- r3 时阶段 0 全部未做：没有 Rust CI job、基线报告、ACP 契约 / e2e 载体、入口审计。所以「无回归」只能说成：编译通过，现有单测通过，上面列出的失败与改动无关。**r4 补注**：0.1 / 0.2 / 0.4 的工具与文档已补（见 §7 阶段 0 与 [`docs/baselines/tui.md`](../../baselines/tui.md)）；0.3（ACP 契约 / e2e）、0.5–0.9（调用图工具化、入口审计、磁盘读取点、能力矩阵、实测结案）仍未做。
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

**建议的后续顺序**（r4 更新；r5 的现状与顺序见 §10.7 末尾）：① 在 Linux 上跑第一次 `TUI CI`（0.1 / 0.2 已写好、未运行），把 `cfg` 代码的错误和失败清单跑出来，按 [`docs/baselines/tui.md`](../../baselines/tui.md) §5 收尾；② 1.R3 剩下的行为部分：`stderr` 去向、优雅退出（1.P2）、残留进程实测，配 PTY 烟测；③ 入口审计（0.6 → A.3），ADR4 的去留决定顺带处理共享会话协议的词汇；④ A.4 去掉全局 `#![allow]`；⑤ 文档（C.2 / C.3）；⑥ 视基线决定阶段 D。

### 10.5 为 pi-rust 铺路

- **ACP 是唯一接缝**：TUI 进程里不再有能创建 agent 或发起模型请求的代码路径。`pi-shell` 只剩 TUI 服务（配置、auth、trust、session registry client、模型目录、sampling 类型薄壳 `pi_shell::sampling`，后者只是对 `pi-sampling-types` 的重导出，不含请求逻辑）。
- **接入点**：agent 命令由 `pi-pager/src/acp/spawn.rs` 解析，顺序为 `PI_AGENT_COMMAND` → `$PI_HOME/{agent,config}.toml` 的 `[agent].command` → `$PI_PYTHON -m pi_agent_cli`。pi-rust 只需是一个讲标准 ACP（stdio JSON-RPC）的可执行文件，把命令指过去即可，不必改 TUI。
- **`-p` 不经过 Rust ACP client**：`pi-pager-bin` 的 `dispatch_python_print` 把 `-p` / `--prompt-json` / `--prompt-file` 及相关旗标原样转给 agent 命令，所以 pi-rust 要么实现同一组旗标，要么先定 1.P6 的旗标契约。
- **能力驱动的 `/model`**：agent 公布 `category: "model"` 的 select 配置项，TUI 就有 `/model` 并用 `session/set_config_option` 切换；没公布时只剩 legacy 的 `session/set_model` 兼容路径。pi-rust 实现这个配置项即可获得 `/model`。
- **保留的 crate**：`pi-tools`（141k 行）、`pi-workspace`（101k）、`pi-agent`（22.9k）仍在 pager 的依赖图里，因为 pager 用的是它们的渲染类型、权限模型与工具展示；它们不是 runtime。pi-rust 若要复用，应作为**独立 agent 进程**的依赖，而不是链回 `pi-pager`（P1）。

### 10.6 r4 追加：阶段 0 的 CI 与基线、leader 残留、1.R3 命名

**阶段 0.1 / 0.2 / 0.4（`3117858`）**：`.github/workflows/tui-ci.yml`（`check`、`test`、手动的 `release-baseline`）、执行器 `scripts/tui_baseline.py`、清单 `scripts/tui_baseline.toml`、报告 [`docs/baselines/tui.md`](../../baselines/tui.md)。0.4 的 deny-list 是阻塞门禁；0.1 / 0.2 没勾选，因为 workflow **从未在 Linux 上运行**——本机（macOS）上 `gates`、`check`、`test` 都跑通，工作流只经过 actionlint 与执行器的 18 个单测。测试与 `--workspace --tests` 在首次 Linux 结果出来之前只报告、不阻塞（清单里 `enforce = false`）。

**leader 的 UI 状态残留（`a648e92`，72 个文件，+346 / −2,684）**：
- 删除：`leader_mode`、`leader_roster`、`reconnect_pending`（生产里都恒为 false）及全部读取点；`Effect::FetchRoster`、`TaskResult::Roster*`；`StartupPhase::LeaderConnect`、`AgentKind::Leader`；`NextStep::RestartSharedLeader`（提示一个不存在的 `zypi leader kill`）；启动失败屏的尝试计数与 leader 行；`[cli].use_leader`、`RemoteSettings.leader_mode`、`AgentMode::Leader`；`pi-test-support` 的 leader 夹具与 UDS 故障代理（连带 3 个依赖）；内嵌 user-guide 与场景 YAML 里的 leader 段落。
- **生产行为不变**。唯一的逻辑改动是后台 follow-up 的路由：`immediate_server_send_eligible` 从 `(leader_mode || steer) && …` 变成 `steer && …`，而 `leader_mode` 恒为 false，等价于 `[ui].follow_up_behavior = "steer"`；默认仍是 `Queue`。
- **测试约定变了**：以前 pager 的测试夹具隐含 leader 的「立即发送」语义。现在夹具用生产默认值（Queue）；要验证「服务端权威的立即发送」的 15 个用例通过 `agent_view::test_fixtures::SteerFollowUp`（RAII，drop 时恢复缓存）显式切到 Steer。
- **旧配置**：已有的 `use_leader` / `leader_mode` 被静默忽略（`CliConfig` / `RemoteSettings` 没有 `deny_unknown_fields`），`merge_section` 保留未建模的键，`pi-config-types` 有回归测试。ADR5 的「首次启动告警一次」仍未做。
- 随被删代码一起删掉 18 个测试（`pi-pager` −17：重连守卫、名册、`leader_mode` 路径；`pi-shell` −1：`[cli].use_leader` 写入），基线数字相应下调，见 [`docs/baselines/tui.md`](../../baselines/tui.md) §3.2。
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
- 待处理：冷启动后 `/resume` 选择器的第一项是当前这个刚创建的空会话（标题现在是「(no messages)」），真正想恢复的要按一下 ↓；pager 不跟 `nextCursor`（见 1.P1）。（r14：第一半在决策 17 之后不再如此：没发过消息的会话没有文件，不在列表里，见 [§10.17](#1017-r14-追加空会话决策-17与四个答复)。）

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
| 5 | `session_startup.rs` `ensure_session_id_available` → `session_exists_for_cwd` | 旧布局 | `--session-id` 查重 | 看不见 Python 会话（GAP；r10：agent 已经会拒绝重复，这一处还在） | 交给 agent 拒绝重复（agent 一侧 r10 已做，见 §10.12） |
| 6 | `session_startup.rs` `parent_session_is_worktree` | 旧布局的 `summary.json`，再退到 git 检查 | `--worktree` 与被恢复的父会话 | 文件不存在，只剩 git 检查 | 删读文件的那一段 |
| 7 | `session_title_resolve.rs` `presandbox_resume_target`（由 `cli.rs` 的 `pin_local_resume_target` 调用） | 旧布局（`resolve_local_session*`、`local_summaries_for_cwd_sync`） | 沙箱施加前把 `--resume` 的目标钉成 id | 恒为 `Unresolved` | 删（见下「沙箱顺序」） |
| 8 | `cli.rs` `saved_resume_profile_for_cwd` → `resumed_session_sandbox_profile` | 旧会话保存的沙箱 profile | 恢复时沿用沙箱 | 恒为 `None` | 删 |
| 9 | `app/mod.rs`（`materialize_startup` 之后）`list_summaries(None)` | 全部旧布局 summary | 给被恢复的会话补标题 | 读不到，没有标题 | 用 `session/list` 带回的标题 |
| 10 | `dispatch/session/load.rs`（选择器 / `/resume` 加载） | `resolve_local_session*` | 本地命中就带上 `original_cwd` | 命中不了，走 ACP 那一支，用选择器那一行的 `cwd`——**已经是 ADR1 的形状** | 删前两个判断 |
| 11 | `effects/helpers.rs` `parse_session_picker_entries` | `extract_first_user_prompt` 读 `chat_history.jsonl`；对 `source == "remote"` 的行再调 `resolve_local_session_any_cwd` | 选择器行没有摘要时的退路；把远程行改标本地 | ACP 的行永远有标题（缺省用 id）、`source` 恒为 `local`，走不到 | 删 |
| 12 | `export_cmd.rs` `load_updates_for_replay` | 旧布局的 `updates.jsonl` | `zypi export <id>` | 找不到：`Session '<id>' not found.`，退出 1（GAP） | 见「决定 6」 |
| 13 | `app/agent_view/plan.rs` `plan_file_path` | `sessions/<编码后的 cwd>/<id>/plan.md` | Plan 预览 | 没有人写这个文件 | 随 Plan 的去留（决定 7） |

*(3) 现状实测：13 例 PTY 验收矩阵。* `scripts/tui_pty/resume_matrix.py`（`babd75f`）用 TUI 做出一个会话（mock 模型，第一条消息 `remember this`），再带着各旗标重新起 `zypi`，看它有没有做帮助文字承诺的事。WSL2 上 **9 PASS、4 GAP、0 FAIL**（r10 的 PR 1 之后是 **10 PASS、3 GAP、0 FAIL**，见 §10.12）；GAP 不让脚本失败，`--strict` 让它们失败——落地 ADR1 的改动要让 `--strict` 全绿。

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
| 10 | `--session-id <新 uuid>` 用这个 id 开新会话 | ~~GAP~~ → PASS（r10） | 原先 Python 忽略 `session/new` 的 `_meta.sessionId`，自己挑 id；PR 1 之后用这个 id |
| 11 | `--session-id <已用的 id>` 被拒绝 | **GAP**（r10 之后换了样子） | pager 的占用检查读旧布局，看不见 Python 的会话；原先于是用另一个 id 开了新会话，r10 之后 agent 拒绝（没有新会话），但 TUI 显示 `Session creation failed: Invalid params: {...}` 并留在界面里，没有退出 1（PR 2） |
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
| `--session-id <uuid>` | 带 `_meta.sessionId` 发 `session/new`；agent 拒绝重复，pager 按错误退出 1（需确认，决定 3） | **兑现 `_meta.sessionId`**：校验是 UUID，不存在就用它做会话 id（`JsonlSessionRepo.create` 接受 `options["id"]`，**但不拒绝已用的 id**——r10 实现时才发现，见 §10.12），已存在则 `invalid_params`；r9 估的「约 10 行」实际是约 60 行、一个 `has_session` 和 27 个测试（r10 已做） |
| `zypi export <id>` | 决定 6 | 看选项 |

**沙箱顺序（落地设计里最硬的一处）。** 今天 `--continue` 与 `--resume <标题或 id>` 在 `apply_sandbox` 之前，由 `PagerArgs::pin_local_resume_target`（`cli.rs`；调用 `session_title_resolve::presandbox_resume_target`）把目标钉成会话 id，再由 `saved_resume_profile` → `resumed_session_sandbox_profile` 去看该会话**保存的**沙箱 profile，让恢复的会话沿用创建时的沙箱；与 `--sandbox` 冲突时 `resolve_startup_sandbox` 报 `SandboxStartup::Conflict`（`pi-pager-bin/src/main.rs`）。这与 ADR1 正面冲突：沙箱不可逆，必须在 agent 起来之前施加；而按 ADR1 解析目标，要先起 agent。三条路：

1. **去掉「恢复时沿用保存的 profile」**：沙箱只由 `--sandbox` 与配置决定。
2. 先起一个不带沙箱的「查询用」agent 解析目标，再施加沙箱起真的那个：冷启动翻倍，还有一段没有沙箱的窗口。
3. 让 Python 把 profile 存进会话，经 `session/list` 的 `_meta` 公布：仍然要先起 agent，同 2。

**提议 1。** Python 会话从来不存 profile，`resumed_session_sandbox_profile` 对它恒返回 `None`（矩阵里 `--continue` 与 `--resume <id>` 两例都没读到过 profile），所以对现有会话**没有行为变化**，只是删掉一条对 Python 永远不生效的分支。可以整块删：`pin_local_resume_target`、`PinnedResumeTarget`、`resume_target_pinned`、`pinned_resume_profile`、`saved_resume_profile`、`SandboxStartup::Conflict`、`TitleResolution::PinnedPreSandbox`；「钉住」要防的竞争（两次标题查找之间被改名）在只查一次时不存在。代价：将来想要「会话记住自己的沙箱」，要另想办法（那也该是 agent 的事，并且要先解决「沙箱先于 agent」的顺序）。

**Python 一侧的两项要求（r10：两项都已做，见 §10.12；响应里的键是 `_meta["pi/cwd"]`）。** ① `session/new` 兑现 `_meta.sessionId`（见上）。② `session/load` 的响应带上会话的 cwd（放在 `_meta` 里）：今天 `load_session` 的响应只有 `configOptions` 与 `pi/*` 模型提示。选择器那一条路径已经把选中行的 `cwd` 传给 `session/load`；CLI 的 `--resume <id>` 今天传 `None`（表 #3），所以从别的目录 `--resume <id>` 时，agent 在会话的目录里干活，TUI 却在启动目录里（影响 `@` 搜索、diff、git 信息——**没有验证**，见下）。按本设计，`resolve_startup` 找到会话时就带着它的 `cwd`，两条路径一致，第 ② 项就只是保险。

**验收。** ① `resume_matrix.py --strict` 13/13 PASS（用例 8 收紧后）。② 门禁：把 `session::persistence`、`session::storage`、`resolve_local_session`、`list_summaries` 加进 `tui_baseline.py gates` 的 deny-list（和 `MvpAgent` 同一类），`pi-pager` 与 `pi-pager-bin` 的非测试代码里零命中。③ `python_agent_e2e.rs` 加用例：对一个有会话的 agent 解析 `--continue`、跨页的 `--resume <标题>`、未知 id 的错误、`_meta.sessionId` 被兑现与重复被拒。

**PR 切分（提议）。** PR 1（Python，不动 TUI）：`_meta.sessionId` 与 `session/load` 带 cwd，加到 `test_acp_stdio_contract.py`（r10 已在分支 `codex/adr1-pr1-python` 做完，PR #11，见 §10.12）。PR 2（Rust，只加不删）：`resolve_startup` 与它的 e2e，接上 `--continue`、`--resume`、`--session-id`；此时旧的读盘路径还在。PR 3（Rust，删）：沙箱顺序那一整块、`find_most_recent_jsonl_session`、#6–#11 的读盘、`list_summaries(None)` 补标题；`export_cmd` 按决定 6；再之后（阶段 B）删 `pi-shell` 的 `session::{persistence, storage}` 实现层。

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

- 新的 CI 步骤（装 Python agent、`PI_E2E_*`）与新增的路径过滤，在 GitHub 上没跑过；清单里 `pi-pager` 的 5,602 与整套的 8,659 是 WSL2 上本地运行的数，**要等第一次 CI 运行证实**。（r10：PR #10 的 CI 证实了，见 §10.12 开头。）
- Rust 的 e2e 与 PTY 脚本只在 Linux（WSL2）上跑过；macOS 与 Windows 没有。Windows 的延迟数只量了 Python 一侧，TUI 没有参与。
- 延迟：冷磁盘、网络文件系统、超过 1,000 个会话、真实形状的会话（量的是合成的 12 / 120 / 2,000 条消息）。
- **跨目录的 `--resume <id>`**：TUI 的当前目录与 agent 绑定的会话目录可能不同，`@` 搜索、diff、git 信息用哪个目录没有验证；选择器那条路径传了 `cwd`，CLI 那条传 `None`。
- 设置菜单里选 Default 的那条路径只有代码与 Python 一侧的断言，没有 PTY 走过；旗标除了 `--always-approve`、`--permission-mode`（`mode_probe.py`）与 `--continue` / `--resume` / `--session-id`（矩阵），没有逐个验证交互模式下是否生效，`-m` / `--model` 尤其没有查。
- 落地设计（`resolve_startup`、沙箱顺序、两项 Python 要求）是纸面设计，没有写一行代码。
- 全部用 mock，没有真实 LLM。

**留给用户决定的。** 十二项，每项给出我的倾向；没有一项已经动手。

1. **ADR1 的落地要不要开工，按上面的 PR 切分。** 覆盖条件没有触发。倾向：开工，先做 PR 1（Python 两项，不动 TUI，风险最小）。
2. **沙箱顺序。** 倾向：去掉「恢复时沿用保存的沙箱 profile」（对 Python 会话没有行为变化）。备选是先起查询用的 agent（冷启动翻倍、有无沙箱的窗口）。
3. **`--session-id` 的契约。** ACP v1 的 `session/new` 没有 session id 参数，`_meta.sessionId` 是 pager 与 agent 之间的约定。倾向：Python 兑现它，重复由 agent 以 `invalid_params` 拒绝。也可以直接删掉这个旗标。（r10：Python 一侧按倾向做了，见 §10.12；用户选的是开工 PR 1，等于认了这一条。）
4. **`--continue` 的「最近」。** Python 的列表按创建时间倒序（ACP 对排序只说「默认排序」）。选项：最近创建（取第一页第一条，最快）；最近活动（`updatedAt` 最大，要翻完所有页，1,000 个会话在 Windows 上约 0.8 s）。倾向：最近活动——用户续的通常是上次用过的，不是上次建的。
5. **未知 id 的报错时机。** `--resume <未知 id>` 现在进了 TUI 才报，状态栏的模型名还是 `unknown`。倾向：进 TUI 之前报错退出 1（`resume_matrix.py` 的用例 8 随之收紧）。
6. **`zypi export`。** (a) 经 ACP 回放导出：起 agent、`session/load`、收 `session/update` 回放，复用现成的 `render_blocks_to_markdown`，约 100 行，多 1–2 s 的 agent 启动；(b) 删掉这个子命令；(c) 维持现状——对 Python 会话恒报 not found，一个静默坏掉的命令。r8 时用户选了不动。倾向：(a) 或 (b)，不要 (c)。
7. **模式映射（1.P3）。** 依据是上面的四个事实。(a) 立刻隐藏 Plan、Auto、Default（Shift+Tab 只剩 Normal ↔ Always-Approve），并让 `--permission-mode` 只接受有效果的值（`bypassPermissions`；`default` 等于不带旗标），其余值报错说明，不动 agent；(b) 做 `session/set_mode`：Python 实现 `set_session_mode` 并公布 `modes`，Plan = 把工具集裁到只读；(c) 其他。Python 的 `auto` 也要定义：今天等于 always-approve，设置页却说是分类器——改名、删除，或真做分类器。倾向：(a) 先行，因为 Plan 现在给用户的是虚假的安全感；(b) 另议。（r10：(a) 按倾向做了，用户选的是 `mode_a`，见 §10.13；(b)、(c) 仍待定。）
8. **旗标契约（1.P6）。** `--system-prompt-override`、`--rules` 在交互模式下无效；`--allow` / `--deny` 没有传给 agent；`--permission-mode` 的六个值里只有 `bypassPermissions` 有效（r10：不再接受没有效果的四个，见 §10.13）；`-m` 的路径没有查清。选项：Python 读 `initialize._meta`（前两项各几行）；或 pager 在对着 Python agent 时拒绝这些旗标并说明；`-p` 另行定义。要用户定哪些旗标属于产品契约。
9. **选择器的 30 天截止。** 倾向：去掉（agent 已分页，不怕多）；或者改成可配置。
10. **清理。** `load_mcp_servers` / `mcp.rs`（1.P4 之后没有调用者）、`recent_dirs.rs`（只剩一行文档注释）；ADR1 落地后的 `pi-shell` 的 `session::{persistence, storage}` 实现层（阶段 B）。
11. **选择器边翻边显示。** 现在等所有页回来再画（1,000 个会话约 0.8 s，Windows）。倾向：不做，除非有人遇到。
12. **推送与 PR。** 分支 `codex/phase1-contract-docs`（16 个提交，叠在 PR #9 上）要不要推、怎么切。倾向：两个 PR——代码（1.P1 / 1.P4 / 1.P7、e2e、退出提示）与脚本加文档，或者一个。推之前按仓库规则先跑 Ruff 与两个平台的 pytest（见「验证」）。注意 `tui-ci.yml` 现在改 `pi_agent_cli/**` 也会跑；新的 e2e 步骤与 5,602 要等第一次 CI 运行证实。PR #9 若先被 squash 合并，用 `git rebase --onto origin/main <PR #9 的旧头> codex/phase1-contract-docs` 摘下来。

### 10.12 r10 追加：ADR1 的 PR 1（Python 一侧）

用户在 r9 之后选了三件事：`push_pr`（推送分支并开 PR）、`adr1_pr1`（开工 ADR1 的 PR 1，本地提交、不推送）、`mode_a`（1.P3 的 (a)，同样本地提交、不推送）。PR [#10](https://github.com/zy1233/pi-python/pull/10) 是 `push_pr` 的结果（16 个提交，叠在 PR #9 之上）。另外两件各在一个本地分支上，线性叠在 PR #10 的分支 `codex/phase1-contract-docs` 之上：`codex/adr1-pr1-python`，再是 `codex/phase1-mode-a`（§10.13）；这样 PR #10 仍然只含「不需要拍板的」部分。这一轮做完时两个分支都在本地；用户随后选了 `push_stack`（推送这两个分支，各开一个 PR，叠在 #10 之上），并要求把已处理的 PR 合并进 `main`：它们是 PR [#11](https://github.com/zy1233/pi-python/pull/11)（`codex/adr1-pr1-python`，base 是 `codex/phase1-contract-docs`）与 PR [#12](https://github.com/zy1233/pi-python/pull/12)（`codex/phase1-mode-a`，base 是 `codex/adr1-pr1-python`）。

**PR #10 的 CI（r9 留下的「要等第一次运行证实」，全部证实）。** 7 项检查：Ruff、`Gates + cargo check`（7 分 9 秒）、`cargo test (Linux x86_64)`（23 分 19 秒）、pytest 的 Python 3.11 / 3.12 / 3.13 各约 2 分钟，全部通过；「Release build baseline」按设计只手动触发，跳过。`cargo test` 的 10 个 suite 合计 **8,659 通过 / 0 失败 / 22 忽略**，与 WSL2 上的数完全一致，`pi-pager` 是 5,602（13 忽略）；`python_agent_e2e` 的 10 个用例在 CI 里真的跑了（装 Python agent 的新步骤与 `PI_E2E_*` 起了作用）。清单里 `pi-pager` 的 `reference_passed` 5,602 由此从「本地数」变成「CI 的数」。PR 的状态：open、可合并，等用户处置。

**PR 1（`codex/adr1-pr1-python`）：Python 兑现 `_meta.sessionId`，响应带 `pi/cwd`。**

*做了什么。*

- `session/new` 把 `_meta.sessionId` 当作会话 id。SDK 把请求的 `_meta` 摊平成关键字参数（`acp/router.py`），所以 `new_session(**kwargs)` 里直接是 `kwargs["sessionId"]`。必须是 `uuid.UUID` 读得懂的 UUID（大写、花括号、没有连字符、`urn:uuid:` 前缀都行；Rust 一侧用 `Uuid::try_parse`，Python 的更宽松，比如连字符放哪都行，没有逐个比较，pager 只会发它自己解析过的），存的是规范形式（小写、带连字符）；其他值——空串、`../../outside`、短 UUID、数字、布尔、列表、字典——一律 `invalid_params`（id 会进文件名，不能放行）。
- 已被某个会话使用的 id → `invalid_params`（`{"sessionId", "reason": "a session with this id already exists"}`），不管那个会话属于哪个目录。同一个 id 的并发请求由一把 `asyncio.Lock` 串行化：5 个并发请求只建 1 个会话，其余 4 个被拒。两个 agent 进程共用一个 home 不在保护范围内。
- `session/new`、`load`、`resume` 的响应带 `_meta["pi/cwd"]`：会话的工作目录。`load` / `resume` 绑定的一直是会话保存的 `cwd`（没有才用请求的）；现在把它告诉客户端，`zypi --resume <id>` 从别的目录起时，客户端可以知道真正的目录。pager 还没有人读它（PR 2）。
- harness 新增 `JsonlSessionRepo.has_session(id)`（为什么需要，见下）。

*实现时发现、与 r9 设计不同的地方。*

- **`create` 接受 id，但不拒绝已用的 id。** r9 写「`JsonlSessionRepo.create` 已经接受 `options["id"]`，约 10 行」。它接受，但只检查「这个确切的文件路径是否已存在」；文件名以创建时间开头，所以同一个 id 的第二个文件放得下，两个会话并存，之后 `session/load` 命中哪个都有可能。重复检查因此必须在 agent 一侧，并且要串行化。实际是约 60 行加 27 个测试，不是 10 行。
- **最直接的重复检查是 `find`，但它对「没有人用的 id」要读完所有文件头——偏偏这是常态。** 第一版就是这么写的，量出来：5,000 个会话时 `find` 一个空闲 id 要 1,660 ms（Windows）/ 1,641 ms（WSL2），`session/new` 带 id 从 21 ms 涨到 1,850 ms。所以加了 `has_session`：文件名是 `<时间戳>-<id>.jsonl`（仓库自己写的格式）的，名字已经说明属于别的会话就不打开；名字不是这个格式的（别人放进去的）才读文件头。代价：一个被手工改名、名字带着另一个 id 的文件，会被当作名字说的那个 id（docstring、`AGENTS.md` 和一个测试里都写明了）。

*实测*（`scripts/session_list_bench.py`，合成 home，每项 3 次中位数，毫秒；`new` 是 `session/new` 不带 id，`new id` 是带一个没人用的 id，后两列在进程内，问的都是一个没人用的 id）：

| 平台 | 会话数 | `new` | `new id` | `has_session` | `find` |
|---|---|---|---|---|---|
| Windows 10 / NTFS | 100 | 21 | 36 | 14 | 46 |
| Windows 10 / NTFS | 1,000 | 20 | 86 | 70 | 335 |
| Windows 10 / NTFS | 5,000 | 21 | 351 | 360 | 1,743 |
| WSL2 / ext4 | 100 | 9 | 16 | 7 | 31 |
| WSL2 / ext4 | 1,000 | 10 | 74 | 64 | 308 |
| WSL2 / ext4 | 5,000 | 9 | 289 | 346 | 1,573 |

`has_session` 剩下的成本几乎都是「列目录」。一次性的分解（没有入库，5 个文件、每项 5 次中位数）：5,000 个文件时 `os.listdir` 11 ms（Windows）/ 1.9 ms（WSL2），`LocalExecutionEnv.list_dir`（每个条目造一个 `FileInfo`）372 / 286 ms，`JsonlSessionRepo._session_names` 336 / 282 ms，`has_session` 326 / 293 ms。`session/list` 的每一页、`find`、`session/load` 付的是同一笔：5,000 个会话时首页 404 ms（Windows）/ 319 ms（WSL2），其中列目录 336 / 282 ms。这是新发现，没有动，见下面的提议 13。

*对着真 zypi（WSL2，`resume_matrix.py`）。* **10 PASS / 3 GAP / 0 FAIL**（r9：9 / 4 / 0）。用例 10（`--session-id <新 uuid>` 用这个 id）由 GAP 变 PASS。用例 11（已用的 id 被拒绝）仍是 GAP，但样子变了：pager 的占用检查读旧布局，看不见 Python 的会话，于是照常发 `session/new`；agent 现在拒绝它（没有新会话；原先是悄悄用了另一个 id），TUI 显示 `Session creation failed: Invalid params: {"sessionId": "...", "reason": "a session with this id already exists"}` 并留在界面里，没有退出 1。要在 PR 2 里改成进 TUI 之前就报错退出。用例 5（`--resume <标题>`）与 12（`export`）不变。矩阵脚本里用例 10 的 `gap` 去掉了，用例 11 的 `gap` 改写成上面的样子，`test_tui_pty.py` 里一条只对「用例 10 有 gap」成立的断言删了。

*验证。*

- 新增测试 27 个。`test_session_chosen_id.py` 18 个：id 被兑现且重启后可见；不带 id 时用自己的 id、多余的 `_meta` 被忽略；规范形式的 4 种写法；8 种非法值（`""`、`not-a-uuid`、`../../outside`、短 UUID、`42`、`True`、列表、字典）都是 code −32602 且没有文件；已用的 id 换一种写法、重启之后、从另一个目录都被拒绝且文件不变；5 个并发请求只建 1 个；三个响应都有 `pi/cwd`，且保存的 `cwd` 压过客户端的。`test_acp_stdio_contract.py` 里 2 个线上用例（真 agent 进程：兑现 id、重复与 `"nope"` 被拒绝、重启后列表里只有一个、`load` 报告的目录）。`test_harness_session_page.py` 里 7 个 `has_session` 用例：只打开名字对得上的文件、名字不说明归属的文件要打开、末尾相同的 id 不混、名字对但文件头不对、手工改名的盲区、不是会话的文件（坏文件、空文件、目录、列出后消失的文件）、目录不存在。
- **变异检查**（改一行、跑测试、还原；11 个）：去掉并发锁 → 并发用例失败；永不拒绝已用的 id → 「已用的 id」用例与线上用例失败；不校验 UUID、不转规范形式 → 规范形式用例与线上用例失败；去掉 `pi/cwd` → 三个响应的用例与线上用例失败；`load` / `resume` 用客户端的 `cwd` → 同一个用例与线上用例失败；`has_session` 打开每个文件、`has_session` 从不打开名字对得上的文件 → `has_session_opens_only_the_file_that_has_the_id` 失败。每一项都被至少一个测试抓住。
- Ruff（`check`、`format --check`）通过。pytest 全量：Windows **2,378 通过 / 36 跳过 / 31 取消选择**（289 s），Linux（WSL2，ext4 克隆里的 `.venv`）**2,379 通过 / 35 跳过 / 31 取消选择**（102 s）；两边的差别是各自平台的跳过项（POSIX 信号与进程组、Windows 路径、沙箱的平台层），`real_llm` 的 31 个按 `addopts` 不选。`scripts/tests/test_tui_pty.py` 有 5 个用例在没有 `pyte` 时跳过，上面两个全量里它们都是跳过的；带 `pyte` 重跑（借 pty venv 的 `site-packages`，只读）19 个全过。

*没有验证 / 留下的。*

- pager 不读 `pi/cwd`，也没有对 `--session-id` 的重复做预检（PR 2）。
- 两个 agent 进程共用一个 home 时，同一个 id 的并发请求仍可能各建一个会话（锁只在进程内）。
- `has_session` 对「手工改名的文件」的盲区。别的工具（比如 TS 版 pi）写进这个目录、名字格式不同的文件，会因为「名字不认识就打开」被读到，这一支有测试，但没有拿真的 TS 文件试过。
- 全部用 mock，没有真实 LLM；zypi 的运行只在 Linux（WSL2）上做了，Windows 没有（PTY 脚本只有 Linux）。

**新的提议（13）。** 会话目录的列举太慢：`LocalExecutionEnv.list_dir` 每个条目造一个 `FileInfo`，5,000 个文件 286–372 ms，而 `os.listdir` 是 2–11 ms；`session/list` 的每一页、`find`、`has_session` 都付这笔钱。倾向：给仓库一条只列名字的路径（`LocalExecutionEnv` 加一个只返回非目录条目名字的方法，`JsonlSessionRepo._session_names` 有就用）。约 25 行加测试；按「首页 − 列目录 + `os.listdir`」估算，5,000 个会话时首页会从 404 / 319 ms 降到约 80 / 40 ms（Windows / WSL2），这只是估算，没有实现也没有量。没做：不属于 PR 1 的契约，也会碰 harness 的文件系统抽象。

### 10.13 r10 追加：1.P3 (a)——`zypi` 只提供 agent 真会照做的权限模式

用户选的 `mode_a`（§10.12 第一段）：决定 7 的 (a)，不动 agent。分支 `codex/phase1-mode-a`，叠在 `codex/adr1-pr1-python` 之上；做完时只在本地，随后推送成 PR [#12](https://github.com/zy1233/pi-python/pull/12)。

**为什么。** r9 量出的三件事（§10.11「模式切换与权限的实测」）：Plan 不限制任何东西，界面却说 `plan`；Auto 在 Python 里等于 Always-Approve，界面却说有分类器；设置里的 Default 被 Python 丢掉，在 Always-Approve 之后选 Default，界面说 Default，agent 仍不问。三处都是界面承诺的比 agent 做的多，而且都错在「用户以为更安全，实际更不安全」的方向。Python 的 agent 只有两种行为：问，或者全批准。

**做了什么**（只改 `pi-pager`；Python 一行没动；`pi-shell` 里给 agent 定义 frontmatter 用的 `PermissionMode::VALID_VALUES` 也没动）。

1. **三个常量。** `app/agent_modes.rs` 里的 `PLAN`、`AUTO`、`DEFAULT`，都是 `false`，意思是「agent 会不会照做」；模块文档写了上面三件事。`AppView` 的 `plan_mode_gate`、`auto_mode_gate`（原来就有，现在还要 `AUTO` 为真）、`default_mode_gate`，和设置页快照里镜像的 `PagerLocalSnapshot::{plan_mode_gate, default_mode_gate}`（`Default` 为 `false`）都由它们定。藏，不删：测试夹具把门打开，被藏起来的路径连同它们原来的测试还在跑；翻一个常量，那个模式带着自己的测试回来（决定 7 的 (b)、(c)）。
2. **Shift+Tab。** 没有 Plan（`!plan_mode_gate`）时，`dispatch_cycle_mode_inner` 交给 `dispatch_cycle_mode_without_plan`：Normal → Always-Approve → Normal，`auto_mode_gate` 开着时 Auto 夹在中间。其余照旧：欢迎页（还没有会话）写 `SessionCreated` 要读的旗标，有会话时走 `set_yolo_mode_inner`，两种都发 `PersistPermissionMode`；受管策略把 Always-Approve 钉死时停在 Normal 并弹策略的警告；pager 里残留着 Plan 旗标时经 Normal 离开，并发 `session/set_mode(default)`。下一站由纯函数 `next_ring_step_without_plan` 决定，有表驱动的测试。动作的说明改成 `Cycle mode (Normal / Always-approve)`。
3. **设置页与提示。** `plan_mode` 行与「显示上下文提示」下的 `plan_mode` 子项在没有 Plan 时不出现（`setting_gated_off_by_agent`）；`permission_mode` 的选择器没有 Default 与 Auto（`enum_choice_gated_off`），说明文字改成「Ask prompts for each tool action; Always approve grants all permissions automatically.」。`set_plan_mode`（设置行的入口）在门关着时什么也不做；`set_permission_mode` 把 Auto / Default 降成 Ask；劝用户进 Plan 的一次性提示（plan nudge）不再出现。
4. **启动。** 旧版本存在 `config.toml` 里的 `permission_mode = "auto"` 或 `"default"` 显示成 Ask（既有的 `downgrade_displayed_auto_if_gated`，加上新的 `downgrade_displayed_default_if_gated`），**文件不改**，用户下次切模式时才写。交互模式启动的软默认是 Auto（`effective_auto_for_launch_interactive`），现在经 `agent_modes::launch_auto`，agent 不认 Auto 时恒为假。
5. **`--permission-mode`。** 自定义的 `value_parser`（`parse_permission_mode_flag`）只收 `default`（等于不带旗标）与 `bypassPermissions`（等于 `--always-approve`）；`plan`、`auto`、`acceptEdits`、`dontAsk` 以退出码 2 拒绝，消息说明原因并列出有效值。`acceptEdits` 与 `dontAsk` 在 pager 与 agent 里都没有对应物，永远不收；`plan` 与 `auto` 随两个常量回来。

*实现时发现的。*

- **Plan 的入口只有三个**：Shift+Tab、设置页的 `plan_mode` 行、`--permission-mode plan`；非测试代码里没有 `/plan` 斜杠命令。`/auto` 本来就由 `auto_mode_gate` 管，不用另外处理。
- **光藏选项不够，Default 要有一道启动时的降级。** `default` 是 resolver 唯一从磁盘保留下来的拼写：存着它的用户，启动后会看到 Default，却再也选不到它。
- **门要放在漏斗上。** `apply_contextual_hints` 是所有「上下文提示」开关的唯一入口，Plan 提示的开关在那里夹住，不在每个读它的地方各夹一次。
- **三个旧测试假设了被藏起来的东西可见**：`rows_contain_categories_and_settings_through_pr_14`（期望列表里有 `plan_mode`）、`expanded_description_wraps_to_modal_width`（断言旧的说明文字）、`enum_choice_gated_off_covers_voice_and_permission`（旧的选项集合），按新行为改了。其余旧测试靠夹具把门打开，没有动。

**实测**（真 zypi，WSL2，`mode_probe.py` 改后的版本；改前 12 行的表在 §10.11）。

| 选了什么 | 状态栏 | 问了吗 | 没人回答就写了吗 | 答「允许」后写了吗 |
|---|---|---|---|---|
| Normal（什么都没选） | （无） | 问 | 否 | 是 |
| `--always-approve` | `· always-approve` | 不问 | **是** | — |
| Shift+Tab ×1：Always-Approve | `mock · always-approve` | 不问 | **是** | — |
| Shift+Tab ×2：回到 Normal | `mock` | 问 | 否 | 是 |
| Shift+Tab ×3：又是 Always-Approve（环里只有两站） | `mock · always-approve` | 不问 | **是** | — |
| `--permission-mode default` | （无） | 问 | 否 | 是 |
| `--permission-mode bypassPermissions` | `· always-approve` | 不问 | **是** | — |
| `config.toml` 里存着 `permission_mode = "auto"` | （无） | 问 | 否 | 是 |
| 存着 `"default"` | （无） | 问 | 否 | 是 |
| 存着 `"always-approve"` | `· always-approve` | 不问 | **是** | — |

- **被拒绝的四个值**（`plan`、`auto`、`acceptEdits`、`dontAsk`）：都是退出码 2，不起 TUI，消息是 `error: invalid value 'plan' for '--permission-mode <MODE>': the agent does not act on `plan`, so the flag would change nothing (valid values: default, bypassPermissions)`（值换成各自的）。r9 时这四个值被接受，什么也不做。
- **设置页**（PTY 里打开设置、搜索 `plan`、打开权限选择器）：没有「Plan mode」行；选择器的选项是 Ask、Always approve。
- 三行 `config.toml` 是新场景，没有改前的对照；Shift+Tab ×3 在 r9 是 Plan（环上还有 Auto），现在又回到 Always-Approve；其余六行与 r9 的对应行一致。

**验证。**

| 检查 | 结果 |
|---|---|
| 10 个 suite 整套（基线执行器，清环境，WSL2，增量编译，退出码 0，1,385 s） | ✓ **合计 8,683 通过 / 0 失败 / 22 忽略**（= PR #10 的 CI 的 8,659 + 24）。`pi-pager` 5,626（13 忽略），其余 9 个 suite 与 CI 的数相同。这一轮之后只改了 `settings_e2e` 里一个用例的内容（加 Up，见表后），用例数不变，该目标单独重跑：254 通过 / 0 失败 |
| 新增的 Rust 测试 24 个 | 21 个 lib：`agent_modes::tests` 2、`app::cli` 3、`dispatch/tests/modes.rs` 13、`settings_modal/tests.rs` 3；3 个 `settings_e2e`（`hidden_modes_*`）。另有 3 个旧测试按新行为改了 |
| 变异检查（改一处、跑 `agent_modes` / `cli` / `modes` / `settings_modal` / `settings::` / `actions::` 的 lib 测试与 `settings_e2e`、还原；12 处，只改 WSL 里的克隆） | ✓ 每一处都被至少一个测试抓住，分三轮：A 8 处、B 4 处、C 把 M7 再测一遍。两个发现见表后 |
| `mode_probe.py`（WSL2，真 zypi） | 10 个场景、4 个被拒绝的值、设置页，结果见上表 |
| Python 全量 pytest（`real_llm` 除外） | ✓ Windows（Python 3.12.7）：**2,382 通过 / 0 失败 / 36 跳过 / 31 取消选择**，334 s；Linux（WSL2 的 ext4 克隆，Python 3.12.3）：**2,383 通过 / 0 失败 / 35 跳过 / 31 取消选择**，112 s。都比 §10.12 多 4 个，是 `test_tui_pty.py` 里 `mode_probe` 的新用例；该文件现有 23 个，没有 `pyte` 时 5 个跳过，借 pty venv 的 `pyte` 单独跑 23 个全过 |
| `ruff check .`、`ruff format --check .` | ✓ 项目 venv 的 Ruff 0.16.10 与 CI 钉的 0.16.0（`uvx --from ruff==0.16.0`）：`ruff check .` 全过，`ruff format --check .` 227 个文件已格式化 |

**变异检查的内容与两个发现。** 12 处是：M1 `--permission-mode` 的值里加回 `plan`；M2 Shift+Tab 回到带 Plan 的环；M3 `set_plan_mode` 在门关着时照样生效；M4 `set_permission_mode` 不再把 Default 降成 Ask；M5 `apply_contextual_hints` 不再夹住 `plan_mode`；M6 `plan_mode` 的设置行重新出现；M7 选择器重新提供 Default；M8 `launch_auto` 不看 `AUTO`；M9 存着的 `default` 不再显示成 Ask；M10 Always-Approve 之后不回 Normal；M11 `--permission-mode` 什么值都收；M12 新建的 `AppView` 提供 Plan。抓住它们的测试都是上面「新增」的那些，M6 还被旧的 `rows_contain_categories_and_settings_through_pr_14` 与 e2e 的 `hidden_modes_plan_settings_are_not_offered` 抓住。两个发现：

- **一个 e2e 用例是瞎的，补了。** B 轮里 M7 被两个 lib 测试抓住，`settings_e2e` 的 `hidden_modes_permission_picker_commits_only_ask_or_always_approve` 却没有：选择器打开时停在当前值 Ask，Default 排在 Ask 前面，只按 Down 看不到它。用例加了 Up（从 Ask 按 6 次 Up 必须仍是 Ask，Down 一次再 Up 6 次也是），C 轮里同一处变异以 `left: Default, right: Ask` 失败；还原后 `settings_e2e` 254 通过 / 0 失败。
- **第一个 M5 让 rustc 崩了。** 把 `resolved.plan_mode &= self.plan_mode_gate;` 换成 `let _ = self.plan_mode_gate;`，rustc 1.94.0 在 `mir_borrowck` 里 panic（ICE，没有进一步缩小原因），所以第一次 A 轮什么也没测成；M5 改成 `resolved.plan_mode &= true;` 后重跑 A 轮，8 处都被抓住。每一轮之后克隆里没有留下 `.mut-orig-*` 文件，Windows 的工作树没动。

**没有验证 / 留下的。**

- **搜索 `plan` 时设置页仍会列出「Show contextual hints」这一组**：组把子项的关键字并进自己的，而搜索在隐藏之前做。纯外观，没改；`permission_mode` 的关键字里也还有 `classifier`、`auto`。
- `--no-plan`（"Disable plan mode"）仍在 `--help` 里（决定 8 的一部分）。
- pager 内嵌的上游用户指南（`docs/user-guide/03-keyboard-shortcuts.md`、`19-plan-mode.md`、`22-permissions-and-safety.md` 与教程 `07-plan-and-permissions.md` 等）仍按旧的环描述 Plan 与 Auto；它们随二进制一起打包，启动时被解压到 `<home>/docs/user-guide/`（`docs.rs` 的注释：为了让模型能从磁盘读到它们）。没有改，也没有查 Python agent 的模型会不会读到。
- 设置页里别的权限项（`remember_tool_approvals`、`default_selected_permission` 等）对 Python 是否有效没有验证；A.1 写明工具调用的选项只有 allow-once / reject-once，多半没有效果（候选 14）。
- 「残留的 Plan 旗标」的离开路径只有单测，PTY 里没走过：Python agent 下我没有找到能让这个旗标出现的入口（Shift+Tab、设置行、`--permission-mode plan` 都已封住），所以也没法在真 zypi 上造出这个状态。
- 对真 zypi 只在 Linux（WSL2）上跑过，macOS 与 Windows 没有；全部用 mock，没有真实 LLM。
- 决定 7 的 (b)（做真的 Plan）与 (c)（Python 的 `auto` 怎么定义）没有动。

**新的候选（14）。** 设置页里其余对 Python 没有效果的项：像这次对模式做的那样，逐个对着真 agent 测，再决定藏起来还是接线。倾向：先测 `remember_tool_approvals` 与 `default_selected_permission`，它们离权限最近，错了的代价最高。没做：不属于 (a)，也还没有证据。

### 10.14 r11 追加：调用图工具化（0.5）与阶段 C 收口

用户让「不用等决定」的事直接做：0.5（调用图工具化）和阶段 C 的 C.1–C.4（运行时验收、内嵌用户指南、`NOTICE`、文档收口）。他还说这台机器的 `~/.pi-python` 里配了真实的 LLM，需要时可以用。分支 `chore/phase-c-closeout`，叠在本地 `main` 的 `df812d1` 上（它比 `origin/main` 的 `ef03afd` 多 4 个提交：计划与基线文档的整理，没有推送）。**做完时只在本地，没有推送**：`main` 受保护，要走 PR。

真模型的 key 只通过环境变量交给子进程。它没有被打印，没有写进文件、日志或文档。文档里也不写模型服务的主机名与端口。

| 提交 | 内容 |
|---|---|
| `6bb1978` | 0.5：`scripts/tui_callgraph.py` 与单测（35 个） |
| `da56ef9` | C.3：`scripts/tui_notices.py`、`tui/NOTICE`、`tui/THIRD-PARTY-NOTICES` |
| `4fadcf3` | C.2：内嵌用户指南缩成 7 页，删教程叠层，启动不再解压 |
| `c034b68` | C.1：`net_audit.py`、`status_line_probe.py`、`status_proxy_agent.py` 与单测 |
| `65b16ea` | C.4：`config.toml` 的约束、`TUI-AND-CODE-AGENT.md`、Phase 4 spec 的修订说明、DESIGN §8.5 |
| `214c553` | C.4：`grok agent` 清零，残留词的棘轮门禁 |
| `24907ef` | 保留清单、残留词表，`pi-pager` 的基线 5,608 |
| `71286ed` | 0.5：按编译器的检查改工具（单测 39 个） |
| 本节所在的提交 | r11 的文档：计划、基线、本文件、`CHANGELOG.md` |

到 `71286ed` 为止，相对 `df812d1`：`tui/` 改了 60 个文件（+997 / −15,571 行）；`scripts/` 与它的测试 12 个文件（+4,547 / −14）；文档与 `packages/` 10 个文件（+540 / −22）。

**0.5：`scripts/tui_callgraph.py`。**

*是什么。* 只用标准库，只读源码文本：不要 cargo，不联网，Windows、Linux、macOS 上的答案一样。三个子命令：

- `crates`：crate 的依赖闭包。workspace 有 78 个 crate。`pi-pager-bin`、`pi-pager`、`pi-pager-minimal`、`pi-update` 的闭包里有 74 个；闭包外的 4 个是 `pi-markdown-fuzz`、`pi-test-support`、`pi-test-utils`、`pi-tracing-macros`。`--without pi-shell` 说去掉 `pi-shell` 之后还有 63 个可达；只经它可达的 10 个是 `pi-bundle`、`pi-chat-state`、`pi-compaction-transcript`、`pi-env`、`pi-http`、`pi-shell-base`、`pi-shell-terminal`、`pi-subagent-resolution`、`pi-system-power`、`pi-workflow`。`--who-uses` 列直接依赖者。
- `units`：一个或几个 crate 里，哪些模块还有活代码在引用。**单元**是模块路径的前两段（`util::config`）。**活**是被活代码直接或间接引用；上面四个产品 crate 整个算活。`--ban` 回答「这个单元没了会怎样」：列出断点，和随它一起死掉的单元。
- `keep`：把 support 的 7 个 crate 写成 [`docs/baselines/tui-support.md`](../../baselines/tui-support.md)。它是生成的，不手改；取代原来「举例」式的清单。

算作引用的有：`use`；以 `crate::`、`self::`、`super::` 起头的路径；以别的 crate 起头的路径；以当前模块的子模块起头的路径；属性字符串里的路径（`serde(with = "crate::util::serde_base64")`）。工具先把注释与字符串抹掉再读，所以写在里面的不算；`#[cfg(test)]` 里的引用也不算。

*结果*（`24907ef` 的树）。support 的 7 个 crate 共 305,581 行非测试代码，其中 17 个单元、6,190 行（2 %）没有活代码引用。

| crate | 非测试行 | 报死的单元 | 报死的行 | 是哪些（行数） |
|---|---|---|---|---|
| `pi-shell` | 40,985 | 7 | 1,006 | `session::events` 589、`session::telemetry` 174、`session::unified_list` 103、`config::reloader` 77、`auth::flow` 45、`upload::turn` 17、`upload` 1 |
| `pi-sampling-types` | 12,043 | 0 | 0 | — |
| `pi-tools` | 130,727 | 2 | 320 | `implementations::editor_infra` 276、`types::error` 44 |
| `pi-agent` | 22,902 | 1 | 24 | `prompt::browser_verification` 24 |
| `pi-workspace` | 81,282 | 1 | 462 | `publish` 462 |
| `pi-hooks` | 9,026 | 5 | 4,339（48 %） | `runner::command` 1,971、`dispatcher` 1,224、`runner::http` 896、`runner` 176、`result` 72 |
| `pi-mcp` | 8,616 | 1 | 39 | `wire` 39 |

`pi-hooks` 是最大的一块。它只被 `pi-agent` 与 `pi-workspace` 带进来，自己的 9,026 行里有将近一半没有人用。

*用编译器查准确度。* 工具说「死」只是估计。查法：把 `tui/` 拷到 ext4 上的副本里，删掉每个报死的单元的文件和父模块里的 `mod` 行，再在副本里跑 `cargo check --locked --keep-going -p pi-pager-bin -p pi-pager-minimal -p pi-update`（三个产品 crate）。第 4 轮另跑 `--workspace --tests --exclude pi-fast-worktree`，这与基线执行器的 `check` 是同一条命令。删文件的脚本是一次性的，没有入库。一共五轮：

| 轮 | 副本里删了 | `cargo check` | 为什么 | 随后改工具 |
|---|---|---|---|---|
| 1 | 21 个单元（被删文件共 7,957 行） | 失败：`pi-tools` 6 个错误 | `util::serde_base64`：路径写在属性的字符串里，1 个错误；`types::error`：`?` 要用的 `impl From<…> for ToolError`，5 个错误 | — |
| 2 | 19 个（7,740 行；留下上面两个） | 失败：`pi-sampling-types` 4 个错误 | `serde_helpers`：同一类属性字符串路径，3 个文件 | ① 属性字符串里的路径算引用 |
| 3 | 18 个（7,721 行；只留 `types::error`） | 失败：`pi-workspace` 1 个错误 | `hub_auth`：`lib.rs` 里的 `hub_auth::init_metrics();`，没有 `use`，也不以 `crate::` 起头 | ② 以子模块起头的路径算引用 |
| 4 | 16 个（6,143 行；只留 `types::error`） | **通过**。`--workspace --tests` 第一次失败，删掉 `pi-hooks/tests/integration.rs` 后通过 | 失败的是这个文件里的 8 个错误：它测的就是被删的 `dispatcher`、`result`、`runner`。唯一多出来的告警：`pi-hooks` 的 `env_expand::expand_env_vars_with_extra` 没人用了（它是函数，不是单元，工具看不到这一级） | ③ 有「为别处定义的类型写的 `impl`」的死单元，单独标出来 |
| 5 | 17 个（6,187 行；什么也不留） | 失败：`pi-tools` 5 个错误，都是缺 `From<…> for ToolError`，即 `types::error` | 它在「check first」里：标注预测对了 | — |

*工具漏掉的三类，和改法。*

1. **属性字符串里的路径**（`util::serde_base64` 173 行、`serde_helpers` 19 行）。工具读源码前先抹掉字符串，所以看不见写在字符串里的路径。现在读 `with`、`serialize_with`、`deserialize_with`、`default`、`skip_serializing_if`、`from`、`into`、`try_from` 后面的字符串路径；属性本身必须在代码里（不在注释或字符串里，也不在 `#[cfg(test)]` 里）。
2. **以子模块起头的路径**（`hub_auth` 与 `hub_auth::proactive`，共 1,578 行）。路径可以以当前模块里声明的子模块起头，不用 `use`。工具原先只认 `use child::x;` 这一种写法。
3. **没有名字就被用到的 `impl`**（`types::error`，44 行）。里面是 `impl From<TodoError>`、`impl From<WebFetchError>` 之类 for `ToolError`；别处的 `?` 靠它们转换错误，却没有任何地方写出 `types::error` 这个路径。读文本看不出来。所以工具只做标注：死单元里有「为它自己没定义的类型写的 `impl`」的，列在清单的「check first」里，摘要表里有一栏。现在是两个：`pi-tools` 的 `types::error`（`ToolError`）与 `pi-shell` 的 `session::events`（`GoalClassifierVerdictTelemetry`）。17 个里真正要留的只有前者；后者删了照样编译。

*局限。* 只到模块一级：活模块里的死函数找不到（第 4 轮的 `expand_env_vars_with_extra` 是例子）。估计偏向「活」：`use` 了就算引用，哪怕名字没被用到；类型位置的路径算调用。宏或 trait 对象不带名字地用到的单元会被漏掉。所以清单是候选，不是判决；真的要删，以编译器为准。

*对工具自己的检查。* 单测 39 个（第一版 35 个）。对工具做 17 处改动，每一处去掉或改坏一个机制；每一处都让至少一个单测失败。有一处（`impl` 头里的泛型）第一次没被抓住，补了用例才抓住。

**C.1：运行时验收（Linux / WSL2）。**

*Rust 一侧的 e2e。* `python_agent_e2e`（pager 的 ACP client 对着真的 Python agent）在本分支上通过。`PI_E2E_REQUIRED=1`，所以不是跳过。这些用例在 `pi-pager` 的 5,608 个里。

*网络审计*（`scripts/tui_pty/net_audit.py`，要 PTY 与 `/proc`，只在 Linux 上跑）。它在 PTY 里起 `zypi`，走一轮对话，再用 `/exit` 退出。这期间约每 9 ms 读一次 `zypi` 与它之下每个进程的 socket：`/proc/<pid>/fd` 里的 socket inode，对上 `/proc/<pid>/net/{tcp,tcp6,udp,udp6}`。13 项检查：欢迎页出现；这一轮有回答；`/exit` 以 0 结束；之后没有进程留下；采样器看得见 `zypi` 自己（防止「什么都没采到」也算通过）；`zypi`、包装进程和别的进程都没有到非本机地址的连接；它们也没有在非本机地址上监听；agent 在 mock 下也没有外连。真模型下，最后一项换成相反的**正向对照**：agent 必须有一个已建立的外连，否则说明采样看不见外连。

| 运行 | 结果 |
|---|---|
| `--mock`（mock LLM） | 13/13，6 s，658 次采样（平均间隔 8.8 ms） |
| `--mock --no-network`：整个进程树放进空的网络命名空间（`bwrap --unshare-net`，不要 root） | 13/13，6 s，528 次采样。这一轮照样答完、正常退出：`zypi` 的一轮对话不需要网络 |
| `--model-from ~/.pi-python/agent.toml`：真模型一次（只把 `[model]` 表复制到临时 home） | 13/13，8 s，810 次采样（平均间隔 9.6 ms）。`zypi` 没有到非本机地址的连接，也不监听。agent 持有到两个非本机地址的连接：一个是域名解析，一个是到模型服务的连接。这就是正向对照。输出里没有主机名、地址、key 或文件内容 |

*踩到的一个坑。* WSL 的登录 shell 会用自己的 profile 重新设 `REAL_LLM_API_KEY`，把环境里传进去的值盖掉。两处的值不同（用 SHA-256 比的，没有打印值）。第一次真模型运行因此是 12/13：agent 拿到的是另一个 key，这一轮以错误行结束。改用非登录的 `bash`，再用 `WSLENV` 只传这一个变量，就是 13/13。

*状态行的探测*（`scripts/tui_pty/status_line_probe.py`）。pager 画「状态行」（模型、上下文用量、费用）只用一种数据：agent 发的 `session_status` 通知，经 `_pi/session_notification` 到达。旧的 Rust agent 发它；Python agent 没有发它的代码。脚本对 `[ui.status_line]` 的两种写法（`type = "command"` 与 `type = "builtin"`）各起一次新的 `zypi`，走一轮对话，等过 pager 的 300 ms 去抖，再记录：命令脚本跑了没有，这一行在不在屏幕上。每种写法跑两次：一次对着原样的 Python agent，一次对着替身 `status_proxy_agent.py`。替身启动真的 `python -m pi_agent_cli`，把 ACP 的 JSON 行原样双向拷贝，只在 `session/new` 的响应与每一轮的结尾之后补一条 `_pi/session_notification`（`sessionUpdate = "session_status"`）。两次之间只差这一条消息，所以第二次是对照：它证明第一次不画，是因为缺这条消息，不是配置写错了。

| `[ui.status_line]` | agent | 这一轮答了 | 命令脚本跑了 | 这一行画了 |
|---|---|---|---|---|
| `command` | 原样的 Python agent | 是 | 否 | 否 |
| `command` | 加替身 | 是 | 是（stdin 上的字段：`context_window`、`cost`、`cwd`、`model`、`schema_version`、`session_id`、`trigger`、`version`、`workspace`） | 是 |
| `builtin` | 原样的 Python agent | 是 | — | 否 |
| `builtin` | 加替身 | 是 | — | 是 |

4/4 与新的指南页所写一致，25 s。后果：配了 `[ui.status_line]` 也画不出这一行，命令脚本也不会跑。计划里为此新增了待决策 15。

**C.2：内嵌用户指南**（`4fadcf3`，49 个文件，+397 / −10,023 行）。

- `pi-pager/docs/` 原有 33 个文件：用户指南 23 页加 README，教程 9 页，全是 Grok 原文。（附录 E 里 r2 记的是「38 个 md」；`df812d1` 的树里数出来是 33 个。）现在只剩 `user-guide/` 里的 7 页加 README：入门、斜杠命令、主题、会话、终端支持、权限与安全、状态行。7 页按代码和 `agent.example.toml` 重写；删了 25 个文件：16 页讲已删功能的用户指南，加 9 页教程。状态行页写明：这一行在 Python agent 下是空的（见 C.1）。
- `docs.rs`：`USER_GUIDE` 只列这 7 页。`extract_user_guide_docs` 与它在 `pi-pager-bin` 里的调用删了，启动不再往 home 写文档。两个守卫用例：页面里没有旧产品名；目录里每个文件都在 `USER_GUIDE` 里。`status_line_tests.rs` 里把 `25-status-line.md` 当夹具的三个用例没有改，对新页面仍然通过。
- 教程叠层（`views/tutorial.rs` 688 行、`tutorial_docs.rs` 149 行）删了：A.3 之后没有入口，页面讲的是 zypi 没有的命令。
- 指向不存在的东西的字符串：文档选择器页脚的提示指着 zypi 不再写的文件；`mcp.rs` 里 4 条、`glob.rs` 里 1 条消息的末尾写着 `See ~/.grok/docs/user-guide/<页>.md`；`zypi wrap` 的帮助里有一行指向不存在的 README；还有两处注释。都改了。
- §10.13「没有验证 / 留下的」里有一条：内嵌用户指南仍按旧的环描述 Plan 与 Auto，启动时被解压。这一条由此结案：那几页没有了，也不再解压。
- 测试：`pi-pager` 5,626 → 5,608（−18）。

**C.3：`NOTICE` 与 `THIRD-PARTY-NOTICES`**（`da56ef9`）。

- 起点：`tui/THIRD-PARTY-NOTICES`（18,898 行，`wc -l`）是上游为它自己的依赖图写的，不跟 `Cargo.lock`。`tui/NOTICE` 写「Product binary is `pi` (`pi-grok-pager-bin`)」。
- `scripts/tui_notices.py` 取 `pi-pager-bin` 的 normal 与 build 依赖闭包里来自 crates.io、git 或 `tui/third_party/` 的包，取五个目标三元组的并集（Linux 的 x86_64 与 aarch64，macOS 的 aarch64 与 x86_64，Windows 的 x86_64-msvc），与文件逐包对账。`check` 不一致就退出 1；`update` 显示要改什么；`update --write` 来改。
- 结果：989 个包会进二进制，文件里有 1,167 个条目；缺 44 个，过期 222 个。过期的删掉，缺的补上，文件变成 16,548 行。手写的段落（打包的主题、树内移植的源码）和 Part II（许可证全文）不动。新条目的许可证措辞抄自同一 SPDX 表达式的旧条目，工具自己不加许可证政策。44 条都有可抄的，没有一条列为 `needs review`。版权行取自包源码里的许可证文件，没有就用包的作者。**这是与 `Cargo.lock` 对账，不是法律审查**：措辞和版权行是机器抄的，没有人逐条读过。
- `NOTICE`：产品是 `zypi`；`tui/` 是 hard fork，不再跟随 upstream；列出改动。`third_party/NOTICE` 与 `third_party/README.md` 里过期的 `nfsserve` 条目去掉了。
- 62 个单测。`check` 要 cargo，所以在 WSL2 上跑；Windows 上没有 cargo，可以用 `--metadata FILE` 给一份存好的 `cargo metadata`。

**C.4：文档与残留词。**

- `packages/pi-agent-cli/AGENTS.md`：`config.toml` 的约束按 §10.10 第 3 项的实测写。`zypi` 对认得的键严格、对不认得的键宽容。文件缺失、有未知的表或键、有 Python 的 `[model]` 表，都能正常启动。顶层的 `permission = "ask"`（TUI 期望那里是表）、`[[models]]` 数组、无效 TOML 让 `zypi` 以 1 退出；`[agent].command` 的类型错了被静默忽略。agent 读 `agent.toml`，只有该文件不存在才回落到 `config.toml`（`load_config`，为旧安装保留）；TUI 的 `[agent].command` 先读 `agent.toml`，没设再读 `config.toml`（`acp/spawn.rs`），不读别的 agent 键。`docs/WINDOWS.md` 里关于 `config.toml` 的一句话同样改了。
- `docs/TUI-AND-CODE-AGENT.md`：ACP 链接改成 agentclientprotocol.com；`[model]` 的真实键（`id`，不是 `model_id`；没有 `context_window`；权限值 `ask`、`auto`、`always-approve`；`[skills]` 的路径）；真实的斜杠命令；写明 zypi 里没有模型客户端、工具执行器和 MCP 客户端；指向网络审计。
- Phase 4 spec（`docs/specs/2026-08-25-phase4-coding-agent-cli-design.md`）：§1.3 #2 与 §3 加修订说明：shell 的 runtime 部分已删；`pi-tools`、`pi-workspace` 仍把 pager 绑在树上；`tui/` 是 hard fork。
- `docs/DESIGN.md` §8.5：`pi/` 扩展的五条准入条件（计划 P2）。
- **`grok agent` 清零**（`214c553`）。原来有 28 行：15 行在旧用户指南的页面里（C.2 删了或重写了），13 行在代码与 README 里：`pi-agent`、`pi-http`、`pi-tty-utils`、`pi-test-support` 的文档注释与一个标签，`pi-test-support` 与 `pi-shell` 的 README。`pi-shell/README.md` 原来是上游 Grok CLI 的用户手册（2,682 行，含一个会装上官方 CLI 的安装脚本），换成 43 行，说明这个 crate 现在是什么。（`214c553` 的提交说明里写的 1,968 是非空行数。）
- **残留词门禁。** `scripts/tui_baseline.py` 加了 `count_residue`，清单里加了 `[gates.residue]`：每个词一个上限，只降不升。现在 `grok agent` 0，`leader` 245，`x.ai` 304（`tui/crates` 下的行数）。计划里「清到 0」对 `leader` 与 `x.ai` 做不到：这些行是已删的 leader 进程的名字、xAI 的服务地址，和「不实现 `x.ai/*`」的过滤本身，阶段 D 之前都在活着的代码里。所以改成棘轮；Python CI 的 job 里有一个单测跑同一个检查。每一类是什么、去哪里，见 [`tui.md` §4](../../baselines/tui.md)。

**验证。**

| 检查 | 结果 |
|---|---|
| 10 个 suite 整套（基线执行器，清环境，`PI_E2E_PYTHON` 与 `PI_E2E_REQUIRED=1`，WSL2，增量编译；`4fadcf3`） | ✓ **合计 8,665 通过 / 0 失败 / 22 忽略**（= r10 的 8,683 − 18），22 m 22 s。`pi-pager` 5,608（13 忽略），其余 9 个 suite 与各自的 `reference_passed` 相同。这是一次本地运行，没有 CI 对账 |
| `tui_baseline.py check`（门禁、消费者构建、`--workspace --tests`；WSL2；`24907ef`） | ✓ 全过，5 m 24 s：依赖图 980，告警 0，残留词 `grok agent` 0 / `leader` 245 / `x.ai` 304。`4fadcf3` 之后 `tui/` 只改了文档注释、一个标签和两份 README，所以测试没有重跑 |
| `tui_notices.py check`（WSL2） | ✓ `71286ed` 上：989 个包会进二进制，文件里 989 个条目，缺 0、过期 0 |
| 网络审计、状态行探测、编译器核对 | 见上；都在 WSL2 上做 |
| Python 全量 pytest（`real_llm` 除外） | ✓ Windows（Python 3.12.7）：**2,510 通过 / 0 失败 / 39 跳过 / 31 取消选择**，290 s；Linux（WSL2 的 ext4 克隆，Python 3.12.3）：**2,538 通过 / 0 失败 / 11 跳过 / 31 取消选择**，120 s。两边的用例总数相同（2,549）；差别是各自平台的跳过项（PTY 脚本的用例在 Windows 上跳过）。比 §10.13 多 131 个用例：调用图 39、notices 62、`test_tui_pty.py` 27（23 → 50）、`test_tui_baseline.py` 3（残留词门禁） |
| `ruff check .`、`ruff format --check .` | ✓ 项目 venv 的 Ruff 0.16.10 与 CI 钉的 0.16.0（`uvx --from ruff==0.16.0`）：`ruff check .` 全过，`ruff format --check .` 234 个文件已格式化 |
| 文档链接与锚点 | ✓ 计划、本文件、`tui.md`、`tui-support.md`、`tui-history.md` 里的链接、锚点与引用标签都对得上 |

**没有验证 / 留下的。**

- 本分支没有 CI 运行。Rust 的数是本地一次运行（见上）。
- 真模型只用来做网络审计（一轮对话），没有用它验证别的功能。
- 网络审计与状态行探测只在 Linux（WSL2）上跑过。macOS 与 Windows 上没有：脚本要 PTY 与 `/proc`。
- 网络审计只看 TCP 与 UDP 的 socket 表（每 9 ms 一次）。它看不见 Unix socket，也没有抓包。一个在两次采样之间开了又关的连接会被漏掉；这一点没有量化。真模型一轮里采样器抓到了 agent 的连接（正向对照），说明这个时间分辨率对这样的流量够用。
- `THIRD-PARTY-NOTICES` 的措辞没有人读过（见 C.3）。`tui_notices.py` 在 Windows 上没有跑过 `check`（要 cargo）。它也没有接进 CI：要改 `tui-ci.yml`，本分支没有 CI 运行，没法先试。依赖变了之后要手动跑一次。
- 编译器核对只做了一次（`24907ef` 的树）。工具只到模块一级。
- 17 个报死的单元里的 16 个没有真的删，也没有在副本里跑测试，所以不知道删了会少几个测试。
- `pi-agent/templates/prompt.md` 里的 `<user_guide>` 块仍写着 `~/.grok/docs/user-guide/`。别的 crate 里没有找到渲染这个模板的调用（只用到 `prompt::skills`、`prompt::agents_md`、`prompt::context`），所以多半进不了任何 prompt。没有改，也没有证明。
- `zypi --help` 仍列出一些 Grok 时代的旗标（`--agent`、`--agents`、`--no-plan`、`--no-subagents`、`--oauth` 等）。没有逐个核对它们在 Python agent 下有没有用。

**新的提议与候选。**

- **待决策 15：自定义状态行。** 这一行在 Python agent 下永远不画（见 C.1）。三条路：(a) Python agent 发这条通知——它是新的 `pi/` 扩展，要先过 DESIGN §8.5 的五条准入条件；(b) pager 用本地已有的东西画（工作目录、模型、权限模式、git 分支），不靠 agent；(c) 删掉这一行的设置、代码与指南页。倾向：(b) 或 (c)，不倾向 (a)：行里的字段大多是 pager 自己就知道的，也没有证据说有人在用它。状态：待定。
- **D.5** `pi-test-support`（7,384 行）里有 5,003 行没有外部用户（mock 推理服务器、SSE 生成器、ACP stdio 客户端、headless 运行器、计数服务器、脚本化响应）。模块之间的相互引用没有查。
- **D.6** 内嵌指南的查看器没有入口：`ActiveModal::DocPicker` 与 `DocViewer` 在非测试代码里没有人打开，启动时的解压又在 C.2 删了，所以七页指南现在没有读者。二选一：接一个入口，或者连同选择器、查看器一起删。
- **D.7** 发行与更新的遗留：`pi-pager/npm/` 下 7 个打包目录，`pi-update` 里写着 npm 包名 `@pi-official/grok` 和 `x.ai/cli/install.*` 的安装提示。自动更新在 Phase 4 §4.4 已禁用，这些不会跑，只是遗留。
- **D.8** 删掉上面编译器核对过的 16 个单元（被删文件共 6,143 行）和 `pi-hooks/tests/integration.rs`（822 行）。`types::error` 不能删。没有做：它是删除型改动，用户还没有定要不要做阶段 D。
- **D.9** `tui_baseline.py test` 在开发者自己的 `HOME` 下跑测试，有测试会往真实的 `~/.pi-python` 与 `/tmp` 写东西。收尾时在 WSL 里发现：`~/.pi-python` 里有 `agent_id`、`worktrees.db`、`config.toml`、18 条指向已删临时目录的信任记录、`last-copy.txt`、`logs/`，`/tmp` 里有 46 个 `grok-unified-log-test-*` 目录（r11 的运行留下 13 个）。时间对得上：18 条信任记录里 16 条与测试建那些目录的时间相差不到 5 秒，另 2 条没有对上的目录；具体是哪个测试写的，没有查。办法是测试运行时把 `HOME` 设成临时目录（先钉住 `CARGO_HOME`、`RUSTUP_HOME`）。没有做：要整套重跑才能验证，而且改的脚本 TUI CI 也在用。

### 10.15 r12 追加：V0.5 的发布里程碑

用户在 r11 的 PR [#13](https://github.com/zy1233/pi-python/pull/13) 合入 `main`、CI 没有问题之后，说「根据当前的剥离状态，设置发布 V0.5 的里程碑」。第一批只改文档，另外在 GitHub 上建了一个里程碑。我把里程碑的条件列出来之后，用户又从六个后续里选了四个（见下面的「续」）；它们在别的 PR 和 CI 运行里做，本 PR 只记录。本 PR 没有改 `tui/` 与 `packages/`，没有打 tag，没有发布。分支 `docs/v0.5-milestone`，叠在 `main` 的 `4fc028a` 上，PR [#16](https://github.com/zy1233/pi-python/pull/16)。条件做完之后，用户又答了三个问题（见「续 2」）；决策 16 因此另开了 PR [#17](https://github.com/zy1233/pi-python/pull/17)，它改了 `tui/` 与 `packages/`。之后用户让我先把 PR 都合入 `main`，WSL 也修好了（见「续 3」）：[#15](https://github.com/zy1233/pi-python/pull/15)、[#17](https://github.com/zy1233/pi-python/pull/17)、[#14](https://github.com/zy1233/pi-python/pull/14) 已合入，本分支改基到 `0efd400`。PR #16 合入之后（`b23e14e`），用户从三件小事里全选了，见「续 4」。之后用户回答了计划里还没定的四项决策，见「续 5」。已定的两项（决策 6 与 15）随后各做成一个 PR，没有合入，见「续 6」与「续 7」。

**起点。** `origin/main` 在 `4fc028a`（#13 的合并提交，2026-10-12 02:30 UTC）。两个工作流在它上面都通过：`CI`（run [38196042958](https://github.com/zy1233/pi-python/actions/runs/38196042958)：Ruff，pytest 在 Python 3.11、3.12、3.13 上）；`TUI CI`（run [38196042998](https://github.com/zy1233/pi-python/actions/runs/38196042998)：Gates + `cargo check` 3 m 46 s，`cargo test` 15 m 49 s，`Release build baseline` 按设计跳过）。`cargo test` 的日志末尾是 `Total: 8665 passed, 0 failed, 22 ignored`，`PI_E2E_REQUIRED=1`：与 §10.14 在 WSL2 上的本地数相同。

**「V0.5」指什么。** 仓库里已经有：核心、harness、cli 的 0.5.0 与三个扩展包的 0.2.0（`pyproject.toml`，`7e7d4d4` 准备的）、`CHANGELOG.md` 的 `[0.5.0] - 2026-10-02` 一段，以及它上面的 `[Unreleased]`（TUI 一侧的 r5–r11）。tag 与 GitHub 上的发布只到 v0.4.0（2026-09-28）。0.5.0 是 Phase 6/7 审计的第 7 批准备的，那时没有打 tag（`docs/AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md` 第五节 14）。所以 V0.5 是把这个没发的 0.5.0 和 TUI 一侧的剥离一起发。

**GitHub 里程碑。** 建了 [v0.5.0](https://github.com/zy1233/pi-python/milestone/1)（编号 1，没有截止日期；描述用英文写：范围、六项条件、不在范围的）。参数核对过 GitHub REST 文档（`POST /repos/{owner}/{repo}/milestones`：`title`、`state`、`description`、`due_on`）。没有挂 PR 或 issue：已合入的 PR 一挂，进度条就是 100 %，而条件还没有做；也没有为每项条件开 issue，条件记在计划里，不记两处。

**范围与条件的依据。**

- *与 v0.4.0 的差。* `git rev-list --count v0.4.0..origin/main` = 23 个提交；`git diff --shortstat v0.4.0 origin/main -- tui` = 1,512 个文件、+12,275 / −641,224 行；`pi-sampler` 的文件（`git ls-tree -r <ref> --name-only`）v0.4.0 有 31 个，`main` 没有。
- *release 体积。* v0.4.0 的 `zypi-linux-x86_64` 是 470,461,776 B，与 `docs/baselines/tui.md` 里的基线一致。422,034,120 B 是 r5 量的（`ac1d3ff`），之后没有再量：这个 job 只手动触发（`tui-ci.yml` 的 `workflow_dispatch` 输入 `release_baseline`），基线表自己也写了这一点。r5 之后 `tui/` 又合入 5 个提交（#8、#9、#10、#12、#13）：`git diff --shortstat 455527c origin/main -- tui`（`455527c` 是 #7 的合并提交）= 115 个文件、+4,481 / −16,045 行。（r12 续：已经量过，见下面的「续」。）
- *发布构建演练。* `release.yml` 只在 `v*` tag 上触发，没有手动入口。macOS arm64 的 job 是 PR [#6](https://github.com/zy1233/pi-python/pull/6)（`ci: publish macOS arm64 TUI binary`，2026-10-01 合入；它的提交是 `07a3574`）加的，晚于 v0.4.0，发布里从没跑过（v0.4.0 的 assets 只有 12 个 Python 文件和 `zypi-linux-x86_64`）。`github-release` 的 `needs` 含 `build-tui-macos-arm`，`publish-pypi` 只需要 `build`：macOS 构建失败时，PyPI 上的包已经发出，GitHub 上没有 Release。`tui/crates` 里有 75 个文件带 `cfg(target_os = "macos")`（`git grep -l -e "target_os = .macos." origin/main -- tui/crates`），Linux CI 不编译它们。本文里最后一次在 macOS 上构建与测试是 r7（§10.10 起点：「macOS 一侧的工作（r3–r7）已经合入 `main`」）；#8 的合并提交 `4b5848f`（2026-10-07）之后，`tui/` 改了 88 个文件（`git diff --name-only 4b5848f origin/main -- tui`），其中 8 个带这样的代码：`pi-pager` 的 `actions/defaults.rs`、`app/agent_view/paste.rs`、`app/app_view.rs`、`app/app_view_tests.rs`、`app/effects/tests.rs`，`pi-pager-bin/src/main.rs`，`pi-sandbox/src/deny/glob.rs`，`pi-shell/src/config/mod.rs`。这只说明有多少这样的代码被改过，不说明哪一处会编不过。v0.3.0 的发布工作流跑了三次：失败、取消、通过（`gh run list --workflow Release`）。
- *与 v0.4.0 对照。* `scripts/tui_pty/` 的 `smoke.py`、`exit_matrix.py`、`resume_matrix.py`、`mode_probe.py` 都有 `--zypi PATH`，所以能直接指向 v0.4.0 的 asset。这一项要把「已知问题在 v0.4.0 上也有」从推断变成测量。现在的依据只有计划 §2.1 的判断：默认路径本来就是内嵌的 Python ACP，被删的 Rust runtime 不可达。没有下载 v0.4.0 的二进制，没有跑。
- *发布说明。* `release.yml` 用 `awk` 摘 `CHANGELOG.md` 里与 tag 同版本的一段（从 `## [0.5.0]` 到下一个 `## [`）作为 GitHub Release 的正文。`[Unreleased]` 不并进去，就不会出现在发布页上。`[Unreleased]` 与 `[0.5.0]` 里都没有这几项：Rust runtime 的删除与随之删掉的界面和命令（r3、r5）、`/model` 经 ACP `configOptions` 恢复（r3）、`-p` 在要求沙箱时拒绝运行（r5）、品牌清理（r6）。在整个 `CHANGELOG.md` 里搜 `dashboard`、`configOptions`、`pi-sampler`、`/model`、`zypi Night` 没有结果，只有 v0.3.0 一条旧的测试说明提到 dashboard。也就是说，这个发布最大的用户可见变化现在没有写进发布说明：#7 的标题就是 `remove the Rust agent runtime and keep /model via ACP config options`（`455527c`，2026-10-06）。`docs/TUI-AND-CODE-AGENT.md` 只写了 Linux x86_64 有预编译二进制，macOS 一行是「Build from source」。
- *已知问题的清单。* 从计划的待决策、「还没有验证的」和 §10.11（恢复矩阵 10 PASS / 3 GAP，三个 GAP 是 `--resume <标题>`、`--session-id <已用的 id>`、`zypi export`）、§10.13、§10.14 汇总，没有在 `main` 上重测。Python 一侧的一条取自审计文档开头的状态块与第八节：F7-02 至 F7-09 没有修复批次（`docs/AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md`）。`-p` 一项读的是 `pi-pager-bin/src/main.rs` 的 `run_python_print`：`--sandbox` 报错退出（退出码 2），被丢掉的旗标打印 `warning: <旗标> has no effect with -p and was ignored`。

**新发现：`GROK_VERSION`（决策 16；r12 续：用户选了 (a)；r12 续 2：PR #17 做了）。** 核对版本号时发现，`zypi --version` 的版本号与「是不是发行构建」的判断用的是同一个环境变量。

- `pi-pager-bin/build.rs`：`VERSION_WITH_COMMIT` 取 `GROK_VERSION`，没有就取 `CARGO_PKG_VERSION`。`pi-pager-bin` 与 `pi-version` 的版本都是 1.0.8（继承自上游）。所以 `zypi --version` 应当打印 `1.0.8` 加提交。
- 整个仓库没有一个文件设 `GROK_VERSION`：`git grep -l GROK_VERSION` 只有 Rust 源码（`pi-pager-bin/build.rs`、`pi-version/build.rs`、`pi-version/src/lib.rs`、`pi-workspace/src/folder_trust.rs`、`pi-shell/src/agent/folder_trust.rs`）与上游的两个安装脚本；`release.yml` 在 v0.3.0、v0.4.0 与 `main` 上都不含它；`tui/.cargo/config.toml` 没有 `[env]`。所以发行二进制一直是「开发构建」：`pi_version::IS_DEV_BUILD`（`option_env!("GROK_VERSION").is_none()`）为真。
- 开发构建里文件夹信任整个是惰性的。`pi-workspace/src/folder_trust.rs` 里 `folder_trust_inert()` 的文档注释写着：为真时从不提示，不管仓库里本地的 `.envrc`、`.claude`、hooks、插件、MCP、LSP，也不读写 `trusted_folders.toml`；有发行戳的构建不受影响。盖了戳，它就按默认开启，发行二进制会开始对这些本地配置问「信任这个文件夹吗」。这些配置里，MCP 的加载代码在 1.P4 之后没有调用者（待决策 10）；其余没有逐项核对。遥测客户端的 `dev_build` 字段（`pi-telemetry/src/client.rs`）也随戳翻转。
- 所以**不能**靠在 `release.yml` 里设 `GROK_VERSION` 把版本号改对。提议是不盖戳，改 `pi-pager-bin` 与 `pi-version` 的 crate 版本。没有核对：版本号从 1.0.8 改小之后，有没有按版本号比较的逻辑（`pi-update` 的 `version_policy.rs` 之类；自动更新已禁用）。
- 以上都是读代码得出的。没有跑发行构建，没有跑 `zypi --version`。
- ACP 的 `agentInfo.version` 写死 `0.1.0`（`pi_agent_cli/agent.py` 的 `_AGENT_INFO`）；Phase 6/7 审计记过，没改（PR [#17](https://github.com/zy1233/pi-python/pull/17) 改了）。

**续：用户选的四件事，与决策 16。** 里程碑与条件写出来之后，我问用户下一步做什么。他从六个选项里选了四个：补 `CHANGELOG.md`（条件 5）、发布构建演练（条件 3）、推送本分支并开 PR、手动触发 `release_baseline`（条件 2）；没有选「与 v0.4.0 对照」（条件 4）。决策 16 他选了 (a)：不盖戳，改 `pi-pager-bin` 与 `pi-version` 的 crate 版本，`agentInfo.version` 读已装包的版本。那个问题问的是倾向，不是「现在改」，选的四件事里也没有它，所以当时只记成已定，没有实施（后来的回答见「续 2」）。

- *条件 5，`CHANGELOG.md`（PR [#15](https://github.com/zy1233/pi-python/pull/15)）。* `[Unreleased]` 的 18 条并进 `[0.5.0]`（每组接在原有条目后面），`[Unreleased]` 清空，日期写 2026-10-12；`[0.5.0]` 顶上加一段概述；补 19 条：Added 1（`/model` 经 ACP 配置项）、Changed 2（`-p` 拒绝沙箱并报告被忽略的旗标；产品名 `zypi`）、Removed 2（新分组：Rust agent runtime；随之删掉的界面、命令与旗标）、Fixed 6（退出时工具进程残留；`session/close` 与 `session/resume`；`session/list` 的标题与时间；macOS 上 `AGENTS.md` 读两遍；`ssh-wrap` 的 alias；指向不存在的目录与命令的提示）、Known issues 8（新分组，取自计划的已知问题）。数字：`git grep -c -e "^"` 数 `tui/crates` 下的 `.rs`，v0.4.0 是 2,500 个文件 / 1,620,612 行，`main` 是 1,755 个 / 1,021,067 行；`tui/Cargo.lock` 的 `[[package]]` 从 1,311 个到 1,257 个；删掉的旗标，用 `PagerArgs` 的长旗标在两个版本之间对比：删了 7 个（`--fork-session`、`--leader`、`--no-leader`、`--leader-socket` 和三个 `--local-workspace*`），没有新增。`--local-workspace*` 在发布构建里本来就没有（没有任何 crate 打开 `cfg(feature = "local-workspace")`），所以没写进条目。各条目的事实来自 §10.2、§10.7、§10.8、§10.10 和代码。核对：改前 75 条、改后 94 条（差的 19 条就是新增），没有条目丢失或被改，`## [0.4.0]` 起的文字不变；用 `release.yml` 里同一段 `awk` 取 `[0.5.0]`：44 KB，五个分组，没有 `## [` 行。留给用户的：日期；已知问题要不要原样公开；macOS 二进制的条目（要等演练；「续 2」补了）。
- *条件 3，发布演练（PR [#14](https://github.com/zy1233/pi-python/pull/14)）。* `release.yml` 加 `workflow_dispatch`，`publish-pypi` 与 `github-release` 的 `if` 改成 `github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')`（只看 ref 不够：手动运行可以把一个 tag 当作 ref）。`actionlint` 1.7.12 通过。我没有等 PR 合入，直接从分支触发了一次：`gh workflow run release.yml --ref ci/release-dry-run`，GitHub 接受了（不用先合入 `main`）。结果：[run 38223353755](https://github.com/zy1233/pi-python/actions/runs/38223353755) 成功，共 27 分钟：`Pre-release tests` 97 s；`Build Python packages` 29 s；`Build TUI binary (Linux x86_64)` 986 s（其中 `cargo build --profile release-dist` 934 s）；`Build TUI binary (macOS arm64)` 1,527 s（其中构建 1,455 s）；`Publish to PyPI` 与 `GitHub Release` 跳过，没有发布任何东西。留下三个产物：`dist`（3,126,019 B）、`tui-binary`（压缩后 97,314,085 B）、`tui-binary-macos-arm64`（压缩后 21,032,990 B），保留到 2027-01-10。macOS 的 job 是第一次在发布工作流里跑；它编译的 `cfg(target_os = "macos")` 代码（75 个文件带这个条件）Linux 的 CI 编译不到，现在通过了。`GitHub Release` 在 macOS 的 job 结束之前不在 job 列表里，结束后显示「跳过」。没有验证：没有人运行过 macOS 二进制，我也没有下载它：`gh run download` 很慢，Linux 的产物 8 分钟只下了约 19 MB，macOS 的 6 分钟几乎没动，我都停掉了，临时文件也删了。`if` 为真的一侧（tag 的 `push`）要等真正的 tag 才跑得到。
- *条件 2，release 体积（[run 38221952524](https://github.com/zy1233/pi-python/actions/runs/38221952524)）。* 手动触发 `TUI CI`（`release_baseline=true`），在 `4fc028a` 上。三个 job 都通过：`Release build baseline` 20 m 40 s，`cargo test` 19 m 04 s，`Gates + cargo check` 3 m 40 s。结果：

| 指标 | v0.4.0 | r5（`ac1d3ff`） | r12（`4fc028a`） |
|---|---|---|---|
| `release-dist` 的 `zypi`（Linux x86_64） | 470,461,776 B | 422,034,120 B | **420,767,096 B**（比 v0.4.0 小 10.6 %，比 r5 小 1,267,024 B） |
| 冷缓存构建耗时 | 1,647 s | 1,155 s | 1,195 s |
| `zypi --version` 中位数 | 未测 | 23.1 ms | 23.4 ms |

  同一次运行的 `check` 与 `test`：依赖图 980、0 条告警、`--workspace --tests` 0 个错误；8,665 通过 / 0 失败 / 22 忽略，与 PR #13 在 `4fc028a` 上的运行一致。条件满足（不大于 470,461,776 B）。数字回填了 `docs/baselines/tui.md`。
- *决策 16 的核对。* 版本号从 1.0.8 改小，会碰到三处按版本比较的代码：① `pi-config` 的 `version_overrides`（用 `pi_version::installed_semver()` 比较 `minimum_version` / `maximum_version`）：配置里的补丁若要求 `minimum_version` 高于新版本，就不再生效；仓库里没有默认配置带这样的补丁，用户自己写的无从查起。② `pi-update` 的 `version_policy.rs` 与 `auto_update.rs`：自动更新没有启动（`pi-pager-bin/src/main.rs` 的 `let bg_update_rx = None;`，子命令里也没有 `update`）。③ `pi-shell` 的模型缓存（`agent/models/cache.rs`）只判断版本字符串是否相等。没有发现必须先改的逻辑。实施要改：`pi-pager-bin` 与 `pi-version` 的 `Cargo.toml`（`Cargo.lock` 里两个包的版本跟着变）、`pi_agent_cli/agent.py` 的 `_AGENT_INFO`（读已装包的版本），再加一个测试，防止它们和发行版本脱节。还没有做（「续 2」做了）。
- *做不了的。* WSL 的 Ubuntu 在这台机器上起不来（`wsl` 返回 −1，信息是「另一个程序正在使用此文件，进程无法访问」），所以没有在 Linux 上运行发行二进制：`zypi --version`（PR #17 之后应当打印 `0.5.0` 加提交，是读代码得出的）和 `scripts/tui_pty/` 里的脚本都没跑。原因不明；我没有试着关 WSL，也没有动它的磁盘文件。（续 3：用户把 WSL 修好了，见「续 3」。）

**续 2：用户的三个回答（同一天晚些时候）。** 四件事做完之后，我又问了三个问题。用户的答案：决策 16「现在做，另开 PR：改 `pi-pager-bin` 与 `pi-version` 的 crate 版本，`agentInfo.version` 读已装包的版本，再加一个测试」；macOS 二进制的条目「写：在 PR #15 加条目，并改 `docs/TUI-AND-CODE-AGENT.md`，注明『CI 构建，未人工运行』」；与 v0.4.0 对照（条件 4）「不做，V0.5 不等它」。

- *macOS 二进制的条目（PR [#15](https://github.com/zy1233/pi-python/pull/15)）。* 演练成功了，所以有依据写它。`CHANGELOG.md`：Added 加一条（`zypi-macos-arm64` 与 `zypi-linux-x86_64` 一起放进 Release；在 `macos-14` 上构建；没有 Developer ID 签名，没有公证；没有人启动过；浏览器下载的文件被拦时，用 `xattr -d com.apple.quarantine <文件>`）；「已知问题」里「只在 Linux 上测过」一条补一句：macOS 二进制由 CI 构建，没有人启动过。`README.md`：下载一段改成「Linux x86_64 或 macOS arm64」，代码块里加 macOS 的说明，路线图里的预编译二进制只剩 macOS x86_64 与 Windows。`docs/TUI-AND-CODE-AGENT.md`：方案 A 的标题、macOS 的 `curl` 步骤、一段说明（不签名、不公证、没有人运行过；起不来就用方案 B）、平台表（`macOS arm64`：预编译二进制，CI 构建，没有人运行过；`macOS x86_64`：从源码构建）。数：`[0.5.0]` 现在 69 条（原有 31，`[Unreleased]` 并进 18，补 20）；用 `awk` 取出的正文 45,076 B，五个分组。
- *决策 16（PR [#17](https://github.com/zy1233/pi-python/pull/17)）。* 叠在 #15 的分支上，因为 `CHANGELOG.md` 的两处改动要用 #15 的 `[0.5.0]` 段；分支 `fix/release-version-numbers`。改了：`pi-pager-bin` 与 `pi-version` 的 `Cargo.toml`，和 `tui/Cargo.lock` 里这两个包，`1.0.8` → `0.5.0`（`pi-agent-cli-lc` 的版本）；`pi_agent_cli/agent.py`：`agentInfo.version` 读已装包 `pi-agent-cli-lc` 的版本，没有包元数据（没装过的检出）时是 `0.0.0+unknown`，它仍是合法的版本号；`CHANGELOG.md`：新加一条 Changed（含行为变化），去掉「已知问题」里的「Version numbers」；`docs/AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md` 第 14 行：「写死 `0.1.0`，这次没有改」后面补一句「后来改了」。`pi-pager` 与 `pi-shell`（也是 1.0.8）不在决策 16 里，没有改。上面「决策 16 的核对」里的三处按版本比较的代码，这次没有再动，也没有新发现。
- *决策 16 的测试与验证。* 先写测试：它们在旧代码上 5 个失败、1 个通过（`GROK_VERSION` 的守卫在现状上本来就通过）。新测试 6 个，在两个文件里：`packages/pi-agent-cli/tests/test_agent_info.py`（`initialize` 报告已装包的版本；没有包元数据时的退路是合法的版本号）和 `scripts/tests/test_release_versions.py`（两个 crate 的版本、`Cargo.lock` 里两个包的版本都等于 `pi-agent-cli-lc` 的版本，所以以后每次发布要一起抬高；没有工作流设 `GROK_VERSION`——这一条用户没有要，是为了守住 (a)，不想要就删）。8 个变异，每次只改一处，测试都失败，文件已还原：agent 的版本改回写死、退路不是合法版本、包名写错、`pi-version` 差一位补丁号、`pi-pager-bin` 改回 `1.0.8`、`Cargo.lock` 的两个条目各改一次、在工作流里加 `GROK_VERSION`。Windows：`ruff check` 与 `ruff format --check` 通过；`pytest` 全量 2,516 通过 / 39 跳过 / 31 取消选择（`real_llm`），299 s。PR #17 的 CI 全过：`CI`（[run 38230414753](https://github.com/zy1233/pi-python/actions/runs/38230414753)）：Ruff；Python 3.11、3.12、3.13 的 `pytest` 各 2,516 通过 / 39 跳过 / 31 取消选择，与 Windows 的数字相同，所以新测试在 Linux 上也通过。`TUI CI`（[run 38230414764](https://github.com/zy1233/pi-python/actions/runs/38230414764)）：Gates + `cargo check` 2 m 46 s，依赖图 980（等于上限），`pi-sampler` 不在；`cargo test` 14 m 03 s，8,665 通过 / 0 失败 / 22 忽略（`PI_E2E_REQUIRED=1`），与 `4fc028a` 上的数字相同；`Release build baseline` 按设计跳过。所以 Rust 一侧的清单与锁文件改动能编译，测试没变。当时没有人看过 `zypi --version` 的输出：没有一个测试检查它（续 3 看过了）。
- *条件 4。* 用户决定不做。计划里条件 4 的结果记成「不做」，V0.5 不等它。留下的是：「已知问题」里的各项哪些 v0.4.0 就有、哪些是剥离带来的，仍是推断，没有测量。

**续 3：合入，WSL 修好（同一天晚些时候）。** 用户说：「你先把 PR 都合进 main，WSL 我已经修好了可以启动。」下面是我做的，和我看到的。

- *合入。* 合入前我查了四个 PR：都是打开、非草稿、可合并、检查全过、没有评论与审查。`main` 没有分支保护，只有一条规则集（不许删除，不许非快进更新）。仓库开着 `deleteBranchOnMerge`，合入会删掉 PR 的分支。所以我先把 #17 的基底改成 `main`，免得删掉 #15 的分支时 #17 被关掉。#14、#15、#17 的 PR 正文里有几句到合入时已经不对的话（「演练还没跑」「WSL 起不来」「先合 #15」），我先改了：squash 提交的正文就是 PR 正文。顺序与结果（都是 squash，用 `--match-head-commit` 钉住 PR 的头）：#15 → `59545c1`；#17 先重放（`git rebase --onto origin/main 993b7a5`，只有它自己的一个提交；`git diff b7567dd HEAD` 为空，树与测过的树一样），强推带 lease，头是 `cb9a9b7`，合入后是 `d57fd9b`（树与 `cb9a9b7` 一样）；#14 → `0efd400`（只改 `release.yml` 的 7 行）。三个 PR 的分支被自动删除。本分支再改基到 `0efd400`（没有冲突）。
- *合入后的 CI。* `CI` 在 `0efd400` 上通过（[run 38233821853](https://github.com/zy1233/pi-python/actions/runs/38233821853)，3 m 40 s）；在 `59545c1` 与 `d57fd9b` 上的 `CI` 被下一次推送取消（并发组，设计如此）。`TUI CI` 在 `d57fd9b` 上通过（[run 38233778549](https://github.com/zy1233/pi-python/actions/runs/38233778549)）：Gates + `cargo check` 3 m 56 s，依赖图 980（等于上限），0 条告警；`cargo test` 15 m 44 s，8,665 通过 / 0 失败 / 22 忽略。`0efd400` 上的 `TUI CI` 没有运行：#14 只改 `release.yml`，不在 `tui-ci.yml` 的路径里。
- *WSL。* Ubuntu 起来了（内核 5.10.16.3，12 核，24 GB；根盘 251 GB，用了 42 GB）。`CARGO_TARGET_DIR` 指向 `/mnt/d/work/cargo-target`，我没有碰它；构建放在 ext4 上我自己建的目录里，做完删了（`~/pi-verify`，9.2 GB，连同克隆、venv、日志与临时脚本）。登录 shell 导出了 `PI_HOME` 与 `REAL_LLM_API_KEY`：我的每次运行都用 `env -u` 去掉它们，PTY 脚本自己也从空环境起。
- *Linux 的 `pytest`。* 在 `0efd400` 上，用 `/tmp/wsl-venv`，在 Windows 的检出里跑（`/mnt/d/work/pi-python`）：**2,539 通过 / 16 跳过 / 31 取消选择**，160.77 s。CI 的 Linux 是 2,516 通过 / 39 跳过；两边的总数相同（2,555），WSL 里有 23 个在 CI 上跳过的用例这次跑了；哪些，没有查。
- *`zypi --version`。* 在 ext4 上克隆 `main`（`0efd400`），`cargo build -p pi-pager-bin`（开发 profile，7 m 08 s，12 核），用空环境运行：`zypi --version` 与 `zypi -V` 都打印 **`zypi 0.5.0 (0efd40071ca0)`**，退出码 0；括号里是 `0efd400` 的前 12 位。与读代码得出的一致。这是开发构建，不是 CI 的 `release-dist` 产物。
- *`agentInfo.version`。* 用 `uv` 在克隆上建新 venv 并装三个包：已装的 `pi-agent-cli-lc` 是 0.5.0，`pi_agent_cli.agent._AGENT_INFO.version` 是 `0.5.0`。顺带看到：`/tmp/wsl-venv` 是 9 月 3 日装的可编辑安装，它的元数据还是 0.2.0，所以在那个环境里 agent 会报 0.2.0。版本取自安装时的元数据，这是决策 16 (a) 的结果；发布的 wheel 没有这个问题，旧的可编辑安装有。
- *PTY 脚本。* 都对着这个开发构建，`--python` 是上面的新 venv：`smoke.py` **22/23**；`exit_matrix.py` 12/12，都不留进程；`print_exit.py` 4/4；`resume_matrix.py` 10 PASS / 3 GAP / 0 FAIL（三个 GAP 与计划里的已知问题一样）；`mode_probe.py` 与 #12 的结果一致（普通模式没有标签，Always-Approve 有标签，四个被拒绝的值退出码 2，设置页没有 Plan 行，选择只有 Ask 与 Always approve）；`status_line_probe.py` 4/4；`net_audit.py --mock` 13/13，`--mock --no-network` 13/13。
- *`smoke.py` 失败的那一项。* `status line follows the switch`：脚本等屏幕上出现 `Second model\s+·`，即模型名后面跟一个点。切换成功了：输入框下边框的标签就是 `Second model`，会话文件里有 `model_change`，之后的一轮有回答，`/resume` 之后模型还是它。又跑了一遍（`--show`），结果一样。原因（推断，没有在旧构建上验证）：点是 `auto` 标志带来的；#12 之后普通模式不再显示它（`mode_probe.py` 的标签是空或只有模型名）。所以这是脚本里过期的检查，不是 TUI 的退化。我没有修它：用户没有要。改法是把正则改成接受有或没有后缀的边框标签，另开一个 PR。（续 4：用户后来要了，已修；没有另开 PR。）
- *没有做。* `scripts/tui_baseline.py test` 的整套 Rust 测试（CI 在 `d57fd9b` 上跑了）；`--sandbox` 下的 `smoke.py`；Landlock 层（内核 5.10 没有）；运行 CI 的 `release-dist` 产物（`tui-binary` 约 97 MB，从这台机器下载太慢）；与 v0.4.0 对照（条件 4，用户不做）。

**改了什么。** 本 PR：计划 `docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`：状态与「当前状态」更新到 `4fc028a`（续 3 又更新到 `0efd400`）；新加「发布里程碑 V0.5」一节（条件 2、3、5 记了结果或 PR）；「下一步」；「还没有验证的」加「发行构建」；决策 16（已做 (a)，PR #17）；条件 4、5 的结果；§10 索引；修订记录；链接定义。本文件：本节、目录、§10 的引言与修订记录。`docs/baselines/tui.md`：release 的数。别的三个 PR：[#14](https://github.com/zy1233/pi-python/pull/14) 改 `.github/workflows/release.yml`（7 行）；[#15](https://github.com/zy1233/pi-python/pull/15) 改 `CHANGELOG.md`，后来还改了 `README.md` 与 `docs/TUI-AND-CODE-AGENT.md`；[#17](https://github.com/zy1233/pi-python/pull/17) 改 crate 版本、`agent.py` 与两个测试文件（决策 16）。

**没有验证 / 留下的。**

- 没有下载或运行 v0.4.0 的二进制（条件 4：用户决定不做）。续 3 在 Linux 上运行了 `main` 的开发构建，但没有运行 CI 的 `release-dist` 产物。条件 1（CI 绿）要在要打 tag 的提交上再看一次；条件 6 归用户。
- macOS 二进制：演练只说明它能不能构建（结果见上），没有人运行过它；`CHANGELOG.md`、`README.md` 与 `docs/TUI-AND-CODE-AGENT.md` 现在写了它（PR #15），并注明「CI 构建，没有人运行过」。
- 决策 16：没有测试检查 `zypi --version` 的输出本身；续 3 看过一次（开发构建，`zypi 0.5.0 (0efd40071ca0)`）。PR #17 已合入（`d57fd9b`）。已装的可编辑安装会带着装的时候的版本（见续 3 的 `agentInfo.version`）。
- 75、88、8 三个数来自 `git grep` 与 `git diff --name-only`，是文本层面的统计。
- 里程碑的描述是英文，计划是中文，各写各的；改范围时两处都要改。
- 计划的 1.R4（`-p` 派发顺序与沙箱）没有状态行；代码里 `-p --sandbox` 已经报错退出（r5，ADR7 的最小版本）。这次没有改它的勾选，留给用户。
- `CHANGELOG.md` 在本 PR 里没有改；改动在 PR [#15](https://github.com/zy1233/pi-python/pull/15) 与 [#17](https://github.com/zy1233/pi-python/pull/17)，都已合入。
- `scripts/tui_pty/smoke.py` 有一项检查过期（续 3）：它在 `main` 上是 22/23，失败的是「状态行跟着切换」，原因见续 3。续 4 已修：23/23。

**续 4：修 `smoke.py`，收尾（次日，2026-10-13）。** 续 3 的汇报之后，我问了三件小事，用户全选了：修 `smoke.py` 过期的那一项检查、更新 GitHub 里程碑的描述、删本地已合入的旧分支。他还说 `smoke.py` 的修复「不需要单独 PR」，所以它直接提交到 `main`（`main` 的规则集只禁止删除与非快进更新，没有必需的检查）。

- *`smoke.py`。* 失败的那一项原来等屏幕上出现 `Second model\s+·`。现在等 `Second model(?: · [^─╯]*)? ─╯`：新名字在输入框的下边框上，后面有或没有标志（如 `· always-approve`）都接受。选择器里那一行的名字后面什么也没有，所以它不会误中。`status_line()` 的文档字符串里的例子也改了（`mock-a · auto` 改成 `mock-a`）。先离线查了这个正则，用续 3 里 `--show` 保存下来的屏幕：切换之前的四块（欢迎页、第一轮之后、斜杠菜单、选择器）不匹配；切换之后的三块（选后、第二轮之后、`/resume` 之后）匹配；`Second model · auto ─╯` 与 `Second model · always-approve ─╯` 匹配；`mock-a ─╯` 不匹配。然后在 ext4 上克隆 `main`（`b23e14e`），构建开发版 `zypi`（5 m 58 s），建新 venv，跑修好的脚本：**23/23**，加 `--sandbox workspace` **26/26**。这两个数和 `docs/baselines/tui.md` 里写的一样，那里没有改。
- *里程碑 [v0.5.0](https://github.com/zy1233/pi-python/milestone/1) 的描述。* 改到合入之后的状态：条件 5 与决策 16 写成「已合入」；条件 3 说触发在 #14（已合入）；条件 1 写 `CI` 在 `b23e14e`、`TUI CI` 在 `d57fd9b` 上通过；加了一行 #14 到 #17；数改成「到 `b23e14e` 为止 27 个提交，`tui/` 1,513 个文件」（原来 23 个、1,512 个）；状态日期改成 2026-10-13。取回来与写进去的逐字比较过，一致。
- *本地分支。* 删了五个：`chore/phase-c-closeout`、`ci/release-dry-run`、`docs/changelog-0.5.0`、`fix/release-version-numbers`、`docs/v0.5-milestone`。每个分支的头都等于对应的已合入 PR 的头（#13、#14、#15、#17、#16），远端分支在合入时已被删掉；PR 的头在 GitHub 的 `refs/pull/<n>/head` 里还在。`docs/archive-plan-execution-log` 不在用户选的名单里，没有动。（续 5：用户后来让清掉，已删。）
- *WSL。* 这次的克隆、构建（9.0 GB）与 venv 都在 `~/pi-verify`（共 9.1 GB），做完删了；根盘回到 42 GB。`/tmp` 与 `~/.pi-python` 里没有今天新增的东西。

**续 5：决策 6、8、14、15 的回答（2026-10-13）。** 续 4 的汇报里我写：计划的决策 6、8、14、15 还没有定，不定的话，它们会作为已知问题进发布说明。用户的回答：「决策 6 采用 a，8 待定，14 待定，15 采用 a。」他还让清掉 `docs/archive-plan-execution-log` 这个本地分支。

- *决策 6（`zypi export`）：(a)，还没做。* 做法是计划里写的倾向：经 ACP 回放导出——起 agent、`session/load`、收 `session/update`，复用 `render_blocks_to_markdown`。它要改 `pi-pager` 的 `export_cmd.rs`；今天那里读的是旧的 Rust 存储（`pi_shell::session::storage::load_updates_for_replay`），这也是 ADR1 的 PR 3 删旧存储层之前要先做完这一项的原因。实现还没开始。V0.5 里 `zypi export` 仍是已知问题。
- *决策 15（`[ui.status_line]`）：(a)，还没做。* 用户选的不是我写的倾向（(b) 或 (c)）。做法是选项里写的：Python agent 发 `session_status` 通知，走 `_pi/session_notification` 载体；这是新的 `pi/` 扩展，要先过 DESIGN §8.5 的五条。做的时候，内嵌指南的 `25-status-line.md` 和 `status_line_probe.py` 的预期也要改，今天它们写的是「画不出」。实现还没开始。V0.5 里这一行仍画不出，是已知问题。
- *决策 8 与 14：待定。* 保持待定；V0.5 带着它们发，已知问题里已经有这两条。
- *没有改的。* `CHANGELOG.md` 的已知问题只写现象，没有写决策号，所以没有改。
- *本地分支。* `docs/archive-plan-execution-log` 的头 `df812d1` 已在 `main` 里（`git merge-base --is-ancestor` 通过，`origin/main..分支` 为空），没有上游；用 `git branch -d` 删了，没有强制。现在本地只有 `main`。
- *这一步改了什么。* 计划：待决策的「已做／已定／待定」三行；6、8、14、15 四项的状态；「已知问题」一段与其中四条；「下一步」；决策 1、§7 的 1.R1 与 C.1 里提到决策 6 或 15 的地方；§10 索引；修订记录。本文件：本节、§10.15 的引言、续 4 里关于分支的一句、修订记录。GitHub 里程碑的描述里「决策 6、8、14、15」一段。

**续 6：决策 6 的实现，`zypi export` 经 ACP 回放（2026-10-13）。** 续 5 之后，我问用户，已定的两项现在做不做。用户选了决策 6 与 15 的实现，并说做出来只开 PR，不合入，进不进 V0.5 他看过再定。本节记决策 6 的 PR：分支 `feat/export-via-acp-replay`，叠在 `main` 的 `62b0a0c` 上，PR [#18](https://github.com/zy1233/pi-python/pull/18)。决策 15 另有一个 PR（续 7）。两个 PR 各自从 `main` 切出，互不依赖。

- *做了什么。* `zypi export <id>` 不再读旧的 Rust 存储，改问 agent，做法与恢复会话一样。`export_cmd.rs` 变成一个目录。`replay.rs` 起 agent（`connect()`，`ConnectFlags::default()`），发 `session/load`（带当前目录；agent 按 id 找会话，用它记下的目录），把 `session/update` 收齐。`mod.rs` 把它们交给 `AcpUpdateTracker` 与 `ScrollbackState`（带 `isReplay`），再用 `render_blocks_to_markdown` 写出，与 `/export` 是同一条渲染路径；输出到 stdout、文件、剪贴板的部分没改。agent 进程由 `AgentProcessGuard` 管：函数无论怎么返回（成功、失败、被打断），都先取消、再等它退出。agent 60 s 内没起来是错误。
- *怎样算收齐。* `session/load` 的响应可能比最后几条回放更早到达 pager：协议库读到响应就交给调用方，而每条通知由自己的任务交给 `AcpClientRx`。所以响应不算完；响应之后 500 ms 内没有新的回放更新才算完（只有回放更新让这段时间重新开始，agent 别的消息不会）。响应之前，agent 的任何消息都让 60 s 的静默计时重来；60 s 一条也没有，报 `it said nothing for 60 s`。`session/request_permission`（项目信任、工具）一律回 `Cancelled`；文件、终端与扩展方法的请求回 method-not-found。回放标记在通知的 `_meta` 里或更新的 `_meta` 里都认。
- *测试。* `export_cmd::tests` 18 个：15 个用 `tokio::time::pause`（顺序；agent 的闲话不收；标记在通知上；标记为 false；别的会话的更新；响应之后才到的更新；闲话不拖长等待；通道在响应之后关闭；长回放不被静默限制切断；权限请求回 `Cancelled`；文件请求被拒；会话不存在；别的拒绝带原因；agent 中途退出；agent 一声不出），不花真实时间，所以 `pi-pager` 的 dev-dependency 加了 `tokio` 的 `test-util`；3 个测渲染。`python_agent_e2e.rs` 新增 `export_replays_a_stored_session_through_an_agent_of_its_own`，对着真的 Python agent。`resume_matrix.py` 的用例 12、13 原来只记录现象，现在要求成功或明确拒绝，并且命令结束后没有进程留下（新的 `Outcome.left_behind`）；`test_tui_pty.py` 加了一个用例。
- *结果（WSL2，Ubuntu，调试构建）。* 单元测试 `export_cmd::tests` 18/18；`python_agent_e2e` 11/11（含新增的一个）；`tui_baseline.py check` 通过（退出码 0）：警告 0，残留词 `grok agent` 0、`leader` 245、`x.ai` 304（与 `main` 相同），依赖图 980 个包（上限 980），`cargo check --workspace --tests` 0 个错误；`resume_matrix.py` 13 例：11 PASS、2 GAP、0 FAIL，导出的两例（12、13）是 PASS，两个 GAP 是 `--resume <标题>` 与 `--session-id <已用的 id>`，与这一步无关；`pytest`：Windows 2,517 passed、39 skipped、31 deselected，WSL 2,540 passed、16 skipped、31 deselected；`ruff check` 与 `ruff format --check` 通过。推送之后，CI 在提交 `e3f3458` 上全部通过（[TUI CI run 38344799030](https://github.com/zy1233/pi-python/actions/runs/38344799030)、[CI run 38344799403](https://github.com/zy1233/pi-python/actions/runs/38344799403)）：`Ruff`；`pytest` 3.11、3.12、3.13；`Gates + cargo check`（警告 0，`leader` 245/245，`x.ai` 304/304，依赖图 980/980）；`cargo test`（8,684 passed、0 failed、22 ignored，其中 `pi-pager` 5,627 passed、13 ignored）；`Release build baseline` 按设计跳过。
- *量了什么。* 一个生成的会话（用户与 agent 的条目交替，每条有自己的 id），`zypi export <id>` 各跑三次，不改会话文件（每次比对 sha256）。调试构建，WSL2：

  | 条目数 | 文件 | `zypi export` | 同一会话 agent 自己（`session/load`，到响应） | 输出 |
  |---|---|---|---|---|
  | 2,000 | 1.4 MB | 2.5 s（三次一致） | 0.24 s | 0.9 MB |
  | 5,000 | 3.6 MB | 5.6–5.7 s | 0.53 s | 2.2 MB |
  | 20,000 | 14.2 MB | 62.7–64.1 s | 2.5 s（第一条更新 0.96 s） | 9.0 MB |

  agent 在 2.5 s 里就交完了 20,000 条，所以剩下的时间在 `zypi` 里：把更新变成文字的那一段，而且时间比大小涨得快。发布构建没有量（要 20 多分钟的构建）；调试构建的数字是上限。
- *发现，没有修。* ① 一个会话文件里条目的 id 重复时（它的父链成了环），Python agent 在 `session/load` 里占满一个 CPU，永远不答。`scripts/session_list_bench.py` 把条目 id 截成 12 位，超过 4,096 条就重复，所以我第一次的压力测试（20,000 条）撞上了这个：`zypi export` 在 64.0 s 后以退出码 1 结束，信息是 `it said nothing for 60 s`，没有进程留下（`ps` 查过），三次都一样。这说明 60 s 的限制与清理在真的卡死下也管用；但 agent 读到坏文件会死循环，是 agent 一侧的缺陷（没有修）。上表的数字用的是每条条目都有自己 id 的文件。② 这个基准脚本在超过 4,096 条时写出的文件，agent 读不了。它的默认值（每个会话 12 条）和 CHANGELOG 里的 120 条都远低于这个数，所以至今没有人撞上；没有改。③ Python agent 把 `isReplay` 放在更新的 `_meta` 里，而 pager 的 `NotificationMeta`（`acp/meta.rs`）只读通知的 `_meta`；导出两处都认，所以不受影响，恢复会话那条路径受不受影响，没有查。
- *没有验证的。* Windows 与 macOS 上的 `zypi export`（只在 WSL2 上跑过）；发布构建的耗时；真模型配置下的 agent（用的是 mock，导出不调用模型）；10 个 suite 的整套本地运行（CI 跑了，见上）。
- *这一步改了什么。* `tui/crates/codegen/pi-pager`：`src/export_cmd/{mod,replay,tests}.rs`（原 `export_cmd.rs`）、`Cargo.toml`、`src/acp/spawn.rs`（一句文档）、`tests/python_agent_e2e.rs`、`docs/user-guide/17-sessions.md`；`pi-pager-bin/src/main.rs`（调用处）；`scripts/tui_pty/{resume_matrix.py,README.md}` 与 `scripts/tests/test_tui_pty.py`；`docs/baselines/tui-support.md`（`session::storage` 的外部引用 1 → 0）；`CHANGELOG.md`（`[Unreleased]`）；计划与本文件。没有改 `packages/`，没有改 `scripts/tui_baseline.toml` 与 `docs/baselines/tui.md` 的数（`reference_passed` 只是参照，下限没动）。

**续 7：决策 15 的实现，复核后从 (a) 改成 (b)（2026-10-13）。** 续 5 之后，用户选了决策 6 与 15 的实现，并说做出来只开 PR，不合入。决策 15 当时选的是 (a)：Python 发 `session_status`，走 `_pi/session_notification`，是新的 `pi/` 扩展。DESIGN §8.5 要求先过五条，所以我动手之前先复核，没有直接做。本节记决策 15 的 PR：分支 `feat/status-line-local-facts`，叠在 `main` 的 `62b0a0c` 上，PR [#19](https://github.com/zy1233/pi-python/pull/19)。决策 6 另有一个 PR（续 6，PR [#18](https://github.com/zy1233/pi-python/pull/18)）；两个 PR 各自从 `main` 切出，互不依赖。

- *复核（§8.5 的五条）。* 缺一就不加，这里两条不过。① 没有标准对应物——**不过**。Python agent 能如实发的东西，pager 本来都知道：工作目录、会话 id、模型（pager 读 `configOptions`）、会话名、轮的计时。其余已有标准载体：版本在 `agentInfo.version`；上下文与费用在 ACP 的 `usage_update`（v1 已稳定）。Python agent 自己不知道模型的上下文窗口（`Model.context_window` 从来没有人设，配置里也没有这个键），也不知道价格，所以 `context` 与 `cost` 哪条路都给不出，不是「没走对的路」。② Python 一侧要有生产者与测试——做得到。③ 要在 `docs/specs/` 登记——做得到。④ 线上类型只定义一处——**不过**：`SessionNotification` 与 `PiSessionUpdate::SessionStatus` 在 `pi-shell`，那是旧运行时的 crate，要么新类型放在旧 crate 里，要么先搬出来。⑤ 经 `initialize` 协商、对第三方无害——做得到。
- *问用户。* 我把复核的结果摆给用户，问他怎么走。他选了 (b)：「pager 在本地用已有数据拼状态行（cwd、模型、会话 id、版本）。只改 Rust，不加线上扩展，Python 不动，两条规则都不破。PR 2 换成这个。」这个 PR 就是它；(a) 的实现没有开始，没有写任何一行。
- *做了什么。* ① `app/status_line.rs`：`ClientOwnedFields` 原来只有 `session_name`，现在有 `session_name`、`cwd`、`session_id`、`model_id`、`model_name`、`turn_running`。`apply_to` 把它们写进 `StatusLineContext`：会话名一律以客户端的为准（本地可以改名）；别的只写在 agent 没填的字段上，所以 agent 发来的状态仍然优先；`version` 空就填 `PAGER_CLIENT_VERSION`。`fill_from_clocks` 补两个只有钟才给得出的数：`cost.total_duration_ms`（本进程持有会话多久，新字段 `AgentView.attached_at`）与 `turn.started_at_ms`（用 pager 自己的、扣掉问答视图时间的 `turn_elapsed()`，所以和 `turn-timer` 一致）。② `app/status_line_policy.rs`：`shell_status_context()` 原来在 agent 没发过状态时返回 `None`，行就不画；现在以 agent 最近发的状态（若有）为底，叠上客户端的数据，再补两个钟。内置行与脚本行用同一份。③ 重建：过时检查 `client_fields_changed` 本来就比较 `ClientOwnedFields`，所以换模型、改名、新会话都会让已经稳定的行重建，不用再接线。`turn_running` 是为「轮结束」加的：命令行在轮里每个 tick 都跑，轮结束后没有 tick；没有这个标志，轮计时（内置行的 `turn-timer`，或脚本画的计时）会留在行上，直到下一次别的变化。④ 指南 `25-status-line.md`（开头的说明、内置项表、「怎么刷新」「载荷有多新」、字段表前面新的「字段从哪来」一段、例子不再读 `workspace.branch` 与上下文、排错）；`status_line_probe.py` 的预期与载荷检查；`scripts/tui_pty/README.md`。没有改 Python，没有改线上协议。
- *刻意不做的。* `context`、`cost` 不造数：Python agent 不知道，行里就没有这一项，脚本的载荷里 `context_window` 是空对象，`cost` 里只有 `total_duration_ms`（探针查「没有，不是 0」）。git 分支、仓库根、worktree 不填：`AgentView.current_branch` 在生产里没有任何地方写它（构造时设为 `None`），所以它不能当来源；脚本自己跑 `git` 就行，指南的例子就是这样写的。旧的入口（`pi/session_notification` 加 `session_status`）没有动：别的 agent 若发，仍照旧生效，且优先。
- *测试。* `pi-pager` 的 suite（manifest 里的那一项：`--lib`、`settings_e2e`、`grok_home_paths`、`selection_model_public_api`、`python_agent_e2e`；带 manifest 的环境、`PI_E2E_PYTHON` 与 `PI_E2E_REQUIRED=1`，调试构建，WSL2）：第一次跑，5,623 个通过，1 个失败（`pin_ambiguous_title_errors_before_sandbox`）；那个失败的原因见下一条，清掉原因之后重跑：5,624 个通过，0 个失败，13 个忽略（`reference_passed` 是 5,608，多出的 16 个正是新增的 16 个）。新增 16 个单元测试：`app/status_line_tests.rs` 9 个（客户端的数据与两个钟怎样填，agent 的状态怎样保留），`app/dispatch/tests/status_line.rs` 7 个（行何时重建，脚本拿到什么）；重写 2 个原来写着「内置行要等 agent 的快照」的测试；调整 1 个：`only_a_row_that_can_change_mid_turn_holds_the_loop_awake_for_one`。轮开始让行过时一次，是新的、有意的行为；这个测试原来在轮开始之后立刻看「谁还要 tick」，所以第一次跑它失败了（真的失败，不是环境）。现在它先让这一次重建发生，再看剩下谁要 tick；内置行不变，命令行仍然要 tick（轮的计时）。第一次跑没有带 manifest 的环境，磁盘也已经写满，有 31 个失败，其中 1 个是这个测试，另外 30 个在带环境、磁盘有余地的重跑里都过了。`pytest`：Windows 2,519 passed、39 skipped、31 deselected；WSL 2,542 passed、16 skipped、31 deselected。`ruff check` 与 `ruff format --check` 通过。`tui_baseline.py check` 通过（退出码 0）：警告 0，残留词 `grok agent` 0、`leader` 245、`x.ai` 304（与 `main` 相同），依赖图 980 个包（上限 980），`cargo check --workspace --tests` 0 个错误。推送之后，CI 在提交 `bfd5425` 上全部通过（[TUI CI run 38348219064](https://github.com/zy1233/pi-python/actions/runs/38348219064)、[CI run 38348219013](https://github.com/zy1233/pi-python/actions/runs/38348219013)）：`Ruff`；`pytest` 3.11、3.12、3.13；`Gates + cargo check`（警告 0，`leader` 245/245，`x.ai` 304/304，依赖图 980/980）；`cargo test`（8,681 passed、0 failed、22 ignored，其中 `pi-pager` 5,624 passed、13 ignored，与本机重跑的数一样）；`Release build baseline` 按设计跳过。
- *一个与这次无关的失败，原因在本机。* `pin_ambiguous_title_errors_before_sandbox` 在我的 WSL 里，整套 `--lib` 一起跑时失败：`main` 与 PR [#18](https://github.com/zy1233/pi-python/pull/18) 的分支也一样（5,339 通过、1 失败；5,357 通过、1 失败）；单独跑它，或只跑它所在的模块，通过。CI 上 PR #18 的整套全过（5,627 / 0）。我先怀疑是这次的状态行改动，所以用三个办法查（都用 `main` 编出来的测试程序）：把 `HOME` 换成空目录，整套通过（5,340 / 0 / 13）；只把真实 home 里的 `sessions/` 复制过去，整套失败；只复制 `config.toml` 与 `trusted_folders.toml`，整套通过。所以原因在 `~/.pi-python/sessions/`：里面有一个早先没跑完的运行（很可能是磁盘写满的那次）留下的目录，是这个测试固定用的两个会话 id。整套运行时，别的测试先把 `grok_home()` 固定在真实的 home 上，这个测试写的会话就落进真实的 home；同一个 id 在两个目录里，会话就不能按标题找到（`duplicate_legacy_id_is_not_title_addressable` 测的就是这一条），于是 `pin_local_resume_target_for_cwd` 返回 `Ok`，而测试要的是错误。单独跑时 `grok_home()` 是测试自己的临时目录，所以通过。CI 的机器是干净的，没有这个目录。我把这个目录删了（里面只有那两个 id，是我自己的运行留下的），再跑一次，结果在上一条。测试夹具用固定的会话 id，又可能落在真实的 home 里，所以一次没跑完的运行会让之后的整套运行失败；这是原有测试的弱点，这次没有改。
- *探针。* `status_line_probe.py` 在调试构建的 `zypi` 上跑（WSL2，23 s），4/4：命令行与内置行各两次，agent 没发状态与发了状态（替身 `status_proxy_agent.py`）。没发状态时：行画出来了；脚本跑了，载荷有会话 id、目录、模型、版本、`workspace.current_dir`；`context_window` 是空对象，`cost` 里只有 `total_duration_ms`，没有 `used_percentage`、`context_tokens`、`context_window_size`、`total_cost_usd`（探针查的是「没有」，不是 0）。发了状态时：行显示 agent 发的，不是客户端知道的。对比 r11：同样 4 个运行，都是「行不画」。
- *没有验证的。* Windows 与 macOS（探针只在 Linux 上跑）；真模型下的行（用的是 mock，行里的数据与模型无关）；真实的、会发 `session_status` 的 agent（只有替身）；`pi-pager` 以外的九个 suite（改动都在 `pi-pager` 里；CI 跑了，见上）。
- *这一步改了什么。* `tui/crates/codegen/pi-pager`：`src/app/{status_line.rs,status_line_policy.rs,status_line_tests.rs}`、`src/app/agent_view/{mod.rs,session.rs}`、`src/app/dispatch/tests/status_line.rs`、`docs/user-guide/25-status-line.md`；`scripts/tui_pty/{status_line_probe.py,README.md}` 与 `scripts/tests/test_tui_pty.py`；`CHANGELOG.md`（`[Unreleased]`）；计划与本文件。没有改 `packages/`，没有改 `scripts/tui_baseline.toml` 与 `docs/baselines/tui.md` 的数。

**续 8：合入 #18 与 #19，放进 V0.5（2026-10-13）。** 续 7 之后，用户说：「你把 2 个 PR 合并到 main，放进 V0.5。不需要再重复本地测试了，然后帮我打 tag 发布。」这改了先前的两条约定：「PR 只开不合，进不进 V0.5 他看过再定」与「不自己打 tag」；用户在这条消息里明确改了它们。本节前几条写到打 tag 之前；打 tag 与发布的结果记在「合入放进 V0.5 的两个 PR」之后。

- *先查的。* `main` 没有分支保护（`branches/main/protection` 返回 404）；有一条仓库规则集（`defalt`，只对 `main`），里面只有两条规则：禁止删除、禁止非快进推送。它不要求 PR，不要求检查，所以合并不受限。仓库允许 squash、合并提交与变基三种合并，合并后自动删头分支。#18、#19 的检查都是绿的，`mergeStateStatus` 都是 CLEAN。又读了三个工作流。`release.yml` 的 `publish-pypi` 只依赖 `build`，不等 TUI 的两个构建，所以 TUI 构建失败时 PyPI 仍会发出：先在最终的 `tui/` 树上跑演练，再打 tag。`tui-ci.yml` 有路径过滤（`tui/**`、`scripts/tui_baseline.*`、它自己、`pi_agent_cli/**` 与 `_tool_agent.py`），只改文档的提交不触发它，`ci.yml` 照常触发。两个工作流都有按引用取消的并发组：同一个 PR 再推一次，上一次还没跑完的运行会被取消。
- *合入 #18。* 合入前把 PR 正文最后一段（「不合入，你定」）改成「按用户的决定合入，进 V0.5」；用 `--match-head-commit` 钉住头 `1cd21d3`，squash 合入，标题带 `(#18)`，与 #14–#17 一样：`ec7d455`。核对：合并提交的树与 PR 头的树相同（`0cb6714`）。
- *合入 #19 之前。* #19 基于合入 #18 之前的 `main`（`62b0a0c`），所以在三个文档文件上与 #18 冲突，共 4 处：`CHANGELOG.md` 的 `[Unreleased]`；计划与执行记录里的 r12 修订记录；执行记录里续 6 与续 7 的插入位置。代码没有冲突（两个 PR 动的文件不重叠；`test_tui_pty.py` 与 `scripts/tui_pty/README.md` 两边都改了，git 自己合上了）。解法：在 #19 的分支里合入 `main`，不变基，不强推（合入时 squash，历史不留）。顺序是 #18 的文字在前、#19 的在后：续 6 在续 7 前，`Fixed` 在 `Changed` 前，修订记录里两句接起来，「没做」的尾巴只留一份。合并结果 `1fa18cf`。核对：它相对 `origin/main` 的差，正是 #19 的 14 个文件，没有多出的。`ruff check .` 与 `ruff format --check .` 通过。没有跑 pytest 与 cargo（用户说不用）。
- *重做条件 2、3。* `tui/` 又变了（#18、#19），所以在 `1fa18cf` 上同时开了两个运行，不等合入：`Release` 的手动演练（[run 38353038777](https://github.com/zy1233/pi-python/actions/runs/38353038777)）与 `TUI CI` 的 `release_baseline`（[run 38353042677](https://github.com/zy1233/pi-python/actions/runs/38353042677)）。这两个运行在分支上，不在 PR 的并发组里，之后的推送取消不了它们。这一步之后只改文档，`tui/` 与 `packages/` 不动，所以两个运行的树就是要发布的树。
- *结果：合入前，在 `1fa18cf` 上。* `CI`（[run 38353039688](https://github.com/zy1233/pi-python/actions/runs/38353039688)）与 `TUI CI`（[run 38353039701](https://github.com/zy1233/pi-python/actions/runs/38353039701)）通过。`Gates + cargo check`：警告 0，`leader` 245/245，`x.ai` 304/304，依赖图 980/980；`cargo test`：16 个测试集，8,700 通过 / 0 失败 / 22 忽略（`pi-pager` 5,643 通过 / 13 忽略；比 8,665 多的 35 个是 #18 的 19 个与 #19 的 16 个）。体积（[run 38353042677](https://github.com/zy1233/pi-python/actions/runs/38353042677)）：`zypi` 420,667,200 B（401.2 MiB），不大于 v0.4.0 的 470,461,776 B，小 10.6 %，比 r12 开头的 420,767,096 B 小 99,896 B；冷缓存构建 1,194 s；`--version` 中位数 22.9 ms。发布演练（[run 38353038777](https://github.com/zy1233/pi-python/actions/runs/38353038777)）成功，共 30 m 46 s：测试 2 m 19 s，Python 包 25 s，Linux x86_64 构建 15 m 07 s，macOS arm64 构建 28 m 12 s；`publish-pypi` 与 `github-release` 跳过；三个产物保留到 2027-01-11。
- *结果：合入后，在 `main` 上。* `ec7d455`（#18 的 squash 提交）：[`CI`](https://github.com/zy1233/pi-python/actions/runs/38352729921) 与 [`TUI CI`](https://github.com/zy1233/pi-python/actions/runs/38352729867) 通过。`9058ab3`（#19 的 squash 提交，`tui/` 的最后一次改动）：[`CI`](https://github.com/zy1233/pi-python/actions/runs/38354903178) 与 [`TUI CI`](https://github.com/zy1233/pi-python/actions/runs/38354903172) 通过；`cargo test` 16 个测试集，8,700 通过 / 0 失败 / 22 忽略，`Gates` 警告 0、`leader` 245/245、`x.ai` 304/304、依赖图 980/980，与合入之前的运行相同。两个 squash 提交的树与各自 PR 头的树相同（`0cb6714`、`7a69acd`），所以合入之前的运行说的就是合入之后的树。
- *放进 V0.5。* 做成一个只改文档的 PR（分支 `docs/v0.5-include-export-status-row`，基于 #19 合入之后的 `main`，即 `9058ab3`；只改文档，所以 `TUI CI` 不会为它运行）。`CHANGELOG.md`：#18 的 `Fixed` 条与 #19 的 `Changed` 条从 `[Unreleased]` 并进 `[0.5.0]`；日期改成 2026-10-13；`zypi export` 的已知问题删去；`[ui.status_line]` 的已知问题从「什么也不画」改成「`context` 与 `cost` 两项仍空」（那是真的，条目里的「它不显示什么」一段也这样写）。`[0.5.0]` 现有 Added 16、Fixed 32、Changed 14、Removed 2、已知问题 6 条；用 `release.yml` 里同一段 `awk` 预览（输入是 LF 文本）：正文 49,429 B，五个分组。再查了 `[0.5.0]` 里有没有被这两个 PR 改成过时的条目（查过的词：`export`、`status_line`、`status row`、`session_status`、`resume_matrix`、`GAP`、`probe`、`tui_pty`）：有一条，Added 里状态行探针的条目还写着 Python agent 下什么也不画，与 Changed 里的状态行条目相反；在打 tag 之前的一个小 PR 里改成了「写的时候是这样，现在画得出来，探针查的是这个」，正文变成 49,640 B。计划：头部与「当前状态」、里程碑的「是什么」一段、条件 1、2、3、5、6、已知问题的一段与两条、下一步、待决策的列表与 1、6、15 三项、1.R1、C.1、§10 索引、附录 A 的两行、修订记录、两个链接定义。`docs/baselines/tui.md`：新的 release 体积。
- *合入放进 V0.5 的两个 PR。* 上一条的文档 PR 是 [#20](https://github.com/zy1233/pi-python/pull/20)，状态行探针条目的修正是 [#21](https://github.com/zy1233/pi-python/pull/21)。两个 PR 的检查都通过（`Ruff`，`pytest` 在 Python 3.11、3.12、3.13 上；`TUI CI` 因为只改文档不触发）。squash 合入，标题带 `(#N)`，用 `--match-head-commit` 钉住头：`6b56286`（#20，06:30 UTC）、`be3f8a8`（#21，06:38 UTC）。核对：两个合并提交的树与各自 PR 头的树相同；`git diff 1fa18cf be3f8a8 -- tui packages pi_agent_core pyproject.toml scripts .github` 为空，所以演练与 `release_baseline` 量的树，就是发布的树。
- *打 tag。* 打 tag 之前，[`CI`](https://github.com/zy1233/pi-python/actions/runs/38357817115) 在 `be3f8a8` 上通过（含 `Ruff`）。`git tag -a v0.5.0`：带注释的 tag（v0.3.0、v0.4.0 的 tag 也是带注释的），tag 对象 `d226977`，指向 `be3f8a8`，`git push origin v0.5.0` 在 06:41:35 UTC。
- *`Release` 的运行。* tag 的 `push` 触发 [run 38358144687](https://github.com/zy1233/pi-python/actions/runs/38358144687)，跑了三次。第 1 次：`test` 2 m 19 s、`build`（六个 Python 包）23 s、`publish-pypi` 22 s、Linux x86_64 构建 20 m 49 s 成功；macOS arm64 构建失败在「Install protoc」一步（18 s），没有开始编译，`github-release` 因此跳过。原因：`release.yml` 里的 `arduino/setup-protoc@v3`（Linux 与 macOS 各一处）没有 `repo-token`，它没有认证地调 GitHub 的 API 查 protoc 的版本；macOS 运行器共用出口地址，额度用完了（日志：`API rate limit exceeded`）。`tui-ci.yml` 里同一步的三处都带 `repo-token: ${{ secrets.GITHUB_TOKEN }}`，所以 `TUI CI` 没有这个问题。运行没有结束时，GitHub 不接受只重跑一个 job（403）；运行结束后用 `gh run rerun 38358144687 --failed`：第 2 次的 macOS 构建在同一步又失败（22 s）；再重跑一次，第 3 次通过（macOS 构建 25 m 57 s），`github-release` 48 s。重跑用的是 tag 提交里的工作流文件，所以没有改 `release.yml`：改了就要移动 tag，那是比重跑大的动作，也该先问用户。
- *核对发布的结果。* ① PyPI：六个包都在，每个有 wheel 与 sdist（`pi-agent-core-lc`、`pi-agent-harness-lc`、`pi-agent-cli-lc` 是 0.5.0；`pi-web-access-py`、`pi-goal-x-py`、`pi-dynamic-workflows-py` 是 0.2.0），文件大小与 GitHub Release 上的一致。刚发布时包的总览 JSON（`/pypi/<包>/json`）还显示旧版本，是缓存；逐版本的 JSON（加一个查询参数避开缓存）与 `/simple/<包>/` 已经是新的；之后总览也更新了。② 装进干净的 venv（Windows，Python 3.12.7）：六个包都从 PyPI 装得上，`uv pip check` 通过，`python -m pi_agent_cli` 对 `initialize` 回答的 `agentInfo.version` 是 `0.5.0`。③ GitHub Release [v0.5.0](https://github.com/zy1233/pi-python/releases/tag/v0.5.0)：标题 `pi-python v0.5.0`，不是草稿也不是预发布，2026-10-13 07:34 UTC 发布，是 `releases/latest`；14 个文件（6 个 wheel、6 个 sdist、`zypi-linux-x86_64` 420,720,608 B、`zypi-macos-arm64` 49,272,472 B）。正文与 tag 提交里 `CHANGELOG.md` 的 `[0.5.0]` 段，按 `release.yml` 的做法（`awk`，去掉结尾的换行，再加一个）得到的文字逐字节相同：49,639 B。④ 发布的 Linux 二进制：从 Windows 下载 GitHub 的发布文件时连接卡住，所以在 WSL2 里下载；大小对得上；`zypi --version` 打印 `zypi 0.5.0 (be3f8a8e5e20)`；sha256 `9be1d218c49a87b61339a745c355c15ef7a63c4fb734ab9fb178919e7a507203`。配 PyPI 上的 `pi-agent-cli-lc` 0.5.0（不是仓库里的源码），用 `scripts/tui_pty/` 的脚本（每个脚本自己建临时的 `PI_HOME`、`HOME` 与工作目录）：`smoke.py` 23/23；`smoke.py --sandbox workspace` 26/26；`status_line_probe.py` 4/4；`resume_matrix.py` 11 通过 / 2 GAP / 0 失败（两个 GAP 是 `--resume <标题>` 与 `--session-id <已用的 id>`，都在 `CHANGELOG.md` 的「已知问题」里；`export <id>` 与 `export <未知 id>` 两个用例通过）；`exit_matrix.py` 12/12 个用例没有留下进程。
- *没有解释的差别。* 发布的 `zypi-linux-x86_64` 比 `release_baseline` 量到的多 53,408 B（420,720,608 B 对 420,667,200 B），两次构建的 `tui/` 树相同；差在哪里没有查。`zypi-macos-arm64` 只有 49,272,472 B，两边用同一个 `release-dist` 配置；原因也没有查。
- *没有做的。* 本地的 pytest 与 cargo 测试（用户说不用；`CI` 与 `Release` 的 `test` job 跑了）；macOS 二进制没有人运行过（构建通过，只证明它能编译与链接）；Windows 没有二进制；`TUI CI` 没有在打 tag 的提交上跑（只改文档的提交不触发它；`tui/` 的树与它通过的 `9058ab3` 相同）；`release.yml` 的 `repo-token` 没有加（要先问用户）。

### 10.16 r13 追加：ADR1 的 PR 2 与 PR 3

用户在 2026-10-16 说：「完成 ADR1 的落地，决策 2、4、5、7 按你的倾向来，我看过比较合适。」四项都按计划里写的倾向做。决策 10（删 `pi-shell` 里的旧存储层，阶段 B）不在答复里，这一轮没有动它。做成两个 PR，都没有合入 `main`：PR 2（[#23](https://github.com/zy1233/pi-python/pull/23)，分支 `feat/adr1-resolve-startup`，基 `main`）加新的解析路径；PR 3（[#24](https://github.com/zy1233/pi-python/pull/24)，分支 `refactor/adr1-drop-session-disk-reads`，叠在 PR 2 上）删旧的读盘代码，并加一道门禁。

| 决策 | 答复 | 做在哪 |
|---|---|---|
| 2 沙箱顺序 | 去掉「恢复时沿用保存的沙箱 profile」 | PR 3 |
| 4 `--continue` 的「最近」 | 最近活动：`updatedAt` 最大，翻完所有页 | PR 2 |
| 5 未知 id 的报错时机 | 进 TUI 之前报错，退出码 1 | PR 2 |
| 7 模式映射 | (a) 保持（r10 已做，PR [#12](https://github.com/zy1233/pi-python/pull/12)）；(b)、(c) 另议 | PR 3：Plan 预览读 `plan.md` 的代码删了 |

**PR 2：`resolve_startup`。** 只加新的路径，旧代码留到 PR 3；所以 PR 2 单独合入也是完整的（旧代码对 Python 会话读不到东西，没有害处）。

*顺序。* 以前：`apply_sandbox` → `app::run` → `materialize_startup`（读盘）→ `acp::connect`。现在：`apply_sandbox` → `app::run` → `acp::connect` → `resolve_startup`（只问 agent）→ 事件循环。终端在 `connect` 之前就准备好了，所以解析出错走与 `connect` 失败同一条路：还原终端、停 agent、返回 `Err`，由 `main` 打印并退出 1（信息本身不以 `Error:` 开头，`main` 会加）。整个解析有 30 s 的上限，与 `connect` 的预算是同一个值（`GROK_CONNECT_UI_TIMEOUT_SECS`）；到时间，或收到信号（取消），都照样收尾退出，不会留一块空白屏。

*各旗标。*

| 旗标 | 读什么 | 找什么 |
|---|---|---|
| `--continue`、不带值的 `--resume` | 本目录的会话，翻完所有页 | `updatedAt` 最大的一个。时间相同取列表里靠前的（agent 把最后创建的排前面）；没有时间的排在有时间的后面。没有会话：`No session found for current directory`，退出 1 |
| `--resume <id>` | 所有目录的会话，找到就停 | 这个 id（UUID 按 UUID 比较，不管大小写与连字符）；取这一行的 `cwd` 交给 `session/load`（与选择器一样）。没有：`Session does not exist: <id>`，退出 1 |
| `--resume <标题>` | 本目录的会话，翻完所有页 | 标题相同的一个（不分大小写，去掉两端空白）；同一个标题有两个以上的会话是错误，信息里列出它们的 id 与最后活动时间。没有：`Session does not exist: …`，退出 1 |
| `--session-id <id>` | 所有目录的会话，找到就停 | 什么也不找，id 必须没人用：有人用就 `Session ID <id> is already in use.`，退出 1；不是 UUID 也在这里拒绝。`session/new` 带 `_meta.sessionId` 照旧，agent 仍然拒绝重复（r10） |
| `--resume`（带值）与 `--session-id` 同时给 | — | `--session-id cannot be combined with --continue or --resume.`，退出 1 |

*边界。*

- 翻页：新的翻页器 `acp::session_list::walk_session_list` 取代选择器里原来的翻页循环，选择器与启动解析共用它。规则与 r9 一样：跟 `nextCursor`，空游标与重复游标当作结束，某页失败则整体失败；最多 `MAX_SESSION_LIST_PAGES`（100）页，即 5,000 个会话。到上限被截断时，`--resume` 的「找不到」信息多一句「列表被截断，会话可能在没读到的部分」。
- agent 不提供 `session/list`（`MethodNotFound`）：`--session-id` 跳过预检，由 `session/new` 拒绝重复（预检从不比 agent 更严）；`--continue`、`--resume` 报「could not list the sessions of the agent」，退出 1。
- r9 的表（§10.11 (4)）把不带值的 `--resume` 写成「选择器，不变」，不对：代码里它与 `--continue` 是同一个意图（`most_recent_for_cwd`），`resume_matrix.py` 的用例 7 期望的也是「回放」；选择器是 `/resume` 与 F3。内嵌指南 `17-sessions.md` 本来就这样写。r13 按代码写。

**PR 3：删旧代码，加门禁。**

删的（Rust，`pi-pager` 与 `pi-pager-bin`；行数是 `git diff --numstat 506769b…`，加 149 行，删 1,829 行）：

- `session_startup.rs`：`materialize_startup`（及 `_for_cwd`、`MaterializeCtx`）、`most_recent_session_id`、`find_most_recent_jsonl_session`、`resolve_existing_session`、`resolve_session_by_title`、`ensure_session_id_available`、`parent_session_is_worktree`（留下只看目录的 `cwd_is_worktree`）、远程恢复一支（`restore_session_from_remote`、`plan_remote_miss`、`RemoteMissPlan`、`in_place_restore_code_allowed`）与 16 个测试。`MaterializedStartup::Resume` 的 `deferred_local_miss`、`suppress_code_restore` 留着（agent 列出的会话恒为 `false`；远程与 worktree 的恢复归 1.P6）。
- `cli.rs`：`pin_local_resume_target`（及 `_for_cwd`）、`saved_resume_profile`（及 `_for_cwd`）、`ResumeTarget`、`SandboxStartup`、`startup_sandbox_profile`，与 3 个测试；`pi-pager-bin/src/main.rs` 的 `resolve_startup_sandbox`（钉目标、与 `--sandbox` 冲突）换成一行 `apply_sandbox(None, <旗标>, <目录>)`。这就是决策 2。
- `session_title_resolve.rs`：`TitleResolution`、`PinnedResumeTarget`、`presandbox_resume_target`、按旧摘要匹配的 `select_listed_by_title`；留下 `select_by_title`（按 `ListedSession` 匹配）。测试 16 → 8，改写成对 `ListedSession` 的用例。
- `effects/helpers.rs`：`extract_first_user_prompt`（读 `chat_history.jsonl`）与 `parse_session_picker_entries` 里把远程行改标本地的一段；`effects/mod.rs`：创建会话前的 `ensure_session_id_available`；`dispatch/session/load.rs`：选择器加载前的 `resolve_local_session*` 两个判断。
- `agent_view/plan.rs`：`plan_file_path` 与 Plan 预览读盘的退路（预览只用审批请求带来的内容；决策 7）；`views/file_search/line_viewer.rs`：随之无人调用的 `open_markdown(path)`；`test_util.rs`：`GrokHomeFixture`（81 行，没有用户了）。

*新门禁* `gates.session_disk_reads`（`scripts/tui_baseline.py` 与 `scripts/tui_baseline.toml`）：`pi-pager` 与 `pi-pager-bin` 的 `src/` 里，生产代码不得出现八个模式：`list_summaries`、`resolve_local_session`、`session_exists_for_cwd`、`resumed_session_sandbox_profile`、`local_summaries_for_cwd_sync`、`session::storage`、`persistence::session_dir`，以及拼会话目录的 `join("sessions")`。`session::persistence` 整体不在列表里：`sanitize_and_cap_title`、`is_forbidden_title_char` 是纯字符串函数，pager 有意用它们。测试文件（`tests.rs`、`*_tests.rs`、`test_util.rs`、`tests/` 目录）、内联的 `#[cfg(test)] mod … { … }` 块与注释行不查；只标在单个 item 上的 `#[cfg(test)]` 不会藏住它后面的代码。命中时报路径与行号。门禁在 `gates`、`check` 里跑，`pytest` 里也跑一遍。变异检查：把同一个扫描器指到 `12349d3`（`main`）得 18 处命中，指到 `506769b`（PR 2）得 17 处，指到 PR 3 得 0 处。

**13 处读盘调用点（§10.11 (2) 的表）的去向。**

| # | 位置 | 去向 |
|---|---|---|
| 1 | `most_recent_session_id` | PR 2 起 `--continue` 走 `resolve_startup`；PR 3 删 |
| 2 | `find_most_recent_jsonl_session` | PR 3 删 |
| 3 | `resolve_existing_session` → `resolve_local_session*` | PR 2 起 `--resume <id>` 由 `find_by_id` 解析；PR 3 删 |
| 4 | `resolve_session_by_title` | PR 2 起由 `list_all` 加 `select_by_title`；PR 3 删 |
| 5 | `ensure_session_id_available` | PR 2 起由 `find_by_id` 查，agent 仍拒绝重复；PR 3 删（含 `effects/mod.rs` 里创建会话前的那一次） |
| 6 | `parent_session_is_worktree` | PR 3 删读文件的一段，留 `cwd_is_worktree` |
| 7 | `presandbox_resume_target`、`pin_local_resume_target` | PR 3 删（决策 2） |
| 8 | `saved_resume_profile_for_cwd`、`resumed_session_sandbox_profile` | PR 3 删（决策 2） |
| 9 | `app/mod.rs` 里 `list_summaries(None)` 补标题 | PR 2：标题取自 `session/list` 的那一行 |
| 10 | `dispatch/session/load.rs` | PR 3 删两个判断 |
| 11 | `effects/helpers.rs` 的 `extract_first_user_prompt` 与远程行改标 | PR 3 删 |
| 12 | `export_cmd.rs` 的 `load_updates_for_replay` | r12 续 6，PR [#18](https://github.com/zy1233/pi-python/pull/18)，已合入 |
| 13 | `agent_view/plan.rs` 的 `plan_file_path` | PR 3 删（决策 7） |

**对着真 zypi：13 例恢复矩阵（WSL2，`resume_matrix.py --strict`）。** 13 PASS / 0 GAP / 0 FAIL。变化的用例：

| # | 用例 | r9 | r13 |
|---|---|---|---|
| 5 | `--resume <标题>`，在会话的目录 | GAP（只匹配旧 agent 的摘要） | PASS |
| 8 | `--resume <未知 id>` | PASS，但进了 TUI 才报 | PASS，要求「进 TUI 之前退出 1」（决策 5） |
| 10 | `--session-id <新 uuid>` | GAP | PASS（r10） |
| 11 | `--session-id <已用的 id>` | GAP | PASS：`Session ID <id> is already in use.`，退出 1，没有新会话 |
| 12 | `zypi export <id>` | GAP | PASS（r12 续 6） |

用例 1（`--continue`）在 r9 时只因为旧代码的退路碰巧读得懂 Python 的文件头才通过；现在它走 `session/list`。数字：r9 的 9 PASS / 4 GAP → r10 的 10 / 3 → r12 续 6 的 11 / 2 → r13 的 13 / 0。

**实测：翻页的代价**（`scripts/session_list_bench.py`，合成 home，每项 3 次中位数，括号里是页数；每页 50 个会话，三分之一的会话属于本目录）。`--continue` 与 `--resume <标题>` 取本目录的页：表里「带 `cwd` 翻完」，脚本里是 `pages`。`--session-id` 与（找不到时的）`--resume <id>` 取所有目录的页：「不带 `cwd` 翻完」，脚本里是新加的 `walk all`。`--resume <id>` 找到就停，所以最坏（id 在最老的一页）才等于 `walk all`：

| 会话数 | 本目录的 | `--continue`：带 `cwd` 翻完（Linux） | 同（Windows） | `--session-id`：不带 `cwd` 翻完（Linux） | 同（Windows） |
|---|---|---|---|---|---|
| 100 | 34 | 27 ms（1 页） | 50 ms（1 页） | 42 ms（3 页） | 90 ms（3 页） |
| 1,000 | 334 | 641 ms（7 页） | 814 ms（7 页） | 1,299 ms（21 页） | 1,787 ms（21 页） |
| 5,000 | 1,667 | 10,159 ms（34 页） | 12,287 ms（34 页） | 28,165 ms（101 页） | 33,877 ms（101 页） |

每一页的代价随**总**会话数涨：agent 对每次 `session/list` 都重新列一遍目录，并为每个文件建一个 `FileInfo`（`JsonlSessionRepo._session_names` → `LocalExecutionEnv.list_dir`）。所以翻完所有页的代价是「页数 × 目录大小」，增长得比会话数快：会话数 ×5（1,000 → 5,000）时，不带 `cwd` 的整趟从 1.3 s 涨到 28.2 s（Linux），从 1.8 s 涨到 33.9 s（Windows）。33.9 s 超过 30 s 的上限：在 Windows 上有约 5,000 个会话时，`--session-id` 会因为超时而失败（信息里点了 `GROK_CONNECT_UI_TIMEOUT_SECS`，可以调大）。`--continue` 与 `--resume <标题>` 只取本目录的页，5,000 个会话（本目录 1,667 个）时要 10–12 s。1,000 个会话以内都在 2 s 以内。

每页的代价几乎全在目录列表：同一个目录用 `os.listdir` 只要 2–11 ms（`packages/pi-agent-cli/AGENTS.md`）。试了一个草稿来看修法值多少（**没有提交，已还原**；只在 Windows 上跑，每项 1 次，不是 3 次中位数）：`_session_names` 只取文件名，不建 `FileInfo`。5,000 个会话时：带 `cwd` 翻完 12.3 s → 2.2 s，不带 `cwd` 翻完 33.9 s → 3.6 s，`session/load` 372 → 59 ms，带 id 的 `session/new` 325 → 33 ms；1,000 个会话时 0.81 s → 0.37 s、1.79 s → 0.57 s。修法在 Python 一侧（`pi-agent-harness`），这一轮没有做；新增待决策 18。

**验证。**

- PR 2 的新测试 32 个。`session_resolve_tests.rs` 25 个，对一个脚本化的 agent：最近活动不是第一行、时间相同取靠前的、没有时间的排后面、翻完所有页、本目录没有会话、不带值的 `--resume` 就是 `--continue`、按 id 找时问所有目录且找到就停、UUID 不分大小写、未知 id 点名、被截断的列表在错误里说明、标题不分大小写、同一个标题有两个会话时列出它们、空白标题什么也不匹配、某一页失败则整体失败、`--session-id` 的各种情形（不是 UUID、已用、空闲、agent 不提供 `session/list`、列表出错）。`app/mod.rs` 里 4 个，测 30 s 上限与取消：结果照原样交回、拒绝照原样交回、agent 不答时到点放弃并点出环境变量、取消时不等查完。`python_agent_e2e.rs` 里 3 个，对着真的 Python agent：`--continue`、`--resume <id>`、`--resume <标题>` 的解析；同一个标题有两个会话是歧义；`--session-id` 对已用的 id 拒绝、对空闲的 id 用 agent 兑现的 id，之后它就算已用。
- PR 3 净少 27 个测试（`session_startup.rs` 16、`cli.rs` 3、`session_title_resolve_tests.rs` 8，都是被删代码的用例），门禁加了 6 个 pytest 测试函数，共 10 个用例（`scripts/tests/test_tui_baseline.py`；其中「测试文件不查」带 5 个参数）：命中报路径与行号、测试文件不查、注释与内联测试模块不查、单个 item 上的 `#[cfg(test)]` 不藏住后面的代码、清单的格式、本树里零命中。
- WSL2（Linux，`$HOME/pi-verify`，ext4）：PR 2 的整套清单 **8,730 通过 / 2 失败 / 22 忽略**，`pi-pager` 5,675 通过 / 0 失败 / 13 忽略；那 2 个失败都是计时用例，在这两个 PR 没有碰的 crate 里：`pi-shell` 的 `auth::manager::lock::tests::dropping_the_guard_silences_the_heartbeat_before_anyone_else_can_hold_the_lock`（心跳）与 `pi-pager-render` 的 `tmux_probe::tests::successful_near_deadline_exit_still_returns_captured_output`（清单里的 `known_failures`）。它们单独跑都过（`pi-shell` 的 `lock::tests` 21 通过 / 1 忽略；`tmux_probe::tests` 7 通过）；整套那一次机器同时在编译，所以只能说「负载下偶发」，没有查心跳用例的根因。PR [#23](https://github.com/zy1233/pi-python/pull/23) 的 CI（`506769b`，`TUI CI` [run 38889657279](https://github.com/zy1233/pi-python/actions/runs/38889657279)）：10 个 suite 合计 **8,732 通过 / 0 失败 / 22 忽略**（= 合入 #18、#19 之后 CI 的 8,700 加 32），`pi-pager` 5,675，那 2 个计时用例在 runner 上都通过；`CI`（Ruff、pytest 3.11 / 3.12 / 3.13）也通过。PR 3：整套 10 个 suite 在最终提交（`9600cdf`）上用基线执行器跑了一遍（清环境，`PI_E2E_PYTHON` 与 `PI_E2E_REQUIRED=1`，WSL2，增量编译，14 m 18 s）：**8,705 通过 / 1 个已知失败 / 21 忽略**，退出码 0。`pi-pager` 5,649 通过 / 0 失败 / 12 忽略（含 `python_agent_e2e` 的 14 个），`pi-pager-bin` 21，`pi-shell` 1,056，`pi-pager-render` 1,091，`pi-pager-minimal` 79，`pi-acp-lib` 21，`pi-http` 13，`pi-telemetry` 239，`pi-file-utils` 216，`pi-sampling-types` 320。比 PR 2 的 5,675 / 13 忽略，`pi-pager` 少 26 个通过、1 个忽略（被删代码的用例；忽略的那个是 `materialize_resume_id_without_chat_still_resolves_on_disk`）。那 1 个已知失败是 `pi-pager-render` 的 tmux 探测（清单里的 `known_failures`；清单里的说明：机器一卡就失败，在 WSL2 上多是刚链接完的测试二进制的第一次运行，这一次也是这样）；其余 9 个 suite 都等于各自的 `reference_passed`，所以清单里的和是 8,706（8,705 加那一个）。这个提交的前一版（同样的生产代码）上，先单独跑过 `pi-pager` 与 `pi-pager-bin`，数相同（5,649 / 12 忽略；21），6 m 39 s。PR [#24](https://github.com/zy1233/pi-python/pull/24) 的 CI（`79e01ee`，`TUI CI` [run 38896545799](https://github.com/zy1233/pi-python/actions/runs/38896545799)）：10 个 suite 合计 **8,706 通过 / 0 失败 / 21 忽略**（= 清单里的和；`pi-pager-render` 的 tmux 探测在 runner 上通过）；`cargo test` job 16 m 31 s，`Gates + cargo check` 3 m 50 s；`CI`（Ruff、pytest 3.11 / 3.12 / 3.13）也通过。
- `check`（门禁 + 消费者构建 + `cargo check --workspace --tests`）：所有门禁通过，告警 0，类型检查 0 个错误。第一遍有 1 条告警（`LineViewerState::open_markdown` 随预览读盘一起变成无人调用），删掉它之后为 0。
- PTY 脚本（真 `zypi`，mock 模型，WSL2）：`resume_matrix.py --strict` 13 PASS / 0 GAP / 0 FAIL（退出 0）；`smoke.py` 23/23；`exit_matrix.py` 12/12；`print_exit.py` 全部通过；`mode_probe.py`；`status_line_probe.py` 4/4；`net_audit.py --mock` 13/13。这六个脚本的退出码都是 0。`zypi` 是在 PR 3 的代码上建的（debug 构建，WSL2，2 m 18 s）。
- Python 测试，Windows 与 Linux 各跑一遍：完整的 `pytest`（`real_llm` 不选）：Windows 2,531 通过 / 39 跳过 / 31 不选（5 m 50 s）；Linux（WSL2，`9600cdf`，我自己的 venv，装了 `AGENTS.md` 里重建 venv 清单的全部包）2,540 通过 / 30 跳过 / 31 不选（1 m 42 s）。`scripts/tests` 单独在 Windows 上 184 通过 / 8 跳过（PR 2 时 174 通过）。`ruff check .` 与 `ruff format --check .` 通过。

**没有验证 / 留下的。**

- **空会话。** Python 在 `session/new` 时就建会话文件，所以没发过消息的会话以标题 `(no messages)` 留在列表里，`zypi --continue` 在它是本目录最近活动的会话时会取到它。pager 里没有特判（特判会把 agent 建文件的时机带进 pager，也会漏掉真的空会话）；真正的修法在 Python：第一条用户消息时才写文件。新增待决策 17。内嵌指南 `17-sessions.md` 照实写了。
- `--continue` 在列表被截断时（本目录超过 5,000 个会话）取的是读到的那一部分里最近的，不提示；`--resume <标题>` 与 `--resume <id>` 会在「找不到」里说。没有用 5,000 个以上的会话试过。
- **翻页的代价增长得比会话数快**（上表）：每一页 agent 都重新列一遍目录，总代价是页数 × 目录大小。1,000 个会话以内都在 2 s 以内；5,000 个会话时 `--continue` 要 10–12 s，`--session-id` 要 28–34 s，在 Windows 上超过 30 s 的上限（会超时，信息里点了 `GROK_CONNECT_UI_TIMEOUT_SECS`）。`--session-id` 对每个新建会话付这笔钱，没有缓存。决策 4 与 ADR1 接受了「翻完所有页」，但那时看的数是 1,000 个会话约 0.8 s（带 `cwd`）；5,000 个会话时的数这一轮才第一次有。修法在 Python 一侧（草稿的数在上表下面），没有做；新增待决策 18。
- 选择器的 30 天截止（决策 9）与「等所有页回来再画」（决策 11）没动。`--resume <id>` 不受 30 天限制。
- `--resume <id>` 从别的目录起：pager 现在把会话自己的目录交给 `session/load`，agent 在那里干活；TUI 一侧（`@` 搜索、diff、git 信息）用哪个目录没有验证（r9 起就是这一项，§10.11）。
- 只在 Linux（WSL2）上跑了 Rust 测试与 PTY 脚本；没有 Windows 与 macOS 的二进制，也没有用真实 LLM（e2e 与 PTY 用的是 mock 模型）。
- 决策 10（删 `pi-shell` 里的 `session::{persistence, storage}`）没动：`tui-support.md` 重生成后，`session::storage` 的外部引用是 0，`session::persistence` 是 12 → 3（只剩清洗标题的纯函数），`session` 25 → 16。

**新的提议（17、18）。** 17：空会话（Python 在第一条用户消息时才建文件）。18：翻页的代价（Python 一侧的 `_session_names` 只取文件名；草稿的数在上面「实测」）。两项都写在计划的「待决策」里，都没有动手。

### 10.17 r14 追加：空会话（决策 17）与四个答复

用户在 2026-10-18 答复了四个问题。决策 10 这一轮还没有动手；做的时候写在 §10.18。

| 问题 | 答复 | 去向 |
|---|---|---|
| #23、#24 怎么办 | 现在合入 | 都已合入 `main`（见「合入」） |
| 决策 18：翻页的代价 | 不做 | 计划里记为「已定，不做」；没有改代码 |
| 决策 17：空会话 | 做 | PR [#25](https://github.com/zy1233/pi-python/pull/25)（分支 `fix/write-session-at-first-message`，基 `main`，没有合入）：本节 |
| 决策 10：删 `pi-shell` 里的旧存储层（阶段 B） | 做 | 这一轮还没做；r15 做了：PR [#26](https://github.com/zy1233/pi-python/pull/26)，见 [§10.18](#1018-r15-追加删-pi-shell-的旧存储层决策-10) |

**合入。**

- #23（分支头 `506769b`）先 squash 合入：`7e0ca5a`。这个提交上，`CI`（[run 39141261174](https://github.com/zy1233/pi-python/actions/runs/39141261174)）与 `TUI CI`（[run 39141261125](https://github.com/zy1233/pi-python/actions/runs/39141261125)）都通过。
- #24 叠在 #23 上。#23 合入之后，把 #24 的基改成 `main`，再把 `main` 合进它的分支（`git merge -s ours`：`main` 的树就是 #23 最后一个提交的树，分支的树不变）。分支头 `43c9a88` 上，`TUI CI`（[run 39141385047](https://github.com/zy1233/pi-python/actions/runs/39141385047)）是 8,706 通过 / 0 失败 / 21 忽略，`CI`（run 39141385046）通过。然后 squash 合入：`0476ea4`。这个提交上，`CI`（[run 39143111042](https://github.com/zy1233/pi-python/actions/runs/39143111042)）与 `TUI CI`（[run 39143111087](https://github.com/zy1233/pi-python/actions/runs/39143111087)）都通过。

**决策 18：不做。** 1,000 个会话以内够用（`--session-id` 在 Linux 上 1.3 s，在 Windows 上 1.8 s）。5,000 个会话时，`--session-id` 在 Windows 上要 33.9 s，超过 30 s 的上限；这是已知的，`CHANGELOG.md` 的 `[Unreleased]` 写了这些数。§10.16 里没有提交的草稿（`_session_names` 只取文件名）原样留着；要改主意，它的数仍然适用。

**决策 17：做法。** §10.16 的问题：Python 在 `session/new` 时就建会话文件，所以没发过消息的会话以 `(no messages)` 留在列表里，`zypi --continue` 在它是本目录最近活动的会话时会取到它。pager 里不特判（r13 的理由：特判会把 agent 建文件的时机带进 pager，也会漏掉真的空会话）。用户答复：在 Python 里修。

*存储层*（`pi-agent-harness`：`session/jsonl_storage.py`、`session/jsonl_repo.py`）：

- 新类 `DeferredJsonlSessionStorage(JsonlSessionStorage)`。第一个 `message` 条目之前的条目（例如 `model_change`）只放在内存里。第一条消息到来时，文件头、攒下的条目和这条消息放进**一次** `write_file`。
- 先写、后记录：写失败时什么也没有记，攒下的条目还在，下一条消息再试一遍。
- 一把 `asyncio.Lock` 把追加串行起来：同时来两条「第一条消息」，文件只写一次，两条都留下。
- `written` 属性说文件是否在盘上。写过之后，每个条目照旧追加。
- 文件头的时间、文件名里的时间和 `session/new` 的时间是同一个（`new_session_header(…, timestamp=created_at)`；以前文件头自己再取一次时间）。
- 只有 `JsonlSessionRepo.create({"deferred": True, …})` 返回延迟的存储。不带 `deferred` 的 `create`（`fork` 也用它）不变，立即写。

*没有文件的会话，对每个 ACP 方法是什么样*（`pi-agent-cli`：`agent.py`）：

| 方法 | 做法 |
|---|---|
| `session/new` | 只在内存里建会话，不写盘。带 `_meta.sessionId`（`--session-id`）时，agent 在会话打开期间记着这个 id（`_reserved_ids`） |
| 第一次 `session/prompt` | 写文件。带选定 id 的会话，写之前再查一次 `repo.has_session(id)`：别的进程这期间先存了同一个 id，就拒绝（`invalid_params`，`a session with this id already exists`）。检查放在 `PiAcpAgent.prompt()`，因为在 harness 里失败只会变成含糊的 `AgentHarnessError` |
| `session/list` | 不列出 |
| `session/load`、`session/resume` | `resource_not_found` |
| 第二个带同一个 id 的 `session/new` | 拒绝（`invalid_params`）：id 在 `_reserved_ids` 里，或者有文件用着它（`_create_session_with_id`，在 `_chosen_id_lock` 下） |
| `session/close`、`pi/session/delete` | 释放 id。`pi/session/delete` 照旧幂等：没有文件也答成功（`deleted: true`） |
| `_bind_session` 失败 | 释放 id |
| `repo.has_session(id)` | 没有文件就是假 |
| `fork` | 不变（立即写） |

*空隙。* 两个 agent 进程共用一个 home：A 在内存里建了带选定 id 的会话，B 这期间把同一个 id 存成了文件。A 的第一次 `prompt` 在写之前会再查一次，所以拒绝。这缩小了空隙，没有堵死：两个进程在「查」与「写」之间仍然可能同时写。

*旧文件。* 旧版本 agent 写的、只有文件头的文件仍在列表里，标题 `(no messages)`。没有清理它们。

**TUI 一侧的事实**（WSL2 探测，真 `zypi`，mock 模型）：

- 不带旗标的 `zypi`：第一条消息之前不发 `session/new`，所以没有会话。
- `zypi --session-id <id>`：启动时立即发 `session/new`。
- `/new`：立即发 `session/new`，屏幕上印 `Session <id>, use /resume to switch between sessions`。
- 模型名（输入框下边框上的 `mock ─╯`）在 `session/new` 回答之后约 0.1–0.2 s 出现，是「TUI 起来了、agent 里有会话了」的可靠信号。欢迎页的字有时在轮询看到之前就没了；在 TUI 起来之前打的字会丢。斜杠命令要先让菜单画出来，再按回车。

**测试的改动。**

*Python：新增 16 个测试，`scripts/tests` 另加 12 个。*

- `test_deferred_session_storage.py`（新文件，10 个）：文件在第一条消息之前不存在；之前的条目攒着，写在消息前面；只攒了条目的会话什么也不留下；第一条消息之后每个条目都追加；文件头与文件名带着会话建立的时间；可以带父会话建立；写失败什么也不记、下一条消息写下全部；两条第一条消息同时来；条目 id 在写之前与之后都拒绝重复；不带 `deferred` 建的会话仍然立即写。
- agent 的新测试 6 个。`test_acp_stdio_contract.py` 1 个，对着真的 agent 进程：开了不聊的会话不在列表里，`session/load` 报错，重启之后也一样；聊过的会话在。`test_session_chosen_id.py` 5 个：没有文件时 id 仍被占着，关闭后空闲；删除没有文件的会话会释放它的 id；另一个进程这期间占了 id，第一次 prompt 被拒绝；带选定 id 的会话能收不止一条消息；打不开的会话不占着 id。
- 已有的测试：凡是期望「会话在列表里、能 `load`」的，现在先发一条消息（`test_acp_agent.py`、`test_acp_stdio_contract.py`、`test_acp_stdio_tools.py`、`test_mcp_notice.py`、`test_project_trust_prompt.py`）。`test_list_sessions_titles_follow_the_first_prompt` 里空会话的断言，由「标题 `(no messages)`」改成「不在列表里」。

*PTY 恢复矩阵（`scripts/tui_pty/resume_matrix.py`）：13 → 16 例。* 新增三例：

| 新用例 | 要求 |
|---|---|
| `--session-id <新 uuid>`，不发消息 | 会话存在（模型名出现），盘上没有新的会话文件 |
| `/new`，不发消息 | 新会话存在（屏幕印出 `use /resume to switch between sessions`），盘上没有新的会话文件 |
| `/new`，再发一条消息 | 第一条消息之后多一个新会话文件，不是原来那个 |

原有的「`--session-id <新 uuid>` 用这个 id 起会话」也改了：先发一条消息，再看文件头带不带这个 id。用例可以带 `steps`（`Step(文字, 完成的标志)`）：`wait_for_run` 在 TUI 起来（模型名出现）时打第一步，屏幕显示上一步生效之后再打下一步，最后一步生效时这次运行结束。`scripts/tests/test_tui_pty.py` 加了 12 个测试，用假终端测这些用例与 `wait_for_run`。

*第一版是空的。* 第一版（14 例，提交 `014a282`）的新用例是「不带旗标启动、不发消息、盘上没有新文件」。把 agent 改回立即写之后，14 例照样全过：不带旗标的 `zypi` 在第一条消息之前不发 `session/new`，这个用例看不见 agent 的行为。探测过入口（上一节）之后，改成 `--session-id` 与 `/new`。改版时还碰到一处：所有用例共用一个 home，一个多出来的空会话会成为最新的一个，让后面的 `--continue` 与不带参数的 `--resume` 取到它，失败就落在别的用例上。所以两个 `/new` 用例都从 `--resume <id>` 起，不靠「哪个会话最新」；`--session-id` 的那个放在最前面，是有意的。

*另外两处调整。* `smoke.py`：重启之后，`/resume` 选择器以前把新的空会话排在第一行，脚本按一下 ↓ 跳过它；现在那个会话没有文件，第一行就是前一个会话，脚本不再按 ↓。`python_agent_e2e.rs`：必须出现在列表里的会话（分页测试的 51 个，id 检查里的 `existing`）先发一条消息；id 检查还断言 agent 对「没有文件的会话」的做法：同一个 id 的第二个 `session/new` 被拒绝（`InvalidParams`），第一条消息发出之后，pager 自己的检查能经 `session/list` 找到这个 id。用例数仍是 14。

**变异检查。** 每次只改生产代码里的一处。只在 WSL 里的克隆里做，跑完用 `git checkout` 还原。

*PTY 一层*（`resume_matrix.py --strict`，真 `zypi`）：

| 改动 | 结果 |
|---|---|
| 无（最终版，`b169df6`） | 16 PASS / 0 FAIL；连跑 4 次 |
| 第一版（`014a282`）；`session/new`（无 id）改回立即写 | 14 PASS / 0 FAIL：看不见（见「第一版是空的」） |
| 最终版；`session/new`（无 id）改回立即写 | 15 PASS / 1 FAIL：`/new`，不发消息 |
| 最终版；带 id 的 `session/new` 改回立即写 | 13 PASS / 3 FAIL：`--session-id`，不发消息；另外两个是 `--continue` 与不带参数的 `--resume`（空会话成了最新的一个，它们取到了它） |

*Python 一层*（15 个改动，每个对着守着它的四个测试文件；`fa62c5a`）：15 个全部被抓住。第一次跑时 13 个被抓住，2 个没有（H5、A7）：H5 的测试比较文件头的时间与文件名的时间，但两次读时钟常落在同一毫秒，改成 `None` 也碰巧通过；A7 没有测试在选定 id 的会话里发第二条消息。补了两处测试：H5 的测试改用每次读都前进的时钟，A7 新增一个发三条消息的测试。（`fa62c5a` 就是这两处。）

| # | 生产代码里的改动 | 失败数 | 抓住它的测试（其一） |
|---|---|---|---|
| H1 | 先记录条目，再写文件 | 5 | `test_a_failed_write_records_nothing_and_the_next_message_writes_it_all` |
| H2 | 不写攒下的条目 | 3 | `test_what_comes_before_the_first_message_is_held_and_written_ahead_of_it` |
| H3 | 第一个条目（不论类型）就写文件 | 4 | `test_a_session_that_only_held_entries_leaves_nothing` |
| H4 | 追加不加锁 | 1 | `test_two_first_messages_at_once_write_the_file_once_and_keep_both` |
| H5 | 文件头的时间不取文件名的时间 | 1 | `test_the_header_and_the_file_name_carry_the_time_the_session_was_made` |
| H6 | 写之前就把文件标成「已写」 | 1 | `test_a_failed_write_records_nothing_and_the_next_message_writes_it_all` |
| A1 | agent 不记着选定的 id | 4 | `test_an_id_stays_taken_while_its_session_has_no_file_and_is_free_when_it_closes` |
| A2 | 会话打不开时不释放 id | 1 | `test_a_session_that_could_not_be_opened_does_not_keep_its_id` |
| A3 | `session/close` 不释放 id | 1 | `test_an_id_stays_taken_while_its_session_has_no_file_and_is_free_when_it_closes` |
| A4 | `pi/session/delete` 不释放 id | 1 | `test_deleting_a_session_that_has_no_file_frees_its_id` |
| A5 | 第一条消息之前不再查一次 id | 1 | `test_an_id_that_another_process_took_meanwhile_is_refused_when_the_session_is_saved` |
| A6 | 记着的 id 不算「已占用」 | 3 | `test_several_requests_for_the_same_id_at_once_make_exactly_one_session` |
| A7 | 第二次查 id 也拒绝已写文件的会话 | 1 | `test_a_session_with_a_chosen_id_takes_more_than_one_message` |
| A8 | 无 id 的 `session/new` 立即写文件 | 2 | `test_a_session_in_which_nobody_typed_is_not_kept` |
| A9 | 带 id 的 `session/new` 立即写文件 | 3 | `test_an_id_stays_taken_while_its_session_has_no_file_and_is_free_when_it_closes` |

**验证。**

- Python 全量 `pytest`（`real_llm` 不选；`fa62c5a`）：Windows 2,559 通过 / 39 跳过 / 31 不选（5 m 00 s）；Linux（WSL2，我自己的 venv，`rg` 在 `PATH` 上，所以搜索用例也跑）2,568 通过 / 30 跳过 / 31 不选（1 m 44 s）。r13 是 2,531 与 2,540；多出来的 28 个就是新测试：10 + 6 + 12。
- Rust（WSL2，`b169df6`）：`rustfmt --check` 对改过的文件干净；`python_agent_e2e` 14/14（19.0 s）。`fa62c5a` 只改两个 Python 测试文件，Rust 部分与 `b169df6` 相同。生产的 Rust 代码这一轮没有改，所以没有跑整套清单；PR 的 `TUI CI` 会跑。
- PTY 脚本（真 `zypi`，mock 模型，WSL2，`b169df6`）：`resume_matrix.py --strict` 16 PASS / 0 GAP / 0 FAIL；`smoke.py` 23/23；`exit_matrix.py` 12/12；`print_exit.py`；`mode_probe.py`；`status_line_probe.py` 4/4；`net_audit.py --mock` 13/13。退出码都是 0。
- `ruff check .` 与 `ruff format --check .`（ruff 0.16.10）通过。
- 文档：`CHANGELOG.md`（`[Unreleased]`）、内嵌用户指南 `17-sessions.md`、`packages/pi-agent-cli/AGENTS.md`、`docs/DESIGN.md` §6.1、`scripts/tui_pty/README.md`、`docs/baselines/tui.md`、计划（r14）和本文件（本节、B.9、修订记录）。
- 本地运行之后的残留（计划 D.9：Rust 测试不隔离 `HOME`）：WSL 的 `~/.pi-python` 没有新文件。`/tmp` 里有我自己留下的：格式化输出文件、这一轮 pytest 的 `pi-output-*.log` 与 `pi-workflow-*`，还有 r13 的 Rust 单元测试留下的 20 个 `grok-unified-log-test-*`（r14 没有跑 Rust 单元测试）；共 29 项，已删。别的条目（eval 日志、`pull-*.err`、`pi-docker-bash-*.log`、`.tmp*`、`pytest-of-*`）不是我的运行产生的，没有动。

**没有验证 / 留下的。**

- 两个 agent 进程之间的空隙（上面「空隙」）：缩小了，没有堵死。
- 别的 ACP 客户端：在发第一条消息之前就对一个会话调 `session/load` 或 `session/resume` 的客户端，现在得到 `resource_not_found`（以前成功）。`zypi` 不这样做；别的客户端没有试。
- 旧版本 agent 写的空会话文件还在列表里（标题 `(no messages)`）；要删它们，用 `pi/session/delete`。
- 只在 Linux（WSL2）上跑了 Rust 与 PTY；没有 Windows 与 macOS 的 `zypi`，也没有用真实 LLM（e2e 与 PTY 用的是 mock 模型）。
- 决策 10（删 `pi-shell` 里的旧存储层）：用户答复了「做」，这一轮还没有动手（r15 做了，§10.18）。

### 10.18 r15 追加：删 `pi-shell` 的旧存储层（决策 10）

用户在 2026-10-18 答复决策 10：做。他的答复只说了存储层，所以这一轮只做存储层这一项：PR [#26](https://github.com/zy1233/pi-python/pull/26)（分支 `refactor/delete-pi-shell-session-store`，基是 #25 的分支 `fix/write-session-at-first-message`，没有合入）。生产代码没有行为变化。分支上有三个提交：`5a39e09`（删除）、`332ba75`（一个测试的串行键，见本节末的「一次失败的测试，和它的原因」）和文档。

**决策 10 有三项。**

| 项 | 内容 | 状态 |
|---|---|---|
| 存储层 | `pi-shell` 的 `session::storage` 与 `session::persistence` | 本节做了 |
| MCP 配置 | `load_mcp_servers`（`pi-shell/src/util/config/mcp.rs`）；pager 只在一个测试里调用它 | 没做：没有问过 |
| 最近目录 | `pi-pager/src/recent_dirs.rs` | 没做：没有问过 |

**起点。** ADR1 的 PR 3（[#24](https://github.com/zy1233/pi-python/pull/24)）之后，pager 不读会话文件，`gates.session_disk_reads` 守着。pager 一侧对 `session::storage` 的引用是 0。对 `session::persistence` 只剩 3 处：`session_notification.rs` 2 处，`session_title.rs` 1 处。三处用的都是清洗标题的纯函数，不读盘。

**做法。**

1. **搬走 pager 要用的东西。** 新模块 `session::title`（`pi-shell/src/session/title.rs`，140 行）放四项：常量 `MAX_TITLE_SCALARS`（100）；函数 `is_forbidden_title_char` 与 `sanitize_and_cap_title`；私有的 `sanitize_title`（旧名 `sanitize_rename_title`，没有别的调用者）。函数体照旧文件搬来，只改了注释（旧注释讲的是被删的代码）。pager 的两个文件改了引用。
2. **删存储层。** `session::storage`：14 个文件，10,007 行（5 个是测试文件，`mod.rs` 里还有内联的测试）。`session::persistence`：12 个文件，2,009 行（11 个是测试文件）。r14 估的「约 8,200 行」是调用图数的非测试行（7,372 + 803）；算上测试文件是 12,016 行。
3. **删只有它们在用的代码。** 删了这两个模块之后，编译器对 `pi-shell` 报了 41 处 `dead_code`（门禁的上限是 0）。把它们和只测它们的测试删掉，再重新编译，直到消费者构建与 `--workspace --tests` 的告警都是 0。结果见下表。
4. **留下一个类型。** `GoalClassifierVerdict`（`Achieved` 与 `NotAchieved`，序列化成 `snake_case`）原来在 `goal_tracker.rs` 里；`extensions/notification.rs` 与 `session/events.rs` 还要用它。它搬到 `extensions/notification.rs`，加了一个测试（`classifier_verdict_serializes_as_snake_case`）。
5. **门禁与基线。** `scripts/tui_baseline.toml`：`[gates.session_disk_reads]` 的说明改了（存储层已删，8 个模式留着当护栏，防止它被写回来；`session::title` 不在模式里，它不读盘）；`pi-shell` 的 `reference_passed` 改成 864，`min_passed` 改成 820。`scripts/tests/test_tui_baseline.py` 加一条断言：`session::title` 不在这个门禁的模式里。`docs/baselines/tui-support.md` 重新生成。
6. **没有动的。** `[storage] cleanup_ttl_days`（`agent/config.rs` 的 `StorageConfig`）：它清理旧会话文件，现在没有代码读它。配置里仍然接受这个键，注释里写明它没人读；删它是另一个改动。`Cargo.lock` 没有变，也没有删依赖（见「留下的」）。
7. **一个测试的串行键。** `util/config/consent_tests.rs`：`set_consent_answer_is_monotonic_per_account` 的 `#[serial_test::serial(GROK_HOME)]` 改成 `#[serial_test::serial]`，加了 4 行注释。这是单独的提交 `332ba75`（+5 / −1，只有这一个测试文件）。原因见「一次失败的测试，和它的原因」。

*第 3 步删掉的东西。*

| 删掉（或删去一部分）的文件 | 它是什么 | 删的行数 |
|---|---|---|
| `session/goal_tracker.rs`、`goal_tracker_tests.rs` | 目标模式的状态（`GoalTracker`） | 978（478 + 500） |
| `session/signals.rs` | 会话信号（给反馈判断用） | 237 |
| `session/workflow/{registry,store,tracker}.rs` | workflow 的状态类型 | 200（15 + 98 + 84，另有 `mod.rs` 的 3 行） |
| `session/wire_tags.rs` | 回放时与 `updates.jsonl` 里的行比对的标签 | 66 |
| `session/announcement_state.rs` | 恢复会话用的公告状态 | 76 |
| `session/plan_mode.rs` | plan 模式快照 | 61 |
| `session/image_normalize.rs`（只留 `ImageCompressionInfo`）、`image_normalize_tests.rs` | 加载时的图片检查 | 145（68 + 77） |
| `session/helpers/session_summary.rs` | 会话标题的自动生成 | 69（68，另有 `mod.rs` 的 1 行） |
| `sampling/conversation.rs`（只留一行 `pub use`）、`conversation_tests.rs` | `fork_filter_chat`（fork 时过滤对话历史） | 279（63 + 216） |
| 其他 | `tools/todo.rs` 1 行；`test_support` 里的 `unix_mode` 7 行；`pi-subagent-resolution/src/context.rs` 里的一条 NOTE 6 行；`session/mod.rs` 的模块声明 11 行；`events.rs`、`config.rs`、`notification.rs` 与 pager 两个文件里被改的 13 行 | 38 |

表里的 2,149 行加上存储层的 12,016 行，就是 14,165 行。

**规模。** `c74cc18` 是 #25 的头，`5a39e09` 是这个 PR 的代码提交。

| 指标 | `c74cc18` | `5a39e09` | 差 |
|---|---|---|---|
| `tui/` 的改动 | — | — | 52 个文件，+185 / −14,165（删 38 个文件，加 1 个，改 13 个） |
| `tui/crates` 下的 `.rs` | 1,760 个文件，1,021,858 行 | 1,723 个文件，1,007,878 行 | −37 个文件，−13,980 行 |
| `pi-shell` 的非测试代码（调用图的数） | 40,985 行，73 个单元 | 31,653 行，67 个单元 | −9,332 行，−6 个单元 |
| `pi-shell` 的测试（Linux） | 1,056 | 864 | −192 |
| 清单里 `reference_passed` 的和 | 8,706 | 8,514 | −192 |

`332ba75`（测试里的串行键）在 `tui/` 里只改一个文件，+5 / −1，上表的 `5a39e09` 一列不含它。含它，`tui/` 对 `c74cc18` 的改动是 53 个文件，+190 / −14,166。测试的个数没有变。

**测试的账。** 被删的文件里有 202 个测试属性；其中 1 个只在 macOS 上编译（`session/storage/jsonl/durable_tests.rs`），所以 Linux 上少 201 个。新加 9 个：`session::title` 的 8 个，`GoalClassifierVerdict` 的 1 个。1,056 − 201 + 9 = 864，与实测相同。8 个新的清洗标题的测试是新写的，没有从旧文件搬：一个干净的标题不复制；两头的空白会去掉；终端转义与别的控制字符会去掉（C1 控制字符也是）；bidi 覆盖与标记会去掉；去掉禁用字符后留在两头的空格也去掉；什么也不剩时得 `None`；长标题按标量个数截断，不在一个字符中间（用 2 字节的字符测）；`is_forbidden_title_char` 的类与 pager 画标题时用的类相同（U+200D 不禁用）。

**变异检查 `session::title`。** 在 WSL 的克隆里一次改一处，跑 `cargo test --locked -p pi-shell --lib session::title`，记下失败的用例，再还原（脚本在每次之后检查文件回到干净的状态）。

不改的基线是 8 个全过。14 个改动，**13 个被抓住，1 个活下来**（`332ba75` 上跑的，每个约 90 秒，因为每次都要重新编译 `pi-shell`）：

| # | 改动 | 失败的用例 |
|---|---|---|
| M1 | 控制字符不再禁用（`c.is_control()` 改成 `false`） | 3：`a_title_with_nothing_left_is_none`、`terminal_escapes_and_other_controls_are_dropped`、`the_forbidden_class_is_the_one_the_pager_uses_to_draw_a_title` |
| M2 | U+200E 不再禁用 | 1：`the_forbidden_class_is_…` |
| M3 | U+200F 不再禁用 | 2：`bidi_overrides_and_marks_are_dropped`、`spaces_that_a_dropped_character_leaves_at_the_ends_are_trimmed_too` |
| M4 | bidi 覆盖的范围少一个（`202A..=202E` 改成 `..=202D`） | 3：`a_title_with_nothing_left_is_none`、`spaces_that_…_trimmed_too`、`bidi_overrides_and_marks_are_dropped` |
| M5 | bidi 隔离的范围少一个（`2066..=2069` 改成 `..=2068`） | 1：`the_forbidden_class_is_…` |
| M6 | 干净的标题被复制 | 1：`a_clean_title_is_kept_and_not_copied` |
| M7 | 脏标题不去两头的空白 | 2：`spaces_that_…_trimmed_too`、`spaces_at_the_ends_are_trimmed` |
| M8 | 干净的标题不去两头的空白 | 1：`a_title_with_nothing_left_is_none` |
| M9 | 空白的标题被留下 | 1：`a_title_with_nothing_left_is_none` |
| M10 | 截断多留一个标量 | 1：`a_long_title_is_cut_at_a_number_of_scalars_and_not_in_a_character` |
| M11 | 按字节截断 | 1：同上 |
| M12 | U+200D（连接符）也被禁用 | 1：`the_forbidden_class_is_…` |
| M13 | 不查转义（`if false`） | 4：`bidi_overrides_and_marks_are_dropped`、`a_title_with_nothing_left_is_none`、`spaces_that_…_trimmed_too`、`terminal_escapes_and_other_controls_are_dropped` |
| M14 | 上限改成 101 | **没有**：活下来了 |

M14 是已知的缺口，没有补：测试用的是常量 `MAX_TITLE_SCALARS` 本身，所以上限改成什么都过；一个钉住 100 的测试只是把常量抄一遍。100 是旧代码的值，原样搬来。脚本把每个改动是「被抓住」还是「编译不过」标错了：它看输出里有没有以 `error:` 开头的行，而 `cargo` 在测试失败时也打一行 `error: test failed`。所以上面的 13 个在脚本的日志里写着「BROKEN (does not compile)」；它们都列了失败的用例，说明编译通过、断言失败，是被抓住了。真编译不过的改动不会有失败的用例。每次之后文件都回到干净的状态（脚本最后核对了 `git status`）。

**验证。**

- `scripts/tui_baseline.py check`（WSL2；`5a39e09` 上 3 m 19 s，`332ba75` 上 2 m 46 s；两次都全过，数相同）：消费者构建 0 条告警，`--workspace --tests` 0 个错误、0 条告警，依赖图 980，读盘 0 处命中，残留词 `grok agent` 0 / `leader` 243（上限 245）/ `x.ai` 297（上限 304）。r15 的删除让 `leader` 少 2 行，`x.ai` 少 7 行；上限没有降（`check` 的输出提示可以降）。
- `scripts/tui_notices.py check`（`332ba75`）：989 个包，989 条，缺 0、旧 0（`Cargo.lock` 没有变）。
- 整套 10 个 suite（`scripts/tui_baseline.py test`，WSL2，清环境，`PI_E2E_PYTHON` 与 `PI_E2E_REQUIRED=1`，增量编译），在最后一个代码提交 `332ba75` 上，16 m 24 s，退出码 0：**合计 8,513 通过 / 1 个已知失败 / 21 忽略**，清单里的和是 8,514（已知失败是 `pi-pager-render` 的 tmux 探测，清单按通过算）。`pi-pager` 5,649（12 忽略），`pi-pager-render` 1,091（另有那 1 个已知失败，2 忽略），`pi-pager-minimal` 79，`pi-pager-bin` 21，`pi-acp-lib` 21，`pi-http` 13，`pi-telemetry` 239，`pi-file-utils` 216（6 忽略），`pi-sampling-types` 320，`pi-shell` 864（`--lib` 859，两个集成测试目标共 5）。在压成一个提交之前的 WIP 提交 `43f291f` 上（19 m 28 s，那时 consent 测试还没改，`pi-shell` 那一轮碰巧没有失败）整套的数完全相同（8,513 / 1 / 21）。
- PTY 脚本（真 `zypi`，mock 模型，WSL2）：`resume_matrix.py --strict` 16 PASS / 0 GAP / 0 FAIL；`smoke.py` 23/23；`exit_matrix.py` 12/12；`print_exit.py`；`mode_probe.py`；`status_line_probe.py` 4/4；`net_audit.py --mock` 13/13。`zypi` 是从 WIP 提交 `2f33815` 构建的。`2f33815`、`5a39e09` 与 `332ba75` 的生产代码相同：差别都在 `#[cfg(test)]` 里（`test_support` 里 7 行辅助函数；consent 测试的一个属性），所以没有重跑 PTY。
- Python 全量 `pytest`（`real_llm` 不选；`332ba75`）：Windows 2,559 通过 / 39 跳过 / 31 不选（5 m 27 s）；Linux（WSL2，我自己的 venv，`rg` 在 `PATH` 上）2,568 通过 / 30 跳过 / 31 不选（1 m 46 s）。两个数都与 r14 相同：这个 PR 没有加 Python 测试。第一遍 Linux 没有把 `rg` 放进 `PATH`，是 2,564 通过 / 34 跳过：多出的 4 个跳过是搜索用例；总数都是 2,598。
- `ruff check .` 与 `ruff format --check .`（ruff 0.16.10）通过。
- CI（PR #26 的 `69932cf`；它与 `332ba75` 只差文档）：`CI`（[run 39165328489](https://github.com/zy1233/pi-python/actions/runs/39165328489)）：Ruff 通过；`pytest` 3.11、3.12、3.13 各 2,559 通过 / 39 跳过 / 31 不选，与 Windows 的数相同。`TUI CI`（[run 39165328437](https://github.com/zy1233/pi-python/actions/runs/39165328437)）：`Gates + cargo check` 3 m 41 s（依赖图 980 / 980，告警 0，`--workspace --tests` 0 个错误，`leader` 243 / 245，`x.ai` 297 / 304，读盘门禁通过）；`cargo test` 16 m 14 s，合计 8,514 通过 / 0 失败 / 21 忽略（`pi-shell` 864，`pi-pager` 5,649，`pi-pager-render` 1,092：那个 tmux 探测在 runner 上通过，所以比本地多 1 个）；`Release build baseline` 按设计跳过。runner 有 4 个 vCPU，测试线程默认 4 个，而竞争在 ≤ 4 个线程时不出现（本地 12 个线程才出现）：所以 CI 通过不能证明竞争已修好；能证明它的是上面本地 12 个线程下的 20 / 20 与 15 / 15。
- 文档：`CHANGELOG.md`（`[Unreleased]`）、`docs/baselines/tui.md`、`docs/baselines/archive/tui-history.md`、`docs/baselines/tui-support.md`（重新生成）、计划（r15）和本文件（本节、B.10、修订记录）。
- 本地运行之后的残留（计划 D.9：Rust 测试不隔离 `HOME`）：查了 WSL 里的两处，都清了。**`~/.pi-python`**：这一天的测试写了 7 项：`agent_id`、`config.toml`、`last-copy.txt`、`logs/unified.jsonl`、`trusted_folders.toml`（1,145 段，路径全在 `/tmp/` 下）、`trusted_folders.toml.lock`、`worktrees.db`。r14 清完之后这里只剩 `workflow-journals`，所以这 7 项都是 r14 之后的运行写的，已删；现在又只剩 `workflow-journals`（9 月 24 日的，不是这次留下的，没动）。**`/tmp`**：`grok-unified-log-test-*` 1,045 个、`.tmp*` 目录 1,658 个（10 个不空，里面是 `worktrees.db`、`config.toml`、图片缓存文件等测试写的文件）、我自己的 `adr1-*` 8 项、`key_owner_line_viewer.txt`（`pi-pager` 的一个测试写的固定文件名）；Python 的两遍 `pytest` 每遍留下 `pytest-of-zy`、一个 `pi-workflow-*` 目录和一个 `pi-output-*.log`。清了之后 `/tmp` 从 3,017 项降到 302 项。没动的：10 月 19 日之前的 101 个 `.tmp*`（最早 9 月 16 日）、更早的 39 个 `pi-workflow-*` 与 57 个 `pi-output-*.log`，以及 `wsl-venv` 等；它们不是这一天的运行留下的。清理按修改时间的窗口和名字删，没有用通配符删整个 `/tmp`。

**一次失败的测试，和它的原因。**

失败的测试是 `pi-shell` 的 `session::worktree::tests::resolve_worktree_by_id_or_path_existing_dir_returns_path`。错误是 `failed to init worktree DB schema`，原因 `disk I/O error`，`Error code 1802: Error invoking fstat to get file metadata`。第一次整套运行（WIP 提交 `43f291f`）通过。之后在同一棵 `tui/` 树上单跑 `pi-shell` 这个 suite，失败了一次（863 通过 / 1 失败）。再往后直接跑测试二进制：`--lib` 整个运行 20 次，20 次都失败（12 个测试线程；线程数 ≤ 4 时不失败）。

*它不是这个 PR 带来的。* 用同样的筛选（`session::worktree`、`remote::`、`util::`、`config::tests`）跑 `c74cc18` 的测试二进制，15 次全失败：`existing_dir_returns_path` 13 次，同一模块的 `nonexistent_returns_none` 2 次。这个 PR 删了约 200 个测试，只改了整套运行里各个测试的先后与重叠。为什么 `c74cc18` 的整套运行没有遇到它（15 次 0 失败），没有查。

*原因：两个测试同时运行。*

- `util/config/consent_tests.rs` 的 `set_consent_answer_is_monotonic_per_account` 把进程的环境变量 `GROK_HOME` 设成自己的临时目录，结束时删掉这个目录。它的串行键是有名字的 `serial(GROK_HOME)`。
- 其余设 `GROK_HOME` 的测试（`config/tests.rs`、`agent/folder_trust.rs`、`util/config/mcp.rs`）用默认键 `#[serial]`。`session::worktree` 的两个测试也用默认键。它们调用 `pi_workspace::worktree::resolve_worktree_by_id_or_path`；这个函数每次调用都读 `GROK_HOME`，在那里打开 `worktrees.db`。
- `serial_test`（3.2.0）的文档说：加了键，就是互不依赖的子集，每个子集内部串行；不同键的测试可以同时运行。`EnvGuard` 自己没有锁（它的注释要求调用者用默认的 `#[serial_test::serial]`）。所以 consent 测试是唯一能与那些测试同时运行的设 `GROK_HOME` 的测试。
- 两个测试重叠时：worktree 测试在 consent 测试的临时目录里建 `worktrees.db`；consent 测试结束，目录被删；worktree 测试接着要为数据库开 `-wal` 文件。SQLite 这时对数据库的路径做 `stat`（为了把权限抄给新文件），得 ENOENT，就返回 1802。1802 的名字（`SQLITE_IOERR_FSTAT`）有误导：失败的是对路径的 `stat`，不是 `fstat`。

*怎么查到的。* 先排除环境：换一个新的 `HOME` 照样失败；只跑 `session::worktree` 这个模块，230 次（30 次 cargo 运行加 200 次直接运行）都通过；筛选里去掉 `remote::`、`util::`、`config::tests` 中任何一组，都不失败（这几组决定哪些测试同时运行）。然后用一个 `LD_PRELOAD` 的探针（一个 C 文件，记下路径含 `worktrees.db` 的 `open`、`close`、`unlink`、`rename`、`stat`、`fstat`，每条带调用栈）跑失败的运行。第一版只看 `fstat`：一个失败也没有。测试二进制里 SQLite 的反汇编显示，能返回 1802 的函数有五个，其中 `getFileMode` 是对路径做 `stat` 的。第二版加上对路径的 `stat`，一次失败的运行是这样（`<tmp>` 是 consent 测试的临时目录）：

| 时间 | 线程 | 事件 |
|---|---|---|
| 0 ms | A：worktree 测试 | 在 `<tmp>/worktrees.db` 建数据库，再建它的日志文件（`GROK_HOME` 这时是 consent 测试的目录） |
| 139 ms | B：consent 测试（调用栈里是它） | 测试结束，`TempDir` 的析构删了 `worktrees.db-journal` 与 `worktrees.db` |
| 297 ms | A | 删日志文件；对 `<tmp>/worktrees.db` 做 `stat64`，两次都得 ENOENT；调用栈 `getFileMode` ← `findCreateFileMode` ← `unixOpen` ← `sqlite3WalOpen`；测试失败 |

另一次失败的运行（探针同上）里，A 从建数据库到失败只隔 24 ms，B 在第 12 ms 删了 `worktrees.db`。窗口的长短每次不同；没有查 A 在那 297 ms 里在做什么。提交 `332ba75` 的信息里写的「130 ms」是当时估的数；跟踪里是 139 ms。提交信息没有改。

*修。* 把 consent 测试的键改成默认的 `#[serial_test::serial]`（`332ba75`，只有测试文件）。和其他设 `GROK_HOME` 的测试一样，它现在与 worktree 的两个测试互斥。生产代码没有改。

*改之前与改之后。* 同一台机器，12 个测试线程，同样的命令。

| 运行 | 改之前（`5a39e09` 的二进制） | 改之后（`332ba75` 的二进制） |
|---|---|---|
| 四组筛选，15 次 | 15 次失败 | 0 次失败 |
| `pi-shell` 的整个 `--lib`，20 次 | 20 次失败（另有 2 次是下面说的心跳用例） | worktree 测试 0 次失败（另有 1 次心跳用例） |
| 基线执行器跑 `pi-shell` 这个 suite | 一次：863 通过 / 1 失败（单跑这个 suite） | 一次：864 通过 / 0 失败（整套运行里的那一次） |

*另一个老的偶发失败，与这个无关。* `auth::manager::lock::tests::dropping_the_guard_silences_the_heartbeat_before_anyone_else_can_hold_the_lock`（心跳）在整个 `--lib` 运行里偶发失败：`c74cc18` 的二进制 15 次里 5 次，上表两列的 20 次里各 2 次与 1 次。r13 也记过它（负载下偶发，没有查根因）。

**没有验证 / 留下的。**

- 决策 10 的另两项（`mcp.rs`、`recent_dirs.rs`）：没有问过，没有做。
- 同一类竞争的其他可能：`pi-pager` 里还有两处 `serial(GROK_HOME)`（`app/dispatch/tests/session/lifecycle.rs` 一个，`tests/grok_home_paths.rs` 两个）；`pi-shell` 里剩下的命名键有 `force_dark_wake_env`、`attribution_emit_count`、`heap_profile_hooks`。没有看这些测试读不读 `GROK_HOME`，也没有找读 `GROK_HOME` 又没有任何 `serial` 的测试：它们仍会遇到同样的竞争。
- 依赖：删完之后，`pi-shell` 的 `Cargo.toml` 里有 7 个依赖不再被它的源码点名：`urlencoding`、`pi-compaction-transcript`、`pi-workflow`、`tdigests`、`nix`，和 dev 依赖 `pretty_assertions`、`pi-test-utils`（`c74cc18` 上是 0 个；方法见 B.10：对源码按整词搜名字，是文本搜索，宏与改名会骗过它）。没有核实，也没有删。要删的话，另做一个改动，并跑 `scripts/tui_notices.py check`，因为 `Cargo.lock` 与 `THIRD-PARTY-NOTICES` 可能变。
- 调用图现在报死的两个单元：`session::helpers`（21 行）与 `session::worktree`（359 行）。rustc 不告警（里面是 `pub(crate)` 的项与重新导出）。没有删，并进计划 D.8。`session::events` 因为那个 `From` 的实现，由 589 行变成 591 行。
- `leader` 与 `x.ai` 的上限（245 与 304）现在高于实测（243 与 297）；没有降。要降的话，一个改清单的小改动就够。
- 只在 Linux（WSL2）上跑了 Rust 与 PTY；没有 Windows 与 macOS 的 `zypi`。被删的文件里有 1 个只在 macOS 上编译的测试，没有在 macOS 上看过。
- 这个 PR 叠在 #25 上。#25 合入之后，把 #26 的基改成 `main`，再把 `main` 合进它的分支（`git merge -s ours`，两边的树相同时；做法同 §10.17 的 #24）。没有合入 #26。

### 10.19 r16 追加：合入 #25 与 #26，删 `pi-shell` 里剩下的死代码

用户在 2026-10-19 从「接下来做哪些」里选了四项：`merge_25_26`（合入 #25 与 #26）、`dead_units`（删调用图报死的 `session::helpers` 与 `session::worktree`）、`small_cleanup`（不用的依赖、两个上限、`[storage] cleanup_ttl_days`）、`race_audit`（检查同类竞争）。他没选 `d10_rest`，所以决策 10 的另两项（`load_mcp_servers` 与 `mcp.rs`、`recent_dirs.rs`）还没做。这一节记前三项。`race_audit` 是另一个只改测试的 PR（r17）。PR [#27](https://github.com/zy1233/pi-python/pull/27)（分支 `refactor/tui-session-store-leftovers`，基是 `7d82c27`）**没有合入**：用户只让合入 #25 与 #26。生产代码的行为没有变化。

**合入。**

| PR | 做法 | `main` 上的提交 | `main` 上的 CI |
|---|---|---|---|
| [#25](https://github.com/zy1233/pi-python/pull/25) | squash 合入（2026-10-19 01:42 UTC） | `2b0baf3` | `CI` [run 39186566545](https://github.com/zy1233/pi-python/actions/runs/39186566545)、`TUI CI` [run 39186566553](https://github.com/zy1233/pi-python/actions/runs/39186566553)：都通过 |
| [#26](https://github.com/zy1233/pi-python/pull/26) | #25 合入后，GitHub 把 #26 的基改成 `main`。`2b0baf3` 的树与 #25 的分支头 `c74cc18` 的树相同，所以把 `main` 合进 #26 的分支用 `git merge -s ours`（`da34128`，树不变）。`CI` 与 `TUI CI` 在 `da34128` 上通过之后，squash 合入（01:59 UTC） | `7d82c27`（树与 `da34128` 相同） | `CI` [run 39187845874](https://github.com/zy1233/pi-python/actions/runs/39187845874)、`TUI CI` [run 39187845858](https://github.com/zy1233/pi-python/actions/runs/39187845858)：都通过；`TUI CI` 合计 8,514 通过 / 0 失败 / 21 忽略，依赖图 980，`leader` 243 / `x.ai` 297 |

两个远端分支在合入时自动删除。§10.18 末尾写的「没有合入 #26」是当时的状态。

**PR #27 的六个提交。** 都叠在 `7d82c27` 上。`tui/` 下共 28 个文件（删 20 个，改 8 个），+13 / −1,644 行。

| 提交 | 内容 | 改动 |
|---|---|---|
| `d687dd2` | 删 15 个没人加载的文件 | 15 个文件，−1,033 |
| `a97abd3` | 删死单元 `session::worktree` 与 `session::helpers` | 6 个文件，+2 / −427 |
| `6118888` | 删 `[storage]` 段（`StorageConfig`），加 1 个测试 | 2 个文件，+11 / −18 |
| `7ae0cc5` | 删 `util::config::worktree` | 2 个文件，−64 |
| `8b146e7` | 删 10 个 `pi-shell` 的源码没点名的依赖 | 3 个文件，−102 |
| `1a8abee` | 把清单里的三个上限降到实测的数（`scripts/`，不在 `tui/` 下） | 1 个文件 |

1. **没人加载的文件（`d687dd2`）。** `session/templates/` 的 14 个 `goal_*.md`（449 行）与 `session/workflows/deep_research.rhai`（584 行）。对每个模板，在 `7d82c27` 的整棵树里按去掉扩展名的文件名搜（`git grep -F`），模板目录之外 0 处命中；`tui/` 里没有一处提到 `deep_research`（Python 包 `pi-dynamic-workflows` 里同名的 `deep_research` 是另一个东西：它用 Python 生成脚本，不读这个文件）。最后一次改变这些名字出现次数的提交是 `455527c`（#7，2026-10-06，删 Rust agent runtime）。rustc 看不到没人 `include_str!` 的文件，所以没有告警；这个办法只能靠文本搜索。
2. **两个死单元（`a97abd3`）。** `session::helpers`（3 个文件，21 行非测试代码）与 `session::worktree`（359 行；24 个测试，只在 Unix 上编译）。调用图（`tui_callgraph.py units --crate pi-shell --live pi-pager-bin pi-pager-minimal pi-update`）报它们死；rustc 不告警，因为里面是 `pub(crate)` 的项与重新导出。`session::worktree` 里的两个 `resolve_worktree_by_id_or_path_*` 测试，就是 §10.18 里在别的测试的临时目录里打开 `worktrees.db` 的那两个；它们没有了，**但那类竞争的原因还在**（有名字的 `serial` 键与默认键互不互斥），见「留下的」。`util/config/consent_tests.rs` 里一条提到 `session::worktree` 的注释改了。那 24 个测试经一条重新导出（`pub(crate) use pi_workspace::worktree::*`）测的是 `pi_workspace::worktree` 里的东西：请求与响应类型的序列化、`build_candidate_list`、`BackgroundCopyGuard`、`resolve_worktree_by_id_or_path`。那些东西留在 `pi-workspace`，没有动；`pi-workspace` 自己有没有测同样用例的测试，没有查（它的 `worktree/mod.rs` 里有内联测试）。按 `pi_workspace::worktree` 搜，`tui/` 里现在没有文件提到它。
3. **`[storage]`（`6118888`）。** 删了 `StorageConfig`（结构、`Config.storage` 字段、默认值）与「有效配置」测试里的 `[storage]` 两行。`config.toml` 里还有 `[storage]` 时，配置照常加载，这一段被报成「不认识的键」（和任何别的不认识的段一样：一条警告，不是错误）。新测试 `a_removed_storage_section_is_reported_as_unrecognized` 钉住这一点。没有动的：`Config.storage_mode` 与隐藏的 `--storage-mode` 旗标（另一个设置，见「留下的」）。
4. **又一个没用户的模块（`7ae0cc5`）。** 第一遍 `check`（在 `a97abd3` 上）有 1 条告警：`util/config/mod.rs` 的 `pub(crate) use worktree::*` 是没用的重新导出。`util::config::worktree`（`WorktreeType` 枚举、`FromStr`、到 `CreationMode` 的 `From`，和 1 个测试）的唯一用户是刚删的 `session::worktree`。模块与 `mod.rs` 里的两行声明一起删。
5. **不用的依赖（`8b146e7`）。** 对 `pi-shell/Cargo.toml` 里的每个依赖，在 `src/`、`tests/`、`build.rs` 里按整词找它的名字，**不算 `//` 注释里的**（r15 的那一遍算了注释，所以漏了 `base64`：它只在一条注释里出现）。结果是 10 个，每个都由编译器核对（删掉之后 `check` 与 `--workspace --tests` 通过）：`[dependencies]` 里 7 个（`urlencoding`、`base64`、`pi-fast-worktree`、`pi-compaction-transcript`、`pi-workflow`、`tdigests`、`nix`），`[dev-dependencies]` 里 3 个（`pretty_assertions`、`pi-test-utils`、`tokio-util`）。在 `7d82c27`（#26 合入时）上，同一个扫描找到 8 个：比 §10.18 列的 7 个多 `base64`，因为 §10.18 的扫描把注释里的名字也算上。另外 2 个，`pi-fast-worktree` 与 `tokio-util`，在 `7d82c27` 上还有用户：`session::worktree`（两个都用，`tokio-util` 在它的测试里）与 `util::config::worktree`（`pi-fast-worktree`）。这两个模块删掉之后，它们也没有用户了。`Cargo.lock` 只少 `tdigests` 一个包（和 10 行依赖）；`THIRD-PARTY-NOTICES` 少 5 条。依赖图 980 → 970：`pi-workflow`（这个工作区里的 crate）和 9 个注册表的包（`tdigests`、`rhai`、`rhai_codegen`、`smartstring`、`thin-vec`、`const-random`、`const-random-macro`、`crunchy`、`tiny-keccak`）。
6. **清单（`1a8abee`）。** `[gates.residue]`：`leader` 245 → 243，`x.ai` 304 → 297；`[graph.max_nodes]`：980 → 970；`pi-shell` 的 `reference_passed`：864 → 838（`min_passed` 820 不变）；清单里 `reference_passed` 的和 8,514 → 8,488。这些数都是同一台机器上的实测值，没有留余量（§10.18 记过：`check` 的输出提示可以降）。

**规模。**

| 指标 | `7d82c27` | `1a8abee` | 差 |
|---|---|---|---|
| `tui/crates` 下的 `.rs` | 1,723 个文件，1,007,882 行 | 1,718 个文件，1,007,386 行 | −5 个，−496 行 |
| `pi-shell` 的非测试代码（调用图） | 31,653 行 | 31,191 行 | −462 行 |
| `pi-shell` 里调用图报死的单元 | 9 个，1,388 行 | 7 个，1,008 行 | −2 个，−380 行 |
| `pi-shell` 的测试 | 864 | 838 | −26 |
| 清单里 `reference_passed` 的和 | 8,514 | 8,488 | −26 |
| 依赖图唯一包数（`x86_64-unknown-linux-gnu`） | 980 | 970 | −10 |
| `Cargo.lock` 的包数 | 1,257 | 1,256 | −1 |
| `THIRD-PARTY-NOTICES` 的条数 | 989 | 984 | −5 |
| `leader` / `x.ai` 的上限（实测值没有变） | 245 / 304 | 243 / 297 | −2 / −7 |

**测试的账。** 864 − 24（`session::worktree`，只在 Unix 上）− 2（`session::helpers`）− 1（`util::config::worktree`）+ 1（新测试）= 838，与实测相同（`--lib` 833，两个集成测试目标共 5）。

**验证。**

- `scripts/tui_baseline.py check`（WSL2）：第一遍（`a97abd3`，5 m 03 s）退出码 1：1 条告警（上面第 4 项），其余全过（依赖图 980，`leader` 243 / `x.ai` 297）。第二遍（`8b146e7` 的源码，3 m 27 s）全过：消费者构建 0 条告警，`--workspace --tests` 0 个错误，依赖图 970，读盘 0 处命中，残留词 `leader` 243 / `x.ai` 297。
- `scripts/tui_notices.py check`：删依赖之后，984 个会发布的包、989 条，旧 5 条（`rhai`、`rhai_codegen`、`smartstring`、`tdigests`、`thin-vec`）；`update --write` 之后 984 / 984。
- 整套 10 个 suite（`scripts/tui_baseline.py test`，WSL2，清环境，`PI_E2E_PYTHON` 与 `PI_E2E_REQUIRED=1`，增量编译），在 `8b146e7` 上，23 m 56 s，退出码 0：**合计 8,487 通过 / 1 个已知失败 / 21 忽略**，清单里的和是 8,488（已知失败是 `pi-pager-render` 的 tmux 探测，清单按通过算）。`pi-shell` 838（1 忽略），`pi-pager` 5,649（12 忽略），`pi-pager-render` 1,091（另有那 1 个已知失败，2 忽略），`pi-pager-minimal` 79，`pi-pager-bin` 21，`pi-acp-lib` 21，`pi-http` 13，`pi-telemetry` 239，`pi-file-utils` 216（6 忽略），`pi-sampling-types` 320。这一遍里 `pi-shell` 的 `--lib` 没有失败的用例，包括 §10.18 里偶发的心跳用例；只有一遍，不说明那个用例好了。
- PTY 脚本（真 `zypi`，mock 模型，WSL2）：`resume_matrix.py --strict` 16 PASS / 0 GAP / 0 FAIL；`smoke.py` 23/23；`exit_matrix.py` 12/12；`print_exit.py`；`mode_probe.py`；`status_line_probe.py` 4/4；`net_audit.py --mock` 13/13。`zypi` 是用与 `8b146e7` 相同的源码构建的（3 m 25 s）。r15 没有重跑 PTY，因为生产代码没变；这一次删了生产代码里的模块与一个配置字段，所以重跑了。
- 变异检查（新测试 `a_removed_storage_section_is_reported_as_unrecognized`）：在 WSL 的克隆里，把 `6118888` 对 `agent/config.rs` 的改动倒过来应用（`StorageConfig` 与 `Config.storage` 字段回来），再跑这个测试：**失败**（`got: []`：`[storage]` 又是认得的段，不再被报告）；不改时通过。每次之后文件还原，克隆是干净的（`git status` 为空）。只做了这一个改动。
- Python 全量 `pytest`（`real_llm` 不选；`1a8abee`）：Windows 2,559 通过 / 39 跳过 / 31 不选（5 m 05 s）；Linux（WSL2，我自己的 venv，`rg` 在 `PATH` 上）2,568 通过 / 30 跳过 / 31 不选（1 m 51 s）。两个数都与 r15 相同：这个 PR 没有加 Python 测试（清单的 TOML 改了，`scripts/tests/test_tui_baseline.py` 的 32 个用例在其中）。第一遍 Linux 在收集阶段失败（14 个错误，`No module named 'pi_web_access'`、`pi_goal_x`、`pi_dynamic_workflows`）：我自己的 venv 当天早些时候重建过，只装了 `pi_agent_*` 三个包；把那三个扩展包按可编辑方式装进去（只动我的 venv）之后重跑，通过。这不是代码的问题。
- `ruff check .` 与 `ruff format --check .`（ruff 0.16.10）通过。
- 本地运行之后的残留（计划 D.9：Rust 测试不隔离 `HOME`）：查了 WSL 里的两处和 Windows 的 `%TEMP%`，都清了。**WSL 的 `~/.pi-python`**：整套 Rust 测试写了 6 项（`agent_id`、`config.toml`、`last-copy.txt`、`logs/`、`trusted_folders.toml` 与它的 `.lock`），已删；现在又只剩 `workflow-journals`（9 月 24 日的，没动）。r15 的残留里有 `worktrees.db`，这一次没有：写它的 `session::worktree` 测试已经删了。**WSL 的 `/tmp`**：这一天 09:40（CST）之后的运行（整套 Rust 测试、两遍 Python `pytest`、变异检查）留下 24 项：`grok-unified-log-test-*` 9 个、`.tmp*` 目录 11 个、`pi-workflow-*` 1 个、`pi-output-*.log` 1 个、`pytest-of-zy`、`key_owner_line_viewer.txt`；每一项的时间都落在我的某次运行的时间窗里（逐项对过）；删了之后 `/tmp` 由 326 项降到 302 项。没动的：更早的 101 个 `.tmp*`、39 个 `pi-workflow-*`、57 个 `pi-output-*.log` 与 `wsl-venv`。**Windows 的 `%TEMP%`**：`pytest-of-c1055`、一个 `pi-workflow-*` 目录、一个 `pi-output-*.log`，已删（`pytest-of-c1055` 里有符号链接，PowerShell 5.1 的 `Remove-Item` 删不掉，用了 `cmd /c rd /s /q`）。
- 文档：`CHANGELOG.md`（`[Unreleased]`）、`docs/baselines/tui.md`、`docs/baselines/archive/tui-history.md`、`docs/baselines/tui-support.md`（重新生成）、计划（r16）和本文件（本节、B.11、修订记录）。

**提交历史改写过一次。** 本地验证之后、推送之前，第一个提交的信息要改：写的是 `deep_research.rhai` 有 564 行，`git diff --numstat` 说 584 行（我把前一轮总结里的数抄错了）。用 `git cherry-pick` 重建了 6 个提交，每个提交的树与重建之前相同（`git diff` 旧分支与新分支，输出为空），所以上面的验证（在旧的提交 id 上跑的）对新的提交仍然成立。文档里写的是新的 id。

**没有验证 / 留下的。**

- `race_audit`：没有在这个 PR 里做。§10.18 里的问题还开着：`pi-pager` 里的 `serial(GROK_HOME)`（两个文件里共 3 处），`pi-shell` 里的命名键（`force_dark_wake_env`、`attribution_emit_count`、`heap_profile_hooks`），和读 `GROK_HOME` 又没有任何 `serial` 的测试。删掉 `session::worktree` 只是去掉了那两个受害的测试，没有去掉竞争的原因。
- 调用图仍报死的 7 个单元（1,008 行）：`auth::flow`（45）、`config::reloader`（77）、`session::events`（591）、`session::telemetry`（174）、`session::unified_list`（103）、`upload`（1）、`upload::turn`（17）。没有删，并进计划 D.8。`session::events` 要先查：它有 `GoalClassifierVerdictTelemetry` 的 `impl`，rustc 可能需要它。
- 没有用户的 crate `pi-workflow`：9 个文件，3,570 行（含 `Cargo.toml` 与 1 个 example）。`tui/Cargo.toml` 的成员列表里有它；没有任何 `Cargo.toml` 依赖它（文本搜索）。没有删，并进 D.8。
- `Config.storage_mode`：`--storage-mode`（隐藏的旗标，`pi-pager/src/app/cli.rs`）经 `pi-pager/src/acp/mod.rs` 进入 `ConfigContext`，由 `StorageMode::resolve` 写进 `Config.storage_mode`（`pi-shell/src/agent/config.rs`）。在 `.rs` 文件里按 `storage_mode` 搜，没有找到读这个字段的地方（只有写与默认值）。是文本搜索的结果，没有删，并进 D.8。
- 没人加载的文件的另一遍扫描（只报告，没有删；候选共约 88 个文件）：

| 组 | 文件 | 说明 |
|---|---|---|
| `pi-pager/tests/scenarios/` | 43 个，70,628 B | 没有代码读它们；附录 E 已记过它们没有 runner |
| `pi-pager/npm/` | 24 个，73,368 B | 上游的 npm 打包；已是 D.7 的项 |
| `pi-pager/scripts/install-enterprise.ps1` | 1 个，12,460 B | 上游的企业安装脚本，没有代码或测试提到它 |
| `pi-pager/assets/logo/logoNN.txt` | 9 个（NN = 04、06、08、09、10、12、16、20、24），10,147 B | 代码只 `include_str!` 了 `logo05.txt` 与 `logo07.txt`（`views/welcome/logo.rs`） |
| `xai_grok_pager__*.snap` | 11 个，2,737 B | crate 改名之前的快照；同名的 `pi_pager__*.snap`（也是 11 个）是现在用的 |

  扫描里还有几个误报，已排除：`pi-markdown` 的 fuzz 种子（按目录读）、`pi-mermaid` 的 `Roboto-LICENSE.txt`（内嵌字体的许可证）、`tui/rustfmt.toml`（rustfmt 读）。这一遍是文本扫描：按文件名在源码里搜，宏拼出来的路径与按目录读的文件会骗过它。没有验证 `xai_grok_pager__*.snap` 是否真没人用（没有删它们再跑测试）。
- 删掉的 24 个 `session::worktree` 测试（见上面第 2 项）：它们测的东西在 `pi-workspace` 里还在；删掉之后，这些用例在 `pi-workspace` 一侧有没有覆盖，没有对过。
- 只在 Linux（WSL2）上跑了 Rust 与 PTY；没有 Windows 与 macOS 的 `zypi`。
- 这个 PR 的 CI（推送 `6bd7db5` 之后，两个运行都通过，数与预期相同）：`CI` [run 39193752450](https://github.com/zy1233/pi-python/actions/runs/39193752450)：Ruff 9 s，Python 3.11 / 3.12 / 3.13 的 `pytest` 2 m 24 s / 2 m 25 s / 2 m 20 s。`TUI CI` [run 39193752451](https://github.com/zy1233/pi-python/actions/runs/39193752451)（03:14:37Z 到 03:32:15Z）：`Gates + cargo check` 4 m 24 s，依赖图 970（上限 970），`leader` 243 / `x.ai` 297，0 条告警，`--workspace --tests` 0 个错误，读盘门禁 0 处命中；`cargo test` 17 m 34 s，**8,488 通过 / 0 失败 / 21 忽略**（`pi-shell` 838，`pi-pager` 5,649，`pi-pager-render` 1,092，`pi-pager-minimal` 79，`pi-pager-bin` 21，`pi-acp-lib` 21，`pi-http` 13，`pi-telemetry` 239，`pi-file-utils` 216，`pi-sampling-types` 320）。`pi-pager-render` 在 CI 是 1,092；本地是 1,091 加 1 个已知失败（tmux 探测，在 CI 的 runner 上通过）。各 suite 的测试自己用的时间（`test result` 行里的 `finished in`）加起来约 54 s（`pi-shell` 7.28 s，`pi-pager` 的库测试 9.11 s）；job 的其余时间是编译与准备。

### 10.20 r17 追加：检查测试之间的竞争，只改测试与工具

用户在 2026-10-19 选的四项里，最后一项是 `race_audit`：检查同类竞争（起因是 §10.18 里的 `GROK_HOME` 串行键）。这一节记它。结果是一个只改测试、脚本与文档的 PR：[#28](https://github.com/zy1233/pi-python/pull/28)（分支 `test/tui-env-race-audit`，叠在 #27 的分支 `refactor/tui-session-store-leftovers` 上，**没有合入**；#27 合入之后，把它的基改成 `main`）。生产代码没有改。

**结果。**

- 随机顺序、32 个测试线程，`pi-pager` 的整个 lib 测试：修之前 6/32 遍有测试失败（种子 1–32）；修之后 0/60（种子 1–60）与 0/32（新种子 201–232）。`pi-shell`：2/20 → 0/20。
- 偶发失败有两类原因。第一类是测试自己的缺陷（4 项）：两把互不排斥的锁；一个测试往别的测试的输出里写；`fork` 出的子进程还拿着锁文件；5 个测试改了环境变量却不还原。这些在测试里修了。
- 第二类是**进程级的 `static`**（6 族）：颜色等级与终端原生调色板、最小模式的标志、双向文字的闩、内嵌弹窗的标志、语音的门、主题。读它们的测试有几百个，都不拿锁，所以加锁修不了。改它们的 57 个测试，现在各在自己的进程里跑（`test_util::in_own_process`，每个多约 30 ms）。
- 工具在 `scripts/tui_envspy/`：环境变量探针，和压力脚本（随机顺序，多个线程；可以跑配对与人口实验）。

**为什么会有这类竞争。** 一个 Rust 测试二进制里的所有测试，是同一个进程里的线程。它们共用环境变量、当前目录和每一个 `static`。这个树里的测试来自一个用 `cargo nextest`（一个测试一个进程）跑测试的项目，所以很多锁只管一部分测试。代码里有注释这样说：`pi-shell/src/config/tests.rs:2513-2516`（"reliable only under nextest's process-per-test isolation"）、`pi-workspace/src/lib.rs:77-80` 与 `folder_trust.rs:919-920`、`pi-telemetry/src/debug_log.rs:528`、`pi-pager-render/src/terminal/xtversion.rs:210`、`pi-update/tests/test_install_sh.rs:14`。只有 `pi-fast-worktree/src/db/mod.rs:466-468` 反着说（"must also pass under `cargo test`"）。这个仓库的 CI 跑 `cargo test`：顺序固定（按名字），4 个 vCPU 就是 4 个测试线程。这样的运行找不到这类竞争，所以 **CI 通过不能证明它们不存在**。r15 就是一例：`session::worktree` 的竞争在 `c74cc18` 里已经有，要等删代码改变了时序，才在每一遍整套测试里出现（§10.18）。

**工具（`scripts/tui_envspy/`，只在 Linux 上）。**

| 提交 | 内容 |
|---|---|
| `5421459` | 探针：`envspy.c`（一个 `LD_PRELOAD` 共享库，包住 `getenv`、`secure_getenv`、`setenv`、`unsetenv`、`putenv`、`chdir`、`fchdir`）、`envspy.sh`、`runner.sh`（把探针设成 cargo 的测试运行器）、`analyze.py`（报告）、`selftest.sh`；压力脚本 `stress.sh`；README；`scripts/tests/test_tui_envspy.py`（10 个用例）；`AGENTS.md` 里一句话 |
| `b00ea06` | `stress.sh` 可以经探针运行（`ENVSPY_TAG`） |
| `37b825e` | `stress.sh` 可以只跑几个点名的测试（`--` 之后的词是过滤器），用于配对与人口实验 |
| `6b434b0` | `stress.sh` 跳过清单跳过的测试（清单里 `skip`）。`pi-acp-lib` 有一个测试永远不结束（`vendor_x_ai_ext_is_dropped_without_sending`，§10.4：`acp::ExtRequest` 序列化时丢掉了 `method`，所以丢弃逻辑不触发，排队的请求没有人回答）；整个 lib 的普查第一次跑它时，第一遍就没有结束，我是用 `gdb` 看到那个线程停在 `block_on` 里才查到的。清单早就跳过它；脚本以前不知道 |

探针记下每次改环境变量的测试和线程，报告三类事：一个测试改变量时，另一个测试还在跑（重叠）；一个测试改了变量却没有还原（泄漏）；一个测试读了别的测试改过的变量。测试的名字取自符号化的调用栈。压力脚本用 libtest 的 `--shuffle-seed` 与 `--test-threads N`（`RUSTC_BOOTSTRAP=1`，只在运行时设）把 `pi-pager` 或 `pi-shell` 的 lib 测试跑很多遍。每一遍有自己的种子，失败的一遍可以重复。探针只看环境变量；`static` 与别的共享状态只有压力脚本找得到，而且它只说出哪个测试失败，不说是哪个测试造成的。找原因用两个办法：配对与人口（下面「怎么找到肇事的测试」）。

**找到的失败。** 压力运行找到 9 个偶发失败的测试（`pi-pager` 7 个，`pi-shell` 2 个）。配对与人口实验又找到 10 个受害者，共 19 个。另有 2 个失败，原因没有查明（见「留下的」）。每个测试的原因都用实验查过，除了下面点名的几处。

**表 1：测试自己的缺陷（4 项）。**

| 测试 | 原因 | 证据 | 修法（提交） |
|---|---|---|---|
| `pi-shell` `config::tests::subagents_config_models_with_env_var_enables` | `GROK_SUBAGENTS` 受两把互不排斥的锁保护：`config/tests.rs` 里的一把 `Mutex`，`agent/config_tests.rs` 里的一个 `#[serial]` 键 | 32 线程，修前 2 次运行失败（共 30 次） | 三把共享的锁（`config/test_env_locks.rs`，管 `GROK_SUBAGENTS` 等、`GROK_RESPECT_GITIGNORE` 等、`GROK_*_MODEL`），两边都用（`272c339`） |
| `pi-pager` `memory_trace::tests::sample_events_are_valid_jsonl_and_rotate` | 别的测试不拿「进程级追踪输出」的锁，却往里写了一行 | 32 线程，修前 5/40 | 只检查样本行，接受别的测试写进来的行（`1c20d7c`） |
| `pi-shell` `auth::manager::lock::tests::dropping_the_guard_silences_the_heartbeat_before_anyone_else_can_hold_the_lock`（心跳） | 别的测试 `fork` 出的子进程，在 `exec` 之前还拿着锁文件的描述符；守卫释放之后马上再加锁，得到 `WouldBlock`（见下面的更正） | 修前：普查 1/20；§10.18 里的 `--lib` 运行 5/15；`fork` 风暴实验 363/1,500 轮（见下） | 加锁失败时重试，最多 5 s（`f0335a5`） |
| 5 个 `pi-pager` 测试：4 个 `persist_permission_mode_*` 与 `trust_folder_grants_and_resolves` | 设了 `GROK_HOME`（后一个还设了 `GROK_TEST_VERSION`），然后不还原。同一个进程里后面的测试因此读到一个已经删掉的目录，或者看到一个模拟的发行构建 | 探针（整套清单的一遍，默认顺序，12 个线程）：重叠 3、泄漏 1、进程结束时仍被改的变量 2；读到泄漏值的约 12,900 次 | 用守卫还原；那 4 个测试加了 `serial(GROK_HOME)`（`91a22de`）。修后：重叠 0，泄漏 0，进程结束时仍被改的变量 0，读到泄漏值 0 |

`pi-shell` 的探针结果前后都是 0 / 0 / 0。三个集成测试二进制里也有设了变量却不还原的地方。我没有当它们是缺陷，没有动：`pi-pager` 的 `python_agent_e2e`（12 处泄漏，进程结束时 6 个）与 `grok_home_paths`（1 处泄漏，进程结束时 1 个），`pi-shell` 的 `test_config_update_isolation`（进程结束时 1 个）。每个文件是一个单独的测试二进制，一个进程只给本文件的测试用。变量或者在开头为整个二进制设一次（`test_config_update_isolation`；`python_agent_e2e` 也先设一次），或者由 `serial` 测试各自重设（`python_agent_e2e`、`grok_home_paths`）。

**表 2：进程级的 `static`（6 族，57 个测试）。** 写者 = 改这个状态的测试。受害者 = 读这个状态、被别的测试的改动弄失败的测试。

| 族 | 进程级的状态 | 写者（现在各在自己的进程里） | 受害者与证据 |
|---|---|---|---|
| 1 颜色等级与终端原生调色板 | `COLOR_LEVEL_CAP`（`pi-markdown/src/colors.rs:134`）；原生调色板的锁（`set_terminal_native_lock`，`pi-pager-render/src/theme/cache.rs:115`） | `thinking_body_dim_italic_survives_the_terminal_native_palette`；`reseed_round_trip_flips_every_mode_derived_gate` | `background_block_gutter_uses_block_background_fill`：配对 200/200 失败，对照 0/200。`execute_header_display_matches_overlay_body`：种子 20（32 线程）6/6 失败；配对没有复现（两种顺序各 200 次，0 次），原因是从代码推断的 |
| 2 最小模式的标志 | `MINIMAL_MODE_ACTIVE`（`app/mod.rs:143`；屏幕模式、内嵌与滚动条的几个全局量由 `apply_screen_mode_globals` 一起设） | `minimal_double_ctrl_c_arms_then_quits`；`reseed_round_trip_flips_every_mode_derived_gate`（同上） | `ordinary_picker_keeps_tip_and_reset`：配对 200/200，对照 0/200；种子 104（4 线程）4/6 |
| 3 双向文字的闩 | `RTL_BIDI_ENABLED`（`pi-pager-render/src/render/bidi.rs:25`） | 6 个 `rtl` 用例（`with_rtl_bidi`）；它们同时是受害者：至少 60 处 `set_appearance` 把闩存成 `false`，且不拿锁 | 3 个 `rtl_*` 用例在压力运行里失败；人口实验：与调 `set_appearance` 的三组测试一起跑，52/60、44/60、29/60 次运行失败，单独和与另外两组一起跑 0/60。`visual_selectable_cols_shifts_region_past_rtl_ellipsis`：与 5 个打开闩的测试配对 198/200，压力运行里没见过 |
| 4 内嵌弹窗的标志 | `modal_window::EMBEDDED`（`views/modal_window.rs:38`） | 3 个：`embedded_fills_area_without_centering`、`embedded_cursor_row_takes_selection_accent`、`dropdown_chrome_embedded_is_flush_left` | `settings_modal` 的 4 个用例（`modal_widens_when_editing_max_thoughts_width`、`modal_returns_to_default_width_when_leaving_edit_mode`、`footer_total_height_grows_when_hints_wrap`、`footer_has_blank_line_between_tip_and_hints_when_hints_wrap`）：`views::` 人口 14/60 次运行失败。配对（`modal_widens_…`）修前 10/100 与 7/100，对照 0/100；其余 3 个没有配对，原因从失败信息推断（弹窗宽度 200，应为 192 或 110） |
| 5 语音设置的门 | `VOICE_MODE_ENABLED`（`app/mod.rs:182`） | 2 个：`rebuild_rows_drops_voice_settings_when_gate_turns_off`、`voice_settings_update_omitted_leaves_gate_unchanged` | `rows_contain_categories_and_settings_through_pr_14`：配对 100/100 与 100/100，对照 0/100 |
| 6 主题 | `theme::cache`（进程级；代码里 `pin_theme()` 出现 54 处，只有拿它的测试互相排斥） | 43 个，经两个帮助函数：`with_theme_test_env`（`app/dispatch/tests/mod.rs`，27 个用户）与 `with_test_env`（`slash/commands/theme.rs`，16 个用户） | `run_job_succeeds_on_temp_file`（`left: TokyoNight`；配对 0/100 与 1/100：窗口很短）、`mermaid_view_pending_dedup_wins_over_disk_hit_race`（配对 96/100 与 94/100，对照 0/100）、`ghost_text_renders_at_cursor_when_at_end`（配对 92/100 与 90/100，对照 0/100）；`mermaid_view_disk_hit_runs_action_without_dispatch` 失败过一次，没有配对。`app::` 人口 9/60 次运行失败 |

写者合计：8 个直接用 `in_own_process`（族 1、2 的 3 个，族 4 的 3 个，族 5 的 2 个）+ 6 个 `rtl` 用例 + 43 个主题用例 = **57 个**。族 1 与 2 共用 `reseed_round_trip_flips_every_mode_derived_gate`，所以 8 个里它只算一次。配对表里的 5 行（族 4、5、6）是在 `4736014`（族 1–3 已隔离，族 4–6 还没有）与最后的提交上各跑 100 遍：见「验证」。

**为什么不给受害者加锁，而是隔离写者。** 受害者有几百个（一个读主题的渲染测试都是），而且每一轮人口实验都找到新的。写者只有 57 个。锁只管拿锁的测试，所以要给每一个读者都加上；一个漏掉的读者就是一个偶发失败。把写者放进自己的进程，读者不用动，已经找到的和还没找到的受害者一起修好。

**做法：`in_own_process`。** `pi-pager/src/test_util.rs` 里的 `in_own_process(|| { … })` 把一个测试的主体放进子进程里跑。

- 父进程（`cargo test` 里的一个测试线程）取自己线程的名字。libtest 用测试的全名给测试线程命名（rustc 1.94.0 上查过：默认线程数、`--test-threads 1`、`--exact`、`RUST_TEST_THREADS=1` 下都是）。
- 它再启动同一个测试二进制：`--exact <全名> --nocapture --test-threads 1`，并设环境变量 `PI_PAGER_TEST_IN_OWN_PROCESS`。子进程里这同一个测试再跑一遍；`in_own_process` 看到这个变量，就直接执行主体。
- 父进程**不**执行主体。它等子进程结束，要求退出码是 0，并且子进程的输出里有 `test result: ok. 1 passed;`（恰好一个测试通过；子进程什么也没跑，测试就不能通过）。不满足就 panic，信息里带子进程的 stdout 与 stderr。
- 子进程有自己的 `static`、环境变量与当前目录，主体里的改动出不去。

限制：

- 调用之前与之后的代码，在两个进程里都跑；只有主体在父进程里被跳过。所以不要在调用之外改进程级状态。
- 子进程继承启动那一刻父进程的环境变量。那一刻若有另一个测试正在改环境变量，子进程会带着它。整个 lib 随机顺序运行 60 遍，没有出现这个问题。
- 取不到带 `::` 的线程名时（测试没有在 libtest 的线程里跑），主体在当前进程里直接跑：没有隔离，也不失败。自检 `a_change_in_the_body_stays_in_the_child` 会发现这种情况：父进程里的标志被改了。
- 代价：每个用到它的测试多一次进程启动，约 30 ms；57 个合计约 1.7 s 的 CPU 时间，分在各个线程上（本机量的；CI 上量到的更大，见「验证」）。
- 与 `cargo nextest` 兼容：nextest 本来就一个测试一个进程，子进程再启动一次，结果相同。
- 自检（2 个新测试，所以 lib 的测试 5,377 → 5,379，清单里 `reference_passed` 的和 8,488 → 8,490）：`a_change_in_the_body_stays_in_the_child`（主体里的改动只留在子进程里）与 `a_failure_in_the_body_fails_the_parent`（主体失败，父进程失败）。

**先走过的路（没有采用）。**

1. 给受害者加锁。项目里已有 `pin_theme()`：它拿一把锁，钉住主题与颜色等级。我给找到的受害者加了它。每加一个，修好一个测试；下一轮人口实验又在 `scrollback::` 里找到 5 个新的受害者。读者太多，锁加不完。
2. 重试。`with_rtl_bidi` 的第一版在闩被别的测试关掉时，把测试主体再跑一遍（最多 100 次）。它要求主体没有副作用，而且受害者的窗口还在。

这两个做法的提交只在本地的备份分支里，不在 PR 里。

**普查（随机顺序）。** `stress.sh`：每个格子里是「有失败的运行数 / 运行数」。

| 运行 | 修之前（`739d2bb`） | 修了缺陷与前 3 个（`aa76df4`） | 最后（`88e04e9`） |
|---|---|---|---|
| `pi-pager` lib，32 线程，种子 1–32 | 6/32 | 3/32 | 0/60（种子 1–60，整个 lib 的人口） |
| `pi-pager` lib，32 线程，种子 201–232（新种子） | — | — | 0/32 |
| `pi-shell` lib，32 线程，种子 1–20 | 2/20 | 0/20 | 0/20（`pi-shell` 的测试代码在 `aa76df4` 之后没有变；在最后的树上又跑了一遍） |
| `pi-pager` lib，4 线程（CI 的线程数），种子 101–120 | 0/20 | 1/20 | 0/20 |
| `pi-pager` lib，固定顺序（CI 的顺序），32 线程，10 遍 | — | 0/10 | 0/10 |
| 曾经一再失败的种子，各 6 遍：32 线程的种子 20、21、24，4 线程的种子 104 | — | 6/6、6/6、3/6、4/6 | 0/6、0/6、0/6、0/6 |

修之前的另一批：`pi-shell` 32 线程 1/10，`pi-pager` 32 线程 3/8，`pi-shell` 4 线程 0/5，`pi-pager` 4 线程 1/5。每个失败的运行在输出文件里有 panic 信息。一个测试线程（种子 20、21、24、104）：0/1，所以这些失败需要至少两个测试同时跑。

其他 7 个 suite（`pi-pager-render`、`pi-pager-minimal`、`pi-acp-lib`、`pi-http`、`pi-telemetry`、`pi-file-utils`、`pi-sampling-types`）的 lib：32 线程，随机顺序，各 10 遍：每个都是 0/10（`pi-pager-render` 1,092 个测试，`pi-pager-minimal` 79，`pi-http` 13，`pi-telemetry` 239，`pi-file-utils` 216，`pi-sampling-types` 320，`pi-acp-lib` 21；最后一个不含清单跳过的那个不结束的测试）。

**人口实验（一个模块的测试，32 线程，随机顺序，种子 1–60）。** 在修之前的提交和最后的提交上各跑一遍：

| 模块 | 测试数 | 之前 | 之后（`e6e5f98` 的树） |
|---|---|---|---|
| `views::` | 1,341（另 2 个忽略） | 14/60（`20232d3`） | 0/60 |
| `app::` | 2,027（另 9 个忽略） | 9/60（`20232d3`） | 0/60 |
| `scrollback::` | 956 | 7/60（`181a254`） | 0/60 |
| 整个 lib | 5,377（另 12 个忽略） | 见上表 | 0/60 |

**配对实验（一个受害者与一个写者，2 个线程，100 遍，两种顺序；对照 = 受害者与无关的测试）。** 「之前」是 `4736014`（族 1–3 已隔离，族 4–6 还没有），「之后」是最后的提交。

| 族 | 受害者 + 写者，名字顺序 | 受害者 + 写者，随机顺序 | 对照 | 之后（三行都是） |
|---|---|---|---|---|
| 4 内嵌弹窗的标志 | 10/100 | 7/100 | 0/100 | 0/100 |
| 6 主题（`run_job_succeeds_on_temp_file`） | 0/100 | 1/100 | 0/100 | 0/100 |
| 5 语音设置的门 | 100/100 | 100/100 | 0/100 | 0/100 |
| 6 主题（`mermaid_view_pending_dedup_…`） | 96/100 | 94/100 | 0/100 | 0/100 |
| 6 主题（`ghost_text_renders_at_cursor_…`） | 92/100 | 90/100 | 0/100 | 0/100 |

每一行第一遍的结果：对照和「之后」的行是 `2 passed`；「之前」的语音设置的门、`mermaid`、`ghost_text` 三行是 `1 passed; 1 failed`（它们几乎每遍都失败）。两种结果都说明两个过滤器各选中一个测试。族 1、2、3 的配对与人口数字是更早测的：族 1、2：200/200 → 0/200（同一批配对）；种子 20（32 线程）6/6 → 0/6；种子 104（4 线程）4/6 → 0/6；族 3：人口 52/60、44/60、29/60 → 0。

**探针（`ENVSPY_TAG`，经随机顺序运行）。** `pi-pager` 与 `pi-shell` 的 lib 各 5 遍，经探针运行（32 线程，随机顺序，种子 1–5，`88e04e9`）：10 遍里重叠 0，泄漏 0，进程结束时仍被改的变量 0，读到已结束的测试留下的值（`GET-LEAK`）0 组。还在的是 `GET-FOREIGN`（一个测试读一个别的测试正在改着的变量）：`pi-pager` 每遍 6 组，变量是 `GROK_HOME` 与 `GROK_TEST_VERSION`，每组 1 到 41 个读者测试；`pi-shell` 每遍 25 组（`GROK_HOME` 12 组，`GROK_TEST_VERSION` 11 组，`GROK_AUTH_EARLY_INVALIDATION_SECS` 2 组）。这些读者不拿锁。探针不能说读者是否依赖这个值，我也没有逐组读，所以这些组留在「留下的」里。对照（`aa76df4` 之后的整套清单，默认顺序，12 个线程）：`pi-pager` 重叠 0、泄漏 0、进程结束时仍被改的变量 0、`GET-LEAK` 0 组、`GET-FOREIGN` 7 组；`pi-shell` 重叠 0、泄漏 0、`GET-FOREIGN` 16 组。修之前同样的运行：`pi-pager` 重叠 3、泄漏 1、进程结束时仍被改的变量 2、`GET-LEAK` 3 组、`GET-FOREIGN` 4 组。这几个数不能直接比：运行的线程数与顺序不同，一组是一个调用点，不是一次读。

**怎么找到肇事的测试。** 压力运行只说出哪个测试失败，不说是哪个测试造成的。两个办法：

- **配对。** 把失败的测试和一个嫌疑测试一起跑 100 到 200 次（`stress.sh … 200 2 0 1 -- 失败的测试 嫌疑测试`：两个过滤器、两个线程，两个测试同时开始），再换成一个无关的测试做对照。两种顺序都要试：嫌疑测试先开始时，它可能在受害者读状态之前就跑完了。
- **人口。** 受害者的窗口只有几微秒时，配对不够（`run_job_succeeds_on_temp_file` 与主题写者：0/100 与 1/100，而整个 `app::` 模块一起跑是 9/60）。把受害者与一整个模块的测试一起跑（过滤器是测试全名的子串，`scrollback::state::selection::tests::` 选整个模块），32 线程、随机顺序、60 遍，并与不会造成失败的模块对照。

三个 `rtl_*` 用例是这样找到肇事者的：与一个调 `set_appearance` 的测试的配对没有复现（两种顺序各 200 次，0 次失败），人口实验复现了（在隔离了族 1、2、还没有隔离 `rtl` 的中间提交 `181a254` 上；它是本地提交，不在 PR 里）：

| 与谁一起 | 有失败的运行 |
|---|---|
| 单独 | 0/60 |
| `views::permission_view::tests::`（不调 `set_appearance`） | 0/60 |
| `views::settings_modal::tests::`（不调 `set_appearance`） | 0/60 |
| `scrollback::state::selection::tests::`（70 个测试，调 `set_appearance`） | 52/60 |
| `scrollback::state::layout::tests::`（63 个测试，调 `set_appearance`） | 44/60 |
| `scrollback::render::tests::`（72 个测试，调 `set_appearance`） | 29/60 |

`set_appearance`（`scrollback/state/mod.rs:361`）把 `rtl_bidi` 的值（默认 `false`）存进进程级的闩 `RTL_BIDI_ENABLED`；`AppView::set_appearance`（`app/app_view.rs:1634`）也存它。`rtl_*` 测试把闩开几微秒，这时任何一个这样的测试都能把它关掉。

**心跳用例：原因更正。** §10.16 与 §10.18 把这个用例记成「负载下偶发，没有查根因」。根因是：另一个测试（任何一个会 `fork` 的测试，比如起子进程的）在 `fork` 之后、`exec` 之前，子进程还拿着父进程所有打开的描述符的副本，包括锁文件的。守卫释放之后马上重新加锁，`flock` 因此得到 `WouldBlock`（os error 11）。**`fork` 风暴实验**（临时分支 `tmp/fork-storm`，不在任何 PR 里；代码在 B.12）：一个测试 300 轮「加锁、起心跳、释放守卫、再加锁」，数第二次加锁立刻失败的轮数。单独跑：5 遍共 1,500 轮，0 轮失败。另一个测试同时不停地 `fork` `/bin/true`（`--test-threads 4`，风暴 6 s）：5 遍分别 65、72、83、68、75 轮失败，共 363/1,500（24.2%）。修过的真测试在同样的风暴下通过 5/5。

**混沌模式什么也没找到。** 探针有一个「混沌」模式：一个测试改了变量之后，同一个测试第一次读它时睡 N 毫秒，把窗口拉宽。它只能拉宽本来就重叠的两个测试之间的窗口。`pi-shell` 与 `pi-pager` 各跑 4 遍、50 ms（修之前），没有失败。压力运行找到了失败：CI 的固定顺序与 4 个线程下，会冲突的测试根本不同时跑。

**`cargo nextest`。** 一个测试一个进程时，环境、`static` 与当前目录是每个测试自己的，这类竞争不会发生。`aa76df4` 上（那时 `static` 的 6 族还没有隔离）：`pi-pager` 的 lib 跑了 11 遍（32 线程 10 遍，4 线程 1 遍），`pi-shell` 的 lib 跑了 5 遍（32 线程），共 16 遍，0 个失败；测试集合相同（`pi-pager` 5,377 通过 + 12 忽略，`pi-shell` 833 + 1）。代价：`pi-pager` 的 lib 在 4 线程下，nextest 79.5 s，`cargo test` 29.7 s（约 2.7 倍）；32 线程下 `pi-pager` 38.3–46.0 s，`pi-shell` 9.5–9.9 s。nextest 不管：测试共用的文件与端口、一个测试自己起的线程与子进程。它不跑文档测试，要另外跑 `cargo test --doc`。装法：GitHub Actions 里 `taiki-e/install-action@nextest`（本地用的是 cargo-nextest 0.9.148）。**没有改 CI。** 现在已知的失败都在测试里修了，nextest 不再是为它们而需要的；它的好处是，以后新写的测试也不会犯这类错；代价见上。改不改，等用户决定。**之后（r18，2026-10-20）：**用户选了改，做在 PR [#31](https://github.com/zy1233/pi-python/pull/31) 里，见 §10.21。上面的「要另外跑 `cargo test --doc`」不适用：基线执行器从来没有跑过文档测试。

**一个没有关系的发现：`protoc` 让 `pi-tools-api` 一遍遍重新构建。** 在 WSL 里跑 `cargo` 时，每次都先编译 `pi-tools-api` 与它上面的 crate（`pi-pager` 要约 2 分钟）。原因：`pi-proto-build/src/find_protoc.rs`（第 3 步，约 81–84 行）在 `$PROTOC` 与 `bin/protoc` 都没有、`PATH` 上有 `protoc` 时，返回一个**裸名字** `protoc`；`pi-proto-build/src/lib.rs`（137–142 行）把它原样打成 `cargo:rerun-if-changed=protoc`。这个路径（相对包目录）不存在，cargo 因此认为构建脚本每次都过期。CI 的日志里也数得到 `Compiling` 行（`TUI CI` [run 39193752451](https://github.com/zy1233/pi-python/actions/runs/39193752451)，在 #27 的 `6bd7db5` 上）：`cargo test` 一个 job（117871929387）里，`pi-tools-api` 编译 7 次，`pi-shell` 4 次，`pi-pager` 3 次；`Gates + cargo check` 一个 job（117871929167）里，2 次、2 次、0 次。我当时把这些次数都当作这个问题的后果；**这是错的，见下面 PR #30 的 CI 结果。**本地的绕法：设一个**绝对路径**的 `PROTOC`（B.12 的命令就这样做）。修法有三个（都没有做）：在 `find_protoc` 里返回绝对路径；名字是裸名字时不打 `rerun-if-changed`；在 `tui-ci.yml` 里设 `PROTOC`。这是上游代码，还是 CI 配置，所以先问用户。**之后（2026-10-20）：** 用户选了第一种，做在 PR [#30](https://github.com/zy1233/pi-python/pull/30) 里：`find_protoc` 的第 3 步在 `PATH` 的各目录里找名为 `protoc` 的文件（要是文件，Unix 上要有执行位），返回绝对路径，第一个能运行的算数；8 个新单元测试，其中一个回归测试在旧代码上失败（`` `protoc` is not a file ``）。WSL2 的数字（`protoc` 28.3 在 `PATH` 上，`PROTOC` 没设，两遍之间什么都没改，`cargo check --locked -p pi-tools-api`，输出放在 tmpfs 里）：修前第 2、3 遍都重新编译 `pi-tools-api`（cargo 的指纹日志：`stale: missing ".../pi-tools-api/protoc"`；第 3 遍 2.2 s），修后第 3 遍只有 `Finished`（0.33 s）；构建脚本生成的文件（`pi.grok.tools.v1.rs`）修前修后的 sha256 相同。一个差别：`PATH` 上找到的 `protoc` 现在是绝对路径，所以 `find_protoc_include_dir` 也给出 `<前缀>/include`（它旁边的目录，有的话），`protoc` 多得到一个 `-I`；绝对的 `PROTOC` 本来就是这样。这台 WSL 上 `~/.local/include` 不存在，所以这个旗标在本地没有试过；CI 上 `setup-protoc` 解出 `bin/` 与 `include/`，PR 的 `TUI CI` 会试它。**PR #30 的 CI 与更正（`7a4ce9d`）：**`TUI CI` [run 39356795564](https://github.com/zy1233/pi-python/actions/runs/39356795564) 与 `CI` [run 39356795545](https://github.com/zy1233/pi-python/actions/runs/39356795545) 都通过，8,490 通过 / 0 失败 / 21 忽略，每个 suite 等于它的 `reference_passed`。上面数 `Compiling` 行得出的次数**不全是这个问题造成的**。`Gates + cargo check` job 的两条 `cargo` 命令选的包相同，`pi-tools-api` 修前编译 2 次，修后 1 次，所以那一次是这个问题（job 用时 3 m 17 s；#28 的三次运行是 3 m 19 s、3 m 47 s、4 m 27 s）。`cargo test` job 是每个 suite 一条 `cargo` 命令，每条命令按自己选的包统一 feature，所以 `pi-tools-api` 在用到它的 7 个 suite 里各编译一次（`pi-shell` 共 4 次，`pi-pager` 共 3 次），修后一样：7 / 4 / 3，逐个 suite 的分布也与修前相同；job 用时 22 m 08 s（#28 的三次运行是 22 m 04 s、22 m 15 s、22 m 43 s）。所以这个修复在 CI 上的收益很小；同一条命令重复运行时它才明显，本地开发就是这样（上面的 WSL 数字）。`-I<前缀>/include` 这一点：两个 job 都编译了 `pi-tools-api`，没有错误；日志不打印 `protoc` 的参数，所以从日志里看不出这个旗标是不是真的传了。

**验证。**

- **长运行在 `88e04e9` 上**（一个本地提交：提交的顺序重写之前的，没有推送；整套清单、整个 lib 的 60 遍、人口、配对与探针）。整套清单（`scripts/tui_baseline.py test`，WSL2，清环境，增量编译，约 4 m 56 s）：**8,490 通过 / 0 失败 / 21 忽略**；`pi-pager` 5,651，`pi-shell` 838，其余各 suite 等于各自的 `reference_passed`。r16 记的那个已知失败（`pi-pager-render` 的 tmux 探测）这一遍通过了。
- **最后的提交 `6438b59`。** 长运行之后，提交的顺序重写过。它的 `tui/` 与 `88e04e9` 只差 `test_util.rs` 里的两处：一个空行，和 panic 信息的格式串（`({}; …` 代替 `(exit {}; …`：`ExitStatus` 的文字已经带 `exit status`，原来打出两个 `exit`）。`scripts/` 差两处：`stress.sh` 跳过清单跳过的测试，`tui_baseline.toml` 里的注释与 `reference_passed`。这个提交上跑了：
  - `scripts/tui_baseline.py check`：`cargo check --workspace --tests` 退出码 0，0 个错误；告警 0（上限 0）；依赖图 970；`leader` 243，`x.ai` 297。
  - `rustfmt --check`，20 个改动的 Rust 文件：没有新的 hunk（rustfmt 1.8.0，2024 版）。
  - `stress.sh` 里「跳过」那一段（提取出来单独跑，不用 `cargo`）：`pi-acp-lib` 得到 `--skip vendor_x_ai_ext_is_dropped_without_sending`，`pi-pager` 什么也没有；`set -u` 下展开正常。
- **`6438b59` 上没有跑测试，也没有跑 `clippy`。** 我本来要在这个提交上再跑整套清单、整个 lib 的 60 遍、两个自检、负面检查和 `pi-acp-lib` 的跳过。这时 WSL 的虚拟磁盘（它的文件在 D: 盘上，只增不减）已经长到 80 GB，把 D: 盘写满：WSL 停了，一次在跑的检查以 `Bus error` 结束。大头是构建目录（29 GB）。我用 NTFS 压缩腾出 0.5 GB，重启 WSL，删了我在 WSL 里的构建与测试产物（31 GB → 0.2 GB）。虚拟磁盘文件仍是 80 GB：压缩它要管理员权限，由用户做。所以 **CI 是第一次在这个提交上跑测试**。
- **CI。** CI 在 `ea9609f`（比 `6438b59` 只多一个只改文档的提交）上第一次跑了测试：`TUI CI` [run 39240903399](https://github.com/zy1233/pi-python/actions/runs/39240903399) 通过，**8,490 通过 / 0 失败 / 21 忽略**，每个 suite 等于它的 `reference_passed`（`pi-pager` 5,651 / 12 忽略，`pi-shell` 838 / 1 忽略，`pi-pager-render` 1,092 / 2 忽略，`pi-pager-minimal` 79，`pi-pager-bin` 21，`pi-acp-lib` 21，`pi-http` 13，`pi-telemetry` 239，`pi-file-utils` 216 / 6 忽略，`pi-sampling-types` 320）；门禁：依赖图 970，0 条告警，`leader` 243，`x.ai` 297，`cargo check --workspace --tests` 0 个错误；`cargo test` job 22 m 04 s，`Gates + cargo check` 3 m 47 s。`CI` [run 39240903417](https://github.com/zy1233/pi-python/actions/runs/39240903417) 通过：`ruff` 13 s，`pytest` 3.11 / 3.12 / 3.13。第二次运行（最后的提交 `7b27775`，只改文档；`TUI CI` 的路径过滤看整个 PR 的改动，所以又跑了）：`TUI CI` [run 39244339476](https://github.com/zy1233/pi-python/actions/runs/39244339476) 同样通过，8,490 通过 / 0 失败 / 21 忽略，`cargo test` job 22 m 43 s，`Gates + cargo check` 4 m 27 s；`CI` [run 39244339779](https://github.com/zy1233/pi-python/actions/runs/39244339779) 通过。成本：`pi-pager` 的 lib 在 CI 上 libtest 用时 15.55 s 与 15.79 s（两次运行）；之前 5 次 CI 运行（没有隔离）是 8.21 s（`7d82c27`）、9.11 s（`6bd7db5`）、13.80 s（`da34128`）、14.73 s（`739d2bb`）、14.83 s（`4046f4c`），平均 12.1 s。两次都在这个范围的上沿或之上，平均多约 3.5 s，约每个隔离的测试 60 ms，比本地量的 30 ms 大。样本少，运行之间的波动有 6.6 s，所以这只是估计。整个 `cargo test` job 是 22 min 左右，影响小。CI 的运行是 4 个线程、固定顺序，找不到这类竞争，所以这个「通过」不证明竞争没有了；证据是上面本地的随机顺序运行。`clippy` 仍没有跑过。
- **Python。** `ruff check .` 与 `ruff format --check .` 通过。全部 `pytest`（`real_llm` 不算，含 `test_tui_envspy.py` 的 10 个）：Windows 2,569 通过 / 39 跳过 / 31 未选；Linux（WSL2，`rg` 在 `PATH` 上）2,578 通过 / 30 跳过 / 31 未选。两边都对 `6438b59`。

**没有验证 / 留下的。**

1. **约 140 个测试写外观缓存里的别的设置**（21 个文件：`set_show_thinking_blocks` 69、`set_group_tool_verbs` 20、`set_collapsed_edit_blocks` 16、`set_vim_mode` 15、`set_render_mermaid` 12、`set_keep_text_selection` 7、`set_simple_mode` 7 等）。没有隔离：压力运行与配对实验没有为它们找到受害者，我只隔离了有受害者的写者。
2. **2 个失败没有查明。** `blank_quote_line_survives_drag_copy_as_blank` 失败过一次，没有复现。`command_palette_search_bar_cursor_only_when_focused` 在 `20232d3` 上 1/60 遍失败；它可能读 vim 模式（15 个测试写它），我没有用配对证实；隔离之后的运行里它没有再失败。
3. **`GET-FOREIGN` 的组**（见「探针」）：读 `GROK_HOME`、`GROK_TEST_VERSION`、`GROK_AUTH_EARLY_INVALIDATION_SECS` 又不拿锁的测试。r16 把「读 `GROK_HOME` 又没有任何 `serial` 的测试」列为要查的一项；这一轮的结果是：探针列出了它们，混沌模式（50 ms，修之前，每个包 4 遍）和随机顺序的 lib 运行（`pi-pager` 92 遍，`pi-shell` 20 遍）没有因它们失败，我没有逐个读。
4. **`pi-shell` 里有名字的 `serial` 键**（r16 点名的第二处；第一处，`pi-pager` 里的 `serial(GROK_HOME)`，是表 1 里那 4 个测试：它们现在与别的 `GROK_HOME` 测试用同一个键）。`force_dark_wake_env`（`GROK_AUTH_FORCE_DARK_WAKE`）：5 遍探针里没有测试在它被改的时候读它。`heap_profile_hooks`：保护一个只装一次的 `OnceLock`，这个键下只有一个测试，我没有查别处。`attribution_emit_count`：保护进程级计数器 `EMIT_COUNT`；整个 `pi-shell` 里只有 `StorageClientAttributionBridge::record_401`（`auth/credential_provider.rs`）会在这两个测试之外增加它，哪个测试会走到那里，我没有查；探针看不到计数器。
5. **别的 crate。** 其他 7 个 suite 的 lib 只各跑了 10 遍；清单外的 crate（`pi-workspace`、`pi-fast-worktree`、`pi-update` 等）和集成测试二进制（上面三个有泄漏的文件除外）没有查。
6. **只在一台机器的 Linux（WSL2，12 个 CPU，32 线程）上。** 另一台机器、macOS、Windows 可能有别的竞争。工具只能在 Linux 上用。0/60 是一个样本：窗口只有几微秒时，一遍找不到的，一千遍也许找得到（配对实验里的 `run_job_succeeds_on_temp_file` 就是 0/100 与 1/100）。
7. **`in_own_process` 的限制**（见上）。
8. **没有做、要等用户决定的**：`TUI CI` 改用 `cargo nextest`；`protoc` 的重新构建问题；决策 10 的另外两项。（r18：前两项做了，见 §10.21。）

### 10.21 r18 追加：改用 `cargo nextest`、`protoc` 的路径、`release.yml` 的令牌

用户在 2026-10-20 从「接下来做哪些」里选了三项：`release_token`、`protoc_abs`、`nextest_ci`。没选的：`protoc_ci_env`（在 `tui-ci.yml` 里设 `PROTOC`）、`d10_rest`（决策 10 的另两项：`util/config/mcp.rs` 的 `load_mcp_servers`、`pi-pager/src/recent_dirs.rs`）、`merge_27_28`。三项各做成一个 PR，一个叠在一个上，生产代码没有行为变化，**都没有合入**：[#29](https://github.com/zy1233/pi-python/pull/29)（分支 `ci/release-setup-protoc-token`，基是 #28 的分支）→ [#30](https://github.com/zy1233/pi-python/pull/30)（`fix/tui-protoc-absolute-path`，基是 #29 的分支）→ [#31](https://github.com/zy1233/pi-python/pull/31)（`ci/tui-nextest`，基是 #30 的分支）。前一个合入之后，把后一个的基改成 `main`（#27 → #28 → #29 → #30 → #31）；基改成 `main` 时若两边的树相同，把 `main` 合进它的分支，`git merge -s ours`，像 #25 与 #26 那样。

**#29：`release.yml` 的令牌。** `release.yml` 里两处 `arduino/setup-protoc@v3`（Linux 与 macOS 各一处）加 `repo-token: ${{ secrets.GITHUB_TOKEN }}`，与 `tui-ci.yml` 里同一步的三处一样。原因在 §10.15 续 8：v0.5.0 的发布里，macOS 的构建在这一步因 GitHub API 的限额失败了两次（没有令牌的请求共用运行器的出口地址）。只改这一个工作流文件，没有别的改动。`release.yml` 在推 tag 时跑；手动运行是演练（测试与构建都做，不发布）。`CI` [run 39356019475](https://github.com/zy1233/pi-python/actions/runs/39356019475)（`81171da`）通过。演练 [run 39360804691](https://github.com/zy1233/pi-python/actions/runs/39360804691)（在 #29 的分支上，`81171da`）：通过。`Pre-release tests` 2 m 07 s；两个构建 job 的 `Install protoc` 各 5 s（Linux x86_64）与 6 s（macOS arm64），构建 20 m 14 s 与 30 m 15 s；`Publish to PyPI` 与 `GitHub Release` 按设计跳过。它说明 `release.yml` 仍是有效的工作流、`setup-protoc` 接受 `repo-token`；它不证明限额问题已经解决：那个限额是随机的（运行器共用出口地址），要等下一个 tag。#29 的 PR 描述原来写「`release.yml` 只在推 tag 时跑，要等下一个 tag 才有第一次测试」，这是错的（文件里有 `workflow_dispatch` 的演练），已改。

**#30：`protoc` 的绝对路径。** 做法与 WSL2 的数字在 §10.20 的 `protoc` 一段（「之后」）。结论：`find_protoc()` 返回 `PATH` 上 `protoc` 的绝对路径，同一条 `cargo` 命令重复运行时，`pi-tools-api` 不再每次重新构建（第 3 遍 2.2 s → 0.3 s）。**更正 r17 的一句话。** §10.20、r17 的修订记录与计划里，我把 CI 的 `cargo test` job 里 `pi-tools-api` 编译 7 次（`pi-shell` 4 次，`pi-pager` 3 次）当作这个问题的后果，并说修了会少。这是错的。#30 的 `TUI CI`（[run 39356795564](https://github.com/zy1233/pi-python/actions/runs/39356795564)，`7a4ce9d`）里，那个 job 还是 7 / 4 / 3，逐个 suite 的分布与修前相同，job 用时 22 m 08 s（#28 的三次运行：22 m 04 s、22 m 15 s、22 m 43 s）。原因：那个 job 每个 suite 一条 `cargo` 命令，每条命令按自己选的包统一 feature，所以 `pi-tools-api` 在用到它的 7 个 suite 里各编译一次，与 `protoc` 的路径无关。只有 `Gates + cargo check` job 的两条命令选的包相同：`pi-tools-api` 在那里由 2 次变成 1 次（job 3 m 17 s；#28 的三次运行 3 m 19 s、3 m 47 s、4 m 27 s）。所以这个修复的好处在开发者的机器上，不在 CI 的用时上。#30 的 PR 描述已改；CHANGELOG 与 §10.20 在 `e2de4c4` 里改了；r17 的两处（修订记录的行与计划里 r17 的段）在 #31 里加了更正的注。

**#31：`TUI CI` 与基线执行器改用 `cargo nextest`。** r17 把它当作要用户决定的一项（§10.20 的 `cargo nextest` 一段）：它让这一类失败不再出现，包括以后新写的测试。用户选了做。

- **改了什么。** `scripts/tui_baseline.py` 有了执行器的选项：清单的 `[test] runner = "nextest"`，命令行的 `--runner cargo|nextest` 盖过它；清单里没有这一项时仍用 `cargo`。nextest 的命令是 `cargo nextest run --locked --no-fail-fast -p <包> <目标> [-- --skip …]`（`--no-fail-fast`：nextest 缺省在第一个失败之后停）。结果取自它的人读输出：`Summary [..] N tests run: P passed, F failed, S skipped` 这一行；失败测试的名字只取 `Summary` 之后的汇总，因为失败测试捕获的输出里可能有形如状态行的文字。没有装 nextest 时，执行器停下，并说怎么装（或者用 `--runner cargo`）。报告里写「Tests (enforced, runner: nextest)」，列名是「Skipped」，工具版本里有 `cargo-nextest 0.9.148`。CI：`taiki-e/install-action@v2`，`tool: nextest@0.9.148`。钉住版本，因为脚本读它的输出；换版本要连解析器与它的测试一起改。job 改名 `cargo nextest (Linux x86_64)`：`main` 的规则集只有 `deletion` 与 `non_fast_forward`，没有必需的检查，所以改名不挡任何东西；旧文档里的旧名字留作历史。`AGENTS.md` 与 `scripts/tui_envspy/README.md` 各改了一句。
- **为什么。** 这个树里的测试是为 nextest 写的，不少测试的注释这么说：`pi-shell/src/config/tests.rs`（「reliable only under nextest's process-per-test isolation」）、`pi-pager-render/src/terminal/xtversion.rs`（设进程级的 `OnceLock`，「safe under nextest's process-per-test isolation」）、`pi-workspace`、`pi-telemetry`、`pi-update`。只有 `pi-fast-worktree` 的一处注释说它的测试也必须在 `cargo test` 下通过；基线不跑那个包（它的 lib 测试在 HEAD 上编译不过）。没有 `.config/nextest.toml`，也没有代码读 `NEXTEST` 环境变量。`in_own_process`（§10.20）在 nextest 下照常工作：子进程再用 `--exact` 跑同一个测试二进制。
- **「跳过」不是「忽略」。** nextest 把 `#[ignore]` 的测试与 `--skip` 去掉的测试合在一起算「跳过」。整个清单预计是 22：21 个忽略，加 `pi-acp-lib` 的清单里一个 `--skip`（`vendor_x_ai_ext_is_dropped_without_sending`）。`cargo test` 打印的是 21 个忽略。
- **文档测试。** r17 写过「nextest 不跑文档测试，要另外跑 `cargo test --doc`」，并把它算作代价（问用户的时候也这么说）。这个代价不存在：执行器给每个 suite 的是明确的 `--lib`、`--test`、`--bin` 目标，从来没有跑过文档测试。所以没有补 `--doc` 的步骤。
- **挂住的 suite。** nextest 把每个测试放在它自己的进程组里。执行器超时后原来向自己的进程组发 SIGKILL：nextest 被杀，挂住的测试进程还活着（实测）。现在先发 SIGTERM：nextest 收到后停掉它的测试，并以退出码 100 结束（实测）；20 秒之后还没有结束，再发 SIGKILL。3 个新的单元测试保着这一点（只在 POSIX 上跑）：SIGTERM 先发；子进程不理 SIGTERM 时 SIGKILL 接着发；正常结束时返回输出。
- **本地验证。** 单元测试（`scripts/tests/test_tui_baseline.py`，21 个新的）：Windows 50 通过 / 3 跳过（3 个只在 POSIX 上跑的信号测试），Linux（WSL2）53 通过。解析器的夹具是 nextest 0.9.148 在有失败的一次运行里打印的真实输出；还测了全通过、没有测试、没有 `Summary`、带颜色码、`exec failed` 与 `timed out`、各种状态标签。端到端在一个为此做的小 crate 上（不是 TUI 的树）：通过的一次、有失败的一次、`known_failures` 里的已知失败、没装 nextest（停下并给出装法）、挂住的测试（SIGTERM 清理）。`ruff check .` 与 `ruff format --check .` 通过。本地对比（WSL2，4 线程，r17 在 `aa76df4` 上量的）：`pi-pager` 的 lib，`cargo test` 29.7 s，nextest 79.5 s。
- **CI。** 在 `1a49eef`（#31 的第一次推送；代码提交 `14ffbdf`）上，`TUI CI` [run 39359879537](https://github.com/zy1233/pi-python/actions/runs/39359879537) 通过，`CI` [run 39359879347](https://github.com/zy1233/pi-python/actions/runs/39359879347) 通过（Ruff 9 s；`pytest` 3.11 / 3.12 / 3.13：2 m 18 s / 2 m 17 s / 2 m 20 s）。这是整棵树第一次在 nextest 下跑：**8,490 通过 / 0 失败 / 22 跳过**，与预计相同，每个 suite 等于它的 `reference_passed`：`pi-shell` 838（1 跳过）、`pi-pager` 5,651（12）、`pi-pager-render` 1,092（2）、`pi-pager-minimal` 79、`pi-pager-bin` 21、`pi-acp-lib` 21（1）、`pi-http` 13、`pi-telemetry` 239、`pi-file-utils` 216（6）、`pi-sampling-types` 320。日志里有 8,490 行 `PASS`，没有别的状态行（没有 `SLOW`、`LEAK`、`FLAKY`、`TIMEOUT`）；`python_agent_e2e` 的 14 个用例在 nextest 下通过（`PI_E2E_REQUIRED=1`）。工具版本：`cargo-nextest 0.9.148 (cd1d6d546 2026-10-07)`；`Install cargo-nextest` 一步 1 s。门禁：依赖图 970，0 条告警，`leader` 243，`x.ai` 297，`cargo check --workspace --tests` 0 个错误。**用时。**测试自己用的时间（nextest 的 `Summary`，各 suite 相加）约 130 s：`pi-shell` 10.6 s，`pi-pager` 85.2 s，`pi-pager-render` 8.9 s，`pi-file-utils` 20.5 s，其余 6 个 suite 合计约 5 s。同一份代码用 `cargo test`（#30 的 `e2de4c4`，各 suite 的 libtest `finished in` 相加）约 64.5 s（`pi-pager`：lib 15.97 s，`python_agent_e2e` 21.42 s，共约 37.4 s）。所以测试自己多约 66 s，其中 `pi-pager` 约 48 s。job 总用时 36 m 17 s（`Test suites` 一步 35 m 22 s），对同时开始的 `cargo test` job（`e2de4c4`，早 4 分钟开始）的 33 m 56 s；`Gates + cargo check` 4 m 28 s，`e2de4c4` 上是 4 m 36 s。`cargo test` job 的总用时在运行之间波动很大：这棵树上最近 7 次是 17 m 34 s 到 33 m 56 s（编译与缓存不同），所以总用时的差别不是可靠的度量，测试自己多的那约 66 s 才是。
- **没验证 / 留下的。** ① 整棵树在 nextest 下只在 CI 里跑过（第一次就是 #31 的 CI，通过）；本地没有在整棵树上跑过整套清单（r17 只跑了 `pi-pager` 与 `pi-shell` 的 lib）。② nextest 只管进程内共享的状态；测试共用的文件、端口与 `/tmp`，一个测试自己起的线程与子进程，它都不管。③ 本地的 `cargo test` 仍然会遇到 r17 的那类竞争；`in_own_process` 与测试里的修让它们少出现，没有让它们消失。④ #30 的 `-I<前缀>/include`：两个 job 都编译了 `pi-tools-api`，没有错误，但日志不打印 `protoc` 的参数，所以看不出这个旗标是不是真的传了。⑤ `release.yml`（#29）：限额问题是随机的（运行器共用出口地址），演练通过只说明工作流文件有效、令牌被接受，不证明限额问题已经解决；要等下一个 tag 才知道。⑥ 只在 Linux 上（CI 与 WSL2）；Windows 与 macOS 上没有跑 nextest。

## 附录 A（摘录）：A.4

附录 A 的 A.1–A.3（能力矩阵、ACP 版本配对、逐方法）是活的参考，留在[计划](../PLAN-RUST-AGENT-RUNTIME-REMOVAL.md)里。A.4 记 r5 的执行结果，搬到这里。

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
# 0.7 的读盘调用点（§10.11 的表；r13 之后期望 0 处，见 B.8）。r9 时期望 12 个文件：表里的 8 个，加上不读会话文件的 4 个——
# session_notification.rs 与 views/session_title.rs（只用清洗标题的纯函数）、test_util.rs 与
# disk_usage_cmd/tests.rs（测试代码）；pi-pager-bin 没有命中。
# 模式里用 . 代替引号：Windows PowerShell 5.1 会吃掉传给 rg 的内层引号。
rg -c 'persistence::|session::storage|resolve_local_session|list_summaries|local_summaries_for_cwd_sync|extract_first_user_prompt|join\(.sessions.\)' \
  tui/crates/codegen/pi-pager/src tui/crates/codegen/pi-pager-bin/src

# 0.7 的延迟（§10.11 (1) 的表是 --entries 120；每个会话约 1 MB 的那组用 --entries 2000 --sessions 100）。
# 「改前」的数：让 pi_agent_cli 解析到 d84d98a 的检出，其余相同。在会话所在的文件系统上跑
python scripts/session_list_bench.py --sessions 100 1000 --entries 120 --runs 3

# 13 例恢复矩阵（r9 时 9 PASS / 4 GAP；r13 落地 ADR1 之后 --strict 13/13，见 B.8）
python scripts/tui_pty/resume_matrix.py --zypi tui/target/debug/zypi --python .venv/bin/python [--strict]

# 权限模式（只记录，不判对错）。r9 时是 12 个场景（§10.11 的表）；r10 起是 10 个场景、4 个被拒绝的
# `--permission-mode` 值与设置页的检查（§10.13 的表）
python scripts/tui_pty/mode_probe.py --zypi tui/target/debug/zypi --python .venv/bin/python

# 0.3 的 e2e：先装 agent（与 tui-ci.yml 同一组包），再走基线执行器
pip install -e ".[dev]" -e ./packages/pi-agent-harness -e ./packages/pi-agent-cli \
  -e ./packages/pi-web-access -e ./packages/pi-goal-x -e ./packages/pi-dynamic-workflows
PI_E2E_PYTHON="$(command -v python)" PI_E2E_REQUIRED=1 python scripts/tui_baseline.py test --suite pi-pager
```

**B.5 r10 的复现**（ADR1 的 PR 1）

```bash
# 客户端选 id 的成本（§10.12 的表）：new / new id 是 session/new 不带 / 带一个没人用的 id；
# has_session / find 在进程内，问的都是一个没人用的 id。在会话所在的文件系统上跑
python scripts/session_list_bench.py --sessions 100 1000 5000 --runs 3

# 新增的 Python 测试（27 个）
python -m pytest packages/pi-agent-cli/tests/test_session_chosen_id.py \
  packages/pi-agent-cli/tests/test_acp_stdio_contract.py \
  packages/pi-agent-harness/tests/test_harness_session_page.py

# 恢复矩阵（r10：10 PASS / 3 GAP）
python scripts/tui_pty/resume_matrix.py --zypi tui/target/debug/zypi --python .venv/bin/python
```

**B.6 r10 的复现**（`mode_a`；Linux 或 WSL2，前提同 B.4）

```bash
# pi-pager 的整套测试（5,626；新增 24 个：agent_modes、cli、dispatch/tests/modes、settings_modal 与 settings_e2e）
python scripts/tui_baseline.py test --suite pi-pager

# 真 zypi 上的权限模式：10 个场景、4 个被拒绝的值、设置页（§10.13 的表）
python scripts/tui_pty/mode_probe.py --zypi tui/target/debug/zypi --python .venv/bin/python

# 单看被拒绝的值：退出码 2，消息里列出有效值（default、bypassPermissions）；在参数解析时就退出，不碰 home
tui/target/debug/zypi --permission-mode plan; echo "exit=$?"

# 新增的 PTY 脚本测试（Windows 上依赖 pty 的会跳过）
python -m pytest scripts/tests/test_tui_pty.py
```

**B.7 r11 的复现**（0.5 与阶段 C。调用图和 notices 在三个平台上都行；PTY 脚本与编译器核对要 Linux 或 WSL2，前提同 B.4）

B.1 与 B.2 的内联脚本由 `scripts/tui_callgraph.py` 取代。它们的期望输出是 r2、r3 时的树的数，现在的数不同；现在的数写在下面各行的注释里。

```bash
# 0.5：调用图（只读源码文本，不要 cargo）
python scripts/tui_callgraph.py crates                       # 78 个 crate，产品闭包 74 个，闭包外 4 个
python scripts/tui_callgraph.py crates --without pi-shell    # 63 个可达，只经 pi-shell 可达的 10 个
python scripts/tui_callgraph.py units --crate pi-hooks       # 13 个单元，5 个报死，4,339 行
python scripts/tui_callgraph.py units --crate pi-hooks --ban dispatcher    # what-if
python scripts/tui_callgraph.py keep --out docs/baselines/tui-support.md   # 重新生成保留清单
python -m pytest scripts/tests/test_tui_callgraph.py         # 39 个

# 0.5 的编译器核对（§10.14 的五轮）：在 ext4 上的副本里做，不要动工作树。删文件的脚本没有入库：
# 对 `units` 报死的每个单元，删它的文件（`a/b.rs` 或 `a/b/mod.rs`）和父模块里的 `mod b;` 行。
# 留下 pi-tools 的 types::error（第 5 轮什么也不留，会得到 5 个错误）
cp -r tui /tmp/prune-tui && cd /tmp/prune-tui
cargo check --locked --keep-going -p pi-pager-bin -p pi-pager-minimal -p pi-update
rm crates/codegen/pi-hooks/tests/integration.rs
cargo check --locked --keep-going --workspace --tests --exclude pi-fast-worktree

# C.1：网络审计（要 pyte、已构建的 zypi；--no-network 还要 bwrap）
python scripts/tui_pty/net_audit.py --zypi tui/target/debug/zypi --python .venv/bin/python --mock
python scripts/tui_pty/net_audit.py --zypi tui/target/debug/zypi --python .venv/bin/python --mock --no-network
# 真模型一次：key 只放在 [model] 表里 api_key_env 写的那个环境变量里，不要打印。
# 在 WSL 里用非登录的 bash，并用 WSLENV 传这个变量；登录 shell 的 profile 可能把它改掉
python scripts/tui_pty/net_audit.py --zypi tui/target/debug/zypi --python .venv/bin/python \
  --model-from ~/.pi-python/agent.toml

# C.1：状态行
python scripts/tui_pty/status_line_probe.py --zypi tui/target/debug/zypi --python .venv/bin/python

# C.3：THIRD-PARTY-NOTICES 与 Cargo.lock 对账（要 cargo）
python scripts/tui_notices.py check                # 期望：989 个包，989 个条目，缺 0、过期 0
python scripts/tui_notices.py update               # 看会改什么
python scripts/tui_notices.py update --write
python -m pytest scripts/tests/test_tui_notices.py # 62 个

# C.4：残留词（`gates` 要 cargo；pytest 里有同一个检查）
python scripts/tui_baseline.py gates
python -m pytest scripts/tests/test_tui_baseline.py scripts/tests/test_tui_pty.py
```

附录 A.2 里的版本号：Rust 一侧看 `tui/Cargo.toml` 与 `tui/Cargo.lock`（`agent-client-protocol`、`agent-client-protocol-schema`），Python 一侧看 `python -c "import acp, importlib.metadata as m; print(m.version('agent-client-protocol'), acp.PROTOCOL_VERSION)"` 与 `acp/meta.py` 头部的 `Schema ref`；SDK 的路由面（哪些方法有路由、哪些是 `unstable`）在 `acp/agent/router.py`。ACP 的版本协商与 v2 的变化来自 ACP 文档（经 Context7 查）。

**B.8 r13 的复现**（ADR1 的 PR 2 与 PR 3；Linux 或 WSL2，前提同 B.4）

```bash
# 读盘门禁：pager 与 pager-bin 的生产代码里读会话文件的调用，期望 0 处。
# 同一个扫描器指到 main 的 12349d3 是 18 处，指到 PR 2 的 506769b 是 17 处。
python scripts/tui_baseline.py gates
python -m pytest scripts/tests/test_tui_baseline.py -k session_disk_read

# 解析路径的单元测试（对脚本化的 agent）与对真 agent 的 e2e
cd tui
cargo test --locked -p pi-pager --lib -- session_resolve bounded_resolve session_title
PI_E2E_PYTHON="$(command -v python)" PI_E2E_REQUIRED=1 \
  cargo test --locked -p pi-pager --test python_agent_e2e
cd ..

# 13 例恢复矩阵：--strict 要 13/13
python scripts/tui_pty/resume_matrix.py --zypi tui/target/debug/zypi --python .venv/bin/python --strict

# 翻页的代价（`walk all` 一列）；在会话所在的文件系统上跑
python scripts/session_list_bench.py --sessions 100 1000 5000 --runs 3

# 支持 crate 的清单（PR 3 之后 session::persistence 的外部引用 12 → 3）
python scripts/tui_callgraph.py keep --out docs/baselines/tui-support.md
```

**B.9 r14 的复现**（决策 17；Python 的测试在三个平台上都行；PTY 与 e2e 要 Linux 或 WSL2，前提同 B.4）

```bash
# 存储层、agent、ACP 契约，与 PTY 脚本自己的测试
python -m pytest packages/pi-agent-harness/tests/test_deferred_session_storage.py \
  packages/pi-agent-cli/tests/test_session_chosen_id.py \
  packages/pi-agent-cli/tests/test_acp_stdio_contract.py \
  scripts/tests/test_tui_pty.py

# 对真 agent 的 e2e（14 个），与 16 例恢复矩阵（--strict 要 16/16）、烟测（23/23）
cd tui
PI_E2E_PYTHON="$(command -v python)" PI_E2E_REQUIRED=1 \
  cargo test --locked -p pi-pager --test python_agent_e2e
cd ..
python scripts/tui_pty/resume_matrix.py --zypi tui/target/debug/zypi --python .venv/bin/python --strict
python scripts/tui_pty/smoke.py --zypi tui/target/debug/zypi --python .venv/bin/python

# 变异检查：在克隆里做，做完用 `git checkout -- <文件>` 还原。
# - `packages/pi-agent-cli/pi_agent_cli/agent.py` 里 `session/new`（无 id）的那一行
#   `self._repo.create({"cwd": cwd, "deferred": True})`：去掉 `"deferred": True`，
#   再跑恢复矩阵：要 15 PASS / 1 FAIL（`/new`，不发消息）。
# - 同一个文件里带 id 的那一行（`_create_session_with_id` 里的
#   `{"cwd": cwd, "id": session_id, "deferred": True}`）：去掉 `"deferred": True`：要 13 PASS / 3 FAIL。
# - Python 一层的 15 个改动在 §10.17 的表里：每次改一处，再跑 `test_deferred_session_storage.py`、
#   `test_session_chosen_id.py`、`test_acp_stdio_contract.py`、`test_acp_agent.py`；每一处都要有测试失败。
```

**B.10 r15 的复现**（决策 10 的存储层一项；`check`、PTY 与测试要 Linux 或 WSL2，前提同 B.4；调用图与基线脚本的单测在三个平台上都行）

```bash
# 旧存储层没有了。读盘门禁、告警（0）、依赖图（980）、全工作区类型检查都在 check 里；期望退出码 0。
ls tui/crates/codegen/pi-shell/src/session/storage        # No such file or directory
python scripts/tui_baseline.py check

# 清洗标题的 8 个单元测试；pi-shell 的整个 suite（864 通过）；整套清单
# （8,513 通过 + 1 个已知失败，清单里的和是 8,514）
cd tui
cargo test --locked -p pi-shell --lib session::title
cd ..
python scripts/tui_baseline.py test --suite pi-shell
python scripts/tui_baseline.py test

# 规模：`c74cc18` 是 #25 的头，`5a39e09` 是这个 PR 的代码提交。
git diff --shortstat c74cc18 5a39e09 -- tui                # 52 files changed, 185 insertions(+), 14165 deletions(-)
git grep -c "^" 5a39e09 -- "tui/crates/*.rs"               # 逐文件的行数；加起来 1,007,878，共 1,723 个文件
git grep -c "^" c74cc18 -- "tui/crates/*.rs"               # 1,021,858，共 1,760 个文件

# 支持 crate 的清单：再生成一次，应当与提交的文件相同（`Commit:` 一行是生成时的 HEAD）。
python scripts/tui_callgraph.py keep --out docs/baselines/tui-support.md
git diff --stat -- docs/baselines/tui-support.md

# THIRD-PARTY-NOTICES 与 Cargo.lock（这个 PR 没有改 Cargo.lock）：989 个包，989 条，缺 0、旧 0
python scripts/tui_notices.py check

# 不再被 pi-shell 的源码点名的依赖：`c74cc18` 上 0 个，`5a39e09` 上 7 个。方法：对 `pi-shell/Cargo.toml` 的
# [dependencies]、[dev-dependencies]、[build-dependencies] 里的每一项，在 `src/`、`tests/`、`benches/`、
# `examples/` 的 `*.rs` 与 `build.rs` 里按整词找它的名字（`-` 换成 `_`）。这是文本搜索，宏与改名会骗过它。
```

*变异检查 `session::title`*：在 WSL 的克隆里一次改一处，跑 `cargo test --locked -p pi-shell --lib session::title`，记下哪些用例失败，再用 `git checkout -- tui/crates/codegen/pi-shell/src/session/title.rs` 还原。改的是什么，见 §10.18 的表。

**B.11 r16 的复现**（`check`、PTY 与测试要 Linux 或 WSL2，前提同 B.4；调用图、文本扫描与 `git` 的各条命令在三个平台上都行）

```bash
# 没人加载的文件：15 个。对每个模板，在 `7d82c27` 的整棵树里按去掉扩展名的文件名搜；
# 模板目录之外 0 处命中。`deep_research` 在 tui/ 里 0 处命中（Python 包里的同名字符串是另一个东西）。
git ls-tree --name-only 7d82c27 tui/crates/codegen/pi-shell/src/session/templates/
git grep -l -F goal_rules 7d82c27 -- . ":(exclude)tui/crates/codegen/pi-shell/src/session/templates"    # 空；对每个文件名各搜一次
git grep -n -F deep_research 7d82c27 -- tui ":(exclude)tui/crates/codegen/pi-shell/src/session/workflows/deep_research.rhai"    # 空
git log --format="%h %ad %s" --date=short -S"goal_rules.md" -- tui    # 455527c（#7）是最后一个改变它出现次数的提交

# 调用图报死的单元：期望 7 个，1,008 行（`7d82c27` 上是 9 个，1,388 行）
python scripts/tui_callgraph.py units --crate pi-shell --live pi-pager-bin pi-pager-minimal pi-update --format text

# 门禁：期望退出码 0；消费者构建 0 条告警，依赖图 970，`leader` 243 / `x.ai` 297，`--workspace --tests` 0 个错误
python scripts/tui_baseline.py check
python scripts/tui_notices.py check                       # 984 个包，984 条，缺 0、旧 0

# pi-shell 的整个 suite（838 通过）；整套清单（8,487 通过 + 1 个已知失败，清单里的和是 8,488）
python scripts/tui_baseline.py test --suite pi-shell
python scripts/tui_baseline.py test

# `[storage]` 的新测试
cd tui
cargo test --locked -p pi-shell --lib a_removed_storage_section_is_reported_as_unrecognized
cd ..

# 规模：`7d82c27` 是 #26 合入 `main` 的提交，`1a8abee` 是这个 PR 的最后一个代码提交。
git diff --shortstat 7d82c27 1a8abee -- tui                # 28 files changed, 13 insertions(+), 1644 deletions(-)
git grep -c "^" 7d82c27 -- "tui/crates/*.rs"               # 逐文件的行数；加起来 1,007,882，共 1,723 个文件
git grep -c "^" 1a8abee -- "tui/crates/*.rs"               # 1,007,386，共 1,718 个文件

# 支持 crate 的清单：再生成一次，应当与提交的文件相同（`Commit:` 一行是生成时的 HEAD）。
python scripts/tui_callgraph.py keep --out docs/baselines/tui-support.md
git diff --stat -- docs/baselines/tui-support.md

# 依赖图少了哪些包：在 `7d82c27` 与 `8b146e7` 的检出里各跑一次（只读元数据，不编译），再比较两份清单。
# 期望：只在旧的里有 10 个包（`pi-workflow` 和 9 个注册表的包），新的里没有新增。
cd tui
cargo tree --locked -p pi-pager-bin --target x86_64-unknown-linux-gnu --prefix none \
  | sed 's/ (\*)$//; s/ (proc-macro)$//' | sort -u > /tmp/graph-<rev>.txt
comm -23 /tmp/graph-old.txt /tmp/graph-new.txt

# 不再被 pi-shell 的源码点名的依赖：`7d82c27` 上 8 个，删掉两个死模块之后（`7ae0cc5`）10 个，`8b146e7` 上 0 个。
# 方法同 B.10，有一处不同：不算 `//` 注释里的出现（`base64` 只在一条注释里出现）。
# 文本搜索只给候选；每一个都由编译器核对：删掉之后 `check` 与 `cargo check --workspace --tests` 通过。

# 没人加载的文件的候选（只报告）：文件数与字节数；logo 只被 `include_str!` 点名两个。
git ls-tree -r -l HEAD -- tui/crates/codegen/pi-pager/tests/scenarios    # 43 个，70,628 B
git ls-tree -r -l HEAD -- tui/crates/codegen/pi-pager/npm                # 24 个，73,368 B
git grep -n "include_str!" -- tui/crates/codegen/pi-pager/src/views/welcome/logo.rs    # logo07.txt、logo05.txt
```

**B.12 r17 的复现**（要 Linux 或 WSL2；前提同 B.4；在 `tui/` 里跑。不要在清单之外启动 `zypi` 或测试二进制，除非先清掉 `PI_HOME` 与相关变量。整套构建的目录约 29 GB；WSL2 的虚拟磁盘文件只增不减，做完要删掉构建目录再压缩它，见 `scripts/tui_envspy/README.md` 的「Disk space」）

```bash
# 绝对路径的 PROTOC：不设的话，每次 cargo 调用都先编译 pi-tools-api 与它上面的 crate（见 §10.20 的 protoc 一节）
export PROTOC="$(command -v protoc)"

# 探针：自检，整套运行，报告。期望自检打印 SELFTEST OK；报告不判断，只列出测试。
../scripts/tui_envspy/envspy.sh selftest
../scripts/tui_envspy/envspy.sh run all cargo test --locked -p pi-shell -p pi-pager
../scripts/tui_envspy/envspy.sh report all > /tmp/envspy-all.txt
../scripts/tui_envspy/envspy.sh report all --key GROK_HOME --detail    # 一个变量，按调用栈
# 混沌模式（修之前没有找到失败）
../scripts/tui_envspy/envspy.sh chaos shell1 50 4 cargo test --locked -p pi-shell --lib

# 普查：格式 stress.sh <包> <遍数> <线程数> <随机顺序 0|1> [第一个种子] [-- 过滤器…]
../scripts/tui_envspy/stress.sh pi-pager 60 32 1 1      # 种子 1–60；修之前（种子 1–32）6/32；最后 0/60
../scripts/tui_envspy/stress.sh pi-pager 32 32 1 201    # 新种子；最后 0/32
../scripts/tui_envspy/stress.sh pi-shell 20 32 1 1      # 修之前 2/20，之后 0/20
../scripts/tui_envspy/stress.sh pi-pager 20 4 1 101     # CI 的线程数；修之前 0/20，aa76df4 上 1/20
../scripts/tui_envspy/stress.sh pi-shell 10 4 1 101     # 0/10
../scripts/tui_envspy/stress.sh pi-pager 10 32 0        # 固定顺序（CI 的顺序）
# 同一个种子重复；一个线程（用来区分「顺序问题」与「需要同时运行」）
for i in 1 2 3 4 5 6; do ../scripts/tui_envspy/stress.sh pi-pager 1 32 1 20; done    # 修之前 6/6
../scripts/tui_envspy/stress.sh pi-pager 1 1 1 20
# 经探针运行随机顺序（日志在 $ENVSPY_WORK/logs/<标签>/，每遍一个）
ENVSPY_TAG=shuf ../scripts/tui_envspy/stress.sh pi-pager 5 32 1 1
ENVSPY_TAG=shuf ../scripts/tui_envspy/stress.sh pi-shell 5 32 1 1
../scripts/tui_envspy/envspy.sh report shuf
# 其他 suite 的 lib（每个 10 遍）
for p in pi-pager-render pi-pager-minimal pi-acp-lib pi-http pi-telemetry pi-file-utils pi-sampling-types; do
  ../scripts/tui_envspy/stress.sh "$p" 10 32 1 1
done

# 配对（`--` 之后的词是过滤器；两个过滤器、两个线程；第一行应当是 `2 passed` 或 `1 passed; 1 failed`）。
# 格式：受害者 写者。名字顺序（第 4 个参数 0）与随机顺序（1）各一遍；再换成无关的测试做对照。
../scripts/tui_envspy/stress.sh pi-pager 100 2 0 1 -- \
  views::settings_modal::tests::rows_contain_categories_and_settings_through_pr_14 \
  views::settings_modal::tests::rebuild_rows_drops_voice_settings_when_gate_turns_off          # 族 5，修之前 100/100
../scripts/tui_envspy/stress.sh pi-pager 100 2 0 1 -- \
  app::mermaid_worker::tests::mermaid_view_pending_dedup_wins_over_disk_hit_race \
  app::dispatch::tests::status::dispatch_confirm_reset_setting_reset_dispatches_typed_setter_for_shared_enum   # 族 6，修之前 96/100
../scripts/tui_envspy/stress.sh pi-pager 100 2 0 1 -- \
  views::settings_modal::tests::rows_contain_categories_and_settings_through_pr_14 \
  views::modal_window::tests::centered_tip_footer_centers_and_clips                            # 对照 0/100
../scripts/tui_envspy/stress.sh pi-pager 200 2 0 1 -- \
  background_block_gutter_uses_block_background_fill thinking_body_dim_italic_survives_the_terminal_native_palette   # 族 1，修之前 200/200
../scripts/tui_envspy/stress.sh pi-pager 200 2 0 1 -- \
  ordinary_picker_keeps_tip_and_reset minimal_double_ctrl_c_arms_then_quits                                           # 族 2，修之前 200/200
# 人口（过滤器是测试全名的子串；一个模块路径选整个模块）：几个受害者 + 一组测试，32 线程，60 遍
../scripts/tui_envspy/stress.sh pi-pager 60 32 1 1 -- \
  rtl_overlay_slice_maps_painted_region_not_trimmed_text rtl_overlay_override_row_copies_whole_stored_text \
  rtl_override_row_copies_whole_stored_text scrollback::state::selection::tests::              # 族 3，修之前 52/60
../scripts/tui_envspy/stress.sh pi-pager 60 32 1 1 -- views::      # 修之前 14/60
../scripts/tui_envspy/stress.sh pi-pager 60 32 1 1 -- app::        # 修之前 9/60
../scripts/tui_envspy/stress.sh pi-pager 60 32 1 1 -- scrollback:: # 修之前 7/60
../scripts/tui_envspy/stress.sh pi-pager 60 32 1 1 -- ::           # 整个 lib（5,379 个测试，另 12 个忽略）

# cargo nextest（工具装在自己的目录，不动别的）
CARGO_TARGET_DIR=$HOME/pi-verify/tools-target cargo install cargo-nextest --locked --root $HOME/pi-verify/tools
cargo nextest run --locked -p pi-pager --lib --test-threads 4 --no-fail-fast       # 79.5 s，5,377 通过 / 12 跳过（aa76df4）
cargo nextest run --locked -p pi-pager --lib --test-threads 32 --no-fail-fast      # 10 遍：38.3–46.0 s
cargo nextest run --locked -p pi-shell --lib --test-threads 32 --no-fail-fast       # 5 遍：9.5–9.9 s，833 通过 / 1 跳过
(cd crates/codegen/pi-pager && cargo test --locked -p pi-pager --lib -- --test-threads 4)   # `finished in` 27.2、27.6、28.0 s（对照：aa76df4 上 28.8 s；墙钟 29.7 s）

# 整套清单（基线执行器；清环境）
python3 ../scripts/tui_baseline.py test                     # 8,490 通过 / 0 失败 / 21 忽略（`88e04e9` 上，约 4 m 56 s，增量编译）

# in_own_process 的反向检查：在一个一次性的克隆里，把 views/modal_window.rs 里
# embedded_fills_area_without_centering 的 `set_embedded(true);` 改成 `set_embedded(false);`，再跑它
cargo test --locked -p pi-pager --lib -- embedded_fills_area_without_centering
# 期望：测试失败，信息里有 `did not pass in its own process`、子进程的 `--- stdout ---` 与断言信息 `embedded fills the whole area`
git checkout -- crates/codegen/pi-pager/src/views/modal_window.rs   # 改回来

# 工具的 pytest（Windows 与 Linux 上都行；在仓库根目录）
python -m pytest scripts/tests/test_tui_envspy.py            # 10 通过

# CI 里一个 crate 被编译几次：数 job 日志里的 Compiling 行
gh run view 39193752451 --job 117871929387 --log > job.log   # `cargo test` 的 job
grep -cE 'Compiling\S* +\S*pi-tools-api ' job.log            # 7；pi-shell 4，pi-pager 3
gh run view 39193752451 --job 117871929167 --log > job2.log  # `Gates + cargo check` 的 job：2、2、0

# 规模
git diff --shortstat 739d2bb 6438b59 -- tui scripts      # 29 个文件，+2,358 / −357
```

*`fork` 风暴实验*：在一个临时分支（`tmp/fork-storm`，提交 `33c4efe`，不在任何 PR 里）把下面三个函数追加到 `pi-shell/src/auth/manager/lock_tests.rs` 的末尾（`cfg(unix)`），在 `pi-shell` 的 lib 测试二进制的包目录里（设 `CARGO_MANIFEST_DIR` 与 `CARGO_PKG_NAME`）运行：(a) `--test-threads 1 --nocapture zz_rounds`；(b) `--test-threads 4 --nocapture zz_rounds zz_fork_storm`；(c) `--test-threads 4 dropping_the_guard_silences zz_fork_storm`（真测试，要通过）。5 遍。

```rust
fn zz_round() -> bool {
    let dir = TempDir::new().unwrap();
    let lock_path = dir.path().join("auth.json.lock");
    let file = OpenOptions::new().read(true).write(true).create(true).truncate(true).open(&lock_path).unwrap();
    file.try_lock_exclusive().unwrap();
    let heartbeat = LockHeartbeat::spawn(file.try_clone().unwrap(), StdDuration::from_millis(1));
    drop(AuthFileLock { heartbeat: Some(heartbeat), file });
    let second = OpenOptions::new().read(true).write(true).create(true).truncate(true).open(&lock_path).unwrap();
    second.try_lock_exclusive().is_ok()
}

#[test]
fn zz_fork_storm() {
    let end = std::time::Instant::now() + StdDuration::from_secs(6);
    let mut forks = 0u32;
    while std::time::Instant::now() < end {
        let _ = std::process::Command::new("/bin/true").status();
        forks += 1;
    }
    eprintln!("ZZ forks {forks}");
}

#[test]
fn zz_rounds() {
    let rounds = 300;
    let busy = (0..rounds).filter(|_| !zz_round()).count();
    eprintln!("ZZ busy {busy} of {rounds}");
}
```

结果：(a) 5 遍都是 `busy 0 of 300`；(b) `busy` 65、72、83、68、75（共 363/1,500）；(c) 5 遍都通过。

## 附录 C：计划 §7 的逐项注释（r10 原文）

下面是计划 §7（分阶段计划）在 r10 时的全文，原样保留，含每一项的执行细节。计划里的 §7 现在每项只留一行状态。末尾是 §8 风险表里 R5 一行的 r10 原文。


依赖关系：`0 → {A, 1} → B → C →（可选）D`。A 与 1 互不依赖，可并行；B 依赖 A 和 1 中的 ADR1。每个阶段拆成若干 PR，每个 PR 独立可回滚；纯删除型 PR 以 `git revert` 回滚。

### 阶段 0：基线与门禁（先于任何删除，不改产品行为）

- [x] 0.1（r5 已做：Linux 上跑通且阻塞——`TUI CI` run [37379203134](https://github.com/zy1233/pi-python/actions/runs/37379203134) 全绿，门禁、消费者构建、`--workspace --tests`、8 个 suite；`enforce = true`、`enforce_workspace_tests = true`）**Rust CI job**：`cargo check -p pi-pager-bin` + 选定 crate 的 `cargo test`（Linux，ADR6）。（r4 已写好 `.github/workflows/tui-ci.yml`：`check`、`test`、手动的 `release-baseline` 三个 job，执行器是 `scripts/tui_baseline.py`，actionlint 与单测通过，本机 macOS 全部跑通；**尚未在 Linux 上运行**，所以不勾选，第一次运行之后的收尾步骤见 [`docs/baselines/tui.md`](../../baselines/tui.md) §5。）
- [x] 0.2（r5 已做：Linux 一栏的依赖图、告警、测试数已回填，告警清零；手动 run [37389455793](https://github.com/zy1233/pi-python/actions/runs/37389455793) 补齐了 release 数：`zypi` 422,034,120 B，比拆除前小 10.3 %，冷缓存构建 1,155 s，比 1,647 s 快 29.9 %，`--version` 中位数 23.1 ms）**基线报告**入库：测试通过 / 失败 / 忽略数、`cargo tree -p pi-pager-bin` 节点数、release 二进制体积、冷编译与启动耗时、去掉全局 allow 后的告警数。（r4 已入库 [`docs/baselines/tui.md`](../../baselines/tui.md)：拆除前（v0.4.0）与拆除后（macOS）的依赖图节点数、告警数、8 个 suite 的测试数、v0.4.0 的 release 体积与构建耗时；**Linux 一栏、拆除后的 release 体积 / 冷编译耗时 / `--version` 延迟在 r5 回填**，「到欢迎页」的启动耗时需要 PTY 载体，未测。「去掉全局 allow 后的告警数」随 A.4 做。）
- [x] 0.3（r5：Python 侧的 stdio 契约套件，见 1.P7；**r9：Rust 一侧做完**——`tui/crates/codegen/pi-pager/tests/python_agent_e2e.rs`，10 个用例，接进 `tui-ci.yml`，见 §10.11）**ACP 契约 / e2e 载体**：Rust client ↔ Python mock agent（脚本化 `stream_fn` 的 `PiAcpAgent` stdio 进程），覆盖 initialize、new、prompt 流式、权限往返、cancel、list、load、Python 缺失时报错、子进程退出；不依赖 PTY（PTY 烟测可选）。
- [x] 0.4（r4 已做）**deny-list**：硬性 `pi-sampler`；条件性 `async-openai`、`pi-sampling-types`（由阶段 D 决定）；对 `pi-tools`、`pi-agent`、`pi-workspace`、`pi-hooks`、`pi-mcp` 逐个写「保留理由或缩减方案」。（`pi-sampler` 及 `MvpAgent` / `acp_session_impl` / `SamplerActor` 三个词是 `tui_baseline.py gates` 的阻塞门禁；七个条件性 crate 的直接依赖者、理由与缩减方案见 [`docs/baselines/tui.md`](../../baselines/tui.md) §4，依赖者列表每次 `check` 重新生成。）
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
- [ ] 1.P3 `set_session_mode`（或 `configOptions` 的 `mode`）取代仅靠 `ext_notification` 的权限模式切换，映射由矩阵定。（r10：先做了不碰 agent 的 (a)——pager 只提供 agent 真正会做的两个模式，见 §10.13；`set_session_mode` 本身没做，所以不勾。）
- [x] 1.P4（r9，`935e4b8`、`7becb49`、`9798e1d`）`mcp_servers`：**显式忽略并在文档声明**（Phase 4 非目标），或实现。做了前者：pager 不再发（`session/new` / `load` 的 `mcp_servers` 恒为空，有单测）；Python 收到非空列表时记一条警告并发一条只含名字的 agent 消息（`mcp_notice.py`、`tests/test_mcp_notice.py`）；`packages/pi-agent-cli/AGENTS.md` 的「MCP servers」与 `docs/TUI-AND-CODE-AGENT.md` 写明这是对 ACP「agent 必须支持 stdio 服务器」的有意偏离。`pi_shell::util::config::load_mcp_servers` 与 `mcp.rs` 现在没有调用者（清理候选，见 §10.11）。
- [ ] 1.P5（r5 部分：`initialize` 公布了 `session/close` 与 `session/resume`，但 `run_agent` 没开 `use_unstable_protocol`，SDK 对这两个方法回「Method not found」，`5163592` 已改；其余公布项与 pager 需求的对照没做）`initialize` 的 capabilities / `_meta`：只公布 pager 实际需要且已登记（P2）的键。
- [ ] 1.P6 `-p` 旗标契约：以 `__main__` 接受的旗标为权威；每个 `PagerArgs` 旗标逐项「支持｜非 0 退出并报错｜文档删除」。
- [x] 1.P7（**r9：权限往返与 cancel 的线上用例补上了**——`28fed94`：`tests/test_acp_stdio_tools.py`，用 `_tool_agent.py` 让一次脚本化的 tool call 走完真实 stdio，Rust 一侧的同类用例见 0.3；r5 部分：`packages/pi-agent-cli/tests/test_acp_stdio_contract.py` 起子进程走 stdio，8 个用例——initialize、new、流式 prompt、`model` 配置项、重启后 list / load 回放 / resume、close、未知方法与连接存活、stdin EOF 退出码 0；`PI_ACP_CONTRACT_COMMAND` 可对准别的 stdio ACP agent。当时没有权限往返与 cancel 的线上用例，因为 mock 流不会产生 tool call，r9 的 `_tool_agent.py` 补上了）契约测试：mock `stream_fn` 驱动 ACP stdio，断言方法面（含既有的「不注册 `x.ai/`」）。

Rust（pager）：

- [ ] 1.R1（r9：读盘调用点清单、落地设计、PR 切分、验收矩阵与延迟实测都在 §10.11；**r10 只做了 PR 1（Python 一侧：`_meta.sessionId`、`pi/cwd`，见 §10.12，PR #11，分支 `codex/adr1-pr1-python`）**；PR 2、PR 3 没有动手——沙箱顺序、`export` 的去向要用户确认，见 §10.11 末尾）ADR1 落地：移除对 session 文件的读取；`export` 的去向。
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

### §8 风险表：R5 一行（r10 原文）

| # | 风险 | 处理方式 |
|---|---|---|
| R5 | 子进程生命周期：孤儿 bash 进程、Windows 行为未知 | 阶段 1 的优雅退出协议 + 实测；Windows 先记录现状。r5：macOS 上已实测并修掉退出 zypi 后残留的 bash 进程（§10.7）；zypi 不是会话首进程时 `kill -9` 的端到端情形与 Windows 当时仍未测。r8：Linux（WSL2）上两种启动方式 × 六种退出方式都实测了，含沙箱下，并补上沙箱 re-exec 的 `--die-with-parent`（§10.10）；`-p` 模式（只杀 zypi 时 agent 子进程残留）随后也修了：Python 一侧监视父进程（§10.10 发现 6）；Windows 仍未处理 |

## 附录 D：计划头部的状态文字（r10 原文）

下面是计划头部在 r10 时的四段文字（状态、基线、验证方式、与既有文档的关系），原样保留。其中「与既有文档的关系」仍在计划头部。

> 状态：r10。r2 的计划稿之上，**已拆除 Rust runtime 并恢复 `/model`（r3）；r4 补了阶段 0 的 Linux CI 与基线工具、清掉 leader 的 UI 状态残留、完成 1.R3 的命名；r5 在 Linux CI 上跑通并启用了基线，执行入口审计（0.6）并完成阶段 A（A.3、A.4）——又删了约 23 万行 Rust、8 个 crate，消费者构建 0 告警**。分支已 push，PR [#7](https://github.com/zy1233/pi-python/pull/7) 已转 ready for review；**r6 清理了品牌残留（把还写着 Grok、或指向不存在的命令 / 目录的提示改成真的东西，见 §10.8），在叠加分支 `codex/branding-cleanup` 上；r7 合并了 `main` 上另一台机器推的 7 个提交，6 个文件的冲突已解，并修了 macOS 上 `AGENTS.md` 被读两遍的 bug（见 §10.9）**；**r8 在 WSL2 / Linux 上补测：退出行为（含沙箱下）、0.9 的真机检查、CI 没跑的 Rust 测试，并修了沙箱 re-exec 缺 `--die-with-parent`、会在 shell 里留下整棵进程树的问题；之后又按用户的选择做了六件事：`-p` 只杀 zypi 时 agent 不再残留、没生效的沙箱现在在屏幕上说一声、agent 的 `stderr` 落到日志文件、CI 清单加了两个 suite 并去掉 `doctor_cmd::` 的 `skip`、PTY 驱动脚本入库、清理 WSL（见 §10.10）**；**r9 在 PR #9 之上补阶段 0 的收尾与阶段 1 里不需要拍板的部分（用户选了 e2e、阶段 0 的门、阶段 1 的免决策项、盯 PR #9 的 CI，没有选「替我拍板」）：0.3 的 Rust 一侧（pager 的 ACP client 对着真的 Python agent 的 10 个 e2e 用例，接进 `tui-ci.yml`；它第一次跑就抓出两个产品 bug——agent 起不来时的原因被吞成「channel closed」、agent 一启动就退出时 `connect` 挂住——都修了）、0.7（会话列表与 `session/load` 的延迟实测并修掉读盘的慢点、pager 一侧的读盘调用点清单、13 个旗标 / 子命令的 PTY 验收矩阵、ADR1 的落地设计）、0.8（附录 A 定稿，新增 ACP 版本配对表）、1.P1（pager 跟 `nextCursor`，Python 分页）、1.P4（显式忽略 `mcp_servers` 并说一声）、1.P7 的余项；每一项会改变产品行为的决定（沙箱顺序、`--session-id`、`export`、模式映射……）都只写成提议，见 §10.11 末尾「留给用户决定的」**；**r10 把 r9 的分支推成 PR [#10](https://github.com/zy1233/pi-python/pull/10)，并按用户的选择在它之上做了 ADR1 的 PR 1——Python 兑现 `_meta.sessionId`、已用的 id 以 `invalid_params` 拒绝、`session/new` / `load` / `resume` 的响应带 `pi/cwd`（PR [#11](https://github.com/zy1233/pi-python/pull/11)，分支 `codex/adr1-pr1-python`，见 §10.12）；PR #10 的 CI 全绿；之后做了 1.P3 的 (a)：Shift+Tab 只剩 Normal ↔ Always-Approve，设置页不再有 Plan / Auto / Default，`--permission-mode` 只收 `default` 与 `bypassPermissions`（PR [#12](https://github.com/zy1233/pi-python/pull/12)，分支 `codex/phase1-mode-a`，见 §10.13）**；执行记录见 §10。§1–§9 保留 r2 原文作为决策依据，与 §10 冲突处以 §10 和 ADR3（r3）为准。负责人：待指派。
> 基线：分支 `codex/rust-agent-runtime-removal-plan`；r2 / r3 的比较基线是 HEAD `07a3574`，r4 / r5 的提交见 §10。
> 验证方式：r2 为静态分析（源码阅读、`Cargo.toml` 解析、模块级引用统计，复现方法见附录 B）；r3 起拆除结果由 `cargo check`（macOS 本机，消费者构建 `pi-pager-bin` / `pi-pager-minimal` / `pi-update` 及 `pi-shell` / `pi-pager` 的 `--tests` 类型检查）、`cargo test`（触及的 crate）、Python 侧 pytest，以及伪终端里 `zypi` ↔ `pi_agent_cli` ↔ OpenRouter 的真实会话（含 `/model` 切换）验证。标「需实测」的其余结论仍是静态推断；Linux / Windows 的 `cfg` 代码本机无法编译，见 §10.4。
> 与既有文档的关系：承接 [Phase 4 设计](../../specs/2026-08-25-phase4-coding-agent-cli-design.md) §3「第一轮允许 pager 继续链接 `xai-grok-shell`……变瘦不是迁入前提」和 [`AUDIT-PHASE4-PHASE5.md`](../../AUDIT/AUDIT-PHASE4-PHASE5.md) 的「TUI 瘦身长期里程碑」，是 Phase 4 的第二轮。与既有决定的张力见 §3。

## 附录 E：计划 §4、§5（r2 的基线，原文）

下面是计划 §4（源码事实基线）与 §5（改动规模）在 r10 时的全文，原样保留。它们写于 r2，拆除之前，描述的是当时的代码，不是现状；与现状冲突处，以本文的 §10 为准（拆除后的规模见 §10.1，残留见 §10.4）。两个节标题降了一级（`##` → `###`），正文没有改。计划里的 §4、§5 现在只剩一个索引。

### 4. 源码事实基线

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

### 5. 改动规模

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
- r10：r9 的分支在 r10 开头推成了 PR [#10](https://github.com/zy1233/pi-python/pull/10)（上面写的「没有推送」是 r9 当时的状态）；在它之上按用户的选择（`push_pr`、`adr1_pr1`、`mode_a`）做了两件事，各在一个分支上，做完时都只在本地。① `adr1_pr1`：ADR1 的 PR 1，分支 `codex/adr1-pr1-python`（PR #11）——Python 兑现 `session/new` 的 `_meta.sessionId`（规范成小写带连字符的 UUID；别的值与已用的 id 都以 `invalid_params` 拒绝；同一个 id 的并发请求由进程内的锁串行），`session/new` / `load` / `resume` 的响应带 `_meta["pi/cwd"]`（保存的工作目录压过客户端传的）；harness 新增 `JsonlSessionRepo.has_session`——第一版用 `find` 检查「没人用的 id」，5,000 个会话时要 1.7 s，改成只打开名字对得上的文件后约 0.3 s，剩下的成本是 `list_dir` 给每个条目造 `FileInfo`（新提议 13，没有动）。27 个新测试、11 个变异检查全部被抓住；恢复矩阵由 9 PASS / 4 GAP 变成 10 / 3。验证：Python 全量 pytest（Windows 2,378 通过、Linux 2,379 通过）、Ruff。没做：pager 不读 `pi/cwd`、不预检 `--session-id` 的重复（PR 2）；两个 agent 进程共用一个 home 时的并发。见 §10.12。② `mode_a`：1.P3 的 (a)，分支 `codex/phase1-mode-a`（叠在 `codex/adr1-pr1-python` 之上，PR #12）——`zypi` 只提供 Python agent 真会照做的权限模式。Plan（不限制任何东西）、Auto（等于 Always-Approve，界面却承诺分类器）、设置页的 Default（被 Python 丢掉）藏在 `app/agent_modes.rs` 的三个常量后面：Shift+Tab 只在 Normal ↔ Always-Approve 之间切；设置页没有 Plan mode 行，权限选择器只剩 Ask / Always approve；存着的 `auto` / `default` 显示成 Ask（文件不改）；`--permission-mode` 只收 `default` 与 `bypassPermissions`，另外四个值以退出码 2 拒绝并说明原因。Python 一侧没动；被藏起来的代码与旧测试还在（夹具把门打开），翻一个常量就能让某个模式带着自己的测试回来。24 个新 Rust 测试（`pi-pager` 5,602 → 5,626，10 个 suite 合计 8,683 通过 / 0 失败 / 22 忽略）；12 个变异检查全部被抓住（其中一个 e2e 用例原来看不见选择器里排在 Ask 前面的 Default，补了 Up；第一个 M5 的写法让 rustc 1.94.0 崩了，换了写法）；真 zypi 上的 `mode_probe.py` 改成 10 个场景加 4 个被拒绝的值与设置页的检查，结果都如预期。验证：Python 全量 pytest（Windows 2,382 通过、Linux 2,383 通过）、Ruff（0.16.10 与 CI 钉的 0.16.0）。这一轮开头 PR #10 的 CI 也跑完了，证实了 r9 留下的数（见 §10.12）。没做：Python 一侧的 `auto` 怎么定义、真的 Plan（决定 7 的 (b)、(c)）；设置页里其余可能无效的权限项（候选 14）；内嵌用户指南与 `--no-plan` 里关于 Plan 的旧说明；macOS 与 Windows 上的 zypi。见 §10.13。③ 收尾：用户选了 `push_stack`，并要求「同步 main 到 origin，然后合并 3 个处理过的 PR」。栈里实际有四个 PR——[#9](https://github.com/zy1233/pi-python/pull/9)、[#10](https://github.com/zy1233/pi-python/pull/10) 和这两个新开的 [#11](https://github.com/zy1233/pi-python/pull/11)、[#12](https://github.com/zy1233/pi-python/pull/12)，下面的 PR 不合并，上面的就没有可合并的基础，所以按四个处理：自下而上逐个 squash 合并（#7、#8 的惯例），每个合并前先把 base 改成 `main`，合并后核对 `main` 的树与该分支的树相同。本地 `main` 比 `origin/main` 多的 9 个提交，除一个已被 #9 里修订过的文档取代的之外都已在 #9 的分支里，所以没有直接推 `main`；合并完把本地 `main` 对齐 `origin/main`，旧的 `main` 留在一个备份分支上。实际过程：#9 先合并（`60033a4`）。#10 随后冲突了——#9 是 squash，#10 的分支里还是 #9 原来的提交，两边在相近的行上都改过（改法相同），git 算作冲突。解法是在 #10 的分支里合并一次 `main`（`-s ours`：`main` 的树就是 #9 最后一个提交的树，没有什么是分支没有的，分支的树不变），再 squash 合并（`1dcfcaf`）。#11、#12 每个都在前一个合并之后 `rebase --onto origin/main <原来的 base 提交>`（树不变）、改 base 为 `main`、再 squash 合并（#11 是 `e60ae25`）。每次合并后都核对了 `main` 的树与该分支的树相同。教训：叠起来的 PR 用 squash 合并时，下一个 PR 要先对齐 `main`，直接改 base 会冲突（用「合并提交」合并下面的 PR 则不会，因为提交的身份保留了）。CI：#10（合并 `main` 之后的 `b0d001b`）、#11、#12 在各自推送时的提交上都是全绿，#12 的数与本地一致（`docs/baselines/tui.md`）；重新对齐之后的提交树与已经绿了的相同，没有再等它们的 CI，合并后 `main` 上的那次运行是最后的核对。
- 整理（r10 之后，2026-10-11）：计划与执行记录分开存放。本文件收下计划里的 §10 执行记录、附录 A.4、附录 B 和这份修订记录；计划 §7 的逐项注释与 §8 的 R5 一行、计划头部的状态文字，原样存入附录 C、附录 D；计划 §4、§5（r2 的源码事实基线与改动规模估计）原样存入附录 E。计划里的 §7 改成每项一行状态，§4、§5 只留索引，另加「当前状态」「待决策」和 §10 索引。只搬，没有删。
- r11：0.5 与阶段 C，分支 `chore/phase-c-closeout`（叠在本地 `main` 的 `df812d1` 上，**没有推送**）。用户说这些「不用等决定」，并说本机配了真实的 LLM，需要时可以用。做了五件事。① 0.5：`scripts/tui_callgraph.py`（`crates`、`units`、`keep`）与生成的保留清单 `docs/baselines/tui-support.md`：support 的 7 个 crate 共 305,581 行，17 个单元（6,190 行）没有活代码引用，`pi-hooks` 一家占 4,339 行。准确度用编译器查了五轮：第一版报了 21 个，其中 4 个是活的（路径写在属性的字符串里、路径以子模块起头），按反馈改了工具；改完之后，把其中 16 个的文件在副本里删掉，三个产品 crate 的 `cargo check` 与 `--workspace --tests` 都通过；第 17 个（`pi-tools` 的 `types::error`，里面是 `?` 要用的 `impl From<…> for ToolError`）不能删，工具把它标在「check first」里。② C.1：`net_audit.py`——一轮对话期间 `zypi` 没有到非本机地址的连接，也不监听（mock 13/13；进程树放进空的网络命名空间 13/13；真模型一次 13/13，含 agent 有外连的正向对照）；`status_line_probe.py`——`[ui.status_line]` 在 Python agent 下画不出（4/4），因为 agent 不发 `session_status`（新的待决策 15）。③ C.2：内嵌用户指南从 33 个文件缩成 7 页加 README，删教程叠层，启动不再解压（`pi-pager` 5,626 → 5,608）。④ C.3：`tui_notices.py` 把 `THIRD-PARTY-NOTICES` 与 `Cargo.lock` 对账（去掉 222 条、补上 44 条，现在 989 对 989），`NOTICE` 重写。⑤ C.4：`config.toml` 的约束、TUI 指南、Phase 4 spec、DESIGN §8.5；`grok agent` 清零；`leader`（245）与 `x.ai`（304）清不到零，改成棘轮上限。新增阶段 D 的 D.5–D.9。验证：10 个 suite 8,665 通过 / 0 失败 / 22 忽略（WSL2，一次本地运行）、`check`（5 m 24 s）、Python 全量 pytest（Windows 2,510、Linux 2,538 通过）、Ruff。没做：本分支的 CI 运行；macOS 与 Windows 上的审计；16 个可删单元的真正删除；`THIRD-PARTY-NOTICES` 措辞的人工阅读。见 §10.14。
- r12：V0.5 的发布里程碑，分支 `docs/v0.5-milestone`（叠在 `main` 的 `4fc028a` 上）。用户说 PR #13 已合入、CI 没有问题，让按当前的剥离状态设置发布 V0.5 的里程碑。只改文档，另在 GitHub 上建了里程碑。做了：① 建 GitHub 里程碑 [v0.5.0](https://github.com/zy1233/pi-python/milestone/1)（没有挂 PR 与 issue）；② 计划里新加「发布里程碑 V0.5」：范围按现状定（阶段 0、A、C 与阶段 B 的删除完成，阶段 1 完成一半，阶段 D 没有开始；不等阶段 1 的余项、ADR1 的 PR 2 与 PR 3、阶段 D），六项退出条件（CI、release 体积再量、发布构建演练、与 v0.4.0 对照、发布说明、打 tag），带着发布的已知问题，不在 V0.5 的项；③ 新增待决策 16：`zypi --version` 打印继承来的 `1.0.8`，而同一个环境变量 `GROK_VERSION` 还决定「是不是发行构建」，发行二进制一直是开发构建（文件夹信任惰性），所以不能靠在 `release.yml` 里盖戳来改版本号；④ 状态更新到 `main` 的 `4fc028a`（`CI` 与 `TUI CI` 都通过，8,665 / 0 / 22）。还查到：`CHANGELOG.md` 里没有 Rust runtime 的删除、入口的删除、`/model` 的恢复、`-p` 拒绝沙箱、品牌清理的条目（发布说明只取 `[0.5.0]` 一段）；macOS arm64 的发布 job 从来没有跑过。里程碑建好之后，用户选了四件事，都在别的 PR 或 CI 运行里做了，本 PR 只记录：并入 `[Unreleased]` 并补条目（PR #15，条件 5）；给 `release.yml` 加手动演练（PR #14，条件 3），并从分支跑了一次（run 38223353755：Linux 与 macOS arm64 都构建成功，什么也没发布）；`release_baseline`（run 38221952524：420,767,096 B，比 v0.4.0 小 10.6 %，条件 2）；推送本分支并开 PR。决策 16 选了 (a)；之后用户又答了三个问题（§10.15 续 2）：决策 16 现在做，另开 PR #17（叠在 #15 上）；macOS 二进制的条目写进 #15；条件 4 不做，V0.5 不等它。续 3：#15、#17、#14 合入（`59545c1`、`d57fd9b`、`0efd400`）；WSL 修好后在 `0efd400` 上跑了 Linux 的 `pytest`、`zypi --version` 与 PTY 脚本（`smoke.py` 22/23，其余符合预期）。续 4：`smoke.py` 过期的那项检查修好了（用户说不另开 PR，直接提交到 `main`）：`smoke.py` 23/23，加 `--sandbox workspace` 26/26；里程碑的描述改到合入之后；删了五个本地旧分支。续 5：用户答了决策 6、8、14、15：6 与 15 选 (a)（都还没做），8 与 14 待定；又删了最后一个本地旧分支。续 6：决策 6 的 (a) 做成了 PR（分支 `feat/export-via-acp-replay`，没有合入）：`zypi export <id>` 改问 agent，经 `session/load` 回放；18 个单元测试、一个端到端测试；`resume_matrix.py` 11 PASS / 2 GAP / 0 FAIL；调试构建下，2,000、5,000、20,000 条的会话要 2.5、5.7、63 s；发现三处没有修的问题（agent 读到条目 id 重复的文件会死循环；基准脚本在 4,096 条之后写出这样的文件；`isReplay` 在 agent 与 pager 里放的 `_meta` 层级不同）。续 7：决策 15 复核后从 (a) 改成 (b)，做成了 PR（分支 `feat/status-line-local-facts`，没有合入）：DESIGN §8.5 的五条里，(a) 有两条不过（没有标准对应物；线上类型要放在旧运行时的 crate 里），用户改选 (b)；`zypi` 用已有的数据（目录、会话 id、模型、会话名、版本，加两个钟）画状态行，agent 发来的状态仍然优先；`context` 与 `cost` 不造数；Python 与线上协议都没动；新增 16 个单元测试、重写 2 个、调整 1 个；`status_line_probe.py` 4/4（r11 时 4 个都画不出）；整套运行时有一个与这次无关的失败，原因是本机真实 home 里一个没跑完的运行留下的目录（见 §10.15 续 7）。续 8：用户让把 #18、#19 合入 `main`、放进 V0.5，不再重复本地测试，再打 tag 发布（这条改了先前的两条约定：「PR 只开不合」与「不自己打 tag」）。#18 先 squash 合入（`ec7d455`）；#19 与它在三个文档文件上冲突，在 #19 的分支里合入 `main` 解决（`1fa18cf`，代码没有冲突）。`tui/` 又变了，所以条件 2、3 在 `1fa18cf` 上重做（[release_baseline](https://github.com/zy1233/pi-python/actions/runs/38353042677)、[发布演练](https://github.com/zy1233/pi-python/actions/runs/38353038777)）。`CHANGELOG.md` 把两条并进 `[0.5.0]`，日期改成 2026-10-13，删去 `zypi export` 的已知问题，改窄 `[ui.status_line]` 的。这些文档改动在两个只改文档的 PR 里合入（#20；#21 又改了状态行探针的条目）。`v0.5.0` 打在 `be3f8a8`，`Release` 发布了 PyPI 的六个包与 GitHub Release（macOS 的构建因 `setup-protoc` 被 GitHub 的 API 限流，失败两次，第 3 次通过）；发布的 Linux 二进制下载后跑过 PTY 脚本。没做：本地的 pytest 与 cargo 测试（用户说不用）；macOS 的二进制没有人运行过。见 §10.15。
- r13：ADR1 落地（2026-10-16）。用户说「完成 ADR1 的落地，决策 2、4、5、7 按你的倾向来，我看过比较合适」。决策 10（删 `pi-shell` 里的旧存储层）不在答复里，没动。做成两个 PR，都没有合入：分支 `feat/adr1-resolve-startup`（PR [#23](https://github.com/zy1233/pi-python/pull/23)，基 `main` 的 `12349d3`）与 `refactor/adr1-drop-session-disk-reads`（PR [#24](https://github.com/zy1233/pi-python/pull/24)，叠在前一个上）。PR 2：agent 起来之后经 `session/list`、`session/load` 解析 `--continue`、`--resume`、`--session-id`（`resolve_startup`），一个共用的翻页器（选择器也用），30 s 的上限，解析失败时还原终端、停 agent、退出 1；`--continue` 取最近活动（决策 4），未知 id 在进 TUI 之前报错（决策 5）；新增 32 个测试，`resume_matrix.py --strict` 13/13；CI（`506769b`）8,732 通过 / 0 失败 / 22 忽略。PR 3：删 pager 里读会话文件、钉沙箱（决策 2）、读 `plan.md`（决策 7）的代码，Rust 加 149 行、删 1,829 行，净少 27 个测试（26 通过、1 忽略）；加门禁 `gates.session_disk_reads`（`main` 上 18 处命中，现在 0 处）；用户指南 `17-sessions.md`、`CHANGELOG.md`、`packages/pi-agent-cli/AGENTS.md`、基线与计划同步。验证（WSL2）：`check` 告警 0；整套清单 8,705 通过 / 1 个已知失败（`pi-pager-render` 的 tmux 探测）/ 21 忽略；`resume_matrix.py --strict` 13/13，其余 PTY 脚本全过；完整 `pytest`：Windows 2,531 通过，Linux 2,540 通过。新发现：翻页的代价增长得比会话数快（每页都重新列目录），5,000 个会话时不带 `cwd` 翻完要 28–34 s，在 Windows 上超过 30 s 的上限；1,000 个会话以内在 2 s 以内。新增待决策 17（空会话：Python 在 `session/new` 时就建文件，`--continue` 可能取到标题为 `(no messages)` 的空会话）与 18（翻页的代价；一个没有提交的草稿让 5,000 个会话的翻完从 33.9 s 降到 3.6 s）。没做：决策 10；Python 一侧的延迟建文件与翻页提速。见 §10.16。
- r14：空会话（2026-10-19）。用户在 2026-10-18 答复了四个问题：合并、决策 18、决策 17、决策 10。① 合入：#23（`7e0ca5a`）与 #24（基改成 `main`、把 `main` 合进它的分支之后，`0476ea4`）；`CI` 与 `TUI CI` 在两个提交上都通过。② 决策 18：不做；计划里记为「已定，不做」，`CHANGELOG.md` 写了 5,000 个会话时的数。③ 决策 17：做成 PR [#25](https://github.com/zy1233/pi-python/pull/25)（分支 `fix/write-session-at-first-message`，基 `main`，没有合入）。agent 在 `session/new` 时只在内存里建会话（`DeferredJsonlSessionStorage`）；第一条用户消息才把文件头、之前攒下的条目和这条消息一次写下去。没发过消息的会话没有文件，不在列表里，`session/load` 与 `session/resume` 答 `resource_not_found`，所以 `zypi --continue` 取不到它。客户端指定的 id 由 agent 记到会话关闭（`_reserved_ids`）；另一个进程先存了同一个 id 时，这个会话的第一次 `prompt` 被拒绝（缩小了两个 agent 进程之间的空隙，没有堵死）。pager 的生产代码没有改。新测试：Python 16 个（存储 10、agent 6）与 `scripts/tests` 的 12 个；PTY 恢复矩阵 13 → 16 例（`--session-id` 与 `/new` 各一个「不发消息、没有文件」的用例，加一个「`/new` 之后发消息」的用例），用例可以分步输入；`smoke.py` 不再按 ↓；`python_agent_e2e.rs` 的用例先发一条消息再找会话（仍是 14 个）。变异检查：PTY 一层的第一版（14 例）看不见「立即写」的变异（不带旗标的 `zypi` 在第一条消息之前不发 `session/new`），改成 `--session-id` 与 `/new` 之后，两个单行变异分别被 1 个与 3 个用例抓住；Python 一层 15 个改动，第一次跑 13 个被抓住，2 个（H5、A7）补测试之后也被抓住。验证：Python 全量 pytest（Windows 2,559、Linux 2,568 通过）、`python_agent_e2e` 14/14、PTY 脚本全过（`resume_matrix.py --strict` 16/16）、Ruff。本地运行留在 WSL `/tmp` 的残留（29 项，含 r13 留下的 20 个）已删，`~/.pi-python` 没有新文件。没做：决策 10（用户答复了「做」，下一个 PR，§10.18）；Windows 与 macOS 上的 `zypi`；真实 LLM；两个 agent 进程之间的空隙。见 §10.17。
- r15：删 `pi-shell` 的旧存储层（2026-10-19）。用户 2026-10-18 的四个答复里，r14 做了前三个；这一条做第四个：决策 10。他的答复只说了存储层，所以只做这一项；决策 10 的另两项（`util/config/mcp.rs` 的 `load_mcp_servers`、`pi-pager/src/recent_dirs.rs`）没有问过，没动。做成 PR [#26](https://github.com/zy1233/pi-python/pull/26)（分支 `refactor/delete-pi-shell-session-store`，基是 #25 的分支 `fix/write-session-at-first-message`，没有合入）。① 删除：`session::storage`（14 个文件，10,007 行）与 `session::persistence`（12 个文件，2,009 行），再加只有它们在用的代码（编译器报了 41 处 `dead_code`：goal 跟踪、会话信号、workflow 状态、图片检查、`fork_filter_chat` 等，共 2,149 行）。`pi-pager` 还要用的标题清洗函数搬进新模块 `session::title`（140 行，8 个测试）；`GoalClassifierVerdict` 搬进 `extensions/notification.rs`。`tui/` 共 53 个文件，+190 / −14,166；`pi-shell` 的非测试代码 40,985 → 31,653 行，测试 1,056 → 864；清单里 `reference_passed` 的和 8,706 → 8,514。生产代码没有行为变化。② 一个测试的竞争：删除之后，整个 `pi-shell` 的 `--lib` 有一个失败（`session::worktree` 的测试，SQLite 错误 1802）；直接跑测试二进制 20 次，20 次都出现。原因：`consent` 的一个测试把 `GROK_HOME` 设成自己的临时目录，用的串行键却是具名的 `GROK_HOME`；其他读 `GROK_HOME` 的测试用默认键，而 `serial_test` 让不同的键同时跑，`EnvGuard` 自己又没有锁。所以 worktree 测试把 `worktrees.db` 建在那个临时目录里，consent 测试结束时把目录删了，SQLite 打开 WAL 文件时对数据库路径做 `stat`，得到 ENOENT，返回 1802（错误名是 `SQLITE_IOERR_FSTAT`，失败的调用却是对路径的 `stat`）。这是用 `LD_PRELOAD` 的跟踪找到的。这个竞争在 #25 的头 `c74cc18` 上就有（同样的筛选运行 15 次失败 15 次），不是这个 PR 带来的。修：那个测试改用默认的 `#[serial_test::serial]`（`332ba75`，+5 / −1，只改测试文件）；筛选运行 15 次失败 0 次，整个 `--lib` 20 次失败 0 次。③ 验证（WSL2）：`check` 在 `5a39e09` 与 `332ba75` 上都通过；整套 10 个 suite 在 `332ba75` 上 8,513 通过 / 1 个已知失败 / 21 忽略（16 m 24 s）；`tui_notices.py check` 989 / 989；对 `session::title` 的变异检查（14 个改动）：13 个被抓住，1 个（标题长度上限 100 → 101）漏过，已记下，没补；Python：Windows 2,559 通过 / 39 跳过 / 31 不选，Linux 2,568 / 30 / 31；Ruff 通过。CI（PR #26 的 `69932cf`）：`TUI CI` 8,514 通过 / 0 失败 / 21 忽略，`Gates + cargo check` 通过（依赖图 980，告警 0）；`CI` 的 Ruff 与 `pytest` 3.11、3.12、3.13（各 2,559 / 39 / 31）通过。CI 的测试线程是 4 个，竞争在这个线程数下本来就不出现，所以 CI 通过不算修好的证据。本地运行在 WSL 的 `~/.pi-python` 与 `/tmp` 里留下的残留已删。没做：决策 10 的另两项；`pi-shell` 里 7 个没人引用的依赖的删除；新出现的死单元 `session::helpers` 与 `session::worktree`（→ D.8）；`leader` 与 `x.ai` 上限的降低（现在是 243 与 297）；`[storage] cleanup_ttl_days`；同类风险的检查（`pi-pager` 里的 `serial(GROK_HOME)`、其他具名键）；`c74cc18` 的整套运行为什么没有碰到这个竞争；`auth::manager::lock` 的心跳测试偶发失败的原因；Windows 与 macOS 上的构建。见 §10.18。
- r16：合入 #25 与 #26，删 `pi-shell` 里剩下的死代码（2026-10-19）。用户从「接下来做哪些」里选了 `merge_25_26`、`dead_units`、`small_cleanup`、`race_audit`；没选 `d10_rest`（决策 10 的另两项）。① 合入：#25（`2b0baf3`）与 #26（`7d82c27`；#25 合入后基改成 `main`，把 `main` 合进它的分支，`git merge -s ours`，因为两边的树相同）；`CI` 与 `TUI CI` 在两个 `main` 提交上都通过（`TUI CI` 在 `7d82c27` 上 8,514 通过 / 0 失败 / 21 忽略）。② PR [#27](https://github.com/zy1233/pi-python/pull/27)（分支 `refactor/tui-session-store-leftovers`，基是 `7d82c27`，没有合入）：6 个提交，`tui/` 共 28 个文件，+13 / −1,644。删了：15 个没人加载的文件（14 个 `goal_*.md` 与 `deep_research.rhai`，最后的引用在 #7 里没有了）；死单元 `session::worktree` 与 `session::helpers`；`[storage]` 段（`StorageConfig`；旧的配置照常加载，这一段被报成不认识的键；1 个新测试）；`util::config::worktree`（第一遍 `check` 的一条 `unused_imports` 告警找到的）；`pi-shell` 的 10 个依赖（依赖图 980 → 970，`Cargo.lock` 少 `tdigests`，`THIRD-PARTY-NOTICES` 少 5 条）。清单：`leader` 245 → 243，`x.ai` 304 → 297，依赖图 980 → 970，`pi-shell` 864 → 838（和 8,514 → 8,488）。生产代码没有行为变化。③ 验证（WSL2）：`check` 两遍（第一遍 1 条告警，已修；第二遍全过）；`tui_notices.py check` 984 / 984；整套 10 个 suite 8,487 通过 / 1 个已知失败 / 21 忽略（23 m 56 s）；PTY 脚本全过（`resume_matrix.py --strict` 16 PASS / 0 GAP / 0 FAIL）；新测试的变异检查（把 `StorageConfig` 放回去，测试失败）；Python：Windows 2,559 通过 / 39 跳过 / 31 不选，Linux 2,568 / 30 / 31；Ruff 通过；本地运行留在 WSL 与 Windows 临时目录里的残留已清；推送后 `CI` 与 `TUI CI` 在 `6bd7db5` 上通过（`TUI CI` 8,488 通过 / 0 失败 / 21 忽略，依赖图 970，`leader` 243 / `x.ai` 297）。④ 本地验证之后、推送之前，用 `git cherry-pick` 重建了这个分支的 6 个提交，因为第一个提交的信息把 `deep_research.rhai` 的行数写成了 564（应为 584）；每个提交的树没有变。⑤ 没做：`race_audit`（下一个只改测试的 PR）；调用图仍报死的 7 个单元（1,008 行）、没有用户的 crate `pi-workflow`（9 个文件，3,570 行）、`Config.storage_mode` 与 `--storage-mode`、约 88 个候选的没人加载的文件（→ D.8）；决策 10 的另两项；Windows 与 macOS 上的构建。见 §10.19。
- r17：检查测试之间的竞争（2026-10-19）。用户选的四项里剩下的一项，`race_audit`（检查同类竞争）。做成了一个只改测试与工具的 PR：[#28](https://github.com/zy1233/pi-python/pull/28)（分支 `test/tui-env-race-audit`，叠在 #27 的分支上，没有合入）。生产代码没有改；清单里 `reference_passed` 的和 8,488 → 8,490（多的 2 个是 `in_own_process` 的自检）。① 工具：`scripts/tui_envspy/`：环境变量探针（`envspy.sh`，一个 `LD_PRELOAD` 的共享库）与压力脚本（`stress.sh`：随机顺序、多个线程；可以只跑点名的测试，做配对与人口实验）；10 个新的 `pytest`。② 压力运行找到 9 个偶发失败的测试；配对与人口实验又找到 10 个，共 19 个。**3 个是测试自己的缺陷**：`pi-shell` 的 `subagents_config_models_with_env_var_enables`（两把互不排斥的锁保护同一个变量）与心跳用例（别的测试 `fork` 出的子进程还拿着锁的文件描述符）；`pi-pager` 的 `memory_trace` 样本用例（别的测试写进了进程级的追踪输出）。另有 5 个测试改了 `GROK_HOME` 等变量却不还原。这些在测试里修了。**16 个由进程级的 `static` 引起**：颜色等级与终端原生调色板、最小模式的标志、双向文字的闩、内嵌弹窗的标志、语音的门、主题。读它们的测试有几百个，都不拿锁，所以加锁修不了。改它们的 57 个测试现在各在自己的进程里跑（`pi-pager` 的 `test_util::in_own_process`：父进程用 `--exact` 再启动同一个测试二进制，子进程跑测试的主体，子进程失败则父进程失败；每个多约 30 ms）。③ 数字（WSL2，本地；32 线程、随机顺序）：`pi-pager` 的 lib，修前 6/32 遍有失败，修后 0/60（种子 1–60）与 0/32（种子 201–232）；`pi-shell` 2/20 → 0/20；4 线程（CI 的线程数，种子 101–120）0/20 → 0/20（中间一次 1/20）；固定顺序（32 线程，10 遍）0/10 → 0/10；其他 7 个 suite 各 0/10。配对（一个受害者与一个写者，100 遍）：族 5 语音的门 100/100 → 0/100，族 6 主题 96/100 与 92/100 → 0/100。整套清单 8,490 通过 / 0 失败 / 21 忽略（`88e04e9`，WSL2）。④ `cargo nextest`（一个测试一个进程）在隔离之前就让 16 遍运行都没有失败；要不要在 `TUI CI` 里改用它，由用户决定：`pi-pager` 的 lib 在 4 个线程下由 29.7 s 变成 79.5 s，nextest 不跑文档测试，基线执行器要改。（r18：改用了，见 §10.21；「不跑文档测试」不构成代价，基线执行器从来没有跑过文档测试。）⑤ 另一个发现，没有改：`find_protoc()` 在 `PATH` 上找到 `protoc` 时返回不带路径的 `protoc`，构建脚本因此发出 `rerun-if-changed=protoc`，这个路径不存在，所以 `pi-tools-api` 与它上面的 crate 每次调用 cargo 都重新编译；CI 的 `cargo test` job 里 `pi-tools-api` 编译了 7 次。（r18 更正：这 7 次不是这个原因，见 §10.20 与 §10.21。）⑥ 没验证 / 留下的：约 140 个测试写外观缓存里的别的设置（`set_show_thinking_blocks` 等），没有隔离，没有已知的受害者；2 个失败原因没有查明（`blank_quote_line_survives_drag_copy_as_blank` 一次；`command_palette_search_bar_cursor_only_when_focused` 1/60，隔离之后没有再出现）；别的 7 个 suite 只各跑了 10 遍，清单外的 crate 没有查；只在 Linux；`in_own_process` 的限制见 §10.20。⑦ 验证的范围：长运行（整套清单、60 遍、人口、配对）在 `88e04e9` 上；最后的提交 `6438b59` 的 `tui/` 与它只差 `test_util.rs` 里两行。`6438b59` 上只跑了门禁（`check`）、`rustfmt` 和 `stress.sh` 的跳过段，没有跑测试：WSL 的虚拟磁盘把 D: 盘写满了（构建目录 29 GB，之后删了；虚拟磁盘文件还要压缩）。CI 在 `ea9609f` 上通过：`TUI CI` [run 39240903399](https://github.com/zy1233/pi-python/actions/runs/39240903399) 是 8,490 通过 / 0 失败 / 21 忽略；`CI` [run 39240903417](https://github.com/zy1233/pi-python/actions/runs/39240903417) 通过。做法与数字见 §10.20。
- r18：改用 `cargo nextest`、`protoc` 的路径、`release.yml` 的令牌（2026-10-20）。用户从「接下来做哪些」里选了 `release_token`、`protoc_abs`、`nextest_ci`；没选 `protoc_ci_env`、`d10_rest`、`merge_27_28`。三个 PR 一个叠在一个上（#28 → #29 → #30 → #31），生产代码没有行为变化，都没有合入。① [#29](https://github.com/zy1233/pi-python/pull/29)：`release.yml` 里两处 `arduino/setup-protoc` 加 `repo-token`；只改一个工作流文件；`CI` 通过；在分支上手动跑了一次演练：通过（[run 39360804691](https://github.com/zy1233/pi-python/actions/runs/39360804691)，细节见 §10.21）。② [#30](https://github.com/zy1233/pi-python/pull/30)：`find_protoc()` 返回 `PATH` 上 `protoc` 的绝对路径；同一条 `cargo` 命令重复运行时 `pi-tools-api` 不再每次重新构建（WSL2：第 3 遍 2.2 s → 0.3 s）；`CI` 与 `TUI CI` 通过（8,490 通过 / 0 失败 / 21 忽略）。**更正 r17：**CI 的 `cargo test` job 里 `pi-tools-api` 编译 7 次不是这个问题造成的（每个 suite 一条 `cargo` 命令，各按自己的 feature 编译一次）；修了之后还是 7 / 4 / 3，job 用时也没变（22 m 08 s）；只有 `Gates + cargo check` job 里 2 次 → 1 次。③ [#31](https://github.com/zy1233/pi-python/pull/31)：`TUI CI` 与基线执行器改用 `cargo nextest`（清单 `[test] runner = "nextest"`，`--runner cargo` 回到 libtest）；解析 nextest 的 `Summary` 行与汇总；「跳过」把 `#[ignore]` 与 `--skip` 合在一起算（预计 22）；不跑文档测试，而这些 suite 从来没跑过；超时先 SIGTERM、20 秒后 SIGKILL（nextest 把每个测试放在自己的进程组里）；21 个新的 `pytest`（Windows 50 通过 / 3 跳过，Linux 53 通过）。#31 的 `TUI CI` 通过：8,490 通过 / 0 失败 / 22 跳过（整棵树第一次在 nextest 下跑），测试自己的时间约 130 s（`cargo test` 约 65 s），job 36 m 17 s。#29 在分支上手动跑了一次 `Release` 的演练，通过。 没验证：整棵树在 nextest 下只在 CI 里跑过，本地没有跑过整套清单；`-I<前缀>/include` 在日志里看不到；`release.yml` 的演练通过不证明限额问题已经解决，要等下一个 tag。见 §10.21。
