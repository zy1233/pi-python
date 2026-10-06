# Phase 6：扩展集成 — 设计方案

> Scope: Git 工作区上下文注入、多提供商生产测试矩阵。MCP 保持上游立场：不做成一等集成。
> 状态：已实施（2026-09-24）。
> 与已落地的 [Phase 3.5 工具生态](2026-07-03-phase3.5-tool-ecosystem-design.md)（read/bash/edit/write/grep/find/ls + LangChain `BaseTool` 适配器）不是同一阶段。
>
> 上游参照：[earendil-works/pi](https://github.com/earendil-works/pi) `packages/coding-agent`。
> 上游 README 写明 **No MCP**：core 与 CLI 不连接服务器、不发现 `mcp.json`。需要 MCP 的人写扩展，或自己把工具接进来。
> pi-python 对齐这一点。MCP 服务器产出的工具若要进循环，调用方先经 `langchain-mcp-adapters` 得到 `BaseTool`，再走已有的 `from_langchain_tool()`。本期不新增 MCP 模块。

---

## 1. 目标与非目标

### 1.1 目标

1. **Git 上下文注入。** 当 cwd 位于 git 工作区时，在 system prompt 中附上只读、有界的分支与工作区状态；这是**会话开始时的快照**，运行期间不随文件变化刷新（见 §4.3）。
2. **多提供商生产测试矩阵。** 在现有 `openai` / `anthropic` / `deepseek`（含 OpenAI 兼容网关）上，用同一组能力断言覆盖真实 API；缺密钥的格子跳过，不进入默认 CI。

### 1.2 非目标

- 不改 `agent_loop` 的事件契约、并行工具顺序、terminate 语义、StreamFn 不抛异常约定。
- **不把 MCP 做成一等集成。** 不新增 `pi_agent_core/mcp/`，不在 CLI 发现 `mcp.json` / `[mcp_servers]`，不管理 stdio 子进程或 HTTP 会话，不做 MCP 专用权限或 ACP kind。
- 不实现 MCP resources、prompts、sampling、roots、elicitation、OAuth。这些属于调用方选用的 MCP 客户端，不进入本仓库。
- 不在 Rust TUI 做 `/mcps` 管理界面，不实现 `x.ai/mcp/*`。
- 不做 git commit、push、checkpoint、auto-stash。
- 不新增 LLM provider 实现；矩阵只验收 `resolve_chat_model` 已有分支。
- 不修改 `from_langchain_tool()` 的映射规则。Phase 3.5 适配器已经是 MCP 工具进入循环的唯一入口。

### 1.3 已确认决策

1. **MCP 与上游一致，留在进程外。** 产品路径不连接 MCP。调用方用 `langchain-mcp-adapters` 得到 `BaseTool`，再 `from_langchain_tool()` / `from_langchain_tools()` 交给 `Agent` 或 `create_session_harness(tools=...)`。连接、重连、关进程都由调用方负责。
2. **Git 段只进 system prompt，不写入 JSONL。** 状态放这里才不会把过期 diff 固化进会话历史。注意 `AgentHarness` 会在会话内缓存 system prompt（提交 `bee053c`，为保持 provider 提示前缀缓存稳定），所以这一段实际上是快照，而不是逐 turn 刷新，见 §4.3。
3. **矩阵是 opt-in 集成测试，不是 PR 门禁。** 默认 `pytest` 保持 mock：`pyproject.toml` 的 `addopts = "-m 'not real_llm'"` 让普通 `pytest` 不收集 `real_llm` 用例（开发机 shell 里导出了 `OPENAI_API_KEY` 等变量也不会真实调用付费 API），显式 `-m real_llm` 覆盖它。被选中的用例在密钥缺失时 `skip`，不失败。（审计 P6-03：原先只靠「缺 key 就 skip」，文档却写成「默认排除」。）

---

## 2. 架构

```
调用方（扩展 / 嵌入程序，不在本期）
  langchain-mcp-adapters → BaseTool
  from_langchain_tool() → AgentTool
            │
            ▼
┌──────────────────────────────────────────────────────────────┐
│  pi_agent_cli                                                │
│  factory.create_session_harness(tools=...)                   │
│  system_prompt callable → GitContext.snapshot()              │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────┐
│  agent_loop（不改）         │
│  StreamFn = langchain_stream│◄── 提供商矩阵只测这条边界
└────────────────────────────┘
```

分层约束：

| 层 | Phase 6 放入的内容 | 不放入 |
|---|---|---|
| `pi_agent_core` | 无新模块。MCP 继续只用 Phase 3.5 的 `from_langchain_tool()` | MCP 客户端、服务器配置、会话生命周期 |
| `pi_agent_cli` | Git 段与开关 | `mcp.json`、`[mcp_servers]`、MCP 权限 |
| `pi_agent_core/tests` + `scripts/` | 提供商矩阵 | 真实 MCP 服务器 |

运行时依赖不增加。不添加 `mcp` extra，也不把 `langchain-mcp-adapters` 列为依赖。要用 MCP 的调用方自行安装那个包。

---

## 3. MCP：不集成，只走已有 BaseTool 适配器

与上游相同，MCP 不是 harness 的功能。本仓库不连接服务器。

已落地的入口在 `pi_agent_core/adapters/langchain_tools.py`：

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from pi_agent_core.adapters.langchain_tools import from_langchain_tools

client = MultiServerMCPClient({...})          # 调用方持有生命周期
lc_tools = await client.get_tools()
agent_tools = from_langchain_tools(lc_tools)  # BaseTool → AgentTool
```

`create_session_harness(..., tools=agent_tools)` 和 `Agent` 的工具列表接受这些 `AgentTool`。名字、schema、文本/图片结果、异常变 `is_error` tool result，都按 Phase 3.5 适配器的现有规则，本期不改。

调用方负责：

- 安装 `langchain-mcp-adapters`（以及它拉取的 MCP SDK）
- 读自己的服务器配置、建立连接、在 session 结束时关闭
- 工具名冲突时自己改名或挑选子集

`zypi` / `python -m pi_agent_cli` 默认不加载任何 MCP 服务器。Phase 7 扩展若要提供 MCP，应在自己的 `activate()` 里做上面的转换再 `register_tool`，而不是要求 core 识别 MCP。

权限：导入后的工具与其他自定义工具一样。`ask` 模式今天只对 `bash` / `edit` / `write` / `workflow` 弹权限（`workflow` 的子代理的每次工具调用同样逐个询问，见 Phase 7 spec §12）；MCP 工具不因为来自 MCP 而自动进入这个名单。调用方若要门禁，用已有的 `before_tool_call` / `tool_call` 钩子。

---

## 4. Git 上下文注入

### 4.1 模块

`packages/pi-agent-cli/pi_agent_cli/git_context.py`

```python
@dataclass(frozen=True)
class GitSnapshot:
    branch: str                 # 分支名；尚无提交时为 "<name> (no commits yet)"；detached 时为 "HEAD (detached at <short-sha>)"
    status_porcelain: str       # 已截断的 porcelain 正文（不含分支行）
    truncated: bool

def snapshot_git_context(cwd: str, *, timeout_seconds: float, max_lines: int) -> GitSnapshot | None:
    """非仓库、git 不在 PATH、或命令失败时返回 None。"""
```

调用（均 `git -C <cwd> -c core.quotePath=false`，不经 shell）：

1. `git rev-parse --is-inside-work-tree` — 非 `true` 则返回 `None`
2. `git rev-parse --abbrev-ref HEAD`；结果为 `HEAD` 时再 `git rev-parse --short HEAD`（detached）；命令失败时再 `git symbolic-ref --short -q HEAD`
3. `git --no-optional-locks status --porcelain=v1 -b --untracked-files=normal`（`--no-optional-locks` 是 git 全局选项，避免锁 index）

第 2 步的最后一个分支处理**尚无提交**的仓库（`git init` 之后）：HEAD 指向一个还不存在的分支，`rev-parse --abbrev-ref HEAD` 会以 128 失败，而 `symbolic-ref` 仍能给出分支名，于是 `branch = "<name> (no commits yet)"`。两者都失败才省略整段。

**超时是整个快照的总预算，不是每条命令各自的上限。** 默认 2 秒；`snapshot_git_context` 在开头取一个 `time.monotonic()` 截止时间，每条命令拿到的 `timeout` 是剩余时间，预算用完后不再启动新命令。逐条计时时，最多四条命令可以让会话等 `4 × timeout_seconds`。超时、预算用尽或非零退出返回 `None`（prompt 里不出现空段，也不出现错误堆栈）。不递归子模块，不跑 `git diff`。

**不能在事件循环上运行。** `snapshot_git_context` 是阻塞的（`subprocess.run`）；`factory.system_prompt_callback` 通过 `asyncio.to_thread(load_system_prompt_options, ...)` 调用，否则慢文件系统上的 git 会冻结整个 agent 进程（ACP 取消、权限回复都在同一个事件循环里）。

编码：git 输出固定按 UTF-8 解码（`encoding="utf-8", errors="replace"`），**不依赖系统 locale**——
中文 Windows 的 locale 是 cp936，`text=True` 会让 `功` 这类分支名在 reader 线程里抛
`UnicodeDecodeError`（`stdout=None` → `AttributeError`），`功能` 这类则静默变成乱码。
`core.quotePath=false` 让非 ASCII 路径原样输出，而不是 `"\344\270\255..."` 八进制转义（引号、反斜杠、
控制字符仍会被转义）。`_git()` 吞掉**所有**异常（含解码失败与 `stdout=None`）并返回 `None`：
这一段只是可选上下文，任何故障都不能让 turn 失败。

### 4.2 注入位置

`build_system_prompt` 在 `<project_context>` 之后、skills 之前追加：

```
<git_status>
Snapshot taken at the start of this session; it is not refreshed as files change. Run `git status` for the current state.
Branch: main
## main...origin/main
 M packages/pi-agent-cli/pi_agent_cli/factory.py
?? docs/specs/2026-09-24-phase6-extended-integrations-design.md
</git_status>
```

`status_porcelain` 保留 `git status -b` 的首行（`## …`），方便模型看到上游跟踪。正文超过 `max_lines`（默认 40）时截断并加一行 `# … truncated`。`truncated=True`。

自定义 system prompt（`custom_prompt`）同样追加该段，与 context files 的处理一致。`no_context_files` **不**关掉 Git 段；两者开关独立。

### 4.3 刷新

**这是会话级快照，不是逐 turn 刷新。** `load_system_prompt_options` 在 system prompt callable 被调用时取一次快照，而 `AgentHarness._create_turn_state` 把 callable 的结果缓存到 `_cached_system_prompt`（提交 `bee053c`）：只有 `invalidate_system_prompt_cache()`（工具集变化、动态加载扩展等）才会重算。

这是有意的取舍。provider 的提示缓存按请求前缀匹配，system prompt 在最前面：git 块一旦变化，它之后的全部内容（skills 段与整段对话历史）都会失去缓存命中。agent 每改一次文件 git 状态就变，逐 turn 重算等于每个 turn 都在为整段历史重新付费。因此块内以固定文案声明「会话开始时的快照、不随文件刷新，最新状态请运行 `git status`」，让模型不把旧状态当作当前状态。

该文案不含时间戳，保证同一会话内 system prompt 字节级稳定。若将来要做「状态变化时追加尾部消息」的新鲜度方案，需要新的注入机制并处理各 provider 对消息顺序的要求，不在本阶段范围。

### 4.4 配置

```toml
[git]
enabled = true
timeout_seconds = 2       # 整个快照的总预算（见 §4.1），不是每条命令
max_status_lines = 40
```

CLI / headless：`--no-git-context` 覆盖为关。默认开。非仓库时开关无效果。

### 4.5 隐私

`<git_status>` 是 system prompt 的一部分，会随每个请求发给模型提供商：里面有分支名，以及**已修改和未跟踪文件的路径**（`--untracked-files=normal` 会列出未跟踪的文件与目录名）。仓库里的分支名或文件名本身可能是敏感信息（客户名、未发布的特性名等），而这些名字并不在用户主动发给模型的内容里。

默认仍然开启：这一段对多数编码任务有用，把默认改成关会让每个用户都失去它。取而代之的是**把事实写清楚**：README 与 `agent.example.toml` 的 `[git]` 都说明了它会发给提供商以及如何关闭（`[git] enabled = false` 或 `--no-git-context`）。内容限于 `git status -b` 的输出（分支及其上游跟踪分支名、变更文件的路径），不含文件内容、diff、提交信息或远程 URL（不跑 `git diff` / `git log` / `git remote`）。

---

## 5. 多提供商生产测试矩阵

### 5.1 现状

`resolve_chat_model` 有三条显式分支和一条兜底：

| `Model.provider` | 实现 | 备注 |
|---|---|---|
| `openai` | `ChatOpenAI`（`stream_usage=True`） | |
| `anthropic` | `ChatAnthropic` | thinking budget 见 `_ANTHROPIC_BUDGET` |
| `deepseek` | `ChatDeepSeek` | OpenAI 兼容网关（SiliconFlow、vLLM）必须走这条，才能保留 `reasoning_content`。DeepSeek 官方 API 的思考开关见 §5.4 |
| 其他 | `init_chat_model` | 需要额外安装 `langchain`，不纳入本期矩阵 |

现有 `pi_agent_core/tests/test_real_llm.py` 只打 SiliconFlow DeepSeek，且标记为 `real_llm`。默认 `pytest`（包括 CI 的 `pytest -v`）不收集它们（`addopts`，见 §1.3），需要时用 `-m real_llm` 选中；选中后密钥缺失仍是 skip，不改成门禁失败。

### 5.2 矩阵

新文件 `pi_agent_core/tests/provider_matrix.py`（数据）与 `pi_agent_core/tests/test_provider_matrix.py`（用例）。一行一个端点，能力是列。密钥环境变量为空则整行 skip。

| id | provider | 模型（可用 env 覆盖） | 密钥 | 能力 |
|---|---|---|---|---|
| `openai` | `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` | text, tools, usage, abort |
| `anthropic` | `anthropic` | `claude-haiku-4-5` | `ANTHROPIC_API_KEY` | text, tools, usage, thinking, abort |
| `deepseek` | `deepseek` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` | text, tools, usage, thinking, abort |
| `siliconflow` | `deepseek` + `base_url` | `deepseek-ai/DeepSeek-V4-Flash` | `REAL_LLM_API_KEY` | text, tools, usage, thinking, abort |

`REAL_LLM_BASE_URL` 默认 `https://api.siliconflow.cn/v1`，与现有 real-llm 测试相同。模型 id、base URL 都允许 env 覆盖（`MATRIX_<ID>_MODEL`、`MATRIX_<ID>_BASE_URL`），避免把供应商的模型改名写死进断言。

`deepseek` 行最初用 `deepseek-chat`。它是非思考别名，DeepSeek 已于 2026-07-24 下线 `deepseek-chat` / `deepseek-reasoner`，`thinking` 用例也就不可能通过；现改用 V4 模型 `deepseek-v4-flash`（审计 P6-03）。`test_provider_matrix.py` 里有离线用例，保证表里不再出现这两个名字。

能力断言（全部走 `run_agent_loop` + `langchain_stream`，不用裸 SDK）：

| 能力 | 通过条件 |
|---|---|
| `text` | 短提示得到非空 assistant 文本，`stop_reason` 为 `stop`，无异常冒泡 |
| `tools` | 提供一个回显工具；模型发起调用；下一轮能看见 tool result 文本 |
| `usage` | 最终 assistant `usage` 的 `input` 或 `totalTokens` > 0 |
| `thinking` | `Model.reasoning=True` 且 `thinking_level="low"` 时，出现 thinking 块 **或** 提供商明确不流式思考（记为 xfail 说明，不记为协议破坏）。`deepseek` / `siliconflow` 必须看到 `reasoning_content` 转成的 thinking，否则失败——这是选 `ChatDeepSeek` 的原因。`deepseek` 行走官方 API，适配器会发出 `thinking: enabled` 与 `reasoning_effort`（§5.4）；`siliconflow` 是网关，什么都不发，由网关自己的默认决定 |
| `abort` | 流开始后立刻 abort；`stop_reason=aborted`；`langchain_stream` 不抛 |

结构化输出与图像不进第一版矩阵。现有 `test_real_llm.py` 的 schema / 图像用例继续只对 SiliconFlow 跑，不复制到每一行。

### 5.3 运行方式

```powershell
# 只跑矩阵（有哪个密钥跑哪行）
.venv-test-real\Scripts\python.exe -m pytest -m real_llm pi_agent_core/tests/test_provider_matrix.py -v
```

依赖：矩阵使用的 provider 包已在 `[providers]` extra（`langchain-openai`、`langchain-anthropic`、`langchain-deepseek`）。文档写明用 `.venv-test-real`，与 AGENTS.md 一致。

CI：新增 workflow `provider-matrix.yml`，`workflow_dispatch` 手动触发，并按 `schedule` 每周一 03:00 UTC 定时运行（审计 P6-03：只能手动触发时，provider 下线模型或改 API 没人会发现）。命令行里的 `-m real_llm` 覆盖 `addopts`，所以 workflow 的 `pytest` 命令必须带它。密钥走 GitHub Actions secrets，映射到上表环境变量。不在 `ci.yml` 的 push/PR job 里调用。未配置的 secret 让对应行 skip，workflow 仍绿。

报告：pytest 输出即可。不把每次跑的结果写回 `docs/`。

### 5.4 DeepSeek 官方 API 的思考开关（审计 P6-03）

DeepSeek 官方 API 默认开启思考（V4 起），开关是请求体里的 `thinking: {"type": "enabled" | "disabled"}` 加 `reasoning_effort`；这两个都不是 OpenAI 标准参数，`thinking` 要经 `extra_body` 发出。适配器原先只处理 openai / anthropic，对 deepseek 什么都不发，`thinking_level` 因而无效，是否思考完全取决于 API 默认值。

`_apply_reasoning_params` 现在对 `provider="deepseek"` 且指向官方 API 的模型显式发送：

| 条件 | 发出的参数 |
|---|---|
| `Model.reasoning=True` 且 `thinking_level` 不是 `off` | `extra_body={"thinking": {"type": "enabled"}}`，`reasoning_effort`：`minimal` / `low` / `medium` / `high` → `high`，`xhigh` → `max` |
| 其他（`off`、未设、`Model.reasoning=False`） | `extra_body={"thinking": {"type": "disabled"}}`，不发 `reasoning_effort` |

映射取自 DeepSeek 为 pi 写的接入文档（`reasoningEffortMap`）。调用方已有的 `extra_body` 内容保留，不被修改。

「官方 API」的判定（`_is_deepseek_api`）：先取生效的地址——`Model.base_url`，没有则取 `DEEPSEEK_API_BASE`（`ChatDeepSeek` 自己会读它）；地址为空，或主机名是 `deepseek.com` / `*.deepseek.com`，即为官方。`api.deepseek.com.evil.example` 这类仿冒名不算。SiliconFlow、vLLM 等网关不满足，**行为不变**：它们对 `thinking` 的理解各不相同，也可能拒收。这与 AGENTS.md 的不变量 5 相比是唯一的例外：闸门关闭时通常什么都不发，而官方 DeepSeek 因为默认开启，必须显式关。

这一节最初留下两个限制：开启思考且带工具时 `reasoning_content` 没有回传（§5.5 处理），CLI 从不设 `Model.reasoning`（§5.6 处理）。

测试：`pi_agent_core/tests/test_deepseek_thinking.py`（参数映射、官方与网关的判定、`ChatDeepSeek` 请求体里确实带上这些参数）；配置面由 `test_provider_matrix.py`（行、workflow）与 `test_real_llm_selection.py`（默认不收集、`-m real_llm` 能选中）覆盖。

### 5.5 回传 `reasoning_content`（审计 P6-03，后续 4）

DeepSeek 的文档：思考模式下，请求带 `tools` 时，之前**所有** assistant 消息的 `reasoning_content` 都要原样传回（连没有调用工具的那几轮也算），否则返回 400（"The `reasoning_content` in the thinking mode must be passed back to the API"）；请求不带 `tools` 时，传了也会被忽略。适配器本来就把 `reasoning_content` 收成 `thinking` 块，但 `ChatDeepSeek` 收得进、发不出（`langchain-deepseek` 1.1.0 核对过：请求转换里没有这个字段），所以官方 API 上「思考 + 工具」在第一轮工具调用之后必然失败。

做法分两层，照 pi 自己的 OpenAI 兼容 provider 对 DeepSeek 的处理（thinking 块写回 `reasoning_content`；`requiresReasoningContentOnAssistantMessages` 打开时，没有的消息补一个空串）：

1. `convert_to_langchain`：`provider="deepseek"` 的 assistant 消息，把内容非空的 thinking 块用换行连起来，放进 `AIMessage.additional_kwargs["reasoning_content"]`。`content` 的形状不变；没有 thinking 就不放；其他 provider 不碰。
2. `ChatDeepSeek` 的子类（`adapters/deepseek_replay.py` 的 `replaying_chat_deepseek()`，首次使用时才构造，因为 `langchain-deepseek` 是可选依赖），覆盖 `_get_request_payload`：`replay_reasoning` 打开时，给每条 `role == "assistant"` 的请求消息写入 `reasoning_content`（上面那份文本，没有则 `""`）；带 `tool_calls` 而 `content` 为 `null` 的，把 `content` 改成 `""`（DeepSeek 自己的示例发的就是 `""`，LangChain 默认发 `null`）。请求消息与 LangChain 消息数量对不上时（某个 LangChain 版本合并或丢弃了消息）什么都不改：缺字段是已知的旧行为，放错位置更糟。

`replay_reasoning` 由 `resolve_chat_model` 决定：**指向 DeepSeek 官方 API，且这次请求要求思考**（`Model.reasoning=True` 且 `thinking_level` 不是 `off`，与 §5.4 是同一个判定，共用 `_deepseek_thinking_level`）。关闭思考（`disabled`）、`Model.reasoning=False`、网关，都不回传。网关不回传是有意的：同 §5.4，网关对这些字段的态度各不相同，也可能拒收；要支持某个网关，得先在那个网关上验证。pi 的上游把 DeepSeek 系的网关也算进去（按模型名里含 `deepseek` 判定），这里没有跟，理由同上。

AGENTS.md 不变量 5 的相应一句已改。

**限制**：没有在真实 API 上验证（没有 `DEEPSEEK_API_KEY`）。矩阵的 `deepseek` 行新增 `thinking_tools` 用例（思考开启、带工具、跑完一轮工具调用，最终 `stopReason` 必须是 `stop`），它是第一次会去验证的地方。

测试：`pi_agent_core/tests/test_deepseek_replay.py`——转换（只对 deepseek、块的连接、空白块、`content` 形状）；请求消息的改写（各角色、补空串、`content`、数量对不上）；`ChatDeepSeek` 的请求体（每档思考、`off`、`Model.reasoning=False`、网关、官方地址）；一次完整的工具往返（假的 `async_client`：流式收到 `reasoning_content`，下一次请求带着它）。

### 5.6 CLI 接入 `Model.reasoning`（审计 P6-03，后续 4）

CLI 构造 `Model` 时从不设 `reasoning`：`thinking_level` 在 OpenAI / Anthropic 上被闸门挡掉，在官方 DeepSeek 上则永远是 `disabled`。现在 `CliConfig` 带 `reasoning`（`[model] reasoning = true|false`，也认顶层键，同 `supports_images`），`model_reasoning` 决定 `Model.reasoning`：

| 配置 | `Model.reasoning` |
|---|---|
| `reasoning` 已设 | 就是它 |
| 未设 | `thinking_level != "off"`（用户要了思考档位，就等于说这个模型能推理） |

**行为变化**：配置里 `thinking_level` 不是 `off` 的用户，此前这个设置经 CLI 什么都不做，现在会真正生效；模型若不接受推理参数（比如 OpenAI 的非推理模型），在 `[model]` 里写 `reasoning = false` 即可。默认（`off`）不变。子 agent 仍不继承 `reasoning`（`subagent.py` 的既有规则）。

测试：`packages/pi-agent-cli/tests/test_config.py`（解析、优先级）、`test_model_reasoning.py`（`create_session_harness` 造出的模型）。

---

## 6. 文件变更总览

| 路径 | 变更 |
|---|---|
| `packages/pi-agent-cli/pi_agent_cli/git_context.py` | 新建，§4 |
| `packages/pi-agent-cli/pi_agent_cli/system_prompt.py` | 追加 `<git_status>` |
| `packages/pi-agent-cli/pi_agent_cli/config.py` | `[git]` |
| `packages/pi-agent-cli/agent.example.toml` | `[git]` 注释示例 |
| `pi_agent_core/tests/test_provider_matrix.py` | 新建，§5；`real_llm` 标记只放在实时用例上，另有离线用例检查表与 workflow |
| `pi_agent_core/tests/test_real_llm_selection.py` | 新建：默认不收集实时用例，`-m real_llm` 能选中（审计 P6-03） |
| `pi_agent_core/tests/test_deepseek_thinking.py` | 新建，§5.4 |
| `pi_agent_core/adapters/langchain_stream.py` | `_apply_reasoning_params` 增加 DeepSeek 官方 API 分支，§5.4（审计 P6-03）；`resolve_chat_model` 对 deepseek 用回传 `reasoning_content` 的子类，§5.5 |
| `pi_agent_core/adapters/deepseek_replay.py` | 新建，§5.5：thinking 文本的取法、请求消息改写、`ChatDeepSeek` 子类 |
| `pi_agent_core/adapters/langchain_convert.py` | deepseek 的 assistant 消息带上 `reasoning_content`，§5.5 |
| `pi_agent_core/tests/test_deepseek_replay.py` | 新建，§5.5 |
| `pi_agent_core/tests/provider_matrix.py`、`test_provider_matrix.py` | `deepseek` 行加 `thinking_tools` 用例，§5.5 |
| `packages/pi-agent-cli/pi_agent_cli/{config,factory}.py`、`agent.example.toml` | `reasoning` 配置与 `Model.reasoning`，§5.6 |
| `packages/pi-agent-cli/tests/{test_config,test_model_reasoning}.py` | §5.6 |
| `pyproject.toml` | `addopts = "-m 'not real_llm'"`（审计 P6-03） |
| `.github/workflows/provider-matrix.yml` | 手动触发，另每周定时 |

不新增 MCP 包或 extra；CLI 配置只多了 `[model] reasoning`（§5.6）。`agent_loop.py`、`types.py` 的 `AgentTool`、`langchain_tools.py` 不改；`langchain_stream.py` 只在 `_apply_reasoning_params` 里加了 DeepSeek 官方 API 的思考开关（§5.4），并让 deepseek 走回传 `reasoning_content` 的子类（§5.5），openai / anthropic 分支不变。

---

## 7. 测试策略

| 域 | 关键用例 | 依赖 |
|---|---|---|
| Git | 临时仓库的分支名、已修改文件、行数截断；非仓库返回 None；超时返回 None；尚无提交的仓库显示 `<name> (no commits yet)`；总预算（假时钟：每条命令拿到剩余时间，预算用尽后不再启动命令）；构建 prompt 时事件循环心跳不被 git 阻塞 | 系统 `git` + 假 `subprocess.run` |
| Prompt | `<git_status>` 位于 `<project_context>` 与 skills 之间；`--no-git-context` 不出现该段 | 现有 prompt 测试 |
| 提供商矩阵 | §5，选中后密钥缺失 skip；普通 `pytest` 不收集（`addopts`） | `.venv-test-real`，非 PR 门禁（每周定时与手动触发除外） |
| DeepSeek 思考开关 | §5.4：各档位到 `thinking` / `reasoning_effort` 的映射；官方与网关的判定；已有 `extra_body` 不被改动；`ChatDeepSeek` 请求体确实带上这些参数 | 无网络，`langchain-deepseek` 缺失时请求体用例 skip |
| DeepSeek 回传 `reasoning_content` | §5.5：转换、请求消息改写、各档位与网关的请求体、一次完整工具往返（假 `async_client`） | 无网络，`langchain-deepseek` 缺失时请求体与往返用例 skip |
| CLI `Model.reasoning` | §5.6：配置解析与优先级；`create_session_harness` 造出的模型 | 无网络 |

不新增 MCP 测试。`from_langchain_tool()` 的现有单测继续覆盖 BaseTool 结果归一，不启动 MCP 服务器。

---

## 8. 实施顺序

1. **Git 上下文。** 无新依赖，只动 CLI prompt。可独立合并。
2. **提供商矩阵与 workflow（手动触发，每周定时）。** 与 Git 无关；SiliconFlow 行复用已有密钥即可在本地验收集路径。

两步都不碰 MCP。第 1 步不要求真实 LLM。
