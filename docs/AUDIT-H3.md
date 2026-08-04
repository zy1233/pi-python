# AUDIT-H3：Phase 3 H3（compaction / branch summarization / navigate_tree / auto_compact）实现审计（2026-08-04）

> 审计范围：`packages/pi-agent-harness/pi_agent_harness/compaction/{utils.py, compaction.py, __init__.py}`
> 以及 `agent_harness.py` 中 H3 相关方法（`compact` / `navigate_tree` / `_maybe_auto_compact`
> / `_compact_internal` / `_navigate_tree_internal`），对照 Phase 3 设计文档
> （`docs/superpowers/specs/2026-07-03-phase3-agent-harness-design.md` §5/§9/§10）
> 与上游 [earendil-works/pi](https://github.com/earendil-works/pi)
> `packages/agent/src/harness/compaction/` 三个 TS 源文件逐段核对。
>
> 验证方式：`.venv-audit`（Python 3.12）全量 pytest 293 通过、`ruff check` 全绿；
> 对每个可疑点结合代码审查与设计文档逐段比对。
>
> 状态图例：`[ ]` 待修复 · `[~]` 记录性（设计文档批准的偏差、低影响或无需行动）。

## 总体结论

H3 主体忠实：`prepare_compaction` 的 token 估算、合法切点选取（toolResult 非切点）、
`complete_simple` 复用现有 stream_fn 零 core 改动、`compact()` 流程含 phase 锁 +
`session_before_compact` hook（cancel / 自供摘要）+ entry 落盘 + `session_compact` 事件、
`collect_entries_for_branch_summary`（双路径公共祖先 + toolResult 过滤）、
`prepare_branch_entries`（尾部向前入选 + 预算截断）、`navigate_tree`（hook → 摘要 →
user 目标移 parent + editor_text → session.move_to → session_tree 事件）、
`auto_compact`（turn_end save_point 后触发、失败不打断 run）均与设计文档和上游一致。
审计发现 2 个实质偏差、3 个中等偏差、5 个低影响偏差/nits，以及若干测试缺口。

---

## 一、实现偏差（实现 ≠ 文档承诺）

### H3-1. split-turn 序列化使用原始 object dump 而非结构化格式（实质）

- [x] **已修复（2026-08-04）** ·（实质 · 代码审查）
- 位置：`compaction/utils.py` `_create_split_turn_summary`（原 L222–224 `_serialize_message`）
- 问题：`_create_split_turn_summary` 使用 `utils.py` 内的简陋 `_serialize_message`
  （输出 `[{role}]: {message}` Python repr），而非 `compaction/compaction.py` 中结构化的
  `serialize_conversation`（`[User]: 文本` / `[Assistant]: 文本` /
  `[Assistant tool calls]: name(json)` / `[Tool result]: 截断文本`，§5.4 描述）。
- 修复：`_create_split_turn_summary` 改用 `compaction.compaction.serialize_conversation`
  （deferred import 避免循环依赖），删除 `utils.py` 内的简陋 `_serialize_message`。
  回归测试 ×1（`test_split_turn_summary_uses_structured_serialization`）。

### H3-2. `_editor_text_for_target` 只处理 user 消息，不处理 custom_message（实质）

- [x] **已修复（2026-08-04）** ·（实质 · 代码审查 + H2 审计遗留）
- 位置：`agent_harness.py` `_editor_text_for_target`
- 问题：设计 §5.7 明确"目标是 user/custom_message 时移到其 parent 并返回 editor_text
  （编辑重发场景）"；pi 的 `navigateTree` 对 `custom_message` entry 同样做 parent 回退
  + 返回文本。实现仅判断 `MessageEntry` + `role == "user"`，`CustomMessageEntry`
  不是 `MessageEntry` 子类，一律走 `None`。
- 修复：`_editor_text_for_target` 增加 `isinstance(entry, CustomMessageEntry)` 分支，
  从 `entry.content`（str 或 list）提取文本；import `CustomMessageEntry`。
  回归测试 ×1（`test_navigate_tree_custom_message_target_returns_editor_text`）。

---

## 二、中等偏差

### H3-3. navigate_tree `fromHook` 判定过宽

- [x] **已修复（2026-08-04）** ·（中 · 代码审查 + H2 审计遗留）
- 位置：`agent_harness.py` `_navigate_tree_internal`
- 问题：`"fromHook": hook_result is not None`——当 hook 只返回 `{label: "tag"}` 而未供
  摘要时，`fromHook` 仍为 True，但实际 summary 来自 LLM（或为 None）。pi 判定为
  `hookResult?.summary !== undefined`，仅在 hook 真正提供了 summary 时才标 `fromHook`。
- 修复：引入 `summary_from_hook = hook_summary is not None` 变量，仅在 hook 实际提供了
  summary 时才设 `fromHook=True`。
  回归测试 ×2（`test_navigate_tree_from_hook_false_when_summary_from_llm` /
  `test_navigate_tree_from_hook_true_when_hook_supplies_summary`）。

### H3-4. cut point 步骤 3（向前跳过非 message entry）未实现

- [x] **已修复（2026-08-04）** ·（中 · 代码审查）
- 位置：`compaction/utils.py` `_select_first_kept_entry`
- 问题：设计 §5.3 步骤 3："切点回退：向前跳过非 message entry（避免把 model_change 等
  留在被压缩侧边缘）"。实现找到第一个合法切点后直接返回，没有向前回退跳过前方紧邻的
  `model_change` / `thinking_level_change` / `active_tools_change` 等 entry。
- 修复：在找到合法切点后，增加 `while` 循环向前跳过紧邻的非消息产出 entry
  （`_is_message_bearing_entry` 辅助函数区分 `MessageEntry` / `CustomMessageEntry` /
  `BranchSummaryEntry` 与 `ModelChangeEntry` 等纯状态 entry），将它们吸收到 kept 侧。
  回归测试 ×1（`test_cut_point_skips_backward_over_non_message_entries`）。

### H3-5. 文件操作详情未格式化到摘要文本中

- [x] **已修复（2026-08-04）** ·（中 · 代码审查）
- 位置：`compaction/compaction.py` `compact_preparation` 返回处
- 问题：设计 §5.4 "格式化为 `<read-files>` / `<modified-files>` 块附在摘要尾部"——
  pi 将文件列表格式化为 XML-like 块追加到 summary 字符串尾部，以便 LLM 在后续轮次
  知道已读/已改了哪些文件。实现仅将 `extract_file_details` 的 dict 存入
  `CompactionResult.details`，未将其格式化追加到 `summary` 文本。
- 修复：新增 `format_file_details` 和 `_append_file_details_to_summary` 辅助函数，
  在 `compact_preparation` 中将 `<read-files>` / `<modified-files>` XML 块追加到
  summary 文本尾部。
  回归测试 ×2（`test_compact_summary_includes_file_details_block` /
  `test_format_file_details_produces_xml_blocks`）。

---

## 三、低影响偏差 / Nits

### H3-6. 模块布局：`branch_summarization.py` 未按设计独立成文件

- [~] 记录性 ·（低 · 代码审查）
- 设计 §2 包布局表列 `compaction/branch_summarization.py` 对应 pi 的
  `compaction/branch-summarization.ts`；实现将 branch summarization 全部代码（
  `collect_entries_for_branch_summary` / `prepare_branch_entries` /
  `create_branch_summary` / `build_branch_summary_prompt`）合并入 `compaction/compaction.py`。
- 影响：无功能差异，`__init__.py` 已正确 re-export；但偏离设计表和 pi 文件对照。

### H3-7. `COMPACTION_SYSTEM_PROMPT` 缺少 Progress 子分类

- [~] 记录性 ·（低 · 代码审查）
- 设计 §5.4 写 "Progress Done-InProgress-Blocked"；实现仅列 "Progress"，缺
  Done/InProgress/Blocked 三个子维度提示。
- 影响：LLM 输出的摘要结构可能不如设计预期精细，但不影响正确性。

### H3-8. `serialize_conversation` 工具调用格式与设计不一致

- [~] 记录性 ·（低 · 代码审查）
- 设计 §5.4："[Assistant tool calls]: name(k=v,...)"；实现使用
  `name({json.dumps(arguments)})`——JSON 风格而非 key=value 风格。
- 影响：格式差异不影响 LLM 摘要质量，JSON 风格更无歧义。

### H3-9. auto_compact 失败无 warning 日志

- [x] **已修复（2026-08-04）** ·（低 · 代码审查）
- 位置：`agent_harness.py` `_maybe_auto_compact`
- 问题：设计 §5.6 "失败仅发 warning 日志"；实现 `except Exception: return` 静默吞掉
  异常，整个 `agent_harness.py` 未 import `logging`。
- 修复：`agent_harness.py` 增加 `import logging` 和模块级 `logger`，
  `_maybe_auto_compact` 的 `except` 分支改为 `logger.warning(..., exc_info=True)`。
  回归测试 ×1（`test_auto_compact_logs_warning_on_failure`）。

### H3-10. `extract_file_details` 包含设计未列的工具名

- [~] 记录性 ·（低 · 代码审查）
- 设计 §5.4 仅列 `read/write/edit` 三种工具；实现将 `list`/`glob` 计入 readFiles，
  `apply_patch` 计入 modifiedFiles。
- 影响：增强性偏差，覆盖更多工具——不破坏兼容但与设计字面不符。

---

## 四、测试缺口（对照 §9 H3 交付判据与 §10 测试策略）

| 缺口 | 说明 | 状态 |
|------|------|------|
| split-turn 摘要生成路径 | `_create_split_turn_summary` 结构化输出断言 | ✅ `test_split_turn_summary_uses_structured_serialization` |
| 迭代 update 模式 | `build_compaction_prompt` 的 `previousSummary` 路径 | ✅ `test_build_compaction_prompt_update_mode_includes_existing_summary` |
| 文件详情提取 | `extract_file_details`（含 `previousDetails` 继承） | ✅ `test_extract_file_details_accumulates_and_inherits` |
| navigate_tree hook cancel | hook 返回 `{cancel: True}` | ✅ `test_navigate_tree_hook_can_cancel` |
| navigate_tree hook 自供 summary + label | hook 跳过 LLM + label 落盘 | ✅ `test_navigate_tree_hook_supplies_summary_and_label` |
| navigate_tree custom_message 目标 | 编辑重发语义（H3-2 回归） | ✅ `test_navigate_tree_custom_message_target_returns_editor_text` |
| compact "Nothing to compact" | `compact()` 单条消息抛错 | ✅ `test_compact_raises_when_nothing_to_compact` |
| auto_compact 失败静默 | LLM 失败不传播到 `prompt()` | ✅ `test_auto_compact_failure_does_not_propagate_to_prompt` |
| branch summary 预算截断 | `prepare_branch_entries` 小预算截断 | ✅ `test_prepare_branch_entries_respects_token_budget` |

---

## 五、记录性（无需行动）

- [~] `should_compact` 增加 `context_window is None` 防御——设计未提及，无害增强。
- [~] `complete_simple` 对 compaction 和 branch summarization 共用
  `COMPACTION_SYSTEM_PROMPT`——设计 §5.7 "同结构化格式"已批准。
- [~] `prepare_branch_entries` 使用 `len(str(entry)) // 4` 估算 token（而非
  `estimate_message_tokens`）——粗粒度估算，在预算截断场景可接受。
- [~] `create_branch_summary` 返回 `CompactionResult`，`tokensBefore` 用
  `sum(len(str(entry)) for entry in entries)` 而非 `estimate_context_tokens`——branch
  summary 的 `tokensBefore` 与 compaction 的含义不同（无 usage 锚点可用），实现合理。

---

## 修复优先级

| 级别 | 项目 | 状态 |
|------|------|------|
| 实质 | H3-1 split-turn 序列化格式、H3-2 custom_message editor_text | ✅ 已修复（2026-08-04，回归 +9） |
| 中 | H3-3 fromHook 判定、H3-4 cut point step 3、H3-5 文件详情格式化 | ✅ 已修复（2026-08-04，回归 +5） |
| 低 | H3-9 auto_compact 日志 | ✅ 已修复（2026-08-04，回归 +1） |
| 记录性 | H3-6 模块布局、H3-7 prompt 子分类、H3-8 工具调用格式、H3-10 额外工具名 | [~] 无需行动 |
| 测试 | 第四节缺口 | ✅ 已补（2026-08-04，+9） |

## 验证状态

审计时全量 pytest 293 通过（`.venv-audit` Python 3.12），`ruff check` 全绿。
第一轮修复（H3-1/H3-2）后全量 pytest 302 通过，H3 专项测试从 5 增至 14 个。
第二轮修复（H3-3/H3-4/H3-5/H3-9）后全量 pytest 308 通过，H3 专项测试 20 个全通过
（`test_harness_compaction.py`）。所有可修复偏差已关闭。
