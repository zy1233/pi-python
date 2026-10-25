# MCP 扩展 `pi-mcp` — 设计方案

> Scope：让 ACP agent 连接**客户端**（编辑器）在 `session/new`、`session/load`、`session/resume` 里传来的 MCP server，并把这些 server 的工具交给模型。
> 决定：2026-10-23，用户选择「做可选扩展包，连接客户端传来的 server；core 和 CLI 仍不内置 MCP」。
> 修订：Phase 6 规格 §3「MCP：不集成」。立场保留，只加一条通路（§1.3）。
>
> 代码：挂钩在 `pi_agent_core/extensions/`、`packages/pi-agent-harness/`、`packages/pi-agent-cli/pi_agent_cli/{agent,client_mcp,mcp_notice,events}.py`；扩展包在 `packages/pi-mcp/pi_mcp/`。
> 测试：`pi_agent_core/tests/test_client_mcp_hooks.py`、`packages/pi-agent-harness/tests/test_client_mcp_harness.py`、`packages/pi-agent-cli/tests/test_client_mcp.py`、`packages/pi-mcp/tests/`。
> 库文档：`langchain-mcp-adapters` 0.3.x、MCP Python SDK 1.30（均经 Context7 核对）。

---

## 1. 背景与决定

### 1.1 为什么现在做

Zed 把用户配置的 MCP server（例如 Zed 扩展提供的 `mcp-server-context7`）随 `session/new` 传给 agent。agent 目前丢掉它们，并发一条通知：「This agent does not connect to MCP servers …」。用户要用这些工具。ACP v1 也写明：agent MUST 支持 stdio server。

### 1.2 历史

| 日期 | 事件 | 结果 |
|------|------|------|
| 2026-07-03 | Phase 3.5 | 不内置 MCP 客户端。MCP 工具经 `langchain-mcp-adapters` → `BaseTool` → `from_langchain_tool()` 进入循环 |
| 2026-08-25 | `730fa18` | `mcp_servers` 只留在方法签名里，值被丢弃 |
| 2026-09-24 | Phase 6（用户决定，对齐上游「No MCP」） | core 与 CLI 不连接 server，不发现 `mcp.json`，TUI 无 `/mcps`；MCP 若要进来，写扩展，在 `activate()` 里转换后 `register_tool` |
| 2026-10-06 | PR #7 | Rust 运行时删除，TUI 的 MCP 页面随之删除 |
| 2026-10-10 | `7becb49` | `zypi` 不再转发 `.mcp.json` 等文件里的 server |
| 2026-10-11 | r9 / PR #10 | 加「server 被忽略」通知。这是我的默认选择，没有问过用户 |
| 2026-10-23 | 本文 | 用户选择扩展包 |

「Phase 4 non-goal」这个说法不准确：Phase 4 规格没有讨论 MCP。真正的出处是 Phase 6（§1.2 第 3 行）。§9 列出要改的文字。

### 1.3 立场：什么不变，什么变

**不变**

- core、harness、CLI 里没有 MCP **客户端**代码：不开 transport，不管 server 进程，不收发 MCP 消息。
- 不读 `mcp.json`，没有 `[mcp]` 配置表，TUI 没有 MCP 页面。
- `langchain-mcp-adapters` 和 `mcp` 不是 core、harness、CLI 的依赖。

**变**

1. core 多认识一种**数据**：`ClientMcpServer`，即客户端传来的一条 server 描述。它只是数据，没有行为。
2. 扩展 API 多五个入口（§4.2）。其中三个是通用的，与 MCP 无关。
3. CLI 把 `mcp_servers` 转成 `ClientMcpServer` 交给 harness。只有**没有扩展认领**的 server 才触发「被忽略」通知。
4. 新包 `pi-mcp-py`（导入名 `pi_mcp`），可选安装。

---

## 2. 目标与非目标

**目标**

1. 装了 `pi-mcp-py` 后，客户端传来的 stdio、http、sse server 的工具出现在会话里，模型可以调用。
2. 没装时，行为与今天相同，只是通知多一句怎么装。
3. 一个 server 起不来、卡住或中途退出，不影响会话，也不影响其他 server。用户看到一句清楚的话。
4. 会话关闭时，所有 server 进程都结束，Windows 和 Linux 都一样。
5. 密钥（`env`、`headers`、`args`、`url`）不进日志、通知和错误文字。

**非目标**

- MCP resources、prompts、sampling、roots、elicitation、OAuth、`tools/list_changed`。这些和 Phase 6 的非目标一致。
- 自己的 MCP 配置文件。server 只来自客户端。
- 同一进程里多个会话共享一个 server 连接（§10 Q4）。
- `zypi`（TUI）里配置 server。`zypi` 不传 server（§1.2，`7becb49`）。
- websocket transport。ACP 没有这种 server 类型。

---

## 3. 数据流

```
编辑器（Zed）                       agent 进程（python -m pi_agent_cli）
  settings: context_servers          ┌────────────────────────────────────────────────────┐
        │ session/new                │ PiAcpAgent                                         │
        │  mcp_servers=[...]         │  client_mcp.py: ACP 类型 → ClientMcpServer（纯数据）│
        └──────────────────────────▶ │       │ harness.set_client_mcp_servers(...)        │
                                     │       ▼                                            │
                                     │ AgentHarness（每会话一个）                          │
                                     │  load_extensions() → activate(pi)                  │
                                     │       │ pi.client_mcp_servers / pi.claim_...       │
                                     │       ▼                                            │
                                     │ pi_mcp（扩展，每会话一个 Manager）                   │
                                     │  每个 server 一个 runner 任务                       │
                                     │   transport → ClientSession → load_mcp_tools        │
                                     │   → from_langchain_tool → pi.register_tool         │
                                     │       │ stdio / http / sse                         │
                                     └───────┼────────────────────────────────────────────┘
                                             ▼
                                      MCP server（子进程或远端）
```

三条信息回到用户：

- 工具调用的结果走普通 `tool_call` / `tool_call_update`。
- 失败走 `pi.notify()` → `NoticeEvent` → 一条 agent 消息（§4.2、§5.4）。
- 状态走 `/mcp` 命令（§5.5）。

---

## 4. 通用挂钩（PR-A）

### 4.1 `ClientMcpServer`

`pi_agent_core/extensions/types.py`，从 `pi_agent_core.extensions` 导出。

```python
@dataclass(frozen=True)
class ClientMcpServer:
    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str = field(default="", repr=False)                       # stdio
    args: tuple[str, ...] = field(default=(), repr=False)              # stdio
    env: tuple[tuple[str, str], ...] = field(default=(), repr=False)   # stdio
    url: str = field(default="", repr=False)                           # http, sse
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)  # http, sse
```

- 所有字段不可变、可哈希。
- `repr` 只显示 `name` 和 `transport`。`command`、`args`、`env`、`url`、`headers` 可能含密钥，所以 `repr=False`。日志里打印整个对象是安全的。
- `name` 为空或只含空白时，CLI 给它起名 `server-<序号>`。同名的 server 加后缀 `-2`、`-3`。这样 `name` 在一次会话里唯一。

### 4.2 `ExtensionAPI` 新增

| 入口 | 含义 |
|------|------|
| `client_mcp_servers -> tuple[ClientMcpServer, ...]` | 客户端为这个会话传来的 server。`activate()` 期间已经可读 |
| `claim_client_mcp_servers(names)` | 扩展声明「这些 server 归我」。CLI 只对**没人认领**的 server 发「被忽略」通知 |
| `notify(text)` | 只给**用户**看的一行文字，模型看不到。CLI 把它发成一条 agent 消息 |
| `register_cleanup(callback)` | 公开 `HarnessBridge.register_cleanup`。`harness.close()` 时依次调用（同步或异步） |
| `add_turn_barrier(wait)` | 注册一个异步函数（或普通函数）。harness 在为每个回合建工具列表**之前** `await` 它 |

只有前两个和 MCP 有关。后三个是通用的，别的扩展也能用。

`add_turn_barrier` 的规则：

- `wait` 必须自己结束。harness 不设上限。
- `wait` 抛异常时，harness 记一条 warning（`A turn barrier of an extension failed`），回合照常进行；别的屏障不受影响。
- `wait` 在没有事可等时必须立刻返回，因为每个回合都会调用它。
- 所有屏障同时运行（`asyncio.gather`），回合等最慢的那个。
- `wait` 可能被取消（见 §4.4 第 6 条）。被取消的 `wait` 不应该杀掉它在等的后台任务：下一个回合还会再调用它。

### 4.3 `AgentHarness` 新增

| 入口 | 含义 |
|------|------|
| `set_client_mcp_servers(servers)` | 扩展加载前调用。加载后再调用抛 `AgentHarnessError("invalid_state")`，和 `set_trust_project_extensions` 一致 |
| `client_mcp_servers` | 只读属性 |
| `unclaimed_client_mcp_servers` | 没被任何扩展认领的 server |
| `add_turn_barrier(wait)` / `wait_turn_barriers()` | 见上。`_run_turn` 在 `_create_turn_state()` 之前调用 `wait_turn_barriers()`。等待期间回合被取消时，`phase` 先回到 `idle` 再抛 `CancelledError`；否则 harness 会一直停在 `turn`，没有别的代码会重置它 |
| `NoticeEvent(type="notice", text)` | `notify()` 发出的事件，订阅者收到。空白文字、非字符串、会话已关闭、没有运行中的事件循环时丢弃（最后一种记一条 warning） |

`HarnessBridge`（Protocol）同步加上对应的成员。

### 4.4 CLI 的变化

1. **转换**：`client_mcp.py` 把 ACP 的 `McpServerStdio`、`HttpMcpServer`、`SseMcpServer` 转成 `ClientMcpServer`。`env` 和 `headers` 是 `[{name, value}]`，转成元组对。不认识的类型跳过并记一条 warning（只记名字）。
2. **传递**：`new_session`、`load_session`、`resume_session`、`_reopen_blank_session` 把 `mcp_servers` 传给 `_bind_session`。`_bind_session` 在 `load_extensions()` 之前调用 `harness.set_client_mcp_servers(...)`。
3. **通知**：`_notify_ignored_mcp_servers` 改为只报 `harness.unclaimed_client_mcp_servers`，在扩展加载之后（`_deferred_session_setup` 里）调用。文字（同时修正了「1 … were」的语法，单数用 it / its，复数用 them / their）：
   - 没有 `pi-mcp` 入口点：`The client passed N MCP server(s) (a, b). This agent does not connect to MCP servers by itself, so their tools are not available in this session. To use them, install the pi-mcp-py package.`
   - 装了入口点、但没有扩展认领：`The client passed N MCP server(s) (a, b). No extension took them over, so their tools are not available in this session.`（装了的用户不需要再被叫去安装；加载失败的扩展有自己的通知。）
   - 名字只留一行可打印字符，最多列 8 个，每个最多 60 字符。日志行 `Ignoring N MCP server(s) the client passed for session <id>: <names>` 保持不变，只有名字。
4. **`NoticeEvent`**：`events.project_event` 把它映射成 `update_agent_message_text(text)`。客户端在 `session/new` 响应之前会丢弃通知（zed#60199），所以 CLI 先把它存在每会话的队列里（`_held_notices`），`_deferred_session_setup` 在 `_advertise_commands` 之后才「打开」通知（`_open_notices`）：按顺序发出存下的，之后的立刻发。打开的检查和标记之间没有 `await`，所以发送期间新来的一条也按顺序出去。`close_session`、删除会话、重新绑定都会清掉或重置这个状态；会话已关闭时，迟到的通知直接丢弃，不留在队列里。
5. **能力**：`initialize` 总是回 `McpCapabilities` 对象（线上形状不变：`{"http": false, "sse": false, "acp": false}`）。装了 `pi-mcp` 入口点（`pi_agent.extensions` 组里名为 `pi-mcp`，只查元数据，不导入，所以坏掉的扩展也算「装了」）时 `http=True, sse=True`。Zed 据此决定要不要传远程 server。ACP 说 stdio 永远允许，所以不用声明。
6. **屏障**：`prompt()` 在 `_wait_for_trust` 之后等 `harness.wait_turn_barriers()`，并和 `session/cancel` 竞争（和 `_wait_for_trust` 一样）。取消时返回 `stop_reason="cancelled"`，harness 的 `phase` 没动过（屏障在 CLI 这一层先等完，才进 `harness.prompt`）。关闭会话（`close_session`、`pi/session/delete`）和重新绑定也结束这个等待：`_drop_trust` 现在同时置位 `cancelled`。否则一个永远不结束的屏障会让 `session/prompt` 挂到进程退出。
7. **重新绑定**：`_bind_session` 发现这个 id 已有 harness 时，先把新 harness 放进 `_harnesses`，再 `close()` 旧的。`close()` 自己会中止旧回合并运行扩展的清理回调。现在旧 harness 被丢掉而不关，清理回调永远不会运行；MCP 子进程会漏。（这是已有的漏洞：Zed 的「Retry」和重复 `session/load` 都会走到。）先换后关，是为了让换的过程中到来的请求仍有 harness 可用。`close()` 抛异常时只记 warning，新会话照常。

### 4.5 兼容

| 组合 | 结果 |
|------|------|
| 新 core + 没装 `pi-mcp` | 通知变清楚一点，其余不变 |
| 新 core + `pi-mcp` | 工具可用 |
| 旧 core + 新 `pi-mcp` | `pi-mcp` 的 `activate` 读 `pi.client_mcp_servers`，旧 core 上这是 `AttributeError`。捕获它，什么也不做，记一条 warning，写明需要 `pi-agent-core-lc` 0.6.0 或更新。没有 bridge 时（`RuntimeError`）当作没有 server，仍注册 `/mcp` |

`pi-mcp-py` 的下限由 `test_extension_packaging.py` 固定为「本检出版本」。发布 0.6.0 时一起抬高。

---

## 5. 扩展包 `pi-mcp`（PR-B）

### 5.1 包

| 项 | 值 |
|----|----|
| 目录 | `packages/pi-mcp/` |
| 发行名 / 导入名 | `pi-mcp-py` / `pi_mcp` |
| 入口点 | `[project.entry-points."pi_agent.extensions"] pi-mcp = "pi_mcp:activate"` |
| 依赖 | `pi-agent-core-lc>=<检出版本>`、`langchain-mcp-adapters>=0.3.2`、`mcp>=1.30,<2` |

模块：

| 文件 | 职责 |
|------|------|
| `__init__.py` | `activate(pi)`：认领、建 `Manager`、注册钩子、命令、清理、屏障 |
| `manager.py` | `Manager`：每会话一个，持有所有 `ServerConnection` |
| `connection.py` | `ServerConnection`：一个 server 的 runner 任务、状态、重连 |
| `transport.py` | 开 stdio / http / sse 会话（§5.2、§6.2） |
| `tools.py` | 工具命名、包装、结果裁剪（§5.3） |
| `redact.py` | 从错误文字里抹掉密钥（§6.1） |
| `errors.py` | 异常类型；把 SDK 的异常组、`McpError`、`httpx` 错误变成一行原因；判断「连接断了」属于哪一类（§5.2） |
| `settings.py` | 环境变量与常量（§5.6） |

### 5.2 一个 server 的生命周期

```
activate(pi)           同步，不做 I/O：读 pi.client_mcp_servers，认领全部名字，
                       建 Manager，注册 /mcp、清理、屏障，挂 session_start。
session_start 钩子     异步，要快：每个 server 起一个 runner 任务，然后返回。
runner 任务            开 transport → ClientSession.initialize() → load_mcp_tools(session)
                       → 注册工具 → 状态 ready → 等 stop 事件 → 关 transport。
屏障                   等所有 server 变成 ready 或 failed，最多 BARRIER_WAIT 秒。
清理                   置 stop 事件，最多等 5 秒，然后取消 runner。
```

要点：

- **一个 server 一个任务。** MCP SDK 用 anyio 的 cancel scope。scope 必须在进入它的那个任务里退出。工具调用可以从别的任务发起，因为它们只经过内存流。
- **`session_start` 不能等。** 常见会话里 CLI 在 `session/new` 处理中 `await load_extensions()`。在那里等 `npx` 下载包，会让 `session/new` 卡住几秒。
- **屏障保证第一回合有工具。** 用户在 server 起来之前发第一条消息时，屏障最多等 `BARRIER_WAIT`（10 秒）。超时的 server 继续在后台起，工具在它 ready 之后注册，从**下一个回合**起生效。同时发一条通知（§5.4）。
- **状态**：`new → starting → ready`；`ready → dead`（调用或 `/mcp` 发现它没了，或 runner 任务出错）；`stopped`（被要求停下，例如重连时）；`failed`（启动失败，或重连没成功）；`closed`（会话结束）。`dead`、`stopped`、`failed` 的 server 收到调用时都会先重连。
- **重连**：下一次工具调用发现 server 不是 `ready` 时，先重连再调用。**连续**最多自动重连 3 次：一次调用拿到了答复（结果，或 server 自己返回的错误）就把计数清零，`/mcp reconnect <name>` 成功也清零。设计初稿写的是「每会话 3 次」；那样一个每天崩一次的 server 到第四次就永远连不上，所以改成「连续」。用完之后调用立刻报错，指向 `/mcp reconnect <name>`（它不受次数限制）。同时到来的几个调用只重连一次（`_generation` 对比）。重连后用新的工具表更新映射；新出现的工具补注册；消失的工具保持注册，调用时报「server 不再提供」。
- **调用已经发出时 server 死了**：不重发，这次调用失败，错误文字写明原因，下一次调用才重连。server 可能已经执行了它（一个「发邮件」工具不该发两封）。只有请求**确定没发出去**（连接已关：`ClosedResourceError`、`BrokenResourceError`、`ConnectError`、`ConnectTimeout`、`session terminated`）时才在重连后重发，且只重发一次。其余的连接错误（`CONNECTION_CLOSED`、`EndOfStream`、`RemoteProtocolError`、`ReadError`、`WriteError` 等）算「可能已执行」。
- **连接断了要让等待中的调用知道**：stdio 流结束时，SDK 会让挂起的请求收到 `McpError(CONNECTION_CLOSED)`。但传输因**取消**而结束时（例如 Streamable HTTP 的 POST 出错，错误发生在子任务里），挂起的请求得不到任何通知，会一直等到调用超时。`ServerConnection` 因此给每次启动一个 `gone` future：runner 任务结束时完成，调用和它竞争，赢了就按「可能已执行」处理（`ConnectionEnded`）。实测：HTTP server 在调用中退出，调用在约 3 秒内返回，不是等 30 秒。已知的例外：旧式 SSE 传输的 `post_writer` 把 POST 错误吞掉，这样的调用只能等到调用超时。
- **`startup_timeout`**（60 秒）：从开 transport 到 `load_mcp_tools` 结束。超时算 `failed`，进程被关掉。

### 5.3 工具

**命名**：`mcp__<server>__<tool>`。

- `<server>` 把 `[a-zA-Z0-9_-]` 以外的字符换成 `_`。
- 总长超过 64 时截断，末尾加 `_` 和 8 位哈希。多个提供商把 64 当上限，`TOOL_NAME_PATTERN` 允许 128，但会被拒。
- 重名（清洗后相同）时加 `_2`、`_3`。
- 前缀让 MCP 工具不可能覆盖内置工具。`AgentHarness._apply_extension_registrations` 对同名工具是**覆盖**，没有它这里就是漏洞（一个叫 `bash` 的 MCP 工具会顶掉内置 `bash`）。

**定义**：

- `description`、`parameters`（MCP `inputSchema`）由 `from_langchain_tool()` 取得，和 Phase 3.5 一致。
- `annotations`：来自 MCP 的四个布尔提示，按 `from_langchain_tool` 的现有规则（只取真正的布尔）。**照单全收**，理由在 §6.3。
- `label`：`<server>: <tool>`。`prompt_snippet`：无（schema 已经给了模型）。
- `execution_mode`：默认（可并行）。

**执行**（包装 `from_langchain_tool` 的 `execute`）：

1. server 不是 `ready` 时，按 §5.2 重连，或抛错。
2. 用 `asyncio.wait_for(..., call_timeout)` 限时。默认 120 秒。超时抛 `RuntimeError`，写明 server 和工具名。
3. 和 `signal.wait_aborted()` 竞争。用户取消时取消调用，抛 `RuntimeError("Operation aborted")`。
4. 结果文字超过 50 KB（和内置 `read` 的上限一致）时，保留开头并加一行说明。图片块不动。
5. 内容里没有文字、但有 `structuredContent` 时，把它序列化成 JSON 当文字。否则模型什么也看不到。
6. `details = {"mcp": {"server": ..., "tool": ...}}`。**不**保存 `structuredContent`：它和文字重复，会让会话文件变大一倍。
7. MCP 的 `isError` 结果和传输错误都变成异常。循环把它们转成 `is_error=True` 的工具结果。错误文字先过 `redact`。

### 5.4 失败与通知

| 事件 | 用户看到 |
|------|----------|
| server 启动失败 | `MCP server "<name>" did not start: <原因>. Its tools are not available in this session. Log: <路径>` |
| 超过屏障时间还没好 | `MCP server "<name>" is still starting. Its tools join in a later turn.` |
| 运行中退出 | **不发通知**。在途的调用失败，工具结果里写 `MCP server "<name>" stopped while the tool ran: <原因>. The next call tries to connect it again.`（用户在工具卡片里看到，模型也看到）。空闲时死掉的 server 要到下一次调用或 `/mcp` 才被发现，下一次调用会自动重连，成功就不说什么 |
| 重连用完 | `MCP server "<name>" stopped and cannot reconnect by itself. Use /mcp reconnect <name>.` |
| 重连没成功 | `MCP server "<name>" stopped and did not come back: <原因>. Use /mcp reconnect <name>.` |
| 同名 server | `The client passed the MCP server name "<name>" twice. Only the first is used.`（CLI 已经给重名加后缀，这是防御） |
| 一切正常 | **什么也不发** |

- 每个 server、每种事件最多发一条（`Manager._say` 按（名字，种类）去重）。
- `<原因>` 先解开 `ExceptionGroup`，取第一个叶子，过 `redact`，截到 300 字符。
- 「命令找不到」单独成句：`command not found: "<可执行文件名>". Check that it is on PATH.`。只写文件名，不写参数。
- 成功不发通知。这样没有 MCP 问题的线程保持空白，不会因为一条通知变成「有内容的线程」（见 PR #34 的 Zed 行为）。

### 5.5 `/mcp` 命令

| 命令 | 输出 |
|------|------|
| `/mcp` | 每个 server 一行：名字、transport、状态、工具数；启动失败的加原因和日志路径；`dead` 的写 `stopped: <原因>. The next tool call connects it again`。输出前先 ping 每个 `ready` 的 server（最多 3 秒），连接已经没了的标成 `dead`；只是忙（ping 超时）的不算死 |
| `/mcp reconnect <name>` | 重连这个 server，并报结果 |

输出里**不写** `command` 的参数、`env`、`url` 的路径和查询串、`headers`。stdio 只写可执行文件名，http 和 sse 只写主机名。

### 5.6 配置

只有环境变量，没有配置文件（Phase 6 决定）：

| 变量 | 默认 | 含义 |
|------|------|------|
| `PI_MCP_STARTUP_TIMEOUT` | 60 | 一个 server 启动的上限（秒） |
| `PI_MCP_CALL_TIMEOUT` | 120 | 一次工具调用的上限（秒） |

值不是正数时用默认值并记一条 warning。屏障等待（10 秒）、输出上限（50 KB）、自动重连次数（3）、关闭宽限（5 秒）是代码里的常量。

---

## 6. 安全

### 6.1 密钥

`env`、`headers`、`args`、`url` 都可能含令牌。规则：

- 日志、通知、`/mcp` 输出、错误文字里只出现 `name`、transport、可执行文件名、主机名。
- 错误文字先过 `redact`：把这个 server 的 `url`、所有 `header` 值、所有长度 ≥ 4 的 `env` 值替换成 `***`。`httpx` 的错误文字常带完整 URL，所以这一步是必要的。
- `ClientMcpServer.__repr__` 不含这些字段（§4.1）。

### 6.2 子进程环境

**不用** `langchain-mcp-adapters` 的 `create_session` 开 stdio 会话，原因有两个：

1. 它对 `env` 的每个值展开 `${VAR}`，取值来自 **agent 进程**的环境。agent 进程里有用户的 LLM 密钥。客户端传来的值不该能读它们。
2. 对没展开的 `${...}` 它把**整个值**写进 warning 日志。值就是密钥时，密钥进了日志。

所以 `transport.py` 自己调用 `mcp.client.stdio.stdio_client` 和 `ClientSession`。http 和 sse 仍用适配器的 `create_session`：它们不展开任何东西。

子进程的环境 = SDK 的默认白名单（Windows：`APPDATA`、`PATH`、`PATHEXT`、`SYSTEMROOT`、`TEMP`、`USERPROFILE` 等；POSIX：`HOME`、`LOGNAME`、`PATH`、`SHELL`、`TERM`、`USER`）+ 下表 + 客户端传来的 `env`（后者优先）。

| 追加的变量 | 为什么 |
|------------|--------|
| `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`（大小写两种） | 在代理后面，server 联网（包括 `npx` 下载）要用它们 |
| `SSL_CERT_FILE`、`SSL_CERT_DIR`、`REQUESTS_CA_BUNDLE`、`NODE_EXTRA_CA_CERTS` | 企业根证书 |
| `LANG`、`LC_ALL`、`TZ` | 语言与时区 |
| Windows：`COMSPEC`、`PROGRAMFILES`、`PROGRAMFILES(X86)`、`PROGRAMDATA`、`TMP`、`ALLUSERSPROFILE` | `npx.cmd` 和 Node 要用 |

其余环境变量（尤其是 LLM 密钥）**不会**传给 server。

`cwd` 是会话的工作目录（`pi.cwd`）。

### 6.3 谁来担保这些 server

- server 是用户在**编辑器**里配置的，agent 不另外问。Zed 的全局 server 来自用户设置。项目里的 `.zed/settings.json` 在 Restricted Mode 下不会被启动，用户信任工作树之后才会（Zed 文档「Zed and trusted worktrees」）。所以编辑器已经问过一次，agent 不再问第二次。
- 这与不变量 9（项目信任）不矛盾：不变量 9 管**项目目录里的文件**（扩展、saved workflow、`.pi/SYSTEM.md`、skills）。MCP server 来自编辑器的设置，由编辑器自己的信任机制把关。
- MCP 规范要求客户端把工具 annotations 当作不可信，除非 server 可信。这里 server 是用户选的，stdio server 本来就是用户权限下的任意代码，所以 annotations 照单全收。提示只影响 `ask` 模式要不要问，而 server 能做的事不因提示而变。若以后要改，在 §10 Q3。
- `ask` 模式照常：没有 `readOnlyHint` 等声明的 MCP 工具，每次调用都问（不变量 8）。

### 6.4 提示注入

工具描述和工具结果会进模型的上下文。恶意 server 可以借此影响模型。这和所有第三方工具一样，本设计不加额外防护。用户选择 server，权限层问用户。

### 6.5 远程 server

- 用 `httpx` 的默认 TLS 校验。不提供关闭校验的开关。
- `headers` 的值按密钥处理（§6.1）。
- 地址在本机（`localhost`、`127.0.0.1`、`::1`）的 server 用一个**不读系统代理**的 `httpx` 客户端（`trust_env=False`，超时与 SDK 默认相同，不跟随重定向）。原因：`httpx` 会读环境变量，以及 Windows 和 macOS 的系统代理设置，而且不像别的 HTTP 客户端那样绕过回环地址，本机的 MCP server 会被送到用户的代理去（在注册表里设了系统代理的 Windows 机器上就是这样）。测试 `test_a_server_on_this_machine_is_not_reached_through_a_proxy` 把代理环境变量指向一个打不通的地址，只有直连客户端能通过。远程 server 仍按系统设置走代理。
- 需要 OAuth 的远程 server 不在范围内。Zed 对没有 `Authorization` 头的远程 server 走自己的 OAuth 流程，是否把令牌作为 `headers` 传给 agent，我没有核实；若不传，这类 server 在这里会得到 401，通知里会写出来。

---

## 7. 平台

| 项 | Windows | Linux / WSL |
|----|---------|-------------|
| 命令解析 | SDK 的 `get_windows_executable_command` 把 `npx` 找成 `npx.cmd` | 直接 `PATH` 查找 |
| 进程树 | 每个 server 进程放进 Job Object（`KILL_ON_JOB_CLOSE`，靠 `pywin32`，`mcp` 在 win32 上自带） | 新会话 + `killpg`（SDK） |
| 事件循环 | 必须是 `ProactorEventLoop`（Python 默认）。`SelectorEventLoop` 不支持子进程，SDK 会退到 `Popen` | 默认 |
| stderr | 每个 stdio server 一个日志文件 `<home>/logs/mcp/<name>.log`。追加写（两个会话可以跑同一个 server），每次启动先写一行 `--- <时间> start ---`，文件超过 512 KB 时这次从头写。写不了文件时输出丢弃 | 同，另外权限 `0600`（server 可能把拿到的令牌打出来） |

stderr 写文件，不继承 agent 的 stderr：话多的 server 会塞满 Zed 读的管道，agent 就会卡住。

两个平台的测试都要跑（工作区规则）。

---

## 8. 测试

**PR-A**（不需要 `mcp`）

- core：`ClientMcpServer` 的 `repr` 不含密钥；`ExtensionAPI` 五个入口；没有 bridge 时的错误。
- harness：`set_client_mcp_servers` 在加载后抛错；`unclaimed_client_mcp_servers` 随认领变化；`notify` 发出 `NoticeEvent`；屏障在 `_create_turn_state` 之前运行，抛异常时回合照常。
- CLI：ACP → `ClientMcpServer` 的转换（三种类型、空名字、重名）；`new_session`、`load_session`、`resume_session`、重开空白会话都把 server 交给 harness；未认领才通知；`NoticeEvent` 在响应后才发；重新绑定会关旧 harness；`initialize` 的能力；屏障与 `session/cancel` 竞争。

**PR-B**

用 `mcp.server.fastmcp.FastMCP` 写一个测试 server 脚本（`tests/mock_server.py`，用 `sys.executable` 启动）。工具：`echo`、`add`、`slow`、`fail`（`isError`）、`big`（大输出）、`readonly`（带 `readOnlyHint`）、`env_names`（返回环境变量名）、`env_value`（返回某个变量的值）、`header`（返回收到的请求头）、`pid`、`cwd`、`crash`（进程退出）、`structured`（只有 `structuredContent`）、`image`、`noisy`（往 stderr 写）、`bash`（和内置工具重名）。5 个测试文件，共 192 个测试，全部用真实的子进程或真实的本地 HTTP server，不 mock MCP SDK：

| 文件 | 测什么 |
|------|--------|
| `test_mcp_units.py` | 不需要 server 的部分：名字、结果整形、脱敏、异常分类、子进程环境、日志文件、设置值 |
| `test_mcp_connection.py` | stdio server：注册、调用、失败的启动、运行中退出与重连、清理、环境、屏障 |
| `test_mcp_http.py` | Streamable HTTP 与 SSE、server 退出后重连、本机地址不走系统代理 |
| `test_mcp_extension.py` | `activate`（旧 core、认领、命令、清理）；真实 `AgentHarness` + 脚本化 LLM |
| `test_mcp_acp.py` | 真实 `PiAcpAgent` 走完整 ACP：`session/new` 带 server → 工具 → `ask` 模式的权限请求 → `session/close` 结束子进程 |

覆盖的内容：

- 连接并注册：名字、schema、annotations、`label`。
- 调用：文字结果；`isError`；超时；取消；大输出被裁；只有结构化内容。
- 失败：命令不存在；进程立刻退出；启动超时。别的 server 不受影响，通知只发一次。
- 运行中退出：调用失败并写明原因；下一次调用自动重连，工具只注册一次；请求已发出时不重发，没发出去时重发一次；同时到来的几个调用只重连一次；连续重连到上限后报错，调用成功或 `/mcp reconnect` 把次数还回来；等待中的调用在连接断掉时立刻返回，不等超时。
- 清理：`harness.close()` 之后子进程不在了（检查 PID）。
- 环境：agent 里的密钥变量在 server 里不可见；客户端 `env` 里的 `${HOME}` **不**被展开；代理变量会传过去。
- 脱敏：令牌出现在 `url`、`headers`、`env` 值里，都不出现在通知和错误里。
- 命名：清洗、截断、重名、不覆盖内置工具。
- `/mcp` 和 `/mcp reconnect`。
- 屏障：慢 server 时第一回合等到工具；超时后回合照常。
- Streamable HTTP 与 SSE：同一个脚本以 HTTP 或 SSE 模式起一个本地 server，连、调、关；`headers` 到达 server；server 退出后重连；本机地址不走系统代理（用打不通的代理环境变量证明）。
- 端到端：`AgentHarness` 和 `PiAcpAgent` + 脚本化的 LLM 调用 MCP 工具；`ask` 模式下，没声明的工具要问，`readOnlyHint` 的不问，被拒绝的调用不到达 server。

---

## 9. 文档与发布改动清单

**PR-A**

- `packages/pi-agent-cli/AGENTS.md`「MCP servers」一节：改写，并加 `client_mcp.py`。
- 根 `AGENTS.md`：模块表加 `packages/pi-mcp/`（PR-B）；不变量里不加新条（它不是新的不变量）。
- `CHANGELOG.md`：Added / Changed / Fixed（重新绑定漏关旧 harness）。
- Phase 6 规格 §3：加修订注记，指向本文。
- 「Phase 4 non-goal」的说法：`packages/pi-agent-cli/AGENTS.md` 和 `mcp_notice.py` 的 docstring 里已改掉。`CHANGELOG.md` 的 0.5.0 一节是已发布版本的说明，不改它；Unreleased 的新条目里更正这个说法。
- Phase 7 规格：§12 末尾（「本 CLI 目前不把 `mcpServers` 接成工具」那句）加一行指向本文 §4.2。

**PR-B**

- 根 `pyproject.toml`：workspace 成员和 `testpaths`。
- `.github/workflows/ci.yml` 的安装行。**不改** `tui-ci.yml`：那里的安装行只是给 Rust 端到端测试准备一个 Python agent，`zypi` 不传 server，装了 `pi-mcp` 只会改变 `initialize` 的能力，对这些测试没有意义。`release.yml` 见下一条。`packages/pi-mcp/tests/conftest.py` 在没装 `mcp`、`langchain_mcp_adapters`、`pi_mcp` 的环境里让这些测试不被收集，并在运行头里说一句；`ci.yml` 装了包，所以这些测试在 CI 里真的会跑。
- **发布**（PR-B 开出之后，用户要求在合入前补上）：`.github/workflows/release.yml` 改两处：预发布测试 job 的安装行加 `-e "./packages/pi-mcp"`，构建 job 加一步 `Build pi-mcp`。没有单独的「发布列表」：`publish-pypi` 发布 `dist/` 里的全部文件，`github-release` 附上 `dist/*`，所以构建步骤就是发布列表。装了包之后，预发布的 `pytest -v` 也跑 `packages/pi-mcp/tests`。README 的包表和安装行、包自己的 README（它就是 PyPI 上的说明）、`CHANGELOG.md`、`pi_mcp/__init__.py` 的 docstring 里「还不在 PyPI」的说法都改成「0.6.0 起在 PyPI 上」。首次上传一个新的 PyPI 项目，要求 `PYPI_API_TOKEN` 有创建项目的权限（账号范围的令牌，或这个名字的待定发布者）；令牌的范围这里查不到，要维护者确认。`pi-mcp-py` 这个名字在 PyPI 上没有人占（2026-10-25 查：`/pypi/pi-mcp-py/json` 返回 404）。发布 0.6.0 把核心抬到 0.6.0 时，`test_extension_packaging.py` 会要求 `pi-mcp-py` 的下限同时抬到 `>=0.6.0`。
- `AGENTS.md`：模块表和「Venv recovery」的安装行。
- `README.md`：包表、「MCP」几行（现在写「MCP tools via langchain-mcp-adapters work out of the box」，仍然对，但要加一句扩展包）。
- `pi_agent_core/tests/test_extension_packaging.py`：`EXTENSIONS` 加 `pi-mcp`。

---

## 10. 默认值与未决问题

下面每一项我都选了默认值并照它实现。用户在 2026-10-25 答「按你的倾向来」：全按默认（[计划的决策 21](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md#待决策)）。要改哪一项，告诉我，改动都小。

| # | 问题 | 默认 | 备选 |
|---|------|------|------|
| Q1 | 包名 | `pi-mcp-py` / `pi_mcp` | `pi-mcp-adapter-py` |
| Q2 | 工具名 | 总是 `mcp__<server>__<tool>` | 只在重名时加前缀（名字会随 server 集合变化，不稳定） |
| Q3 | annotations | 照单全收 | 默认丢弃，让每个 MCP 工具都问；或按 server 设开关 |
| Q4 | 多会话共享 server | 不共享，每会话各起一份 | 进程内按（server 描述 + cwd）引用计数共享。Zed 原生就是这样。省内存和启动时间，代价是线程间共享 server 状态 |
| Q5 | `zypi` 里怎么配 server | 不配（Phase 6） | `~/.pi-python/mcp.toml`，由 `pi-mcp` 读；需要 TUI 先支持把它交给 agent |
| Q6 | 声明 http / sse 能力 | 装了 `pi-mcp` 才声明 | 总是声明（没装时 server 仍会被忽略并通知） |
| Q7 | 超时 | 启动 60 秒，调用 120 秒，屏障 10 秒 | 调用不限时（Claude Code 的做法），靠用户取消 |
| Q8 | 通知渠道 | 只通知失败 | 也通知「已连接 N 个工具」。会让每个线程都不再空白 |
| Q9 | 地址在本机的 http / sse server | 不走系统代理 | 照 `httpx` 默认（读系统代理，本机 server 会被送去代理） |
| Q10 | server 在空闲时死了 | 不通知；下一次调用重连；`/mcp` 看得到 | 一死就通知（可能没有人在用它，却打扰了用户） |
| Q11 | 重连次数 | 连续 3 次，调用成功后清零 | 每会话 3 次（初稿）；不限次数 |

已知的客户端问题（不是本设计能解决的）：Zed 在项目的第一个工作树加载完成之前创建的线程，会传一个**空的** `mcpServers`（zed#64611）。这样的线程里没有 MCP 工具，新开一个线程即可。
