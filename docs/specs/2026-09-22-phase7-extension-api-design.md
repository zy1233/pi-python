# Phase 7 ExtensionAPI — 设计方案

> Scope: Python 版 ExtensionAPI 骨架（对齐上游 pi 的 TypeScript `ExtensionAPI`）
> \+ 移植 `pi-web-access`、`pi-goal-x` 两个热门扩展包。
> 上游参照：[earendil-works/pi](https://github.com/earendil-works/pi) `packages/coding-agent/docs/extensions.md`
> 及 [pi.dev/packages](https://pi.dev/packages)（5500+ npm 扩展市场）。
>
> 原则：ExtensionAPI 是已有基础设施（`AgentTool` 协议、`AgentHarness` 事件系统、
> `before_tool_call`/`after_tool_call` 钩子、Skills 加载）的统一门面，
> **不新建运行时**——扩展注册的工具/命令/事件处理器全部通过已有管道执行。

---

## 1. 目标与背景

### 1.1 上游 pi 扩展体系

上游 pi 的扩展是 TypeScript npm 包，导出一个接受 `ExtensionAPI` 的默认函数：

```typescript
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
export default function (pi: ExtensionAPI) {
  pi.registerTool({ name, description, parameters, execute });
  pi.registerCommand("cmd", { handler });
  pi.on("tool_call", handler);
}
```

核心 API 面：`registerTool` / `registerCommand` / `on` / `getActiveTools` /
`setActiveTools` / `sendMessage` / `appendEntry` / `exec`。
TUI 面（`ctx.ui.confirm/select/notify/setStatus/setWidget/custom`）和
`registerShortcut/registerFlag/registerMessageRenderer` 是 TUI-only 扩展。

### 1.2 pi-python 已有基础设施

| 已有能力 | 位置 | 对应上游 |
|---|---|---|
| `AgentTool` protocol / `SimpleTool` / `CodingTool` | `pi_agent_core/types.py`, `tools.py`, `coding_tools/_base.py` | `pi.registerTool()` |
| `before_tool_call` / `after_tool_call` 回调 | `AgentLoopConfig` | `pi.on("tool_call")` |
| `AgentHarness.subscribe()` / `.on()` | `agent_harness.py` | `pi.on()` 事件 |
| `AgentHarness.set_tools()` / `.set_active_tools()` | `agent_harness.py` | `pi.setActiveTools()` |
| Skills 加载 + system prompt 注入 | `pi_agent_harness/skills.py` | `pi.registerCommand()` (skill 部分) |
| Session tree 持久化 (`CustomEntry`) | `pi_agent_harness/types.py` | `pi.appendEntry()` |
| `Shell.exec()` | `ExecutionEnv` protocol | `pi.exec()` |

**核心差距**：没有统一的 `ExtensionAPI` 门面将这些能力暴露给第三方包，
也没有扩展发现/加载机制。

### 1.3 目标

1. 在 `pi_agent_core/extensions/` 实现 Python 版 `ExtensionAPI` 最小可用子集。
2. 在 `AgentHarness` 中集成扩展生命周期（发现 → 加载 → 激活 → 注入）。
3. 基于该框架移植 `pi-web-access`（web_search + fetch_url）和
   `pi-goal-x`（/goal 目标规划）为 Python 原生扩展包。

---

## 2. 架构

```
┌─────────────────────────────────────────────────────┐
│          Third-party Extension Packages             │
│  pi-web-access-py  │  pi-goal-x-py  │  user ext    │
└────────┬───────────┴────────┬────────┴──────┬───────┘
         │ activate(pi)       │               │
         ▼                    ▼               ▼
┌─────────────────────────────────────────────────────┐
│            pi_agent_core/extensions/                │
│  ExtensionAPI  ←  ExtensionRegistry  ← Loader      │
└────────┬───────────┬────────────────────────────────┘
         │           │
         │  register_tool / register_command / on()
         ▼           ▼
┌─────────────────────────────────────────────────────┐
│            AgentHarness (existing)                  │
│  set_tools()  │  subscribe()  │  Session  │  Shell  │
└─────────────────────────────────────────────────────┘
```

### 2.1 包结构

```
pi_agent_core/extensions/        (新建，core 子包)
├── __init__.py                  公开 API 导出
├── types.py                     ToolDefinition, CommandDef, EventHandler, ExtensionMeta
├── registry.py                  ExtensionRegistry
├── api.py                       ExtensionAPI class
└── loader.py                    发现 + 加载

packages/pi-web-access/          (新建，独立 PyPI 包)
├── pyproject.toml
└── pi_web_access/
    ├── __init__.py              activate(pi)
    ├── web_search.py            web_search 工具
    ├── fetch_url.py             fetch_url 工具
    └── providers/
        ├── __init__.py
        ├── brave.py
        ├── tavily.py
        └── searxng.py

packages/pi-goal-x/              (新建，独立 PyPI 包)
├── pyproject.toml
└── pi_goal_x/
    ├── __init__.py              activate(pi)
    ├── goal_tool.py             goal_update / goal_complete 工具
    ├── goal_state.py            GoalState 状态机
    └── prompts.py               prompt 片段
```

---

## 3. ExtensionAPI 规格

### 3.1 入口点约定

Python 扩展包必须在模块级暴露一个 `activate` 函数（对应上游 TS 的
`export default function`）：

```python
from pi_agent_core.extensions import ExtensionAPI

def activate(pi: ExtensionAPI) -> None:
    ...
```

### 3.2 ExtensionAPI 方法表

| 方法 | 签名 | 对应上游 | 实现路径 |
|---|---|---|---|
| `register_tool` | `(definition: ToolDefinition) -> None` | `pi.registerTool()` | 构建 `SimpleTool` → harness `set_tools()` |
| `register_command` | `(name, *, description, handler) -> None` | `pi.registerCommand()` | 存入 registry → CLI 消费 |
| `on` | `(event: str, handler) -> Unsubscribe` | `pi.on()` | 委托 harness `subscribe()` 按类型过滤 |
| `get_active_tools` | `() -> list[str]` | `pi.getActiveTools()` | 委托 `harness.active_tool_names` |
| `set_active_tools` | `(names: list[str]) -> None` | `pi.setActiveTools()` | 委托 `harness.set_active_tools()` |
| `get_all_tools` | `() -> list[ToolInfo]` | `pi.getAllTools()` | 从 harness `_tools` 读取 |
| `send_message` | `(text: str) -> None` | `pi.sendMessage()` | 委托 harness steer queue |
| `append_entry` | `(custom_type: str, data) -> None` | `pi.appendEntry()` | 委托 session `append_custom_entry()` |
| `exec` | `async (command, *, cwd, timeout) -> ExecResult` | `pi.exec()` | 委托 `ExecutionEnv.exec()` |
| `cwd` | `@property -> str` | `pi.cwd` | 从 harness env 读取 |
| `session_id` | `@property -> str` | `pi.sessionId` | 从 session metadata 读取 |

### 3.3 明确不实现项（TUI-only，推迟到有需求时）

- `ctx.ui.*`（confirm, select, notify, setStatus, setWidget, custom, editor）
- `register_shortcut()`
- `register_flag()`
- `register_message_renderer()`
- `pi.events`（跨扩展事件总线）

### 3.4 ToolDefinition 类型

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: type[BaseModel] | dict[str, Any]
    execute: Callable[..., Awaitable[AgentToolResult]]
    label: str | None = None                    # 默认 = name
    prompt_snippet: str | None = None
    prompt_guidelines: list[str] = field(default_factory=list)
    execution_mode: ToolExecutionMode | None = None
    prepare_arguments: Callable[[Any], Any] | None = None
```

`ToolDefinition` → `CodingTool`（或 `SimpleTool`）的映射在 `register_tool` 内部完成，
调用者无需关心内部工具实现类型。

### 3.5 事件映射

| `pi.on(event)` | 匹配的 `AgentHarnessEvent.type` | 阻塞能力 |
|---|---|---|
| `"tool_call"` | `tool_call` (harness own) | 可返回 `{ block, reason }` |
| `"tool_result"` | `tool_result` (harness own) | 只读 |
| `"session_start"` | `before_agent_start` | 只读 |
| `"agent_end"` | `agent_end` | 只读 |
| `"turn_start"` | `turn_start` | 只读 |
| `"turn_end"` | `turn_end` | 只读 |
| `"message_start"` | `message_start` | 只读 |
| `"message_end"` | `message_end` | 只读 |
| `"tool_execution_start"` | `tool_execution_start` | 只读 |
| `"tool_execution_end"` | `tool_execution_end` | 只读 |

`tool_call` 是唯一具有阻塞能力的事件（返回 `{"block": True, "reason": "..."}` 等价于
`BeforeToolCallResult(block=True, reason=...)`），与上游 pi 行为一致。

---

## 4. 扩展发现与加载

### 4.1 三种发现方式

1. **entry_points**：`[project.entry-points."pi_agent.extensions"]` —— 标准
   setuptools/pip 机制，`pip install pi-web-access-py` 后自动发现。
2. **目录扫描**：`~/.pi-python/extensions/` 和 `.pi-python/extensions/` ——
   对齐上游 pi 的 `~/.pi/agent/extensions/` 和 `.pi/extensions/`。
   扫描目录下的 Python 模块，import 并查找 `activate` 函数。
3. **编程注入**：`ExtensionLoader.load(module_or_callable)` ——
   测试和嵌入场景，直接传入 `activate` 函数。

### 4.2 加载顺序

entry_points → 用户目录（`~/.pi-python/extensions/`）→ 项目目录
（`.pi-python/extensions/`）→ 编程注入。同名扩展后加载的覆盖先加载的（与上游一致）。

### 4.3 与 AgentHarness 集成

`AgentHarness.__init__` 新增可选参数：

```python
extensions: list[Callable[[ExtensionAPI], None]] | None = None
extension_dirs: list[str] | None = None
auto_discover_extensions: bool = True
```

在首次 `prompt()` 调用时（`_ensure_extensions_loaded`），执行：

1. 如果 `auto_discover_extensions`，通过 `ExtensionLoader` 发现 entry_points + 目录扩展
2. 合并手动传入的 `extensions`
3. 为每个扩展创建 `ExtensionAPI` 实例，调用 `activate(pi)`
4. 收集所有 `register_tool` 注册的工具，合并到 `_tools` 字典
5. 收集所有 `on()` 注册的事件处理器，挂接到 `subscribe()` 管道
6. 发射 `session_start` 事件

---

## 5. pi-web-access 移植

### 5.1 工具规格

**`web_search`**

| 字段 | 值 |
|---|---|
| 参数 | `query: str`, `provider: str \| None = None`, `max_results: int = 5` |
| 提供商选择 | 环境变量自动检测：`BRAVE_API_KEY` → Brave；`TAVILY_API_KEY` → Tavily；`SEARXNG_URL` → SearXNG；未配置 → 报错说明 |
| 返回 | `[{title, url, snippet}]` 文本列表，每条 `title: url\nsnippet` |
| prompt_snippet | `"Search the web for real-time information"` |

**`fetch_url`**

| 字段 | 值 |
|---|---|
| 参数 | `url: str`, `extract_text: bool = True`, `max_length: int \| None = None` |
| 实现 | `httpx.AsyncClient.get(url)` → 如 `extract_text` 则用简单 HTML tag 剥离（无重依赖）→ `truncate_head` 截断 |
| 返回 | 页面文本内容 |
| prompt_snippet | `"Fetch and read the contents of a URL"` |

### 5.2 依赖

- `httpx`（HTTP 客户端，已是 Python 生态主流，无 C 扩展）
- `trafilatura`（可选，`pip install pi-web-access-py[readability]`）

---

## 6. pi-goal-x 移植

### 6.1 核心机制

- `/goal <description>` 命令启动目标模式
- 注入 prompt_guidelines 告诉 LLM 使用 `goal_update` 和 `goal_complete` 工具
- `goal_update(step, status, details)` — 报告步骤进度
- `goal_complete(summary)` — 标记目标完成
- `on("turn_end")` 检查是否所有步骤完成，未完成时追加提醒
- `append_entry("goal_state", data)` 持久化状态
- `on("session_start")` 恢复已有目标状态（从 session entries 读取）

### 6.2 GoalState

```python
@dataclass
class GoalStep:
    description: str
    status: Literal["pending", "in_progress", "done", "blocked"] = "pending"
    details: str | None = None

@dataclass
class GoalState:
    description: str | None = None
    steps: list[GoalStep] = field(default_factory=list)
    completed: bool = False
```

### 6.3 依赖

无新增运行时依赖（仅依赖 `pi-agent-core-lc`）。

---

## 7. 文件变更总览

| 文件 | 变更 | 说明 |
|---|---|---|
| `pi_agent_core/extensions/` (新目录，5 个文件) | 新建 | §2–§4 |
| `pi_agent_core/extensions/__init__.py` | 新建 | 公开 API 导出 |
| `pi_agent_core/extensions/types.py` | 新建 | §3.4 ToolDefinition, CommandDef 等 |
| `pi_agent_core/extensions/registry.py` | 新建 | ExtensionRegistry |
| `pi_agent_core/extensions/api.py` | 新建 | ExtensionAPI class |
| `pi_agent_core/extensions/loader.py` | 新建 | §4 发现 + 加载 |
| `packages/pi-agent-harness/.../agent_harness.py` | 修改 | §4.3 集成扩展生命周期 |
| `packages/pi-web-access/` (新包) | 新建 | §5 |
| `packages/pi-goal-x/` (新包) | 新建 | §6 |
| `pyproject.toml` | 修改 | workspace members 追加两个新包 |
| tests | 新建 | 各阶段测试 |

核心运行时（`types.py` / `agent_loop.py` / `agent.py` / `messages.py`）**零改动**。

---

## 8. 测试策略

| 域 | 关键用例 |
|---|---|
| ExtensionAPI | mock 扩展 register_tool → 工具出现在 harness；register_command → 命令可调用；on("tool_call") → 返回 block 阻止工具执行 |
| ExtensionLoader | entry_points mock；目录扫描 tmp_path；编程注入 |
| pi-web-access | mock httpx 响应 → 验证输出格式；多提供商切换；无 API key 时报错文案 |
| pi-goal-x | GoalState 状态机转换；append_entry 持久化 → session_start 恢复；turn_end 进度检查 |
| pi-dynamic-workflows | WorkflowRuntime agent/parallel/pipeline 编排；TokenBudget 限额；5 个 built-in pattern 脚本语法校验；workflow tool 参数解析 |

---

## 9. Phase 4: 移植 pi-dynamic-workflows

### 9.1 概述

上游 [`@quintinshaw/pi-dynamic-workflows`](https://github.com/QuintinShaw/pi-dynamic-workflows)
是 Pi 最大的第三方扩展之一（600KB+ TS），核心是让 LLM 编写脚本来编排 subagent 并行执行。
Python 移植将脚本语言从 JavaScript 改为 Python，运行在受限命名空间中。

### 9.2 包结构

```
packages/pi-dynamic-workflows/
├── pyproject.toml
└── pi_dynamic_workflows/
    ├── __init__.py              activate(pi) + /workflows 命令
    ├── workflow_tool.py         workflow 工具定义
    ├── runtime.py               WorkflowRuntime 沙盒执行引擎
    ├── builtin_workflows.py     5 个内置 pattern
    ├── model_routing.py         tier 路由
    └── budget.py                token budget tracking
```

### 9.3 核心机制

- **`workflow` 工具**：LLM 传入 Python 脚本或 `name`（内置 pattern）
- **沙盒执行**：脚本在受限命名空间中执行，只能用 `agent()`, `parallel()`,
  `pipeline()`, `phase()`, `log()`, `budget`, `args`, `cwd`
- **`agent(prompt, **opts)`**：spawn 隔离 subagent，支持 `tier`/`model`/`schema`/`label`
- **`parallel(thunks)`**：并发运行 agent 调用
- **`pipeline(items, *stages)`**：流水线：stage 串行、item 并行
- **Token budget**：可选软限额，超限后阻止新 agent 调用
- **SubagentExecutor**：协议类，注入真实/mock subagent 执行器

### 9.4 内置 Pattern

| 名称 | 参数 | 说明 |
|---|---|---|
| `deep-research` | `{ question }` | 生成搜索角度 → 并行研究 → 交叉校验 → 综合报告 |
| `adversarial-review` | `{ task, reviewers? }` | 调查 → 对抗性审查 → 综合 |
| `code-review` | `{ diff }` | 5 角度并行 review → 验证排序 |
| `multi-perspective` | `{ topic, perspectives? }` | 多角度分析 → 综合 |
| `codebase-audit` | `{ scope, checks }` | 并行审计检查 → 交叉验证 |

### 9.5 后续实现项

以下各项已完成设计（§12–§16），按优先级排列。

| 编号 | 项 | 前置依赖 | 优先级 |
|------|-----|---------|--------|
| §12 | SubagentExecutor 真实实现 | 无 | P0 — workflow 可用性的基本前提 |
| §13 | Background run / result delivery | §12 | P1 — 长时间 workflow 不阻塞会话 |
| §14 | Run persistence / resume | §12 | P1 — 避免重复执行、节省 token |
| §15 | Git worktree isolation | §12 | P2 — 多 subagent 同时修改文件不冲突 |
| §16 | Saved workflow 存储 | 无 | P2 — 用户自定义 workflow 持久化 |

Task panel / widget / UI 通知、`workflow_control` 工具（pause/resume/stop）
仍推迟至 TUI Python 原生版实现时再设计。

---

## 10. TUI / CLI 集成状态

### 10.1 已完成的适配

| 层 | 状态 | 说明 |
|---|---|---|
| `AgentHarness` 扩展参数 | ✅ | `extensions` / `extension_dirs` / `auto_discover_extensions` 已实现 |
| `_tool_from_definition` → `CodingTool` | ✅ | `prompt_snippet` / `prompt_guidelines` 正确转发 |
| `factory.py` → `auto_discover_extensions=True` | ✅ | CLI/TUI 构造 harness 时开启 entry_point 自动发现 |
| 子包 entry_points 声明 | ✅ | 3 个子包的 `pyproject.toml` 均声明 `[project.entry-points."pi_agent.extensions"]` |
| System prompt 注入 | ✅ | 扩展注册的工具的 `prompt_snippet` / `prompt_guidelines` 自动进入 system prompt |
| LLM 可用性 | ✅ | `workflow` / `web_search` / `fetch_url` / `goal_update` / `goal_complete` 工具在 TUI 会话中自动注册，LLM 可直接 tool_call |
| Slash 命令路由 | ✅ | `AgentHarness.dispatch_command()` + `_try_slash_dispatch()` 拦截 `/command args`；`PiAcpAgent._advertise_commands()` 通过 `AvailableCommandsUpdate` 填充 TUI/Zed 自动补全 |

### 10.2 尚未适配项

| 项 | 说明 | 影响 |
|---|---|---|
| **`ctx.ui.*` API** | 上游 pi 的 `ctx.ui.confirm()` / `ctx.ui.select()` / `ctx.ui.notify()` / `ctx.ui.setWidget()` 等 TUI 交互 API 未实现 | 需要时扩展可通过 `send_message()` 降级实现文本反馈 |
| **SubagentExecutor 真实实现** | 详见 §12 | 当前使用 `MockSubagentExecutor` |
| **Background run / result delivery** | 详见 §13 | 当前仅同步模式 |
| **Run persistence / resume** | 详见 §14 | 每次从头运行 |
| **Git worktree isolation** | 详见 §15 | subagent 共享 cwd |
| **Saved workflow 存储** | 详见 §16 | 仅支持内置 pattern |
| **Task panel / widget** | TUI 侧的实时进度面板未移植 | 纯文本结果输出替代 |

### 10.3 工作流链路（当前状态）

```
TUI (zypi) ──ACP stdio──> pi_agent_cli ──> factory.create_session_harness()
                                               │
                                               ▼
                                         AgentHarness(auto_discover_extensions=True)
                                               │
                                               │ _bind_session() 时
                                               ▼
                                         load_extensions() + _advertise_commands()
                                               │
                                    ┌──────────┼──────────┐
                                    ▼          ▼          ▼
                             entry_points   目录扫描    编程注入
                                    │          │          │
                                    ▼          ▼          ▼
                              activate(pi) ──> register_tool() ──> CodingTool
                                               register_command()       │
                                               on(event)                ▼
                                                              system prompt 注入
                                                              LLM 可以 tool_call

                              /command args → _try_slash_dispatch()
                                    │                            │
                                    ▼                            ▼
                           dispatch_command()            未匹配 → LLM turn
                                    │
                                    ▼
                         handler 执行 + 事件序列
                                    │
                                    ▼
                         ACP session_update → TUI 显示
```

---

## 11. 实施排序

1. `extensions/types.py` — 零依赖的类型定义
2. `extensions/registry.py` — 纯内存注册表
3. `extensions/api.py` — ExtensionAPI 门面（依赖 registry）
4. `extensions/loader.py` — 发现机制
5. `agent_harness.py` 集成 — 扩展生命周期接入
6. Phase 1 测试
7. `packages/pi-web-access/` — web_search + fetch_url
8. `packages/pi-goal-x/` — /goal 目标规划
9. `packages/pi-dynamic-workflows/` — workflow 工具 + runtime + 5 built-in patterns
10. `factory.py` — 开启 `auto_discover_extensions=True`，完成 TUI/CLI 集成

---

## 12. SubagentExecutor 真实实现 ✅

`HarnessSubagentExecutor`（`subagent.py`）替代 `MockSubagentExecutor`。
每次 `agent()` 创建独立 `MemorySessionStorage` + `AgentHarness`
执行单次 prompt-to-completion。

**关键行为**：model 显式指定 > tier 路由（`resolve_tier`）> 继承父 model；
独立 session 不污染父会话；`timeout_ms` 通过 `asyncio.wait` 实现；
`schema` 走 prompt 注入 + JSON 解析 → `AgentResult.structured`。

**Bridge 扩展**：`HarnessBridge` 增加 `stream_fn` / `model` / `get_api_key_fn`
只读属性，`activate()` 据此构造真实 executor。

---

## 13. Background run / result delivery ✅

`WorkflowManager`（`manager.py`）管理后台 workflow 的 asyncio task。

`WorkflowParams.background=True` → `asyncio.create_task(runtime.execute)` +
返回 `AgentToolResult(terminate=True)`，turn 立即结束。完成后通过
`bridge.send_message()` 将格式化结果推入 steer queue 触发新 turn。

`cancel_all()` 取消所有后台 task；`shutdown(timeout)` 等待 pending
runs 或超时后强制取消。

---

## 14. Run persistence / resume ✅

`Journal`（`journal.py`）— append-only JSONL 日志，记录每个 `agent()` 调用的
`hash_request("agent", {prompt, opts})` SHA-256 + 返回值。

**重放**：`try_replay(kind, req_hash)` 按游标顺序匹配，命中返回缓存结果
跳过 LLM 调用，不匹配则从该点截断（divergence）。上限 10000 条 / 64MB。

**集成**：`WorkflowRuntime.__init__(journal=...)` → `agent_fn` 内先查
journal，miss 时执行并 append。`WorkflowParams.resume_from_run_id`
指定前次 run_id，journal 存储于 `~/.pi-python/workflow-journals/<run_id>.jsonl`。

---

## 15. Git worktree isolation ✅

`WorktreeManager`（`worktree.py`）通过 `git worktree add --detach` 创建
linked worktree，每个 subagent 在独立目录中运行。

**Snapshot 模式**（默认）：`git diff HEAD` + `git diff --cached HEAD` 复制
staged/unstaged 变更到 worktree。**Clean 模式**：从指定 ref checkout。

`collect_diff()` 收集 worktree 变更；`apply_changes()` 将 diff apply 回源
cwd；`cleanup()` / `cleanup_all()` 删除 worktree。

**限制**：仅 git（非 jj）；不含 btrfs/overlay/NFS 优化；不复制 untracked 文件。

---

## 16. Saved workflow 存储 ✅

`WorkflowStore`（`store.py`）扫描 `~/.pi-python/workflows/`（用户级）和
`.pi-python/workflows/`（项目级）的 `.py` 脚本。

**Meta 提取**：`extract_meta(script)` 通过 `ast.literal_eval` 安全解析脚本
顶层 `meta = {...}` 字典，无需执行脚本。

**注册**：`activate()` 中 `store.scan()` → 为每个 saved workflow 注册
slash command，通过 `AvailableCommandsUpdate` 暴露给 TUI 自动补全。

**安全**：名称 `[a-z0-9-]{1,64}`；256KB 上限；原子写入 + no-clobber；
`save_project()` / `save_user()` 分别写入项目/用户目录。
