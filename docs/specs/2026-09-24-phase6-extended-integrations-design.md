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
3. **矩阵是 opt-in 集成测试，不是 PR 门禁。** 默认 `pytest` 保持 mock。`real_llm` 用例在密钥缺失时 `skip`，不失败。

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
    branch: str                 # 分支名；detached 时为 "HEAD (detached at <short-sha>)"
    status_porcelain: str       # 已截断的 porcelain 正文（不含分支行）
    truncated: bool

def snapshot_git_context(cwd: str, *, timeout_seconds: float, max_lines: int) -> GitSnapshot | None:
    """非仓库、git 不在 PATH、或命令失败时返回 None。"""
```

调用（均 `git -C <cwd> -c core.quotePath=false`，不经 shell）：

1. `git rev-parse --is-inside-work-tree` — 非 `true` 则返回 `None`
2. `git rev-parse --abbrev-ref HEAD` 与（仅当结果为 `HEAD`）`git rev-parse --short HEAD`
3. `git --no-optional-locks status --porcelain=v1 -b --untracked-files=normal`（`--no-optional-locks` 是 git 全局选项，避免锁 index）

超时默认 2 秒。超时或非零退出返回 `None`（prompt 里不出现空段，也不出现错误堆栈）。不递归子模块，不跑 `git diff`。

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
timeout_seconds = 2
max_status_lines = 40
```

CLI / headless：`--no-git-context` 覆盖为关。默认开。非仓库时开关无效果。

---

## 5. 多提供商生产测试矩阵

### 5.1 现状

`resolve_chat_model` 有三条显式分支和一条兜底：

| `Model.provider` | 实现 | 备注 |
|---|---|---|
| `openai` | `ChatOpenAI`（`stream_usage=True`） | |
| `anthropic` | `ChatAnthropic` | thinking budget 见 `_ANTHROPIC_BUDGET` |
| `deepseek` | `ChatDeepSeek` | OpenAI 兼容网关（SiliconFlow、vLLM）必须走这条，才能保留 `reasoning_content` |
| 其他 | `init_chat_model` | 需要额外安装 `langchain`，不纳入本期矩阵 |

现有 `pi_agent_core/tests/test_real_llm.py` 只打 SiliconFlow DeepSeek，且标记为 `real_llm`。密钥缺失时 skip。默认 CI 的 `pytest -v` 会收集这些测试；保持 skip，不改成门禁失败。

### 5.2 矩阵

新文件 `pi_agent_core/tests/provider_matrix.py`（数据）与 `pi_agent_core/tests/test_provider_matrix.py`（用例）。一行一个端点，能力是列。密钥环境变量为空则整行 skip。

| id | provider | 模型（可用 env 覆盖） | 密钥 | 能力 |
|---|---|---|---|---|
| `openai` | `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` | text, tools, usage, abort |
| `anthropic` | `anthropic` | `claude-haiku-4-5` | `ANTHROPIC_API_KEY` | text, tools, usage, thinking, abort |
| `deepseek` | `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` | text, tools, usage, thinking, abort |
| `siliconflow` | `deepseek` + `base_url` | `deepseek-ai/DeepSeek-V4-Flash` | `REAL_LLM_API_KEY` | text, tools, usage, thinking, abort |

`REAL_LLM_BASE_URL` 默认 `https://api.siliconflow.cn/v1`，与现有 real-llm 测试相同。模型 id、base URL 都允许 env 覆盖（`MATRIX_<ID>_MODEL`、`MATRIX_<ID>_BASE_URL`），避免把供应商的模型改名写死进断言。

能力断言（全部走 `run_agent_loop` + `langchain_stream`，不用裸 SDK）：

| 能力 | 通过条件 |
|---|---|
| `text` | 短提示得到非空 assistant 文本，`stop_reason` 为 `stop`，无异常冒泡 |
| `tools` | 提供一个回显工具；模型发起调用；下一轮能看见 tool result 文本 |
| `usage` | 最终 assistant `usage` 的 `input` 或 `totalTokens` > 0 |
| `thinking` | `Model.reasoning=True` 且 `thinking_level="low"` 时，出现 thinking 块 **或** 提供商明确不流式思考（记为 xfail 说明，不记为协议破坏）。`deepseek` / `siliconflow` 必须看到 `reasoning_content` 转成的 thinking，否则失败——这是选 `ChatDeepSeek` 的原因 |
| `abort` | 流开始后立刻 abort；`stop_reason=aborted`；`langchain_stream` 不抛 |

结构化输出与图像不进第一版矩阵。现有 `test_real_llm.py` 的 schema / 图像用例继续只对 SiliconFlow 跑，不复制到每一行。

### 5.3 运行方式

```powershell
# 只跑矩阵（有哪个密钥跑哪行）
.venv-test-real\Scripts\python.exe -m pytest -m real_llm pi_agent_core/tests/test_provider_matrix.py -v
```

依赖：矩阵使用的 provider 包已在 `[providers]` extra（`langchain-openai`、`langchain-anthropic`、`langchain-deepseek`）。文档写明用 `.venv-test-real`，与 AGENTS.md 一致。

CI：新增 workflow `provider-matrix.yml`，仅 `workflow_dispatch`。密钥走 GitHub Actions secrets，映射到上表环境变量。不在 `ci.yml` 的 push/PR job 里调用。未配置的 secret 让对应行 skip，workflow 仍绿。

报告：pytest 输出即可。不把每次跑的结果写回 `docs/`。

---

## 6. 文件变更总览

| 路径 | 变更 |
|---|---|
| `packages/pi-agent-cli/pi_agent_cli/git_context.py` | 新建，§4 |
| `packages/pi-agent-cli/pi_agent_cli/system_prompt.py` | 追加 `<git_status>` |
| `packages/pi-agent-cli/pi_agent_cli/config.py` | `[git]` |
| `packages/pi-agent-cli/agent.example.toml` | `[git]` 注释示例 |
| `pi_agent_core/tests/test_provider_matrix.py` | 新建，§5 |
| `.github/workflows/provider-matrix.yml` | 仅手动触发 |

不新增 MCP 包、extra 或 CLI 配置。`agent_loop.py`、`types.py` 的 `AgentTool`、`langchain_tools.py`、`langchain_stream.py` 的提供商分支不改。

---

## 7. 测试策略

| 域 | 关键用例 | 依赖 |
|---|---|---|
| Git | 临时仓库的分支名、已修改文件、行数截断；非仓库返回 None；超时返回 None | 系统 `git` |
| Prompt | `<git_status>` 位于 `<project_context>` 与 skills 之间；`--no-git-context` 不出现该段 | 现有 prompt 测试 |
| 提供商矩阵 | §5，密钥缺失 skip | `.venv-test-real`，非默认 CI |

不新增 MCP 测试。`from_langchain_tool()` 的现有单测继续覆盖 BaseTool 结果归一，不启动 MCP 服务器。

---

## 8. 实施顺序

1. **Git 上下文。** 无新依赖，只动 CLI prompt。可独立合并。
2. **提供商矩阵与手动 workflow。** 与 Git 无关；SiliconFlow 行复用已有密钥即可在本地验收集路径。

两步都不碰 MCP。第 1 步不要求真实 LLM。
