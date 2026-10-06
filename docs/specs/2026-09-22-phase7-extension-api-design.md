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
| `home` | `@property -> Path` | —（本移植版新增） | 会话使用的 pi-python 家目录（`PI_HOME` 或 `~/.pi-python`）；扩展的用户级文件放这里，不要自己拼 `Path.home()`（§4.5） |

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
    annotations: ToolAnnotations | None = None  # MCP 风格的提示，见 §12「权限模型」
```

`ToolDefinition` → `CodingTool`（或 `SimpleTool`）的映射在 `register_tool` 内部完成，
调用者无需关心内部工具实现类型。`annotations` 一并转发：CLI 的 `ask` 模式据此决定要不要问用户。

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
2. **目录扫描**：`<家目录>/extensions/`（家目录默认 `~/.pi-python`，可由 `PI_HOME` 或
   `home=` 参数改变，见 §4.5）和 `.pi-python/extensions/` ——
   对齐上游 pi 的 `~/.pi/agent/extensions/` 和 `.pi/extensions/`。
   扫描目录下的 Python 模块，import 并查找 `activate` 函数。
   项目目录（`.pi-python/extensions/`）需要显式信任才会扫描，见 §4.4。
3. **编程注入**：`ExtensionLoader.load(module_or_callable)` ——
   测试和嵌入场景，传入 module 对象或 `activate` 函数均可。

### 4.2 加载顺序

entry_points → 用户目录（`<家目录>/extensions/`）→ 项目目录
（`.pi-python/extensions/`，仅当项目受信任，§4.4）→ 编程注入。同名扩展后加载的覆盖先加载的（与上游一致）；
什么算「同名」见 §4.5。

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

上游 pi 对此有「项目信任」（`packages/coding-agent/docs/security.md`）：信任决定作出之前只加载用户/全局扩展和命令行 `-e` 扩展，项目扩展在信任后才加载；非交互模式不弹提示，按 `defaultProjectTrust` 处理（`ask` / `never` 忽略项目资源，`always` 信任），`--approve` / `--no-approve` 单次覆盖；决定按目录保存（`~/.pi/agent/trust.json`），父目录的决定适用于子目录。本移植版实现其子集：**默认不使用项目资源，授权只来自项目自己写不到的地方——用户的配置与命令行，以及用户自己在交互式提示里给出、并绑定到文件内容的回答**（回答保存在 `~/.pi-python/agent/trust.json`，见下文「交互式确认与内容指纹」）。

同一个信任决定还管着项目里另外几样会改变模型所见内容的东西（上游同样把它们放在信任门后）。它们不执行代码，但一个克隆来的仓库不该只因为被打开就能改写 system prompt：

| 受信任门约束（未受信项目被忽略） | 不受约束 |
|---|---|
| `<项目>/.pi-python/extensions`（扩展，会执行代码） | entry_points 扩展、`~/.pi-python/extensions`、`extensions=` / `extension_dirs=` |
| `<项目>/.pi/SYSTEM.md`、`<项目>/.pi/APPEND_SYSTEM.md`（回退到家目录的 `agent/SYSTEM.md` / `agent/APPEND_SYSTEM.md`） | `agent.toml` 里直接给出的 `custom_system_prompt*` / `append_system_prompt*`；家目录的 prompt 文件 |
| `[skills].paths` 里相对项目的条目（如 `.pi/skills`） | `[skills].paths` 里的绝对路径与 `~` 条目 |
| | `AGENTS.md` / `CLAUDE.md`（上游同样不设门） |

| 授权来源 | 说明 |
|---|---|
| `~/.pi-python/agent.toml` → `[extensions] trusted_projects = ["/abs/path"]` | 绝对路径（或 `~`）白名单；Windows 请写正斜杠（`"C:/work/repo"`），TOML 双引号里的 `\U` 是语法错误，通知里给出的建议值已是正斜杠。名单内目录的子目录同样受信；相对路径被忽略并告警；比较前先解析符号链接（受信目录里指向别处的链接不继承信任） |
| `[extensions] default_project_trust = "ask"` / `"never"` / `"always"`（默认 `ask`） | ≈ 上游 `defaultProjectTrust`：`ask` 在 ACP 客户端里询问；`never` 不询问也不授信（白名单、命令行、已保存的回答仍然有效）；`always` 信任所有项目、不询问 |
| `[extensions] trust_project_extensions = true` | 旧的全局开关，等价于 `default_project_trust = "always"`；显式写了 `default_project_trust` 时以后者为准 |
| 环境变量 `PI_TRUST_PROJECT_EXTENSIONS=1`（`1/true/yes/on`）或命令行 `--trust-project-extensions` | 单次运行（≈ 上游 `--approve`），适合 headless / CI；后者只是前者的另一种写法，对 ACP 与 headless 都生效 |
| ACP 里的回答「Trust and remember」→ `<home>/agent/trust.json` | 绑定到受门约束文件的内容指纹；文件变了就失效、重新询问（见下） |

只读取家目录的 `agent.toml`，绝不读取项目内的配置文件，否则仓库可以给自己授信；`load_local_env` 同样只读 `~/.pi-python/local.env`。

未受信的项目目录**不会被 import**：`ExtensionLoader` 只列出本会加载的模块名（`ExtensionLoader.skipped` / `AgentHarness.skipped_extensions`，元素为 `SkippedExtensions(directory, names)`），写一条 warning 日志。CLI 再告诉用户被跳过了什么（扩展，以及存在且本会生效的 prompt 文件与 skills 目录，`extension_trust.skipped_project_resources`）、怎样启用（ACP：`session/new` 应答之后的一条 `agent_message_chunk`；headless：stderr，stdout 仍只有回答）。

`AgentHarness(trust_project_extensions=False)` 与 `ExtensionLoader.load_all(trust_project_extensions=False)` 默认都是拒绝；CLI 通过 `extension_trust.decide_project_trust(config, cwd, home=)` 得出该值并放进会话的 `ProjectTrust`（`project_extensions_trusted` 是只看配置与命令行、不读文件的子集），`create_session_harness`（扩展）、`load_system_prompt_options`（prompt 文件）与 `load_session_resources`（skills）共用它。配置项沿用 `extensions` 之名，实际管的是上表的整组项目资源。

**交互式确认与内容指纹（ACP）。** 信任决定由 `extension_trust.decide_project_trust(config, cwd, home=)` 作出（会读文件并哈希，须在事件循环之外调用），先命中者为准：

| 顺序 | 条件 | 结果 |
|---|---|---|
| 1 | `PI_TRUST_PROJECT_EXTENSIONS` / `--trust-project-extensions` | 受信（`override`） |
| 2 | `default_project_trust = "always"`，或未设它时的旧开关 | 受信（`always`） |
| 3 | `trusted_projects` | 受信（`allow-list`） |
| 4 | 项目没有任何受门约束的资源 | 无需信任（`nothing`） |
| 5 | 资源太多 / 太大 / 读不了，取不出指纹 | 不受信、不提问（`unpinnable`）：回答无从可靠地记住，请写入 `trusted_projects` |
| 6 | `trust.json` 里有该目录的记录且指纹一致 | 受信（`saved`） |
| 7 | `default_project_trust = "never"` | 不受信、不提问（`never`） |
| 8 | 其余 | 有记录但指纹不同为 `changed`，无记录为 `undecided`：ACP 客户端会被询问，headless 不受信 |

- **提问方式。** 只有 ACP agent 会问，且仅当决定可询问、并连着客户端。先发一条普通 `agent_message_chunk`（`trust_prompt.trust_explanation`：目录、涉及的文件及各自的作用；TUI 只渲染权限请求的标题与选项，不渲染其 `content`，所以 tool call 不带 `content`）。这段文字里的名字来自被审查的仓库，而 Linux 上的文件名可以含换行、转义序列与方向控制符，所以不可打印字符写成转义（`\n`、`\x1b`），名字放进代码片段（栅栏比名字里最长的反引号串更长，名字关不掉它），扩展文件名清单最多 300 个字符并以 `…` 标明——仓库没办法给这段话添行。这条消息发不出去（多半是连接已断）就不再发问题：没被告知内容的是与否毫无价值。然后发 `session/request_permission`：合成的 tool call，标题 `loading this project's extensions and prompt files`（TUI 渲染成 “Allow …?”），两个选项——`Don't trust`（`reject_once`，排第一）与 `Trust and remember`（`allow_always`）。**刻意没有 `allow_once`**：ACP 允许客户端代用户批准权限请求，TUI 的 YOLO 模式会自动选第一个 `allow_once`，而是否运行一个仓库的代码不该由为工具调用开的模式代答；没有该选项，YOLO 无从自动选择，问题就落到真人面前。只有「选中且 id 恰为 `trust-project`」算同意（不复用 `permissions.outcome_allows`，它把一切不以 `reject` 开头的 id 当同意）；取消、未知 id、客户端报错一律视为不信任。不论 `permission` 模式（`auto` / `always-approve`）是什么都会询问——询问不等于自动授信。`raw_input` 只含 `project` / `resources` / `changed`，刻意避开 TUI 会据以推断含义的键（`command`、`file_path` 等）。
- **时机。** 在 `session/new`（及 `load` / `resume`）应答发出之后（客户端会丢弃对尚未登记的会话的请求，与 `available_commands_update` 同理，`agent._deferred_session_setup`）；得到回答之前不加载任何项目资源（扩展不被 import），会话的第一个 `prompt` 等待回答——`session/cancel` 结束此刻所有在等的 `prompt` 并让它们返回 `cancelled`（每次取消换一个新的事件，之后再来的 `prompt` 照常等），关闭 / 删除会话撤回问题。同目录并发打开的会话依次询问（按项目加锁；后到的会话在锁内重新判定：前一个答了「信任」就直接命中 `saved`，前一个拒绝了则再问一次，因为拒绝不被记住）。会话的信任由 `ProjectTrust` 承载，`create_session_harness` / `load_system_prompt_options` / `load_session_resources` 都读它，所以回答无需重建会话即生效（`AgentHarness.set_trust_project_extensions(bool)`，仅在扩展加载前有效）。
- **记住。** 选「Trust and remember」后，先对磁盘上的文件重新取指纹（对话框可能开了很久）：与提问时不同则不信任、不保存（通知原因为 `modified`）；相同才写入 `trust.json`。拒绝不被记住，下次仍会问。
- **指纹。** `trust_fingerprint.fingerprint_resources`：对所有受门约束资源的**文件内容**取 SHA-256（扩展目录递归；跳过 `__pycache__` 与 `.git`；跟随符号链接并防环；只哈希普通文件；条目上限 2000、总量上限 64 MiB，超出即取不出）。用内容而非 mtime：`touch` 不会让信任失效，`git pull` 改了文件会。
- **存储。** `trust.json` = `{"version": 1, "projects": {<目录键>: {"project", "fingerprint", "trusted_at", "resources"}}}`，按**精确目录**记录（键为 `normcase(realpath)`），不像白名单那样覆盖子目录。原子写（临时文件 + `os.replace`）；读取即关闭（缺失、读不了、不是 JSON、形状不对、版本不符都等于没有记录）；损坏的文件在下一次保存时先改名为 `trust.json.corrupt` 而不是被默默覆盖，读不了的文件保持原样并让保存报错。保存失败（目录只读等）时本次会话仍受信，并告诉用户「下次还会再问，想避免请写入 `trusted_projects`」。
- **headless** 从不提问（与上游的非交互模式一致），但承认指纹仍然一致的已保存回答。
- **通知带原因**（`untrusted_project_notice(why=)`）：未决定 / 自上次信任后已改变 / 提问期间已改变 / 你选择了不信任 / 无法询问客户端 / `default_project_trust` 为 `never` / 文件太多太大无法检查。

已知限制（相对上游）：

- 没有 `/trust` 命令，也没有「对此项目永不信任」的持久化；收回信任要手工删除 `trust.json` 里该项目的条目（`TrustStore.forget` 只是库函数）。
- 指纹只在会话开始时核对（以及对话框关闭后复核一次）：会话进行中再改文件不会撤销信任，已 import 的扩展也收不回。
- 换行符转换（`core.autocrlf` 切换、换分支带来的 CRLF/LF 差异）会改变内容哈希并触发重新询问：宁可多问。
- 客户端可以自动批准 `session/request_permission`（ACP 允许）。问题的形状已排除「自动选第一个 `allow_once`」这类做法，但没有协议层面的办法阻止一个无条件点同意的客户端；TUI 还会按「上次确认的选项类型」粘性预选光标，上次选的是 “always” 类时，「Trust and remember」会是高亮行。
- 客户端丢弃这个请求（例如尚未登记会话）时，取消被当作拒绝并在通知里说明；重新打开会话会再问一次。
- 授权以整个项目目录为单位，不区分单个扩展或资源。
- 上游还把 `.pi/settings.json`、`.pi/prompts`、`.pi/themes` 放在信任门后；本移植版没有这些项目级资源，无需处理。

### 4.5 扩展的身份、失败、工具名、家目录与打包

**身份（名称）。** 调用方没给名字时：模块级的 `activate` 函数（entry point 与目录扩展都是这种）以**模块名**为名；其余可调用对象以 `module.qualname` 为名。以前一律用 `activate.__module__`，同一模块里并排定义的两个扩展会互相覆盖，后者悄悄顶掉前者。同名重载仍然允许（重载同一扩展、项目扩展覆盖用户扩展都依赖它）；被**另一个**可调用对象顶替时，在激活成功之后写一条 warning（激活失败则回滚、原扩展保持不变，不会声称发生过替换）。`AgentHarness.load_extension(activate, name=...)` 可显式命名。

**失败不再无声。** 三种失败——目录里的模块/包在 import 时抛错、entry point 加载失败或解析不出 `activate`、`activate()` 抛错——都会写日志（含 traceback），并记入 `ExtensionLoader.failed` / `AgentHarness.failed_extensions`（`FailedExtension(name, source, error)`；`source` 为 `entry_point` / `directory` / `programmatic`，`error` 为 `ExceptionType: message`）。走到 `activate()` 才失败的扩展整体回滚，其余扩展照常加载。CLI 把失败告诉用户（`extension_notices.failed_extensions_notice`：ACP 在 `session/new` 应答之后发一条 `agent_message_chunk`；headless 写 stderr，stdout 仍只有回答），每条错误只取首行并截短。一个没有 `activate` 的普通 `.py`（辅助模块）不算错误；不想被扫描就以 `_` 开头命名。

**工具名。** `register_tool` 要求名字匹配 `[a-zA-Z0-9_-]{1,128}`（模型 provider 只接受这个）。不合法时在注册处抛 `ValueError`（指明扩展与规则），该扩展整体回滚。否则一个坏名字会让此后每个请求都被 provider 拒绝，整个会话不可用。

**一个「家」。** `pi_agent_core.home.pi_home(override=None)` 是唯一的解析器：参数 > `$PI_HOME` > `~/.pi-python`。CLI、`ExtensionLoader(home=)`、`AgentHarness(home=)`、`ExtensionAPI.home`（`pi.home`）和 pi-dynamic-workflows（journal、saved workflow）都走它。以前 CLI 认 `PI_HOME`，而 loader 与 workflow 扩展直接用 `Path.home() / ".pi-python"`：设了 `PI_HOME` 的会话会从一处读配置、从另一处读扩展、往第三处写 journal。

**打包。** 扩展包与核心一同发布，所以 `pi-agent-core-lc` 的下限就是仓库里的核心版本（旧下限 `>=0.3.0`，而 `pi_agent_core.extensions` 到 0.4.0 才出现；0.5.0 起是 `>=0.5.0`：扩展用到了 `ToolAnnotations` 等 0.4.0 没有的接口）。扩展包自己的版本号也要随发布抬高（0.5.0 时三个都从 0.1.0 到 0.2.0）：发布工作流的 PyPI 步骤带 `skip-existing`，版本号不变就会让已发布的旧构建原样留在 PyPI 上。用到 `pi_agent_harness` 的包（pi-dynamic-workflows）必须声明 `pi-agent-harness-lc`，并在 `[tool.uv.sources]` 里指向工作区；否则 `pip install` 之后 workflow 工具只能回答「sub-agents unavailable」。`pi_agent_core/tests/test_extension_packaging.py` 检查这三点：发版时抬高核心版本却忘了抬下限，会在 CI 里失败，而不是发布之后才暴露。

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
| 实现 | 见 §5.3：校验 URL → 逐跳校验并（直连时）固定解析到的地址 → `client.stream()` 带上限读取 → 如 `extract_text` 则转成文本（装了 `trafilatura` 用它，否则用自带的线性扫描器）→ `truncate_head` 截断 |
| 返回 | 页面文本内容；被截断时末尾附说明 |
| prompt_snippet | `"Fetch and read the contents of a URL"` |

### 5.2 依赖

- `httpx`（HTTP 客户端，已是 Python 生态主流，无 C 扩展）
- `trafilatura`（可选，`pip install pi-web-access-py[readability]`）

### 5.3 `fetch_url` 的安全边界与上限（审计 P7-09）

URL 由模型给出，而模型可能是在别人写的网页里读到它的。所以 `fetch_url` 不能成为「替陌生人让本机发请求」的通道。原实现对任何地址发请求、自动跟随重定向、把整个响应读进内存，还用一组在畸形标记上二次方退化的正则清洗 HTML（8000 个 `<script>` 要 2.5 s，16000 个要 10 s，且跑在事件循环上）；输出一旦被截断（超过 2000 行或 50 KB），它读了 `TruncationResult` 上并不存在的 `outputLines` / `totalLines`，于是任何较大的页面都以 `Failed to fetch …` 收场。

**只访问公网**

- 拒绝 loopback、私网、link-local（含云元数据 `169.254.169.254`）、CGNAT（`100.64.0.0/10`）、组播、保留段和 IPv6 site-local。IPv4 映射、6to4、NAT64（`64:ff9b::/96`）的地址按其中内嵌的 IPv4 判断。
- 字面量地址不论怎么写都会被识别：`127.1`、`2130706433`、`0x7f.1`、`0177.0.0.1`、`[::ffff:7f00:1]`、`127.0.0.1.`（先按 `ipaddress`，再按 `inet_aton` 的规则）。
- 按惯例指向本机或内网的名字不做解析、直接拒绝：`localhost`、`*.localhost`、`*.local`、`*.internal`、`*.localdomain`、`*.home.arpa`，以及不含点的单标签名。含 `%` 的主机名也拒绝（代理可能把 `%31%32%37.0.0.1` 解码成别的东西）。
- 只接受 `http` / `https`；URL 里带凭据（`user:pw@host`）或没有主机名一律拒绝。
- 拒绝文案只说「不在公网」，不透露名字解析到了什么地址（否则可以借工具探测内网 DNS）。
- 没有关闭开关（不提供 `allow_private` 之类的选项）；确有必要访问内网时，交给用户授权的工具（如 `bash` 里的 `curl`）去做。

**直连（没有代理）**：名字只解析一次，**所有**答案都必须是公网地址（有一个不是就整体拒绝，空答案也拒绝）。随后请求直接发往这个已校验的地址（IPv4 优先，仅在 `ConnectError` / `ConnectTimeout` 时换下一个地址），`Host` 头与 TLS SNI / 证书校验仍用原名字（httpx 的 `sni_hostname` 扩展）。没有第二次解析，DNS rebinding 无从下手；对 badssl.com 的过期证书、主机名不符证书实测会失败。

**经代理**（`HTTP(S)_PROXY` / `ALL_PROXY` / Windows 系统代理，沿用原有行为；`NO_PROXY` 与系统绕过规则命中时走直连）：域名由代理解析。本地解析既不能说明请求会落在哪里（在被污染的 DNS 下甚至是错的），也拿不到代理实际连接的地址，所以不做本地解析、不固定地址，只拒绝无需解析就能判断的东西：字面量地址与上面的本地名字。

> **已知限制**：代理把一个公网域名解析到内网地址时，工具拦不住；经代理时也没有「校验的地址即连接的地址」的保证（代理一侧的 rebinding 窗口）。需要更强保证的部署应在代理一侧限制出口，或不设代理走直连。

**重定向**：手动跟随，最多 5 跳。每一跳（scheme、凭据、字面量、名字、解析结果）都重新完整校验之后才发请求；相对 `Location` 以原 URL（名字）为基准，而不是以被固定的 IP；每一跳新建 client，不跨跳复用连接；`Location` 非法时由 httpx 报错，表现为 `Failed to fetch …`。

**上限**

| 项 | 值 |
|---|---|
| 正文 | 5 MiB：`client.stream()` + `aiter_bytes()`，读到上限即停，不读完（正好 5 MiB 的正文不算被截断）；被截断时输出末尾说明 |
| 重定向 | 5 跳 |
| 总耗时 | 30 s：`asyncio.wait_for` 覆盖整条重定向链与 HTML 转换；分步超时 connect / write / pool 10 s、read 20 s |
| 编码 | 请求 `Accept-Encoding: identity`；charset 取自响应头（未知则按 UTF-8，`errors="replace"`） |
| 输出 | `truncate_head` 的 2000 行 / 50 KB，`max_length` 只能把行数改小；被截断时说明「显示了多少行 / 为什么」；第一行就超过 50 KB（压缩过的 JSON 之类）时给出它的前 50 KB（在 UTF-8 字符边界处截断），而不是空内容 |

**HTML → 文本**：装了 `trafilatura` 时优先用它，任何异常或空结果都回退到自带实现；两者都在 `asyncio.to_thread` 里跑，不占事件循环。自带实现是单遍线性扫描器：丢弃标签、注释、声明以及 `<script>` / `<style>` 的内容，块级标签变成换行，字符引用解码；没有闭合的标签、注释、脚本会吞掉其后的全部内容（与浏览器一致）；引号里的 `>` 会让标签提前结束，其余的属性值显示为文本（近似值，装了 `trafilatura` 时不受影响）。不用标准库的 `HTMLParser`，是因为它在畸形输入上同样是超线性的（实测）；数字字符引用过长（十进制 ≥ 8 位、十六进制 ≥ 7 位）先替换为 U+FFFD，因为 `html.unescape` 对超过 4300 位的十进制引用会抛 `ValueError`。

**明确的偏差与残余风险**

- 工具不响应回合的 abort 信号，靠总耗时停止（与 `web_search` 相同）；超时后 `trafilatura` 的工作线程会自己跑完，无法中断。
- 服务端无视 `Accept-Encoding: identity` 而发压缩正文时，由 httpx 解压：一次读取至多 64 KiB 压缩数据，按 zlib 的最大压缩比（约 1000:1）单块解压后瞬时可达约 64 MiB，之后被 5 MiB 上限截断。
- 见上面「经代理」的已知限制。

---

## 6. pi-goal-x 移植

### 6.1 核心机制

- `/goal <description>` 命令启动目标模式
- 注入 prompt_guidelines 告诉 LLM 使用 `goal_update` 和 `goal_complete` 工具
- `goal_update(step_index, description, status, details)` — 报告步骤进度。`step_index` 从 0 开始，与状态清单方括号里的编号一致（`⬜ [0] 设计`）；省略则追加新步骤，此时 `description` 必填。索引不存在、或新步骤没有 `description` 时，`GoalState.update_step` 抛 `ValueError`（消息里写明现有步骤的编号范围），工具调用以错误结果返回给模型，状态不变（审计 P7-11：原先会追加一个 `Unnamed step` 并报告成功；状态清单又从 1 开始编号，正好诱导模型传错）
- `goal_complete(summary)` — 标记目标完成
- `on("turn_end")`：所有步骤都是 done、模型却没有调用 `goal_complete` 时，发一条提醒（`send_message`，会引出下一轮）。**最多提醒 2 次**（`MAX_COMPLETION_REMINDERS`），之后不再提醒；某个 `turn_end` 上步骤不再全部 done、或用 `/goal` 重新开始，计数清零；对已完成的步骤再做 `goal_update` 不清零（否则会话会在「更新—提醒」里循环）。原实现每轮都提醒：harness 与 CLI 默认都没有回合上限（`max_turns` 为 `None`），无视提醒的模型会一直被提醒到用户中断；审计复现时配了 `max_turns = 12`，打满 12 次 LLM 调用后以 error 收场
- 持久化：状态**每次变化时**用 `append_entry("goal_state", data)` 保存（`/goal`、`goal_update`、`goal_complete`），所以最后一条就是当前状态；没有变化的回合不写。`append_entry` 先排队，在回合结束或 `agent_end` 时落盘。原实现只在还有未完成步骤的回合末保存，`goal_complete` 从不保存，于是已完成的目标在恢复会话时又变回「进行中」
- `on("session_start")` 恢复已有目标状态：读最后一条 `goal_state` entry；已完成的目标不恢复

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
    reminders_sent: int = 0   # 只在本进程内计数（提醒上限），不随状态保存
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
| pi-web-access | mock httpx 响应 → 验证输出格式；多提供商切换；无 API key 时报错文案；`fetch_url`（`test_fetch_url_safety.py`）：非公网地址（各种写法）、本地名字、凭据、非 web scheme 一律拒绝且不发请求，DNS 固定与 SNI，重定向逐跳校验，经代理，正文 / 重定向 / 总耗时上限，输出截断说明，HTML 转换的线性性；另有一组对真实 httpx 客户端的回环服务器测试 |
| pi-goal-x | GoalState 状态机转换；append_entry 持久化 → session_start 恢复；turn_end 进度检查 |
| pi-dynamic-workflows | WorkflowRuntime agent/parallel/pipeline 编排；TokenBudget 限额；5 个 built-in pattern 脚本语法校验；workflow tool 参数解析 |

---

## 9. Phase 4: 移植 pi-dynamic-workflows

### 9.1 概述

上游 [`@quintinshaw/pi-dynamic-workflows`](https://github.com/QuintinShaw/pi-dynamic-workflows)
是 Pi 最大的第三方扩展之一（600KB+ TS），核心是让 LLM 编写脚本来编排 subagent 并行执行。
Python 移植将脚本语言从 JavaScript 改为 Python，运行在独立的子进程里（§9.3）。

### 9.2 包结构

```
packages/pi-dynamic-workflows/
├── pyproject.toml
└── pi_dynamic_workflows/
    ├── __init__.py              activate(pi) + /workflows 命令
    ├── workflow_tool.py         workflow 工具定义
    ├── runtime.py               WorkflowRuntime：把脚本交给沙盒运行，并做宿主一侧的事（子 agent、预算、journal）
    ├── sandbox/                 脚本沙盒：独立进程 + 资源限制（设计见 2026-10-01-workflow-sandbox-design.md）
    │   ├── host.py              宿主侧：起进程、协议校验、限制、生命周期
    │   ├── child.py             脚本进程里运行的程序（只依赖标准库）
    │   └── winjob.py            Windows Job Object
    ├── paths.py                 子 agent 的 cwd 约束
    ├── builtin_workflows.py     5 个内置 pattern
    ├── model_routing.py         tier 路由
    └── budget.py                token budget tracking
```

### 9.3 核心机制

- **`workflow` 工具**：LLM 传入 Python 脚本或 `name`（内置 pattern）
- **独立进程里的沙盒**（`sandbox/`，完整设计见 `2026-10-01-workflow-sandbox-design.md`）：脚本在一个只有标准库的
  子进程里运行，命名空间只暴露 `agent()`, `parallel()`, `pipeline()`, `phase()`, `log()`, `budget`, `args`,
  `cwd`, `result` 和一小组内建函数，用来引导脚本只做编排。有副作用的事（起子代理、日志、预算）都走管道，
  由宿主执行，宿主把管道另一头当作不可信输入。子进程有清空的环境、空的工作目录和内核限制（POSIX 的 rlimit 与
  `NO_NEW_PRIVS`；Windows 的 Job Object 与低完整性），再加一个审计钩子。
  钩子是速度栏，不是边界：走出命名空间的脚本在各平台上还能做什么（Linux 上读文件、联网、读同用户进程的
  环境变量；Windows 上读文件、联网），见该规格 §9。真正约束脚本副作用的仍是子代理工具调用所经过的权限策略
  （见 §12「权限继承」）。
  *（此前是进程内 `exec` + 受限命名空间，审计 P7-01 实测有两条路径走出命名空间，且 `while True` 会冻住宿主的事件循环。）*
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
| 项目目录扩展信任 | ✅ | `<cwd>/.pi-python/extensions` 默认不加载；ACP 里询问并按内容指纹记住，或白名单 / `default_project_trust` / 环境变量 / `--trust-project-extensions`（§4.4） |
| 子包 entry_points 声明 | ✅ | 3 个子包的 `pyproject.toml` 均声明 `[project.entry-points."pi_agent.extensions"]` |
| System prompt 注入 | ✅ | 扩展注册的工具的 `prompt_snippet` / `prompt_guidelines` 自动进入 system prompt |
| LLM 可用性 | ✅ | `workflow` / `web_search` / `fetch_url` / `goal_update` / `goal_complete` 工具在 TUI 会话中自动注册，LLM 可直接 tool_call |
| Slash 命令路由 | ✅ | `AgentHarness.dispatch_command()` + `_try_slash_dispatch()` 拦截 `/command args`；`PiAcpAgent._advertise_commands()` 通过 `AvailableCommandsUpdate` 填充 TUI/Zed 自动补全 |

### 10.2 尚未适配项

| 项 | 说明 | 影响 |
|---|---|---|
| **`ctx.ui.*` API** | 上游 pi 的 `ctx.ui.confirm()` / `ctx.ui.select()` / `ctx.ui.notify()` / `ctx.ui.setWidget()` 等 TUI 交互 API 未实现 | 需要时扩展可通过 `send_message()` 降级实现文本反馈 |
| **SubagentExecutor 真实实现** | §12 ✅ | `HarnessSubagentExecutor` 已实现，含 coding tools + env |
| **Background run / result delivery** | §13 ✅ | `trigger_message()`（带类型的自定义消息）空闲时触发新 turn、运行中并入当前 turn；`register_cleanup()` 在会话 `close()` 时回收任务 |
| **Run persistence / resume** | §14 ✅ | 首次 run 自动创建 Journal，按 run_id 恢复；格式校验防路径穿越 |
| **Git worktree isolation** | §15 ✅ | `WorktreeManager` 创建隔离 worktree；snapshot baseline + apply_changes 回写 |
| **Saved workflow 存储** | §16 ✅ | `WorkflowStore` scan/save，project 覆盖 user；`os.link` 原子 no-clobber |
| **Task panel / widget** | TUI 侧的实时进度面板未移植 | 纯文本结果输出替代 |
| **TUI 文件夹信任库 ↔ 项目扩展信任** | TUI 自己的信任库（`/hooks trust`）与 Python 端 §4.4 互不相通 | TUI 用户会在 ACP 会话里被询问（§4.4）；也可在 `agent.toml` 白名单里登记项目，或带 `PI_TRUST_PROJECT_EXTENSIONS=1` 启动（环境变量会传给被拉起的 Python agent）。`/hooks trust` 的记录不会被 Python 端读取 |

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

**造不出执行器时不再退回 mock。** 没有 harness、harness 没给 `model` / `stream_fn`、或构造时抛错，`activate` 就注册一个 `UnavailableSubagentExecutor(reason)`：`workflow` 工具（含 built-in 名称与后台请求）直接回答 `The workflow tool cannot run in this session: sub-agents are unavailable (<reason>).`，原因同时写 warning 日志。以前静默退回 `MockSubagentExecutor`，脚本用罐头答案「跑完」并且报告成功。`MockSubagentExecutor` 只留给不传执行器的 `create_workflow_tool()`（测试替身）。

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

- `workflow` 工具本身不声明 annotations，所以 `ask` 模式下启动 workflow 需要用户批准（规则见下面的「权限模型」）；
  `auto` / `always-approve` 不弹窗，与其它工具一致。
- 桥接新增 `tool_call_gate`（即 `AgentHarness.check_tool_call`）：对父 harness 的 `tool_call` hook 链
  （权限层 + 扩展 hook，首个 block 生效，见 §3.5）跑一遍，返回 `None`（放行）或
  `{"block": True, "reason": ...}`。`HarnessSubagentExecutor(tool_call_gate=...)` 给每个子代理 harness
  装一个 `tool_call` hook，把它的每次工具调用送进该 gate。**批准 `workflow` 不等于批准其子代理的写操作**：
  每次调用逐个询问（声明只读的工具 `read` / `grep` / `find` / `ls` 与会话自己一样不询问，但扩展 hook 照样生效）。
- gate 抛异常按拒绝处理（fail-closed）：hook 异常在 harness 里表现为该调用失败，工具不会执行。
- `ToolCallEvent.origin`：子代理的调用带 `{"kind": "subagent", "cwd": ..., "label": ...}`（会话自己的调用为
  `None`）。权限弹窗标题据此写成 `write (workflow sub-agent in <cwd>)`：脚本可以用 `agent(..., cwd=)` 把
  子代理指向项目内的某个子目录，而弹窗只显示工具入参，相对路径本身看不出落点。`label` 是脚本自己起的文本，
  只进 `origin`，不进标题。（`cwd` 被限制在项目目录内，见下面的「已知限制」。）
- 工具调用 id 由模型生成（`call_1` …），会在父会话与各子代理之间重复，而权限弹窗以 id 为键，所以每次子代理
  运行的 id 加前缀 `subagent-<8 hex>:`。
- CLI 对同一会话的权限询问串行化（每会话一个锁）：并行子代理不会同时弹出多个弹窗。
- 子代理等待权限回复的时间计入其 `timeout_ms`。
- 没有 gate 的 executor（独立使用，或未实现 `tool_call_gate` 的旧 bridge）没有可继承的策略，子代理不受限；
  `activate()` 发现 bridge 没有 `tool_call_gate` 时记录 warning。
- `isolation=True` 用 `HarnessSubagentExecutor.with_worktree_manager()`（浅拷贝）派生 executor，gate 与其它配置
  不会在派生时丢失（此前用构造函数重建，新增配置容易漏传）。
- **`cwd` 约束**：脚本给 `agent(..., cwd=)` 的目录必须落在项目目录之内（相对路径相对项目目录解析，符号链接与
  目录联接跟随后再比较）。越界在占用 agent 名额与查 journal 之前就报错，所以收紧后的策略不会被一次 resume 绕过；
  `HarnessSubagentExecutor` 在建 worktree 之前也检查一次，独立使用时同样受约束。规则见沙盒规格 §8.1。
- 已知限制：`auto` / `always-approve` 下子代理可执行一切，与会话自己一致；`isolation=True`（worktree）时子代理
  在 worktree 根运行，`cwd` 的子目录被忽略；脚本进程里的审计钩子可被绕过，钩子被绕过之后脚本在各平台上还能做
  什么见沙盒规格 §9（§9.3 的脚本运行方式）。

**权限模型（默认询问，按工具自述放行）**：`ask` 模式原来是一张工具名白名单（`PERMISSION_TOOLS = {bash, edit, write,
workflow}`），名单之外的工具——包括任何扩展注册的——不问就执行。现在反过来：每次调用都询问，除非工具自己声明无害。

- 工具带 `annotations`（`pi_agent_core.types.ToolAnnotations`，名字与含义同 Model Context Protocol：`readOnlyHint` /
  `destructiveHint` / `idempotentHint` / `openWorldHint`，全部可选，缺省 = 未声明）。`SimpleTool` / `CodingTool` /
  `ToolDefinition` 都有该字段；`from_langchain_tool` 从 `tool.metadata` 取这四个布尔提示（`langchain-mcp-adapters` 把
  MCP 工具的 annotations 放在那里；值不是真正布尔的不取）。循环本身不读 annotations，只有权限层读。
- 放行规则（`pi_agent_cli.permissions.needs_permission(mode, annotations)`）：`auto` / `always-approve` 从不询问；`ask` 下
  `readOnlyHint is True` 放行，或 `destructiveHint is False` **且** `openWorldHint is False` 放行（MCP 的含义：只做增量
  更新、交互范围是封闭的，例如把笔记记进会话的目标跟踪工具）；其余一律询问——没声明、声明了别的、值是 `"true"` / `1`
  这类非布尔、annotations 不是映射，都问。MCP 规范对缺省值同样按最坏情况算（非只读、有破坏性、开放世界）。注意第二条
  比「只读」宽：一个自称只做增量、范围封闭的工具，即使往项目里新建文件也不会被问，全凭作者的声明（见下）。
- harness 在 `tool_call` 事件上带 `ToolCallEvent.annotations`：`AgentHarness.check_tool_call` 按工具名在**自己的**工具表里查
  （会话自己的循环与 workflow 子代理的调用走同一条路径）；工具表里没有该名字、或工具没声明就是 `None`。事件上的字典是
  副本，hook 改不到工具自己的。所以子代理的调用按父会话里**同名工具**判定，父会话没有的名字没有 annotations，要问。
- 内置：`read` / `grep` / `find` / `ls` 声明 `readOnlyHint: true`（与 `READ_ONLY_TOOL_NAMES` 一致，测试钉住）；`bash` /
  `edit` / `write` 不声明。随包扩展：`web_search` / `fetch_url` 声明 `readOnlyHint` + `openWorldHint`（只读，但会联网）；
  `goal_update` / `goal_complete` 声明 `readOnlyHint: false, destructiveHint: false, openWorldHint: false`（只在会话里
  记笔记）；`workflow` 刻意不声明——启动它是用户的决定（它的子代理能写文件、跑命令）。
- 提示是工具作者的一面之词，运行时不验证。内置与随包工具是我们自己的；扩展是用户选择加载的代码（它本来就能做更糟的
  事，标错一个提示并没有让它多出能力）。MCP 规范要求客户端把 annotations 视为不可信，除非来自受信任的服务器（恶意服务器
  可以把破坏性的工具标成只读）；`from_langchain_tool` 原样转交 `tool.metadata` 里的提示，所以连接了不信任的 MCP 服务器的
  调用者要先去掉这些提示。本 CLI 目前不把 ACP `session/new` 的 `mcpServers` 接成工具（该参数被忽略），不受此影响。
  没有按工具放行的配置项：想放行一个没声明的第三方工具，只能补上 annotations，或改用 `auto` 模式。
- 行为变化：`ask` 模式下，不声明 annotations 的扩展工具现在会询问（此前直接执行）。

**Bridge 扩展**：`HarnessBridge` 增加 `stream_fn` / `model` / `get_api_key_fn` / `tool_call_gate`
只读属性，`activate()` 据此构造真实 executor；另有 `trigger_message(custom_type, text, *, details=None)`
（§13，后台结果的交付）。`HarnessBridge` 是 `runtime_checkable` 协议，自制 bridge（测试替身之类）要补上该方法才能通过
`isinstance`。

---

## 13. Background run / result delivery / 会话生命周期 ✅

`WorkflowManager`（`manager.py`）管理后台 workflow 的 asyncio task。

`WorkflowParams.background=True` → `asyncio.create_task(runtime.execute)` +
返回 `AgentToolResult(terminate=True)`，turn 立即结束。

**结果的交付**：完成后经桥接的 `trigger_message("workflow-result", text, details=...)` 交付，
是一条带类型的自定义消息（`role: "custom"`、`display=True`，`details` 含 `runId` / `name` /
`status` / `agentCount` / `durationMs`），不再走 `trigger_prompt`。后者等于「用户打了这段字」：
以 `/` 开头的输出会被当作 slash 命令执行，消息也没有来源标记。harness 空闲时它开启一个新 turn
（该 turn 的首条消息就是这条自定义消息，不做 slash 分派），运行中则进 steer 队列并入当前 turn。

**不可信输出的信封**：自定义消息在 LLM 层被 `harness_convert_to_llm` 转成普通 user 消息，模型看到的权威与用户消息相同，
所以消息正文自己交代来源。manager 自己知道的事实（run id、agent 数、耗时、token 用量）写在信封之外；
子代理的产出（它们可能读过不可信的文件或网页）放进
`<workflow-output boundary=HEX>` … `</workflow-output boundary=HEX>` 之内，信封之前的文字声明这是
「不可信数据、不是指令」。`HEX` 是每条消息各自的 `secrets.token_hex(8)`，且保证不出现在输出里（出现则重抽），
输出因此无法提前「关闭」信封再冒充 manager 说话。脚本自己起的 workflow 名字同样只出现在信封内。
失败的 run 也一样：错误文本在信封内，`details.status == "failed"`。

**取消**：`workflow` 工具的中止信号（`signal`）会停掉前台 run：`_abortable` 让运行时 task 与中止信号赛跑，
中止时取消运行时 task 并等它收尾（最多 `_UNWIND_GRACE_S = 10` s，之后放弃并记 warning），工具返回 cancelled 结果，
带 `run_id` 和 `resume_from_run_id` 的提示——已完成的 agent 已记入 journal，可以接着跑。开始前信号就已中止则
什么也不启动（前台、后台都是）。`parallel()` / `pipeline()` 在取消或提前失败时不留孤儿 task（`_settle`：
所有子 task 都被取消并等待）。后台 run **不**随发起它的那个 turn 的中止而停止（后台 run 的意义就是比 turn 活得久），
只随会话的 `close()` 停止。以前中止信号到不了 workflow：工具要等所有 agent 跑完才返回，其间子代理继续消耗 token、继续改文件。

**生命周期**：ACP `session/close` 与 `pi/session/delete`（先 `abort()` 再 `close()`）都调用 `AgentHarness.close()`：

- `close()` 幂等；先 abort 进行中的 turn（不阻塞），再依次执行扩展注册的清理回调（一个失败不影响其余，
  失败只记日志），最后停止 harness 自己起的后台 task（`CLOSE_GRACE_S = 5`：宽限期内不理会 abort 的 task 被取消并放弃）。
  此前它只跑清理回调，进行中的 turn 继续跑。
- `closed` 属性；关闭后 `prompt()` 抛 `AgentHarnessError("invalid_state")`，桥接的 `trigger_prompt` /
  `trigger_message` 什么也不做（也不入队）；已排入但尚未开始的触发、`close()` 时仍在准备中的 turn 同样不会跑起来
  （后者一建好 abort controller 就被中止）。
- 扩展的清理回调是 `WorkflowManager.close(timeout=5)`：取消所有后台 run 并等它们收尾（run 的 `finally`
  会清理 worktree）；超时未停的 run 记 warning 后放弃。此后不再交付任何结果，`start_background()` 抛
  `RuntimeError`。以前的回调是 `shutdown()`——最多等 30 s 让 run 跑完，而 run 的结果随后会在已结束的会话上
  开启一个新的 LLM turn。
- `cancel_all()`（只取消、不等待）与 `shutdown(timeout)`（等 pending 或超时后取消）保留，会话收尾不再使用。

**已知限制**：

- 自定义消息在 LLM 层是 user 角色（与 `bashExecution` 等一致）；信封是文本约定，不是协议级隔离。
- 后台结果触发的 turn 没有对应的 `session/prompt` 请求。ACP v1 没有禁止 turn 之外的 `session/update`，
  但客户端未必渲染。v2 草案的 `state_update`（idle / running）正是为此设计的，**本项目不发**：它只存在于协议 v2，而 v2
  仍是需要显式选择的不稳定草案，并且是破坏性的（`session/prompt` 改为受理即返回用户消息的 `messageId`，回合结束改由
  idle 的 `state_update` 报告）。Python SDK（`acp.schema.SessionNotification`）与 TUI 所用的 Rust crate
  （`agent-client-protocol-schema` 0.11.4 的 `SessionUpdate`：没有此变体，也没有兜底变体）都读不了它，在 v1 会话里发出去
  会被拒收；本 agent 对 `initialize` 总是回答不大于 1 的版本。等 SDK 与客户端支持 v2 之后再重新评估
  （`test_state_update_is_not_a_session_update_the_sdk_can_read` 是个绊线）。
  v1 里能做的是：客户端并不知道会话正被这样一个 turn 占着，此时到达的 `session/prompt` 不再以 `busy` 失败，而是等该 turn
  结束再运行（等待期间 `session/cancel` 或关闭会话让它以 `cancelled` 结束；同一客户端同时发出两个 prompt 仍是 `busy`）。
- 自定义消息只进 session 与 LLM 上下文，`project_message_replay`（`session/load` 的历史回放）不含它，
  与实时 UI 一致（实时 UI 也不显示自定义消息）。
- 不合作的 run（脚本吞掉取消）在宽限期后被放弃：它仍在后台运行，直到自己结束，但不再交付结果。

---

## 14. Run persistence / resume ✅

`Journal`（`journal.py`）— append-only JSONL 日志，记录每个 `agent()` 调用的
`hash_request("agent", {prompt, opts})` SHA-256 + 返回值。

**重放**：journal 是「按请求索引的缓存」，不是逐步对账的记录。`try_replay(kind, req_hash)` 返回**同一请求**
中本次 run 尚未用过的最早一条记录（命中则跳过 LLM 调用），与记录顺序无关（`parallel()` 按完成顺序写入），
也与脚本其它部分是否改动无关；相同请求按记录顺序依次回放。未命中什么都不改：不丢弃记录，也不重写文件
（以前是按游标顺序匹配、不匹配就从该点截断：并行 run 的 resume 一条也命中不了，一个改动过的步骤还会让其后
所有已完成的记录被删掉）。本次 run 自己新追加的记录不会回放给自己：可回放的集合，是回放开始时文件里已有的。
上限 10000 条 / 64MB。`Journal.truncate_from` 已移除。失败的 `agent()` 调用（`result.error`）不写入
journal：失败不是答案，resume 时（provider 恢复、超时放宽之后）应当重试；脚本本身照旧拿到 `None`。
（以前失败被记成成功的 `None`，resume 时原样回放。）

**已知限制**：请求未变的步骤照样回放，即使它前面的步骤因为改动而重跑了；被回放的步骤当初对工作区做过的事
（比如改文件）不会重做。

**集成**：`WorkflowRuntime.__init__(journal=...)` → `agent_fn` 内先查
journal，miss 时执行并 append。`WorkflowParams.resume_from_run_id`
指定前次 run_id，journal 存储于 `<家目录>/workflow-journals/<run_id>.jsonl`（家目录即会话的
`pi.home`，见 §4.5）。`run_id` 按 `fullmatch(r"[a-zA-Z0-9_-]{1,128}")` 校验（run_id 会变成文件名；
`$` 会放过结尾换行）。

---

## 15. Git worktree isolation ✅

`WorktreeManager`（`worktree.py`）通过 `git worktree add --detach` 创建
linked worktree，每个 subagent 在独立目录中运行。

**Snapshot 模式**（默认）：`git diff --binary HEAD` + `git diff --binary --cached HEAD` 复制
staged/unstaged 变更到 worktree（`--binary`：否则改过的二进制文件只是一行 `git apply` 拒绝的
「Binary files differ」，连累整个快照）。**Clean 模式**：从指定 ref checkout。

`collect_diff()` 返回 `bytes`：先 `git add -A`，再 `git diff --binary --cached HEAD`。子代理**新建**的文件
因此在补丁里（`git diff HEAD` 只列已跟踪文件），二进制文件靠 `--binary` 带上；补丁全程是 bytes、从不解码
（非 UTF-8 文件经 decode/encode 往返会损坏，`git apply` 随后拒绝整个补丁）。`apply_changes()` 把补丁 apply 回
源 cwd；`cleanup()` / `cleanup_all()` 删除 worktree。

**清理**：`HarnessSubagentExecutor` 在 `finally` 中删除本次调用创建的 worktree（回答、超时、异常、取消都算），
只有拿到回答才 apply 变更。前台 run 结束（含被中止）时 `workflow_execute` 的 `finally` 调 `cleanup_all()`；
后台 run 由 manager 的 `cleanup=` 回调在 run 结束时清理（完成、失败、取消都会跑）。

**限制**：仅 git（非 jj）；不含 btrfs/overlay/NFS 优化；快照不复制源目录里**未跟踪**的文件（子代理看不到它们）；
apply 失败（例如补丁与源目录此刻的状态冲突）时记 warning，该子代理的变更丢失。

---

## 16. Saved workflow 存储 ✅

`WorkflowStore`（`store.py`）扫描 `<家目录>/workflows/`（用户级，家目录即会话的 `pi.home`，
见 §4.5）和 `.pi-python/workflows/`（项目级）的 `.py` 脚本。构造函数的旧参数 `home=`
仍表示「用户主目录」（其下再拼 `.pi-python`）；新参数 `pi_home=` 直接给出家目录，两者同时给出时以 `pi_home` 为准。

**Meta 提取**：`extract_meta(script)` 通过 `ast.literal_eval` 安全解析脚本
顶层 `meta = {...}` 字典，无需执行脚本。`meta` 不是 dict（如 `meta = 5`）或 `name` 不是字符串时按「没有 meta」处理，
一个写坏的脚本不会让整个目录的扫描失败。

**注册**：`activate()` 中 `store.scan()` → 为每个 saved workflow 注册
slash command，通过 `AvailableCommandsUpdate` 暴露给 TUI 自动补全。

**安全**：名称 `fullmatch(r"[a-z0-9][a-z0-9-]{0,63}")`（名称会变成文件名；`$` 会放过结尾换行）；256KB 上限；原子写入 + no-clobber；
`save_project()` / `save_user()` 分别写入项目/用户目录。
