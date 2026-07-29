# pi-agent-core Python 详细设计方案

> 基于 [earendil-works/pi](https://github.com/earendil-works/pi) 的 `@earendil-works/pi-agent-core`，用 LangChain 替代 `pi-ai`。
>
> 本文是 Phase 1 的原始设计。Phase 2（usage/thinking/transform）见
> `docs/superpowers/specs/2026-05-25-phase2-production-enhancements-design.md`；
> Phase 2.5（重试/失控保护/观测/粒度事件/guardrail 钩子/预算信号/结构化输出）的
> 设计决策与实施记录见 `docs/AUDIT-2026-07-02.md` 第三部分。当前 API 全貌以
> README 为准。

## 1. 目标

| 项 | 说明 |
|---|---|
| 范围 | Phase 1 MVP：`Agent` + `agent_loop` + 事件协议 + 工具执行 |
| LLM 层 | LangChain `BaseChatModel.astream()`，不移植 pi-ai 多厂商 registry |
| 原则 | 忠实移植 pi 循环语义；LangChain 仅作 `StreamFn` 边界适配 |

## 2. 架构

```
AgentMessage[] → transform_context() → convert_to_llm() → LangChain BaseMessage[]
                                                              ↓
                                                    stream_fn (LangChain astream)
                                                              ↓
                                                    AssistantMessageEvent → AgentEvent
```

### 模块对照

| pi (TS) | Python |
|---------|--------|
| `packages/agent/src/types.ts` | `pi_agent_core/types.py` |
| `packages/agent/src/agent-loop.ts` | `pi_agent_core/agent_loop.py` |
| `packages/agent/src/agent.ts` | `pi_agent_core/agent.py` |
| `packages/ai/src/stream.ts` | `pi_agent_core/adapters/langchain_stream.py` |
| `pi-ai` messages | `pi_agent_core/messages.py` |

## 3. 事件契约（不可破坏）

`prompt("Hello")` 无工具时：

```
agent_start → turn_start → message_start(user) → message_end(user)
→ message_start(assistant) → message_update* → message_end(assistant)
→ turn_end → agent_end
```

有工具时：在 `message_end(assistant)` 后插入 `tool_execution_*`，再 `message_start/end(toolResult)`，可能多轮 `turn_start`。

### 并行工具顺序

- `tool_execution_end`：按**完成顺序**发射
- `toolResult` 消息：按 assistant 中 tool call **源顺序**持久化

### terminate 语义

仅当同一批次**全部** finalized 工具结果的 `terminate=True` 时，跳过下一轮 LLM。

## 4. 类型设计

### Model

```python
@dataclass
class Model:
    provider: str  # openai, anthropic, deepseek（OpenAI 兼容网关）, 其他走 init_chat_model
    model_id: str
    api: str = "langchain"
    context_window: int = 128_000  # ContextBudget 预算信号的分母
    supports_images: bool = True
    reasoning: bool = False  # thinking 参数注入与历史剥除的总开关
    base_url: str | None = None  # OpenAI 兼容网关（对齐 pi 的 baseUrl）
```

### AgentTool

- `name`, `description`, `label`
- `parameters`: `type[BaseModel]` 或 `dict`（JSON Schema，MVP 主要用 Pydantic）
- `execute(tool_call_id, params, signal, on_update) -> AgentToolResult`
- `execution_mode`: parallel | sequential
- `prepare_arguments?`

### StreamFn

```python
async def stream_fn(model, context: LlmContext, options) -> AssistantMessageEventStream
```

契约：不向调用方抛异常；失败编码为 `error` 事件 + `stop_reason=error|aborted`。

## 5. LangChain 适配

1. `convert_to_langchain(messages)` → `HumanMessage` / `AIMessage` / `ToolMessage`
2. `resolve_chat_model(model)` → `ChatOpenAI` / `ChatAnthropic`
3. `bound = model.bind_tools(lc_tools)`（工具执行仍在 agent_loop，非 ToolNode）
4. `astream` → 映射 `text_delta`、`toolcall_delta`、`done`

## 6. 后续阶段状态

- Phase 2（已完成）：`transform_messages` 跨厂商回放、usage/cost、thinking/reasoning
- Phase 2.5（已完成）：重试/退避、`max_turns`/`tool_timeout`、`on_payload`/`on_response`、
  run_id/turn_id、粒度事件、工具结果图片、`before/after_llm_call`/`on_agent_end`、
  `ContextBudget`、`response_schema` 结构化输出（详见审计报告第三部分）
- Phase 3（设计已完成，待实施）：AgentHarness——Session 树（pi v3 兼容 JSONL）、
  Compaction、Skills、Prompt Templates、ExecutionEnv，详见
  `docs/superpowers/specs/2026-07-03-phase3-agent-harness-design.md`（H1–H4 四批）

## 7. 测试策略

Mock `stream_fn` 无需 API Key，镜像 pi `agent-loop.test.ts` 场景。

## 8. 依赖

- 核心：`pydantic>=2`, `langchain-core`
- 可选：`langchain-openai`, `langchain-anthropic`
- 开发：`pytest`, `pytest-asyncio`

## 9. Phase 1 实现清单（已完成）

| 文件 | 状态 |
|------|------|
| `pi_agent_core/messages.py` | 完成 |
| `pi_agent_core/types.py` | 完成 |
| `pi_agent_core/event_stream.py` | 完成 |
| `pi_agent_core/validation.py` | 完成 |
| `pi_agent_core/queues.py` | 完成 |
| `pi_agent_core/agent_loop.py` | 完成 |
| `pi_agent_core/agent.py` | 完成 |
| `pi_agent_core/adapters/langchain_*.py` | 完成 |
| `pi_agent_core/tests/` | 完成（Phase 2.5 后共 80 tests） |
| `examples/minimal_agent.py` | 完成（另见 `examples/production_agent.py`） |

## 10. 使用说明

### 定义工具

```python
from pydantic import BaseModel, Field
from pi_agent_core.tools import SimpleTool
from pi_agent_core.types import AgentToolResult


class EchoParams(BaseModel):
    message: str = Field(description="text")


async def echo(_id, params: EchoParams, signal, on_update) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": params.message}], details={})


tool = SimpleTool(
    name="echo", description="Echo", label="Echo", parameters=EchoParams, execute_fn=echo
)
```

### 自定义 StreamFn（测试 / 代理）

```python
from pi_agent_core.tests.mock_stream import mock_text_stream

agent = Agent(stream_fn=mock_text_stream, ...)
```

### 无 API Key 运行示例

```bash
PI_USE_MOCK=1 python3 examples/minimal_agent.py
```
