# AUDIT：Phase 7 ExtensionAPI 设计与实现审计

> **编号说明**：本文件的 `P7-01` … `P7-15`、`P7R3-xx` 等编号属于这一轮审计（2026-09-22 ~ 09-28）。
> 2026-09-30 的另一轮审计（[`AUDIT-PHASE6-PHASE7-2026-09-30.md`](AUDIT-PHASE6-PHASE7-2026-09-30.md)）
> 另有一套 `P6-xx` / `P7-xx` 编号，含义不同；代码、测试、CHANGELOG 里写作 `audit P7-xx` 的指那一轮。
> 本文件「已记录限制」第 1 条（untracked 文件不会被 worktree 回写）已被那一轮的 P7-10 取代：新文件现在会回写。
>
> **审计周期**：2026-09-22 ~ 2026-09-28，共七次复核
>
> **审计范围**：
> - `pi_agent_core/extensions/{types,registry,api,loader,_harness_bridge,__init__}.py`
> - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py` 中 Phase 7 集成
> - `packages/pi-web-access/`、`packages/pi-goal-x/`、`packages/pi-dynamic-workflows/`
>   （含 `subagent.py`、`manager.py`、`journal.py`、`worktree.py`、`store.py`）
> - `packages/pi-agent-cli/pi_agent_cli/{agent,factory}.py` 的 slash command/ACP 集成
>
> **对照设计**：`docs/specs/2026-09-22-phase7-extension-api-design.md`
>
> **最终测试状态**：
> - Windows：`573 passed, 30 skipped`
> - WSL：`567 passed, 4 skipped`
> - `ruff check` + `ruff format --check`：All checks passed
>
> **状态图例**：`[x]` 修复已验证 · `[x/~]` 主路径修复、子项残留 · `[~]` 已记录的低风险偏差

---

## 一、问题总览

### 高影响

| ID | 问题 | 修复轮次 | 状态 |
|----|------|----------|------|
| P7-01 | 扩展生命周期事件未接入事件总线 | R1 | `[x/~]` |
| P7-02 | activate 失败后部分注册残留 | R1 | `[x]` |
| P7-03 | bridge 在 activate 后才绑定，激活期 API 不可用 | R1 | `[x]` |
| P7-04 | `session_id` 始终返回空字符串 | R1+R2 | `[x]` |
| P7R3-01 | 首次 run 不创建 journal，resume 不可用 | R3 | `[x]` |
| P7R3-02 | subagent 无 tools/env/cwd，worktree 未接入 | R3+R4 | `[x/~]` |
| P7R3-03 | 后台结果不触发新 turn，lifecycle 未关闭 | R3+R4 | `[x/~]` |
| P7R4-01 | 后台 workflow 在 agent_end 后被取消 | R4 | `[x]` |
| P7R5-01 | worktree 修改不会回写，隔离结果被丢弃 | R5+R6 | `[x]` |
| P7R6-01 | snapshot worktree dirty diff 无法安全 apply | R6 | `[x]` |

### 中影响

| ID | 问题 | 修复轮次 | 状态 |
|----|------|----------|------|
| P7-05 | 动态加载扩展后 handler 不接线、同名工具覆盖无效 | R1 | `[x]` |
| P7-07 | `register_command()` 无运行时消费方 | R2 | `[x]` |
| P7-09 | `max_agents` 可被并发绕过 | R1 | `[x]` |
| P7-10 | 工具状态更新绕过 harness | R1+R4 | `[x/~]` |
| P7R3-04 | journal phase hash 不含隐式 phase | R3 | `[x]` |
| P7R3-05 | `resume_from_run_id` 允许路径穿越 | R3 | `[x]` |
| P7R3-06 | 64MB journal 上限未实现 | R3 | `[x]` |
| P7R3-07 | `background=True` 无 manager 时静默退化 | R3 | `[x]` |
| P7R3-08 | saved workflow 项目/用户优先级方向相反 | R3 | `[x]` |
| P7R3-09 | WorkflowStore 原子保存失败路径 | R3+R4 | `[x/~]` |
| P7R4-03 | unsubscribe 不从 harness hooks 移除 | R4 | `[x/~]` |
| P7R4-04 | session_start 事件未触发 + goal restore 空实现 | R4+R5 | `[x/~]` |
| P7R4-05 | set_active_tools 无事件/持久化 | R4 | `[x]` |
| P7R4-06 | 同名扩展覆盖未实现 | R4+R5 | `[x/~]` |
| P7R4-07 | WorkflowStore no-clobber TOCTOU 竞态 | R4 | `[x]` |
| P7R5-02 | 动态 `on()` 不进入运行时 hooks | R5 | `[x]` |
| P7R5-03 | goal restore 仍是空实现 | R5 | `[x]` |
| P7R5-04 | 同名覆盖不清理 live harness + 失败无法回滚 | R5+R6 | `[x]` |
| P7R6-02 | 同名替换失败后 live runtime 未恢复 | R6 | `[x]` |

### 低影响 / 契约偏差

| ID | 问题 | 状态 |
|----|------|------|
| P7-12 | `auto_discover_extensions` 默认值与设计不一致 | `[x]` 设计文档已更新为 `False` |
| P7-13 | 编程加载 API 名称/类型与设计不一致 | `[x]` 新增 `load()` 别名 |
| P7-14 | `tool_result` handler 可修改结果（设计标只读） | `[x]` 设计文档已更新 |
| P7-15 | `web_search` 文本格式未按设计示例 | `[x]` 设计文档已更新 |
| P7R3-10 | 设计文档 §9.5/§10.2 与 §12-§16 状态矛盾 | `[x]` §10.2 已更新为 ✅ |

---

## 二、高影响问题详情与修复

### P7-01. [x/~] 扩展生命周期事件未接入事件总线

- **问题**：核心 agent 事件通过 `_emit_any()` → `_subscribers` 分发，但扩展
  handler 存放在 `_hooks` 中，`_emit_any()` 不遍历 `_hooks`。导致 `turn_end`、
  `agent_end`、`message_*`、`tool_execution_*` 扩展事件均不触发。
- **修复**：`_handle_agent_event()` 在 `_emit_any()` 后调用 `_emit_hook()`。
- **残留**：`session_start → before_agent_start` 映射未实现（见 P7R4-04 修复）。

### P7-02. [x] activate 失败后部分注册残留

- **问题**：`activate()` 直接写共享 registry，中途异常不回滚；失败扩展的 ghost
  tool 仍对 LLM 可见。
- **修复**：`load_callable()` 在 activate 前 snapshot，失败时 restore。

### P7-03. [x] bridge 在 activate 后才绑定

- **问题**：`load_all()` 先执行所有 activate，之后才创建 bridge。activate 内
  调用 `pi.cwd` 等 API 抛 `RuntimeError`。`pi-dynamic-workflows` 降级为 `"."`。
- **修复**：`_ensure_extensions_loaded()` 先创建 bridge，再传给 `load_all()`；
  loader 在 activate 前调用 `_set_bridge()`。

### P7-04. [x] session_id 始终返回空字符串

- **问题**：bridge 的 `session_id` 无条件 `return ""`。
- **修复**：`_ensure_extensions_loaded()` 在创建 bridge 前读取 metadata 并
  缓存 `_session_id`，activate 期即可读取真实 id。

### P7R3-01. [x] 首次 run 不创建 journal，resume 不可用

- **问题**：Journal 仅在传入 `resume_from_run_id` 时创建；正常首次运行返回的
  run id 无对应文件，重放时重新执行而非命中缓存。
- **修复**：`workflow_tool.py` 每次 run 立即分配稳定 `run_id`
  (`uuid.uuid4().hex[:12]`)，立即创建 Journal。`resume_from_run_id` 增加
  格式校验 (`^[a-zA-Z0-9_-]{1,128}$`) 防止路径遍历。

### P7R3-02. [x/~] subagent 无 tools/env/cwd

- **问题**：`run_agent()` 创建 AgentHarness 时不传 tools/env；`effective_cwd`
  赋值后未使用；`WorktreeManager` 无调用方。
- **修复**：`run_agent()` 创建 `LocalExecutionEnv(effective_cwd)` +
  `create_all_tools()`，传入 harness。`WorkflowParams` 新增
  `isolation: bool = False`，启用时通过 `WorktreeManager` 创建隔离 worktree。
- **残留**：P7R3-03 中的 `agent_end` shutdown 回归已在 P7R4-01 修复。

### P7R3-03. [x/~] 后台结果不触发新 turn

- **问题**：`_on_complete()` 只 append `steer_queue`，idle harness 不会 drain。
  `shutdown()`/`cancel_all()` 无生产调用方。
- **修复**：bridge 新增 `trigger_prompt(text)` 方法（idle 时触发 `prompt()`，
  忙碌时降级 steer）。`_on_complete()` 改用 `trigger_prompt()`。

### P7R4-01. [x] 后台 workflow 在 agent_end 后被取消

- **问题**：`activate()` 把 `manager.shutdown()` 注册到 `agent_end` hook，但
  `agent_end` 每次 prompt 结束都触发，后台 workflow 30s 后被强制取消。
- **修复**：bridge 新增 `register_cleanup(callback)` 用于 session-close 级回调。
  harness 新增 `async close()` 方法。ACP `close_session()` 调用
  `await harness.close()`。`activate()` 改用 `register_cleanup(manager.shutdown)`。

### P7R5-01 → P7R6-01. [x] worktree 修改丢失 / dirty diff 无法 apply

- **P7R5-01 问题**：`run_agent()` 执行后直接 `cleanup()`，未调用
  `collect_diff()`/`apply_changes()`，修改随 worktree 删除。worktree 创建失败
  时静默回退到共享 cwd。
- **P7R5-01 修复**：cleanup 前调用 `apply_changes(wt_path)` 回写 diff。创建
  失败时返回 `AgentResult(error=...)` 而非静默回退。
- **P7R6-01 问题**：源 cwd 有 dirty 修改时，`collect_diff()` 返回完整 diff
  （dirty + agent），对已有 dirty 的源 cwd `git apply` 失败（patch already applied）。
- **P7R6-01 修复**：`_apply_snapshot()` 在复制 dirty 修改后执行
  `git add -A && git commit -m "pi-snapshot-baseline"`，使 `collect_diff()` 只
  返回 agent 增量。

---

## 三、中影响问题详情与修复

### P7-05. [x] 动态加载扩展后 handler 不接线

- **修复**：`load_extension()` 重新调用 `_apply_extension_registrations()`。

### P7-07. [x] register_command() 无运行时消费方

- **修复**：harness 新增 `dispatch_command()`，`prompt()` 入口调用
  `_try_slash_dispatch()`。ACP 层 `_advertise_commands()` 广播
  `AvailableCommandsUpdate`。新增 12 个测试（harness 9 + ACP e2e 3）。

### P7-09. [x] max_agents 并发绕过

- **修复**：检查 + 计数预留放入 `_count_lock` 保护。

### P7-10. [x/~] 工具状态更新绕过 harness

- **修复**：`set_active_tool_names()` 拒绝未知工具名。后续 R4 补充：队列
  `pending_session_writes` + 异步发射 `ToolsUpdateEvent`。

### P7R3-04. [x] journal phase hash 不含隐式 phase

- **修复**：hash 使用解析后的实际参数（`resolved_phase`、`resolved_cwd`）。

### P7R3-05. [x] resume_from_run_id 路径穿越

- **修复**：附带在 P7R3-01 中修复，格式校验 `^[a-zA-Z0-9_-]{1,128}$`。

### P7R3-06. [x] 64MB journal 上限未实现

- **修复**：`append()` 写入前检查文件大小，超限跳过并记 warning。

### P7R3-07. [x] background=True 无 manager 时静默退化

- **修复**：返回明确错误文本。

### P7R3-08. [x] saved workflow 优先级方向相反

- **修复**：`scan()` 扫描顺序从 `[user, project]` 改为 `[project, user]`。

### P7R3-09. [x/~] WorkflowStore 原子保存失败路径

- **修复**：R3 用 `fd_closed` 标志修复二次关闭。R4 用 `os.link(tmp, dest)`
  替代 `dest.exists()` + `os.replace()` 消除 TOCTOU 竞态。

### P7R4-03 + P7R5-02. [x] unsubscribe 不生效 + 动态 on() 不进入运行时

- **问题**：unsubscribe 只操作 registry，不操作 `_hooks`；首次加载后 `on()` 新增
  的 handler 只进 registry 不进 `_hooks`。
- **修复**：bridge 新增 `add_hook()`/`remove_hook()` 方法。unsubscribe 调用
  `bridge.remove_hook()`。`on()` 在 `_loading==False` 且 bridge 存在时调用
  `bridge.add_hook()` 注入 live hooks。

### P7R4-04 + P7R5-03. [x] session_start 未触发 + goal restore 空实现

- **P7R4-04 修复**：`_ensure_extensions_loaded()` 完成后调用
  `_emit_hook_simple("session_start")`。
- **P7R5-03 修复**：bridge 新增 `get_custom_entries(custom_type)`。harness 在
  session_start 前缓存 custom entries。`_restore_goal_state()` 从
  `goal_state` entry 恢复 `GoalState`。

### P7R4-06 + P7R5-04 + P7R6-02. [x] 同名覆盖不生效 / 不清理 live harness / 失败无法回滚

- **P7R4-06 修复**：registry 新增 `_tool_owners` 追踪归属 + `remove_by_extension()`。
  `load_callable()` 同名时清除旧注册后加载新扩展。
- **P7R5-04 修复**：bridge 新增 `remove_tool()`。`_purge_live_harness()` 遍历
  snapshot 清理旧扩展的 harness 工具和 hooks。
- **P7R6-02 修复**：`_purge_live_harness()` 从 activate 前移至 activate 成功后。
  activate 期间 `_loading=True`，tools/hooks 不注入 live harness，因此失败时
  registry restore 即可完整回滚，harness 运行时不受影响。

---

## 四、低影响与契约偏差

| ID | 说明 |
|----|------|
| P7-12 | `auto_discover_extensions` 默认 `False`（设计 `True`），CLI/TUI 显式传 `True` 不受影响 |
| P7-13 | 只有 `load_callable(activate_fn)`，不接受 module 对象，无 `load()` 别名 |
| P7-14 | `tool_result` handler 可修改返回值（`AfterToolCallResult`），设计标只读 |
| P7-15 | `web_search` 输出编号列表格式，非设计的 `title: url\nsnippet` |
| P7R3-10 | 设计 §9.5/§10.2 保留过期状态，与 §12-§16 已实现状态矛盾 |

---

## 五、已记录限制

1. **Untracked 文件不会被 worktree 回写**：`collect_diff()` 使用 `git diff HEAD`，
   不含 untracked 文件。设计 §15 已明确不复制 untracked，按已知限制处理。
   若后续需要可写 worktree 支持新增文件，需 stage untracked 后再 diff。

2. **后台 workflow 结果仅在 harness idle 时自动交付**：harness 忙碌时降级为
   steer queue append，不保证及时触发新 turn。

---

## 六、最终判定

**第七次复核（2026-09-28）：所有高影响和中影响问题已修复或标记残留。**

- 10 个高影响问题全部关闭。
- 20 个中影响问题全部关闭（部分标 `[x/~]` 表示有已记录的低风险子项）。
- 5 个低影响 / 契约偏差已全部修复（设计文档对齐 + `load()` 别名）。
- 2 项已知限制已记录。

测试从首次审计的 `493 passed` 增至 `573 passed`，新增回归测试覆盖所有审计修复项。
