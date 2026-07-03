# Phase 3 AgentHarness — 设计方案

> Scope: Session 树（JSONL v3）+ AgentHarness 主类 + Compaction + Skills / Prompt Templates + ExecutionEnv
> 基于 Phase 1（core loop）+ Phase 2（usage/thinking/transform）+ Phase 2.5（重试/钩子/预算信号/结构化输出，81 tests）
> 上游参照：[earendil-works/pi](https://github.com/earendil-works/pi) `packages/agent/src/harness/`（2026-07 main 分支，已逐文件核读）

---

## 1. 目标与范围

### 1.1 移植清单

| 上游模块 | 内容 | 本设计章节 |
|---|---|---|
| `harness/types.ts`（26KB） | 错误层级、SessionTreeEntry 11 种、协议、hook 结果类型 | §3、§4、§8 |
| `harness/session/`（6 文件） | Session 树、JSONL v3 存储、内存存储、Repo | §3 |
| `harness/agent-harness.ts`（36KB） | 主类：phase 锁、三队列、写缓冲、事件/hook 系统 | §4 |
| `harness/compaction/`（3 文件） | token 估算、cut point、结构化摘要、分支摘要 | §5 |
| `harness/skills.ts`、`prompt-templates.ts` | SKILL.md 加载、frontmatter、参数替换 | §6 |
| `harness/messages.ts` | 4 种 harness 消息 role + harness 版 convert_to_llm | §7 |
| `harness/env/` | FileSystem + Shell 抽象 | §8 |

**排除项**：`stream_proxy`（浏览器后端代理，与 Python 库定位无关）；pi 的 `Models` 多厂商 registry（沿用 LangChain 适配层）。

### 1.2 两个已确认的方向决策

1. **忠实全量移植**：含 session 树分支/fork/navigateTree/label。分批实施（§9），但设计一次到位，避免中途改存储格式。
2. **JSONL 与 pi v3 字节兼容**：同 header、同 entry 类型名、camelCase 字段。pi 生态工具（TUI、session 查看器）可直接读写我们的 session 文件。我们的消息模型字段本就是 camelCase（`stopReason`/`toolCallId`），兼容成本低。

### 1.3 与 core 层的边界

**`AgentHarness` 平行于 `Agent`，不包 `Agent` 类**（忠实 pi）：两者都直接驱动 `run_agent_loop`，harness 自管 steer/follow-up 队列与 abort。理由：harness 的队列 drain 需要发 `queue_update` 事件、`prepare_next_turn` 需要 flush 写缓冲后从 session 重建 turn state——这些都要求对 loop config 的完全控制权，包一层 `Agent` 反而要打穿它的封装。

core 层在 Phase 2.5 预留的接线点全部用上：

| core 接线点 | harness 用途 |
|---|---|
| `AgentLoopConfig.prepare_next_turn` | flush 写缓冲 → `session.build_context()` 重建 turn state（模型/工具/系统提示可能已被 setter 改变） |
| `AgentLoopConfig.transform_context` | 触发 `context` hook（应用可整体替换消息列表） |
| `AgentLoopConfig.before_tool_call` / `after_tool_call` | 触发 `tool_call` / `tool_result` hook（block/patch） |
| `StreamOptions.on_payload` / `on_response` | 触发 `before_provider_payload` / `after_provider_response` hook |
| `before_llm_call` + `ContextBudget` | 自动压缩触发信号（§5.6） |
| `run_agent_loop(prompts, context, config, emit, signal, stream_fn)` | harness 的 `_execute_turn` 直接调用，emit 指向 `_handle_agent_event` |

**core 层零改动原则**：本阶段不修改 `pi_agent_core/`（harness 子包除外）。若实施中发现 core 缺口，回审计报告立项，不顺手改。

---

## 2. 架构与模块对照

```
应用层                        AgentHarness                         core (既有)
────────                     ──────────────                       ────────────
prompt/skill/template  ──►   phase 锁 + next_turn 队列
subscribe('*')         ◄──   AgentEvent + 19 种自有事件
on(type, handler)      ◄──   带返回值 hook（11 种）
                             │
                             │ createTurnState: session.build_context()
                             │ + system_prompt 解析 + resources 快照
                             ▼
                             run_agent_loop ──────────────►  agent_loop.py
                             │  emit=_handle_agent_event          │
                             │  （message_end → session 落盘）      ▼
                             │                               langchain_stream
                             ▼
                       Session (树) ──► SessionStorage ──► JSONL v3 文件
                             │
                       Compaction / BranchSummary（LLM 摘要，走同一 stream_fn）
                             │
                       Skills / PromptTemplates ◄── ExecutionEnv (FileSystem+Shell)
```

### 包布局 ↔ pi TS 源文件

| Python 模块 | pi (TS) | 批次 |
|---|---|---|
| `pi_agent_core/harness/__init__.py` | `harness/index` 导出面 | H1–H4 递增 |
| `pi_agent_core/harness/types.py` | `harness/types.ts` | H1 |
| `pi_agent_core/harness/session/uuid7.py` | `session/uuid.ts` | H1 |
| `pi_agent_core/harness/session/session.py` | `session/session.ts`（含 `build_session_context`） | H1 |
| `pi_agent_core/harness/session/jsonl_storage.py` | `session/jsonl-storage.ts` | H1 |
| `pi_agent_core/harness/session/memory_storage.py` | `session/memory-storage.ts` | H1 |
| `pi_agent_core/harness/session/jsonl_repo.py` | `session/jsonl-repo.ts` + `repo-utils.ts` | H1 |
| `pi_agent_core/harness/session/memory_repo.py` | `session/memory-repo.ts` | H1 |
| `pi_agent_core/harness/messages.py` | `harness/messages.ts` | H2 |
| `pi_agent_core/harness/agent_harness.py` | `harness/agent-harness.ts` | H2 |
| `pi_agent_core/harness/compaction/utils.py` | `compaction/utils.ts` | H3 |
| `pi_agent_core/harness/compaction/compaction.py` | `compaction/compaction.ts` | H3 |
| `pi_agent_core/harness/compaction/branch_summarization.py` | `compaction/branch-summarization.ts` | H3 |
| `pi_agent_core/harness/skills.py` | `harness/skills.ts` | H4 |
| `pi_agent_core/harness/prompt_templates.py` | `harness/prompt-templates.ts` | H4 |
| `pi_agent_core/harness/system_prompt.py` | `harness/system-prompt.ts` | H4 |
| `pi_agent_core/harness/env.py` | `harness/types.ts` 的 FileSystem/Shell + `env/` | H4（协议定义在 H1） |

---

## 3. Session 树与 JSONL v3 格式

### 3.1 文件格式（与 pi 字节兼容）

首行 header，之后每行一个 entry，append-only：

```jsonl
{"type":"session","version":3,"id":"<uuid>","timestamp":"<ISO8601>","cwd":"/path","parentSession":"/optional/parent.jsonl"}
{"type":"message","id":"0198c9a1","parentId":null,"timestamp":"...","message":{"role":"user","content":[...],"timestamp":1751500000000}}
{"type":"message","id":"0198c9a2","parentId":"0198c9a1","timestamp":"...","message":{"role":"assistant",...}}
{"type":"leaf","id":"0198c9a3","parentId":"0198c9a2","timestamp":"...","targetId":"0198c9a1"}
```

- **entry id**：uuid7 前 8 位（时间有序、短），冲突时重试 100 次后回退完整 uuid7。Python 侧自实现 `uuid7()`（标准库 3.11/3.12 无；≈40 行，不引依赖）。
- **树结构**：`parentId` 构成树；分支 = 从任意历史 entry 续写；当前叶子由**最后一行推导**——`leaf` entry 则取其 `targetId`，否则取该行自身 `id`。`leaf` entry 是"移动叶子指针"的持久化记录，天然支持 undo/redo 审计。
- **消息序列化**：pydantic `model_dump(exclude_none=True)`。我们的 `Message` 字段名与 pi 一致（camelCase），`structured_output` 是 Python 版扩展字段——pi 读到会忽略，不破坏兼容。
- **解析校验**（对齐 pi）：header 必须 `version==3` 且有 `id/timestamp/cwd`；entry 必须有 `type/id/timestamp`，`parentId` 为 null 或 str。校验失败抛 `SessionError("invalid_session"/"invalid_entry")`，消息含文件路径与行号。

### 3.2 SessionTreeEntry（11 种，pydantic discriminated union）

```python
class SessionTreeEntryBase(BaseModel):
    id: str
    parentId: str | None            # 顶层保持 camelCase（兼容优先）
    timestamp: str                  # ISO8601

class MessageEntry(SessionTreeEntryBase):          # type: "message"
    message: dict                    # 原样 JSON；构造 context 时再验证为 Message
class ThinkingLevelChangeEntry(...):               # thinkingLevel: str
class ModelChangeEntry(...):                       # provider, modelId
class ActiveToolsChangeEntry(...):                 # activeToolNames: list[str]
class CompactionEntry(...):                        # summary, firstKeptEntryId, tokensBefore, details?, fromHook?
class BranchSummaryEntry(...):                     # fromId, summary, details?, fromHook?
class CustomEntry(...):                            # customType, data?
class CustomMessageEntry(...):                     # customType, content, details?, display
class LabelEntry(...):                             # targetId, label?
class SessionInfoEntry(...):                       # name?（legacy 命名，兼容保留）
class LeafEntry(...):                              # targetId: str | None
```

设计要点：`MessageEntry.message` 存 dict 而非强类型 `Message`——历史文件可能含未来版本/其他实现写入的自定义 role，宽容读取、`build_session_context` 时才逐条转换，未知 role 保留为 dict 透传给 `convert_to_llm`。

### 3.3 SessionStorage 协议与两个实现

```python
class SessionStorage(Protocol):
    async def get_metadata(self) -> SessionMetadata: ...
    async def get_leaf_id(self) -> str | None: ...
    async def set_leaf_id(self, leaf_id: str | None) -> None:   # 追加 leaf entry
    async def create_entry_id(self) -> str: ...
    async def append_entry(self, entry: SessionTreeEntry) -> None: ...
    async def get_entry(self, id: str) -> SessionTreeEntry | None: ...
    async def find_entries(self, type_: str) -> list[SessionTreeEntry]: ...
    async def get_label(self, id: str) -> str | None: ...
    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]: ...
    async def get_entries(self) -> list[SessionTreeEntry]: ...
```

- **JsonlSessionStorage**：打开时全量读入内存（entries list + byId dict + labels 缓存 + 当前 leaf），之后 append 一行 + 更新内存。与 pi 相同的读写策略；文件 I/O 用 `asyncio.to_thread` 包装（协议保持 async，为远端存储留门）。
- **MemorySessionStorage**：纯内存，测试与临时会话用。

### 3.4 Session 类（薄封装）

`append_message / append_thinking_level_change / append_model_change / append_active_tools_change / append_compaction / append_custom_entry / append_custom_message_entry / append_label / append_session_name / move_to(entry_id, summary?) / get_branch(from_id?) / build_context()`——每个 append 方法组装 entry（新 id + 当前 leaf 为 parent + ISO 时间戳）后写入 storage。`move_to` 即分支切换：`set_leaf_id(target)`，可选追加 `branch_summary` entry。

### 3.5 build_session_context（路径回放）

对 root→leaf 路径单次遍历，输出 `SessionContext(messages, thinking_level, model, active_tool_names)`：

1. 状态归约：`thinking_level_change`/`model_change`/`active_tools_change` 依次覆盖；assistant 消息也更新 model（provider+modelId）；记住**最后一个** compaction entry。
2. 消息回放：若有 compaction——先放 `compactionSummary` 消息，然后从 `firstKeptEntryId` 起回放到 compaction 行之前，再回放 compaction 行之后全部；无 compaction 则全量回放。`custom_message`/`branch_summary` entry 转成对应 harness 消息（§7）。

### 3.6 SessionRepo（create/open/list/delete/fork）

- **JsonlSessionRepo(env, dir)**：文件名 `<timestamp>-<session-id>.jsonl`；`list(cwd?)` 读每文件首行 header 过滤；`fork(source, entry_id?, position?)` 复制 header（换新 id、记 `parentSession`）+ 截断到目标 entry 的路径。
- **MemorySessionRepo**：dict 存储。

---

## 4. AgentHarness 主类

### 4.1 状态与 phase 锁

```python
class AgentHarnessPhase:  # Literal["idle", "turn", "compaction", "branch_summary", "retry"]
```

`prompt/skill/prompt_from_template` 要求 idle，否则抛 `AgentHarnessError("busy")`；`compact()`/`navigate_tree()` 同样要求 idle 并各自持锁。与 `Agent` 类的可重入队列语义不同——harness 是**单占用**模型（pi 语义，UI 层自己排队）。

构造参数（对齐 pi `AgentHarnessOptions`）：

```python
AgentHarness(
    env: ExecutionEnv,
    session: Session,
    stream_fn: StreamFn,                      # pi 的 models: Models → 我们的 LangChain stream_fn
    get_api_key: Callable | None = None,
    tools: list[AgentTool] | None = None,
    resources: AgentHarnessResources | None = None,   # skills + prompt_templates
    system_prompt: str | Callable | None = None,      # 静态或回调（收 env/session/model/thinking_level/active_tools/resources）
    stream_options: AgentHarnessStreamOptions | None = None,
    model: Model,
    thinking_level: ThinkingLevel = "off",
    active_tool_names: list[str] | None = None,
    steering_mode: QueueMode = "one-at-a-time",
    follow_up_mode: QueueMode = "one-at-a-time",
    compaction: CompactionSettings | None = None,     # Python 版扩展：含 auto_compact（§5.6）
)
```

`AgentHarnessStreamOptions`（harness 拥有、每 turn 快照）：`timeout_ms / max_retries / max_retry_delay_ms / headers / metadata`。映射到 core `StreamOptions` 的 `max_retries/retry_max_delay`；`headers/metadata` 经 LangChain 的 `default_headers`/`metadata` 下传（H2 实施时核对各 provider 支持度，不支持则记录診断并跳过）。

### 4.2 三队列与写缓冲

- **steer / follow_up**：语义同 `Agent`，但 drain 时发 `queue_update` 事件；hook 抛异常则**回滚**（unshift 回队列）再抛 `AgentHarnessError("hook")`。
- **next_turn**（harness 特有）：任何时刻可入队；下次 `prompt()` 时插在用户消息**之前**。
- **pending_session_writes**：turn 进行中调用 `set_model/set_thinking_level/set_tools/set_active_tools/append_message/...` 时不直接写 session（并发写会破坏 parentId 链），先入缓冲；flush 时机（见 4.4）按序重放。

### 4.3 事件与 hook 系统

两条通道（对齐 pi）：

1. **`subscribe(listener)` 广播**：收到全部 `AgentEvent`（core 透传）+ 19 种 harness 自有事件。listener 按注册顺序 await；异常包装为 `AgentHarnessError("hook")` 上抛（**不吞**——harness 与 core 的 StreamFn"不抛"契约不同，hook 错误是应用 bug，应显式失败）。
2. **`on(type, handler)` 定向 hook**：带返回值，多 handler 顺序执行、**最后一个非 None 返回值生效**（`before_provider_request` 例外：patch 依次叠加）。

自有事件（19 种）与 hook 返回值（11 种带返回值）沿用 pi 的 `AgentHarnessEventResultMap`：

| 事件 | 时机 | hook 返回值 |
|---|---|---|
| `queue_update` | 任一队列变化 | — |
| `save_point` | turn_end flush 后 | — |
| `abort` | abort() 完成 | — |
| `settled` | agent_end 后 | — |
| `before_agent_start` | prompt 组装后、loop 前 | `{messages?, system_prompt?}` 追加消息/换系统提示 |
| `context` | 每次 LLM 调用前（接 `transform_context`） | `{messages}` 整体替换 |
| `before_provider_request` | stream_fn 调用前 | stream options patch（叠加合并，`None` 值删除键） |
| `before_provider_payload` | 接 core `on_payload` | 替换 payload（观测/脱敏） |
| `after_provider_response` | 接 core `on_response` | — |
| `tool_call` | 接 `before_tool_call` | `{block?, reason?}` |
| `tool_result` | 接 `after_tool_call` | `{content?, details?, is_error?, terminate?}` patch |
| `session_before_compact` | compact 摘要生成前 | `{cancel?}` 或自供 `{compaction}` |
| `session_compact` | compaction entry 落盘后 | — |
| `session_before_tree` | navigate_tree 移动前 | `{cancel?, summary?, custom_instructions?, label?}` |
| `session_tree` | 叶子移动后 | — |
| `model_update` / `thinking_level_update` / `tools_update` / `resources_update` | setter 调用后 | — |

### 4.4 turn 生命周期与持久化时序（不变量）

`_handle_agent_event`（emit 落点）的三条规则，**顺序不可变**：

1. **`message_end`** → 先 `session.append_message(event.message)` **再**广播事件。保证订阅者看到事件时消息已持久化（崩溃恢复一致性）。
2. **`turn_end`** → 先广播（异常暂存），flush 写缓冲，再抛暂存异常，最后发 `save_point{had_pending_mutations}`。
3. **`agent_end`** → flush、`phase = idle`、广播、发 `settled{next_turn_count}`。

**run 失败合成**（loop 异常逃逸时）：构造 `stopReason="error"|"aborted"` 的失败 assistant 消息，按 `message_start → message_end → turn_end → agent_end` 完整走一遍 `_handle_agent_event`——事件流闭合 + 失败也落盘。双重失败（合成也抛）包 `AgentHarnessError("unknown")`。

### 4.5 turn state 快照与 prepare_next_turn

每 turn 开始 `_create_turn_state()`：`session.build_context()` + resources 快照 + stream_options 克隆 + system_prompt 解析（回调则 await）。**turn 中 setter 只写缓冲与内存字段，正在跑的 turn 用旧快照**；`prepare_next_turn`（core 回调）= flush 缓冲 → 重建 turn state → 返回 `{context, model, thinking_level}` 给 loop——下一 turn 生效。这是 pi "harness 状态变更按 turn 边界生效"语义的核心。

### 4.6 API 面

`prompt(text, images?) -> AssistantMessage`（含 next_turn 注入 + `before_agent_start` hook）、`skill(name, additional_instructions?)`、`prompt_from_template(name, args)`、`steer/follow_up/next_turn`、`append_message`、`compact(custom_instructions?)`、`navigate_tree(target_id, summarize?/custom_instructions?/label?)`、`abort() -> {cleared_steer, cleared_follow_up}`、`wait_for_idle()`、getter/setter × {model, thinking_level, tools, active_tools, resources, stream_options, steering_mode, follow_up_mode}。

工具名唯一性与 active 名单校验（`invalid_argument`）；`abort()` 清两队列 + 触发 run abort controller + 等 idle，收集全部错误后聚合上抛。

---

## 5. Compaction

### 5.1 设置与触发

```python
class CompactionSettings(BaseModel):
    enabled: bool = True
    reserve_tokens: int = 16384      # 摘要 prompt+输出预留
    keep_recent_tokens: int = 20000  # 压缩后保留的近期上下文
    auto_compact: bool = False       # Python 版扩展（§5.6）

def should_compact(context_tokens, context_window, settings) -> bool:
    return settings.enabled and context_tokens > context_window - settings.reserve_tokens
```

### 5.2 token 估算

- `calculate_context_tokens(usage)`：`totalTokens` 或四字段之和。
- `estimate_context_tokens(messages)`：找最后一条有效 assistant usage（`stopReason` 非 error/aborted 且 tokens>0），其前用真实 usage、其后逐条 `estimate_tokens`（chars/4 启发式：text/thinking 全文、toolCall 名+args JSON、图片按 4800 chars、harness 消息按 summary/command+output）。
- 与 core `ContextBudget` 的关系：`ContextBudget` 是 core 每轮从上次 usage 产生的**轻量信号**（触发用）；`estimate_context_tokens` 是 harness 的**精确版**（记录 `tokensBefore` 用）。两者共存，不合并。

### 5.3 cut point 算法（忠实移植）

1. 合法切点：user/assistant/bashExecution/custom/branchSummary/compactionSummary 消息行 + branch_summary/custom_message entry 行；**toolResult 不是切点**（不能把工具结果与其 assistant 调用拆开）。
2. 从尾部向前累积 `estimate_tokens` 直到 ≥ `keep_recent_tokens`，取该位置之后第一个合法切点。
3. 切点回退：向前跳过非 message entry（避免把 model_change 等留在被压缩侧边缘）。
4. **split-turn 检测**：切点非 user 消息时，向前找 turn 起点（user/bashExecution/branch_summary/custom_message）；turn 前缀（起点→切点）单独摘要为 "Turn Context"，与历史摘要拼接。

### 5.4 摘要生成

- 结构化 prompt（Goal / Constraints & Preferences / Progress Done-InProgress-Blocked / Key Decisions / Next Steps / Critical Context），已有摘要时用 UPDATE 版 prompt 迭代更新（保留旧信息 + 并入新消息）。
- 对话序列化 `serialize_conversation`：`[User]:` / `[Assistant]:` / `[Assistant thinking]:` / `[Assistant tool calls]: name(k=v,...)` / `[Tool result]:`（截 2000 chars）。
- 文件操作提取：从 toolCall 的 `read/write/edit` 工具 args.path 累积（read/written/edited 三集合，继承前一次 compaction 的 details），格式化为 `<read-files>` / `<modified-files>` 块附在摘要尾部，details 存 `{readFiles, modifiedFiles}`。
- **Python 化决策——`complete_simple()`**：pi 用 `models.completeSimple`；我们在 `harness/compaction/compaction.py` 内实现辅助函数：调用现有 `stream_fn(model, LlmContext(...), StreamOptions(signal=...))` 后 `await stream.message_result()` 收流为最终 `AssistantMessage`。零 core 改动，重试/退避自动复用。`maxTokens` 上限暂不下传（core StreamOptions 无此字段；摘要输出靠 prompt 约束，实测超长再立项）。

### 5.5 compact() 流程（harness 方法）

idle 检查 → `phase="compaction"` → `prepare_compaction(branch_entries, settings)`（无可压缩返回 None → 抛 `"Nothing to compact"`）→ `session_before_compact` hook（可 cancel / 可自供结果跳过 LLM）→ `compact(preparation, ...)` 生成摘要 → `session.append_compaction(...)` → `session_compact` 事件 → 返回 `{summary, first_kept_entry_id, tokens_before, details}`。错误归一化为 `AgentHarnessError("compaction")`。

### 5.6 自动压缩（Python 版扩展，pi 放在应用层）

`CompactionSettings.auto_compact=True` 时：`turn_end` 处理完（flush + save_point 后）用 `estimate_context_tokens(session.build_context().messages)` 检查 `should_compact`，命中则在**同一 run 的 turn 间隙**执行 compact 流程（phase 转 `compaction` 再回 `turn`）。失败仅发 warning 日志 + `session_compact` 不发——**自动压缩失败不打断 run**（下轮再试或用户手动 compact）。设计为可选开关是为忠实保留 pi 的应用层控制模式。

### 5.7 branch summarization（navigate_tree 配套）

`collect_entries_for_branch_summary(session, old_leaf, target)`：求两路径的最深公共祖先，收集被放弃分支的 entries。`prepare_branch_entries(entries, token_budget)`：从**尾部**向前入选（预算 = context_window - reserve_tokens），跳过 toolResult。摘要 prompt 同结构化格式（无 update 模式），输出加 `BRANCH_SUMMARY_PREAMBLE` 前缀。`navigate_tree` 流程：`session_before_tree` hook（cancel/自供 summary/改 instructions/打 label）→ 需要时生成摘要 → 目标是 user/custom_message 时移到其 parent 并返回 `editor_text`（编辑重发场景）→ `session.move_to(new_leaf, summary?)` → `session_tree` 事件。

---

## 6. Skills 与 Prompt Templates

### 6.1 Skill 加载（`load_skills(env, dirs)`）

- 目录递归扫描；目录内发现 `SKILL.md` 则加载并**停止**深入该目录其余条目（pi 语义：skill 目录是叶子）；根目录的直接 `.md` 子文件也作为 skill。
- ignore 规则：`.gitignore`/`.ignore`/`.fdignore` 逐目录叠加（相对路径加前缀）。Python 侧用 `pathspec`（gitwildmatch）实现，对齐 pi 的 `ignore` npm 包。
- frontmatter：YAML（`name?/description?/disable-model-invocation?`）；name 缺省取父目录名。
- 校验（warning 诊断，不阻断）：name ≤64 字符、`^[a-z0-9-]+$`、无首尾/连续连字符、与父目录名一致；description 必填 ≤1024（缺失则**跳过该 skill**）。
- 诊断模型：`SkillDiagnostic{type:"warning", code, message, path}`，code ∈ file_info_failed/list_failed/read_failed/parse_failed/invalid_metadata。
- `load_sourced_skills`：带来源标签的批量加载（应用自定义 provenance）。

### 6.2 系统提示注入与显式调用

- `format_skills_for_system_prompt(skills)`：agentskills.io 风格 XML 块（name/description/filePath 列表），`disable_model_invocation=True` 的排除。
- `format_skill_invocation(skill, additional_instructions?)`：skill 全文 + "References are relative to <dir>" 包装，作为 `harness.skill(name)` 的 prompt 文本。

### 6.3 Prompt Templates

- `load_prompt_templates(env, paths)`：目录取直接 `.md` 子文件（非递归）、文件路径直接加载；name = 文件名去 `.md`；description = frontmatter 或首行截 60 字符。
- 参数替换 `substitute_args(content, args)`：`$1..$n`、`$@`、`$ARGUMENTS`、`${@:N}`、`${@:N:L}`；`parse_command_args` 支持单双引号的 shell 风格分词。

### 6.4 依赖

`pyproject.toml` 新增可选依赖组 `harness`：`PyYAML>=6`、`pathspec>=0.11`（并入 `all`）。core 依赖不变——不装 `harness` 组时 `pi_agent_core.harness.skills` import 报友好错误（延迟 import + 提示信息）。

---

## 7. Harness 消息类型（落地审计 C4）

四种自定义 role（pydantic model，与 pi 的 JSON 形状一致）：

```python
class BashExecutionMessage(BaseModel):
    role: Literal["bashExecution"] = "bashExecution"
    command: str; output: str
    exitCode: int | None = None; cancelled: bool = False; truncated: bool = False
    fullOutputPath: str | None = None; timestamp: int
    excludeFromContext: bool = False

class CustomMessage(BaseModel):        # role="custom": customType, content(str|blocks), display, details?, timestamp
class BranchSummaryMessage(BaseModel): # role="branchSummary": summary, fromId, timestamp
class CompactionSummaryMessage(BaseModel):  # role="compactionSummary": summary, tokensBefore, timestamp
```

`harness_convert_to_llm(messages)`（harness 版 `ConvertToLlmFn`，喂给 `AgentLoopConfig.convert_to_llm`）：

- `bashExecution` → user 消息（"Ran \`cmd\`" + 输出代码块 + 退出码/取消/截断注记）；`excludeFromContext=True` 的丢弃
- `custom` → user 消息（content 直转）
- `branchSummary` / `compactionSummary` → user 消息，加 `BRANCH_SUMMARY_PREFIX` / `COMPACTION_SUMMARY_PREFIX` 包装文本
- `user/assistant/toolResult` → 原样
- 未知 role → 丢弃

**审计 C4 的落地方式**：借此定义 `AgentMessageProtocol`（带 `role: str` 的 runtime-checkable Protocol）放 `harness/types.py`，harness 消息全部满足之；core 的 `AgentMessage = Message | Any` 不动（core 零改动原则），README 增自定义消息示例。C4 在审计报告中标记为"经 H2 落地"。

---

## 8. ExecutionEnv

### 8.1 协议（Python 化：异常代替 Result）

pi 的 `Result<T,E>` 全面改为 Python 异常——语义忠实（错误码保留）优先于形式忠实：

```python
class FileError(Exception):      # code: aborted|not_found|permission_denied|not_directory|is_directory|invalid|not_supported|unknown
class ExecutionError(Exception): # code: aborted|timeout|shell_unavailable|spawn_error|callback_error|unknown
class SessionError(Exception)    # code: not_found|invalid_session|invalid_entry|invalid_fork_target|storage|unknown
class CompactionError(Exception) # code: aborted|summarization_failed|invalid_session|unknown
class BranchSummaryError(Exception)  # code: aborted|summarization_failed|invalid_session
class AgentHarnessError(Exception)   # code: busy|invalid_state|invalid_argument|session|hook|auth|compaction|branch_summary|unknown
```

归一化函数 `normalize_harness_error(exc, fallback_code)`：Session/Compaction/BranchSummary 错误映射到对应顶层 code，其余用 fallback——对齐 pi 的 `normalizeHarnessError`。

```python
class FileSystem(Protocol):
    cwd: str
    async def absolute_path(self, path) -> str: ...
    async def read_text_file(self, path) -> str: ...
    async def read_text_lines(self, path, max_lines=None) -> list[str]: ...
    async def read_binary_file(self, path) -> bytes: ...
    async def write_file(self, path, content) -> None: ...
    async def append_file(self, path, content) -> None: ...
    async def file_info(self, path) -> FileInfo: ...       # name/path/kind/size/mtime_ms，不追 symlink
    async def list_dir(self, path) -> list[FileInfo]: ...
    async def canonical_path(self, path) -> str: ...
    async def exists(self, path) -> bool: ...
    async def create_dir / remove / create_temp_dir / create_temp_file ...
    async def cleanup(self) -> None: ...                    # 尽力而为，不抛

class Shell(Protocol):
    async def exec(self, command, *, cwd=None, env=None, timeout=None,
                   signal=None, on_stdout=None, on_stderr=None) -> ExecResult:  # {stdout, stderr, exit_code}
    async def cleanup(self) -> None: ...

class ExecutionEnv(FileSystem, Shell, Protocol): ...
```

### 8.2 LocalExecutionEnv（默认实现）

文件操作 = `pathlib` + `asyncio.to_thread`；shell = `asyncio.create_subprocess_shell`（Windows 用默认 shell，超时 kill 进程组）。协议放 H1（session 存储只依赖 FileSystem 的 4 个方法），完整 Local 实现放 H4（skills/shell 需要）。JsonlSessionStorage 对 FileSystem 的依赖收窄为 `Pick` 等价的小协议 `JsonlStorageFs`（read_text_file/read_text_lines/write_file/append_file），测试可用轻量 fake。

---

## 9. 实施批次

| 批次 | 内容 | 前置 | 交付判据 |
|---|---|---|---|
| **H1** | `harness/types.py`（错误层级 + entry 模型 + 协议）、`uuid7`、Session/build_session_context、Jsonl/Memory Storage+Repo、`JsonlStorageFs` 小协议 | 无 | ✅ 已实施（2026-07-03）：JSONL 往返 + 与手工构造的 pi v3 样例文件互读；树/分支/回放单测 |
| **H2** | harness 消息 + `harness_convert_to_llm`、AgentHarness 主类（phase/队列/写缓冲/事件/hook/run 失败合成）、与 core loop 接线 | H1 | mock stream 驱动的端到端 prompt；持久化时序（4.4 三条不变量）与 hook 语义单测 |
| **H3** | compaction utils/估算/cut point/摘要、`complete_simple`、compact()、branch summarization、navigate_tree、auto_compact | H2 | cut point 与 split-turn 单测（合成 entries）；摘要走 mock stream；SiliconFlow 实测一次真实压缩 |
| **H4** | skills、prompt templates、system-prompt 注入、ExecutionEnv Local 完整实现、`harness` 可选依赖组 | H1（env 协议） | skills 加载诊断/ignore/校验单测；模板参数替换单测；示例 `examples/harness_agent.py` |

每批次完成：ruff + 全量 pytest + 提交推送 + 审计报告勾选。

## 10. 测试策略

延续 mock stream 模式（无 API key）：

- **格式兼容**：签入 `pi_agent_core/tests/fixtures/pi-v3-session.jsonl`（手工按 pi 格式构造，含分支/label/leaf/compaction），断言读取回放正确；我们写出的文件再读回 == 原树（往返）。
- **时序不变量**：`message_end` 先落盘后广播（listener 内查 session 断言已存在）；turn 中 setter 延迟到下轮生效（`prepare_next_turn` 快照）；hook 异常回滚队列。
- **cut point**：合成 entry 序列覆盖——纯对话 / toolResult 邻接不可切 / split-turn 前缀摘要 / 连续 compaction 迭代更新。
- **run 失败合成**：mock stream 抛异常 → 断言合成失败消息落盘且事件流闭合（`agent_end` 可达、phase 回 idle）。
- **skills**：临时目录构造 SKILL.md 变体（无 frontmatter / name 不合法 / description 超长 / ignore 命中），断言诊断码。
- **真实 API 冒烟**（H3 后）：`scripts/smoke_real_api.py` 增 harness 场景——多轮对话触发 auto_compact，SiliconFlow 实测。

---

## 附：风险与缓解

| 风险 | 缓解 |
|---|---|
| pi v3 格式细节偏差（字段可选性、时间戳格式） | fixtures 用真实 pi 会话文件片段校验；entry 模型 `model_config = ConfigDict(extra="allow")` 宽容未知字段并在写回时保留 |
| harness 与 `Agent` 类行为分叉（两套队列实现） | 队列语义单测共享参数化用例；文档明确"二选一"使用建议 |
| `AgentHarnessStreamOptions.headers/metadata` 在部分 LangChain provider 不生效 | H2 实测三 provider，不支持的记诊断日志，文档标注支持矩阵 |
| Windows 路径分隔符 vs pi 的 POSIX 风格 env path 函数 | FileSystem 协议统一返回 POSIX 风格（内部 `PurePosixPath` 归一），仅 LocalExecutionEnv 边界转换 |
| compaction 摘要质量依赖模型 | `session_before_compact` hook 允许应用自供摘要；prompt 忠实 pi（经实战验证） |
