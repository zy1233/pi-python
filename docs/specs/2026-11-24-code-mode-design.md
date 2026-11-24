# Code Mode 与延迟工具加载（`pi-codemode`）— 架构设计方案

> Scope：为 `pi-python`（`zycode`）引入对齐上游 `pi` 1.0 `codemode` + `tool_search` 以及 Claude Code `Code Mode` / Programmatic Tool Calling（PTC）的程序化工具编排与按需工具发现能力。
> Status：**Approved（2026-11-24，D1–D8 全部采用推荐方案，按 §12 落地）** — 设计已确认，按 §12 分 4 个 PR 依次实现。
> Base of facts：`main` @ `49d38a9`（分支 `feat/code-mode`）。
> 关联规格与不变量：`AGENTS.md` 不变量 1–9d、`docs/specs/2026-10-01-workflow-sandbox-design.md`（子进程沙盒）、`docs/specs/2026-10-23-mcp-extension-design.md`（`pi-mcp`）、`docs/specs/2026-11-02-auto-mode-design.md`（Auto 权限模式）、`docs/specs/2026-10-31-agent-binaries.md`（`zycode-agent` 单文件冻结产物）、`docs/specs/2026-11-05-acp-extension-registry.md`（ACP 线协议登记表）。

---

## 1. 上游与业界调研对比

### 1.1 上游 `pi`（`earendil-works/pi` 1.0，`@earendil-works/pi-codemode` + `tool_search`）

上游 `pi` 在 1.0（2026-10）反转了早期“不内置 MCP”的立场，选择以 **Codemode** 作为原生工具编排与 MCP 承载底座，主要解决两类上下文与延迟瓶颈：

1. **消除多轮串行工具调用的 ping-pong 与上下文膨胀**：传统循环中，模型每读一个文件、跑一次 grep、解析一页 API 结果都要消耗一轮 LLM 往返，且所有原始中间结果永久留在会话上下文里。Codemode 让模型在单轮内写入一段短小异步脚本，直接在沙盒中做循环、并发调用、结构化过滤和聚合，中间大数据不出沙盒，仅显式输出或返回值进入主对话上下文。
2. **原生承载 MCP 与延迟工具暴露（Deferred Tool Loading）**：如果把若干 MCP server 的数十上百个工具 schema 全部塞进每次请求的 `tools` 参数和系统提示词，每轮都会固定白烧数千 tokens。Pi 1.0 在引入精简版 Codemode 与延迟工具暴露后，官方发布说明给出了复杂场景约 **40% prompt tokens 降幅**（如从 ~5,300 tokens 降至 ~3,300 tokens）。

上游实现的关键设计要素：

| 要素 | 上游 `pi` 1.0 设计 |
|---|---|
| 核心工具接口 | `codemode` 单工具，参数主要为 `code: string`（裸 JS/TS 异步函数体，不包 Markdown 围栏，支持顶层 `await` 与 `return`） |
| 沙盒运行时 | `@earendil-works/pi-codemode`（`CodemodeSandbox`），在 Worker 线程内跑编译为 WebAssembly 的 QuickJS VM；无 Node.js API、无文件系统、无网络栈、无定时器，严格依赖注入的全局桥出沙盒 |
| 沙盒内置全局对象 | `tools.<normalized_name>(args)`（连字符等非标识符字符归一化为 `_`，如 `mcp__dev-radius__search` → `tools.mcp__dev_radius__search`）、`ALL_TOOLS`（`Record<string, { description, signature }>`）、`searchTools()` / `describeTool()`、`text(...)`、`image(...)`、`console.log(...)`（聚合入 `<console_output>`） |
| 工具发现与按需加载 | Deferred 暴露的 MCP server 在初始系统提示词仅保留概览，由脚本内 `searchTools()` / `ALL_TOOLS` 或独立工具 `tool_search` 动态发现并按需激活 |
| 运行配置与控制 | 以内建扩展形态提供，支持配置启停与 `/codemode` 命令切换，并改进了工具参数校验失败后的错误恢复提示 |

### 1.2 Claude Code（`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`）与 Anthropic PTC

Claude Code 自 v2.1.323 起通过实验标志 `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1` 提供对应的 Code Mode 能力（即 Anthropic Programmatic Tool Calling 在本地 CLI 的实现）：

1. **启停边界**：仅可由环境变量或用户级设置 `~/.claude/settings.json` 开启，**出于安全原因明确忽略项目级配置文件**（这与本仓库 `AGENTS.md` 不变量 9 完全同源）。
2. **工具接口（`REPL`）与 JS 辅助原语**：模型调用内部 `REPL` 工具（载荷在 `tool_input.code`），脚本内通过 `$` 模板标签调 shell（支持并发扇出、`.text()` 读取与本地过滤）、`edit` 等文件辅助函数修改代码；**脚本最后一条表达式的求值结果**自动作为返回值送回模型。
3. **超时与后台执行**：前台脚本超时上限为 10 分钟；当会话处于 `auto` 或 `bypassPermissions` 模式时，长耗时脚本可推入后台运行最长 2 小时。
4. **Anthropic API PTC（`code_execution_20260120`）**：在 API 层通过服务端 Python 容器工具 `code_execution_20260120` 配合每个自定义工具的 `allowed_callers: ["code_execution_20260120"]` 白名单字段实现，容器在单回合/会话内保留状态。
5. **关键安全教训——「运行期动态值盲区」（Runtime Value Blindspot）**：
   - Claude Code 中，脚本内部发出的单次操作仍会经由本地 `PreToolUse` 钩子与常规 allow/ask/deny 规则检查（其中对 `Bash` 的钩子必须审查完整复合命令）。
   - 然而在 **Auto 模式**下，Claude Code 的安全分类器是在**脚本执行前**对整段生成的 JS 源码做静态审查，而不是在脚本运行期逐条审查实际求值后的调用参数。
   - **安全漏洞所在**：当脚本在运行期通过搜索结果动态拼接路径或命令（例如先跑 `rg` 取得列表再在循环里构造变量传给 `edit` 或 `$`）时，执行前的静态分类器根本看不到运行期才会算出来的真实路径或命令行，从而形成高风险的审查盲区。**`pi-python` 的设计必须从架构上彻底根除这一缺陷。**

### 1.3 Python 生态参考实现调研（`pydantic-monty` / `python-quickjs-ng`）

- **`pydantic-monty`（Pydantic AI CodeMode 引擎）**：由 Rust 编写的受限 Python 子集解释器（`pydantic-monty-client` + `pydantic-monty-runtime` 二进制，`AsyncMonty` 进程池），冷启动 ~1 ms，默认无系统调用。但其仅实现了 Python 语法与标准库的受限子集，模型在编排中顺手写的常见模块或语法（如完整 `re` 特性、`difflib`、`urllib.parse`、`ast` 等）容易触发子集不支持错误，且会为本仓库 5 平台 PyInstaller 打包引入新的 Rust 二进制分发与故障面。
- **`python-quickjs-ng`（`quickjs-ng` C 扩展）**：在 Python 内嵌 QuickJS-NG 引擎，支持 `set_memory_limit`、`set_time_limit`、`set_max_stack_size`。但一方面会强迫纯 Python Agent 体系下的模型在工具调用参数里切换成写 JavaScript（且无法直接复用模型习惯的 Python `re` / `json` / `difflib` 处理文本）；另一方面 C 扩展同步 `eval` 与 `asyncio` 异步工具桥接（协程挂起/取消/超时清理）复杂度高，且新增跨 5 平台原生轮子依赖。

### 1.4 综合对比与本仓库选型结论

| 维度 | 上游 `pi` 1.0 | Claude Code Code Mode | `pydantic-monty` | **`pi-python` 选型（本方案）** |
|---|---|---|---|---|
| **脚本语言** | JavaScript / TypeScript | JavaScript (`REPL`) | Python 受限子集 | **标准 Python 3.11+（`async def` 包装 + 尾表达式捕获）** |
| **沙盒隔离机制** | QuickJS WASM + Worker | 本地 JS 执行环境 | Rust 解释器子进程池 | **提升并复用仓库现有 `pi_agent_core.sandbox` 多平台子进程沙盒（独立进程 + 清空环境 + PEP 578 审计钩子 + Linux `rlimit`/`NO_NEW_PRIVS` + Windows Job/低完整性）** |
| **MCP 与延迟加载** | `codemode` + `tool_search` + `deferred` | 无内置对应 deferred 机制 | 需外层框架自行封装 | **原生补齐 `ToolDefinition.deferred` + `tool_search` + `pi-mcp` 自动协同** |
| **Auto 模式审查时机** | 由宿主权限回调处理 | **执行前静态审整段源码（存在运行期值盲区）** | 框架自定义 | **运行期逐条同步审查具象化后的真实调用参数，且在 Auto 下强制 `decide → execute` 串行闭环** |
| **跨轮次状态** | 无状态（单次函数体） | 偏无状态（不依赖跨轮变量） | 支持按会话维护状态 | **v1 无状态（单次脚本干净执行，可复现、与会话恢复一致）** |
| **后台执行** | 不作为核心路径 | Auto/bypass 下最长 2 小时 | 无内置 | **v1 不支持（长异步任务走已有 `pi-dynamic-workflows` 与 `/goal`）** |
| **依赖与打包代价** | Node 原生链 | Node/Bun | 额外 Rust worker 轮子 | **零新增第三方依赖、零新增冻结二进制（复用唯一 `pi-sandbox-child`）** |

---

## 2. 定位、目标与非目标

### 2.1 `codemode` 与 `workflow`（`pi-dynamic-workflows`）的清晰边界

本仓库已具备 `packages/pi-dynamic-workflows`（`workflow` 工具），两者定位完全正交，共享底层多平台子进程沙盒内核，但上层抽象不可混淆：

| 对比项 | `codemode`（`pi-codemode-py`） | `workflow`（`pi-dynamic-workflows-py`） |
|---|---|---|
| **解决的问题** | **单回合内（intra-turn）** 程序化编排现有工具、本地过滤大体积输出、按需发现调用大规模 MCP 工具集 | **跨子智能体（multi-agent）** 复杂长链路任务编排（如深调研、对抗审阅、多视角审计） |
| **额外 LLM 开销** | **0 次额外 LLM 调用**（仅跑当前回合确定性脚本逻辑，除非 Auto 模式模型裁判步审查特定 `bash` 调用） | **多次子智能体 LLM 调用**（支持 `small`/`medium`/`big` 路由与 token 预算账本） |
| **脚本原语** | `tools.*`、`call_tool`、`ALL_TOOLS`、`search_tools`、`describe_tool`、`parallel`、`text`、`image`、`print` | `agent`、`parallel`、`pipeline`、`phase`、`budget`、`log`、`result` |
| **持久化与隔离** | 无跨轮状态；操作直接作用于当前会话上下文与工作区（受会话权限策略约束） | 支持 journal 断点重放、独立 git worktree 隔离（需项目受信任） |
| **互调约束** | **禁止**在 `codemode` 脚本内递归调用 `codemode`、`workflow` 或 `tool_search` | 子智能体拥有独立 `AgentHarness`，受 `tool_call_gate` 约束 |

### 2.2 核心目标

1. **单轮编排与上下文瘦身**：支持模型通过一次 `codemode` 调用并发或循环调用 `read`/`grep`/`find`/`ls`/`edit`/`write`/`bash` 与 `mcp__*` 工具，在沙盒进程内完成解析、过滤、diff 计算与汇总，中间大文本留存沙盒内销毁，只将精简结果或图片返回主 LLM 上下文。
2. **原生 Delayed / Deferred Tool Loading**：在 Core/Harness 层引入 `ToolDefinition.deferred`，配合 `tool_search` 与沙盒内 `search_tools` / `describe_tool` / `ALL_TOOLS`，让 `pi-mcp` 挂载大量 MCP 工具时不再把完整 JSON Schema 常驻塞入每一轮 LLM 请求。
3. **彻底根除 Claude Code 的「运行期值盲区」**：`codemode` 内发起的**每一次真实工具调用**，均在参数完全求值后通过 `AgentHarness.invoke_tool` → `check_tool_call` 进入当前会话的扩展钩子与权限层（`ask` / `auto` / `always-approve`），严格守住 `AGENTS.md` 不变量 8、9、9b、9c、9d。
4. **零新增外部依赖与单文件打包兼容**：不引入 QuickJS C 扩展或额外 Rust 运行时；将 `pi_dynamic_workflows.sandbox` 上提为 `pi_agent_core.sandbox`，`packaging/agent/build.py` 维持**单一 `pi-sandbox-child`** 冻结子程序，保证 Windows Job `ActiveProcessLimit=1` 与 5 平台发布包构建完全兼容。
5. **100% 标准 ACP 线协议兼容**：复用现有标准 `tool_call`、`tool_call_update`、`session/request_permission`、`available_commands_update`，`docs/specs/2026-11-05-acp-extension-registry.md` **零新增非标扩展项**；`zypi`、`vscode/`、Zed 开箱可用。
6. **配置信任边界**：`[codemode]` 仅从用户主目录 `~/.pi-python/agent.toml` 读取，任何项目目录内的配置均不生效。

### 2.3 非目标

1. **不做跨轮次持久化内存 REPL**：每次 `codemode` 调用都是独立、干净的脚本执行。跨轮保存变量会破坏会话 JSONL 树回放/分支切换（`navigate_tree`）的一致性，并诱发隐式状态依赖。
2. **v1 不做后台脱钩执行**：若允许 `codemode` 脱离当前 turn 在后台跑 2 小时并持续改写工作区文件，将直接破坏 `AutoSession` 的单会话顺序性假设（`docs/specs/2026-11-02-auto-mode-design.md` 风险 R4）。长耗时异步任务由已有的 `workflow` 与 `/goal` 承担。
3. **不在 Core/Harness/CLI 内置 MCP 客户端**：完整保留 `docs/specs/2026-10-23-mcp-extension-design.md` §1.3 架构立场——MCP 连接仍由可选扩展 `pi-mcp-py` 负责，`core`/`harness` 仅提供通用的 `deferred` 暴露原语与 `invoke_tool` 门控通道。
4. **不宣称 CPython 子进程沙盒具备容器级安全边界**：严格沿用 `docs/specs/2026-10-01-workflow-sandbox-design.md` §9 的事实边界描述，真实对抗恶意载荷仍需外部 OS 沙盒（`zypi --sandbox`、容器或 VM）。

---

## 3. 总体架构与模块划分

### 3.1 分层数据流

```
LLM (AssistantMessage: tool_call "codemode", args={"code": "..."})
  │
  ▼
AgentHarness (当前会话)
  │ 1. 外层 tool_call 校验与钩子（默认 require_approval=false -> harmless 免打扰；
  │    开启 require_approval=true -> 先弹一次外层确认）
  ▼
pi_codemode.CodemodeRuntime (_CodemodeHost)
  │ 2. 注入会话工具表快照（ALL_TOOLS 元数据）与限制参数，启动/复用策略走共享沙盒
  │
  ├─ stdin  (行式 JSON 协议 v2: run{mode="codemode", script, tools_meta, ...})
  ▼
┌─────────────────── 沙盒进程 (pi_agent_core/sandbox/child.py) ───────────────────┐
│ · 独立进程 (-I -S -B -X utf8)、清空 env、空临时 cwd、fd 0/1 空设备             │
│ · 内核限制 (Linux rlimit/NO_NEW_PRIVS, Windows Job ActiveProcessLimit=1/低完整性)│
│ · PEP 578 审计钩子 + _safe_import 纯计算标准库白名单                             │
│ · AST 编译包装为 async def __codemode_main__() + 尾表达式捕获                   │
│ · 全局对象: tools.*, call_tool, ALL_TOOLS, search_tools, describe_tool,         │
│            parallel, text, image, print, ToolError, ToolPermissionError         │
└─────────────────────────────────────────────────────────────────────────────────┘
  │ stdout (行式 JSON: call{id, fn="tool", tool, args} / output / result / log)
  ▼
_CodemodeHost (宿主事件循环)
  │ 3. 校验消息大小/深度/在途数/总调用数；剔除禁止递归工具 (codemode/workflow/tool_search)
  │ 4. Auto 模式下通过串行门控锁保证: 非只读调用严格 decide -> execute -> decide
  ▼
AgentHarness.invoke_tool(call_id="codemode-<run>:<seq>", tool_name, args, origin={"kind":"codemode", ...})
  ├─ a. prepare_arguments + validate_tool_arguments (报错则结构化返回并附工具签名)
  ├─ b. check_tool_call -> 扩展钩子 & PiAcpAgent._handle_tool_call
  │       └─ ask / AutoSession.decide(实参!) -> 必要时 ACP request_permission 弹窗
  └─ c. tool.execute(call_id, validated_args, signal, on_update)
          └─ 若为 bash 且 Auto 放行，自动按 call_id 消费 BashLimitRegistry 限额
  │
  ▼
将 ToolText(text, details, images) 回传子进程；子进程本地过滤聚合后返回最终 text/images
  │
  ▼
ACP client (zypi / vscode / Zed): 单条 ToolCallStart -> 节流 progress 更新 -> 最终结果
```

### 3.2 模块职责映射表

| 包 / 模块 | 职责与改动 | 依赖与打包影响 |
|---|---|---|
| `pi_agent_core/sandbox/` *(新增，自 `pi_dynamic_workflows.sandbox` 上提)* | 通用子进程沙盒 `host.py`、`child.py`、`winjob.py`（协议 v2）：同时支持 `mode="workflow"` 与 `mode="codemode"` | 仅依赖 Python 标准库；无新增外部依赖 |
| `pi_agent_core/extensions/types.py`、`api.py`、`registry.py`、`harness_bridge` | `ToolDefinition` 增加 `deferred: bool = False`；`ToolInfo` 增加 `parameters`、`annotations`、`deferred`、`active` 视图；`ExtensionAPI` / `HarnessBridge` 新增 `invoke_tool(...)` 与 `activate_tools(...)` | 保持向后兼容，所有新字段带默认值 |
| `packages/pi-agent-harness/` | `AgentHarness` 维护 `_deferred_tool_names` 与延迟激活生命周期；实现 `invoke_tool(...)` 复用核心参数校验 + `check_tool_call` + `tool.execute` | 核心层测试全量回归 |
| `packages/pi-dynamic-workflows/pi_dynamic_workflows/sandbox/` | 变为对 `pi_agent_core.sandbox` 的薄 re-export 层，保持原有公开符号与既有沙盒测试路径零破坏 | 无需重复维护两套沙盒代码 |
| `packages/pi-codemode/` *(新增 workspace 成员 `pi-codemode-py`)* | `codemode` 工具、`tool_search` 工具、`CodemodeRuntime`、签名桩生成与工具名归一化、输出裁剪与进度流、`/codemode` 命令 | 作为标准 `pi_agent.extensions` entry point 注册，仅依赖 `pi-agent-core-lc` |
| `packages/pi-mcp/` | `Settings` 支持暴露模式（`direct` / `deferred`）：当 `pi-codemode` 存在且启用延迟暴露时，以 `deferred=True` 注册 MCP 工具并输出紧凑摘要 | 约 15 行协同改动，无 `pi-codemode` 时行为不变 |
| `packages/pi-agent-cli/` | `[codemode]` 用户主目录配置（`config.py` / `agent.example.toml`）、系统提示词摘要、ACP 图标与标题美化（`events.py`）、`AutoSession` 与 `permissions.py` 对 `origin.kind == "codemode"` 的安全接入 | 纳入 `pi-agent-cli` 既有依赖与测试 |
| `packaging/agent/build.py` & `zycode-agent.spec` | 将 `pi-sandbox-child` 冻结源路径更新为 `pi_agent_core/sandbox/child.py`，打包 `pi_codemode` 模块与元数据 | 单文件 `zycode-agent` 仍只内嵌一个 `pi-sandbox-child` |

---

## 4. `codemode` 与 `tool_search` 工具契约及脚本运行时语义

### 4.1 顶层工具定义

#### 4.1.1 `codemode` 工具

- **工具名**：`codemode`
- **Pydantic 参数 Schema**：
  ```python
  class CodemodeParams(BaseModel):
      code: str = Field(
          description=(
              "Async Python script body (no markdown fences). Supports top-level `await`, "
              "`return`, or a trailing expression. Call session tools via `await tools.<name>(...)` "
              "and discover deferred tools via `search_tools(...)` / `describe_tool(...)`."
          )
      )
      timeout_ms: int | None = Field(
          default=None,
          ge=100,
          le=600_000,
          description="Optional wall-clock timeout override in milliseconds.",
      )
  ```
- **工具描述动态生成策略（对齐 Pi 1.0 Token 精简策略）**：
  - `codemode` 的 `description` 在每次构建工具上下文时由会话当前工具列表渲染，但不展开所有工具的完整 JSON Schema 散文，仅输出：
    1. **直接工具签名桩**（每工具 1 行，约 10–25 tokens）：
       ```text
       Available tools on `tools`:
       - read(*, path: str, offset: int | None = None, limit: int | None = None) -> ToolText [readOnly]
       - edit(*, path: str, oldText: str, newText: str) -> ToolText
       - write(*, path: str, content: str) -> ToolText
       - bash(*, command: str, timeout: int | None = None) -> ToolText
       - grep(*, pattern: str, path: str | None = None, ...) -> ToolText [readOnly]
       - find(*, pattern: str, path: str | None = None) -> ToolText [readOnly]
       - ls(*, path: str | None = None) -> ToolText [readOnly]
       ```
    2. **延迟工具（Deferred）命名空间摘要**：不逐个列出参数，仅输出分组计数：
       ```text
       Deferred tools (discover via `search_tools(query)` / `describe_tool(name)` / `ALL_TOOLS`):
       - mcp__context7__* (2 tools)
       - mcp__github__* (18 tools)
       ```
    3. **沙盒原语速查**（固定 ~60 tokens）：`call_tool`, `parallel`, `text`, `image`, `print`, `ToolError`。
- **围栏容错**：部分模型偶发在 `code` 参数外包裹 ```` ```python ... ``` ````，宿主在传给沙盒前使用正则剥离首尾对称围栏。

#### 4.1.2 `tool_search` 伴生工具

对齐上游 Pi 1.0 的 `tool_search`：用于会话在 **非 `codemode`（`hybrid`）模式** 下，把按需发现的延迟工具提升为模型可直接发起 JSON `tool_call` 的工具。

- **工具名**：`tool_search`
- **Annotations**：`ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)`（`ask` / `auto` 下均直接放行）。
- **参数**：
  - `query: str`：空格分词子串或 `/regex/` 检索工具名与描述；传 `"*"` 或空串可列出全部延迟工具摘要。
  - `limit: int = 10`（1..50）。
  - `activate: bool = True`：命中后是否立即将匹配的延迟工具加入当前会话 `active_tool_names`（在下一轮 LLM 请求中携带其完整 JSON Schema）。
- **返回内容**：命中工具的名称、签名、简述，以及已激活工具列表提示。

### 4.2 脚本编译与返回值规则

`pi_agent_core/sandbox/child.py` 在 `mode="codemode"` 下对 `script` 做如下确定性 AST 变换：

1. 使用 `ast.parse(script, filename="<codemode>", mode="exec")` 解析语法；若失败，直接报 `SyntaxError` 与准确行号/列号，不启动异步循环。
2. 包裹为顶层协程函数：
   ```python
   async def __codemode_main__():
       ... # 原始 AST 语句列表
   ```
   使用 `ast.fix_missing_locations` 与保留原节点 `lineno` / `col_offset`，确保异常堆栈中 `<codemode>` 行号与模型传入的 `code` **逐行完全对齐**。
3. **尾表达式自动捕获（对齐 Claude Code REPL 习惯）**：
   - 若函数体最后一条语句是 `ast.Expr` 且其顶层调用不是显式输出原语（`print`、`text`、`image`），将该表达式替换为：
     ```python
     __cm_last_value__ = <expr>
     return __cm_last_value__
     ```
   - 若脚本内部已显式执行 `return <val>`，则以显式 `return` 为准；若最终返回值为 `None`，不附加多余 `"None"` 文本（除非没有任何其他输出且也没有调用过工具）。

### 4.3 沙盒内全局命名空间与原语语义

| 全局符号 | 类型 / 签名 | 行为与说明 |
|---|---|---|
| `tools` | 只读代理对象 | 属性访问 `tools.<ident>` 返回异步代理函数；不支持调用的工具（`codemode`、`workflow`、`tool_search`）在属性读取时立即抛 `PermissionError` |
| `call_tool(name, args=None, /, **kwargs)` | `async def` | 通过**原始工具名**（如 `"mcp__dev-radius__search"`）或归一化名动态调用工具，方便脚本从 `ALL_TOOLS` / `search_tools` 动态查找后调用 |
| `ALL_TOOLS` | 只读 `MappingProxyType` | `{tool_name: {"call_name": str, "description": str, "signature": str, "parameters": dict, "deferred": bool, "read_only": bool}}`，**零 LLM token 开销** |
| `search_tools(query="", *, limit=20)` | `def`（纯本地内存） | 对 `ALL_TOOLS` 做本地关键词（多词 AND）或正则检索，返回有序命中列表 |
| `describe_tool(name)` | `def`（纯本地内存） | 按原始名或 `call_name` 返回单工具完整参数 Schema、注解提示与 Python 调用样例；找不到时抛出附带 `difflib.get_close_matches` 相近建议的 `KeyError` |
| `parallel(*coros_or_fns, concurrency=8, return_exceptions=False)` | `async def` | 有界信号量并发调度工具协程或零参可调用对象；任一项取消时清理未完成任务 |
| `text(*values, sep=" ")` | `def` | 显式追加正文本输出块到最终工具结果（对齐 Pi 1.0 `text()`） |
| `image(data_or_block, mime_type="image/png")` | `def` | 追加图片块（支持直接传入 `res.images[0]`、或 `{"data": ..., "mimeType": ...}`、或 base64 字符串），透传进 `AgentToolResult.content` |
| `print(*values, sep=" ", end="\n")` | `def` | 写入沙盒日志缓冲，汇总到 `<console_output>` 区块（不进 stdout JSON 协议通道） |
| `ToolError` / `ToolPermissionError` | `RuntimeError` 子类 | 工具参数错误、未知工具、返回 `is_error=True` 或被拒绝授权时抛出，附带 `.tool_name`、`.code` 与正确参数签名提示 |
| 预置常用模块 | `json`, `re`, `math`, `asyncio` | 无需 `import` 即可直接使用 |

#### 4.3.1 工具名标识符归一化（`signatures.py`）

- 规则与上游 `@earendil-works/pi-codemode` 对齐：将非 `[a-zA-Z0-9_]` 字符（常见于 MCP 工具名中的连字符 `-` 或点号 `.`）替换为 `_`；若首字符为数字则前缀 `_`；若命中 Python 关键字（如 `import`、`class`）则追加 `_`。
- **冲突消歧**：按注册顺序检测碰撞，后碰撞者依次加 `_2`、`_3`；同时保留原始精确名称映射，`call_tool(raw_name, ...)` 永远精确无歧义，并在 `ALL_TOOLS[raw_name]["call_name"]` 中明示映射后的属性名。

#### 4.3.2 双调用约定与 `ToolText` 返回值兼容性

1. **双调用约定**：LLM 在写编排代码时有时按 Python 习惯传关键字参数 `await tools.read(path="a.py", limit=50)`，有时受 JS 习惯影响传单字典 `await tools.read({"path": "a.py", "limit": 50})`。代理封装同时接受这两种写法（若传入单一 `dict` 位置参数且无关键字参数，解包为工具参数；若某单参数工具接受快捷位置参数或关键字参数冲突则清晰报错提示），彻底消除因调用习惯差异导致的无谓重试回合。
2. **`ToolText(str)` 返回类型**：工具执行完成后，宿主将 `AgentToolResult.content` 中的所有文本块拼接并通过管道返回，子进程侧封装为继承自 `str` 的 `ToolText` 对象：
   - 作为字符串：`isinstance(res, str) is True`，可直接传入 `json.loads(res)`、`re.findall(..., res)`、`res.splitlines()`、`len(res)`、`print(res)`。
   - `.text -> str`：显式读取纯文本。
   - `.details -> dict[str, Any]`：读取工具附带的 JSON 化 `details` 字段（如 `exit_code`、`truncation` 等）。
   - `.images -> tuple[dict[str, str], ...]`：读取工具返回的图片块列表，便于脚本做 `if res.images: image(res.images[0])`。

#### 4.3.3 纯计算标准库白名单导入（`_safe_import`）

`mode="codemode"` 保留 `workflow` 的进程/环境/内核隔离与 PEP 578 审计钩子，同时向脚本的 `__builtins__.__import__` 提供仅放行**纯计算、无 I/O、无进程/网络能力**的标准库白名单：

- **允许导入的模块**：`json`, `re`, `math`, `cmath`, `statistics`, `decimal`, `fractions`, `random`, `collections`, `collections.abc`, `itertools`, `functools`, `operator`, `difflib`, `fnmatch`, `string`, `textwrap`, `unicodedata`, `datetime`, `time`（仅纯时间读取/格式化函数，拒绝 `sleep` 阻塞或改由 `asyncio.sleep` 限流）、`csv`, `io`（仅 `StringIO` / `BytesIO`）、`hashlib`, `hmac`, `base64`, `binascii`, `urllib.parse`, `ast`, `copy`, `dataclasses`, `enum`, `typing`, `types`, `pathlib`（仅导出纯路径运算类 `PurePath` / `PurePosixPath` / `PureWindowsPath`，屏蔽触碰本地磁盘的 `Path` 方法）。
- **明确拒绝并返回指引**：试图导入 `os`、`sys`、`subprocess`、`socket`、`http`、`urllib.request`、`shutil`、`tempfile`、`ctypes`、`gc` 等模块时，直接抛出：
  `ImportError: Module 'os' is disabled in codemode sandbox; use await tools.read / grep / find / ls / bash instead.`

### 4.4 最终输出组装与体积保护

脚本结束时，`child.py` 按以下固定结构向宿主返回结果，由 `codemode_tool.py` 组装为 `AgentToolResult`：

1. **Text 部分**：
   - 若存在 `text(...)` 显式追加的内容，按调用顺序拼接；
   - 若存在非 `None` 尾返回值 / `return` 值：`str` 原样写入，`dict`/`list`/`tuple`/基本类型以紧凑 JSON（`ensure_ascii=False`）写入；
   - 若存在 `print(...)` 输出，包裹在独立 `<console_output>\n...\n</console_output>` 块中（沿用 Pi 1.0 约定）；
   - 若以上全部为空且有工具曾被调用，返回简洁确认提示 `(codemode finished with no output)`，避免向模型返回空 toolResult。
2. **输出截断保护**：最终文本超过 `max_output_bytes`（默认 64 KiB）时执行头尾或尾部截断（复用 `pi_agent_core.coding_tools.truncate` 逻辑），附加明确提示：`Output truncated to 64 KiB; filter or summarize inside the codemode script before returning.`
3. **Images 部分**：收集所有 `image(...)` 调用传入的图片块（上限保护，例如单次最多 8 张），随文本块一并放入 `AgentToolResult.content`，供支持多模态的模型直接查看。

---

## 5. 沙盒协议 v2 与共享 `pi-sandbox-child` 设计

### 5.1 从 `pi_dynamic_workflows.sandbox` 上提至 `pi_agent_core.sandbox`

`docs/specs/2026-10-01-workflow-sandbox-design.md` 设计的多平台进程沙盒已经经过 Windows + WSL + macOS CI 与变异测试严格验证。本方案将其**直接上提为基础设施包 `pi_agent_core/sandbox/`**，并把行式 JSON 协议从 v1 演进为向后兼容的 v2：

- `pi_agent_core/sandbox/host.py`：`ScriptSandbox`、`SandboxLimits`、`ScriptOutcome`、`ScriptHost`（Protocol）。
- `pi_agent_core/sandbox/child.py`：仅依赖 Python 标准库，支持 `--mode workflow|codemode`（或由 `run` 消息携带 `"mode"` 字段，同一冻结可执行文件通用）。
- `pi_agent_core/sandbox/winjob.py`：Windows Job Object 与低完整性令牌逻辑不变。
- `pi_dynamic_workflows/sandbox/{__init__,host,child,winjob}.py`：保留兼容 re-export，`pi_dynamic_workflows` 的现有测试（`test_sandbox_isolation.py`、`test_sandbox_protocol.py`、`test_sandbox_frozen.py`）全部继续通过。

### 5.2 协议 v2 消息定义（兼容协议 v1）

每条消息仍为单行 `ensure_ascii=True` JSON：

| 方向 | `t` | 新增 / 变更字段 | 含义 |
|---|---|---|---|
| 子→宿主 | `ready` | `protocol: 2` | 握手 |
| 宿主→子 | `run` | `mode: "workflow" \| "codemode"`、`script`、(`workflow`: `args/cwd/budget`；`codemode`: `tools_meta/max_output_bytes/max_log_chars`) | 启动对应模式运行时 |
| 子→宿主 | `started` | `layers[]` | 审计钩子与内核层已就绪 |
| 子→宿主 | `call` | `id`、`fn`（`"agent"` 或 `"tool"`）、`payload` | `workflow` 调子 agent；`codemode` 调会话工具 `{tool, args}` |
| 宿主→子 | `reply` | `id`、`ok`、`value`（`codemode` 含 `{text, details, images}`）或 `etype`+`message`+`meta` | 工具结果或结构化异常回传 |
| 子→宿主 | `cancel` | `id` | 子进程协程被取消时通知宿主中止对应工具调用 |
| 子→宿主 | `log` | `msg` | `print` / `log` 行 |
| 子→宿主 | `output` | `kind: "text" \| "image"`、`data` | `codemode` 显式输出块（受单条与总量上限约束） |
| 子→宿主 | `phase` | `title` | `workflow` 阶段通知 |
| 子→宿主 | `result` | `value` | 最终返回值 |
| 子→宿主 | `done` | `meta` | 正常结束 |
| 子→宿主 | `error` | `etype`、`message`、`line` | 脚本语法或运行异常 |

### 5.3 默认资源限制（`CodemodeSandboxLimits`）

| 限制项 | 默认值 | 说明 |
|---|---|---|
| `memory_bytes` | `256 MiB` | Linux 地址空间 / Windows Job 已提交内存（比 `workflow` 512 MiB 更紧凑） |
| `cpu_seconds` | `30 s` | 沙盒自身纯计算 CPU 时间（不含等待宿主工具执行耗时） |
| `wall_seconds` | `120 s` | 整次 `codemode` 墙钟上限（可由参数/配置放宽至最高 `600 s`） |
| `open_files` | `64` | POSIX 文件描述符上限 |
| `max_message_bytes` | `4 MiB` | 单条双向消息上限（大工具输出在宿主或截断后回传，防 OOM） |
| `max_inflight_calls` | `16` | 沙盒同时向宿主发起的最大并发工具调用数（超过触发宿主背压） |
| `max_tool_calls` | `100` | 单次脚本运行累计允许的工具调用次数硬上限（防无限循环刷调用） |
| `max_output_bytes` | `64 KiB` | 汇总给模型的最终文本输出上限 |
| `max_log_lines` / `max_log_chars` | `2000` / `256 KiB` | `print` 捕获上限 |

### 5.4 PyInstaller 冻结构建（`packaging/agent/`）

`packaging/agent/build.py` 与 `packaging/agent/zycode-agent.spec` 的修改保持最小化：

1. `CHILD_SCRIPT` 改为指向 `pi_agent_core/sandbox/child.py`。
2. `datas` 中单一冻结子进程目录放在 `pi_agent_core/sandbox/frozen-child`（程序名仍为 `pi-sandbox-child` / `pi-sandbox-child.exe`），不再维护两份冻结子程序，**单文件体积几乎零增长**。
3. `OUR_PACKAGES` 与 `OUR_DISTRIBUTIONS` 加入 `pi_codemode` 与 `pi-codemode-py`（`pi_agent_core/sandbox` 本身已在 `pi_agent_core` 内自动收集）。

---

## 6. 权限模型、Auto 模式安全闭环与消除「运行期值盲区」

本节直接回应 `AGENTS.md` 不变量 8、9、9b、9c、9d，以及 §1.2 中 Claude Code 暴露的静态分类器盲区问题。

### 6.1 双层权限架构：为什么必须“外层免打扰、内层运行期逐条门控”

如果把 `codemode` 简单视为一个不透明黑盒工具（`annotations=None`）：
- 在 `ask` 模式下，哪怕模型只是想把 5 次只读 `read` + `grep` 合并为一轮 `codemode`，用户也会被迫每次都点一次授权弹窗，直接倒退回比不用 `codemode` 更差的用户体验；
- 更致命的是，一旦用户在 `ask` 模式下“允许一次 `codemode`”，若内层调用不再受控，模型就可以在同一个脚本里夹带 `edit` 写 `.git/hooks` 或跑 `rm -rf`；
- 而在 `auto` 模式下，如果像 Claude Code 那样在运行前让模型审查 JS/Python 源码文本，脚本只要先 `await tools.find(...)` 再动态拼路径变量传给 `await tools.write(...)`，执行前分类器就完全处于瞎子状态。

因此，本设计确立如下双层契约：

1. **外层 `codemode` 与 `tool_search` 的声明**：
   - **默认配置（`[codemode] require_approval = false`）**：`codemode` 启动的仅是一个受限计算沙盒，其本身不直接读写磁盘或联网，所有外部副作用都必须通过内层 `tools.*` 经过宿主门控，因此 `codemode` 默认声明 `ToolAnnotations(destructiveHint=False, openWorldHint=False)`（在 `ask` 与 `auto` 下免去外层冗余弹窗，严格满足 Rule A0 单调性：`ask` 免问则 `auto` 必免问）。
   - **保守开关（`[codemode] require_approval = true`）**：为高敏环境提供配置选项；开启后 `codemode.annotations = None`，在 `ask` 与 `auto` 模式下每次执行脚本前先弹一次外层确认。
   - **`tool_search`**：声明 `ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)`，`ask` 与 `auto` 下均直接放行。
2. **内层每一次工具调用的运行期实时门控**：
   - 脚本运行到 `await tools.<name>(args)` 时，所有表达式和变量**已经在沙盒内求值成具体字面量**，作为确定性 JSON 载荷到达宿主 `_CodemodeHost`。
   - 宿主为该次调用分配全局唯一 ID `codemode-<run_id>:<seq>`，构造：
     ```python
     origin = {
         "kind": "codemode",
         "cwd": session_cwd,
         "parent_call_id": outer_tool_call_id,
     }
     ```
   - 宿主调用 `AgentHarness.invoke_tool(...)`，内部依次执行：
     1. 检查禁止列表（禁止递归调用 `codemode`、`workflow`、`tool_search`）；
     2. 查找 `self._tools[tool_name]`，执行 `prepare_arguments` 与 `validate_tool_arguments`（如果参数自身无效，立即向脚本抛出含正确签名的 `ToolError`，**无需无效打扰权限弹窗**）；
     3. 调用 `await self.check_tool_call(call_id, tool_name, validated_dict, origin=origin)`，触发完整 `tool_call` 钩子链与 `PiAcpAgent._handle_tool_call`；
     4. 仅当钩子与权限系统均放行（`None`）后，才调用 `tool.execute(...)`；若被拒绝，向脚本抛出 `ToolPermissionError(reason)`（携带原因但不泄露敏感配置细节），脚本无法绕过。

**效果**：无论脚本内做了多少层字符串拼接、Base64/JSON 解析、循环与条件分支，真正触及 `auto_rules`、`AutoSession` 模型裁判和用户确认弹窗的，永远是**即将落盘或执行的具象化参数**——**Claude Code 的「运行期值盲区」在架构层被 100% 消灭**。

### 6.2 `AutoSession` 与 `permissions.py` 的三处精确适配

结合现有代码 `packages/pi-agent-cli/pi_agent_cli/auto_mode.py`（L656–L688）与 `permissions.py`（L71–L107）实现，必须做以下三处精确改造：

1. **识别 `origin.kind == "codemode"`（`auto_mode.py` `_decide`）**：
   - 现状（L660–L666）：当前 `_decide` 仅接受 `origin.get("kind") == "subagent"`，其他 `origin` 一律返回 `session.unknown_origin`（ASK）。若改为不传 `origin`，又会命中 L672 `_other_calls_open`（因外层 `codemode` 工具调用正处于运行状态，误判为 `session.parallel_batch` 而把所有内层只读外的一切操作全部拦成 ASK）。
   - 改造：`auto_mode.py` 显式允许 `origin.get("kind") in {"subagent", "codemode"}` 并校验 `origin["cwd"]`（`codemode` 无独立子工作区，必须等于会话 `cwd`）；当外层是 `codemode` 时，`_other_calls_open` 忽略其 `parent_call_id` 本身，仅防并发互扰。
2. **Auto 模式下的内层串行门控锁（守护不变量 9d）**：
   - 不变量 9d 要求：**While Auto is on, the harness runs the calls of one assistant message one by one: decide, run, decide, run.**
   - 若脚本内使用 `asyncio.gather(tools.write(...), tools.bash(...))` 或 `parallel(...)` 并发发出多个内层工具调用，必须防止“第 1 个调用已通过 `decide` 正在写文件/改软链，第 2 个调用在旧文件状态下并发通过 `decide`”。
   - **机制**：`_CodemodeHost` 持有异步互斥锁（或由 bridge 暴露会话是否处于串行策略 `is_sequential_policy`）：当 `harness.tool_execution == "sequential"`（Auto 模式开启时 `AutoSession` 会自动设为 `"sequential"`）时，所有需要经过 `check_tool_call` / 非纯只读声明的工具调用在宿主侧严格通过锁串行化为 **`decide → execute → 释放锁 → 下一个 decide → execute`**。纯只读且无副作用声明的工具（`read` / `grep` / `find` / `ls`）在无写入锁占用时仍可全速并发！
3. **`BashLimitRegistry`、审计记录与 ACP 弹窗互斥**：
   - `AutoSession.decide` 放行内层 `bash` 后，按 `call_id = "codemode-<run_id>:<seq>"` 存入 `self.limits.put(call_id, ...)`；`bash` 工具执行该 ID 时自动消费 `bash_timeout_seconds` 与 `bash_max_output_mb` 限额，语义与主循环及 `workflow` 子智能体完全一致。
   - `pi.auto_decision` 会话审计条目记录 `"origin": "codemode"`（或在 `/auto-log` 中可读展示），不写入命令原文或模型文本。
   - `permissions._prompt_title` 识别 `origin.get("kind") == "codemode"`，生成清晰标题：`bash (Auto: deletes files outside git; codemode)` 或 `edit (codemode)`。
   - `PiAcpAgent._permission_locks[session_id]` 确保即便脚本并发弹出多条需审批的调用，同一时刻向客户端发起的 `session/request_permission` 也严格串行排队，不会卡死单弹窗客户端。

### 6.3 残余风险与坦诚边界（对齐不变量 9c）

1. **沙盒内代码抗逃逸边界**：同 `docs/specs/2026-10-01-workflow-sandbox-design.md` §9，PEP 578 审计钩子与 `_safe_import` 是引导层/速度栏；真正对抗蓄意构造恶意字节码逃逸的边界是独立进程 + 清空环境 + OS 内核层（Windows 低完整性拒绝写普通完整性对象；Linux `RLIMIT_FSIZE=0` 阻断写入，但 `unlink`/同用户 `/proc`/网络仍在；macOS 无 `RLIMIT_AS`/`RLIMIT_NPROC`）。若用户处在高风险/不可信代码库环境，应运行在 `zypi --sandbox` / 容器 / VM 中，或在 `agent.toml` 设置 `[codemode] require_approval = true` / `enabled = false`。
2. **只读数据外泄风险**：与常规会话一致（`auto_mode_design` D8），`read`、`grep`、`find`、`ls`、`fetch_url`、`web_search` 在 `ask`/`auto` 下均可直接读取内容。在 `codemode` 中脚本可先 `read(".env")` 再作为参数拼进 `fetch_url` 或 `bash`——对此 `fetch_url` 仍保持现有声明边界，`bash` 出站网络（`curl`、`ssh` 等）则依然受 Auto 地板规则拦截。

---

## 7. Deferred Tool Loading（延迟工具加载）与 `pi-mcp` 协同设计

### 7.1 `ExtensionAPI` 与 `AgentHarness` 扩展

1. **`ToolDefinition`（`pi_agent_core/extensions/types.py`）**：
   ```python
   @dataclass
   class ToolDefinition:
       ...
       deferred: bool = False
       """When True, the tool is registered into the session and callable via
       ``invoke_tool`` / ``codemode``, but omitted from ``active_tool_names``
       until promoted via ``tool_search`` or ``set_active_tool_names``."""
   ```
2. **`ToolInfo`（`pi_agent_core/extensions/types.py`）**：
   新增只读元信息字段（均带默认值以兼容现有调用方）：
   - `parameters: dict[str, Any] = field(default_factory=dict)`（由 `BaseModel.model_json_schema()` 或原始 dict 转换提取）
   - `annotations: dict[str, Any] | None = None`
   - `deferred: bool = False`
   - `active: bool = True`
3. **`AgentHarness` 内部生命周期（`packages/pi-agent-harness/pi_agent_harness/agent_harness.py`）**：
   - 新增 `self._deferred_tool_names: set[str] = set()`。
   - `_put_extension_tool(definition)`：
     - 始终把工具加入 `self._tools[tool.name]`；
     - 若 `definition.deferred` 为 `True`（且尚未被显式激活过），记录到 `self._deferred_tool_names`，**不追加进 `self.active_tool_names`**；
     - 若 `definition.deferred` 为 `False`，保持现有行为加入 `self.active_tool_names`。
   - 新增 `activate_tools(names: list[str]) -> list[str]`（经 `HarnessBridge` 暴露给 `ExtensionAPI`）：将指定延迟工具加入 `active_tool_names`（去重保序），记录 `active_tools_change` 会话写入条目，发射 `ToolsUpdateEvent`，并刷新系统提示词缓存；返回本次新激活的工具名。
   - **会话恢复与动态重连兼容**：重连或恢复会话时，若历史 `active_tools_change` 已激活某工具，沿用现有会话恢复逻辑；否则保持延迟直到需要时再激活。

### 7.2 与 `pi-mcp` 的无缝协同（`packages/pi-mcp/`）

保持 `docs/specs/2026-10-23-mcp-extension-design.md` 的完全解耦原则：

- `pi-mcp` 与 `pi-codemode` 互不硬依赖。
- 在 `pi_mcp/settings.py` 增加 `exposure: Literal["auto", "direct", "deferred"] = "auto"`（可通过环境变量 `PI_MCP_EXPOSURE` 或由 `agent.toml` `[codemode].defer_mcp` 传递桥接提示控制）：
  - 当 `exposure == "deferred"`，或 `exposure == "auto"` 且当前会话激活了 `codemode`（`pi.get_tool` / `get_all_tools()` 中包含 `codemode` 工具且未被配置显式禁用 `defer_mcp`）时，`Manager._definition(...)` 生成 `ToolDefinition(..., deferred=True)`。
  - 若会话未安装或禁用了 `pi-codemode`，`exposure == "auto"` 自动保持 `deferred=False`，100% 保持现有直接暴露行为。
- 结合系统提示词中的 `<deferred_tools>` 紧凑概览块（见 §7.3），用户即使挂了 5 个 MCP server（上百个工具），首轮 prompt token 膨胀也仅增加约 30–50 tokens，模型可直接在 `codemode` 中用 `search_tools("...")` + `await call_tool(...)` 单轮完成检索与调用，或用 `tool_search` 激活为直接工具。

### 7.3 系统提示词注入（`packages/pi-agent-cli/pi_agent_cli/create_harness.py` & `system_prompt.py`）

在 `build_coding_agent_harness_system_prompt` 中：

- 若存在非空的延迟工具列表（`tool.name in deferred_names and tool.name not in active_set`）：
  - 按常见命名空间前缀（例如 `mcp__<server>__*` 取前两段，其余按 `"other"`）聚合计数；
  - 在系统提示词末尾（`Current working directory` 之前）附加紧凑块：
    ```xml
    <deferred_tools>
    The following tool namespaces are available on demand (schemas omitted to save context):
    - mcp__context7 (2 tools)
    - mcp__github (18 tools)
    Use `codemode` (`search_tools(query)` / `describe_tool(name)`) to discover and call them in one turn, or `tool_search` to load schemas directly.
    </deferred_tools>
    ```

---

## 8. ACP 客户端呈现与线协议兼容性

本特性**不新增任何非标 ACP 协议字段**，`docs/specs/2026-11-05-acp-extension-registry.md` 保持 X1–X8 不变：

1. **工具分类与标题（`packages/pi-agent-cli/pi_agent_cli/events.py`）**：
   - `_KIND["codemode"] = "execute"`
   - `_KIND["tool_search"] = "search"`
   - `tool_title("codemode", args)`：取 `code` 行数展示紧凑标题，如 `codemode (14 lines)`（空代码回退为 `codemode`），避免把整段脚本塞进单行标题破坏 TUI / VS Code 排版。
   - `tool_title("tool_search", args)`：展示 `tool_search <query>`。
2. **执行过程流式进度（`on_update`）**：
   - 宿主 `_CodemodeHost` 在每次内部工具开始/完成时，向最外层 `codemode` 的 `on_update` 发送节流后的紧凑步进摘要（例如每 ~150 ms 或每次工具状态变更合并刷新，限制最近 20 条，避免刷屏）：
     ```text
     ▸ read src/auth.py
     ✓ read src/auth.py (0.02s)
     ▸ bash pytest -q tests/test_auth.py
     ✓ bash pytest -q tests/test_auth.py (1.14s)
     ```
   - `zypi`、`vscode/` 与 Zed 会自动通过标准 `tool_call_update`（`status="in_progress"`）实时显示执行进度。
3. **内层权限弹窗**：
   - 当脚本内某个 `edit` 或 `bash` 在 `ask` / `auto` 下需要确认时，ACP 客户端收到标准 `session/request_permission`，标题明确标记来自 `(codemode)` 且 `raw_input` 展示实际要执行的参数，用户同意或拒绝后结果立刻回到挂起的沙盒脚本继续或触发 `ToolPermissionError`。
4. **斜杠命令通告**：
   - `/codemode` 作为标准非 passthrough `CommandDef` 注册，经 `available_commands_update` 自动出现在 `zypi` 与 `vscode/` 的 `/` 命令补全列表中。

---

## 9. 配置与命令设计（`agent.toml` 与 `/codemode`）

### 9.1 `agent.toml` 配置项（仅用户主目录 `~/.pi-python/agent.toml`）

```toml
[codemode]
enabled = true             # true | false（见 §11 D2 默认值评审项）
mode = "hybrid"            # "hybrid": 保留常用内置工具直接调用 + codemode
                           # "only": 除 codemode 与元工具外，将常规工具转为仅在 codemode 内调用
defer_mcp = true           # codemode 启用时，MCP 工具默认走 deferred 延迟加载
require_approval = false   # true: ask/auto 模式下每次执行 codemode 脚本前额外确认一次
timeout_seconds = 120      # 单次脚本墙钟上限（1..600 秒）
max_tool_calls = 100       # 单次脚本最大内部工具调用次数（1..500）
max_output_kb = 64         # 返回给模型的文本上限（4..512 KiB）
```

配置解析沿用 `packages/pi-agent-cli/pi_agent_cli/config.py` 的宽容降级规则：
- 非法值丢弃并在启动诊断 `notes` 中报告（不崩溃）；
- 若开启 `enabled = false`，扩展不注册 `codemode`（也不强制延迟 MCP 工具）；
- 项目目录下任何 `.pi-python/agent.toml` 中的 `[codemode]` 设置一律不读取。

### 9.2 `/codemode` 运行时命令

由 `pi-codemode` 注册为非 passthrough 斜杠命令（会话级生效，并通过 `pi.append_entry("pi.codemode_mode", ...)` 记录，支持 `session/load` / `session/resume` 恢复）：

| 命令 | 行为 |
|---|---|
| `/codemode` 或 `/codemode status` | 发送一条状态消息（`pi.send_message` 或 `notify`），展示当前模式（`hybrid` / `only` / `off`）、已激活工具数、延迟工具数及沙盒生效内核层 |
| `/codemode on` 或 `/codemode hybrid` | 启用 `hybrid` 模式（恢复内置核心工具直调 + `codemode` + `tool_search`） |
| `/codemode only` | 切换为纯 Codemode 模式（将其他非元工具移出 `active_tool_names`，仅保留 `codemode`（以及必要时的 `tool_search`），最大化节省工具 schema 上下文） |
| `/codemode off` | 关闭当前会话的 `codemode` 与 `tool_search`，并将已加载的延迟 MCP 工具恢复进 `active_tool_names` |

---

## 10. 测试、红队变异与跨平台验证计划

| 测试套件 | 覆盖重点 | 运行平台 |
|---|---|---|
| `pi_agent_core/tests/test_sandbox_v2.py` | 协议 v2 消息校验、AST 行号精确映射、尾表达式自动返回与显式 `return` 优先级、`_safe_import` 纯计算白名单拦截 `os`/`subprocess`/`socket` 等、无界内存/死循环/墙钟超时终止、并发取消传播 | Windows + WSL |
| `packages/pi-dynamic-workflows/tests/test_sandbox_*.py` | 验证沙盒上提至 `pi_agent_core.sandbox` 后，全部已有隔离/协议/冻结替身测试无回归 | Windows + WSL |
| `packages/pi-agent-harness/tests/test_deferred_tools_and_invoke.py` | `ToolDefinition(deferred=True)` 不进入初始 `active_tool_names`、`activate_tools` 去重激活与 `ToolsUpdateEvent`、`invoke_tool` 参数校验、`prepare_arguments` 与 `check_tool_call` 拦截 | 全平台 |
| `packages/pi-codemode/tests/test_codemode_runtime.py` | `tools.<ident>(**kw)` 与 `tools.<ident>(dict)` 双调用约定、`ToolText` 的 `str`/`json`/`details`/`images` 兼容、连字符工具名归一化与碰撞消歧、`search_tools`/`describe_tool`、错误恢复（带签名提示的 `ToolError`）、`parallel` 有界并发、`image()` 透传、`max_tool_calls` 与 `max_output_bytes` 截断、禁止递归调用 `codemode`/`workflow`/`tool_search`、`/codemode [on\|only\|off\|status]` | 全平台 |
| `packages/pi-mcp/tests/test_mcp_deferred.py` | `codemode` 存在时 MCP 工具默认 `deferred=True`；`codemode` 禁用或 `direct` 模式下 `deferred=False` | 全平台 |
| `packages/pi-agent-cli/tests/test_codemode_auto_and_acp.py` | **重点红队与安全闭环测试**：<br>1. **消除运行期值盲区**：脚本内动态拼接路径 `p = ".github/" + "workflows/ci.yml"; await tools.write(path=p, ...)` 或读取后拼接项目外路径在 Auto 模式下**必被地板规则拦截为 ASK**；<br>2. **Auto 串行门控**：脚本 `asyncio.gather(tools.write(...), tools.bash(...))` 在 Auto 模式下严格按 `decide → execute → decide → execute` 串行；<br>3. **`BashLimitRegistry` 联动**：内层 `tools.bash` 放行后准确消费 `bash_timeout_seconds` / `bash_max_output_mb`；<br>4. **ACP 端到端**：stdio 进程跑通 `codemode` 进度更新、内层弹窗放行/拒绝与 `session/cancel` 取消传播 | Windows + Linux |
| `packaging/agent/smoke.py` | 在 5 平台冻结产物构建后验证 `zycode-agent` 可正常启动共享 `pi-sandbox-child` 完成一次 `codemode` 调用 | Linux/macOS/Windows CI |

---

## 11. 关键架构决策表（待评审确认）

在进入编码实施前，请审阅确认以下 8 项核心设计决策（推荐方案已列出）：

| ID | 决策议题 | 推荐方案 | 备选方案 | 推荐理由 |
|---|---|---|---|---|
| **D1** | 脚本语言与沙盒引擎 | **Python + 共享 `pi_agent_core.sandbox`（`pi-sandbox-child`）** | JS/QuickJS（`python-quickjs-ng`）或 `pydantic-monty` | 与仓库全 Python 工具生态一致；复用已验证的 5 平台内核/低完整性/审计钩子沙盒；零新增第三方原生轮子与打包体积 |
| **D2** | 默认启用策略 | **默认启用（`enabled = true, mode = "hybrid"`）** | 默认关闭（`enabled = false`，opt-in） | 开箱即用对齐 Pi 1.0 体验，同时保留传统工具直调避免弱模型不适应；若倾向极度保守可改为默认 `false` |
| **D3** | 外层 `codemode` 权限声明 | **默认 `require_approval = false`（外层免问，内层逐条门控）** | 默认 `require_approval = true`（每次运行脚本先弹外层确认） | 保证只读多步编排在 `ask`/`auto` 下零打扰（Rule A0）；真实副作用均由内层 `invoke_tool` 把关；提供配置供高敏用户一键开启外层确认 |
| **D4** | Auto 模式审查机制 | **运行期对已求值实参逐条同步审查 + 内层非只读调用串行锁** | 学 Claude Code 执行前对源码跑静态模型分类器 | 从架构上彻底根除 Claude Code 的「运行期动态值盲区」，完全复用现有规则地板、模型裁判与 `BashLimitRegistry`（不变量 9b、9d） |
| **D5** | MCP 工具延迟暴露 | **`codemode` 启用时默认 `defer_mcp = true`** | 默认全量直接暴露 | 解决多 MCP server schema 撑爆上下文痛点，配合 `search_tools` / `tool_search` 按需发现 |
| **D6** | 跨轮次状态模型 | **单次无状态调用（每次干净函数体）** | 跨轮次持久化内存 REPL 会话 | 与会话 JSONL 树 `navigate_tree` / `session/resume` / 重放完全一致，不引入隐式脏状态 |
| **D7** | 后台异步执行 | **v1 不支持后台脱钩 REPL** | 允许 Auto 下转后台运行 2 小时 | 避免破坏 Auto 单会话状态连续性假设（R4）；长耗时任务由已有 `workflow` 与 `/goal` 覆盖 |
| **D8** | 模块组织形态 | **独立工作区扩展包 `packages/pi-codemode` + `pi_agent_core.sandbox` 共享内核** | 直接把 `codemode` 硬编码进 `pi_agent_harness` | 延续 `pi-dynamic-workflows` 与 `pi-mcp` 的扩展解耦风格，便于独立测试、开关和未来维护 |

---

## 12. 分阶段 PR 落地路线图

评审确认 §11 决策后，建议拆分为 4 个边界清晰的 PR 依次落地：

1. **PR 1：共享沙盒上提与协议 v2**
   - 将 `pi_dynamic_workflows/sandbox` 上提为 `pi_agent_core/sandbox`（协议 v2），保留 `pi_dynamic_workflows/sandbox` 薄重导出层；
   - 更新 `packaging/agent/build.py` 与 `zycode-agent.spec` 的 `CHILD_SCRIPT` 与冻结路径；
   - 新增 `pi_agent_core/tests/test_sandbox_v2.py`，并在 Windows + WSL 运行全量沙盒回归。
2. **PR 2：Harness 延迟工具与受控内部调用原语**
   - `ToolDefinition.deferred`、`ToolInfo` 扩展、`ExtensionAPI.invoke_tool` / `activate_tools`、`HarnessBridge` 与 `AgentHarness` 实现；
   - 新增 `packages/pi-agent-harness/tests/test_deferred_tools_and_invoke.py`。
3. **PR 3：`packages/pi-codemode` 核心包与 `pi-mcp` 协同**
   - 搭建 `packages/pi-codemode/`（`codemode` 工具、`tool_search` 工具、签名桩生成、双调用约定、`ToolText`、`parallel`、`/codemode` 命令）；
   - `packages/pi-mcp/` 增加延迟暴露协同与测试；
   - 更新根 `pyproject.toml` workspace 成员与 `AGENTS.md` / `zycode-agent.spec`。
4. **PR 4：CLI 配置、Auto 模式安全闭环、打包冒烟与文档**
   - `packages/pi-agent-cli` 新增 `[codemode]` 用户配置、系统提示词 `<deferred_tools>` 渲染、`events.py` 标题/类型、`auto_mode.py` 与 `permissions.py` 对 `origin.kind == "codemode"` 适配与串行锁；
   - 新增红队/ACP 端到端测试 `test_codemode_auto_and_acp.py` 与 `packaging/agent/smoke.py` 验证；
   - 更新 `AGENTS.md`、`README.md` 与 `docs/DESIGN.md` 索引。
