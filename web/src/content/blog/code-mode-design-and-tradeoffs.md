---
title: "为 zycode 设计 Code Mode：对比 pi 与 Claude Code，以及我们的取舍"
description: "拆解上游 pi 1.0 与 Claude Code 在 Code Mode 上的实现路径和安全盲区，记录 zycode 1.1.0 引入 pi-codemode-py 时的八项架构决策与边界取舍。"
date: 2026-11-25
lang: zh
tags:
  - code-mode
  - architecture
  - security
  - mcp
translation: code-mode-design-and-tradeoffs-en
---

在 `zycode` 1.1.0 中，我们引入了独立扩展包 `pi-codemode-py`（`codemode`、`tool_search` 与 `/codemode` 命令），用来解决编程智能体在复杂仓库与多 MCP 场景下的两类老问题：

1. **多轮串行工具调用的延迟与上下文膨胀**：传统循环里，模型读几个文件、跑几次搜索、再对结果做过滤聚合，往往要走三五轮 LLM 往返，而且中间大段原始输出会永久留在对话上下文里。
2. **大规模 MCP 工具 Schema 的固定 Token 税**：如果会话挂载了多个 MCP server、几十上百个工具，把所有完整的 JSON Schema 每一轮都塞进请求体，上下文还没开始干活就已经烧掉数千 tokens。

动手实现之前，我们先系统调研了上游 `pi` 1.0（`zycode` 的核心循环移植自 `pi`，但本作是非官方独立项目，与 `pi` 项目无隶属关系）与 Claude Code 的 Code Mode，并对照了 Python 生态里的 `pydantic-monty` 和 `python-quickjs-ng`。这篇文章分两部分记录调研结论与 `zycode` 的取舍——完整设计规格见仓库中的 [`docs/specs/2026-11-24-code-mode-design.md`](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-11-24-code-mode-design.md)。

---

## 一、对比 `pi` 1.0 与 Claude Code

两家解决的是同一类问题（让模型在单轮内写一段短脚本程序化编排工具），但在运行时形态、延迟加载机制和安全审查时机上走出了明显不同的路。

### 1. 上游 `pi` 1.0：`@earendil-works/pi-codemode` + `tool_search`

上游 `pi` 在 1.0 版本反转了早期“不内置 MCP”的立场，把 **Codemode** 直接做成工具编排与 MCP 承载底座。根据 `pi` 1.0 的发布说明，引入精简版 Codemode 与延迟工具暴露后，复杂场景下 prompt tokens 约有 **40% 的降幅**（例如从约 5,300 tokens 降至约 3,300 tokens）。

其核心设计非常克制：

- **单工具入口**：暴露 `codemode` 工具，接收裸 JS/TS 异步函数体 `code: string`，支持顶层 `await` 与 `return`。
- **强隔离的轻沙盒**：在 Worker 线程内运行编译为 WebAssembly 的 QuickJS VM——没有 Node.js API、没有文件系统、没有网络栈，脚本只能通过宿主注入的桥访问外部能力。
- **脚本内工具发现**：宿主注入归一化后的 `tools.<normalized_name>(args)`（如把连字符替换为 `_`）、`ALL_TOOLS` 元数据表、`searchTools()` / `describeTool()`，以及 `text()`、`image()`、`console.log()`（日志汇总到 `<console_output>`）。
- **延迟加载闭环**：Deferred 暴露的 MCP server 在初始提示词中只保留摘要，模型既可以在 `codemode` 脚本里直接检索并调用，也可以通过伴生工具 `tool_search` 按需把工具激活到主循环。

### 2. Claude Code：`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1` 与 PTC

Claude Code 自 v2.1.323 起通过实验开关 `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1` 提供对应的本地 Code Mode（Anthropic API 侧的 Programmatic Tool Calling 则对应服务端 Python 容器工具 `code_execution_20260120` 配合 `allowed_callers` 白名单）：

- **配置信任边界清晰**：仅允许通过环境变量或用户级 `~/.claude/settings.json` 开启，**明确忽略项目级配置文件**，避免不可信仓库擅自放开代码执行能力。
- **`REPL` 工具与 JS 原语**：模型通过内部 `REPL` 工具传入 JS 代码，使用 `$` 模板标签调用 shell（支持并发扇出与本地文本过滤）、`edit` 等辅助函数改文件；**最后一条表达式的求值结果**自动作为返回值送回模型。
- **长耗时后台运行**：前台脚本超时上限 10 分钟；在 `auto` 或 `bypassPermissions` 模式下，脚本可以转入后台运行最长 2 小时。
- **关键安全教训——「运行期动态值盲区」（Runtime Value Blindspot）**：
  - 在 Claude Code 里，脚本内发出的单次操作仍会经过本地 `PreToolUse` 钩子与常规 allow/ask/deny 规则；
  - 但在 **Auto 模式**下，安全分类器是在**脚本执行前**对整段生成的 JS 源码做静态审查，而不是在脚本运行期逐条审查求值后的真实参数。
  - 一旦脚本先运行搜索再动态拼出路径或命令（例如先跑 `rg` 取回文件列表，再在循环里把变量拼成路径传给 `edit` 或 `$`），执行前的静态分类器根本看不到运行期才会算出来的真实目标，直接形成高风险的审查盲区。

### 3. 横向对比一览

我们同时也评估了 Python 生态里的两个常见选项：`pydantic-monty`（Rust 写的受限 Python 子集解释器）与 `python-quickjs-ng`（Python 嵌 QuickJS-NG C 扩展）。四者与 `zycode` 最终选型的对比如下：

| 维度 | 上游 `pi` 1.0 | Claude Code | `pydantic-monty` / `quickjs-ng` | `zycode` 1.1.0（`pi-codemode-py`） |
| --- | --- | --- | --- | --- |
| **脚本语言** | JS / TS | JS（`REPL`） | Python 受限子集 / JS | **标准 Python 3.11+（`async def` 包装 + 尾表达式捕获）** |
| **沙盒机制** | QuickJS WASM + Worker | 本地 JS 执行环境 | Rust 解释器池 / C 扩展 | **共享 `pi_agent_core.sandbox` 多平台子进程沙盒** |
| **延迟加载与 MCP** | `codemode` + `tool_search` + `deferred` | 无内置对应 deferred 机制 | 需外层框架自行封装 | **`ToolDefinition.deferred` + `tool_search` + `pi-mcp` 协同** |
| **Auto 审查时机** | 宿主权限回调 | **执行前静态审源码（有运行期值盲区）** | 框架自定义 | **运行期对已求值实参逐条同步审查 + 串行闭环** |
| **跨轮状态** | 无状态 | 偏无状态 | 可选会话状态 | **无状态（单次干净执行）** |
| **后台执行** | 非核心路径 | Auto/bypass 下最长 2 小时 | 无内置 | **v1 不支持（长任务走 `workflow` / `/goal`）** |
| **依赖与打包代价** | Node 原生链 | Node / Bun | 新增 5 平台 Rust/C 原生轮子 | **零新增第三方依赖，复用单一 `pi-sandbox-child`** |

---

## 二、`zycode` 的决策与取舍

结合 `zycode` 既有的不变量（事件契约、按声明授权、项目信任、Auto 失败关闭与串行门控，以及 5 平台 PyInstaller 单文件打包约束），我们落地了 8 项核心取舍（对应设计文档 D1–D8）。

### 1. 脚本语言与沙盒：为什么选标准 Python + 共享子进程，而不是 QuickJS 或 `pydantic-monty`

`zycode` 的工具、扩展和主循环全部是 Python。如果为了抄作业而塞入 `python-quickjs-ng`，不仅强迫模型在 Python 项目里切回 JS 写编排，还绕开了模型最顺手的 `re`、`json`、`difflib`、`ast` 等纯计算标准库，更会在 C 扩展同步 `eval` 与 `asyncio` 异步工具桥接上引入高复杂度和 5 平台原生轮子负担。

`pydantic-monty` 的冷启动很快，但它只支持 Python 语法和标准库的受限子集——模型在编排脚本里随手写一个稍复杂的 `re` 特性或 `difflib`、`urllib.parse`，就会多浪费一轮报错重试，而且还要给 `zycode-agent` 单文件产物新增一套 Rust 运行时分发。

我们的取舍是：**把 `pi-dynamic-workflows` 里已经过 Windows、WSL 和 macOS CI 检验的多平台子进程沙盒上提为 `pi_agent_core.sandbox`（协议 v2）**，让 `workflow` 和 `codemode` 共用同一个纯标准库子程序 `pi-sandbox-child`：

- 独立进程（`-I -S -B -X utf8`）、清空环境变量、空临时工作目录；
- OS 内核层限制（Linux `rlimit` / `PR_SET_NO_NEW_PRIVS`，Windows Job Object `ActiveProcessLimit=1` + 低完整性令牌）；
- PEP 578 审计钩子 + `_safe_import` 纯计算标准库白名单（导入 `os` / `subprocess` / `socket` 直接报错并提示改用 `await tools.*`）；
- AST 包装为 `async def __codemode_main__()` 并保留原行号映射，同时借鉴 Claude Code 习惯支持**尾表达式自动捕获**；
- 宿主代理同时兼容关键字参数 `await tools.read(path="a.py")` 与 JS 风格字典参数 `await tools.read({"path": "a.py"})`，返回继承自 `str` 的 `ToolText`（既可直接 `json.loads(res)`，也可读 `.details` 与 `.images`），尽量减少无谓重试。

这里我们也必须坦诚边界：沿用仓库对沙盒的一贯口径，**PEP 578 审计钩子和 `_safe_import` 只是速度栏，不是容器级安全边界**；Linux 下 `unlink`、同用户 `/proc` 和 socket 仍存在，而 macOS 内核不强制执行 `RLIMIT_AS` 与 `RLIMIT_NPROC`。面对不可信代码库，真正边界仍然是 `zypi --sandbox`、容器或虚拟机。

### 2. 彻底根除「运行期值盲区」与双层权限收紧

这是整个设计里最核心的安全决策。

在 `zycode` 中，脚本内部每一次 `await tools.<name>(args)` 到达宿主时，所有循环、条件、字符串拼接和解析**都已经在沙盒内求值成了具体字面量**。宿主通过 `AgentHarness.invoke_tool` 带着 `origin={"kind": "codemode", "cwd": ..., "parent_call_id": ...}` 把这组真实参数送进完整的校验与权限链：

```text
沙盒脚本求值出真实参数 (JSON)
  → AgentHarness.invoke_tool(origin="codemode")
  → 参数校验（无效参数直接抛带签名的 ToolError，不无谓弹窗）
  → check_tool_call → 扩展钩子 & ask / AutoSession.decide(实参!)
  → tool.execute（内层 bash 自动消费 BashLimitRegistry 超时与输出限额）
```

即便脚本写成 `p = ".github/" + "workflows/ci.yml"; await tools.write(path=p, ...)`，真正到达 Auto 地板规则、模型裁判和 ACP 确认弹窗的永远是完整路径 `.github/workflows/ci.yml`——**运行期动态值盲区在架构层被消除**。

此外还有两处容易忽略但非常关键的细节：

1. **Auto 下的内层串行门控（`_GateLock`）**：`AGENTS.md` 不变量 9d 规定，Auto 开启时必须严格 `decide → execute → decide → execute`，避免脚本用 `parallel(...)` 同时发 `write` 和 `bash` 时，后一个调用在旧文件状态下并发通过审查。我们在 `runtime.py` 用写者优先的异步读写锁 `_GateLock` 守住这条不变量：涉及变更或处于串行策略时严格串行门控，无写入等待时只读工具仍可并发。
2. **外层默认 `require_approval = true`（PR #147 收紧）**：最初草案曾讨论过“外层免问、只靠内层逐条门控”来换取纯只读脚本零弹窗；但在 PR #147 评审后，我们决定**默认收紧为 `require_approval = true`**——因为 `codemode` 运行在与 `workflow` 相同的子进程沙盒里，遵循不变量 8（不宣称无害即询问）更诚实也更稳妥。已经运行在容器、VM 或 `zypi --sandbox` 里的用户，可以在用户主目录 `~/.pi-python/agent.toml`（项目内配置一律忽略）中显式设置：

```toml
[codemode]
enabled = true
mode = "hybrid"
defer_mcp = true
require_approval = false
```

### 3. 默认 `hybrid` 模式与 `pi-mcp` 解耦协同

我们没有一上来就强制所有会话进入纯 Codemode，而是把默认模式设为 **`hybrid`**（`enabled = true, mode = "hybrid", defer_mcp = true`）：

- 7 个内置编程工具（`read`/`edit`/`write`/`bash`/`grep`/`find`/`ls`）保持直接可调，简单单步操作无需包一层脚本，也能照顾代码编排能力较弱的模型；
- 多步批处理、大输出过滤和 MCP 调用交给 `codemode`；
- 如果希望最大化压缩工具 Schema 上下文，随时可以通过 `/codemode only` 切到纯 Codemode 模式，或用 `/codemode off` 在当前会话关闭并恢复全部 MCP 工具直调。

在架构解耦上，`pi-agent-core`、`pi-agent-harness` 与 `pi-agent-cli` 依然**不内置 MCP 客户端**。Core/Harness 只提供通用的 `ToolDefinition.deferred`、`activate_tools` / `deactivate_tools` 与 `invoke_tool` 原语；`pi-mcp-py` 与 `pi-codemode-py` 互不硬依赖，只在会话检测到 `codemode` 启用且 `defer_mcp = true` 时（可通过 `PI_MCP_EXPOSURE=auto|direct|deferred` 覆盖），自动将 MCP 工具以 `deferred=True` 注册，系统提示词末尾只注入紧凑的 `<deferred_tools>` 命名空间计数块。

### 4. 有意不做的三件事

好架构往往取决于明确拒绝了什么：

1. **不做跨轮次持久化内存 REPL（D6）**：每次 `codemode` 调用都是独立、干净的脚本执行。如果跨轮次在内存里留变量，一旦用户做会话分支跳转（`navigate_tree`）、压缩或 `session/resume`，隐式内存状态就会和 JSONL 会话历史脱节。
2. **v1 不做后台脱钩执行（D7）**：让一段脚本脱离当前 turn 在后台跑 2 小时并持续改写工作区文件，会直接打破 Auto 模式的单会话顺序假设。长耗时、多子智能体协作任务本来就有 `pi-dynamic-workflows`（`workflow`）和 `/goal` 承担，两者共享底层沙盒内核但上层职责正交：`codemode` 负责单回合内 0 额外 LLM 开销的确定性工具编排，`workflow` 负责多子智能体长链路与独立 worktree 隔离；同时脚本内明令禁止递归调用 `codemode`、`workflow` 或 `tool_search`。
3. **零新增非标 ACP 协议字段**：执行进度走标准 `tool_call_update`（150 ms 节流汇总最近 20 条步骤），内层确认走标准 `session/request_permission`（标题标注 `(codemode)` 并由会话锁串行排队避免单弹窗客户端卡死），`/codemode` 命令走标准 `available_commands_update`。`zypi`、VS Code 扩展和 Zed 不需要任何协议补丁即可直接使用。

---

## 小结

回看整个 1.1.0 的 Code Mode 落地，我们从上游 `pi` 1.0 学到了“用延迟工具加载 + 脚本内发现解决上下文膨胀”的简洁模型，从 Claude Code 看到了“忽略项目级配置”的信任边界以及“执行前静态审查”的运行期盲区教训，再结合 `zycode` 自身的 Python 栈、共享沙盒、Auto 串行门控与标准 ACP 约束，做出了偏保守、可审计、零额外外部依赖的实现。

如果你感兴趣，可以直接阅读相关源码与文档：

- 设计规格：[`docs/specs/2026-11-24-code-mode-design.md`](https://github.com/zy1233/pi-python/blob/main/docs/specs/2026-11-24-code-mode-design.md)
- 扩展实现：[`packages/pi-codemode/`](https://github.com/zy1233/pi-python/tree/main/packages/pi-codemode)
- 共享沙盒内核：[`pi_agent_core/sandbox/`](https://github.com/zy1233/pi-python/tree/main/pi_agent_core/sandbox)
