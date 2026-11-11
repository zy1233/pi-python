# AUDIT：Rust Runtime 剥离计划的代码与设计对照（2026-11-08）

> **审计日期**：2026-11-08。发现、修复与本文在同一天完成。
>
> **审计范围**：计划 [`PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md)（r44 的状态）对照 `main` 上 `26ffb9b` 的代码。代码：`tui/crates/codegen/` 里的 `pi-pager`、`pi-acp-lib`、`pi-pager-render`、`pi-shared`、`pi-telemetry`、`pi-workspace`；`packages/pi-agent-cli/`（Python 的 ACP agent）；`scripts/tui_baseline.py` 与它的清单 `scripts/tui_baseline.toml`。
>
> **对照设计**：原则 P1 至 P7（这一轮用到 P2 协议所有权与 P6 生命周期）；ADR1、ADR3、ADR4、ADR5；阶段 C 的退出条件与 D.4；附录 A（ACP 契约）；[ACP 扩展登记](../specs/2026-11-05-acp-extension-registry.md)。
>
> **方法**：读代码；用 `rg` 找符号的调用者；对每个发现写出设计怎么说、代码怎么做；每个修复带测试或门禁；对新测试做变异检查（去掉修复，测试必须失败）。没有用探针脚本。五个发现（F-1 至 F-5）先在聊天里报告过。本文的事实不是抄那份报告，而是对着代码重新核对的。
>
> **修复**：本地分支 `fix/audit-rust-runtime-findings`，基于 `origin/main` 的 `26ffb9b`，每个主题一个提交（`git log 26ffb9b..fix/audit-rust-runtime-findings`）。没有推送，没有 PR。用户没有要求推送。（2026-11-11 补记：分支后来推到了远端；用户要求合并，我在 2026-11-11 开了 PR [#115](https://github.com/zy1233/pi-python/pull/115) 并合入。经过见执行记录的 [§10.48](../PLAN/archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md#1048-r45-追加审计计划与代码的偏差修-xailog图片缓存的位置加权限管理器的门禁)「r46 补记」。）
>
> **测试状态**：见第五节。只在 macOS 上跑；Linux 与 Windows 没有跑。（2026-11-11 补记：PR #115 的 CI 在 Linux 上跑了，全部通过：Ruff、pytest、`TUI CI` 的门禁与 `cargo check`、`cargo nextest`。`TUI CI` 只在 Linux 上构建：Rust 代码在 Windows 上的编译没有人跑过；macOS 上只有作者的那一次。）
>
> **状态图例**：`[x]` 已修复并验证 · `[x/~]` 已修复，有已记录的残留（第四节）

---

## 一、问题总览

编号 `F-n` 是本次审计的编号（F = Finding）。它与 `AUDIT-PHASE6-PHASE7-2026-09-30.md` 里的 `F6-xx` / `F7-xx` 无关。

| ID | 级别 | 问题 | 处理 | 状态 |
|----|------|------|------|------|
| F-1 | 中 | pager 把自己的日志当作 `x.ai/log` 通知发给 agent；挡住它的守卫从不触发；守卫的测试会永远挂住，所以清单把它 `--skip` 了 | 代码 | `[x]` |
| F-2 | 中 | pager 把粘贴的图片与渲染的图表缓存写进 agent 的会话目录；删会话时它们留在磁盘上；读盘门禁没有看拼路径的两个 crate | 代码 | `[x/~]` |
| F-3 | 低 | 原则 P6 写的退出协议（`session/close`、SIGTERM、SIGKILL）比做成的长 | 文档 | `[x]` |
| F-4 | 低 | Rust 的权限管理器（`permission/`，35,166 行）没有调用者，也没有门禁防它被接回 pager | 门禁与文档 | `[x]` |
| F-5 | 低 | 附录 A、ADR4、ADR5、阶段 C 的几行与代码不符 | 文档 | `[x/~]` |

---

## 二、各项详情

### F-1. [x] pager 向 agent 发 `x.ai/log`，守卫不触发

**设计怎么说。** P2：pager 与 agent 之间只走标准 ACP 与已登记的扩展。`x.ai/*` 是 xAI 的方法，计划不实现它们。`pi-acp-lib` 的 `acp_send` 是 pager 向 agent 发消息的唯一出口，它有一道守卫，要丢掉 `x.ai/*` 的扩展。

**代码怎么做。**
- `pi-pager/src/unified_log.rs` 把每条日志（时间、进程号、版本、级别、会话号、消息、上下文）放进内存缓冲区，每秒（或满 16 条）作为通知 `x.ai/log` 发给 agent（线上是 `_x.ai/log`）。
- Python 的 `ext_notification` 对没见过的方法什么也不做。所以每条转发的日志都在 agent 一侧被丢掉。旧的 Rust shell 会把它们写进 `unified.jsonl`；现在的 agent 是 Python 进程，没有人写。只有 `write_direct_info` 不走转发，它直接写 `unified.jsonl`；它没有变。
- 守卫读的是序列化之后的请求。`acp::ExtRequest` 与 `acp::ExtNotification` 的名字字段 `method` 带 `#[serde(skip)]`（`agent-client-protocol-schema` 0.11.4），序列化的结果只有参数。守卫读不到名字，从不触发。
- 守卫的测试 `vendor_x_ai_ext_is_dropped_without_sending` 因此会永远等一个回答：请求真的发出去了，没有人答。清单（`scripts/tui_baseline.toml`）用 `--skip` 把它去掉；计划 §10.4 记过这个挂起，当时没有人把它看成守卫失效。

**影响。** 不是安全问题：agent 是本机的子进程，它丢掉了这些通知。但是：违反 P2；转发的日志整个丢了；一个守卫看上去在，其实不起作用，被一个 `skip` 盖住。

**怎么修的。**
1. `pi-acp-lib`：`AcpMethod` 加 `ext_name()`，扩展请求与扩展通知从类型本身返回名字；`acp_send` 用它判断（`message.rs`、`channel.rs`）。守卫丢掉以 `x.ai/` 或 `_x.ai/` 开头的扩展，其余的（`pi/yolo_mode_changed`、`pi/session/delete`、没有前缀的名字）照发。
2. pager：删了转发。`unified_log` 的 `info`、`warn`、`error`、`debug` 还在，条目写进 `tracing`（目标 `pi_pager::unified_log`），也就是 pager 自己的日志。`init` 与 `flush_blocking` 没有人再调，一并删了。
3. `pi-telemetry`：删了通知的线上类型 `LogNotificationParams` 与常量 `LOG_METHOD`，和它们的 2 个测试。
4. 清单：去掉 `--skip`；`x.ai` 的残留上限 245 → 240（改了几行带这个词的注释、日志文字与测试里的字面量）。
5. 测试：恢复 `vendor_x_ai_ext_is_dropped_without_sending`，并让它不再等回答（`answer_at_once`：只轮询一次，守卫坏了就失败，不会挂住）；另加通知的丢弃、该照发的名字、读名字的函数 3 个。

**变异检查。** 把守卫改回读序列化后的值，丢弃用例失败（不挂住）。

**端到端的断言：做不到。** 修复之后 pager 不再发任何 `x.ai` 的扩展，线上没有可断言的现象。守卫由 `pi-acp-lib` 的单元测试覆盖。

### F-2. [x/~] 图片与图表的缓存写进 agent 的会话目录

**设计怎么说。** ADR1：会话数据是纯 ACP；pager 不读 agent 的会话文件，也不拼会话目录的路径。清单的 `[gates.session_disk_reads]` 防这一点。会话存储属于 agent：`<home>/sessions/<时间>-<id>.jsonl`，平的。

**代码怎么做。**
- `pi-pager-render/src/prompt_images.rs` 用 `pi_shared::session::session_dir(...)`（即 `sessions_cwd_dir(cwd)/<会话号>`）算出 `<home>/sessions/<编码后的工作目录>/<会话号>/`，在里面放 `images/`（粘贴的图片）与 `mermaid/`（渲染的图表 PNG）。
- agent 的 `pi/session/delete` 只删会话文件。它不知道这个目录，所以用户删了会话，图片留在磁盘上。粘贴的图片可能是任何东西的截图。
- 门禁只看 `pi-pager` 与 `pi-pager-bin`。拼路径的两个 crate（`pi-pager-render`、`pi-shared`）不在清单里，所以没有报。

**影响。** 删掉的会话留下用户的图片；pager 往 agent 的目录里写；门禁有洞。

**怎么修的。**
1. 新位置 `<home>/pager-cache/<会话号>/{images,mermaid}`，不在 `sessions/` 里（`pi-pager-render` 的 `session_cache_dir_in`、`session_images_dir`、`session_mermaid_dir`）。`<home>` 是 pager 的家目录：`$PI_HOME`（或 `$GROK_HOME`），都没设时是 `~/.pi-python`。
2. 会话号来自 agent，而删除缓存用 `remove_dir_all`，所以规则从严：会话号只有 1 至 128 个字符，每个字符是 `A-Z a-z 0-9 - _ .` 之一，而且不能全是点，才算一个普通的名字。别的会话号不给它建目录（`None`；图片只留在内存里）。
3. 新建的目录在 Unix 上是 `0700`（`create_dir_all_private`）；已有的目录不改权限。
4. pager 在 agent 确认 `pi/session/delete` 之后删 `pager-cache/<会话号>`（`effects/mod.rs` 的 `forget_session_cache`）。agent 拒绝时不删。删不掉只写日志，不让删会话失败。
5. 门禁：`[gates.session_disk_reads]` 的 crate 加 `pi-pager-render` 与 `pi-shared`；模式加 `sessions_cwd_dir`（也匹配 `ensure_sessions_cwd_dir`）与 `session::session_dir`。`pi-shared` 里没有人用的 `session_dir` 删了。
6. 测试：`pi-pager-render` 里 1 个用例换成 6 个（净增 5）；`pi-pager` 加 `delete_session_removes_the_pager_cache_after_the_agent_confirms`（对着一个假的 agent：确认的会话清掉缓存，拒绝的与别的会话保留；它设 `PI_HOME`，在自己的进程里跑）；`scripts/tests` 加 2 个门禁用例。

**变异检查。** 去掉 `forget_session_cache` 的调用，`pi-pager` 的新用例失败。

**残留。** 旧版本已经写进 `sessions/<目录>/<会话号>/{images,mermaid}` 的文件不迁移，也不清理。这一轮没有问用户要不要清。要清的话，用户可以手动删这些目录。

### F-3. [x] P6 写的退出协议比做成的长

**设计怎么说。** P6 的原文：「子进程启动失败、取消、退出有明确协议（`session/close` → 关闭 stdin → 宽限 → SIGTERM → SIGKILL）」。

**代码怎么做。** `pi-pager/src/acp/spawn.rs`：pager 关闭 agent 的 stdin（EOF 就是停止请求），等 3 秒（`AGENT_EOF_GRACE`）；agent 还没有退出，就杀掉它并回收（`stop_child`；Unix 上是 SIGKILL）。总预算 10 秒（`AGENT_EXIT_GRACE`，编译期断言 3 秒小于它）。pager 不发 `session/close`，也不发 SIGTERM。Python 一侧把 SIGTERM 与 SIGHUP 接到 EOF 的同一条退出路径（`__main__.py`；POSIX；Windows 只靠 EOF）。这是 r5 的 1.P2（计划 §10.7），P6 一行没有跟着改。

**怎么处理。** 只改文档：P6 一行加了更正，写明真实的协议。没有改代码。

### F-4. [x] Rust 的权限管理器没有调用者，没有门禁

**设计怎么说。** 原则 P1：权限由 Python agent 决定。D.4 的 r37 状态写 `permission/` 是活的（`pi-workspace` 的主要用处）；用户在 r38 定了不重构 `pi-workspace`。

**代码怎么做。** `permission/` 有 35,166 行，里面有一个完整的权限管理器，含 Auto：一个让模型来判断的分类器（调模型的函数由调用者装上，`set_classifier_with_side_query`）。pager 用的是 `permission/` 的类型与辅助函数。启动管理器的两个函数 `spawn_permission_manager` 与 `spawn_permission_manager_with_hub` 在 `permission/` 之外没有调用者：它们只在 `permission/manager/mod.rs` 定义，在 `permission/mod.rs` 再导出。所以「活的」只对类型与辅助函数成立；管理器没有跑。

**风险。** 没有门禁防它被接回去。一个调用者就会在 pager 进程里放进第二个决定权限的引擎，用它自己的规则；再给它一个调模型的函数，它的 Auto 分类器就会自己去问模型。

**怎么处理。**
1. `scripts/tui_baseline.py` 加一种门禁 `no_callers`（`scan_callers`）：清单里的 `[[gates.no_callers]]` 给一个名字、一组符号（子串）和允许的目录；符号在允许的目录之外出现就失败。跳过测试文件、`#[cfg(test)]` 模块与注释行。现在只有一条：符号 `spawn_permission_manager`，允许 `codegen/pi-workspace/src/permission`。符号是子串，所以 `spawn_permission_manager_with_hub` 也被挡；第三个 `spawn_permission_manager_with_pin` 是私有函数（只在 `manager/mod.rs` 里用），名字里也有这个子串。
2. `scripts/tests/test_tui_baseline.py` 加单元测试，和一个对着真实的树跑的用例（`test_nothing_outside_permission_starts_the_rust_permission_manager`）。
3. 文档：计划 D.4 加 r45 的状态；`docs/baselines/tui.md` 加一行。代码没有动（用户的决定不变）。

**变异检查。** 在 `permission/` 之外加一个调用，门禁与真实树的用例都失败。

### F-5. [x/~] 几行文档与代码不符

只改文档。核对过代码之后改了这些行（计划里每处带「r45 更正」）：

| 位置 | 原来写的 | 代码 |
|------|----------|------|
| 附录 A.1 模型选择；A.3 `session/set_config_option` | 只认 `model` | 认 `model` 与 `mode`（r39 起；`mode` 经 `_select_mode`，与 `session/set_mode` 是同一个状态） |
| 附录 A.1 模式切换，「决定」一格 | 隐藏 Plan / Auto / Default | Auto 自 r41 起提供；还藏着的只有 Plan 与 Default（`agent_modes.rs` 的 `PLAN = false`、`DEFAULT = false`、`AUTO = true`） |
| 附录 A.1 登录；A.3 `initialize`、`authenticate` | `auth_methods=[]`，`authenticate` 没有处理器（`method_not_found`） | v0.7.0 起：客户端声明终端登录的能力（`clientCapabilities.auth.terminal`，或旧式的 `_meta["terminal-auth"]`）、而且 agent 自己读配置时，`initialize` 公布方法 `pi-setup`；`authenticate` 只认 `pi-setup`（别的 id：`invalid_params`），配置还不能用时回 `auth_required`。`zypi` 不声明这个能力，所以拿到空表（`auth_methods.py`、`agent.py`） |
| 附录 A.3 `session/set_mode` | pager 发 Plan（Shift+Tab 第三档） | Plan 藏在 `PLAN = false` 后面；`set_plan_mode` 在闸门关着时什么也不做。`modes.rs` 与 `session/lifecycle.rs` 里其余的 `Effect::SetSessionMode` 没有逐条核对 |
| 阶段 C 的退出条件 | 现在 `leader` 239、`x.ai` 282 | `leader` 213、`x.ai` 240（以清单为准） |
| ADR4 | 没有状态 | 加了：① 做了；② 找到 `x.ai/log` 这一处漏的；③ 不做（1.R5，用户，2026-11-04）；`session/delete` 等 SDK |
| ADR5 | 没有状态 | 加了：忽略未知键成立；首次启动告警没做（现有的是一条进日志的 `tracing` 警告）；字段清单没出，因为 B.4 已定不做 |
| 计划的头部、「当前状态」、「下一步」 | `main` 在 `2426452`，比 v0.8.0 多 31 个提交 | `main` 在 `26ffb9b`，多 35 个（多出的是 #107、#108、#110、#111） |

**残留。** 代码里的两条注释也过期了，这一轮没有动（只改文档）：`tui/crates/codegen/pi-pager/src/app/agent_modes.rs` 开头写着 `session/set_mode` 在 Python 一侧没有处理器（r39 起有，Plan 的结论仍然成立：agent 对 `plan` 回 `invalid_params`，调用失败，只记日志）；`app/session_startup.rs` 第 164 行的「plan item 1.P6 removes the rest」（r44 记过）。

---

## 三、核对过、没有偏差的部分

这些是这一轮对着代码核对、没有找到偏差的项。没有列在这里的，这一轮没有查。

- **ACP 版本配对（A.2）。** Rust：`agent-client-protocol` 0.10.4、`agent-client-protocol-schema` 0.11.4（`tui/Cargo.lock`）。Python：`agent-client-protocol>=0.12.0`（`packages/pi-agent-cli/pyproject.toml`；本机装的是 0.12.1）。pager 的 `initialize` 发 `ProtocolVersion::V1`；agent 回 `min(请求的版本, PROTOCOL_VERSION)`。
- **MCP（1.P4）。** `session/new` 与 `session/load` 都不带 `mcp_servers`；测试 `session_new_and_load_send_no_mcp_servers` 通过。
- **退出时间的关系。** `AGENT_EOF_GRACE`（3 秒）小于 `AGENT_EXIT_GRACE`（10 秒），编译期断言。
- **扩展的发送。** 守卫放行 `pi/yolo_mode_changed`、`pi/session/delete` 与 `custom/test`（`channel.rs` 的测试），这三个名字不被误丢。
- **终端登录。** pager 的代码里没有 `auth.terminal` 或 `terminal-auth`，所以 `zypi` 总是拿到空的 `authMethods`。
- **门禁。** 改动之后，`scripts/tui_baseline.py gates`：依赖图 954 个包（上限 954）；`deny_crates` 与 `deny_words` 通过；残留词 `grok agent` 0、`leader` 213、`x.ai` 240；读盘与调用者门禁 0 处命中。

---

## 四、没有处理的与没有验证的

**没有处理的。**
1. **旧的缓存文件。** 见 F-2 的残留。
2. **`permission/` 的 35,166 行。** 留着（D.4，用户的决定）；F-4 只加了门禁。
3. **`pi-chat-state` 的 actor。** `ChatStateActor::spawn` 与 `spawn_with_pruning`（`src/actor/mod.rs:54`、`:72`）在这个 crate 之外没有调用者；别的 crate 只用它的类型（`UsageTotals`、`UsageLedger`、`CompactionDetail`、`CompactionAttempt`）。这个 crate 的 `src/` 有 14,254 行。`pi-shell` 的 `Cargo.toml` 依赖它，源码里没有引用。这是观察，没有门禁，也没有决定要不要动。
4. **ADR4 ③。** pager 里 `pi/*` 的解码留着：1.R5 已定不做（用户，2026-11-04）。
5. **ADR5 的首次启动告警。** 没做；计划里没有决定要不要补它。
6. **两条过期的代码注释。** 见 F-5 的残留。
7. **守卫只认 `x.ai/` 前缀。** 别的未登记的扩展名没有同样的守卫。登记里的八项由 [ACP 扩展登记](../specs/2026-11-05-acp-extension-registry.md) 管。

**没有验证的。**
- **Linux 与 Windows。** 所有的测试只在 macOS 上跑过。清单里 `pi-pager-render` 的 1,097 与 `pi-pager` 的 5,519 是 Linux 的旧数加上净增的个数，不是在 Linux 上量的，等 CI 对账。
- **nextest。** 本机没有 `cargo-nextest`，用的是 `cargo test`；同一个测试二进制里的测试是线程，环境变量与 `static` 的竞争可能只在某些运行里出现。新加的 `pi-pager` 用例设 `PI_HOME`，所以用 `in_own_process` 在自己的进程里跑。
- **F-4 的门禁认的是名字。** 它挡 `spawn_permission_manager` 这个子串，也就是两个公开的入口。我没有查管理器里的类型能不能被 `permission/` 之外的代码直接构造并启动，绕过这两个函数。
- **真的终端。** 在 `zypi` 里粘一张图、再删会话，看缓存目录有没有被清：没有做。缓存的路径与清理只有单元测试与对着假 agent 的测试看过。
- **PTY 脚本**（`scripts/tui_pty/`）：没有跑。它们需要 Linux 与一个编好的 `zypi`。
- **真实模型。** 没有跑。
- **整套 pytest**（Python 一侧）：这一轮没有改 Python 代码，没有跑。

---

## 五、验证方法与结果

环境：macOS，cargo 1.96.1（没有 rustup，所以 `tui/rust-toolchain.toml` 的 1.94.0 没有生效），`.venv` 的 Python 3.14.6、pytest 9.1.1、ruff 0.16.9。跑 cargo 时用 `HOME=/tmp/tui-test-home`，不碰真实的家目录。

| 检查 | 结果 |
|------|------|
| `cargo test --locked -p pi-acp-lib`（`--lib` 与 `ext_wire_golden`） | 68 通过 |
| `cargo test --locked -p pi-telemetry --lib` | 218 通过 |
| `cargo test --locked -p pi-pager-render --lib` | 1,094 通过 / 2 忽略（第一遍有一个已知会受负载影响的计时用例失败，单独重跑通过；它在清单的 `known_failures` 里） |
| `cargo test --locked -p pi-pager --lib` | 5,243 通过 / 12 忽略 |
| `cargo test --locked -p pi-pager --test settings_e2e --test grok_home_paths --test selection_model_public_api --test python_agent_e2e --test user_guide_pages` | 272 通过（2 + 15 + 2 + 252 + 1）。设了 `PI_E2E_PYTHON`（`.venv` 的 Python）与 `PI_E2E_REQUIRED=1`，所以 `python_agent_e2e` 的 15 个用例对着真的 Python agent 跑了，没有跳过 |
| `python scripts/tui_baseline.py check --out /tmp/...` | 通过：消费者构建 0 条告警（上限 0）；`cargo check --workspace --tests` 0 个错误；下面的门禁同样通过 |
| `python scripts/tui_baseline.py gates --out /tmp/...` | 全部通过：依赖图 954 个包（上限 954）；残留 `grok agent` 0、`leader` 213、`x.ai` 240；读盘门禁（四个 crate）与调用者门禁 0 处命中 |
| `.venv/bin/python -m pytest scripts/tests` | 275 通过 / 8 跳过 |
| `ruff check .`、`ruff format --check .` | 通过（312 个文件已格式化） |

`pi-pager` 的一个已有告警：`tests/python_agent_e2e.rs` 里 `Env.mark` 这个字段没有人读（`dead_code`）。这个文件这一轮没有改，所以不是这一轮造成的；消费者构建的告警数不算测试目标，所以它不影响上限 0。
