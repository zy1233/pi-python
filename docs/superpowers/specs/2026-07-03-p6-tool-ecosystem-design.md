# P6 工具生态接入 — 设计方案

> Scope: 内置编码工具（read/bash/edit/write/grep/find/ls）+ LangChain 工具生态适配器（审计 #8）。
> 上游参照：[earendil-works/pi](https://github.com/earendil-works/pi) `packages/coding-agent/src/core/tools/`
> （main @ 2026-07-03）。与 Phase 3 harness 设计（另一会话）并行推进：本设计只产出工具层，
> harness 通过工厂函数按需装配（对齐 pi `createAgentSession({ tools: [...] })` 的消费方式）。
>
> 原则回顾：内置工具只提供**基础功能**（执行语义 + 截断 + 错误约定，不含 TUI 渲染/自动下载
> 等增值件）；**编程组**（read/bash/edit/write）提供完整的文件操作和命令执行能力；
> **只读组**（read/grep/find/ls）提供无修改的搜索和检查能力。

---

## 1. 目标与归层

pi 的内置工具位于 **coding-agent 包**而非 agent-core——工具是 loop 的消费者，不是 loop 的一部分。
Python 版没有独立包，故落在 `pi_agent_core/coding_tools/` 子包，与核心运行时隔离：

| 层 | 内容 | 上游对应 |
|---|---|---|
| `pi_agent_core/coding_tools/` | 7 个内置工具 + 截断/路径/互斥公共设施 | `packages/coding-agent/src/core/tools/` |
| `pi_agent_core/adapters/langchain_tools.py` | LangChain `BaseTool` → `AgentTool` 适配 | 无（Python 特有，审计 #8 定位 core·适配器） |

约束：

- 工具只实现 `AgentTool` 协议（`types.py`），不反向依赖 agent_loop/Agent；核心运行时不依赖本子包。
- 零新增运行时依赖：全部 stdlib 实现；`grep` 优先调用外部 `rg` 二进制（缺失时纯 Python 回退）。
- 事件契约、并行工具顺序、terminate 语义等不变量（AGENTS.md）不受影响——工具经由既有
  `_execute_tool_calls_parallel` 执行路径运行。
- 与 Phase 3 harness 的 `ExecutionEnv`（FileSystem+Shell 协议，见 harness spec §8）**互不依赖**，
  对齐 pi 上游分工：内置工具直接持 cwd 落本地 FS/子进程（远程化走各工具的 `*Operations`
  扩展点，§7），`ExecutionEnv` 服务于 skills/session 存储；两者的远程化路径独立演进。

## 2. 包结构与公开 API

```
pi_agent_core/coding_tools/
├── __init__.py          # 工厂与常量导出
├── truncate.py          # truncate_head/tail/line、TruncationResult、format_size
├── path_utils.py        # resolve_to_cwd、glob→regex 翻译、图片魔数嗅探
├── mutation_queue.py    # 按绝对路径的 asyncio 互斥（write/edit 用）
└── read.py  bash.py  edit.py  write.py  grep.py  find.py  ls.py
```

工厂 API（与 pi `index.ts` 一一对应，均绑定 `cwd`，相对路径基于 cwd 解析）：

```python
ToolName = Literal["read", "bash", "edit", "write", "grep", "find", "ls"]
ALL_TOOL_NAMES: frozenset[ToolName]

def create_read_tool(cwd: str, **options) -> AgentTool     # 其余 6 个同形
def create_tool(name: ToolName, cwd: str, **options) -> AgentTool

def create_coding_tools(cwd: str) -> list[AgentTool]       # read/bash/edit/write（默认组）
def create_read_only_tools(cwd: str) -> list[AgentTool]    # read/grep/find/ls
def create_all_tools(cwd: str) -> dict[ToolName, AgentTool]
```

- **编程组 = pi 的默认四件套**：完整文件操作（读/写/精确编辑）+ 命令执行。
- **只读组 = pi 的 read-only mode**：审查/分析场景（`pi --tools read,grep,find,ls`）无修改保证——
  组内工具不含任何写路径（唯一例外是 grep 回退实现的只读遍历）。
- 不从 `pi_agent_core` 顶层再导出：镜像 pi 的包分离（agent-core 不含工具），
  使用方 `from pi_agent_core.coding_tools import create_coding_tools`。

每个工具是一个轻量 dataclass 实例（同 `tools.py` 的 `SimpleTool` 风格），字段：

- 协议必备：`name/description/label/parameters/execution_mode(=None)/prepare_arguments/execute`；
- 可选元数据：`prompt_snippet: str | None`、`prompt_guidelines: list[str]`（照抄 pi 的
  `promptSnippet/promptGuidelines` 文案），供 harness 组装 system prompt，core 不消费。

`description` 与参数 description **照抄 pi 原文**（英文）——它们是提示工程面，措辞即行为。

## 3. 与 AgentTool 协议的对接

| 协议点 | 约定 |
|---|---|
| `parameters` | pydantic `BaseModel` 子类；`execute` 收到的 `params` 是**已校验的模型实例**（`validation.py` 行为） |
| `prepare_arguments` | 仅 `edit` 使用（容错旧参数形态，见 4.3）；其余工具为 None |
| 错误 | 工具内直接 `raise`；agent_loop 既有路径转为 `is_error=True` 的 tool result（LLM 可见可自纠） |
| `signal` | 鸭子类型：`.aborted` 布尔位必备，`wait_aborted()` 可选（`_AbortSignal` 两者都有）。快工具在 await 点后检查 `.aborted`；`bash` 与 `wait_aborted()` 竞争等待并杀进程树 |
| `on_update` | 仅 `bash` 使用：同步回调 + 100ms 节流（B3 修复后的实时通道） |
| `AgentToolResult.details` | plain dict（JSON 可序列化，服务 Phase 3 Session JSONL）；key 沿用 pi 的 camelCase（`fullOutputPath`/`firstChangedLine`…），与 `messages.py` 对 pi 字段的处理一致 |
| `terminate` | 内置工具一律不设（None） |
| 并发 | `execution_mode=None`（跟随全局 `tool_execution`）；write/edit 经 mutation_queue 保证同文件串行 |

## 4. 内置工具规格

截断上限全库统一（数值与 pi 相同）：**2000 行 / 50KB**（先到先截），grep 单行 **500 字符**。
所有截断/上限提示采用 pi 的 actionable notice 风格——告诉 LLM **下一步怎么拿到剩余内容**。

### 4.1 read（只读）

参数：`path: str`；`offset: int | None`（1-based 起始行）；`limit: int | None`（最大行数）。

- 文本：按行切分；`offset` 越界 → 抛 `Offset N is beyond end of file (M lines total)`；
  先应用用户 `limit`，再 `truncate_head`。提示语三态（照抄 pi）：
  - 截断（行/字节）→ 追加 `[Showing lines X-Y of Z. Use offset=N to continue.]`
  - 用户 limit 停止但文件还有内容 → `[N more lines in file. Use offset=M to continue.]`
  - 首行独超 50KB → 输出仅为 `[Line N is <size>, exceeds 50.0KB limit. Use bash: sed -n 'Np' <path> | head -c 51200]`
- 图片：魔数嗅探 png/jpg/gif/webp/bmp → `[TextContent(说明) , ImageContent(base64)]`。
  非视觉模型的图片剥除**不在工具内做**——交给 convert 层已实现的 C1 三路处理。
  pi 的 2000px 自动缩放不移植（见 §7）。
- `details = {"truncation": {...}}`（截断时）。

### 4.2 bash（编程组核心：命令执行）

参数：`command: str`；`timeout: float | None`（秒，无默认超时；上限护栏同 pi 的 2^31ms）。

- shell 解析：POSIX → `bash -c`（缺失回退 `sh -c`）；Windows → PATH 上的 `bash.exe`
  （Git Bash）优先，缺失回退 `cmd /c`。可经 `shell_path` 选项覆盖（对应 pi `shellPath`）。
  环境继承 `os.environ`，工作目录 = 绑定的 cwd（不存在 → 抛错）。
- 输出：stdout+stderr 按到达顺序合并累积；**尾部**截断（保留末尾，错误通常在结尾）；
  截断时全量输出落临时文件，`details.fullOutputPath` 指向之，提示语
  `[Showing lines X-Y of Z. Full output: <path>]`。
- 流式：`on_update` 每 100ms 节流推送当前输出快照（`content` + 截断 details）。
- 终态（照抄 pi 措辞，均带已捕获输出前缀）：
  - 退出码非 0 → 抛 `... Command exited with code N`
  - 超时 → 杀进程树，抛 `... Command timed out after N seconds`
  - abort → 杀进程树，抛 `... Command aborted`
- 进程树终止：POSIX `start_new_session=True` + `os.killpg(SIGKILL)`；Windows
  `taskkill /PID <pid> /T /F`。abort 响应：`proc.wait()` 与 `signal.wait_aborted()` 竞争等待
  （仅 `.aborted` 布尔位的自定义 signal 退化为逐周期轮询，与 langchain_stream 的兼容策略一致）。

### 4.3 edit（编程组：精确修改）

参数：`path: str`；`edits: list[{oldText: str, newText: str}]`（至少 1 项）。

- 匹配语义（pi 不变量）：每个 `oldText` 必须在**原始文件**中恰好唯一匹配（0 次 → not found
  报错；>1 次 → not unique 报错）；各 edit 的匹配区间**互不重叠**（重叠 → 报错）；
  全部基于原文匹配后一次性应用，不做增量。
- 编码容错：剥 BOM → 记录并归一 CRLF→LF → 应用 → 还原行尾与 BOM（模型看到/产出的都是 LF 文本）。
- 返回：`Successfully replaced N block(s) in <path>.`；
  `details = {"diff": ..., "patch": ..., "firstChangedLine": N}`——Python 版 `diff` 与 `patch`
  同为 `difflib.unified_diff` 产物（pi 的 diff 是 TUI 展示格式，此处无 TUI，两 key 都保留以维持
  SDK 消费面兼容）。
- `prepare_arguments`（pi 对 Opus/GLM 的容错，原样移植）：顶层 `oldText/newText` 旧形态 →
  合并进 `edits`；`edits` 为 JSON 字符串 → 解析为数组。
- 文件不存在/不可写 → `Could not edit file: <path>. <原因>`。经 mutation_queue 串行；
  abort 在每个 await 点后检查（不从事件回调中 reject，保持队列锁到当前操作落定——pi 注释语义）。

### 4.4 write（编程组：创建/覆盖）

参数：`path: str`；`content: str`。

- 自动递归创建父目录；存在即覆盖；UTF-8 写入。
- 返回：`Successfully wrote N bytes to <path>`（N 为 `content` 的 UTF-8 字节数）。
- 经 mutation_queue 串行；abort 检查同 edit。

### 4.5 grep（只读：内容搜索）

参数：`pattern: str`；`path: str | None`（默认 cwd）；`glob: str | None`；
`ignoreCase/literal: bool | None`；`context: int | None`（前后文行数）；`limit: int | None`（默认 100）。

- 首选 **rg**（PATH 探测）：`--json --line-number --color=never --hidden` 流式解析，
  命中数达 limit 即杀进程；遵守 .gitignore。`ensureTool` 自动下载不移植——rg 缺失时走回退。
- 回退（纯 Python，只读遍历）：os.walk + 目录剪枝（`.git/node_modules/.venv/__pycache__` 等
  默认忽略清单）+ `re` 逐行匹配；嗅探二进制（NUL 字节）跳过。**不完全遵守 .gitignore**，
  作为已声明差异记录。
- 输出：`<relpath>:<line>: <text>`（context 行用 `-` 分隔符，同 rg 惯例）；单行超 500 字符截断
  并加 `... [truncated]`。无命中 → `No matches found`。
- notice：`[100 matches limit reached. Use limit=200 for more, or refine pattern. ...]`；
  `details` 记 `matchLimitReached/truncation/linesTruncated`。

### 4.6 find（只读：文件名搜索）

参数：`pattern: str`（glob，如 `*.py`、`src/**/*.ts`）；`path: str | None`；`limit: int | None`（默认 1000）。

- 纯 Python 实现（不依赖 fd、不自动下载）：os.walk + 默认忽略清单剪枝 + glob→regex 匹配
  （`path_utils` 提供翻译：`**/`→`(?:.*/)?`、`*`→`[^/]*`、`?`→`[^/]`）。
- 匹配语义对齐 pi 对 fd 的调用：pattern 不含 `/` → 仅匹配 basename；含 `/` → 匹配相对
  POSIX 路径且自动补 `**/` 前缀（`src/**/*.spec.ts` 能命中任意深度下的 `src/`）。
- 输出：相对 POSIX 路径，遍历序；无命中 → `No files found matching pattern`。
- notice/details 同 grep 形态（`resultLimitReached/truncation`）。已声明差异：默认忽略清单
  代替 .gitignore。

### 4.7 ls（只读：目录列举）

参数：`path: str | None`（默认 cwd）；`limit: int | None`（默认 500）。

- 不存在 → `Path not found: <path>`；非目录 → `Not a directory: <path>`。
- 条目字典序（大小写不敏感），目录加 `/` 后缀，含 dotfiles，stat 失败的条目跳过；
  空目录 → `(empty directory)`。
- notice：`[500 entries limit reached. Use limit=1000 for more]`；`details.entryLimitReached`。

## 5. 公共设施

### truncate.py（对应 pi `truncate.ts`，数值与语义逐项相同）

```python
DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500

@dataclass
class TruncationResult:  # content/truncated/truncatedBy/totalLines/totalBytes/
    ...                  # outputLines/outputBytes/lastLinePartial/firstLineExceedsLimit/
                         # maxLines/maxBytes —— to_dict() 供 details 序列化

def truncate_head(content, *, max_lines=..., max_bytes=...) -> TruncationResult  # read/grep/find/ls
def truncate_tail(content, *, ...) -> TruncationResult                           # bash（保留末尾）
def truncate_line(line, max_chars=GREP_MAX_LINE_LENGTH) -> tuple[str, bool]
def format_size(n_bytes) -> str                                                  # "50.0KB"
```

不变量：head 截断**从不返回半行**（首行独超限时返回空内容 + `firstLineExceedsLimit`）；
tail 截断允许末行部分保留（`lastLinePartial`，bash 超长单行场景）；字节计数用 UTF-8 编码长度，
切割点落在字符边界。

### mutation_queue.py（对应 pi `withFileMutationQueue`）

按 `os.path.realpath` 归一的绝对路径维护 `asyncio.Lock` 注册表：

```python
async def with_file_mutation_queue(absolute_path: str, fn: Callable[[], Awaitable[T]]) -> T
```

并行工具执行（D1 修复后为真并发）下，同文件的 write/edit 互斥；锁表用 WeakValueDictionary
防泄漏。跨进程不保证（pi 同样只做进程内互斥）。

### path_utils.py

`resolve_to_cwd(path, cwd)`（相对路径基于绑定 cwd 解析、`~` 展开）；glob→regex 翻译（find/grep
回退共用）；图片魔数嗅探 `detect_image_mime(buffer) -> str | None`（png/jpg/gif/webp/bmp，
手写 ~15 行——stdlib `imghdr` 已在 3.13 移除，不可依赖）。

## 6. LangChain 生态适配（`adapters/langchain_tools.py`，审计 #8 原项）

pi 无此对应物（TS 生态用 extensions 机制）；Python 版的差异化价值是让 LangChain 工具生态
（含 `langchain-mcp-adapters` 产出的 MCP 工具）零成本进入 pi 事件协议。

```python
def from_langchain_tool(tool: BaseTool) -> AgentTool
def from_langchain_tools(tools: Sequence[BaseTool]) -> list[AgentTool]
```

映射规则：

| pi 侧 | LangChain 侧 |
|---|---|
| `name` / `description` / `label` | `tool.name` / `tool.description` / `tool.name` |
| `parameters` | `tool.tool_call_schema`（优先，剔除注入参数）；缺失回退 `args_schema`；两者皆无 → `{}` 空 schema |
| `execute(id, params, signal, on_update)` | `await tool.ainvoke(args)`，`args` 为 dict（pydantic 实例经 `model_dump()`） |
| 结果归一 | `str` → 单 text 块；LC content blocks 列表 → text/image 块映射（image 的 base64+mimeType 转 `ImageContent`，其余块 str() 兜底）；`response_format="content_and_artifact"` 的 ToolMessage/tuple → content 归一 + artifact 存 `details` |
| 错误 | 异常原样冒泡（含 `ToolException`），loop 转 error tool result；不做二次包装 |
| `signal`/`on_update` | 不透传（LC BaseTool 无对应通道）；abort 由 loop 的 tool_timeout 与批次收尾兜底 |

`langchain-core` 已是核心依赖，适配器不引入新依赖。MCP 说明：不内置 MCP 客户端（与 pi 一致
——pi 明确不含 built-in MCP），`langchain-mcp-adapters` 产出的 `BaseTool` 经同一适配器接入。

## 7. 明确不移植项（保持简洁）

| pi 侧存在 | 决策 | 理由 |
|---|---|---|
| `renderCall`/`renderResult`、主题、keybinding hint | 不移植 | TUI 专属；本库无 UI 层，渲染是 harness/前端职责 |
| `ensureTool`（rg/fd 自动下载） | 不移植 | 供应链面大；rg 缺失走纯 Python 回退，find 直接纯 Python |
| 图片 2000px 自动缩放（`processImage`） | 不移植（记为可选增强） | 需 Pillow；如需，后续以 `pi-agent-core[images]` extra 提供 |
| 每工具 `*Operations` 远程委托协议（SSH 等） | 不移植（记为扩展点） | 基础功能不需要；工厂 `**options` 签名为其预留后门 |
| `promptSnippet`/`promptGuidelines` 之外的 prompt 装配 | 不移植 | 元数据字段保留（§2），装配逻辑归 harness |
| bash 的 `commandPrefix`/`spawnHook` | 简化为 `shell_path` 单选项 | 高级定制待真实需求 |

## 8. 测试策略

全走本地文件系统 + tmp_path，无 API key、无网络；镜像 pi 语义的断言点：

| 域 | 关键用例 |
|---|---|
| truncate | 行/字节先到先截、首行独超限、tail 半行边界、UTF-8 多字节切割不产生烂字符 |
| read | offset/limit 组合、三态提示语、越界报错、图片魔数→ImageContent |
| write | 父目录自动创建、覆盖、字节数报告 |
| edit | 唯一匹配（0 次/多次报错）、重叠拒绝、多 edit 原文匹配、CRLF/BOM 往返、prepare_arguments 两种容错形态、diff/patch details |
| bash | 退出码/超时/abort 三终态措辞、尾部截断 + fullOutputPath、on_update 执行期间送达（复用 B3 回归的时序断言）、进程树清理 |
| grep/find/ls | limit notice、无命中文案、glob 语义（basename vs 路径 pattern）、忽略清单生效；grep 的 rg 路径在 CI 上有 rg 时跑，回退路径强制 `_use_fallback` 跑 |
| mutation_queue | 并行 write/edit 同文件串行（交错写入不撕裂） |
| langchain_tools | str/blocks/artifact 三种返回归一、schema 提取、异常冒泡为 error tool result |
| 集成 | `create_coding_tools` 挂进 Agent + mock stream 跑一轮工具循环，事件序列不变量不破坏 |

## 9. 实施排序（依赖驱动）

1. `truncate.py` + `path_utils.py` + `mutation_queue.py`（一切的地基）
2. `read` / `write` / `ls`（纯 FS，验证协议对接与 details 形态）
3. `edit`（匹配/重叠/编码往返语义最重，独立可测）
4. `bash`（进程管理 + 流式更新 + abort，风险最高）
5. `grep` / `find`（rg 集成与回退、glob 翻译）
6. `adapters/langchain_tools.py`（独立无耦合，可与 4-5 并行）
7. 工厂装配 + 集成测试 + README「内置工具」一节

## 10. 文件变更总览

| 文件 | 变更 | 说明 |
|---|---|---|
| `pi_agent_core/coding_tools/`（新目录，11 个文件） | 新建 | §2 结构 |
| `pi_agent_core/adapters/langchain_tools.py` | 新建 | §6 |
| `pi_agent_core/adapters/__init__.py` | 修改 | 导出 `from_langchain_tool(s)` |
| `pi_agent_core/tests/test_coding_tools_*.py`（约 5 个） | 新建 | §8 分域 |
| `pi_agent_core/tests/test_langchain_tools.py` | 新建 | §8 |
| `README.md` | 修改 | 「内置工具」+「LangChain 工具接入」两节 |
| `docs/AUDIT-2026-07-02.md` | 修改 | #8 行链接本 spec；实施后补记录 |
| `AGENTS.md` | 修改 | 文档索引 + 模块表补行 |

核心运行时（`types.py`/`agent_loop.py`/`agent.py`/`messages.py`）**零改动**。
