# pi-agent-core Python 详细设计方案

> 基于 [earendil-works/pi](https://github.com/earendil-works/pi) 的 `@earendil-works/pi-agent-core`，用 LangChain 替代 `pi-ai`。

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
    provider: str   # openai, anthropic
    model_id: str
    api: str = "langchain"
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

## 6. Phase 2/3（本次不实现）

- `transform_messages` 跨厂商回放
- AgentHarness：Session JSONL、Compaction、Skills
- `stream_proxy`

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
| `pi_agent_core/tests/` | 9 tests 通过 |
| `examples/minimal_agent.py` | 完成 |

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

tool = SimpleTool(name="echo", description="Echo", label="Echo", parameters=EchoParams, execute_fn=echo)
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
