# Phase 2 生产化增强 — 设计方案

> Scope: Usage/cost + thinking/reasoning + transform_messages
> 基于 Phase 1 MVP（Agent + agent_loop + 事件协议 + LangChain 适配器，9 tests 通过）

---

## 1. Usage / cost

### 目标

从 LangChain 流式响应中提取 token 用量，填充已有的 `Usage` 模型；通过 `cost_calculator` 回调钩子支持金额换算。

### 提取映射

> 2026-07-02 修订（审计 B2）：原表按 provider 分支、且从"最终 chunk"读取，两点都是错的——
> LangChain 已把各家字段标准化进 `usage_metadata`（Anthropic 的 cache 字段同样落在
> `input_token_details`，见 langchain-anthropic `_create_usage_metadata`），且部分 provider
> 将 usage 分片在多个 chunk 上报。现行实现：**逐 chunk 累加**（对齐 LangChain `add_usage`
> 语义），全 provider 统一读标准化字段。

| LangChain 字段（标准化，provider 无关） | Usage 字段 |
|---|---|
| `input_tokens` | `usage.input` |
| `output_tokens` | `usage.output` |
| `input_token_details.cache_read` | `usage.cacheRead` |
| `input_token_details.cache_creation` | `usage.cacheWrite` |
| `total_tokens` | `usage.totalTokens` |
| `output_token_details.reasoning` | `usage.reasoningTokens` |

内部函数：`_merge_usage_meta(acc, meta)`（逐 chunk 逐字段累加）+ `_usage_from_meta(acc) -> Usage`。
另：ChatOpenAI 需显式 `stream_usage=True`（旧版默认不随流返回 usage）。

### cost_calculator 回调

`StreamOptions` 新增字段：

```python
@dataclass
class StreamOptions:
    ...
    cost_calculator: Callable[[Usage, Model], UsageCost] | None = None
```

逻辑：
- `cost_calculator` 存在 → 调用，结果写入 `usage.cost`
- 不存在 → `usage.cost` 保持全零默认值

### Agent 层暴露

`Agent.__init__` 新增可选参数 `cost_calculator`，自动传入 `StreamOptions`。

### 影响文件

- `langchain_stream.py`：提取 usage、调用 cost_calculator
- `types.py`：StreamOptions 新增 cost_calculator
- `messages.py`：Usage 新增 reasoningTokens 字段
- `agent.py`：传递 cost_calculator

---

## 2. thinking / reasoning

### 目标

将 `ThinkingLevel` 映射为 provider 参数，捕获 thinking 文本写入 `ThinkingContent`。

### ThinkingLevel → provider 参数映射

| ThinkingLevel | Anthropic (`budget_tokens`) | OpenAI (`reasoning_effort`) |
|---|---|---|
| off | 不传 / disabled | 不传 |
| minimal | 1,024 | "low" |
| low | 4,096 | "low" |
| medium | 10,000 | "medium" |
| high | 20,000 | "high" |
| xhigh | 40,000 | "high" |

传入方式（2026-07-02 修订，审计 B6/D5）：
- **Anthropic**：顶层构造参数 `thinking={"type": "enabled", "budget_tokens": N}`，并联动
  `max_tokens = budget + headroom`（Anthropic 要求 `max_tokens > budget_tokens`）
- **OpenAI**：顶层构造参数 `reasoning_effort="low|medium|high"`
- 二者均为显式构造参数，**不得**经 `model_kwargs` 注入（旧版 langchain-core 直接 ValueError）
- 注入门槛：`model.reasoning=True` **且** `thinking_level != "off"`。能力位由用户在 Model
  上声明（不按模型名硬编码，避免随新模型过时）；该开关同时控制 `transform_messages` 的
  thinking 历史剥除，保证请求参数与消息回放一致。

内部函数：`_apply_reasoning_params(kwargs: dict, model: Model, level: ThinkingLevel) -> dict`。

### 捕获 thinking 内容

| Provider | 行为 |
|---|---|
| Anthropic | 流式 chunk.content 中检测 `{"type": "thinking"}` 块，累积文本，最终写入 `AssistantMessage.content` 的 `ThinkingContent`；同时捕获 `signature` 写入 `ThinkingContent.signature`（多轮工具回放必需，审计 B7），增量以 `thinking_delta` 事件实时发射（审计 D6） |
| OpenAI | 不返回 thinking 文本。从 `usage_metadata.output_token_details.reasoning` 读取 reasoning token 数，记录到 `Usage.reasoningTokens` |

### ThinkingContent 位置

`AssistantMessage.content` 中，`ThinkingContent` 放在 `TextContent` **之前**（与 Anthropic API 顺序一致）。

### 影响文件

- `langchain_stream.py`：注入 reasoning 参数、捕获 thinking 块
- `messages.py`：Usage 新增 `reasoningTokens: int = 0`

---

## 3. transform_messages

### 目标

处理跨 provider 消息兼容性，支持对话中途换模型和跨实例消息共享。内置到 `langchain_stream`，同时导出工具函数。

### 新模块 `pi_agent_core/transform.py`

核心函数：

```python
def transform_messages(
    messages: list[Message],
    target_model: Model,
    source_model: Model | None = None,
) -> list[Message]:
```

内部由可组合的 transformer 按顺序执行：

```
messages → normalize_tool_call_ids → downgrade_thinking → strip_unsupported_images → result
```

### Transformer 明细

#### normalize_tool_call_ids

- **触发**：始终执行
- **行为**：将 AssistantMessage 中的 `toolCall.id` 和对应 ToolResultMessage 的 `toolCallId` 重写为目标 provider 兼容格式
- **ID 格式**：OpenAI → `call_xxx`，Anthropic → `toolu_xxx`
- **一致性**：维护 `{old_id: new_id}` 映射，确保 toolCall.id 和 toolCallId 配对正确
- **规则**：已是目标格式的 ID 不变

#### downgrade_thinking

- **触发**：`target_model.reasoning == False`
- **行为**：从 AssistantMessage.content 中移除所有 `ThinkingContent` 块

#### strip_unsupported_images

- **触发**：`target_model.supports_images == False`
- **行为**：从 UserMessage.content 中移除 `ImageContent` 块；纯图片消息替换为 `[image content removed]` 占位文本

### 集成位置

在 `langchain_stream` 内部，`convert_to_llm` 之后、`convert_to_langchain` 之前自动调用：

```
原管道：  convert_to_llm → convert_to_langchain → LLM
新管道：  convert_to_llm → transform_messages(target_model) → convert_to_langchain → LLM
```

### 导出

`transform_messages`、`normalize_tool_call_ids`、`downgrade_thinking`、`strip_unsupported_images` 从 `pi_agent_core` 和 `pi_agent_core.transform` 导出，供自定义 `stream_fn` 使用。

### 影响文件

- 新建 `pi_agent_core/transform.py`
- 修改 `langchain_stream.py`：调用 transform_messages
- 修改 `__init__.py`：导出 transform 函数

---

## 4. 测试策略

### Usage/cost 测试

- Mock stream 返回带 `usage_metadata` 的 chunk，验证 `Usage` 字段正确填充
- 提供 mock `cost_calculator`，验证 `UsageCost` 正确写入
- 不提供 cost_calculator 时，验证 cost 为全零

### thinking/reasoning 测试

- Mock stream 返回包含 thinking 块的响应，验证 `ThinkingContent` 写入 AssistantMessage.content
- 验证 ThinkingContent 位于 TextContent 之前

### transform_messages 测试

- `normalize_tool_call_ids`：OpenAI→Anthropic ID 重写、Anthropic→OpenAI ID 重写、已兼容 ID 不变、toolCall 和 toolResult 配对一致
- `downgrade_thinking`：reasoning=False 时 thinking 块被移除、reasoning=True 时保留
- `strip_unsupported_images`：supports_images=False 时图片被移除并替换占位文本
- `transform_messages` 端到端：混合场景（含 thinking + tool calls + images），切换 provider 后消息正确转换

---

## 5. 文件变更总览

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `pi_agent_core/transform.py` | 新建 | transform_messages + 3 个 transformer |
| `pi_agent_core/langchain_stream.py` | 修改 | 提取 usage、reasoning 参数、thinking 捕获、调用 transform |
| `pi_agent_core/messages.py` | 修改 | Usage 新增 reasoningTokens |
| `pi_agent_core/types.py` | 修改 | StreamOptions 新增 cost_calculator |
| `pi_agent_core/agent.py` | 修改 | 传递 cost_calculator |
| `pi_agent_core/__init__.py` | 修改 | 导出 transform 函数 |
| `pi_agent_core/tests/test_transform.py` | 新建 | transform 单元测试 |
| `pi_agent_core/tests/test_usage.py` | 新建 | usage 提取 + cost_calculator 测试 |
| `pi_agent_core/tests/test_thinking.py` | 新建 | thinking 捕获测试 |
| `pi_agent_core/tests/mock_stream.py` | 修改 | 新增带 usage/thinking 的 mock stream |
