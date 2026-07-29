# AgentHarness H2 审计报告(2026-07-03)

> 审计对象:Phase 3 批次 H2 交付物 —— `packages/pi-agent-harness/pi_agent_harness/`
> 的 `agent_harness.py`、`messages.py`、`types.py`(H2 相关部分)与 core loop 接线。
>
> 对照基准:① Phase 3 设计文档 §1.3/§4/§7/§9/§10
> (`docs/superpowers/specs/2026-07-03-phase3-agent-harness-design.md`);
> ② 总审计报告 C4 条目(`docs/AUDIT-2026-07-02.md`);
> ③ 上游 [earendil-works/pi](https://github.com/earendil-works/pi)
> `packages/agent/src/harness/agent-harness.ts`(2026-07 main 分支,全文逐段比对)。
>
> 验证方式:`.venv`(Python 3.12)全量 pytest 基线通过(审计时 183);对每个可疑点
> 编写 mock-stream 运行时探针实测(subscribe 事件面 / patch 叠加 / hook 错误码 /
> idle steer / on() 对广播事件);修复项带回归测试,修复后全量 241 通过
> (总数增长含并行落库的 P6 批次测试)。
>
> 状态图例:`[x]` 已修复 · `[ ]` 待修复 · `[~]` 文档修正项(实现对齐 pi,改文档)

## 总体结论

H2 骨架忠实:§4.4 三条持久化时序不变量(message_end 先落盘后广播 / turn_end
广播→flush→暂存异常→save_point / agent_end flush→idle→settled)、turn state
快照与 `prepare_next_turn` 重建、写缓冲、run 失败合成事件闭合、
`harness_convert_to_llm` 四种消息映射均与设计文档和上游一致。
实质偏差集中在 **hook 分发子系统**(错误归一化、链式 patch)与 **C4 承诺未落地**;
另有 4 处文档表述需要修正、若干测试缺口。

---

## 一、实现偏差(实现 ≠ 文档承诺)

### H2-1. C4「自定义消息协议」实际未落地

- [x] **已修复(2026-07-03)** ·(高 · 全库 grep 实证)
- 位置:`pi_agent_harness/types.py`(缺 `AgentMessageProtocol`)、`README.md`、
  `docs/AUDIT-2026-07-02.md` C4 条目
- 问题:设计 §7 承诺三件事全部缺失——① 定义 runtime-checkable 的
  `AgentMessageProtocol`(带 `role: str`)放 `harness/types.py`;② README 增
  自定义消息示例;③ 审计报告 C4 标记"经 H2 落地"。现状:全库 grep
  `AgentMessageProtocol` 仅设计文档命中;README 无示例;C4 仍为 `- [ ] 待实施`。
  `messages.py` 的 docstring 自称是 C4 落点,但协议类型没写。
- 影响:自定义消息 role 拼写错误仍静默消失,无结构约束与 IDE 提示(C4 原始诉求)。
- 修复:① `types.py` 定义 `@runtime_checkable AgentMessageProtocol`(`role: str`),
  经 `pi_agent_harness` 与 `pi_agent_core.harness` shim 导出;② README 增
  "Custom messages"节(harness 消息持久化 + 自定义 role + `isinstance` 协议检查
  示例);③ `AUDIT-2026-07-02.md` C4 勾选并记录落地方式。回归测试 ×2
  (四种 harness 消息 + core 消息满足协议;缺 `role` 的对象不满足)。
- **落地时发现并连带修复的真实 bug**:session 回放对 harness/未知 role 保留
  raw dict(设计 §3.2 的宽容读取),而 `harness_convert_to_llm` 用
  `getattr(message, "role", None)` 取 role——dict 上恒为 None,**已持久化的
  `bashExecution`/`custom` 消息重放后被静默丢弃,进不了 LLM 上下文**
  (运行时探针实证:append → build_context → convert 后消息数为 0)。修复:
  `_role_of` 兼容 dict 与对象两种形态;core 三种 role 的 dict 经
  `model_validate` 还原为典型消息后透传。回归测试 ×1(bashExecution 往返 +
  dict user 消息 + 未知 role 丢弃)。

### H2-2. hook/listener 异常未归一化为 `AgentHarnessError("hook")`

- [x] **已修复(2026-07-03)** ·(高 · 运行时探针实测)
- 位置:`agent_harness.py` `_emit_any` / `_emit_hook`
- 问题:设计 §4.2/§4.3 承诺"异常包装为 `AgentHarnessError("hook")` 上抛";pi 的
  `emitOwn/emitAny/emitHook/drainQueuedMessages` 全部经 `normalizeHookError`。
  实现无任何包装。实测:`before_agent_start` hook 抛 `RuntimeError` →
  `prompt()` 抛出 code=`unknown`(应为 `hook`);订阅者异常同样 `unknown`。
  错误码语义不可依赖,`abort()` 的错误分类随之失真。
- 修复:`_emit_any`/`_emit_hook` 逐 listener/handler `try/except` →
  `raise normalize_harness_error(e, "hook") from e`(已是 `AgentHarnessError`
  的透传不重复包装);`_drain_queue` 回滚后 re-raise 的即为归一化错误,
  与 pi `drainQueuedMessages` 语义对齐。回归测试 ×2
  (`test_hook_errors_normalize_to_hook_code`、
  `test_subscriber_errors_normalize_to_hook_code`)。

### H2-3. `before_provider_request` patch 不叠加;`before_provider_payload` 不链式

- [x] **已修复(2026-07-03)** ·(中 · 运行时探针实测)
- 位置:`agent_harness.py` `_create_stream_fn`、`_create_loop_config.on_payload`
- 问题:设计 §4.3 明确 `before_provider_request` 是"最后非 None 生效"规则的唯一
  例外——"patch 依次叠加";pi 有专用循环 `emitBeforeProviderRequest`(每个
  handler 收到当前已叠加快照,patch 逐个 `applyStreamOptionsPatch`)。实现走
  通用 `_emit_hook`,只有最后一个 handler 的 patch 生效。实测:handler1 返回
  `{maxRetries: 9}`、handler2 返回 `{timeoutMs: 5}` → 结果 `maxRetries=None`,
  前者被整体丢弃。同源问题:pi 的 `emitBeforeProviderPayload` 是链式替换
  (后一个 handler 收到前一个的输出),实现中每个 handler 都收到原始 payload。
- 修复:新增 `_emit_before_provider_request`(链式 patch:handler 收
  `current.model_copy(deep=True)` 快照,非 None 返回值经 `_apply_stream_patch`
  叠加)与 `_emit_before_provider_payload`(链式替换),`_create_stream_fn` 与
  `on_payload` 改走专用发射器;异常同样归一化为 `"hook"`。回归测试 ×1
  (`test_provider_request_and_payload_hooks_chain_across_handlers`:断言第二个
  handler 看到第一个的 patch、两个 patch 都透传到 core `StreamOptions`、
  payload 经两级替换)。

### H2-4. next_turn 队列 drain 后无回滚

- [x] **已修复(2026-07-29)** ·(中 · 运行时探针实测)
- 位置:`agent_harness.py` `_execute_turn` 开头(next_turn drain 处)
- 问题:steer/follow-up 的 `_drain_queue` 有回滚(§4.2 承诺),但 `_execute_turn`
  里 next_turn 是 `clear()` 后 `_emit_queue_update()`——任一订阅者抛异常,
  已取出的 `queued` 永久丢失。pi 在此处 catch → `unshift` 回滚 →
  `normalizeHookError`。
- 修复:emit 失败时 `self.next_turn_queue[:0] = queued` 再抛。回归测试 ×1
  (`test_next_turn_queue_rolls_back_when_queue_update_fails`)。

### H2-5. `abort()` 不聚合错误

- [x] **已修复(2026-07-29)** ·(中 · 运行时探针实测)
- 位置:`agent_harness.py` `abort()`
- 问题:设计 §4.6 承诺"收集全部错误后聚合上抛";pi 对
  `emitQueueUpdate / waitForIdle / emit abort` 三步各自 try/catch 收集 errors,
  最后聚合(单错误直抛,多错误 `AggregateError`)为 code=`hook`。实现三步裸调用,
  任一步抛出即中断——后续等待与 `abort` 事件不再执行。
- 修复:三步各自捕获入列;新增 `_raise_hook_errors`,多错误时
  `ExceptionGroup` → `normalize_harness_error(..., "hook")`。回归测试 ×2
  (`test_abort_clears_queues_and_emits_abort_event`、
  `test_abort_aggregates_hook_errors_from_multiple_steps`)。

### H2-6. `before_provider_payload` 的"替换 payload"在 core 被丢弃

- [x] **已修复(2026-07-29)** ·(core 最小改动 · 运行时探针实测)
- 位置:harness 侧 `on_payload` 返回替换值;core
  `pi_agent_core/adapters/langchain_stream.py` L470-483 调用
  `options.on_payload(...)` 后**忽略返回值**
- 问题:设计 §4.3 给该 hook 的返回值语义是"替换 payload(观测/脱敏)"。harness
  正确返回了替换值(H2-3 修复后为链式结果),但 core 丢弃;且该 payload 本是
  描述性 dict,不回写实际请求。当前架构下 hook 只能观测,"替换/脱敏"名不符实
  (pi-ai 的 `onPayload` 返回值是真替换)。
- 修复:`langchain_stream` 消费 `on_payload` 返回值,至少对
  `system_prompt`/`messages` 字段生效后再传给 `chat.astream`;设计文档 §4.3
  同步恢复"替换 payload"承诺。回归测试 ×1
  (`test_on_payload_return_value_replaces_outgoing_request`)。

### H2-7. 双重失败缺 AggregateError 语义

- [x] **已修复(2026-07-29)** ·(低 · 运行时探针实测)
- 位置:`agent_harness.py` `_execute_turn` except 分支
- 问题:设计 §4.4 说双重失败(run 失败且失败合成也抛)"包
  `AgentHarnessError("unknown")`"。实现里 `_emit_run_failure` 再抛时第二个异常
  直接传播:① 第一个异常信息完全丢失(pi 用 `AggregateError` 同时保留两个);
  ② 若第二个异常是 `SessionError`,最终 code 会是 `session` 而非承诺的
  `unknown`。
- 修复:except 内再套 try/except,双抛时构造
  `AgentHarnessError("unknown", ..., cause=ExceptionGroup([原错, 合成错]))`。
  回归测试 ×1 (`test_double_run_failure_raises_unknown_with_both_causes`)。

---

## 二、文档修正项(实现对齐 pi 上游,设计文档表述不准)

### H2-D1. §4.3「subscribe 收到全部 19 种自有事件」不成立

- [x] **已改文档(2026-07-29)** ·(运行时探针实测,与 pi 语义一致)
- 实际只有 **11 种广播型事件**到达订阅者:`queue_update / save_point / abort /
  settled / after_provider_response / session_compact / session_tree /
  model_update / thinking_level_update / tools_update / resources_update`
  (外加 core 透传的全部 `AgentEvent`);**8 种 hook 型事件**(`before_agent_start /
  context / before_provider_request / before_provider_payload / tool_call /
  tool_result / session_before_compact / session_before_tree`)只送 `on()` 定向
  handler,订阅者不可见。反向同理:`on("save_point")` 等对广播型事件永不触发。
  同段"11 种带返回值 hook"与自身表格(8 种非"—")也不一致。
- 修改建议:§4.3 改为"broadcast 11 种 + hook 8 种,两通道互斥(对齐 pi)"。

### H2-D2. §4.2「steer/follow_up 语义同 Agent」不准

- [x] **已改文档(2026-07-29)** ·(运行时探针实测)
- harness 的 `steer()/follow_up()` 在 idle 时抛 `invalid_state`(pi 语义);
  `Agent.steer()` 允许 idle 入队、由 `continue_()` 消费。两者语义不同。

### H2-D3. §1.3 接线表 `before_llm_call + ContextBudget` 行过时

- [x] **已改文档(2026-07-29)** ·(代码审查)
- 表中写"自动压缩触发信号(§5.6)",但 H3 实施走的是 §5.6 自述的
  turn_end 后 `estimate_context_tokens` 路径,`before_llm_call`/`ContextBudget`
  在 harness 中完全未使用。该 core 接线点当前空置,表行应改注。

### H2-D4. §4.1 构造签名与 §4.6 setter 形式与实现不一致

- [x] **已改文档(2026-07-29)** ·(代码审查)
- ① 实现的 `env` 为可选 `Any`(文档为必填首参 `ExecutionEnv`);② 实现新增
  `max_turns` / `tool_timeout` 构造参数(Python 扩展,文档未记);③
  `stream_options / steering_mode / follow_up_mode` 以公开属性代替文档所列
  getter/setter——pi 的 `setStreamOptions` 有克隆语义,直接属性赋值没有
  (turn 内安全仅因 turn state 深拷贝);④ `_create_turn_state` 末尾无条件
  `build_harness_system_prompt` 自动注入 skills 到系统提示(pi 留给应用回调),
  属 H4 有意扩展但 §4.5/§6.2 未记载。

---

## 三、测试缺口(对照 §9 H2 交付判据与 §10 测试策略)

- [x] §10 明确列"hook 异常回滚队列",`test_harness_agent.py` 已补 steer/follow-up
  drain、异常回滚、block、abort、turn_end flush 覆盖(2026-07-29,+8)。
- [x] `test_tool_hooks_block_or_patch_results` 名义测 block,实际 `tool_call`
  handler 返回 None,block 路径未测 → 已补 `test_tool_call_hook_can_block_execution`。
- [x] `abort()` 无测试;turn_end 不变量 2(广播异常暂存后仍 flush)无直接测试
  → 已补(2026-07-29)。
- [x] hook 错误归一化、provider hook 链式已随 H2-2/H2-3 补测(2026-07-03,+3)。

---

## 四、顺带发现(低优先级 / 跨批次)

- [x] **已修复(2026-07-29)** · `wait_for_idle()` 改用 `_idle_event`（`asyncio.Event`）等待;
  删除未使用的 `_run_promise` 字段;`_set_phase` 统一维护 phase/事件。回归测试 ×1
  (`test_wait_for_idle_blocks_until_run_completes`)。
- [x] **已修复(2026-07-29)** · `_create_user_message` 纯文本时 `content` 改为
  `[{"type":"text","text":...}]` 块数组(对齐 pi)。回归测试 ×1
  (`test_prompt_persists_user_message_as_text_block_array`)。
- [ ]（H3 范围)`navigate_tree` 的 `editor_text` 只处理 user 消息目标,不处理
  `custom_message`(pi 两者都处理);`fromHook` 判定用 `hook_result is not None`
  过宽——hook 只返回 label 而摘要来自 LLM 时会误标 `fromHook=True`(pi 判
  `hookResult?.summary !== undefined`)。
- [x] **已更新(2026-07-29)** · `AGENTS.md` 状态段:H2 审计收尾 + 测试数 293。

---

## 修复优先级

| 级别 | 项目 | 状态 |
|------|------|------|
| 高(错误语义 / 承诺缺口) | H2-2 hook 错误归一化、H2-3 provider hook 链式 | ✅ 已修复(2026-07-03,回归 +3) |
| 高(一次性补齐) | H2-1 C4 `AgentMessageProtocol` + README 示例 + C4 勾选 | ✅ 已修复(2026-07-03,回归 +2) |
| 中(小修) | H2-4 next_turn 回滚、H2-5 abort 聚合 | ✅ 已修复(2026-07-29,回归 +3) |
| 中(跨层) | H2-6 payload 替换(core 消费返回值) | ✅ 已修复(2026-07-29,回归 +1) |
| 低 | H2-7 双重失败聚合、第四部分杂项 | ✅ 已修复(2026-07-29);H3 navigate_tree 仍待 |
| 文档 | H2-D1 ~ D4 一次文档修订 | ✅ 已修复(2026-07-29) |
| 测试 | 第三部分缺口(steer/follow-up/block/abort) | ✅ 已补(2026-07-29,+8) |
