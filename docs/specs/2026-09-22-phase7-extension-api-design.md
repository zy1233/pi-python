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
| `"tool_result"` | `tool_result` (harness own) | 可返回 `AfterToolCallResult` 修改 content/terminate |
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

**分发语义**：`tool_call` 按注册顺序（ACP 权限层先注册，扩展后注册）逐个调用 handler，
**首个返回 block 的 handler 立即终止分发**（`AgentHarness._emit_tool_call_hook`，对应上游
`emitToolCall`）；非阻断的返回值（`None` / `{}` / `{"block": False}`）一律忽略。因此后注册的
扩展无法撤销权限层已作出的拒绝。其余事件仍沿用 `_emit_hook` 的 last-non-None 规则。

`AgentHarness.check_tool_call(tool_call_id, tool_name, tool_input, *, origin=None)` 是同一条 hook 链的
公开入口（`before_tool_call` 也走它），供在循环之外运行 agent 的扩展使用（`HarnessBridge.tool_call_gate`，
见 §12「权限继承」）。`ToolCallEvent` 有可选字段 `origin`，会话自己的调用为 `None`。

---

## 4. 扩展发现与加载

### 4.1 三种发现方式

1. **entry_points**：`[project.entry-points."pi_agent.extensions"]` —— 标准
   setuptools/pip 机制，`pip install pi-web-access-py` 后自动发现。
2. **目录扫描**：`~/.pi-python/extensions/` 和 `.pi-python/extensions/` ——
   对齐上游 pi 的 `~/.pi/agent/extensions/` 和 `.pi/extensions/`。
   扫描目录下的 Python 模块，import 并查找 `activate` 函数。
   项目目录（`.pi-python/extensions/`）需要显式信任才会扫描，见 §4.4。
3. **编程注入**：`ExtensionLoader.load(module_or_callable)` ——
   测试和嵌入场景，传入 module 对象或 `activate` 函数均可。

### 4.2 加载顺序

entry_points → 用户目录（`~/.pi-python/extensions/`）→ 项目目录
（`.pi-python/extensions/`，仅当项目受信任，§4.4）→ 编程注入。同名扩展后加载的覆盖先加载的（与上游一致）。

### 4.3 与 AgentHarness 集成

`AgentHarness.__init__` 新增可选参数：

```python
extensions: list[Callable[[ExtensionAPI], None]] | None = None
extension_dirs: list[str] | None = None
auto_discover_extensions: bool = False  # CLI/TUI 显式传 True
trust_project_extensions: bool = False  # True 才会扫描 <cwd>/.pi-python/extensions（§4.4）
```

在首次 `prompt()` 调用时（`_ensure_extensions_loaded`），执行：

1. 如果 `auto_discover_extensions`，通过 `ExtensionLoader` 发现 entry_points + 用户目录扩展；`trust_project_extensions` 为真时再加上项目目录扩展
2. 合并手动传入的 `extensions`
3. 为每个扩展创建 `ExtensionAPI` 实例，调用 `activate(pi)`
4. 收集所有 `register_tool` 注册的工具，合并到 `_tools` 字典
5. 收集所有 `on()` 注册的事件处理器，挂接到 `subscribe()` 管道
6. 发射 `session_start` 事件

### 4.4 项目目录扩展需要显式信任

import 一个扩展就是执行它的代码，而 `<项目>/.pi-python/extensions/` 随仓库分发。若无条件扫描，打开一个不受信任的仓库会在用户输入第一个字之前运行仓库里的代码——没有提示，也不经过权限层（加载扩展不是工具调用，§3.5 的 `tool_call` 链管不到它）。

上游 pi 对此有「项目信任」（`packages/coding-agent/docs/security.md`）：信任决定作出之前只加载用户/全局扩展和命令行 `-e` 扩展，项目扩展在信任后才加载；非交互模式不弹提示，按 `defaultProjectTrust` 处理（`ask` / `never` 忽略项目资源，`always` 信任），`--approve` / `--no-approve` 单次覆盖；决定按目录保存（`~/.pi/agent/trust.json`），父目录的决定适用于子目录。本移植版实现其最小子集：**默认拒绝，只接受项目自己写不到的位置给出的授权**。

| 授权来源 | 说明 |
|---|---|
| `~/.pi-python/agent.toml` → `[extensions] trusted_projects = ["/abs/path"]` | 绝对路径（或 `~`）白名单；Windows 请写正斜杠（`"C:/work/repo"`），TOML 双引号里的 `\U` 是语法错误，通知里给出的建议值已是正斜杠。名单内目录的子目录同样受信；相对路径被忽略并告警；比较前先解析符号链接（受信目录里指向别处的链接不继承信任） |
| `[extensions] trust_project_extensions = true` | 全局开关，信任所有项目（≈ 上游 `defaultProjectTrust = "always"`） |
| 环境变量 `PI_TRUST_PROJECT_EXTENSIONS=1`（`1/true/yes/on`）或命令行 `--trust-project-extensions` | 单次运行（≈ 上游 `--approve`），适合 headless / CI；后者只是前者的另一种写法，对 ACP 与 headless 都生效 |

只读取家目录的 `agent.toml`，绝不读取项目内的配置文件，否则仓库可以给自己授信；`load_local_env` 同样只读 `~/.pi-python/local.env`。

未受信的项目目录**不会被 import**：`ExtensionLoader` 只列出本会加载的模块名（`ExtensionLoader.skipped` / `AgentHarness.skipped_extensions`，元素为 `SkippedExtensions(directory, names)`），写一条 warning 日志，CLI 再告诉用户被跳过了什么、怎样启用（ACP：`session/new` 应答之后的一条 `agent_message_chunk`；headless：stderr，stdout 仍只有回答）。用户目录、entry_points、`extensions=` / `extension_dirs=` 不受影响：前两者由用户自己安装，后两者由嵌入方显式传入。

`AgentHarness(trust_project_extensions=False)` 与 `ExtensionLoader.load_all(trust_project_extensions=False)` 默认都是拒绝；CLI 的 `create_session_harness` 通过 `extension_trust.project_extensions_trusted(config, cwd)` 得出该值。

已知限制（相对上游）：

- 没有交互式确认，也没有 `/trust` 持久化；白名单靠手写 `agent.toml`。后续可在 ACP 里加确认并写入信任库。
- 信任按路径而非内容：白名单内的项目之后才出现的扩展（例如 `git pull` 带来的）会被直接运行。内容哈希信任库可以关闭这个口子。
- 只拦了扩展。上游同样放在信任门后的 `.pi/SYSTEM.md`、`.pi/APPEND_SYSTEM.md` 和项目 skills（`[skills].paths` 里的相对路径，如 `.pi/skills`），在本移植版里仍被无条件读取：它们不执行代码，但会进入 system prompt（与上游同样不设门的 `AGENTS.md` 属同一类注入面）。
- 授权以整个项目目录为单位，不区分单个扩展。

---

## 5. pi-web-access 移植

### 5.1 工具规格

**`web_search`**

| 字段 | 值 |
|---|---|
| 参数 | `query: str`, `provider: str \| None = None`, `max_results: int = 5` |
| 提供商选择 | 环境变量自动检测：`BRAVE_API_KEY` → Brave；`TAVILY_API_KEY` → Tavily；`SEARXNG_URL` → SearXNG；未配置 → 报错说明 |
| 返回 | `[{title, url, snippet}]` 编号列表，每条含序号、缩进 URL 和 snippet |
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
    ├── runtime.py               WorkflowRuntime 脚本执行引擎（受限命名空间，非安全边界）
    ├── builtin_workflows.py     5 个内置 pattern
    ├── model_routing.py         tier 路由
    └── budget.py                token budget tracking
```

### 9.3 核心机制

- **`workflow` 工具**：LLM 传入 Python 脚本或 `name`（内置 pattern）
- **受限命名空间（不是沙盒）**：脚本在只暴露 `agent()`, `parallel()`, `pipeline()`, `phase()`,
  `log()`, `budget`, `args`, `cwd` 和一小组内建函数的命名空间中执行，用来引导脚本只做编排。
  它**不是安全边界**：经由命名空间里已有对象的属性访问可以到达 `__import__` 等能力。真正约束脚本副作用的
  是子代理工具调用所经过的权限策略（见 §12「权限继承」）。
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
| 项目目录扩展信任 | ✅ | `<cwd>/.pi-python/extensions` 默认不加载，需白名单 / 全局开关 / 环境变量 / `--trust-project-extensions`（§4.4） |
| 子包 entry_points 声明 | ✅ | 3 个子包的 `pyproject.toml` 均声明 `[project.entry-points."pi_agent.extensions"]` |
| System prompt 注入 | ✅ | 扩展注册的工具的 `prompt_snippet` / `prompt_guidelines` 自动进入 system prompt |
| LLM 可用性 | ✅ | `workflow` / `web_search` / `fetch_url` / `goal_update` / `goal_complete` 工具在 TUI 会话中自动注册，LLM 可直接 tool_call |
| Slash 命令路由 | ✅ | `AgentHarness.dispatch_command()` + `_try_slash_dispatch()` 拦截 `/command args`；`PiAcpAgent._advertise_commands()` 通过 `AvailableCommandsUpdate` 填充 TUI/Zed 自动补全 |

### 10.2 尚未适配项

| 项 | 说明 | 影响 |
|---|---|---|
| **`ctx.ui.*` API** | 上游 pi 的 `ctx.ui.confirm()` / `ctx.ui.select()` / `ctx.ui.notify()` / `ctx.ui.setWidget()` 等 TUI 交互 API 未实现 | 需要时扩展可通过 `send_message()` 降级实现文本反馈 |
| **SubagentExecutor 真实实现** | §12 ✅ | `HarnessSubagentExecutor` 已实现，含 coding tools + env |
| **Background run / result delivery** | §13 ✅ | `trigger_prompt()` 空闲时触发新 turn；`register_cleanup()` 回收任务 |
| **Run persistence / resume** | §14 ✅ | 首次 run 自动创建 Journal，按 run_id 恢复；格式校验防路径穿越 |
| **Git worktree isolation** | §15 ✅ | `WorktreeManager` 创建隔离 worktree；snapshot baseline + apply_changes 回写 |
| **Saved workflow 存储** | §16 ✅ | `WorkflowStore` scan/save，project 覆盖 user；`os.link` 原子 no-clobber |
| **Task panel / widget** | TUI 侧的实时进度面板未移植 | 纯文本结果输出替代 |
| **TUI 文件夹信任库 ↔ 项目扩展信任** | TUI 自己的信任库（`/hooks trust`）与 Python 端 §4.4 互不相通 | TUI 用户需在 `agent.toml` 白名单里登记项目，或带 `PI_TRUST_PROJECT_EXTENSIONS=1` 启动（环境变量会传给被拉起的 Python agent） |

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

**tier 与凭据的边界**（防止静默换厂商、把 A 厂商的 key 发给 B 厂商）：

- 内置 tier 默认值按**父 model 的 provider** 限定（`DEFAULT_MODEL_TIERS_BY_PROVIDER`，目前只有
  `anthropic`）；父 provider 没有条目时，`tier` 解析为 `None`，即继承父 model。显式传入的
  `tiers` 映射优先、可指向任意 provider，且不回退到内置默认。
- 模型 id 为 `provider/model`，无 `/` 表示与父 model 同 provider。`base_url` 是 provider 级属性：
  仅当子代理留在父 provider 上才继承，跨 provider 一律丢弃。`supports_images` / `reasoning`
  是模型级属性，不继承。
- 循环按子代理**实际使用的 provider** 调用 `get_api_key(provider)`。CLI 的 `make_get_api_key`
  仅对 `[model].provider` 返回 `api_key_env` 的值，其它 provider 返回 `None`，由其 SDK 读取自己的
  标准环境变量（如 `ANTHROPIC_API_KEY`）。
- 已知限制：`provider/model` 按第一个 `/` 拆分，对自身含 `/` 的网关模型 id（如 `Qwen/Qwen3-8B`）
  有歧义；此类 id 需写成 `<provider>/Qwen/Qwen3-8B`。

**权限继承（子代理的工具调用）**：子代理运行在各自的 `AgentHarness` 上，不带父会话的任何 hook，
所以父会话的权限层（ACP `session/request_permission`）原本看不到它们的 `bash` / `edit` / `write`，
`ask` 模式下也不会有任何询问。现在：

- `workflow` 工具本身列入 `PERMISSION_TOOLS`：`ask` 模式下启动 workflow 需要用户批准；`auto` /
  `always-approve` 不弹窗，与其它工具一致。
- 桥接新增 `tool_call_gate`（即 `AgentHarness.check_tool_call`）：对父 harness 的 `tool_call` hook 链
  （权限层 + 扩展 hook，首个 block 生效，见 §3.5）跑一遍，返回 `None`（放行）或
  `{"block": True, "reason": ...}`。`HarnessSubagentExecutor(tool_call_gate=...)` 给每个子代理 harness
  装一个 `tool_call` hook，把它的每次工具调用送进该 gate。**批准 `workflow` 不等于批准其子代理的写操作**：
  每次调用逐个询问（只读工具 `read` / `grep` / `find` / `ls` 与会话自己一样不询问，但扩展 hook 照样生效）。
- gate 抛异常按拒绝处理（fail-closed）：hook 异常在 harness 里表现为该调用失败，工具不会执行。
- `ToolCallEvent.origin`：子代理的调用带 `{"kind": "subagent", "cwd": ..., "label": ...}`（会话自己的调用为
  `None`）。权限弹窗标题据此写成 `write (workflow sub-agent in <cwd>)`：脚本可以用 `agent(..., cwd=)` 把
  子代理指向任意目录，而弹窗只显示工具入参，相对路径本身看不出落点。`label` 是脚本自己起的文本，只进
  `origin`，不进标题。
- 工具调用 id 由模型生成（`call_1` …），会在父会话与各子代理之间重复，而权限弹窗以 id 为键，所以每次子代理
  运行的 id 加前缀 `subagent-<8 hex>:`。
- CLI 对同一会话的权限询问串行化（每会话一个锁）：并行子代理不会同时弹出多个弹窗。
- 子代理等待权限回复的时间计入其 `timeout_ms`。
- 没有 gate 的 executor（独立使用，或未实现 `tool_call_gate` 的旧 bridge）没有可继承的策略，子代理不受限；
  `activate()` 发现 bridge 没有 `tool_call_gate` 时记录 warning。
- `isolation=True` 用 `HarnessSubagentExecutor.with_worktree_manager()`（浅拷贝）派生 executor，gate 与其它配置
  不会在派生时丢失（此前用构造函数重建，新增配置容易漏传）。
- 已知限制：`auto` / `always-approve` 下子代理可执行一切，与会话自己一致；脚本可指定任意 `cwd`，目前只是
  在弹窗里可见，未限制在工作区内；受限命名空间不是安全边界（见 §9.3）。

**Bridge 扩展**：`HarnessBridge` 增加 `stream_fn` / `model` / `get_api_key_fn` / `tool_call_gate`
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
