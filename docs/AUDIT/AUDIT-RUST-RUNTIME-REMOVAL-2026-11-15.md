# AUDIT：Rust Runtime 剥离计划第二次代码审计（2026-11-15）

> **审计日期**：2026-11-15。发现、修复与本文在同一天完成。
>
> **审计范围**：在第一次审计（[`AUDIT-RUST-RUNTIME-REMOVAL-2026-11-08.md`](AUDIT-RUST-RUNTIME-REMOVAL-2026-11-08.md)，PR [#115](https://github.com/zy1233/pi-python/pull/115)）合入之后，对照 [`PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md)（r48）、[`docs/DESIGN.md`](../DESIGN.md) §8 与 [ACP 扩展登记](../specs/2026-11-05-acp-extension-registry.md)，对 `main` @ `3431699` 做第二次全量代码实现审计。覆盖：
> - Rust TUI：`tui/crates/codegen/{pi-pager,pi-pager-bin,pi-pager-render,pi-acp-lib,pi-home,pi-shell,pi-workspace,pi-shared,pi-telemetry}`；
> - Python ACP agent：`packages/pi-agent-cli/pi_agent_cli/`；
> - VS Code ACP client：`vscode/src/core/`；
> - 门禁脚本：`scripts/tui_baseline.py` 与 `scripts/tui_baseline.toml`。
>
> **状态图例**：`[x]` 已修复并验证

---

## 一、问题总览

编号 `R2-n` 是第二次审计的编号（Round 2 Finding），另含第一次审计 F-1 / F-3 / F-5 的收尾项。

| ID | 级别 | 位置 | 问题摘要 | 状态 |
|----|------|------|----------|------|
| R2-1 | 中 | `packages/pi-agent-cli/pi_agent_cli/agent.py` | `pi/session/delete` 删除当前已打开的会话时，遗漏清理 `_session_models`、`_session_cwds` 与 `auto.cancel()`（与 `close_session` 不一致，也与 ACP 扩展登记 X1 的契约不符） | `[x]` |
| R2-2 | 中 | `tui/crates/codegen/pi-pager/src/{app/mod.rs,app/event_loop.rs,unified_log.rs}` | F-1 跟进：`init_tracing()` 晚于 `acp::connect` 与 `resolve_startup` 初始化，导致启动阶段的 `tracing` / `unified_log` 事件丢失，且 `app/mod.rs` 与 `tracing.rs` 重复调用 `pi_telemetry::external::init()`；`unified_log::make_entry` 残留未使用的 `_lvl`/`_sid` 参数 | `[x]` |
| R2-3 | 中 | `tui/crates/codegen/pi-pager-render/src/prompt_images.rs` | F-2 跟进：图片缓存迁至 `<home>/pager-cache/<sid>/images/` 后，`build_content_blocks_with_workspace` 的孤儿 `[Image #N: <path>]` 占位符路径白名单仍只放行工作区与临时目录，未包含 `<home>/pager-cache/` | `[x]` |
| R2-4 | 低–中 | `scripts/tui_baseline.py` | `production_lines()` 只识别行首顶格的 `mod tests {` 与 `}`，对缩进的 `#[cfg(test)] mod tests {` 或单行 `mod tests {}` 会吞掉后续生产代码，导致 `session_disk_reads` / `no_callers` 门禁存在盲区 | `[x]` |
| R2-5 | 低–中 | `tui/crates/codegen/pi-home/src/lib.rs` | Rust 侧 `pi-home` 解析 `PI_HOME` / `GROK_HOME` 时未展开 `~` / `~/...`，若用户导出 `PI_HOME=~/.pi-python`，`zypi` 会在当前工作目录下创建字面量 `./~` 目录，与 Python / VS Code 端不一致 | `[x]` |
| R2-6 | 低 | `tui/crates/codegen/pi-pager-bin/src/{main.rs,print_mode.rs}` | ① `zypi -p "hi" -v` 先进入 `dispatch_python_print` 拉起 Python agent，而非优先处理 `-v`/`--version`；② `async_main` 仍设置无读取者的 `GROK_COMPACTION_MODE` / `GROK_COMPACTION_DETAIL` 环境变量；③ `print_mode::ignored_flags` 遗漏 `--trust`、`--compaction-mode`、`--compaction-detail`、`--worktree-ref`、`--restore-code`、`--no-ask-user`、`--no-wait-for-background`、`--oauth` | `[x]` |
| R2-7 | 低 | `docs/specs/2026-11-05-acp-extension-registry.md`、`pi-acp-lib/src/message.rs` | ① ACP 扩展登记未记录 `vscode/` 对 X1、X3、X5、X8 的读写，§5 未列全 `zypi` 发送而 Python 忽略的客户端 `_meta` 键；② `AcpArgsGeneric` / `AcpClientMessageGeneric` / `AcpAgentMessageGeneric` 未转发 `AcpMethod::ext_name()`；③ `agent_modes.rs` 与 `session_startup.rs` 的两处过期注释 | `[x]` |

---

## 二、各项详情与修复

### R2-1. `[x]` `pi/session/delete` 清理与 `session/close` 对齐

- **问题**：`packages/pi-agent-cli/pi_agent_cli/agent.py` 中，`close_session` 弹出 `_session_models`、`_harnesses`、`_permission_locks`，释放 `_reserved_ids`，并在存在 `auto` gate 时调用 `auto.cancel()`，但没有弹出 `_session_cwds`；而 `ext_method("pi/session/delete")` 只弹出了 `_harnesses`、`_permission_locks` 并释放 `_reserved_ids`（另清理信任、通知与模式状态），遗漏了 `_session_models.pop`、`_session_cwds.pop` 与 `auto.cancel()`。
- **修复**：抽出 `_release_session(self, session_id: str) -> AgentHarness | None`，由 `close_session` 与 `ext_method("pi/session/delete")` 共同调用，确保两处清理路径完全一致。`close_session` 因此也新增了 `_session_cwds.pop`（此前遗漏）。
- **测试**：`packages/pi-agent-cli/tests/test_session_lifecycle.py` 新增 `test_deleting_an_open_session_forgets_its_model_cwd_and_cancels_auto`。

### R2-2. `[x]` 启动阶段 `tracing` 初始化前置、去重 `pi_telemetry::external::init` 与 `unified_log` 清理

- **问题**：
  1. 第一次审计 F-1 将 `unified_log` 改为写入 `tracing`（目标 `pi_pager::unified_log`），但 `crate::tracing::init_tracing()` 在 `event_loop::run`（`tui/crates/codegen/pi-pager/src/app/event_loop.rs:914`）才调用，晚于 `app::run` 中的 `acp::connect` 与 `resolve_startup`。因此启动与会话恢复阶段的 `tracing` 和 `unified_log::info` 事件没有 subscriber，且连接失败退出时只写 stderr 而不进日志文件。
  2. `app/mod.rs:688` 调用了一次 `pi_telemetry::external::init(...)`，而 `tracing.rs:174` 在 `init_tracing()` 内又调用了一次。
  3. `unified_log.rs:20` 的 `make_entry` 保留了未使用的 `_lvl: &str` 与 `_sid: Option<&str>` 参数。
- **修复**：
  1. 在 `app::run` 入口设置 `GROK_LOG_SAMPLING` 并立即调用 `crate::tracing::init_tracing()`，把返回的 `TracingHandle` 传给 `event_loop::run`；连接失败分支补充 `tracing::error!`。
  2. 删除 `app/mod.rs` 中重复的 `pi_telemetry::external::init` 调用。
  3. 简化 `unified_log::make_entry(msg, ctx)` 签名。

### R2-3. `[x]` `<home>/pager-cache/` 孤儿图片占位符恢复白名单

- **问题**：第一次审计 F-2 把粘贴图片缓存从 `<home>/sessions/...` 迁到了 `<home>/pager-cache/<sid>/images/`。但 `tui/crates/codegen/pi-pager-render/src/prompt_images.rs` 中 `build_content_blocks_with_workspace` 用于从历史文本恢复孤儿 `[Image #N: <path>]` 占位符的 `allowed_prefixes_for_workspace` 只允许工作区目录与系统临时目录，未包含 `<home>/pager-cache/`。
- **修复**：新增 `allowed_prefixes_with_pager_cache_in(workspace_cwd, grok_home)`，将 `<home>/pager-cache` 加入 `build_content_blocks_with_workspace` 的前缀白名单（同时继续拒绝 `<home>` 根目录下的其它文件如 `config.toml`）。
- **测试**：`prompt_images.rs` 新增 `orphan_placeholder_in_pager_cache_is_allowed_while_home_root_stays_out`。

### R2-4. `[x]` `scripts/tui_baseline.py` `production_lines()` 的 `#[cfg(test)]` 解析加固

- **问题**：`scripts/tui_baseline.py` 的 `production_lines()` 用 `line.startswith("mod tests")` 与顶格 `line.rstrip() == "}"` 判断 `#[cfg(test)]` 测试模块边界：
  1. 若 `#[cfg(test)]` 标注在缩进的嵌套模块上（或单行 `mod tests {}`），找不到顶格 `}` 时会把文件剩余的所有生产代码都当作测试代码跳过；
  2. 若 `#[cfg(test)]` 与 `mod tests` 之间夹有空行、注释或其它属性，状态机立即复位，跳过失效。
- **修复**：记录 `mod tests` 的缩进前缀 `indent` 并匹配对应缩进的闭合 `}`（忽略行尾注释），支持同行 `#[cfg(test)] mod tests`、单行 `mod tests {}`，以及 `#[cfg(test)]` 与 `mod tests` 之间的空行/注释/属性。
- **测试**：`scripts/tests/test_tui_baseline.py` 新增 `test_indented_or_single_line_cfg_test_module_does_not_hide_following_code`。

### R2-5. `[x]` `pi-home` 支持展开 `PI_HOME` / `GROK_HOME` 中的 `~`

- **问题**：Python 端（`pi_agent_core/home.py` 的 `expanduser()`）与 VS Code 扩展（`vscode/src/core/first-run.ts` 的 `expandHome`）都会将 `PI_HOME=~/...` 展开为用户家目录，而 Rust 端 `tui/crates/codegen/pi-home/src/lib.rs` 直接使用 `PathBuf::from(trimmed)`，导致 `PI_HOME=~/.pi-python` 在相对路径下读写 `./~/.pi-python`。
- **修复**：在 `pi-home/src/lib.rs` 中新增 `expand_tilde(trimmed, os_home)`，对 `~`、`~/...`、`~\...` 按 `os_home` 展开。
- **测试**：`pi-home/src/lib.rs` 新增 `env_tilde_expands_against_os_home`。

### R2-6. `[x]` `pi-pager-bin` 的 `-p -v` 优先级、死环境变量清理与 `print_mode::ignored_flags` 补齐

- **问题**：
  1. `main.rs` 中 `dispatch_python_print(&args)` 在 `dispatch_version_if_requested(&args)` 之前执行，导致 `zypi -p "hi" -v` 会启动 Python agent 而非打印版本号退出；
  2. `async_main` 仍在设置 `GROK_COMPACTION_MODE` 与 `GROK_COMPACTION_DETAIL` 环境变量，仓库内已无任何读取者；
  3. `print_mode::ignored_flags` 遗漏了 8 个 `PagerArgs` 接收但 `-p` 不转发给 Python agent 的旗标。
- **修复**：
  1. 将 `dispatch_version_if_requested(&args) || dispatch_doctor_if_requested(&args)` 移到 `dispatch_python_print(&args)` 之前，并在 `dispatch_python_print` 内检查 `args.version`；
  2. 删除 `async_main` 中设置 `GROK_COMPACTION_MODE` / `GROK_COMPACTION_DETAIL` 的代码，更新 `cli.rs` 中对应的文档注释；
  3. 在 `print_mode::ignored_flags` 中补齐 `--trust`、`--compaction-mode`、`--compaction-detail`、`--worktree-ref`、`--restore-code`、`--no-ask-user`、`--no-wait-for-background`、`--oauth`。
- **测试**：更新 `print_mode.rs` 的 `flags_the_agent_does_not_receive_are_reported` 与 `main.rs` 的 `version_flags_and_doctor_are_distinct_early_intents`。

### R2-7. `[x]` ACP 扩展登记更新、`AcpMethod::ext_name` 包装类型转发与过期注释清理

- **修复**：
  1. `tui/crates/codegen/pi-acp-lib/src/message.rs`：在 `AcpArgsGeneric`、`AcpClientMessageGeneric`、`AcpAgentMessageGeneric` 上转发 `ext_name(&self)`，消除泛型包装层漏转发的隐患；
  2. `docs/specs/2026-11-05-acp-extension-registry.md`：更新 X1、X3、X5、X8 的发送方/读取方（补入 `vscode/` 的调用方：`acp-connection.ts` 与 `chat-controller.ts`），并在 §5 补齐 `zypi` 发送而 Python 忽略的客户端 `_meta` 键；
  3. 清理第一次审计 F-5 留下的两处过期注释（`agent_modes.rs` 与 `session_startup.rs`）。
