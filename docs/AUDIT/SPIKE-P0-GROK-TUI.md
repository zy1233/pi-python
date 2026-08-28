# P0 Spike：grok-build TUI 拆除面（2026-08-25）

> Clone：仓外 `d:\work\grok-build`；已 `git archive` 迁入本仓 `tui/`。HEAD `c2ad97f`；上游 `SOURCE_REV` `437c7c9…`。
> 目的：列出 pager 对 `x.ai/*` 的依赖，供 fork 时拆除。**Python 不实现这些方法。**

## 拆除进度（迁入后）

`git archive` 迁入时**没有**做 SPIKE 拆除。第一轮 fork 已开始：

| 项 | 状态 |
|---|---|
| 停发 `x.ai/*`（`pi-acp-lib::acp_send` 丢弃，不入队） | 已做 |
| 忽略入站 `x.ai/*` 通知 / ext_method | 已做 |
| 停止写入 initialize / session `_meta` 的 `x.ai/*` 键 | 已做 |
| 跳过空 `auth_methods` 登录墙；spawn `python -m pi_agent_cli` | 已做（P2） |
| 删除 grok CLI 子命令；`pi-grok-*` crate → `pi-*`；启动跳过 auth/prefetch | **已做**（P5 de-grok） |
| Cargo crate `xai-*` → `pi-*` | **已改** |
| 产品二进制名 `zypi`（crate `pi-pager-bin`） | **已做** |

验收：欢迎页 → `initialize` → `session/new` → `session/prompt` 不得把上表 RPC 发到 Python。

## 构建

WSL2 上 **`cargo check -p pi-pager-bin` 已通过**。产品二进制 **`zypi`**（`cargo check -p pi-pager-bin`）。

注意：

- 代理用主机局域网 IP `:10809`（例如 `172.20.35.30`），不要用 WSL 网关 `172.26.176.1`（入站会被防火墙丢掉）。
- Windows clone 的 `bin/protoc` 是 CRLF，Linux 上会报 `env: ‘dotslash\\r’`。check 前对 `bin/*` 去 `\r`，并安装 `dotslash`。
- 建议 `CARGO_TARGET_DIR` 放在 Linux 家目录，不要写 `/mnt/d`。

## 调度中枢

TUI 扩展 RPC 大多从 `crates/codegen/pi-pager/src/app/effects/mod.rs` 发出。标准 ACP（Python agent）无 grok 云认证；远程 session restore 已禁用。

标准 ACP 已在用：`initialize`、`session/new`、`session/load`、`session/prompt`、`session/cancel`、`session/update`、`request_permission`。这些保留。

## 拆除分类

下列字符串均出现在 pager `src/**/*.rs` 的 `"x.ai/…"` 字面量中（含测试与 `_meta` 键）。fork 时应：停发对应 RPC、忽略对应通知、不要依赖 grok `_meta`。

### 启动 / 认证 / 计费（必须拆，否则阻塞 welcome）

- `x.ai/auth/get_url`、`x.ai/auth/submit_code`、`x.ai/auth/cancel`、`x.ai/auth/logout`、`x.ai/auth/check_subscription`
- `x.ai/consent/record`、`x.ai/privacy/setCodingDataRetention`
- `x.ai/billing`、`x.ai/auto-topup-rule`

### 会话扩展（有标准 ACP 替代则改映射，否则隐藏 UI）

- `x.ai/session/list`、`x.ai/session/delete`、`x.ai/session/fork`、`x.ai/session/info`、`x.ai/session/usage`、`x.ai/session/rename`、`x.ai/session/search`、`x.ai/session/update`、`x.ai/session/prompt_complete`、`x.ai/session/interjection`
- `x.ai/sessions/list`、`x.ai/sessions/changed`
- `x.ai/session_notification`
- `x.ai/compact_conversation`、`x.ai/prompt_history`、`x.ai/rewind/points`、`x.ai/rewind/execute`
- `x.ai/share_session`

`/new` `/resume` 应对准标准 `session/new` / `session/load`，不要走 `x.ai/session/*`。

### 队列 / 插入 / 建议

- `x.ai/queue/changed`、`clear`、`edit`、`hold_edit`、`release_edit`、`interject`、`remove`、`reorder`
- `x.ai/interject`、`x.ai/btw`、`x.ai/recap`、`x.ai/follow_ups`
- `x.ai/suggest`、`x.ai/suggestPrompt`

### 任务 / 子代理 / 终端

- `x.ai/task/kill`、`x.ai/task_backgrounded`、`x.ai/task_completed`
- `x.ai/subagent/cancel`
- `x.ai/terminal/background`
- `x.ai/scheduler/delete`、`x.ai/scheduled_task_*`、`x.ai/schedulerBackgroundLoops`

### Git / worktree

- `x.ai/git/worktree/*`（list/show/remove/gc/detach/salvage/db/* / create / resume）
- `x.ai/git_head_changed`、`x.ai/gitHeadChanged`

### MCP / hooks / plugins / marketplace / skills

- `x.ai/mcp/list`、`setup`、`upsert`、`delete`、`toggle`、`toggle_tool`、`auth_trigger`、`elicit`、`elicit_complete`、`init_progress`、`server_status`、`servers_updated`、`tools_changed`、`mcp_initialized`
- `x.ai/hooks/list`、`x.ai/hooks/action`
- `x.ai/plugins/list`、`action`、`notify-updates`
- `x.ai/marketplace/list`、`action`
- `x.ai/skills/list`、`toggle`、`refresh-baseline`
- `x.ai/workflows/list`、`x.ai/commands/list`

### 其它 RPC / 通知

- `x.ai/ask_user_question`、`x.ai/exit_plan_mode`、`x.ai/toggle_plan_mode`
- `x.ai/yolo_mode_changed`、`x.ai/models/update`、`x.ai/settings/update`
- `x.ai/feedback`、`x.ai/feedback/upload-trace`
- `x.ai/memory/rewrite`、`x.ai/bundle/entry/get`、`x.ai/bundle/status`
- `x.ai/leader/version_mismatch`、`x.ai/announcements/update`、`x.ai/monitor_event`

### `_meta` 键（不是 JSON-RPC method，但 session/new 会带）

`x.ai/session`、`x.ai/tool`、`x.ai/partial`、`x.ai/restore_code`、`x.ai/runningPromptId`、`x.ai/local_workspace`、`x.ai/cloud_*`、`x.ai/listScope`、`x.ai/facetFilters`、`x.ai/hunkTracker`、`x.ai/incrementalBashOutput`、`x.ai/bashOutputNoColor`、`x.ai/replayed`、`x.ai/titleIsManual` 等。Python agent **不要**解析或回这些字段；TUI 侧应停止写入。

## 标准 ACP 主路径（应保留）

welcome → `initialize` →（跳过 auth）→ `session/new` → `session/prompt` → `session/update` 流式 → `request_permission` → `session/cancel`。P2 改造的验收就是这条路径不依赖上表任何 `x.ai/` RPC。
