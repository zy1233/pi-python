# AUDIT：Phase 7 ExtensionAPI 设计与实现审计（2026-09-24，六次复核）

> 审计范围：
> - `pi_agent_core/extensions/{types,registry,api,loader,_harness_bridge,__init__}.py`
> - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py` 中 Phase 7 集成
> - `packages/pi-web-access/`、`packages/pi-goal-x/`、`packages/pi-dynamic-workflows/`
>   （含 `subagent.py`、`manager.py`、`journal.py`、`worktree.py`、`store.py`）
> - `packages/pi-agent-cli/pi_agent_cli/{agent,factory}.py` 的 slash command/ACP 集成
> - `factory.py` 与三个扩展包的 entry point 声明
>
> 对照设计：
> - `docs/specs/2026-09-22-phase7-extension-api-design.md`
>
> 验证方式：
> - 静态逐项对照设计 §3–§16
> - 使用内存 Session / mock stream 做针对性运行复现
> - 临时 HOME 下全量 `.venv\Scripts\python.exe -m pytest -q`：
>   **558 passed, 11 skipped**
> - 默认受控环境下 workflow 包：**2 failed + 14 setup errors**（HOME/TEMP 隔离）
> - `ruff check` Phase 7 相关包：**All checks passed**
>
> 状态图例：
> `[x]` 复核确认修复 · `[x/~]` 主问题已修复、已知子项仍被记录 ·
> `[ ]` 已知未修复 · `[~]` 已记录限制/低风险偏差，本轮不重复评级
>
> 说明：下文保留首次审计的问题详情与证据；状态标题和复核结论反映对应日期的
> 代码状态。2026-09-24 三次复核针对设计新增的 §12–§16 重新审计，不沿用上一轮
> “全部适配完成”的结论。

---

## 历史复核摘要（第一、二次）

首次审计的核心结论是：ExtensionAPI 的数据模型和注册机制存在，但运行时事件、
失败隔离、bridge 生命周期、动态加载和 agent 安全上限存在阻断级问题。

2026-09-23 二次复核对修复后的代码重新执行了静态审查、针对性运行复现和全量测试。
结论如下：

- **4 个高影响问题的主路径均已关闭**：
  - 核心生命周期事件已同时派发到 subscriber 与 hook；
  - activate 失败会回滚 tool/command/event 注册；
  - bridge 已在 activate 前绑定，真实 `cwd` 可用；
  - 首次 turn 建立后 `session_id` 已正确填充。
- 同时复核确认 P7-05（首次 prompt 后动态加载）和 P7-09（并发 agent cap）
  已修复，P7-10 的未知工具名称校验已补上。
- **未发现上一轮修复的高影响回归**。全量测试由首次审计时的 `493 passed` 增至
  `548 passed, 11 skipped`，新增测试覆盖了事件派发、回滚、bridge、并发上限、
  以及 slash 命令路由（dispatch + ACP 广播 + 端到端）。
- 2026-09-24 代码进一步调整了 `session_id` 初始化顺序：metadata 在 bridge
  创建前读取并缓存。三次复核实测 activate 阶段同时得到正确 `cwd` 与
  `session_id`，因此该时序残留已关闭。
- P7-07（slash 命令路由死端）已在后续修复中补全：Harness 层 dispatch +
  ACP 层 `AvailableCommandsUpdate` 广播，`/goal`、`/workflows` 等命令可执行。
- P7-06、P7-08、P7-11 以及低风险 `[~]` 项属于首次审计已记录问题；
  本轮按要求不重复评级，也不纳入高影响修复验收。

**二次复核判定（仅代表 2026-09-23 代码状态）：高影响问题验收通过。**
该结论已被下方“三次复核（2026-09-24）”覆盖，不再代表当前代码。

## 二次复核结论

| 首次问题 | 复核状态 | 验证结果 |
|---|---|---|
| P7-01 事件总线 | `[x/~]` | `turn_start`、`turn_end`、`agent_end`、`message_*` 均触发一次；`session_start` 映射仍随 P7-08 记录 |
| P7-02 失败回滚 | `[x]` | 崩溃扩展的 tool/command/event 全部回滚，运行时无 ghost tool |
| P7-03 bridge 时序 | `[x]` | `LocalExecutionEnv.cwd` 在 activate 中直接可读，workflow 不再降级为 `"."` |
| P7-04 session id | `[x]` | 三次复核确认 activate 期与 turn_end 均返回真实 metadata id |
| P7-05 动态加载 | `[x]` | 后加载 handler 生效，同名工具运行时定义被替换 |
| P7-09 并发 agent cap | `[x]` | `max_agents=2, concurrency=1` 下第 3 个并发 agent 被拒绝 |
| P7-10 工具校验 | `[x/~]` | 未知名称已被拒绝；事件/持久化一致性仍属已知残留 |

复现摘要：

```text
core_event_counts {'agent_end': 1, 'message_end': 2, 'message_start': 2,
                   'turn_end': 1, 'turn_start': 1}
session_start_calls 0
rollback_state {'tools': [], 'commands': [], 'handlers': []}
activate_cwd D:\workspace-expected
activate_session_id 'sid-live'
post_prompt_session_id 'bridge'
late_tool_description new
late_event_count 1
```

---

## 三次复核（2026-09-24）

本轮同时覆盖新增设计 §12–§16。结论是：上一轮的四个高影响问题仍保持修复状态，
但新增的“真实 subagent / 后台运行 / resume / worktree / saved workflow”功能
存在新的高影响实现缺口，当前不能按设计文档的 ✅ 状态验收。

### P7R3-01. [x] 高：首次 run 不创建 journal，`resume_from_run_id` 无法恢复真实运行

- 设计：§14 `docs/specs/2026-09-22-phase7-extension-api-design.md:489-499`。
- 实现：
  - `_resolve_journal()` 只有在传入 `resume_from_run_id` 时才返回 Journal；
    正常首次运行返回 `None`。
  - `WorkflowRuntime` 仅在 `journal is not None` 时 append。
  - 运行结束后返回的 `runId` 是 runtime 内部临时生成的，不会反向创建
    `~/.pi-python/workflow-journals/<run_id>.jsonl`。
- 运行复现：
  - 首次 workflow 返回 run id `d7385db1-7eb`，对应 journal 文件不存在。
  - 再用该 run id 调用 `resume_from_run_id`，executor 调用数从 1 增到 2，
    说明没有命中缓存，而是重新执行。
- 影响：
  - §14 的“持久化 → 按 run_id resume”端到端不可用；只有手工预置 journal 的
    测试路径能工作。
- 建议：
  - 每次运行先分配稳定 run_id，创建对应 journal，并把同一 run_id 传入 runtime；
    重放时使用该 id 加载，而不是在运行结束后才生成结果 id。

### P7R3-02. [x/~] 高：真实 subagent 没有 tools/env，`cwd` 被忽略，worktree 接入缺失

- 设计：§12 `docs/specs/2026-09-22-phase7-extension-api-design.md:461-473`；
  §15 `:503-514`。
- 实现：
  - `HarnessSubagentExecutor.run_agent()` 创建 `AgentHarness` 时只传
    `session/model/stream_fn/get_api_key`，没有 `tools`，也没有 `env`。
  - `effective_cwd = cwd or self._cwd` 被赋值后完全未使用。
  - `WorktreeManager` 只存在于 `worktree.py`，仓库内没有调用方；
    `WorkflowParams` 也没有 isolation/worktree 参数。
- 运行复现：
  - 用带 `cwd` 的 executor 运行 subagent，观察到的 `context.tools` 数量为 `0`。
- 影响：
  - `codebase-audit`、`code-review` 等要求“检查代码”的 pattern 没有文件/命令工具，
    只能根据 prompt 文本猜测。
  - §15 声称“每个 subagent 在独立目录中运行”，实际所有运行都没有 cwd/env。
- 建议：
  - 由 bridge 暴露父 harness 的工具集合或可重建的 coding tools + env；
  - 将 executor 的 cwd/worktree 真正传给 `LocalExecutionEnv`；
  - 为 worktree 创建、应用 diff、清理增加端到端测试。

### P7R3-03. [x/~] 高：后台结果只入 steer queue，不能保证触发新 turn；生命周期也未关闭

- 设计：§13 `docs/specs/2026-09-22-phase7-extension-api-design.md:476-485`。
- 实现：
  - `WorkflowManager._on_complete()` 完成后只调用
    `self._bridge.send_message(text)`。
  - `Harness._Bridge.send_message()` 只是 append `steer_queue`。
  - 当 workflow 在 agent loop 已结束后完成，idle harness 不会自动 drain queue，
    也不会启动新 turn。
  - `shutdown()` / `cancel_all()` 没有生产调用方；ACP `close_session()` 只 pop
    harness，不关闭 workflow manager。
- 运行复现：
  - 发起 `background=True` 后等待完成：`steer_queue` 长度 `1`、phase `idle`、
    LLM stream 调用次数仍为 `1`。结果没有自动交付。
- 影响：
  - 后台 workflow 的完成消息可能长期滞留，甚至跨 session 生命周期存在；
  - session close 不会取消后台 task。
- 建议：
  - 提供明确的后台完成唤醒 API（而不是普通 steer append）；
  - 将 manager 注册到 session shutdown，至少调用 `cancel_all()`/`shutdown()`。

### P7R3-04. [x] 中：journal 的 phase 哈希不包含隐式当前 phase，可能回放错误结果

- 位置：`packages/pi-dynamic-workflows/pi_dynamic_workflows/runtime.py:188-209`。
- 问题：
  - executor 实际收到 `phase=opts.get("phase") or runtime._current_phase`；
  - 但 journal hash 只使用 `opts.get("phase")`，没有把 `runtime._current_phase`
    纳入 hash。
- 运行复现：
  - 先执行 `phase("A"); agent("same")` 并写入 journal；
  - 再用 `phase("B"); agent("same")` 重放，第二次直接返回 A 的结果，
    executor2 调用次数为 `0`。
- 影响：
  - 不同 phase 的语义请求可能错误命中同一缓存。
- 建议：
  - 构造 hash 时使用 executor 实际接收的全部参数，包括解析后的 phase 和 timeout。

### P7R3-05. [x] 中：`resume_from_run_id` 未校验，允许路径穿越

- 位置：`packages/pi-dynamic-workflows/pi_dynamic_workflows/workflow_tool.py:128-137`。
- 问题：
  - 直接把用户/LLM 提供的字符串拼成
    `~/.pi-python/workflow-journals/<resume_from_run_id>.jsonl`，没有限制为
    12 位 run id。
- 运行复现：
  - `resume_from_run_id="../../escaped-run"` 生成的 path 已离开 journal 目录。
- 影响：
  - 可读取/重放工作目录中其他 JSONL 文件，形成越界输入面。
- 建议：严格校验 run id 格式，并 resolve 后确认路径仍位于 journal 根目录。

### P7R3-06. [x] 中：声明的 64MB journal 上限没有实现

- 设计：`docs/specs/2026-09-22-phase7-extension-api-design.md:495`。
- 实现：`journal.py:20-21` 定义 `MAX_FILE_BYTES`，但 `append()` 只检查
  `MAX_ENTRIES`，从未按文件大小拒绝写入。
- 影响：长结果/大量数据可无限增长，违背设计的安全边界。

### P7R3-07. [x] 中：`background=True` 在没有 manager 时静默退化为前台执行

- 位置：`packages/pi-dynamic-workflows/pi_dynamic_workflows/workflow_tool.py:192-216`。
- 运行复现：
  - `create_workflow_tool(manager=None)` 传入 `background=True`，返回结果
    `terminate=None` 且文本为同步完成，而不是立即返回后台 run id。
- 影响：
  - 扩展初始化失败或使用自定义 bridge 时，显式后台请求会阻塞当前 turn。
- 建议：manager 缺失时应返回明确错误，不能静默改变执行模式。

### P7R3-08. [x] 中：saved workflow 的项目级覆盖方向与实现声明相反

- 位置：`packages/pi-dynamic-workflows/pi_dynamic_workflows/store.py:71-90`。
- 问题：
  - docstring 声称 project 覆盖 user；
  - 实际先扫描 user，再把 project 中同名 workflow 当作重复项跳过。
- 运行复现：
  - 用户目录和项目目录都有 `same`，`scan()` 返回的 source 仍是 `user`。
- 影响：项目级本地工作流无法覆盖用户级版本。

### P7R3-09. [x/~] 中：`WorkflowStore` 原子保存失败路径会二次关闭 fd，并遗留临时文件

- 位置：`packages/pi-dynamic-workflows/pi_dynamic_workflows/store.py:107-126`。
- 问题：
  - `os.replace()` 失败后，`fd` 已在正常路径关闭，except 又调用
    `os.get_inheritable(fd)/os.close(fd)`，可能抛 `Bad file descriptor` 并跳过清理。
  - `dest.exists()` 检查与实际 `os.replace()` 之间存在 TOCTOU，
    并发保存仍可能覆盖已有文件。
- 运行复现：
  - monkeypatch `os.replace` 抛错后，最终异常是 `OSError: [Errno 9] Bad file descriptor`，
    目录残留 `*.py.tmp` 文件。
- 建议：用明确的 fd 所有权和独占创建/原子 no-clobber 机制，确保异常路径清理临时文件。

### P7R3-10. [~] 设计文档内部仍保留已过期状态

- 设计 §9.5 `:366-379` 和 §10.2 `:397-406` 仍写着 background 仅同步、
  worktree 未适配、saved workflow 仅内置 pattern；
- 但 §12–§16 `:461-530` 又统一标为 ✅。
- 这使实现验收标准互相矛盾，需在修复后同步更新文档。

### 三次复核测试缺口

1. 没有首次运行自动创建 journal、再用返回 run_id 恢复的端到端测试。
2. 没有 subagent tools/env/cwd 隔离测试，也没有 `WorktreeManager` 任何测试。
3. 没有真实 harness 下 background 完成后自动触发下一 turn 的测试。
4. 没有 session close 取消后台 workflow 的测试。
5. 没有 journal phase 冲突、64MB 上限和 run id 路径穿越测试。
6. 没有 user/project saved workflow 同名优先级测试，也没有保存失败路径测试。
7. 全量 pytest 通过不等于 lint 通过：当前 `ruff` 仍有 25 项错误。

---

## 一、高影响问题

### P7-01. [x/~] 扩展生命周期事件没有接到实际事件总线

- 二次复核：
  - 核心事件问题已修复。`_handle_agent_event()` 现在在 `_emit_any()` 后调用
    `_emit_hook()`，扩展订阅的核心生命周期事件均能执行。
  - `session_start -> before_agent_start` 映射仍未实现，归入已记录问题 P7-08，
    本轮不重复评级。

- 严重度：高
- 位置：
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:323-326`
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:462-480`
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:621-647`
  - `pi_agent_core/extensions/api.py:93-105`
- 设计承诺：
  - `docs/specs/2026-09-22-phase7-extension-api-design.md:133`：`on()` 委托
    harness `subscribe()` 按类型过滤。
  - 同文档 `:172-183`：`agent_end`、`turn_start`、`turn_end`、`message_start`、
    `message_end`、`tool_execution_start/end` 等都应触发扩展 handler。
- 首次审计行为：
  - 扩展 handler 被放进 `self._hooks`。
  - 核心 agent 事件由 `_emit_any()` 发出，而 `_emit_any()` 只遍历
    `self._subscribers`，不会遍历 `self._hooks`。
  - `_emit_hook()` 才会遍历 `_hooks`，但核心事件路径没有调用它。
- 运行复现：
  - `harness.on("turn_start", handler)` 后执行 prompt，handler 调用次数为 `0`，
    而通过 `harness.subscribe()` 能看到 `turn_start`。
  - 通过扩展注册 `pi.on("session_start", ...)` 后，首个 prompt 的事件回调仍为空。
- 影响：
  - `turn_end` 不触发，导致 `pi-goal-x` 的进度提醒/状态写入完全失效。
  - `agent_end`、所有 `message_*`、`tool_execution_*` 扩展事件同样不会触发。
  - 当前只有 `tool_call` 和 `tool_result` 可用，因为它们分别经过
    `_emit_hook(ToolCallEvent)` 和 `_emit_hook(ToolResultEvent)`。
- 附带的映射缺失：
  - `session_start -> before_agent_start` 的转换没有实现。
  - `_ensure_extensions_loaded()` 也没有按设计
    `docs/specs/2026-09-22-phase7-extension-api-design.md:224` 发射 `session_start`。
- 建议：
  - 让扩展订阅统一走 `subscribe()` 包装层，或在所有事件分发路径同时派发
    `_subscribers` 与 `_hooks`。
  - 明确实现并测试 `session_start -> before_agent_start` 映射。
  - 至少补齐 `turn_end`、`agent_end`、`message_*`、`tool_execution_*` 的端到端测试。

### P7-02. [x] 激活失败的扩展仍会留下并注入部分注册

- 二次复核：已修复。
  - `ExtensionLoader.load_callable()` 在 activate 前 snapshot，失败时 restore。
  - 独立复现同时注册 tool、command、event 后抛错，三者均回到空状态。

- 严重度：高
- 位置：
  - `pi_agent_core/extensions/loader.py:114-121`
  - `pi_agent_core/extensions/loader.py:141-145`
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:305-320`
- 首次审计问题：
  - `activate(api)` 直接向共享 registry 写入 tool/command/event。
  - 如果 activate 在注册若干项后抛异常，registry 没有事务回滚，且该 API 不会加入
    `_apis`。
  - `load_all()` 又用 `contextlib.suppress(Exception)` 吞掉异常。
  - `_ensure_extensions_loaded()` 随后无条件把 registry 中所有工具注入 harness。
- 运行复现：
  - 扩展先 `register_tool("ghost")`，再抛 `RuntimeError`。
  - prompt 后：
    - `"ghost" in extension_registry.get_tools()` -> `True`
    - `"ghost" in harness._tools` -> `True`
  - 即失败的扩展仍对 LLM 可见。
- 影响：
  - 半初始化扩展可污染工具集、active tools、命令和事件注册。
  - 对第三方/用户目录扩展而言，这是加载隔离和失败原子性问题。
- 建议：
  - 为每个扩展使用 staging registry，activate 成功后再提交。
  - 失败时回滚该扩展的全部注册；目录发现失败至少应记录并保持全局无副作用。

### P7-03. [x] `HarnessBridge` 在 activate 之后才绑定，激活期 API 不可用

- 二次复核：已修复。
  - `_ensure_extensions_loaded()` 先创建 bridge，再传给 `load_all()`；
    loader 在调用 activate 前 `_set_bridge()`。
  - 使用真实 `LocalExecutionEnv("D:/workspace-expected")` 时，activate 内
    `pi.cwd` 返回正确路径。

- 严重度：高
- 位置：
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:305-313`
  - `pi_agent_core/extensions/api.py:49-58`
  - `packages/pi-dynamic-workflows/pi_dynamic_workflows/__init__.py:69-75`
- 设计承诺：
  - `cwd`、`get_active_tools()`、`get_all_tools()`、`send_message()`、
    `append_entry()` 等属于 ExtensionAPI 门面。
  - `docs/specs/2026-09-22-phase7-extension-api-design.md:217-223` 的顺序是
    “创建 API -> 调用 activate -> 注入工具/事件”。
- 首次审计行为：
  - `load_all()` 先执行所有 activate，之后才创建 bridge 并调用 `_set_bridge()`。
  - activate 内调用任何依赖 bridge 的 API 都会抛 `RuntimeError`。
- 已产生的实际影响：
  - `pi-dynamic-workflows` 在 activate 中读取 `pi.cwd`，因 bridge 尚未绑定而失败；
    该扩展用 `suppress(Exception)` 降级为 `"."`。
  - 结果是 workflow 工具持有进程当前目录，而不是 `AgentHarness.env.cwd` 对应的
    会话工作目录。
- 运行复现：
  - 使用 `LocalExecutionEnv("D:/workspace-expected")` 时，activate 内读 `pi.cwd`
    得到 `RuntimeError`；同一 API 在 prompt 之后才返回正确路径。
- 影响：
  - 扩展无法在初始化阶段可靠读取 active tools、cwd、session 信息。
  - 当前 workflow 内置功能可能相对错误目录工作。
- 建议：
  - 在调用 activate 前绑定 bridge；若 session metadata 需要异步加载，则先绑定一个
    可安全读取已初始化状态的 bridge。
  - `pi-dynamic-workflows` 不应静默吞掉 `pi.cwd` 错误。

### P7-04. [x] `ExtensionAPI.session_id` 始终返回空字符串

- 三次复核：已完全修复。
  - `_ensure_extensions_loaded()` 在创建 bridge/activate 前读取 metadata 并写入
    `_session_id`，activate 内即可读取真实 id。
  - 实测 activate 阶段返回 `sid-live`，不再依赖首次 `_create_turn_state()`。

- 严重度：高
- 位置：
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:393-395`
  - `pi_agent_core/extensions/api.py:152-155`
- 设计承诺：
  - `docs/specs/2026-09-22-phase7-extension-api-design.md:141`：
    `session_id` 从 session metadata 读取。
- 首次审计行为：
  - bridge 的 `session_id` property 无条件 `return ""`。
- 运行复现：
  - session metadata id 为 `sid-real` 时，`pi.session_id` 仍为 `""`。
- 影响：
  - 任何依赖 session 身份做缓存、持久化、跨进程关联的扩展都会失效。
- 建议：
  - 在 bridge 创建时缓存 metadata id，或在 turn state 生成时更新。

---

## 二、中影响问题

### P7-05. [x] 首次 prompt 后动态加载扩展：handler 不接线，同名工具覆盖无效

- 二次复核：已修复。
  - `load_extension()` 会重新调用 `_apply_extension_registrations()`。
  - 后加载的 `tool_call` handler 实际执行；同名工具运行时 description
    已从 `"old"` 替换为 `"new"`。

- 严重度：中
- 位置：
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:404-416`
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:323-326`
- 首次审计问题：
  - `load_extension()` 在已加载状态下只绑定 bridge，并仅注入“尚未存在”的工具。
  - 它没有把新事件的 registration 追加到 `_hooks`。
  - 当 registry 已按 last-write-wins 替换同名工具时，运行时因
    `if defn.name not in self._tools` 仍保留旧工具。
- 运行复现：
  - 后加载扩展注册 `tool_call` handler，手动派发 `ToolCallEvent`，回调次数为 `0`。
  - 后加载扩展把 `shadow` 从 `"old"` 覆盖为 `"new"` 后：
    - registry description = `"new"`
    - `harness._tools["shadow"].description` = `"old"`
    - `get_all_tools()` 却把旧工具标为 `source="extension"`。
- 影响：
  - 动态插件生命周期不可用，且 introspection 与执行实体不一致。
- 建议：
  - 统一走一个 `apply_registration()` 事务，负责 bridge、工具替换、事件接线。

### P7-06. [ ] 已接线 handler 的 unsubscribe 不生效；后注册的 `on()` 完全不生效

- 严重度：中
- 位置：
  - `pi_agent_core/extensions/api.py:100-105`
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:323-326`
- 问题：
  - 首次加载时 harness 把 handler 从 registry “复制”到 `_hooks`。
  - 返回的 unsubscribe 只从 registry 删除 registration，不会从 `_hooks` 删除。
  - 首次加载后通过 `api.on()` 新增的 handler 只进 registry，不会再进入 `_hooks`。
- 运行复现：
  - 对已接线的 `tool_call` handler 调用 unsubscribe，再派发事件，回调仍然执行。
  - 后加载扩展注册的 `tool_call` handler 从未执行。
- 影响：
  - `Unsubscribe` 返回值违反契约；动态订阅语义双向不成立。
- 建议：
  - unsubscribe 必须操作实际运行时的订阅句柄，而不是只操作历史 registry。

### P7-07. [x] `register_command()` 是死端：没有任何运行时消费方

- 二次复核：**已修复**。
  - **Harness 层**：`AgentHarness.dispatch_command(name, args)` 查找 registry、
    执行 handler、从 steer_queue 中捕获 `send_message()` 输出并返回。
    `prompt()` 在进入 LLM turn 之前调用 `_try_slash_dispatch()`；匹配到的
    命令发射完整事件序列 (`agent_start` … `agent_end`) 使 subscriber 看到规范 turn。
  - **ACP 层**：`PiAcpAgent._advertise_commands()` 将 registry 中的命令转为
    `AvailableCommandsUpdate` 发送给 TUI/Zed 客户端，填充 slash 自动补全。
    `_bind_session()` 在 `load_extensions()` 之后即时调用。
  - **E2E 链路**：TUI 用户输入 `/goal test` → TUI resolve 不匹配内置 →
    `CommandResult::PassThrough("/goal test")` → ACP `prompt()` →
    `harness.prompt("/goal test")` → `_try_slash_dispatch` →
    `dispatch_command("goal", "test")` → handler 执行 →
    `pi.send_message(...)` 被捕获 → 合成 `AssistantMessage` + 事件序列 →
    ACP `session_update` → TUI 显示。
  - `/goal`、`/workflows` 以及五个内置 workflow 命令现已可执行。
  - 新增 12 个测试覆盖：harness dispatch（9）+ ACP 端到端（3）。
  - 全量测试 **515 passed, 11 skipped**。
- 严重度：中
- 原始位置：
  - `pi_agent_core/extensions/api.py:74-89`
  - `pi_agent_core/extensions/registry.py:45-54`
- 原始问题：
  - 命令只被保存，没有 Python 侧 dispatch API，也没有 CLI/TUI 读取该 registry。

### P7-08. [ ] `pi-goal-x` 的 session 恢复是空实现，且 API 缺少读取入口

- 严重度：中
- 位置：
  - `packages/pi-goal-x/pi_goal_x/__init__.py:59-61`
  - `packages/pi-goal-x/pi_goal_x/__init__.py:77-78`
  - `docs/specs/2026-09-22-phase7-extension-api-design.md:267`
  - `docs/specs/2026-09-22-phase7-extension-api-design.md:318`
- 问题：
  - `_restore_goal_state()` 只有 `pass`，没有读取 session entries，也没有恢复 state。
  - 即使 P7-01 修好，恢复仍然不会发生。
  - ExtensionAPI 当前没有读取 session entries 的方法；`BeforeAgentStartEvent`
    也不携带 entries，因此按现有接口无法实现设计承诺的恢复逻辑。
- 测试缺口：
  - `packages/pi-goal-x/tests/test_goal_x.py:194-196` 只检查 registry 中存在
    `"session_start"` handler，不验证行为。
- 影响：
  - 重启/恢复 session 后 goal 状态丢失，与包 docstring 和设计不一致。
- 建议：
  - 增加只读 session entry API，或在 session start 事件 payload 中提供 entries；
    然后实现恢复和 round-trip 测试。

### P7-09. [x] Dynamic Workflows 的 `max_agents` 可被并发绕过

- 二次复核：已修复。
  - 检查、budget gate 和计数预留已放入 `_count_lock`。
  - 并发复现未再超过 `max_agents`，新增并发回归测试通过。

- 严重度：中
- 位置：
  - `packages/pi-dynamic-workflows/pi_dynamic_workflows/runtime.py:167-188`
- 问题：
  - `_agent_count >= max_agents` 检查发生在获取 semaphore 之前，计数递增发生在
    semaphore 内部。
  - 当并发数小于 max_agents 时，多个等待任务可以先通过同一个旧计数的检查，随后
    依次递增并超限。
- 运行复现：
  - 配置 `max_agents=2, concurrency=1`，通过 `parallel()` 发起 3 个 agent 调用。
  - 实际完成 `agent_count=3`，未触发 agent limit。
- 影响：
  - 人为设置的安全/成本上限不可靠；并发 fan-out 可能产生额外调用。
- 现有测试缺口：
  - `packages/pi-dynamic-workflows/tests/test_dynamic_workflows.py:213-224`
    只覆盖串行 `await agent(...)`，无法触发该竞争窗口。
- 建议：
  - 在 semaphore 内原子检查并递增，或使用锁保护“检查 + 预留名额”。

### P7-10. [x/~] 工具状态更新绕过 `AgentHarness.set_tools()/set_active_tools()`

- 二次复核：名称校验已修复；其余部分仍是已记录残留。
  - `set_active_tool_names()` 会拒绝未知工具。
  - 仍直接写 `active_tool_names`，未触发 `ToolsUpdateEvent`，也未走 session
    active-tools 持久化；该项不纳入本轮高影响验收。

- 严重度：中
- 位置：
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:333-345`
  - `packages/pi-agent-harness/pi_agent_harness/agent_harness.py:964-992`
  - 设计 `docs/specs/2026-09-22-phase7-extension-api-design.md:131,135`
- 问题：
  - `inject_tool` 直接改写 `_tools` 和 `active_tool_names`。
  - `set_active_tool_names` 直接赋值，不调用 harness 的同名方法。
  - 因而缺少：
    - unknown tool 校验；
    - `ToolsUpdateEvent`；
    - active tools change 的 session 持久化；
    - `set_tools()` 的共享一致性语义。
- 运行复现：
  - `pi.set_active_tools(["missing-tool"])` 不抛错；
  - `harness.active_tool_names` 变成 `["missing-tool"]`。
- 影响：
  - session 回放/订阅方看不到真实工具状态，非法名字被静默接受。
- 建议：
  - 同步 ExtensionAPI 若要调用异步 harness 方法，应明确调度/阻塞语义；
    不能以直接字段赋值代替。

### P7-11. [ ] 同名扩展“后加载覆盖”的语义与设计相反，并按模块名误判重复

- 严重度：中
- 位置：
  - `pi_agent_core/extensions/loader.py:107-112`
  - 设计 `docs/specs/2026-09-22-phase7-extension-api-design.md:202-205`
- 设计承诺：
  - 加载顺序为 entry point -> 用户目录 -> 项目目录 -> 编程注入；
    同名扩展后加载者覆盖先加载者。
- 实际行为：
  - `load_callable()` 遇到同名扩展直接返回第一次创建的 API，后续 activate 根本不执行。
  - 未显式传 `name` 时，扩展身份取 `activate.__module__`；同一模块中的两个不同
    programmatic activate 会被误判为同一扩展。
- 影响：
  - 无法通过后加载覆盖 project/user/entry point 扩展。
  - 测试与调用方容易误以为“已加载”，实际注册/激活被吞掉。
- 建议：
  - 用调用方声明的扩展名作为身份，并定义覆盖时的卸载/替换流程。

---

## 三、低影响与契约偏差

### P7-12. [~] `auto_discover_extensions` 默认值与设计不一致

- 严重度：低
- 设计：`docs/specs/2026-09-22-phase7-extension-api-design.md:214` 为 `True`。
- 实现：`agent_harness.py:229` 为 `False`。
- 说明：
  - CLI/TUI 的 `factory.py:159` 显式传 `True`，主产品路径不受影响。
  - 直接构造 `AgentHarness` 的嵌入式用户行为与设计不同。
- 建议：要么改默认值，要么更新设计明确仅 CLI 启用。

### P7-13. [~] 编程加载 API 与设计名称/接受类型不一致

- 严重度：低
- 设计：`docs/specs/2026-09-22-phase7-extension-api-design.md:199` 为
  `ExtensionLoader.load(module_or_callable)`。
- 实现：只有 `load_callable(activate_fn)`，不接受 module 对象，也没有 `load()` 别名。
- 影响：
  - 设计中的 module-or-callable 编程注入无法按文档直接使用。

### P7-14. [~] `tool_result` 并非只读

- 严重度：低
- 设计：`docs/specs/2026-09-22-phase7-extension-api-design.md:175` 标注只读。
- 实现：`packages/pi-agent-harness/pi_agent_harness/agent_harness.py:754-779`
  会把 handler 非空返回值解释为
  `AfterToolCallResult`，允许修改 content/details/is_error/terminate。
- 说明：
  - 这是已有 harness 能力的自然暴露，不一定是缺陷；但属于设计契约偏差。

### P7-15. [~] `web_search` 文本格式未按设计示例输出

- 严重度：低
- 设计：`docs/specs/2026-09-22-phase7-extension-api-design.md:238` 为
  `title: url\nsnippet`。
- 实现：`packages/pi-web-access/pi_web_access/web_search.py:110-119` 输出编号列表
  和缩进 URL/snippet。
- 说明：
  - 信息完整，测试也按当前格式验证；只有格式契约不一致。

---

## 四、首次审计设计条款对照（历史）

| 设计条款 | 状态 | 结论 |
|---|---|---|
| `register_tool()` 注册并进入 LLM | 部分 | 首次批量注入可用；失败扩展残留、动态覆盖失效、绕过 `set_tools()` |
| `register_command()` | `[ ]` | 仅存 registry，无运行时消费 |
| `on()` 委托 subscribe 按类型过滤 | `[ ]` | 核心事件未派发；仅部分 `_emit_hook` 事件可用 |
| `get_active_tools()` | 基本可用 | 读取 bridge 当前字段 |
| `set_active_tools()` | 偏差 | 绕过校验、事件、session 持久化 |
| `get_all_tools()` | 部分 | 元数据可能描述 registry 新定义，而执行仍用旧工具 |
| `send_message()` | 可用 | 写入 steer queue |
| `append_entry()` | 部分 | 进入 pending session writes，按现有 flush 时序落盘 |
| `exec()` | 可用 | 转发 `ExecutionEnv.exec()` |
| `cwd` | 部分 | bridge 绑定后可读；activate 期不可用，已影响 workflow |
| `session_id` | `[ ]` | 始终 `""` |
| `session_start -> before_agent_start` | `[ ]` | 无映射、无发射 |
| entry point / 用户目录 / 项目目录 | 部分 | 路径已实现；同名覆盖语义错误 |
| 加载失败隔离 | `[ ]` | 无事务回滚，部分注册会生效 |
| `/goal` 状态恢复 | `[ ]` | 空实现，且当前 API 缺少读取 session entries 的能力 |
| 5 个 workflow pattern / token budget | 基本可用 | `max_agents` 并发上限可绕过 |

---

## 五、首次审计测试缺口（保留）

现有 Phase 7 测试主要验证 registry 数据结构和 tool definition 转换，未覆盖以下关键
运行时契约：

1. 扩展 `on("turn_end")`、`agent_end`、`message_*`、`tool_execution_*` 是否执行。
2. `session_start` 映射与 goal state 恢复。
3. 已接线 handler 的 unsubscribe。
4. 首次 prompt 后 `load_extension()` 的事件接线与工具覆盖。
5. activate 内部调用 `pi.cwd/session_id/get_active_tools()`。
6. activate 抛错后的事务回滚。
7. `set_active_tools()` 的未知工具校验、事件和 session 持久化。
8. `register_command()` 的实际 dispatch，而不只是 registry key。
9. workflow agent cap 的并发测试。
10. 同名扩展按顺序覆盖或明确拒绝的测试。

首次审计建议至少为 P7-01～P7-10 各增加一个失败前会失败、修复后会通过的行为测试。
修复后新增测试已覆盖 P7-01～P7-05、P7-09 和 P7-10 的名称校验路径；其余已记录问题
仍按本节缺口保留。

---

## 六、最终判断

- **上一轮高影响修复：仍通过。** P7-01～P7-04 的主路径未出现回归。
- **第三次复核新增高影响问题：3 项。** P7R3-01（journal/resume 不可用）、
  P7R3-02（真实 subagent 无 tools/env/cwd，worktree 未接入）、
  P7R3-03（后台结果不能可靠触发新 turn，且 session close 不回收任务）。
- **设计一致性：未通过。** §12–§16 标记为 ✅，但 `worktree`、`resume`、后台交付
  均未达到设计描述；同时 §10.2 与后续章节状态互相矛盾。
- **测试状态（第三次快照）：** 当时全量 `540 passed, 11 skipped`；
  `ruff check` 当时为 25 errors。该状态已被第九节第四次复核覆盖。
- **当前最终判定：不接受 Phase 7 新增 §12–§16 为完成状态。**
  必须优先修复 P7R3-01～P7R3-03；P7R3-04～P7R3-09 属于下一批中等风险修复项。

---

## 七、P7R3-01～03 修复记录（2026-09-24）

### P7R3-01 修复

- `workflow_tool.py`：每次 run 在 tool 层分配稳定 `run_id`（`uuid.uuid4().hex[:12]`），
  立即创建 Journal 文件（不再等 resume 时才创建）。同一 `run_id` 传入
  `runtime.execute()`。`resume_from_run_id` 增加格式校验防止路径遍历。
- `runtime.py`：`execute()` 接受可选 `run_id` 参数，不再内部自行生成。

### P7R3-02 修复

- `subagent.py`：`run_agent()` 创建 `LocalExecutionEnv(effective_cwd)` 和
  `create_all_tools(effective_cwd)`，传入 AgentHarness 的 `env=` 和 `tools=`。
  subagent 现在可以读文件、写文件、执行 bash 等。
- **仍未修复：** `WorktreeManager` 仍无生产调用方，`WorkflowParams` 也没有
  isolation 参数；§15 “每个 subagent 在独立 worktree 中运行”仍未实现。

### P7R3-03 修复

- `_harness_bridge.py`：协议新增 `trigger_prompt(text)` 方法。语义：
  harness 空闲时触发 `prompt(text)`，忙碌时降级为 `steer_queue.append()`。
- `agent_harness.py`：`_Bridge` 实现 `trigger_prompt`。
- `manager.py`：`_on_complete()` 改用 `trigger_prompt()` 替代 `send_message()`，
  后台 workflow 结果可以在 harness 空闲时自动触发新 turn。
  `start_background()` 改为接收调用方传入的 `run_id`（不再自行生成）。
- `__init__.py`：`activate()` 在 manager 存在时注册 `agent_end` hook，
  session 结束时调用 `manager.shutdown()` 回收所有后台任务。
- **新增回归：** `agent_end` 是每个 agent run 结束时触发，不是 session 结束。
  该 hook 会在后台 workflow 启动所在 turn 结束时调用 `shutdown()`；默认
  30 秒后会取消仍未完成的后台任务，违背“长时间 workflow 不阻塞会话”的设计。

**验证：全量 `540 passed, 11 skipped`。**

---

## 八、P7R3-04～09 修复记录（2026-09-24）

### P7R3-04 修复

- `runtime.py`：journal hash 现在使用解析后的实际参数
  (`resolved_phase = opts.get("phase") or runtime._current_phase`，
  `resolved_cwd = opts.get("cwd") or runtime.cwd`) 而非 opts 原始值。
  不同隐式 phase 下的同 prompt 不再错误命中缓存。
- 新增测试 `TestJournalPhaseHash` 验证隐式 phase 隔离和同 phase 回放。

### P7R3-05 修复

- 已在 P7R3-01 中附带修复：`_RUN_ID_RE = r"^[a-zA-Z0-9_-]{1,128}$"` 格式校验，
  `../../escaped-run` 等路径穿越格式被拒绝。
- 新增测试 `TestRunIdValidation`。

### P7R3-06 修复

- `journal.py`：`append()` 在写入前检查 `self._path.stat().st_size + line_bytes`
  是否超过 `MAX_FILE_BYTES` (64MB)，超出时跳过写入并记 warning。
- 新增测试 `TestJournalFileLimit`。

### P7R3-07 修复

- `workflow_tool.py`：`background=True` 且 `manager is None` 时返回明确错误文本
  而非静默退化为前台执行。
- 新增测试 `TestBackgroundNoManager`。

### P7R3-08 修复

- `store.py`：`scan()` 扫描顺序从 `[user, project]` 改为
  `[project, user]`，项目级同名 workflow 优先。与 docstring 声明一致。
- 新增测试 `TestWorkflowStorePriority`。

### P7R3-09 修复

- `store.py`：用 `fd_closed` 标志跟踪 fd 所有权，避免 `os.replace()` 失败后
  对已关闭 fd 重复 `os.close()`。使用 `contextlib.suppress(OSError)` 包裹
  清理操作，确保临时文件被删除。
- 新增测试 `TestWorkflowStoreSaveFailure`。
- **仍不完整：** `dest.exists()` 与 `os.replace()` 之间仍有 TOCTOU。
  并发保存同一名称时，两边都可能通过 existence check，后写入者覆盖前者，
  不满足 no-clobber 语义。

### Ruff 修复

- 消除全部 39 个 ruff 错误（E402、RUF006、SIM105、E501、F841）：
  - `agent.py`：`logger` 移到所有 import 之后；`create_task` 引用存入
    `_background_tasks` 集合。
  - `subagent.py`：长行拆分；`try-except-pass` 改为 `contextlib.suppress`。
  - `store.py`/`manager.py`：同上。
  - `runtime.py`/`worktree.py`/测试文件：长行拆分。
- `ruff check .` 和 `ruff format --check .` 均通过。

**验证：Windows `548 passed, 11 skipped`；WSL `544 passed, 4 skipped`。**

---

## 九、第四次复核（2026-09-24）

### 复核结论

“所有高影响和中影响问题已经修复”这一结论**尚未成立**。

已确认关闭：

- P7R3-01：首次 run 现在立即创建 journal；返回的 run id 可命中重放。
- P7R3-04：journal hash 使用解析后的 phase/cwd/timeout。
- P7R3-05：run id 格式校验阻止路径穿越。
- P7R3-06：64MB journal 文件上限已生效。
- P7R3-07：缺少 manager 时 `background=True` 返回明确错误。
- P7R3-08：project saved workflow 覆盖 user 同名项。
- P7R3-09 的一部分：fd 失败路径已避免二次关闭并清理临时文件。
- P7R3-02 的一部分：subagent 已接入 7 个 coding tools、LocalExecutionEnv 与 cwd。
- P7R3-03 的一部分：后台完成时 `trigger_prompt()` 能在 idle 状态启动新 turn。
- Ruff 已清零。

仍未关闭/仍有回归：

### P7R4-01. [x] 高：后台 workflow 会在 agent_end 后被取消

- 位置：`packages/pi-dynamic-workflows/pi_dynamic_workflows/__init__.py:131-141`
  、`manager.py:113-123`。
- `activate()` 把 `manager.shutdown()` 注册到扩展的 `agent_end` handler；
  `agent_end` 每个 agent run 都会触发，而设计意图是 session 生命周期结束才回收。
- 后台 workflow 通常在启动它的 turn 结束时仍在运行，因此 `shutdown()` 会等待
  默认 30 秒，然后取消任务。实测缩短 timeout 的等价复现：
  `shutdown_calls=1`、后台 workflow 被取消、没有触发结果 prompt。
- 影响：长于 30 秒的后台 workflow 必然失败，不能完成 §13 的 long-running
  background 设计目标。
- 建议：不要在 `agent_end` 调用 shutdown；注册真正的 session-close/shutdown
  hook，或让 manager 由 session owner 显式持有并回收。

### P7R4-02. [x/~] 高：worktree isolation 仍未接入

- 位置：`packages/pi-dynamic-workflows/pi_dynamic_workflows/worktree.py`
  与 `subagent.py`；全仓没有 `WorktreeManager` 生产调用方。
- `WorkflowParams` 没有 isolation/worktree 配置，subagent 的 `cwd=` 只传给
  `LocalExecutionEnv`，不会创建 linked worktree、不会 collect/apply/cleanup。
- 这仍未满足设计 §15 `:503-514` 的“每个 subagent 在独立 worktree 中运行”。

### P7R4-03. [x/~] 中：历史 P7-06 unsubscribe 仍不生效

- 独立复现：handler 接线后调用 unsubscribe，再派发两次 `tool_call`，结果仍为
  `['x', 'y']`。
- `ExtensionAPI.on()` 只修改 registry，harness 已复制的 `_hooks` 不会被移除。

### P7R4-04. [x/~] 中：历史 P7-08 session_start 恢复仍未实现

- `pi-goal-x` 的 `_restore_goal_state()` 仍是 `pass`。
- `pi.on("session_start")` 仍没有映射到 `before_agent_start`；运行 prompt 后
  session_start callback 次数为 `0`。

### P7R4-05. [x] 中：历史 P7-10 工具状态事件/持久化仍缺失

- 独立复现：调用 `set_active_tools([])` 后 `tools_update` 事件数 `0`，
  session entries delta 为 `0`；bridge 仍直接改 `active_tool_names`。

### P7R4-06. [x/~] 中：历史 P7-11 同名扩展覆盖仍未实现

- `load_callable(name="same")` 第二次调用仍直接返回第一次 API，
  实测 activate 顺序为 `['a']`、API 数量 `1`；后续扩展没有覆盖先加载者。

### P7R4-07. [x] 中：WorkflowStore no-clobber 竞态（原问题）

- `_save()` 仍是 `dest.exists()` 后再 `os.replace()`。
- 并发保存同名 workflow 时，两次都存在通过 existence check 的窗口；后一次
  `replace` 可覆盖先一次结果，不能严格保证 no-clobber。

### 验证结果

- `ruff check`：通过。
- 使用临时 `USERPROFILE/HOME`：全量 `548 passed, 11 skipped`。
- 默认受控环境直接运行全量：`546 passed, 2 failed`；失败原因是测试未隔离
  `Path.home()`，尝试写真实用户目录 `C:\Users\...\.pi-python\workflow-journals`
  被权限拒绝。此问题本身属于测试隔离缺陷。

**第四次最终判定：不能接受“所有高影响和中影响问题已修复”。**
至少必须先关闭 P7R4-01 / P7R4-02 两个高影响项；P7R4-03～P7R4-07 仍是明确的
中影响残留。

---

## 十、P7R4-01～02 修复记录（2026-09-24）

### P7R4-01 修复：后台 workflow 不再被 agent_end 取消

- **根因**：`activate()` 把 `manager.shutdown()` 注册到 `agent_end` hook，
  但 `agent_end` 每次 `prompt()` 结束都触发，不是 session 关闭。后台 workflow
  在启动 turn 结束后 30s 被强制取消。
- **修复**：
  - `_harness_bridge.py`：协议新增 `register_cleanup(callback)` 方法，用于
    注册 session-close 级别的异步清理回调。
  - `agent_harness.py`：
    - `_cleanup_callbacks: list` 存储回调。
    - `_Bridge.register_cleanup()` 实现注册。
    - 新增 `async close()` 方法，依次调用所有 cleanup callback。
  - `__init__.py`：移除 `agent_end` hook，改用
    `bridge.register_cleanup(manager.shutdown)`。
  - `agent.py`（ACP agent）：`close_session()` 在 pop harness 前调用
    `await harness.close()`。
- **效果**：后台 workflow 在 turn 结束后继续运行，只在 session 关闭时才 shutdown。

### P7R4-02 修复：worktree isolation 接入

- **根因**：`WorktreeManager` 存在但无调用方；`WorkflowParams` 无 isolation 参数；
  subagent 直接使用 `LocalExecutionEnv(cwd)`，不创建隔离 worktree。
- **修复**：
  - `workflow_tool.py`：
    - `WorkflowParams` 新增 `isolation: bool = False` 参数。
    - `isolation=True` 时创建 `WorktreeManager(cwd)` 并注入新的
      `HarnessSubagentExecutor` 实例。
    - 前台 run 完成后 `wt_mgr.cleanup_all()` 清理所有 worktree。
  - `subagent.py`：
    - `HarnessSubagentExecutor.__init__` 接受可选 `worktree_manager`。
    - `run_agent()` 在执行前 `worktree_manager.create()` 创建隔离 worktree，
      subagent 在 worktree 目录中运行。
    - 执行完成后 `worktree_manager.cleanup(wt_path)` 清理。
    - 创建/清理失败时 graceful fallback（不中断执行）。
- **效果**：`workflow(isolation=True)` 的每个 subagent 在独立 git worktree 中运行，
  满足设计 §15 的隔离要求。非 git 项目或未指定 `isolation` 时行为不变。
- **五次复核仍不完整：** 全仓没有 `collect_diff()` / `apply_changes()` 调用。
  subagent 在 worktree 中的文件修改会在 cleanup 时直接丢弃；同时 worktree
  创建失败会被静默忽略并回退到共享 cwd。

**验证：Windows `548 passed, 11 skipped`；WSL `544 passed, 4 skipped`；
ruff check + format 通过。**

---

## 十一、P7R4-03～07 修复记录（2026-09-24）

### P7R4-03 修复：unsubscribe 从 harness hooks 中移除 handler

- **根因**：`ExtensionAPI.on()` 的 unsubscribe 仅从 `ExtensionRegistry` 移除
  handler，但 `_apply_extension_registrations()` 已将 handler 复制到
  `AgentHarness._hooks`。registry 的变更不反映到运行时 hooks。
- **修复**：
  - `_harness_bridge.py`：协议新增 `remove_hook(event, handler)` 方法。
  - `agent_harness.py`：`_Bridge.remove_hook()` 从 `harness._hooks` 移除。
  - `api.py`：`on()` 的 unsubscribe 除了 registry 移除，还调用
    `bridge.remove_hook(event, handler)`。
- **测试**：`TestUnsubscribeRemovesHook` — 验证 unsubscribe 调用 bridge、
  真实 harness 中 handler 不再在 hooks 中。
- **五次复核仍不完整：** 首次加载后再调用 `ExtensionAPI.on()`，handler 仍只进
  registry、不进入运行时 `_hooks`，因此动态订阅依然不会触发。

### P7R4-04 修复：session_start 事件在扩展加载后触发

- **根因**：无 `session_start` hook 事件发射；扩展注册
  `pi.on("session_start", handler)` 后 handler 从未被调用。
- **修复**：
  - `agent_harness.py`：`_ensure_extensions_loaded()` 完成加载和注册后，
    调用 `_emit_hook_simple("session_start")`。
  - 新增 `_emit_hook_simple(event_name)` 辅助方法，用简单 dict 触发 hooks。
- **测试**：`TestSessionStartEvent` — 验证 `session_start` handler 在第一次
  prompt 时被调用且事件 type 正确。
- **五次复核仍不完整：** `pi-goal-x` 的 `_restore_goal_state()` 仍是 `pass`；
  触发 session_start 事件并没有实现 goal state 恢复。

### P7R4-05 修复：set_active_tools 触发事件和持久化

- **根因**：`_Bridge.set_active_tool_names()` 仅修改
  `harness.active_tool_names` 列表，不触发 `ToolsUpdateEvent`，不队列 session
  write。
- **修复**：
  - `agent_harness.py`：`_Bridge.set_active_tool_names()` 现在：
    1. 记录 `previous_active` 状态
    2. 队列 `pending_session_writes` 条目 (`active_tools_change`)
    3. 通过 `asyncio.create_task` 调度 `ToolsUpdateEvent` 发射
- **测试**：`TestSetActiveToolsEvent` — 验证调用后 `pending_session_writes`
  包含 `active_tools_change` 条目。

### P7R4-06 修复：同名扩展后加载者覆盖先加载者

- **根因**：`ExtensionLoader.load_callable()` 检测到同名时直接返回旧 API，
  不执行新的 `activate()`。
- **修复**：
  - `registry.py`：
    - 新增 `_tool_owners: dict[str, str]` 追踪每个 tool 的归属扩展。
    - `add_tool()` 记录 `extension_name`。
    - 新增 `remove_by_extension(name)` 方法，清除指定扩展的所有注册。
    - `snapshot()`/`restore()` 包含 `_tool_owners`。
  - `loader.py`：同名时调用 `remove_by_extension()` 清除旧注册，
    从 `_apis` 和 `_loaded_names` 移除旧 API，然后正常加载新扩展。
- **测试**：`TestSameNameOverride` — 加载 ext-a(tool-a, cmd-a) 后加载
  ext-b(tool-b, cmd-b) 同名 "same"，验证仅 ext-b 注册存在。
- **五次复核仍不完整：** registry 已覆盖，但 harness 运行时仍保留旧 tool 和旧
  hook；同名替换 activate 失败时，旧扩展也已从 registry 删除，无法回滚。

### P7R4-07 修复：WorkflowStore no-clobber 原子化

- **根因**：`_save()` 用 `dest.exists()` 检查后 `os.replace()`，存在
  TOCTOU 竞态窗口——两个并发写入都可通过 exists 检查。
- **修复**：
  - `store.py`：用 `os.link(tmp, dest)` 替代 `dest.exists()` +
    `os.replace()`。`os.link` 是原子操作，目标存在时直接抛出
    `OSError`；捕获后检查 `dest.exists()` 转为 `FileExistsError`。
    temp 文件在 `finally` 中清理。
- **测试**：`TestWorkflowStoreNoClobberAtomic` — 先保存再重复保存同名，
  验证抛出 `FileExistsError` 且内容不变。

**验证：Windows `554 passed, 11 skipped`；WSL `550 passed, 4 skipped`；
ruff check + format 通过。**

---

## 十二、第五次复核（2026-09-24）

### 复核结论

P7R4-01、P7R4-05、P7R4-07 已关闭。P7R4-02、P7R4-03、P7R4-04、P7R4-06
仍只完成了主修复的一部分，因此不能判定全部关闭。

### P7R5-01. [x/~] 高：worktree 中的修改不会回写，隔离结果被丢弃

- 位置：
  - `packages/pi-dynamic-workflows/pi_dynamic_workflows/subagent.py:110-180`
  - `packages/pi-dynamic-workflows/pi_dynamic_workflows/workflow_tool.py:209-278`
  - `packages/pi-dynamic-workflows/pi_dynamic_workflows/worktree.py:93-127`
- 现状：
  - `isolation=True` 已能创建 worktree，并在隔离目录创建 env/tools。
  - 但执行结束后直接调用 `cleanup(wt_path)`；全仓没有
    `collect_diff()` 或 `apply_changes()` 调用。
- 影响：
  - subagent 在 worktree 中完成代码修改后，修改随 worktree 一起删除；
    §15 声明的 “collect_diff + apply_changes + cleanup” 链路没有完成。
  - worktree 创建失败时静默回到共享 cwd，调用方无法知道 isolation 实际失效。
- 建议：
  - 明确区分只读隔离与可写隔离；可写模式必须在 cleanup 前 collect/apply；
  - 需要冲突检测与失败处理，不能静默丢弃修改。

### P7R5-02. [x] 中：首次加载后动态 `on()` 仍不会进入运行时

- 位置：`pi_agent_core/extensions/api.py:102-116`。
- 现状：
  - unsubscribe 已调用 `bridge.remove_hook()`，这一半已修复。
  - 但 `on()` 仍只调用 `registry.add_event_handler()`；harness 已加载时不会把
    handler 加入 `_hooks`。
- 独立复现：首次 prompt 后执行 `api.on("tool_call", handler)`，再派发 tool_call，
  handler 调用次数为 `0`。
- 影响：扩展运行期动态订阅仍不可用。
- 建议：`on()` 在 bridge 存在且 `_loading == False` 时同步注册到 live hooks。

### P7R5-03. [x] 中：session_start 已触发，但 goal restore 仍是空实现

- 位置：`packages/pi-goal-x/pi_goal_x/__init__.py:59-61`。
- 现状：
  - `_ensure_extensions_loaded()` 已发射 `session_start`，handler 能收到
    `{"type": "session_start"}`。
  - `_restore_goal_state()` 仍是 `pass`，没有读取 `CustomEntry`。
- 独立复现：预置 `goal_state` entry 后加载扩展并 prompt，再调用 `goal_update`，
  返回 `No active goal`。
- 影响：session 恢复后目标状态仍丢失。
- 建议：增加只读 session entry API 或在事件 payload 提供 entries，并实现恢复测试。

### P7R5-04. [x/~] 中：同名覆盖只清 registry，不清理 live runtime；失败替换也无法回滚

- 位置：`pi_agent_core/extensions/loader.py:113-137`。
- 独立复现：
  - 动态加载旧扩展（`old_tool` + hook），再加载同名新扩展（`new_tool`）。
  - registry 只剩 `new_tool`，但 `harness._tools` 同时存在
    `old_tool/new_tool`，`tool_call` hooks 数量为 2。
  - 同名替换 activate 抛错后，旧扩展 registry 已为空，无法回滚恢复。
- 影响：
  - 被覆盖扩展仍可通过旧工具/handler 影响运行时；
  - 替换失败会让原扩展也失效。
- 建议：
  - 先 snapshot 旧扩展完整状态，再移除；成功后再清理/替换 live tools/hooks；
    失败时恢复旧扩展和原 cleanup 回调。

### 已验证修复

- P7R4-01：`agent_end` shutdown 已移除，改为 `AgentHarness.close()` cleanup；
  ACP `close_session()` 会 await `harness.close()`。
- P7R4-05：`set_active_tools()` 会追加 `active_tools_change` session write，
  并异步发射 `tools_update`。
- P7R4-07：`os.link(tmp, dest)` 提供原子 no-clobber，temp 在 finally 清理。

### 验证结果

- `ruff check`：通过。
- 临时 `USERPROFILE/HOME` 下全量：`554 passed, 11 skipped`。
- 默认受控环境运行 workflow 包：2 failed + 14 setup errors，原因是真实
  HOME/TEMP 不可写；测试仍缺少统一 home/tmp 隔离。

**第五次最终判定：P7R4-01、05、07 通过；P7R4-02/03/04/06 不能判定为完整修复。**

---

## 十三、P7R5-01～04 修复记录（2026-09-24）

### P7R5-01 修复：worktree collect_diff + apply_changes 链路补全

- **根因**：`subagent.py` 在 worktree 中执行后直接 `cleanup()`，未调用
  `collect_diff()` / `apply_changes()`，subagent 的文件修改随 worktree 删除。
  worktree 创建失败时静默回退到共享 cwd，调用方无法感知隔离失效。
- **修复**：
  - `subagent.py`：`run_agent()` 在 `cleanup()` 之前调用
    `worktree_manager.apply_changes(wt_path)`，将变更 diff 回写到源 cwd。
    失败时 `logger.warning()` 记录且不中断执行。
  - worktree 创建失败时，不再静默回退；直接返回
    `AgentResult(error="worktree creation failed: ...")` 让调用方明确知晓。
- **测试**：通过现有 HarnessSubagentExecutor 测试验证（无 worktree_manager
  时路径不变）。
- **六次复核仍不完整：** 默认 snapshot 模式会把源 cwd 既有 dirty diff 复制到
  worktree；`collect_diff()` 再次返回该 diff 及 agent 修改。对已经 dirty 的源
  cwd 执行 `git apply` 会失败，随后 cleanup 删除 worktree，agent 修改仍丢失。

### P7R5-02 修复：动态 on() 注入到 live harness hooks

- **根因**：`on()` 只写 `registry.add_event_handler()`；加载完成后
  (`_loading=False`)，handler 不进入 harness 运行时 `_hooks`。
- **修复**：
  - `_harness_bridge.py`：协议新增 `add_hook(event, handler)` 方法。
  - `agent_harness.py`：`_Bridge.add_hook()` 调用
    `harness._hooks.setdefault(event, []).append(handler)`。
  - `api.py`：`on()` 在 `_loading == False` 且 bridge 存在时，同步调用
    `bridge.add_hook(event, handler)` 注入到 live hooks。
- **测试**：`TestDynamicOnEntersLiveHooks` — 首次 prompt 后动态 `on()`，
  验证 handler 出现在 `harness._hooks` 中；unsubscribe 后消失。

### P7R5-03 修复：pi-goal-x 目标恢复实现

- **根因**：`_restore_goal_state()` 仍是 `pass`，无法从 session entries 恢复。
  extension 缺少读取 session entries 的 API。
- **修复**：
  - `_harness_bridge.py`：协议新增 `get_custom_entries(custom_type)` 方法。
  - `agent_harness.py`：
    - `_cached_custom_entries` 缓存在 `_ensure_extensions_loaded()` 中
      填充（`session.get_entries()` 过滤 type=="custom"），
      于 `session_start` 事件触发前完成。
    - `_Bridge.get_custom_entries()` 从缓存中按 `customType` 过滤返回。
  - `api.py`：新增 `get_custom_entries(custom_type)` 方法。
  - `pi_goal_x/__init__.py`：`_restore_goal_state()` 调用
    `pi.get_custom_entries("goal_state")`，取最后一条的 `data`，
    用 `GoalState.from_dict()` 恢复 active goal 的所有字段。
- **测试**：`TestGoalRestore` — 预写入 `goal_state` entry，加载扩展后
  调用 `goal_update`，验证不返回 "No active goal"。

### P7R5-04 修复：同名覆盖清理 live harness + 失败完整回滚

- **根因**：
  1. `load_callable()` 同名覆盖只清理 registry，不清理
     `harness._tools` / `_hooks` 中已复制的旧工具/handler。
  2. 替换 `activate()` 失败时，旧扩展 registry 已被删除，无法回滚。
- **修复**：
  - `_harness_bridge.py`：协议新增 `remove_tool(name)` 方法。
  - `agent_harness.py`：`_Bridge.remove_tool()` 从 `harness._tools` 和
    `active_tool_names` 中移除工具。
  - `loader.py`：
    - 新增 `_purge_live_harness(bridge, ext_name, snap)` 静态方法，
      遍历 snapshot 中的 `tool_owners` 和 `event_handlers`，
      对属于旧扩展的工具调用 `bridge.remove_tool()`，
      对属于旧扩展的 hook 调用 `bridge.remove_hook()`。
    - `load_callable()` 覆盖流程改为：
      1. Snapshot **整个** registry（含旧扩展注册）
      2. 备份 `_apis` 和 `_loaded_names`
      3. 移除旧注册 + 清理 live harness
      4. `activate()` 新扩展
      5. **失败时**：从 snapshot 恢复 registry + 恢复 `_apis`/`_loaded_names`
         → 旧扩展完整保留
- **测试**：
  - `test_override_cleans_live_harness` — 旧 tool/hook 从 harness 移除，
    新 tool 注入，hook 数为 0。
  - `test_failed_override_rolls_back` — 新 activate 抛错后旧 tool 仍在
    registry 中，API 列表完好。
- **六次复核仍不完整：** 失败回滚只恢复 registry/`_apis`；此前
  `_purge_live_harness()` 已从 harness 删除旧 tools/hooks，失败后没有恢复，
  旧扩展运行时状态仍丢失。

**验证：Windows `558 passed, 11 skipped`；WSL `554 passed, 4 skipped`；
ruff check + format 通过。**

---

## 十四、第六次复核（2026-09-24）

### 复核结论

P7R5-02、P7R5-03 已完整关闭；P7R5-01 在“源工作区干净”时可用，但默认
snapshot + 源工作区已有未提交修改时仍会丢失 agent 修改；P7R5-04 的成功覆盖
已关闭，失败回滚仍不完整。

### P7R6-01. [ ] 高：snapshot worktree 的 dirty diff 无法安全 apply

- 位置：
  - `packages/pi-dynamic-workflows/pi_dynamic_workflows/worktree.py:73-111`
  - `packages/pi-dynamic-workflows/pi_dynamic_workflows/subagent.py:176-192`
- 场景：
  1. 源 cwd 有未提交修改 `source-dirty`；
  2. `WorktreeManager.create()` 默认 snapshot，把该修改复制到 worktree；
  3. subagent 再把文件改为 `agent-change`；
  4. `collect_diff()` 返回相对 HEAD 的完整 diff；
  5. 对已经有 `source-dirty` 的源 cwd 执行 `git apply`。
- 运行复现：

```text
snapshot_copied source-dirty
apply_error RuntimeError
error: patch failed: a.txt:1
error: a.txt: patch does not apply
```

  `apply_changes()` 失败后 `subagent.py` 只记 warning，随后 cleanup worktree，
  `agent-change` 仍然丢失。
- 根因：
  - 回写的是 `git diff HEAD`（源基线 + agent 修改），不是“agent 相对 snapshot
    基线的增量”；
  - 源 cwd 已有同一批 dirty 修改，patch 无法重复应用。
- 影响：
  - 设计宣称的 default snapshot 模式在常见 dirty worktree 下不可用；
  - 可写 workflow 的修改仍可能被静默丢弃。
- 建议：
  - 在 worktree 创建 snapshot 后记录 baseline commit/tree，回写时只生成
    agent delta；
  - 应用时使用 3-way/冲突检测，并明确向 workflow 返回 apply 失败，
    不要 cleanup 后静默丢失。

### P7R6-02. [ ] 中：同名扩展失败替换未恢复 live runtime

- 位置：`pi_agent_core/extensions/loader.py:113-147`。
- 现状：
  - 成功覆盖、registry 恢复和 API 列表备份均已实现。
  - 失败路径只执行 `registry.restore(snap)`、恢复 `_apis` / `_loaded_names`。
  - 但在 activate 前已调用 `_purge_live_harness()`，从 harness 删除了旧 tool
    和 hook；失败后没有恢复 live state。
- 独立复现：

```text
same_failure_registry ['old_tool']
same_failure_live ['old_tool'] 1 -> [] 0
```

- 影响：
  - registry 看似回滚成功，但旧扩展的工具/handler 已无法在运行时使用。
- 建议：
  - 延迟 live purge 到替换 activate 成功后；或 snapshot/restore live tools、
    hooks、active_tool_names。

### 已验证关闭

- P7R5-02：首次加载后动态 `on()` 现在进入 live hooks；实测 handler 触发。
- P7R5-03：`goal_state` custom entry 可恢复；`goal_update` 不再返回
  `No active goal`。
- P7R5-04 成功路径：新旧同名扩展覆盖后 registry、`_tools`、hooks 只保留新扩展。
- P7R4-01/05/07 保持通过。

### 验证结果

- `ruff check`：通过。
- 临时 `USERPROFILE/HOME` 下全量：`558 passed, 11 skipped`。
- 默认受控环境 workflow 包仍受真实 HOME/TEMP 权限影响，测试隔离问题未变。

**第六次最终判定：P7R5-01、P7R5-04 仍有未关闭部分；当前不能判定 P7R5 全量修复。**
