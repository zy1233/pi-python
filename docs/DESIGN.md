# pi-python 项目设计方案

> 基于 [earendil-works/pi](https://github.com/earendil-works/pi) 的 `@earendil-works/pi-agent-core`，
> 用 LangChain 替代 `pi-ai` 作为 LLM 层的 Python 移植。
>
> 本文是项目整体设计文档，涵盖已完成的核心运行时、生产化增强、AgentHarness、工具生态，
> 以及 Phase 4 Coding Agent CLI（fork grok TUI + 标准 ACP）。各阶段的完整详细设计见独立 spec 文档（§11 索引）。

---

## 1. 目标与原则

| 项 | 说明 |
|---|---|
| 定位 | 非官方 Python 移植，忠实复现 pi 的 agent 循环语义，最终目标是构建完整的 Coding Agent CLI |
| LLM 层 | LangChain `BaseChatModel.astream()`，不移植 pi-ai 多厂商 registry |
| 核心原则 | **忠实移植 pi 循环语义**；**LangChain 仅作 StreamFn 边界适配**——工具执行、turn 管理、事件协议均在 `agent_loop.py`，不委托 LangChain agents/ToolNode |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│              Coding Agent CLI (Phase 4, P0–P2 landed)           │
│   forked grok TUI (ACP client) · pi_agent_cli (ACP agent)       │
│              标准 ACP stdio · 权限确认 · JSONL v3                 │
├─────────────────────────────────────────────────────────────────┤
│                        AgentHarness (Phase 3)                   │
│  Session 树 · phase 锁 · 三队列 · 写缓冲 · 19 种事件/hook            │
│  Compaction · Skills / Templates · SystemPrompt · ExecutionEnv  │
├─────────────────────────────────────────────────────────────────┤
│                        Tool Ecosystem (P6)                      │
│  read · bash · edit · write · grep · find · ls                  │
│  LangChain BaseTool 适配器 · MCP (via langchain-mcp-adapters)    │
├─────────────────────────────────────────────────────────────────┤
│                        Core Runtime (Phase 1–2.5)               │
│  Agent · agent_loop · 事件协议 · StreamFn                         │
│  transform_messages · usage/cost · thinking/reasoning           │
│  retries · max_turns · guardrail hooks · ContextBudget          │
│  structured output · tool-result images                         │
├─────────────────────────────────────────────────────────────────┤
│                        LangChain Adapter                        │
│  langchain_stream · langchain_convert · resolve_chat_model      │
│  OpenAI / Anthropic / DeepSeek / init_chat_model                │
└─────────────────────────────────────────────────────────────────┘
```

### 消息流水线

```
AgentMessage[] → transform_context() → convert_to_llm() → transform_messages()
                                                              ↓
                                                    convert_to_langchain()
                                                              ↓
                                                    stream_fn (LangChain astream)
                                                              ↓
                                                    AssistantMessageEvent → AgentEvent
```

### 模块完整对照

| Python 模块 | 职责 | pi (TS) 对应 |
|---|---|---|
| `pi_agent_core/messages.py` | 规范消息（user/assistant/toolResult）、content blocks、`Usage` | `pi-ai` messages |
| `pi_agent_core/types.py` | `Model`、`AgentTool`、contexts、event types、`AgentLoopConfig` | `packages/agent/src/types.ts` |
| `pi_agent_core/event_stream.py` | `EventStream` / `AssistantMessageEventStream` | `pi-ai` EventStream |
| `pi_agent_core/agent_loop.py` | 核心循环：turns、工具执行、hooks、事件发射 | `packages/agent/src/agent-loop.ts` |
| `pi_agent_core/agent.py` | 有状态 `Agent` 封装：prompt/steer/follow-up 队列、abort | `packages/agent/src/agent.ts` |
| `pi_agent_core/transform.py` | 跨 provider 回放（tool-call id 规范化、thinking 降级、图片剥离） | `pi-ai` transforms |
| `pi_agent_core/tools.py` | `SimpleTool` 辅助工厂 | — |
| `pi_agent_core/validation.py` | Pydantic 参数校验（替代 pi 的 TypeBox） | — |
| `pi_agent_core/queues.py` | steering / follow-up 消息队列 | — |
| `pi_agent_core/adapters/langchain_stream.py` | `StreamFn`：LangChain `astream()` → pi 事件 | `packages/ai/src/stream.ts` |
| `pi_agent_core/adapters/langchain_convert.py` | pi ⇄ LangChain 消息互转 | `packages/ai/src/stream.ts` |
| `pi_agent_core/adapters/langchain_tools.py` | LangChain `BaseTool` → `AgentTool` 适配 | 无（Python 特有） |
| `pi_agent_core/coding_tools/` | 7 个内置编码工具 + 截断/路径/互斥公共设施 | `packages/coding-agent/src/core/tools/` |
| `pi_agent_harness/` | Session 树、AgentHarness、Compaction、Skills/Templates、Env | `packages/agent/src/harness/` |

---

## 3. 事件契约（不可破坏）

`prompt("Hello")` 无工具时：

```
agent_start → turn_start → message_start(user) → message_end(user)
→ message_start(assistant) → message_update* → message_end(assistant)
→ turn_end → agent_end
```

有工具时：在 `message_end(assistant)` 后插入 `tool_execution_*` 与 `toolResult` message events，可能跨多轮 `turn_start`。

`message_update` 包装粒度 assistant-stream 事件：`text_start/delta/end`、`thinking_start/delta/end`、`toolcall_start/delta/end`。end 事件携带聚合结果。

### 关键不变量

1. **并行工具顺序** — `tool_execution_end` 按**完成顺序**发射；`toolResult` 消息按 tool call **源顺序**持久化。
2. **terminate 语义** — 仅当同一批次**全部** finalized 工具结果的 `terminate=True` 时，跳过下一轮 LLM。
3. **StreamFn 契约** — 不向调用方抛异常；失败编码为 `error` 事件 + `stop_reason=error|aborted`。
4. **Thinking/reasoning 门控** — reasoning 参数仅在 `Model.reasoning=True` **且** `thinking_level != "off"` 时注入；同一标志驱动 `transform_messages` 的 thinking 历史剥除。
5. **Usage 累积取 per-field max** — 不求和（避免累积快照被放大）。
6. **结构化输出不破坏流式** — `response_schema` 走 prompt 注入 + 原生 `response_format`；不用 `with_structured_output`。

---

## 4. Phase 1：核心运行时

> 详细设计原稿：`docs/PLAN/PLAN-PHASE1.md`

### 4.1 类型与消息模型 (`types.py`, `messages.py`)

- **三种规范消息**：`UserMessage`、`AssistantMessage`、`ToolResultMessage`
- **Content blocks**：`TextContent`、`ThinkingContent`、`ToolCallContent`、`ImageContent`
- **事件联合类型**：`AgentEvent`（`agent_start/end`、`turn_start/end`、`message_*`、`tool_execution_*`）
- **Model** dataclass：`provider`、`model_id`、`context_window`、`supports_images`、`reasoning`、`base_url`
- **AgentTool** Protocol：`name`/`description`/`label`/`parameters`/`execute`/`execution_mode`/`prepare_arguments`

### 4.2 Agent 循环 (`agent_loop.py`)

忠实移植 pi `agent-loop.ts` 的双层循环：

- **内层**：LLM 流式响应 → 解析 tool calls → validate → `before_tool_call` hook → 并行/串行执行 → `after_tool_call` → terminate 判定
- **外层**：steering 消息注入（turn 边界）→ follow-up 消息注入（无 tool/steering 时）
- **关键 hooks**：`should_stop_after_turn`、`prepare_next_turn`、`before/after_tool_call`

### 4.3 Agent 类 (`agent.py`)

有状态封装：`prompt()`/`continue_()`/`abort()`/`wait_for_idle()`/`reset()`/`steer()`/`follow_up()`。`subscribe()` 监听器按注册顺序 await，`agent_end` 为 settlement 屏障——`is_streaming` 在全部监听器完成后才清零。

### 4.4 LangChain 适配层 (`adapters/`)

- **`langchain_stream.py`**：`StreamFn` 实现——`resolve_chat_model(model)` 构造 `ChatOpenAI`/`ChatAnthropic`/`ChatDeepSeek`（或 `init_chat_model` 通用路径）；`bind_tools` 绑定工具 schema；`astream` 流式映射 `text_delta`/`toolcall_delta`/`done`/`error`
- **`langchain_convert.py`**：pi `Message` ⇄ LangChain `BaseMessage` 双向转换；system prompt 注入；tool call id/content 规范化

---

## 5. Phase 2 / 2.5：生产化增强

> 详细设计：`docs/specs/2026-05-25-phase2-production-enhancements-design.md`
> 审计与 P2.5 增强记录：`docs/AUDIT/AUDIT-2026-07-02.md`

### 5.1 Usage / Cost 追踪

从 LangChain 流式 chunk 的 `usage_metadata` 逐 chunk 累加（对齐 LangChain `add_usage` 语义），全 provider 统一读标准化字段。`cost_calculator` 回调将 token 数转为金额。三种真实 provider 报告形态（单次终报、互补分片、累积快照）均正确处理。

### 5.2 Thinking / Reasoning

`ThinkingLevel`（off/minimal/low/medium/high/xhigh）映射为 provider 参数：Anthropic `thinking.budget_tokens` + 联动 `max_tokens`；OpenAI `reasoning_effort`。Anthropic thinking 块流式捕获为 `ThinkingContent`（含 `signature` 用于多轮工具回放），以 `thinking_delta` 事件实时发射。DeepSeek `reasoning_content` 同样捕获。

### 5.3 跨 Provider 消息回放 (`transform.py`)

```
messages → normalize_tool_call_ids → downgrade_thinking → strip_unsupported_images → result
```

- **normalize_tool_call_ids**：OpenAI `call_xxx` ⇄ Anthropic `toolu_xxx`，维护映射保证 toolCall/toolResult 配对
- **downgrade_thinking**：`target_model.reasoning=False` 时剥除 `ThinkingContent`
- **strip_unsupported_images**：`supports_images=False` 时移除 `ImageContent`，纯图片消息替换为占位文本

### 5.4 流式重试与失控保护

- **重试**：首 token 前指数退避 + 抖动（`Retry-After` 感知），可配 `max_retries`
- **`max_turns`**：超限抛 `MaxTurnsExceededError`，Agent 转为 error-stop assistant message
- **`tool_timeout`**：单工具超时 → error tool result（LLM 可见可自纠）

### 5.5 Guardrail Hooks 与可观测性

- **`before_llm_call(context, budget)`**：ContextBudget token 信号——压缩钩子挂载点
- **`after_llm_call(context, message)`**：tripwire on raise（内容审查等）
- **`on_agent_end(context, messages)`**：run 结束后收尾
- **`on_payload` / `on_response`**：观测裸请求/响应
- 每个事件携带 `run_id` / 1-based `turn_id`

### 5.6 结构化输出

`response_schema`（pydantic model 或 JSON schema dict）→ prompt 注入 + OpenAI-style 原生 `response_format` → 解析结果存 `AssistantMessage.structured_output`。流式不受影响——JSON 文本仍以 `text_delta` 发射。

### 5.7 工具结果图片

Anthropic 原生 image blocks；其他 provider 回退为 user-message 注入；`supports_images=False` 时自动剥离。

---

## 6. Phase 3：AgentHarness

> 详细设计：`docs/specs/2026-07-03-phase3-agent-harness-design.md`
> 审计：`docs/AUDIT/AUDIT-H1.md` ~ `docs/AUDIT/AUDIT-H4.md`

独立发行包 `pi-agent-harness`（`packages/pi-agent-harness/pi_agent_harness/`），依赖 `pi-agent-core`。

### 6.1 Session 树与 JSONL v3 存储

- **与 pi v3 字节兼容**的 append-only JSONL 格式：首行 header，之后每行一个 entry
- **11 种 SessionTreeEntry**（pydantic discriminated union）：message、thinkingLevelChange、modelChange、activeToolsChange、compaction、branchSummary、custom、customMessage、label、sessionInfo、leaf
- **树结构**：`parentId` 构成树；分支 = 从任意历史 entry 续写；leaf entry 实现叶子指针移动
- **两种存储**：`JsonlSessionStorage`（文件 I/O + 内存索引）、`MemorySessionStorage`（纯内存，测试用）
- **Repo**：create/open/list/delete/fork；fork 语义忠实对齐 pi `getEntriesToFork`

### 6.2 AgentHarness 主类

**平行于 `Agent`，不包 `Agent` 类**——两者都直接驱动 `run_agent_loop`。

- **Phase 锁**：`idle`/`turn`/`compaction`/`branch_summary`/`retry`——单占用模型
- **三队列**：steer、follow_up（drain 时发 `queue_update`）、next_turn（任何时刻可入队，下次 `prompt()` 时注入）
- **写缓冲**：turn 中 setter 不直接写 session，flush 在 turn 边界按序重放
- **事件系统**：11 种 broadcast + 8 种 hook（互斥通道，hook 带返回值）
- **持久化时序不变量**：`message_end` → 先落盘后广播；`turn_end` → 先广播再 flush 再 `save_point`；`agent_end` → flush + idle + `settled`
- **Run 失败合成**：loop 异常逃逸时构造失败 assistant message，完整走 `_handle_agent_event` 保证事件流闭合

### 6.3 Compaction

- **token 估算**：`estimate_context_tokens` 混合真实 usage + chars/4 启发式
- **Cut point 算法**：从尾部向前累积直到 `keep_recent_tokens`，找合法切点（toolResult 不可切）；split-turn 检测 + 前缀单独摘要
- **结构化摘要**：Goal / Constraints / Progress / Key Decisions / Next Steps / Critical Context；迭代更新模式保留旧信息
- **auto_compact**（Python 扩展）：`turn_end` 后检查 `should_compact`，命中则在 turn 间隙执行——失败不打断 run

### 6.4 分支导航 (`navigate_tree`)

求两路径的最深公共祖先，收集被放弃分支 entries 生成 branch summary。`session_before_tree` hook 可 cancel / 自供 summary / 打 label。

### 6.5 Skills 与 Prompt Templates

- **Skills**：递归扫描目录，`SKILL.md` 为叶子；YAML frontmatter（name/description/disable-model-invocation）；`.gitignore`/`.ignore`/`.fdignore` 叠加忽略；诊断模型（warning 不阻断）
- **Prompt Templates**：目录取 `.md` 子文件；`$1..$n`/`$@`/`$ARGUMENTS`/`${@:N:L}` 参数替换；shell 风格引号分词
- **系统提示注入**：`format_skills_for_system_prompt` 输出 agentskills.io 风格 XML 块

### 6.6 ExecutionEnv

- **FileSystem + Shell 两个 Protocol**（`@runtime_checkable`）；Python 化：异常代替 pi 的 `Result<T,E>`
- **LocalExecutionEnv**：`pathlib` + `asyncio.to_thread`（FS）；`asyncio.create_subprocess_shell`（Shell，Windows kill 进程组）
- **错误层级**：`FileError`/`ExecutionError`/`SessionError`/`CompactionError`/`BranchSummaryError`/`AgentHarnessError`，归一化函数 `normalize_harness_error`

### 6.7 Harness 自定义消息

四种自定义 role：`bashExecution`、`custom`、`branchSummary`、`compactionSummary`。`harness_convert_to_llm` 决定各 role 如何到达 LLM（bashExecution → user 消息含代码块；未知 role → 丢弃）。

---

## 7. P6：工具生态

> 详细设计：`docs/specs/2026-07-03-p6-tool-ecosystem-design.md`

### 7.1 内置编码工具

归层在 `pi_agent_core/coding_tools/`——工具是 loop 的消费者，不是 loop 的一部分。7 个工具均实现 `AgentTool` 协议，不反向依赖 agent_loop/Agent。零新增运行时依赖。

| 工具 | 归组 | 核心行为 |
|---|---|---|
| `read` | 编程 + 只读 | 文本 2000 行/50KB 截断 + offset/limit 分页；图片魔数嗅探 → ImageContent |
| `bash` | 编程 | 真实 shell（bash -c / Git Bash on Windows）；合并 stdout+stderr 尾部截断；超时/abort 杀进程树；100ms 节流流式更新 |
| `edit` | 编程 | 精确文本替换（唯一匹配保证、重叠拒绝、CRLF/BOM 往返）；unified-diff details |
| `write` | 编程 | 创建/覆盖 + 自动递归建目录；经 mutation_queue 串行 |
| `grep` | 只读 | ripgrep 优先（`--json` 流式解析）；纯 Python 回退；单行 500 字符截断 |
| `find` | 只读 | 纯 Python glob walk；basename vs 路径 pattern 语义对齐 pi 对 fd 的调用 |
| `ls` | 只读 | 大小写不敏感排序，`/` 目录后缀，含 dotfiles |

### 7.2 公共设施

- **`truncate.py`**：`truncate_head`/`truncate_tail`/`truncate_line` + `TruncationResult`，数值与 pi 逐项相同
- **`mutation_queue.py`**：按 `realpath` 归一的 `asyncio.Lock` 注册表，write/edit 同文件互斥
- **`path_utils.py`**：`resolve_to_cwd`、glob→regex 翻译、图片魔数嗅探（stdlib 实现，不依赖 Pillow）

### 7.3 工厂 API

```python
create_coding_tools(cwd)       # read/bash/edit/write（pi 默认组）
create_read_only_tools(cwd)    # read/grep/find/ls（无修改保证）
create_tool(name, cwd)         # 按名构造单工具
create_all_tools(cwd)          # 全部 7 个
```

### 7.4 LangChain 工具适配器

`from_langchain_tool(tool: BaseTool) -> AgentTool`：schema 提取（`tool_call_schema` 优先，剔除 LangChain 注入参数）、结果归一（str → text 块、content blocks 列表 → text/image 映射、artifact → details）、异常冒泡为 error tool result。MCP 工具经 `langchain-mcp-adapters` 产出的 `BaseTool` 走同一适配器。

---

## 8. Phase 4：Coding Agent CLI

> 详细设计：`docs/specs/2026-08-25-phase4-coding-agent-cli-design.md`
> TUI 上游：[xai-org/grok-build](https://github.com/xai-org/grok-build)（Apache-2.0）；协议：[ACP](https://agentclientprotocol.com)

**不自研 Python TUI。** Fork grok-build 的 `pi-grok-pager` 作 ACP Client；本仓库 Python 作 ACP Agent，只走标准 ACP，**不实现 `x.ai/*`**。

### 8.1 进程边界

```
pi (Rust TUI)  --stdio 标准 ACP-->  python -m pi_agent_cli  -->  AgentHarness
```

- `pi`：全屏 TUI（Markdown/diff/权限 modal），spawn Python agent。
- `pi acp` / Zed：同一 agent 给编辑器。
- `pi -p`：纯 Python headless。
- 会话真源：JSONL v3。TUI 本地缓存不是真源。

### 8.2 仓库

整棵 grok-build Cargo workspace 已放进 `tui/`（不要只拷 pager 两 crate）。`tui/` Apache-2.0，不进 Python sdist/wheel。

### 8.3 TUI 改造要点

spawn 默认为 `python -m pi_agent_cli`（`PI_AGENT_COMMAND` / `PI_PYTHON` 可覆盖）。`acp_send` 丢弃 `x.ai/*` 扩展 RPC；空 `auth_methods` 跳过 xAI 登录；auto-update 关闭；交互路径不装 otel。家目录 `PI_HOME` / `~/.pi-python`。无标准 ACP 对照的 slash（`/compact` `/model` `/rewind` 等）仍待 P3 从菜单拿掉。

### 8.4 权限与配置

`before_tool_call` 对 bash/edit/write 发 ACP `session/request_permission`。`~/.pi-python/config.toml`：model、permission 模式、skills、`agent.command`。

---

## 9. 测试策略

全量 Mock `stream_fn`，无需 API key，镜像 pi `agent-loop.test.ts` 场景。

| 域 | 覆盖要点 |
|---|---|
| 核心循环 | 事件序列（纯对话/单工具/多工具/parallel/sequential）、steering、follow-up、abort、`should_stop_after_turn` |
| Usage/Thinking | mock chunk 带 `usage_metadata`/thinking 块，验证提取与累积 |
| Transform | tool-call id 跨 provider 重写、thinking 降级、图片剥离、端到端混合场景 |
| Harness | JSONL 往返（与 pi v3 fixture 互读）、持久化时序不变量、hook/队列、turn 边界 setter、run 失败合成 |
| Compaction | cut point / toolResult 非切点 / LLM 摘要 / hook 自供 / branch summary / auto_compact |
| Skills/Templates | frontmatter 解析、ignore 规则、校验诊断、参数替换 |
| 编码工具 | 截断边界、offset/limit 组合、图片魔数、edit 唯一匹配/重叠/CRLF 往返、bash 三终态、grep rg+回退、mutation queue 串行 |
| LangChain 适配 | str/blocks/artifact 返回归一、schema 提取、异常冒泡 |
| Real-API 冒烟 | `scripts/smoke_real_api.py` 对接 SiliconFlow（OpenAI 兼容） |

---

## 10. 依赖

### 核心 (`pi-agent-core-lc`)

- `pydantic>=2.0`、`langchain-core>=0.3.0`、`typing-extensions>=4.6`
- 可选 providers：`langchain-openai`、`langchain-anthropic`、`langchain-deepseek`

### Harness (`pi-agent-harness-lc`)

- 依赖 `pi-agent-core-lc`
- `PyYAML>=6`（skills frontmatter）、`pathspec>=0.12`（gitignore 匹配）

### 开发

- `pytest>=8.0`、`pytest-asyncio>=0.24`、`ruff>=0.16`

---

## 11. 文档索引

| 文档 | 内容 |
|---|---|
| `AGENTS.md` | 项目总览、模块表、不变量、当前状态 |
| `README.md` | 公开 README、Quick start、Roadmap |
| `docs/DESIGN.md`（本文） | 项目整体设计方案 |
| `docs/PLAN/PLAN-PHASE1.md` | Phase 1 原始规划（历史） |
| `docs/AUDIT/AUDIT-2026-07-02.md` | 核心层审计：缺陷修复 + Phase 2.5 增强追踪 |
| `docs/AUDIT/AUDIT-H1.md` ~ `docs/AUDIT/AUDIT-H4.md` | Harness 各批次审计 |
| `docs/AUDIT/SPIKE-P0-GROK-TUI.md` | Phase 4 P0：pager `x.ai/*` 拆除清单 |
| `docs/specs/2026-05-25-phase2-*.md` | Phase 2 详细设计：usage/cost、thinking、transform |
| `docs/specs/2026-07-03-phase3-*.md` | Phase 3 详细设计：Session 树、AgentHarness、Compaction、Skills、Env |
| `docs/specs/2026-07-03-p6-*.md` | P6 详细设计：7 个内置工具 + LangChain 适配器 |
| `docs/specs/2026-08-25-phase4-coding-agent-cli-design.md` | Phase 4：fork grok TUI + 标准 ACP agent |
