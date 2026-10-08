# AUDIT：Phase 6 / Phase 7 设计与实现审计（2026-09-30）

> **审计日期**：2026-09-30（发现与前三批修复在同一天完成；批 4 在 10 月 1 日，批 5 至 7 在 10 月 2 日）。2026-10-08 另一个会话做了一轮只读的符合性复核（它的文档没有保留；有用的结论与逐条重新核对的结果都在第八节），批 8 在同一天修复了其中的两个中危问题
>
> **审计范围**：
> - Phase 6：`packages/pi-agent-cli/pi_agent_cli/{git_context,prompt_options,system_prompt,factory}.py`、`pi_agent_core/adapters/langchain_stream.py`、`pi_agent_core/tests/{provider_matrix,test_provider_matrix}.py`、`.github/workflows/provider-matrix.yml`
> - Phase 7：`pi_agent_core/extensions/`、`AgentHarness` 的扩展集成与生命周期、CLI 的扩展接线 / 权限层 / ACP 会话、`packages/pi-web-access/`、`packages/pi-goal-x/`、`packages/pi-dynamic-workflows/`
>
> **对照设计**：`docs/specs/2026-09-24-phase6-extended-integrations-design.md`、`docs/specs/2026-09-22-phase7-extension-api-design.md`
>
> **方法**：代码阅读；在 Windows（zh-CN 区域设置）上用探针脚本实测；provider 文档（DeepSeek 等）对照核对。探针是临时脚本，没有入库；每一项的回归测试入库，并且都先红后绿。
>
> **修复批次**：批 1 `6aaf29a`、批 2 `cf92422`、批 3 `b3de95f`、批 4（P7-02 的交互式信任提示与内容哈希）`0a1dd67`、批 5（P7-01 的子 agent `cwd` 约束与脚本沙盒）`9baefa6`、批 6（第七节的 4–6：DeepSeek 的 `reasoning_content` 回放与 CLI 的 `Model.reasoning`、按 annotations 放行的权限模型、后台 turn 期间到达的 prompt）`d4ab3f8`、批 7（第七节的 7：发布准备，版本 0.5.0）`7e7d4d4`、批 8（第七节的 8–9：F7-01 项目级 saved workflows 纳入项目信任门，P7-17 一个坏的 `meta.description` 不再让整个会话不可用）随本文档的这次更新一起提交（`git log -- docs/AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md`）。第八节里 2026-10-08 复核的其余发现（F7-02 至 F7-09，低危与文档备注）还没有修复批次
>
> **最终测试状态**（批 7 收尾时，`7e7d4d4`）：
> - Windows：`1980 passed, 10 skipped, 31 deselected`（10 个 skip 是只在 POSIX 上有意义的用例：批 4 的 5 个（符号链接的显示路径、命名管道、权限位 2 个、非法文件名），批 5 的 5 个（`RLIMIT_NPROC`、rlimit 数值、`prctl`、文件描述符上限、信号名）；31 个 deselected 是 `real_llm` 用例，批 6 起矩阵的 `deepseek` 行多一项 `thinking_tools`；审计开始时 `576 passed, 30 skipped`）
> - WSL：`1975 passed, 15 skipped, 31 deselected`（4 个 skip 是 WSL 里没有 `rg`，3 个是只在 Windows 上有意义的路径大小写用例，8 个是批 5 里只在 Windows 上有意义的用例：Job Object 3 个、低完整性 3 个、驱动器号与路径大小写 / 斜杠各 1 个）
> - WSL，Python 3.11 与 3.13（CI 的矩阵；各用一个临时 venv）：`1956 passed, 34 skipped, 31 deselected`（比 3.12 多出的 19 个 skip 是这些 venv 没装 `langchain-deepseek`，CI 也不装它；装上之后，DeepSeek 相关的 145 个用例在两个版本上都通过）
> - `ruff check .` + `ruff format --check .`：All checks passed（本地 0.15.20，也用 CI 钉住的 0.16.0 跑过一遍）
> - 2026-10-08，当前 HEAD（`4b5848f`）：Windows `2025 passed, 14 skipped, 31 deselected`（复核的作者与之后的核对各跑了一遍，结果相同）；WSL `2024 passed, 15 skipped, 31 deselected`；`ruff check .` + `ruff format --check .`：All checks passed（本地 ruff 现在是 0.16.10，CI 钉住的 0.16.0 也跑过一遍）。与批 7 收尾时相比，Windows 多 45 个通过、4 个 skip，WSL 多 49 个通过，原因是批 7 之后合入的两个 TUI 重构提交（`455527c`、`4b5848f`）新增了用例：ACP stdio 契约、关闭、session 列表、配置、上下文文件，以及 `scripts/tests/test_tui_baseline.py`；多出的 4 个 skip 是 `test_acp_shutdown.py` 里需要 POSIX 信号与进程组的用例（Windows 跳过，WSL 里运行）。既有用例全部仍然通过
> - 批 8 收尾时（在 `4b5848f` 之上的工作区，随本文档的这次更新一起提交）：Windows `2151 passed, 14 skipped, 31 deselected`；WSL `2150 passed, 15 skipped, 31 deselected`；WSL 的 Python 3.11 与 3.13（各用一个临时 venv，没装 `langchain-deepseek`）都是 `2131 passed, 34 skipped, 31 deselected`（与 3.12 的差，仍是那 19 个 DeepSeek 用例）；`ruff check .` + `ruff format --check .`：All checks passed（本地 0.16.10，CI 钉住的 0.16.0 也跑过）。与 `4b5848f` 相比，Windows 与 WSL 各多 126 个通过，skip 数不变：都是批 8 新增的用例（指纹、提示、通知、store、扩展注册、API 门面、harness 的桥，以及 ACP 与 headless 的端到端）；既有用例全部仍然通过
>
> **状态图例**：`[x]` 已修复并验证 · `[x/~]` 主路径已修复，有已记录的残留（见第五节）· `[ ]` 已记录，尚未处理（第八节）

> **编号说明**：本文件的 `P6-xx` / `P7-xx` 是本次审计的编号；代码与测试里写作 `audit P7-xx`、`audit P6-xx` 的，指本文件。
> `AUDIT-PHASE7-EXTENSION-API.md` 是更早的一轮审计，它的 `P7-01` … `P7-15`、`P7R3-xx` 与本文件重名但含义不同；测试文件里孤立的 `# P7-02: failed activate must roll back …` 这类注释属于旧文件（已在旧文件开头加了一条指向本文件的说明）。
> 2026-10-08 的复核用 `F6-xx` / `F7-xx`（F = Finding），与上面几套编号都不通用；第八节沿用这些编号。

---

## 一、问题总览

### 高危

| ID | 问题 | 批次 | 状态 |
|----|------|------|------|
| P6-01 | Windows 中文区域设置下 git 快照编码错误：会话不可用或分支名乱码 | 1 | `[x]` |
| P6-02 | `<git_status>` 在会话内被冻结，与规格「每个 turn 刷新」相反 | 2 | `[x]` |
| P7-01 | `workflow` 等价于无需授权的任意代码执行，子 agent 绕过 `ask` 权限 | 2 | `[x/~]` |
| P7-02 | 项目目录中的扩展无需信任确认，打开项目即执行 | 2 + 3 + 4 | `[x/~]` |
| P7-03 | 内置 workflow 的 tier 默认指向 Anthropic，并把其他厂商的 API key 发给它 | 1 | `[x]` |

### 中危

| ID | 问题 | 批次 | 状态 |
|----|------|------|------|
| P6-03 | Provider 矩阵：`deepseek` 行不可能通过，`real_llm` 并非默认排除，矩阵只能手动触发 | 3 | `[x/~]` |
| P6-04 | 事件循环内同步执行 3 次 git 子进程，超时按命令而非按总量计 | 3 | `[x]` |
| P7-04 | 扩展的 `prompt_snippet` / `prompt_guidelines` 在 CLI 路径从未进入 system prompt | 1 | `[x]` |
| P7-05 | Journal：并行 resume 命中 0，失败被缓存为成功的 `None` | 3 | `[x/~]` |
| P7-06 | 前台 workflow 不响应取消 | 3 | `[x/~]` |
| P7-07 | 会话生命周期：`close` 之后仍发起新一轮 LLM 调用；后台结果以 user 角色注入 | 3 | `[x/~]` |
| P7-08 | 扩展 hook 可以覆盖权限拒绝（最后一个非 `None` 结果获胜） | 1 | `[x]` |
| P7-09 | `fetch_url`：SSRF、无体积上限、正则二次退化并阻塞事件循环 | 3 | `[x/~]` |
| P7-10 | Worktree 隔离：丢失新增文件、GBK 文件回写失败、清理泄漏 | 3 | `[x/~]` |
| P7-11 | goal-x：状态不持久、越界步骤、提醒循环无界 | 3 | `[x/~]` |

### 低危

| ID | 问题 | 批次 | 状态 |
|----|------|------|------|
| P6-05 | 尚无提交的仓库（unborn HEAD）返回 `None`，git 段整体消失 | 3 | `[x]` |
| P6-06 | git 段默认开启，分支名与改动文件名会发给 LLM 提供商，文档没提隐私影响 | 3 | `[x/~]` |
| P7-12 | 扩展身份取自 `activate.__module__`，同模块两个扩展互相覆盖；加载失败被静默吞掉 | 3 | `[x/~]` |
| P7-13 | workflow journal / store / 扩展目录忽略 CLI 的 `PI_HOME` | 3 | `[x]` |
| P7-14 | 子 agent 执行器构造失败时静默退回 `MockSubagentExecutor` | 3 | `[x]` |
| P7-15 | 扩展包依赖声明不全（版本下限过低、漏声明 `pi-agent-harness-lc`） | 3 | `[x]` |
| P7-16 | `register_tool` 不校验工具名，一个非法名称会让此后每个请求被服务端拒绝 | 3 | `[x]` |

### 与上游对齐的补充项（批 3，源自 P7-02）

| 项 | 说明 | 状态 |
|----|------|------|
| 项目提示文件与 skills 的信任 | 上游 pi 把项目的 `.pi/SYSTEM.md`、`.pi/APPEND_SYSTEM.md`、项目相对的 skills 与项目扩展放在同一信任规则之后；本仓库此前只门控了扩展 | `[x]` |
| 项目信任的交互式确认与内容指纹（批 4） | 上游 pi 的 `defaultProjectTrust = ask` 会在交互模式里询问并按目录保存决定；批 2 只做了不提示、按路径的最小子集 | `[x]` |

### 2026-10-08 复核带来的新发现（第八节；`F` 开头的编号来自那次复核）

| ID | 问题 | 级别 | 状态 |
|----|------|------|------|
| F7-01 | 项目级 saved workflows（`.pi-python/workflows/`）游离于项目信任门之外：不受信任的项目也会把里面的脚本注册成斜杠命令，描述进入补全与会话上下文；同名脚本还能顶替用户自己的 saved workflow，甚至 `/workflows` 命令本身 | 中 | `[x]`（批 8） |
| P7-17 | saved workflow 的 `meta.description` 不是字符串时，`AvailableCommand` 校验失败：该会话里每个 `session/prompt` 都报错，命令一个也不广播（核对 F7-01 时另外发现的） | 中 | `[x]`（批 8） |
| F7-02 | `get_custom_entries` 只反映加载时的快照，读不到本会话刚写的条目 | 低 | `[ ]` |
| F7-03 | `ExtensionAPI.run_async` 用 `asyncio.get_event_loop()`：没有运行中的循环时，3.12 起发弃用警告，3.14 起抛错 | 低 | `[ ]` |
| F7-04 | `register_command` 不校验命令名：含空白的名字永远派发不到，却照常广播给客户端 | 低 | `[ ]` |
| F7-05 | 目录扩展 import 失败后，半初始化的模块留在 `sys.modules` | 低 | `[ ]` |
| F7-06 | 扩展工具静默覆盖同名内置工具；动态注入路径不发 `ToolsUpdateEvent` | 低 | `[ ]` |
| F7-07 至 F7-09 | 文档：规格 §3.5 的 `session_start` 映射与 §4.3 矛盾；`ToolDefinition.execute` 的调用契约没写；§14 的 journal 哈希写成 `{prompt, opts}`，实际是子集 | 备注 | `[ ]` |

---

## 二、高危问题详情

### P6-01. [x] Windows 中文区域设置下 git 快照编码错误

- **问题**：`_git()` 用 `subprocess.run(text=True)` 且不指定 `encoding`，按系统区域设置（cp936）解码 git 的 UTF-8 输出，只捕获 `OSError` / `TimeoutExpired`。实测：分支名为 `功` 或 `功能分` 时，每次 `prompt()` 抛 `AgentHarnessError`（解码失败发生在读线程里，`stdout` 变成 `None`，随后 `.replace` 抛 `AttributeError`），LLM 从未被调用，该仓库里的会话完全不可用；分支名 `功能` 被写成 `鍔熻兘`；中文文件名被输出为 `\344\270\255…` 转义。
- **修复**（批 1）：`encoding="utf-8", errors="replace"`；git 加 `-c core.quotePath=false`；任何失败都省略 `<git_status>`，不让 turn 失败。
- **验证**：真实 git 仓库里的非 ASCII 分支名与路径；断言 git 调用固定 UTF-8 与 `core.quotePath=false`；任何 git 失败、或 `stdout` 缺失，都省略该段而不是让 turn 失败。
- **规格**：Phase 6 规格 §4.1（编码与失败处理）。

### P6-02. [x] `<git_status>` 在会话内被冻结

- **问题**：规格、README、CHANGELOG 都写「每个 turn 刷新」，但 `AgentHarness` 会缓存第一次生成的 system prompt（提交 `bee053c`，为保持 provider 提示前缀缓存稳定），实测第一次 prompt 之后修改工作区，后面的 prompt 里 git 段仍是旧状态，块内也没有任何提示。
- **决策**（用户选择）：标注为会话开始时的快照，不做每 turn 刷新。刷新会让每次文件改动都使 skills 段与其后的全部历史的缓存失效。
- **修复**（批 2）：块开头写明「Snapshot taken at the start of this session; it is not refreshed as files change. Run `git status` for the current state.」，不带时间戳，prompt 保持字节稳定；规格 §4.3、README、CHANGELOG 里「on every turn」的说法一并更正。
- **残留**：模型看不到会话期间的变化，是这一取舍的代价。

### P7-01. [x/~] `workflow` 等价于无需授权的任意代码执行

- **问题**：`PERMISSION_TOOLS` 只有 `bash` / `edit` / `write`，`workflow` 不在其中；子 agent 由全新的 harness 创建，不挂任何 `tool_call` 钩子，实测 `ask` 模式下子 agent 直接 `write` 成功，没有授权请求。「沙盒」只是收窄 `__builtins__`，实测两条路径都成功在沙盒外写出文件（`agent.__globals__['__builtins__']['__import__']`，以及 `().__class__.__base__.__subclasses__()` 走到 `catch_warnings.__init__.__globals__`）。
- **修复**（批 2）：`workflow` 加入 `PERMISSION_TOOLS`（`ask` 模式下启动 workflow 要用户批准；批 6 起名单换成 annotations，见下）；子 agent 的每次工具调用都走父会话的 `tool_call` 链（`AgentHarness.check_tool_call`，扩展经 `HarnessBridge.tool_call_gate` 使用；出错时拒绝），调用 id 为 `subagent-<hex>:<model>`，`ToolCallEvent.origin = {"kind": "subagent", "cwd": ..., "label": ...}`，ACP 授权提示注明来自子 agent 与它的工作目录；批准 `workflow` 不等于批准它的子 agent 写文件，每次调用逐个询问（只读工具与会话自己一样不询问，但扩展 hook 照样生效）；同一会话的授权提示串行，并行子 agent 不会叠出多个对话框。文档不再称运行时为「沙盒」。
- **修复**（批 5，子 agent 的 `cwd`）：`agent(..., cwd=)` 给的目录必须落在项目目录内：相对路径相对项目目录解析（不是进程的当前目录），符号链接与目录联接跟随后再比较，越界是脚本里可以 `except` 的 `ValueError`（非字符串是 `TypeError`，含 NUL 字符是 `ValueError`）。检查在占用 agent 名额与查 journal 之前，所以收紧后的策略不会被一次 resume 绕过；journal 哈希仍用脚本给的原始 `cwd`；`HarnessSubagentExecutor` 在建 worktree 之前再检查一次，独立使用执行器也受约束。新模块 `paths.resolve_subagent_cwd`。
- **修复**（批 5，脚本沙盒）：脚本不再在宿主的解释器里 `exec`，每次 `WorkflowRuntime.execute()` 起一个子进程运行它（`python -I -S -B -X utf8 sandbox/child.py`：只有标准库、清空的环境、空的临时工作目录，fd 0 / 1 指向空设备），有副作用的事都经管道（行式 JSON，协议 v1）请求宿主去做，宿主把管道另一头当作不可信输入：超长 / 非法 / 嵌套过深的消息、未知消息、字段类型不对、重复的调用 id、乱序消息都会结束这次运行并杀进程；在途调用数、日志行数与字符数、phase 个数都有上限（调用堆积时宿主不再读，而不是拒绝）。钩子之下是内核限制：POSIX 的 `RLIMIT_CORE` / `AS` / `CPU` / `NOFILE` / `FSIZE`（软硬同值，脚本改不回去），Linux 另有 `PR_SET_NO_NEW_PRIVS` 与 `RLIMIT_NPROC=0`（不能 `fork`、不能再起线程；root 不受约束）；Windows 的 Job Object（进程内存与 CPU 时间、只允许一个进程、UI 限制、宿主放手时一并结束）与低完整性（脚本不能再写用户能写的地方、读宿主的内存、复制它的句柄）。墙钟（`SandboxLimits.wall_seconds`，默认关闭）到点杀进程；宿主被杀后不留脚本进程。同时加固了审计钩子（它仍然只是速度栏）：子进程设置限制用过 `ctypes` 之后把它从 `sys.modules` 里摘掉，让钩子能看到后来的 `import`。脚本抛异常 → `WorkflowScriptError`（`RuntimeError`，`str()` 是脚本自己的消息，另有 `error_type`、`line`）；被限制终止 / 起不来 → `SandboxError` 的子类。**行为变化**：值以 JSON 过边界（`args`、`result()`、`agent()` 的返回值），脚本里的 `budget` 是只读视图，每次运行多一次进程启动（Linux 约 0.06 s，Windows 约 0.14 s），Windows 上要求默认的 Proactor 事件循环。一次 `agent()` 调用带上它发出时的 phase（宿主用任务处理调用，轮到它时并行的另一个 thunk 可能已经让脚本进入下一个 phase；这是迁移时发现并补上的一处语义回归）。
- **修复**（批 6，权限模型）：`ask` 模式不再是一张工具名白名单（`PERMISSION_TOOLS` 删除），改为默认询问、工具自己声明无害才放行。工具带 `ToolAnnotations`（MCP 的四个提示；`SimpleTool` / `CodingTool` / `ToolDefinition` 都有该字段，LangChain 工具从 `tool.metadata` 取）；放行条件是 `readOnlyHint is True`，或 `destructiveHint is False` 且 `openWorldHint is False`；没声明、声明了别的、值不是真正的布尔、annotations 不是映射，一律询问。`AgentHarness.check_tool_call` 按工具名在会话自己的工具表里查，把 annotations 放在 `ToolCallEvent.annotations` 上；会话自己的循环与子 agent 的调用走同一条路径，所以子 agent 的调用按父会话里同名工具判定，父会话没有的名字要问。内置的 `read` / `grep` / `find` / `ls`、`web_search` / `fetch_url`（只读，但联网）、`goal_update` / `goal_complete`（只在会话里记笔记）声明自己；`bash` / `edit` / `write` / `workflow` 刻意不声明。**行为变化**：`ask` 模式下，不声明 annotations 的扩展工具现在会询问，此前直接执行。
- **验证**：先红后绿，包括经真实 ACP agent、真实 workflow 扩展、真实 git worktree 的端到端用例；批 5 的沙盒用「走出命名空间的脚本」（`sandbox_support.PRELUDE`，即审计里的第一条逃逸）当作攻击者，分别在钩子打开与关闭两种情况下检查剩下的是什么：钩子关闭时只剩内核层，各层都有独立的用例。批 5 共 115 个变异体（`cwd` 15 个、沙盒 100 个；Windows 与 WSL 上都跑过），全部被杀，分簇的数字与首轮幸存的 13 个见第六节。
- **规格**：Phase 7 规格 §9.3、§12（权限继承）；沙盒的完整设计、协议、逐平台的层与钩子被绕过后的残余能力见 `docs/specs/2026-10-01-workflow-sandbox-design.md`。
- **残留**：见第五节 1–3。

### P7-02. [x/~] 项目目录中的扩展无需信任确认

- **问题**：`<cwd>/.pi-python/extensions` 在 `session/new` 时立即 `exec_module`：克隆一个恶意仓库并打开，就会在用户权限下执行仓库里的 Python 代码。
- **决策**（用户选择）：批 2 默认不加载、靠显式开关信任，不做交互式提示；批 4 按用户要求补上交互式提示与内容指纹（见下）。
- **修复**（批 2）：项目扩展默认不导入（连模块都不加载）。信任只来自项目自己写不到的地方：home 的 `agent.toml` 里的 `[extensions] trusted_projects = [...]`（含子目录）/ `trust_project_extensions = true`、环境变量 `PI_TRUST_PROJECT_EXTENSIONS=1`、命令行 `--trust-project-extensions`（ACP 与 headless 都有）。被跳过的扩展会记日志，并告诉用户（ACP 在 `session/new` 之后发一条 agent 消息，headless 写 stderr）。顺带修复了 cwd 为 home 时 `~/.pi-python/extensions` 被扫描两次。库 API：`AgentHarness(trust_project_extensions=False)`、`ExtensionLoader.load_all(trust_project_extensions=False)`、`skipped`。
- **修复**（批 3，对齐上游）：项目的 `.pi/SYSTEM.md`、`.pi/APPEND_SYSTEM.md` 和项目相对的 `[skills].paths`（如 `.pi/skills`）同样受这条信任规则约束；不受信任时回落到用户自己的 `~/.pi-python/agent/SYSTEM.md` / `APPEND_SYSTEM.md`；通知里也列出这些文件。
- **修复**（批 4，信任的交互与内容指纹）：ACP 里，项目带有受门约束的资源、而配置 / 命令行 / 白名单 / 已保存的回答都没有决定它时，agent 在 `session/new`（及 `load` / `resume`）应答之后先发一条普通的 agent 消息说明（目录、文件、各自作用；TUI 只显示权限请求的标题与选项、不显示其 `content`，所以说明放在消息里；文件名由仓库决定，可能带换行、转义序列或方向控制符，所以不可打印字符写成转义、名字放进代码片段；说明发不出去就不提问），再发 `session/request_permission`：`Don't trust`（`reject_once`，排第一）与 `Trust and remember`（`allow_always`），**没有 `allow_once`**——ACP 允许客户端代答，TUI 的 YOLO 会自动选第一个 `allow_once`，没有该选项则问题落到真人面前。只有选中且 id 恰为 trust 选项才算同意，取消 / 未知 id / 客户端报错一律不信任，拒绝不被记住。得到回答之前项目资源一概不加载，首个 `prompt` 等待回答（`cancel` 结束此刻所有在等的 prompt，关闭会话也结束等待）；同目录多个会话依次询问，一次「信任」就回答了所有会话；对话框关闭后对磁盘重新取指纹，对话框期间文件变了就不信任。「记住」写入 `<home>/agent/trust.json`：按精确目录保存文件内容的 SHA-256 指纹（不是 mtime），`git pull` 改了文件就重新询问并说明「自上次信任后已改变」；文件读取失败即关闭，损坏的文件另存为 `trust.json.corrupt`，保存失败时本次会话仍受信并告知用户。新增 `[extensions] default_project_trust = "ask" | "never" | "always"`（默认 `ask`，旧开关 `trust_project_extensions` 等价于 `always`）。取不出指纹（文件过多 / 过大 / 读不了）的项目不提问，请写入 `trusted_projects`。headless 不提问，但承认指纹仍然匹配的已保存回答，其「被跳过」通知带原因。顺带修复：`PiAcpAgent(home=)` 与 `run_print(home=)` 没把 home 传给 `create_session_harness`，用户扩展目录读错了地方。
- **验证**：批 2 的信任判定 14 个变异体、批 3 的提示文件 / skills 14 个变异体，全部被杀；批 4 新增 330 个用例（存储、指纹、判定、提示、ACP 全流程、headless、harness 各一组）与 210 个变异体，全部被杀，分簇的数字见第六节。
- **规格**：Phase 7 规格新增 §4.4（信任模型，含批 4 的交互式确认与内容指纹）。
- **残留**：见第五节 4–6。

### P7-03. [x] tier 路由默认指向 Anthropic，并把其他厂商的 key 发给它

- **问题**：5 个内置 workflow 的 13 处 `agent()` 都带 `tier=`，默认 tier 写死 Anthropic；`_parse_model_id` 只继承 `api` 与 `context_window`，丢弃 `base_url` / `supports_images` / `reasoning`；`make_get_api_key` 对任何 provider 都返回同一个环境变量——实测 `get_api_key('anthropic')` 返回的是 SiliconFlow 的 key，适配层直接把它当 `api_key`，且 `base_url` 为空，请求会走 Anthropic 默认端点（最后一跳按代码路径推断，未联网实测）。默认配置（SiliconFlow / DeepSeek）下运行内置 workflow，子 agent 会全部失败，用户的 key 会发给第三方。
- **修复**（批 1）：内置 tier 按父 provider 给出（`DEFAULT_MODEL_TIERS_BY_PROVIDER`，目前只有 `anthropic`；`DEFAULT_MODEL_TIERS` 删除），其他 provider 的 tier 继承父模型；显式传入的 `tiers` 仍然优先。子 agent 模型只在同 provider 时保留父模型的 `base_url`。`make_get_api_key(config)` 只对 `config.provider` 返回 key，其余返回 `None`。
- **验证**：批 1 共 52 个回归测试，修复前全部为红；含经 CLI → `workflow` 工具 → 子 agent stream 的端到端用例。
- **迁移**：workflow 把子 agent 路由到另一个 provider 时，需自行导出那个 provider 的标准环境变量（如 `ANTHROPIC_API_KEY`）。

---

## 三、中危问题详情

### P6-03. [x/~] Provider 矩阵

- **问题**：`deepseek` 行用 `deepseek-chat`（非思考别名，DeepSeek 已于 2026-07-24 下线）却要求出现 thinking；适配器 `_apply_reasoning_params` 只处理 openai / anthropic，对 deepseek 什么都不发，`thinking_level` 无效，是否思考取决于 API 默认值（V4 起默认开启）；`pyproject.toml` 标注「deselected by default」，实际没有 `addopts`，开发机导出 `OPENAI_API_KEY` 等变量时普通 `pytest` 会调用付费 API；矩阵只有 `workflow_dispatch`。
- **修复**（批 3）：
  - 适配器：对指向 DeepSeek 官方 API 的模型显式发送 `thinking: {"type": "enabled" | "disabled"}`（经 `extra_body`）与 `reasoning_effort`（`minimal` 至 `high` 为 `high`，`xhigh` 为 `max`，取自 DeepSeek 为 pi 写的接入文档）。`Model.reasoning=True` 且 `thinking_level` 不是 `off` 才请求思考，其余情况发 `disabled`。判定「官方 API」：生效地址（`Model.base_url`，没有则 `DEEPSEEK_API_BASE`）为空，或主机名是 `deepseek.com` / `*.deepseek.com`；SiliconFlow、vLLM 等网关不变。
  - 矩阵 `deepseek` 行改为 `deepseek-v4-flash`。
  - `pyproject.toml` 加 `addopts = "-m 'not real_llm'"`（命令行的 `-m real_llm` 覆盖它）；`real_llm` 标记从整个矩阵模块移到实时用例上，离线检查照常运行。
  - `provider-matrix.yml` 加每周一 03:00 UTC 的 `schedule`。
- **验证**：DeepSeek 分支 35 个变异体全部被杀；配置面 11 个变异体中 10 个被杀，幸存的一个是把 `addopts` 写成反向选择——那样整套用例都被取消选择，没有任何用例能自检。
- **规格**：Phase 6 规格 §1.3、§5、新增 §5.4。
- **残留**：见第五节 13。批 3 与批 6 对 DeepSeek 的修复**都没有对真实 API 验证**（没有 `DEEPSEEK_API_KEY`）；每周矩阵（`deepseek` 行，批 6 起含 `thinking_tools`）是第一次会验证它们的地方。
- **行为变化**：官方 DeepSeek 上 `off`（默认）与 `Model.reasoning=False` 现在发 `thinking: disabled`，此前什么都不发。
- **修复**（批 6，思考与工具同用）：开启思考且带工具时，DeepSeek 要求此前所有 assistant 消息（没有工具调用的回合也算）带回 `reasoning_content`，否则 400，而 `ChatDeepSeek` 不回传（1.1.0 / 1.1.1 都不）。现在 `convert_to_langchain` 把 DeepSeek 回合的 thinking 放进 `AIMessage.additional_kwargs["reasoning_content"]`，`resolve_chat_model` 构造的 `ChatDeepSeek` 子类（`adapters/deepseek_replay.py`）把它写回请求里的每一条 assistant 消息：没有就写空串（与 pi 自己的 provider 一致），带工具调用的消息用 `content: ""` 而不是 `null`（与 DeepSeek 自己的样例一致）。只在官方 API 且请求了思考时生效，网关上的 DeepSeek 模型不动。CLI 现在设置 `Model.reasoning`（`[model] reasoning = true|false`，缺省为 `thinking_level != "off"`），所以 `thinking_level` 经 CLI 才真正生效。验证：37 个变异体全部被杀（第六节）。**行为变化**：`thinking_level` 设为非 `off` 的用户现在得到所要求的思考；模型拒绝推理参数（比如不会推理的 OpenAI 模型）时用 `reasoning = false`。规格：Phase 6 规格 §5.5。

### P6-04. [x] 事件循环内同步执行 git 子进程

- **问题**：`snapshot_git_context` 在 async 的 system prompt 回调里同步调用 `subprocess.run`（两次 `rev-parse`、一次 `status`），实测事件循环最长停顿 124 ms；超时是「每条命令 2 秒」，最坏 3 × 2 秒内 ACP 的取消 / 授权响应都被卡住。
- **修复**（批 3）：prompt 的输入在工作线程里构造；`[git] timeout_seconds` 改为整个快照的总预算（最多四条命令），每条命令只拿剩下的时间，用完后不再启动新命令。
- **验证**：假时钟的预算用例；构建 prompt 时事件循环心跳不被阻塞的用例；9 个变异体全部被杀（与 P6-05 共用）。
- **规格**：Phase 6 规格 §4.1、§4.4。

### P7-04. [x] 扩展的 prompt 片段从未进入 system prompt

- **问题**：`factory.py` 的 `all_tools` 取自 `ctx.get('tools')`，而 harness 回调只传 `active_tools`，于是回退成只含内置工具的表；实测 12 个工具发给了 LLM，goal-x 的 `GOAL_SYSTEM_GUIDELINES` 与 web-access 的 snippet 都不在 prompt 里。
- **修复**（批 1）：system prompt 用的工具查找表包含扩展工具。因为 `pi-goal-x` 在每个会话里都注册工具，它的 guidelines 改写成「没有活动 goal 时也成立」。
- **验证**：端到端断言「扩展 snippet 出现在 prompt 里」。

### P7-05. [x/~] Journal 并行 resume 与失败缓存

- **问题**：journal 按位置匹配，遇到第一个不匹配就丢弃其后的所有记录；`parallel()` 按完成顺序写入，实测 resume 命中 0 次，全部子 agent 重新执行；失败的 `agent()` 被记成成功的 `None` 并原样回放。
- **修复**（批 3）：journal 改为以请求为键的缓存：一次调用得到本次运行尚未用过的、对同一请求最早的记录，与记录顺序无关；未命中不改磁盘；失败不入 journal，所以 resume 会重试。
- **验证**：`test_journal_replay.py`；变异检查（与 P7-06、P7-10 合计 33 个变异体）。
- **规格**：Phase 7 规格 §14。
- **残留**：见第五节 7。

### P7-06. [x/~] 前台 workflow 不响应取消

- **问题**：实测 t = 0.2 s 触发 abort，工具在 3.0 s 才返回，其间子 agent 继续消耗 token。
- **修复**（批 3）：运行与 abort 信号竞速；取消时给运行最多 10 秒收尾（之后放弃并记 warning），移除它的 worktree，工具返回「已取消」及 `run_id` 与 resume 方法（已完成的 agent 已写入 journal）；已经 abort 的信号不启动任何东西；`parallel()` / `pipeline()` 被取消或某个分支抛异常时不再留下孤儿任务。后台运行有意不绑定到发起它的 turn。
- **验证**：`test_workflow_cancellation.py`；变异检查。
- **规格**：Phase 7 规格 §13（取消）。
- **残留**：见第五节 8。

### P7-07. [x/~] 会话生命周期与后台结果的注入方式

- **问题**：后台 workflow 运行中收到 `close`，`close()` 阻塞等它跑完（生产上最多 30 秒），随后 `trigger_prompt` 在已关闭的会话上发起一次新的 LLM 调用；`pi/session/delete` 不调用 `close()`；后台结果作为 user 消息进入会话，网页内容、子 agent 输出于是变成 user 角色。
- **修复**（批 3）：
  - `AgentHarness.close()` 中止在途的 turn，跑完所有清理回调（其中一个失败不影响其余），并停止 harness 为扩展启动的 turn（`CLOSE_GRACE_S`，5 秒，之后取消）；此后 `prompt()` 抛 `AgentHarnessError("invalid_state")`，扩展也不能再启动 turn；`close()` 幂等，新增 `closed`。
  - `WorkflowManager.close(timeout=5)` 取消后台运行并等待收尾，5 秒后仍不停的运行会被放弃并记 warning；关闭之后不再投递任何结果。
  - ACP `session/close` 与 `pi/session/delete` 都走 `close()`。
  - 后台结果改用新的 `HarnessBridge.trigger_message(custom_type, text, *, details=None)`，以带类型的 `workflow-result` custom 消息投递（不会被当作斜杠命令，`details` 里有 `runId` / `name` / `status`），输出包在 `<workflow-output boundary=...>` 信封里：边界是输出无法包含的随机串，前面说明这是不可信的数据、不是指令。遇到已在运行的 turn 时并入该 turn，不丢弃。
- **修复**（批 6，turn 之外到达的 prompt）：后台结果触发的 turn 没有 `session/prompt` 在背后，客户端不知道会话被占着，期间发来的 prompt 以前被 `busy` 拒绝。现在 `session/prompt` 等该 turn 结束再运行；`session/cancel` 或关闭会话结束等待（`cancelled`）；同一客户端并发的第二个 `session/prompt`（不论前一个在运行还是在等待）仍是 `busy`。ACP 的 `state_update`（idle / running）**刻意不发**：它只存在于协议 v2（不稳定的破坏性草案：`session/prompt` 改为受理即返回，回合结束由 idle 的 `state_update` 报告），Python SDK 与 TUI 所用的 Rust crate（`agent-client-protocol-schema` 0.11.4，`SessionUpdate` 既无此变体也无兜底变体）都读不了它，在 v1 会话里发出去会被拒收；本 agent 对 `initialize` 总是回答不大于 1 的版本。`test_state_update_is_not_a_session_update_the_sdk_can_read` 是个绊线：SDK 学会它之后会失败，提醒重新评估。
- **验证**：`test_harness_lifecycle.py`、`test_workflow_lifecycle.py`、`test_session_lifecycle.py`；53 个变异体全部被杀。批 6：`test_background_turns.py`（10 个用例），15 个变异体全部被杀。
- **规格**：Phase 7 规格 §12（Bridge 扩展）、§13（结果交付、不可信输出的信封、生命周期、turn 之外的 prompt）。
- **残留**：见第五节 9、10。

### P7-08. [x] 扩展 hook 可以覆盖权限拒绝

- **问题**：`_emit_hook` 取最后一个非 `None` 结果，ACP 权限层最先注册、扩展 handler 之后追加：实测权限层拒绝 `write` 之后，扩展 handler 返回 `{}` 或 `{"block": False}` 就能让被拒工具照常执行。
- **修复**（批 1）：`tool_call` 分发遇到第一个 block 即停止，与上游 `emitToolCall` 一致。

### P7-09. [x/~] `fetch_url`：SSRF、无体积上限、正则退化

- **问题**：跟随重定向到任何地址（云元数据服务、`http://localhost:8080/`）；`resp.text` 无上限；HTML 清洗正则在 1000 / 2000 / 4000 / 8000 个 `<script>` 上耗时 37 / 157 / 598 / 2391 ms（每翻倍约 ×4）且在事件循环上同步执行；另外截断提示会触发 `AttributeError`（`TruncationResult` 没有 `outputLines`），所以超过 2000 行或 50 KB 的页面直接返回 `Failed to fetch ...`。
- **修复**（批 3）：只取公网上的 `http(s)`：环回、私有、link-local、CGNAT、组播、保留地址不论怎样书写（`127.1`、`2130706433`、`[::ffff:7f00:1]`、6to4、NAT64）都拒绝，`localhost`、`*.local`、`*.internal`、单标签名、带凭据的 URL 也拒绝，重定向目标按同样规则检查（手动跟随，最多 5 跳）。无代理时名字只解析一次，所有答案必须是公网地址，连接到检查过的地址，`Host` 与 TLS 服务器名仍是原名；有代理时只能拒绝字面地址与本地名。响应体用 `client.stream()` 读取，最多 5 MiB；整个调用（含重定向与转换）最多 30 秒；HTML 由线性扫描器在工作线程里转换；被截断时说明原因。没有 opt-out。
- **验证**：`test_fetch_url_safety.py` 的 276 个用例（含回环 socket 的用例；整个 `pi-web-access` 包共 290 个）；120 个变异体全部被杀。
- **规格**：Phase 7 规格新增 §5.3。
- **残留**：见第五节 11。

### P7-10. [x/~] Worktree 隔离

- **问题**：`collect_diff` 用 `git diff HEAD`，不含未跟踪的新文件；diff 经过 `decode(errors='replace')` 再 `encode`，GBK 文件的修改回写时报 `patch does not apply`；快照没有 `--binary`，一个二进制文件就让整个快照失败；子 agent 提前失败、超时、被取消或在后台运行时 worktree 泄漏。
- **修复**（批 3）：`git add -A` + `git diff --binary --cached HEAD`，全程用 `bytes`；每次调用在 `finally` 里删除自己的 worktree；前台运行在 `finally` 里 `cleanup_all()`，后台运行结束时清理。
- **验证**：`test_worktree_isolation.py`（真实 git worktree）；变异检查。
- **规格**：Phase 7 规格 §15。
- **残留**：见第五节 7。

### P7-11. [x/~] goal-x

- **问题**：全部步骤完成的那个 turn 不保存，`goal_complete` 也不保存，会话恢复后目标又变回活动状态；`update_step` 遇到越界索引追加 `Unnamed step` 并报告成功；状态列表从 1 开始编号而 `step_index` 从 0 开始；模型忽略提醒时每一轮都再提醒（harness 与 CLI 默认没有回合上限，会一直提醒到用户中断；审计复现时配了 `max_turns = 12`，实测打满 12 次 LLM 调用后以 error 收场）。
- **修复**（批 3）：每次变化都保存（`/goal`、`goal_update`、`goal_complete`，没有变化的 turn 不写）；越界或为负的索引抛 `ValueError` 并列出现有步骤，新步骤必须有描述；状态列表改成 `[0]`、`[1]` …；完成提醒最多 2 次（`MAX_COMPLETION_REMINDERS`），重新打开某个步骤或开始新目标时重新计数。
- **验证**：`pi-goal-x` 包共 53 个用例；44 个变异体全部被杀。
- **规格**：Phase 7 规格 §6.1。
- **残留**：见第五节 12。

### P6-05、P6-06、P7-12 至 P7-16（低危）

规格：Phase 6 规格 §4.1（P6-05）、§4.5（P6-06）；Phase 7 规格 §4.5（P7-12、P7-13、P7-15、P7-16）、§12 与 §16（P7-14、P7-13）。

| ID | 修复（批 3） | 验证 |
|----|--------------|------|
| P6-05 | 尚无提交的仓库回退到 `git symbolic-ref --short -q HEAD`，显示 `Branch: main (no commits yet)`；连这个也失败时才省略该段 | `test_git_context.py` |
| P6-06 | README、`agent.example.toml`、Phase 6 规格说明这个块会随每个请求发给 LLM 提供商，以及如何关闭（`[git] enabled = false`、`--no-git-context`）。**默认仍然开启**，审计建议的「首次启动提示」与「私有仓库默认关闭」没有采纳 | 文档 |
| P7-12 | 模块级 `activate` 仍以模块名命名，其他 callable 命名为 `module.qualname`；`AgentHarness.load_extension(activate, *, name=None)` 可显式命名。名字冲突**仍然替换**（重新加载与「项目覆盖用户」依赖它），但替换成不同 callable 时记 warning——审计建议的「冲突时报错」没有采纳。加载失败（导入错误、入口点解析为空、`activate()` 抛异常）记录在 `ExtensionLoader.failed` / `AgentHarness.failed_extensions`，并通知用户（ACP agent 消息，headless stderr），其他扩展照常加载 | `test_extension_loading_rules.py`、`test_extension_notices.py` |
| P7-13 | 新增 `pi_agent_core.home.pi_home`（参数、`$PI_HOME`、`~/.pi-python`），CLI、`ExtensionLoader(home=)`、`AgentHarness(home=)`、`ExtensionAPI.home`、`WorkflowStore(pi_home=)`、`create_workflow_tool(home=)` 共用 | `test_home.py`、`test_home_and_availability.py` |
| P7-14 | 构造不出子 agent 时注册 `UnavailableSubagentExecutor(reason)`：`workflow` 工具回答「sub-agents are unavailable (<reason>)」并记 warning，不再拿模拟答案冒充结果；`MockSubagentExecutor` 只剩测试替身的用途 | `test_home_and_availability.py` |
| P7-15 | 三个扩展包的 `pi-agent-core-lc` 下限提到 `>=0.4.0`；`pi-dynamic-workflows-py` 声明 `pi-agent-harness-lc`。新测试检查扩展导入的每个工作区发行版都已声明、下限等于本仓库版本、`[tool.uv.sources]` 指向工作区 | `test_extension_packaging.py` |
| P7-16 | `register_tool` 校验 `[a-zA-Z0-9_-]{1,128}`，拒绝时指出扩展名与规则，并像其他失败一样回滚该扩展 | `test_extension_loading_rules.py` |

---

## 四、规格对账与系统性缺口

审计发现文档承诺与实现相反的地方，已全部对齐（改实现或改文档）：

| 位置 | 原承诺 | 现状 |
|------|--------|------|
| Phase 6 规格 §1.1 / §1.3 / §4.3、README、CHANGELOG 0.4.0 | git 段每个 turn 刷新 | 明确为会话开始时的快照，块内有说明（P6-02） |
| Phase 6 规格 §4.1 | git 失败时省略该段，不出现错误堆栈 | 编码错误不再让 turn 失败（P6-01） |
| Phase 6 规格 §5.2 | `deepseek` 行必须看到 thinking | 行改用 V4 模型，适配器发出 `thinking`（P6-03） |
| `pyproject.toml` 的 `real_llm` 说明；Phase 6 规格 §5.1 | 前者写「默认排除」但没有配置；后者写「默认 CI 会收集这些测试，缺密钥时 skip」 | `addopts` 真正排除，两处说明改成同一说法（P6-03） |
| Phase 7 规格 §10.1 | 扩展工具的 snippet / guidelines 自动进入 system prompt | CLI 路径生效（P7-04） |
| Phase 7 规格 §9.3 | workflow 脚本在受限命名空间中「沙盒」执行 | 批 1–2 不再称沙盒，并把它的工具调用放进权限链；批 5 把脚本换到独立进程并加内核限制，`cwd` 限制在项目内，规格 §9.3 与 §12 改写，并说明这仍不是对抗恶意脚本的边界（P7-01） |
| Phase 7 规格 §3.5 | `tool_call` 是唯一具有阻塞能力的事件 | 首个 block 短路，与上游一致（P7-08） |
| `AUDIT-PHASE7-EXTENSION-API.md` 的最终判定 | 10 个高影响、20 个中影响问题全部关闭 | 该文件全文没有沙盒、信任、权限、SSRF 相关条目（关键字检索无匹配），本文件补上。它的已记录限制 1（未跟踪的文件不会回写 worktree）已被 P7-10 取代：新文件现在会回写；限制 2（结果遇到运行中的 turn 时并入该 turn）仍然成立，交付方式由 P7-07 改成带类型的 custom 消息。旧文件只在开头加了一条编号说明 |

2026-10-08 的复核另外发现三处规格与实现没有对齐（第八节）：Phase 7 规格 §4.4 的受门约束资源表与 §16 都没有项目级 saved workflows，实现与规格一致，是共有的缺口（F7-01）——批 8 把实现与规格一起补上（§3.2、§4.4、§16）；另外两处还没改：§3.5 的事件表把 `session_start` 映到 `before_agent_start`，与 §4.3 自相矛盾，实现取一次性的 `session_start`（F7-07）；§14 写 `hash_request("agent", {prompt, opts})`，实际只哈希 `opts` 的子集（F7-09）。

### 系统性缺口的收口情况

| 审计时归纳的缺口 | 现状 |
|------------------|------|
| 没有信任 / 能力模型：只定义了发现与覆盖，没有回答「谁的代码可以运行」「嵌套调用如何过权限」；权限是按工具名的白名单 | 项目信任（P7-02：ACP 里询问，回答绑定到文件内容）与子 agent 的权限继承（P7-01）已补上；权限模型由按工具名的白名单改为默认询问、按工具自己声明的 annotations 放行（批 6；提示是作者的一面之词，第五节 3） |
| 特性之间没有对账：提示缓存与每 turn 刷新；tier 路由与 provider / key；`trigger_prompt` 与 `close()` 及 ACP turn 模型 | 三处都已对账：git 段标注为会话快照（P6-02）；tier 与 key 按 provider 限定（P7-03）；`close()` 之后不再开启 turn，后台结果改用 custom 消息（P7-07） |
| 「忠实移植」在扩展语义上有偏差：`tool_call` 不做首个 block 短路、嵌套调用不过钩子、没有 `project_trust` | 首个 block 短路（P7-08）；子 agent 的调用过钩子（P7-01）；`project_trust`（P7-02：有提示与内容指纹，无 `/trust`） |
| 失败语义不统一：有的静默降级（Mock 回退、journal 缓存 `None`、`suppress(Exception)`），有的硬失败（git 编码） | 不再退回 Mock（P7-14）；失败不入 journal（P7-05）；git 失败省略该段（P6-01）；扩展加载失败对用户可见（P7-12）。其他位置的 `suppress(Exception)` 不在本次修复范围 |

### 审计确认没有问题的部分

- 扩展 API 的表面小而清晰，`HarnessBridge` 把核心库与 harness 隔开，发现顺序与覆盖语义与上游一致。
- hook 抛异常时 fail-closed（`_prepare_tool_call` 把异常转成错误结果），与上游「`tool_call` handler 失败即阻止工具」一致。
- Phase 6 的 git 段只读、有界、可关闭，且不写入 JSONL，不会把过期 diff 固化进会话。
- 矩阵按数据行驱动，缺密钥自动 skip，无密钥环境下不会失败。
- 2026-10-08 另一个会话按规格逐节核对了当前代码，独立确认上面各批的修复都在位，且没有高危问题（第八节 8.1）。

---

## 五、已记录的残留与限制

| # | 关联 | 内容 |
|---|------|------|
| 1 | P7-01 | 脚本现在跑在独立进程里并受内核限制（批 5），但审计钩子是 Python，写给它的脚本能把它拆掉，所以这仍**不是**对抗恶意脚本的安全边界。钩子被绕过后：Linux 上脚本能读用户可读的一切文件（含宿主与同用户其他进程的 `/proc/<pid>/environ`，没有 Yama 时还能 `ptrace` 宿主、读它的内存）、截断 / 删除 / 改名文件（写不了内容）、联网、杀宿主；Windows 上能读文件、联网、杀宿主（写文件、起进程、读宿主内存、复制宿主句柄都被拒）。root 不受 `RLIMIT_NPROC` 约束；Windows 的 CPU 限制检查很粗（1 s 的限制约 8 s 才生效，墙钟更及时但默认关闭）；macOS 没有验证。没做：Landlock 或用户 / 挂载命名空间（隔离 Linux 的文件系统与 `/proc`；本机 WSL 内核 5.10 没有 Landlock，无法验证）、seccomp、Windows 的 AppContainer / 受限令牌（后两者按机制推断会很脆弱，没有尝试）。要对抗这类脚本，请在容器里运行 agent。详见沙盒规格 §9 |
| 2 | P7-01 | 子 agent 的 `cwd` 已限制在项目内（批 5）。残留：`isolation=True`（worktree）时子 agent 在 worktree 根运行，`cwd` 的子目录被忽略；检查与使用之间的符号链接竞态（TOCTOU）：脚本自己不能写文件，要改只能经受权限策略约束的子 agent |
| 3 | P7-01 | `auto` / `always-approve` 模式下，子 agent 可以做会话允许的一切；没有 gate 的 executor（独立使用，或 bridge 没有 `tool_call_gate`）里的子 agent 不受限，`activate()` 只记一条 warning。权限模型已按审计的建议改为「默认需授权、按 annotations 放行」（批 6），残留：annotations 是工具作者的一面之词，运行时不验证；`agent.toml` 没有按工具放行的配置，想放行一个没声明的第三方工具只能补上 annotations 或改用 `auto`；子 agent 的调用按父会话里同名工具判定，父会话没有的名字要问；CLI 不接 MCP 服务器（`session/new` 的 `mcpServers` 被忽略），所以 MCP 工具的提示目前不会被读到（MCP 规范要求把不受信任的服务器的 annotations 当作不可信；`from_langchain_tool` 原样转交，库用户自己接入不信任的 MCP 服务器时要先去掉这些提示）；`ask` 模式下不声明 annotations 的扩展工具会让用户被询问（这是有意的行为变化）；扩展工具与内置工具重名时，harness 查到的是扩展的那一个，所以按它自己声明的 annotations 判定，而重名覆盖目前不留任何日志（F7-06，第八节） |
| 4 | P7-02 | 授权以整个项目目录为单位，不区分单个扩展或资源；保存的回答只适用于精确目录，不像白名单那样覆盖子目录；没有 `/trust` 命令，也没有「对此项目永不信任」的持久化（收回信任要手工删 `trust.json` 里的条目）；指纹只在会话开始时核对（加上对话框关闭后复核一次），会话进行中再改文件不会撤销信任；换行符转换（`core.autocrlf`）会改变内容哈希而触发重新询问；客户端可以自动批准 `session/request_permission`（ACP 允许；问题没有 `allow_once` 以避开 TUI 的 YOLO，但 TUI 按上次确认的选项类型粘性预选光标，上次选 “always” 类时高亮的就是「Trust and remember」）；客户端丢弃请求时，取消被当作拒绝；TUI 自己的目录信任（`/hooks trust`）不适用于此；`[extensions]` 这个配置名同时管所有项目资源（扩展、saved workflows、提示文件、skills）。（批 4 之前这一条还包括「按路径而非内容」「没有交互式提示」，已解决） |
| 5 | P7-02、F7-01 | `AGENTS.md` / `CLAUDE.md`（上游不论信任与否都会加载）、`agent.toml` 里直接给出的 prompt、绝对路径或 `~` 开头的 skills 路径、用户自己的 `<pi home>/workflows` 不受门控。项目级 saved workflows（`.pi-python/workflows/`）曾是**缺口，不是取舍**，批 8 起受门控（F7-01，第八节）。门由读取方遵守：harness 管得住自己加载的扩展，管不住扩展加载之后自己去读什么；dynamic-workflows 扩展读 `pi.project_trusted`，第三方扩展若自己读 `<cwd>/.pi-python/…` 而不看它，没有机制拦它（根目录 `AGENTS.md` 的第 9 条不变量写明了这条规矩）。从没保存过 workflows 的回答升级上来的项目，只要 `.pi-python/workflows` 里有 `*.py`，已保存的回答就随指纹失效，会再问一次（宁可多问） |
| 6 | P7-02 | 项目扩展一旦被信任，就是完整的进程内代码执行 |
| 7 | P7-05、P7-10 | journal 重放不会重做「请求没变的步骤」曾对工作区做的事（比如改过的文件）；worktree 回写失败（补丁与源目录此刻的状态冲突）时，该子 agent 的改动丢失；快照不复制源目录里未跟踪的文件 |
| 8 | P7-06 | 不合作的运行（脚本吞掉取消）在 10 秒宽限期后被放弃并记 warning：它仍在后台跑到自己结束，但不再交付结果 |
| 9 | P7-07 | custom 消息在 LLM 层仍是 user 角色，信封是文本约定，不是协议级隔离；它只进会话与 LLM 上下文，不进 `session/load` 的历史回放 |
| 10 | P7-07 | 后台结果触发的 turn 没有对应的 `session/prompt` 请求：ACP v1 没有禁止 turn 之外的 `session/update`，但客户端未必渲染；v2 草案的 `state_update`（idle / running）刻意不发（只存在于协议 v2，SDK 与 TUI 的 crate 都读不了，见 P7-07 的批 6 修复）；期间到达的 `session/prompt` 改为等待该 turn 结束（批 6）。客户端（尤其是 Rust TUI，这里无法运行它）怎样渲染 turn 之外的 `session/update` 仍未验证；等 SDK 与客户端支持 v2 之后重新评估 |
| 11 | P7-09 | 走代理时，代理把公开域名解析到私网地址拦不住，也没有「校验的地址即连接的地址」的保证（需要更强保证就在代理一侧限制出口，或不设代理走直连）；工具不响应 turn 的 abort 信号，靠 30 秒总时限兜底，超时后 `trafilatura` 的工作线程会自己跑完；服务端无视 `Accept-Encoding: identity` 发压缩正文时，一次读取的 64 KiB 压缩数据最多瞬时膨胀到约 64 MiB，之后被 5 MiB 上限截断 |
| 12 | P7-11 | 提醒次数只在本进程内计，不随状态保存 |
| 13 | P6-03 | 官方 DeepSeek 上开启思考且带工具时回传 `reasoning_content`（批 6）与 CLI 的 `Model.reasoning` 接线都**没有对真实 API 验证**（没有密钥；矩阵 `deepseek` 行的 `thinking_tools` 用例是第一次真实检验）。请求的形状取自 DeepSeek 的文档与样例；测试走已安装的 `langchain-deepseek` 真实的请求构造路径（`_get_request_payload`）与一次完整的工具往返（伪造的只是网络那一端）。网关上的 DeepSeek 模型（SiliconFlow、vLLM 等）刻意不回放：它们各自的要求不同，也没有验证 |
| 14 | P7-15 | 扩展的版本下限必须随核心发布一起上调（`test_extension_packaging.py` 会拦住不同步的情况）。批 7 把核心、`pi-agent-harness-lc`、`pi-agent-cli-lc` 升到 0.5.0，三个扩展包的下限同步成 `>=0.5.0`，并把扩展包自己的版本从 0.1.0 升到 0.2.0（发布工作流的 PyPI 步骤带 `skip-existing`，版本号不变就会让 PyPI 上已有的旧构建原样留着）；六个包都用 `uv build` 构建过，wheel 元数据的版本与依赖符合预期。**只做了本地提交：没有打 tag、没有发布、没有推送**；0.5.0 这个版本号与扩展包的 0.2.0 是按惯例选的，发布者可以改。今后每次发布都要重复这一步。另外 ACP 的 `agentInfo.version`（`agent.py` 的 `_AGENT_INFO`）一直硬编码为 `0.1.0`，与包版本早就对不上，这次没有改 |
| 15 | P7-03 | 模型 id 写成 `provider/model`，按第一个 `/` 拆分，对自身含 `/` 的网关模型 id（如 `Qwen/Qwen3-8B`）有歧义，需写成 `<provider>/Qwen/Qwen3-8B` |
| 16 | 编号 | 本文件与 `AUDIT-PHASE7-EXTENSION-API.md` 的编号重名，见文件开头的说明 |

---

## 六、验证方法与结果

- **先红后绿**：每项修复的回归测试在修复前必须失败。批 1 共 52 个，批 2 含经真实 ACP agent / 真实 workflow 扩展 / 真实 git worktree 的端到端用例，批 3 每一簇都先写测试再改实现，批 4 同样：ACP 流程用真实的 `PiAcpAgent` 加脚本化的客户端（能应答、延迟应答、抛错、丢请求、报错），headless 用真实的 `run_print`，指纹与存储用真实的文件系统（含符号链接、循环、命名管道、损坏的文件）。批 6 同样：权限模型用真实的 `PiAcpAgent` 加脚本化的客户端，端到端地检查问或不问（含 workflow 子 agent 的调用）；turn 之外的 prompt 用真实的 harness 和一个可以被扣住的假模型；DeepSeek 回放走已安装的 `langchain-deepseek` 真实的请求构造路径。批 8 同样：ACP 流程用真实的 `PiAcpAgent`、真实的 dynamic-workflows 扩展（入口点发现换成指向它的列表）、脚本化的客户端与临时的项目目录，指纹、提示、通知、store、扩展注册、API 门面、harness 的桥各有一组；修复前 90 个失败，修复后 0 个。
- **手工变异检查**：改坏实现的一处，跑对应测试，看有没有被抓住；幸存的变异体要么补测试、要么证明是等价变异。批 1 至 3 的行是在批 3 提交前的最终代码上重跑的结果（重跑时有 5 个变异体的匹配模式因后来的重构或格式化失效，已更新后单独重跑，均被杀）；批 4 的行是在批 4 最终代码上把全部变异体整套重跑的结果：有 5 个变异体的匹配模式因后来的重构或格式化而失效（取消标志与 `waiting` 计数的旧设计 2 个、`_decide_trust` 抽出之前的写法 1 个、旧的悬空链接标记 1 个，都已按新代码换成对应的变异体；「空扩展目录也算受门约束」1 个因格式化换行而更新了写法，被杀）；整套重跑时另外补了一类「遍历提前结束」的变异体（在悬空链接、`.git` / `__pycache__`、符号链接环、用户级 skills 条目处把 `continue` 换成 `break`），其中「悬空链接」一个幸存，说明没有用例保证排在它后面的文件仍被指纹覆盖（仓库自己起文件名，可以借此让后来改动的文件不影响指纹），已补 `test_nothing_in_a_directory_hides_the_files_listed_after_it`（强制两种目录列出顺序）与 `test_a_skills_entry_after_the_users_own_is_still_gated`，随后全部被杀。

  | 簇 | 变异体 | 结果 |
  |----|--------|------|
  | P7-02 信任判定 | 14 | 全部被杀 |
  | P7-02 提示文件与 skills | 14 | 全部被杀 |
  | P6-04 / P6-05 git | 9 | 全部被杀 |
  | P7-12 至 P7-16 扩展 | 50 | 全部被杀 |
  | P7-05 / P7-06 / P7-10 workflow | 33 | 全部被杀 |
  | P7-07 生命周期 | 53 | 全部被杀 |
  | P7-09 `fetch_url` | 120 | 全部被杀 |
  | P7-11 goal-x | 44 | 全部被杀 |
  | P6-03 DeepSeek 思考开关 | 35 | 全部被杀 |
  | P6-03 矩阵配置 | 11 | 10 被杀；1 个无法自检（见 P6-03） |
  | P7-02（批 4）信任存储 `trust_store` | 20 | 全部被杀 |
  | P7-02（批 4）指纹 `trust_fingerprint` | 26 | 全部被杀；「非常规文件也被读取」只能在 POSIX 上被杀（Windows 没有命名管道），在 WSL 里验证 |
  | P7-02（批 4）判定 `decide_project_trust` | 20 | 全部被杀 |
  | P7-02（批 4）ACP 流程 `agent.py` | 52 | 全部被杀；「说明里写的是未解析路径」在 Windows 上是等价变异（符号链接用例在 Windows 上跳过），在 WSL 里被杀 |
  | P7-02（批 4）提示与说明 `trust_prompt` | 42 | 全部被杀 |
  | P7-02（批 4）接线（factory、prompt_options、headless、harness、通知、config） | 50 | 全部被杀 |
  | P7-01（批 5）子 agent 的 `cwd` 约束（`paths`、执行器、运行时衔接） | 15 | 全部被杀；「NUL 字符」在 POSIX 上是等价变异（见下），在 Windows 上被杀 |
  | P7-01（批 5）沙盒宿主 `sandbox/host.py` | 45 | 全部被杀；`-X utf8` 在 POSIX 上是等价变异（见下），在 Windows 上被杀 |
  | P7-01（批 5）沙盒子进程 `sandbox/child.py` | 39 | 全部被杀 |
  | P7-01（批 5）Windows Job Object `sandbox/winjob.py` | 3 | 全部被杀 |
  | P7-01（批 5）运行时衔接 `runtime.py` | 13 | 全部被杀 |
  | P6-03（批 6）DeepSeek 思考回放 `deepseek_replay` | 15 | 全部被杀 |
  | P6-03（批 6）转换与流式适配器 `langchain_convert` / `langchain_stream` | 11 | 全部被杀 |
  | P6-03（批 6）CLI 的 `Model.reasoning` 接线（`config`、`factory`） | 11 | 全部被杀 |
  | P7-01（批 6）权限模型：annotations 字段、LangChain 转换、harness 查询、CLI 判定、随包工具的声明 | 35 | 全部被杀（首轮幸存 1 个等价变异，见下） |
  | P7-07（批 6）turn 之外到达的 prompt（`agent.py`） | 15 | 全部被杀 |
  | P7-17（批 8）一个坏的 `meta` 不拖垮会话：store 清洗、`register_command` 校验、逐个捕获与扫描失败的隔离、`_advertise_commands` 跳过与告警文字 | 9 | 全部被杀（首轮幸存 1 个，见下） |
  | F7-01（批 8）扩展一侧的门：`_project_trusted`、`ExtensionAPI.project_trusted`、store 的 `include_project`、保留的命令名 | 8 | 全部被杀 |
  | F7-01（批 8）harness 的桥 `project_trusted` | 2 | 全部被杀 |
  | F7-01（批 8）CLI 一侧的门：指纹、提示与通知文案、判定与通知的 `home`、ACP 与 headless 的接线 | 14 | 全部被杀（首轮幸存 1 个，见下） |
  | 合计 | 828 | 827 被杀 |

- **批 5 的变异检查**：沙盒的变异体在 Windows（87 个）与 WSL（89 个）上各整套跑了一遍，其中 76 个两边相同；Windows 另有 11 个（Job Object、低完整性、Windows 的环境清理与启动器），WSL 另有 13 个（各项 rlimit、`NO_NEW_PRIVS`、信号名、POSIX 的环境清理），所以上表的 100 个是两边的并集。`cwd` 的 15 个也在两个平台上跑过：Windows 上全部被杀，WSL 上 14 个被杀，剩下的「NUL 字符」是 POSIX 上的等价变异（POSIX 的 `realpath` 自己就因 NUL 抛 `ValueError`；Windows 的则原样返回，所以那里需要显式的检查）；其中运行时衔接的 5 个在沙盒重写运行时之后按新代码重新写过（旧写法已经匹配不上，运行时报告的是 SKIP，不是幸存）。Windows 首轮 87 个里幸存 13 个，全是用例的毛病，不是实现的：① 审计钩子名单里 `shutil` / `socket` / `subprocess` / `ctypes` 掉出去没有人察觉——原有的「连接服务器」用例其实是被 `create_connection` 途中加载 `idna` 编码时的 `open` 拦下的，并没有碰到 `socket` 本身；`shutil`（最终要 `open`）与 `subprocess`（最终是 `_winapi.CreateProcess`）身后各有第二道墙，更不会被察觉。补了逐条列出策略的用例（每个被拒的事件族和被拒的导入各一例，再各一个放行的反例），连接服务器改成直接 `socket().connect`；② 「任意函数名都能调用」的用例用的调用 id 恰与随后那次 `agent()` 撞车，被「重复 id」的同一句错误提前拦下；日志字符上限的用例里没有任何一行跨在上限上；③ 预算视图的「恰在总额处就算用尽」「剩余不为负」没有用例；④ `parallel()` 里有 thunk 起不来时，已经起了的任务应当被取消，而不是留下来继续向宿主发请求；⑤ 经 `WorkflowRuntime` 的用例没有检查脚本拿到的 `cwd` 与预算总额（新用例连 `args` 一并检查），也没有检查 phase 只列一次；⑥ 系统拒绝降低完整性级别时，不该把它记为一层（用一个换掉 `SetTokenInformation` 的一次性解释器来测）。逐项补了用例之后这 13 个都被杀。WSL 上 89 个里 88 个被杀，幸存的是 `-X utf8`：脚本进程的环境是清空的，区域设置成了 C，Python 本来就会启用 UTF-8 模式，所以它在 POSIX 上是等价变异；在 Windows 上它被杀（stderr 里的中文要按 UTF-8 读出来）。WSL 上有 4 个变异体把用例挂住、由超时判死（调用 id 可以重复、已完成的调用留在在途表里、结束时不取消在途调用、stdin 没有指向空设备）；为了让这类回归变成失败而不是挂住整套用例，沙盒用例的 `run()` 在没有别的限制时给 120 s 的墙钟（重跑其中两个，已改为用例失败）。另外还有一项不是变异体发现的：随包带的五个工作流（`deep-research` 等）原先只检查过「是合法的 Python」，从没在沙盒里跑过；现在 `test_the_bundled_workflows_run` 逐个跑它们（去掉命名空间里的 `zip` 就会失败），并要求新增的内置工作流在该表里登记参数。
- **批 6 的变异检查**：87 个变异体都在最终代码上整套跑过（思考回放与 `Model.reasoning` 接线 37、权限模型 35、turn 之外的 prompt 15）。思考回放有 3 个变异体的匹配模式因后来把条件拆成 `thinks` 变量而失效（运行时报告 SKIP，不是幸存），按新代码重写后单独重跑，均被杀。权限模型首轮 35 个里幸存 1 个，「harness 不复制工具的 hints 就放进事件」，它是等价变异：事件是 pydantic 模型，校验时本来就会复制字典字段（实测：事件上的字典不是工具持有的那个对象，改它不影响工具，`MappingProxyType` 也被接受），显式的 `dict(hints)` 是多余的，已删掉改成一行注释，并换成「不查是否为映射就放行」等变异体，全部被杀。权限模型的变异体覆盖：每个只读内置工具与每个会改东西的工具（声称只读）、三种工具类型各自缺字段、LangChain 转换（不传、任意类型都取、丢掉某个提示、不查 metadata）、harness 的查询与映射检查、判定规则的每一处（`and` 换 `or`、真值代替 `is True`、只查一个提示、`auto` / `always-approve` 询问、`ask` 从不 / 总是询问、事件上的 annotations 被忽略）、随包五个工具各自的声明（`workflow` 声称只读、`goal_*` 声称联网）。turn 之外的 prompt：总是 / 从不 `busy`、阈值、计数泄漏与残留的零项、取消与关闭会话被忽略、不等空闲、每次新建取消事件、吞掉非 `busy` 的错误、`busy` 判反、`cancelled` 映射成 `end_turn`、重试前不等待、`initialize` 声称请求的版本。
- **批 8 的变异检查**：33 个变异体都在最终代码上整套跑过（用脚本逐个改坏一处，只跑对应的测试，无论结果怎样都把文件字节级还原；Windows）。覆盖：store 对 `description` 与 `when_to_use` 的清洗（各一个），`register_command` 的类型检查，`_advertise_commands` 的跳过，逐个捕获，扫描失败的隔离，告警文字（写 pydantic 的原文、什么都不写、其他失败不带类型），`_project_trusted`（总是信任、读不到时放行、真值代替 `is True`），`ExtensionAPI.project_trusted`（真值代替 `is True`），保留的命令名（去掉 `workflows`，整个检查去掉），store 的 `include_project`（总是读项目目录、默认值反过来），harness 的桥（总是信任、从不信任），指纹（用户目录算作项目的、与脚本同名的目录当作脚本、忘记资源种类），资源的顺序，workflows 不受门控，`home` 在四处被丢掉（判定、`skipped_project_resources`、ACP 通知、headless 通知，各一个），文案（通知里没有 workflows 的那一句、说明里没有 workflows 的作用、标题里 workflows 换了名字），通知的过滤条件反过来，headless 不报告被跳过的资源。首轮幸存 2 个，都是用例的毛病，不是实现的：① 「扫描失败被 `except` 包住」换成只捕获别的异常类型没有被发现（那个 `try` 批 8 之前就在，批 8 把 store 的构造与信任的读取也放进了它里面；没有用例让 `scan()` 抛错），补了 `test_a_scan_that_fails_leaves_no_saved_workflow_commands_and_says_so`；② headless 的「被跳过的资源」少传 `home`：整个 CLI 的用例目录（650 个）里没有一个从「家目录就在项目里」起的 headless 运行，补了该用例，并补了 headless 报告被跳过的 workflows、承认与不承认已保存的回答各一个。另外，写变异体时发现告警里的原因（`_why_invalid`）没有用例钉住，在跑那三个变异体之前先补了两个（字段名在、值不在日志里；其他失败按类型与文字报告）。
- **探针重跑**：审计时用探针脚本复现问题，修复后重跑，行为已变成预期（探针是临时脚本，没有入库；对应的回归测试入库）。
- **跨平台**：Windows 与 WSL 各跑一遍全量。WSL 首跑暴露了 4 个失败：这台机器的 WSL 导出了 `PI_HOME`，让「把 `Path.home()` 指向临时目录」的测试读到真实目录（P7-13 让加载器也认 `PI_HOME` 之后才出现）。根目录 `conftest.py` 现在为每个测试清掉 `PI_HOME`，`test_home.py` 里有回归测试。批 4 里有 5 个用例只在 POSIX 上有意义（符号链接的显示路径、命名管道、权限位、非法文件名），Windows 上跳过、在 WSL 里跑；对应的两个只能在 POSIX 上被杀的变异体也在 WSL 里重跑过。根目录 `conftest.py` 现在还清掉 `PI_TRUST_PROJECT_EXTENSIONS`（同类泄漏：开发者为自己的项目导出它，所有「项目不受信任」的用例就会随 shell 而变）。批 5 的沙盒用例里有 5 个只在 POSIX 上有意义（Linux 的 rlimit 数值与 `RLIMIT_NPROC`、`prctl`、文件描述符上限、信号名），8 个只在 Windows 上有意义（Job Object、低完整性、驱动器号与 Windows 的路径规则），各在对应的平台上跑，其余在两边都跑；平台独有的变异体也只在对应的平台上跑（见上）。批 8 没有新增只在一边有意义的用例（两边的 skip 数与批 7 之后相同），它的变异体只在 Windows 上跑过（用例两边都跑）。
- **provider 文档**：DeepSeek 的模型名与下线日期、`thinking` / `reasoning_effort` 参数、`reasoning_content` 回传要求，以及 DeepSeek 为 pi 写的接入文档（`reasoningEffortMap`），都对照官方文档核对过；与真实 API 的对接没有验证（没有密钥）。
- **2026-10-08 的复核**：另一个会话读规格与代码做的符合性核对，没有改代码，也没有跑变异检查（沙盒内部不在范围内）。第八节的每条发现都对着当前 HEAD 的代码与规格原文重新核对过（只读，没有改代码）。F7-01 的同名顶替与 P7-17 用真实的 `PiAcpAgent` 和临时项目实测过（含对照实验）；F7-03、F7-05 另在 Python 3.12.7 上实测（没有运行中的循环：`DeprecationWarning`，在运行中的循环里返回同一个循环；手写的导入流程在模块抛错后留着 `sys.modules` 的条目，标准的 `import` 会清掉），并对照了 CPython 的文档。探针是临时脚本，没有入库；F7-01 与 P7-17 的回归测试随批 8 入库，其余的等修复时再写。测试与 lint 的复跑结果在开头的状态块里。

---

## 七、后续工作（未做，按价值排序）

1. ~~**信任的交互与内容哈希**~~：已在批 4 完成（P7-02；剩余的限制见第五节 4）。
2. ~~**子 agent 的 `cwd` 约束**~~：已在批 5 完成（P7-01；残留见第五节 2）。
3. ~~**workflow 真正的隔离**~~：独立进程加资源限制，已在批 5 完成（P7-01；钩子被绕过后脚本还能做什么见第五节 1）。接下来的加固方向：Linux 的 Landlock（或用户 / 挂载命名空间）隔离文件系统与 `/proc`，需要 ≥ 5.13 的内核才能验证；其后是 seccomp。
4. ~~**DeepSeek 的 `reasoning_content` 回放**，并在 CLI 里接入 `Model.reasoning`~~：已在批 6 完成（P6-03；仍未对真实 API 验证，第五节 13）。
5. ~~**默认需授权的权限模型**：按工具 annotations 放行只读工具~~：已在批 6 完成（P7-01；残留见第五节 3）。
6. ~~**ACP `state_update`**~~：调查后**决定不发**（它只存在于协议 v2，SDK 与 TUI 的 crate 都读不了），改为让 turn 之外到达的 prompt 等该 turn 结束（批 6，P7-07；第五节 10）。
7. ~~**发布时**：上调三个扩展包的版本下限~~：已在批 7 完成（核心 0.5.0，扩展包 0.2.0；只做了本地提交，没有打 tag、没有发布；第五节 14）。
8. ~~**项目级 saved workflows 纳入项目信任门**~~：已在批 8 完成（F7-01；第八节）。
9. ~~**一个坏的 saved workflow 不该拖垮整个会话**~~：已在批 8 完成（P7-17；第八节）。
10. **一次清理**（F7-02 至 F7-05，低；第八节）：`append_entry` 时同步更新 `get_custom_entries` 读到的缓存（或在文档里写明只有快照语义）；`run_async` 改用 `asyncio.get_running_loop()`；`register_command` 校验命令名（非空、不含空白、不以 `/` 开头），失败时像 `register_tool` 一样在 `activate()` 阶段回滚；`_load_module_from_file` 在 `exec_module` 失败时清掉 `sys.modules` 里的条目。每项各补一个先红后绿的测试。
11. **覆盖内置工具时留痕**（F7-06，低；第八节）：扩展工具与内置工具重名时记一条 warning；动态注入路径补发 `ToolsUpdateEvent`（可选）。
12. **改文档**（F7-07 至 F7-09，备注；第八节）：Phase 7 规格 §3.5 事件表里 `session_start` 的映射改成「加载后发一次」，与 §4.3 一致；`ToolDefinition.execute` 的 4 参调用契约写进 `ExecuteFn` 的文档串与规格 §3.4；§14 补一句 journal 哈希包含 `opts` 的哪些字段。

第 1 至 9 项都做完了；第 10 至 12 项（低危与文档备注）是 2026-10-08 复核新增的，批 8 只处理了其中的中危问题，它们还没有开始。其余仍然开放的是：第 3 项里写的加固方向（Landlock / 用户与挂载命名空间、seccomp，需要 ≥ 5.13 的 Linux 内核才能验证，本机 WSL 是 5.10）、用真实的 DeepSeek API 验证批 6 的回放（第五节 13）、以及 TUI 怎样渲染 turn 之外的更新（第五节 10）。

---

## 八、2026-10-08 复核（另一个会话的符合性审计）

2026-10-08，另一个会话做了一轮**只读**的审计：没有改任何代码与既有文档，它的文档也没有保留，有用的部分都在本节。它做的是「实现 ↔ 规格」的符合性核对：逐节比对 Phase 6 / Phase 7 两份规格与当时的代码，并检查本文件与 `AUDIT-PHASE7-EXTENSION-API.md` 声称已修复的项是不是真的在位。它**不是**又一轮攻击测试，也不审 workflow 脚本沙盒的内部（沙盒有独立的规格与测试）。8.1 是它的结论（本文件没有逐项重做），8.2 的每条发现都对着当时的 HEAD（`4b5848f`）重新核对过。批 8 修复了其中的两个中危问题（F7-01、P7-17），其余的低危与文档备注还没有处理。

### 8.1 复核的结论

- 总评：两份规格都已忠实落地，**没有发现高危问题**。Phase 6 的五个小节（MCP 立场、git 上下文、矩阵、DeepSeek 思考开关与 `reasoning_content` 回传、CLI 的 `Model.reasoning`）逐项符合规格，P6-01 至 P6-06 的修复都在位。
- Phase 7：ExtensionAPI 门面、发现与加载顺序、信任链（§4.4 的八级判定、内容指纹、`trust.json`、提问的形状与时机）、权限模型（§12 的 annotations）、后台运行与生命周期（§13）、journal（§14）、worktree（§15）、store（§16）、三个扩展包的打包，与规格一致，或是规格明示的取舍。
- 规格里写明的「已知限制」逐条核对过：没有实现超出声明的限制，也没有声明了却没做的。
- 规格的测试矩阵与测试文件的对应关系都落实，此前两轮审计补入的回归测试都在位。缺口只有 F7-01（项目 workflows 的信任门）与 F7-04（命令名校验）：行为本身缺失，所以没有测试；F7-01 的一组随批 8 补上，F7-04 修复时补。
- 复核时的测试基线（Windows `2025 passed, 14 skipped, 31 deselected`）与之后在 `4b5848f` 上的重跑一致，数字与来历见开头的状态块。

### 8.2 新发现（F7-01 与 P7-17 已在批 8 修复，其余都还没有修复）

#### F7-01. [x] 项目级 saved workflows 游离于项目信任门之外（中；批 8）

- **位置**（批 8 之前）：`pi_dynamic_workflows/__init__.py::_register_saved_workflows` 在 `activate()` 里无条件调用 `WorkflowStore.scan()`；后者扫描 `<项目>/.pi-python/workflows/` 与用户目录，项目在前，同名时项目覆盖用户。`trust_fingerprint.py` 的 `ResourceKind` 只有 `extensions` / `prompt` / `skills`；扩展也拿不到信任状态（`ExtensionAPI` 没有暴露它）。
- **问题**：§4.4 的门管着项目的扩展、提示文件与项目相对的 skills，理由是克隆来的仓库不该只因为被打开，就能执行代码或改写模型看到的内容。saved workflows 有同样的暴露面，却没有设门：项目里的每个脚本都注册成斜杠命令，命令的描述（取自脚本的 `meta`，由仓库控制）进入客户端的自动补全；用户一运行该命令，handler 就把描述与脚本路径交给会话。规格 §16 与实现一致，所以这是规格与实现**共有**的覆盖缺口，不是实现走样。
- **核对时补充的细节**：
  - 命令的输出是被 `dispatch_command` 截下来的：handler 放进 steer 队列的消息被取走，当作命令的输出，由 harness 记成一条**助手角色**的合成消息（`/名字` 本身记成用户消息）写进会话，下一轮的 LLM 上下文里看得到（不是用户角色的 steer 消息；暴露的结果一样）。
  - 同名顶替不止顶替用户的：项目目录在 `scan()` 里排在用户目录之前，所以仓库可以放一个与用户自己保存的工作流同名的脚本，让 `/<那个名字>` 指向仓库里的路径。`_register_saved_workflows` 只跳过五个内置工作流的名字，已注册的命令被覆盖时 registry 只记一条 warning，于是项目里名为 `workflows` 的脚本还能顶替扩展自己的 `/workflows` 命令（在 `PiAcpAgent` 的会话里实测：命令描述变成了脚本里写的文字）。别的扩展的命令暂时不受影响：goal-x 比它加载得晚，后注册的赢（实测 `/goal` 仍是 goal-x 的），但这只是加载顺序，不是保证。
  - 暴露面仍然比扩展弱：不会自动触发；脚本内容不会自动进上下文（模型得自己 `read` 那个文件，`read` 声明只读，不会被问）；真要运行得过 `workflow` 工具调用（`ask` 模式下会问，`auto` / `always-approve` 下不问）；`workflow` 工具的 `name=` 只解析内置工作流，不解析 saved 的；子 agent 的调用受 `tool_call_gate` 约束。剩下的主要是描述性的 prompt injection、自动补全里的误导性命名与上面的同名顶替。
- **修复**（批 8）：
  - **门**：`trust_fingerprint.gated_project_resources` 多一个 `"workflows"` 种类：`<项目>/.pi-python/workflows` 里的 `*.py`（只算普通文件；这个目录与 `<pi home>/workflows` 是同一个目录时——从家目录起的会话——它是用户自己的，不算项目的），排在扩展之后、提示文件之前。指纹覆盖该目录，记录里带着资源的种类（同样的字节作为扩展与作为 workflow 不是一回事）；提问的标题（`loading this project's extensions, saved workflows and prompt files`，只列实际存在的几类）与说明、「被跳过」的通知、`extension_trust.skipped_project_resources(config, cwd, trusted=, home=)` 都认这个种类，ACP 与 headless 两条路径都把会话的 `home` 传进去。
  - **把信任交给读取方**：saved workflows 由 dynamic-workflows 扩展读，不由 CLI 读，所以 `HarnessBridge` 多一个只读属性 `project_trusted`，`ExtensionAPI.project_trusted`（`pi.project_trusted`）把它交给扩展；在 harness 里它就是 `trust_project_extensions`，`set_trust_project_extensions` 只在扩展加载前有效，所以激活时它已是定值。**读不到就按未信任**：只有真正的 `True` 算信任（`1`、非空字符串、对象都不算），老的核心没有这个属性、读取时抛异常，都当作未信任并记 warning。
  - **扩展一侧**：`WorkflowStore(include_project=)`，未受信任时 `scan()` 只读用户目录；`_register_saved_workflows` 不再让脚本顶替扩展自己的命令（`workflows` 与五个内置工作流的名字：记 warning 并跳过）。
  - **取舍**：受信任项目里与用户自己保存的同名的脚本仍然覆盖用户的（与扩展同一标准：信任了就是信任了）；不受信任的项目整个不读，所以它顶替不了用户的。第七节原来写的「仓库里与用户自己保存的同名的脚本不再顶替用户的」因此收窄为：只有用户信任了这个项目，才会发生。
  - **行为变化**：此前不受信任的项目里的 saved workflows 也会成为斜杠命令，现在要先受信任（项目带有 workflows 时，ACP 客户端会被问到，回答与扩展、提示文件、skills 一起记住）。
- **验证**：先红后绿（90 个用例修复前失败）。指纹（种类、排序、目录与家目录重合、没有脚本不算、用户目录不算、改动即失效、种类进入摘要）、提示（标题、说明、顺序）、通知，各有单元用例；端到端用真实的 `PiAcpAgent` 与真实的 dynamic-workflows 扩展：未受信任时没有命令，被问到的内容（标题、`raw_input`、说明），信任后注册，回答被记住、`git pull` 改了脚本就再问，新增一个脚本再问，`workflows` 命令不可被顶替，用户级的不需要信任，被拒绝的项目顶替不了用户的，受信任的项目覆盖用户的，白名单免问，「被跳过」的通知，从家目录起的会话里没有项目的东西可问；扩展一侧覆盖 store、`pi.project_trusted` 的每种读法（`True`、各种「真值」、缺失、抛异常）与经真实加载器的路径；harness 的桥覆盖默认、构建时、之后设置、收回、真正的布尔值；headless 覆盖报告被跳过的 workflows、承认与不承认已保存的回答、家目录在项目里。变异检查见第六节。
- **规格**：Phase 7 规格 §3.2（`register_command`、新增的 `project_trusted`）、§4.4（受门约束资源表、「被跳过」通知、`pi.project_trusted`、提问标题、指纹、新增的已知限制）、§16（store 的 `include_project`、注册规则）；`packages/pi-agent-cli/AGENTS.md` 的 Project trust 一段与根目录 `AGENTS.md` 的第 9 条不变量；README、`docs/TUI-AND-CODE-AGENT.md`、`agent.example.toml`。
- **残留**：见第五节 5。

#### P7-17. [x] saved workflow 的 `meta.description` 不是字符串，会让整个会话不可用（中；核对 F7-01 时另外发现；批 8）

- **位置**（批 8 之前）：`WorkflowStore._load_one` 只校验 `name`（代码注释：非文本的名字曾让整次扫描抛错），`description` 与 `when_to_use` 原样取自脚本的 `meta`；`_register_saved_workflows` 把 `description` 交给 `register_command`；`PiAcpAgent._advertise_commands` 用它构造 `AvailableCommand`，ACP SDK 的 pydantic 模型要求 `description` 是字符串。
- **问题**：`meta = {"description": 5}`（任何为真的非字符串值：非零的数字、非空的列表与字典、`True`；为假的值会退回默认描述）使 `AvailableCommand` 校验失败。实测（真实的 `PiAcpAgent`，`permission="auto"`，没有任何信任提示）：`session/new` 之后的后台任务抛 `ValidationError`，一个命令也没广播，同一个任务里排在后面的两个通知（项目没被信任、扩展加载失败）也就不会发；之后每个 `session/prompt` 都在「补发命令」那一步抛同一个 `ValidationError`，因为 `_commands_advertised` 只在成功之后才记。同样的项目把 `description` 换成字符串，命令照常广播，prompt 返回 `end_turn`（对照实验）。项目里放这样一个文件，打开它就让 agent 不可用，用户看到的只是一条不指明文件的校验错误。用户级目录（`~/.pi-python/workflows/`）同样会被扫描，所以即使 F7-01 的信任门补上了，这一项也还在；扩展作者写出 `register_command(description=None)` 之类的错误，得到的是同样的结果。
- **修复**（批 8；三层，每一层单独都能让会话保持可用）：
  1. `WorkflowStore._load_one`：非字符串的 `description` / `when_to_use` 视为没写（前者退回 `""`，后者退回 `None`），与对非法 `name` 的处理一致；`_register_saved_workflows` 对空描述用默认的 `Run saved workflow: <名字>`，并且扫描失败、单个脚本注册失败都只记 warning，不影响其余脚本，也不影响扩展自己的 `workflow` 工具与 `workflows` 命令。
  2. `ExtensionAPI.register_command`：`description` 不是字符串就抛 `TypeError`，指出扩展与命令名，像 `register_tool` 的名字校验一样在 `activate()` 阶段失败并回滚（加载器把它记进 `failed`，用户在会话开始时被告知）。
  3. `PiAcpAgent._advertise_commands`：逐个构造 `AvailableCommand`，描述不了的命令记 warning（说明是哪个字段、违反了什么规则；不把值写进日志，pydantic 的文本会原样重复它，而它可以是任何东西、任何大小）并跳过，其余照常广播，会话照常记为已广播。
- **没有照做的一处**：第七节原来的建议还写了「`prompt()` 里的补发失败也不该让这次 prompt 失败」。触发它的原因（一个描述不了的命令）已经在上面三层被拦下；剩下能让补发失败的只有传输层的失败（客户端已断开），那时这次 prompt 本来也发不出任何更新，吞掉它只会把原因藏起来，所以这一处保持原样。
- **验证**：先红后绿；端到端用真实的 `PiAcpAgent` 与真实的扩展，用户级与项目级各一组，`description` 取 `5`、`True`、`["a"]`、`{"x": 1}`、`1.5`：命令照常广播（默认描述），`session/prompt` 返回 `end_turn`；另有直接往注册表里塞一个 `CommandDef(description=5)` 的用例（`_advertise_commands` 的一层），其余命令照常广播、会话记为已广播、告警里有字段名而没有值。变异检查见第六节。
- **规格**：Phase 7 规格 §3.2（`register_command` 的 `description` 必须是字符串）、§16（meta 里不是文本的 `description` / `when_to_use` 被丢弃；注册规则）。

#### F7-02. [ ] `get_custom_entries` 只反映加载时的快照（低）

- **位置**：`agent_harness.py::_ensure_extensions_loaded` 一次性把会话里的 custom 条目填进 `_cached_custom_entries`，bridge 的 `get_custom_entries` 只过滤这份缓存；`append_entry` 只是排进 `pending_session_writes`，既不更新缓存，落盘之后也不刷新。
- **问题**：扩展在同一会话里先写后读，读不到自己刚写的条目，而 `ExtensionAPI.get_custom_entries` 的文档写的是「读取会话里持久化的全部条目」。goal-x 只在 `session_start` 读，不受影响。
- **补充**：一个看似可行的办法是「读取时重新拉 `session.get_entries()`」，这行不通：`get_custom_entries` 是同步方法，`get_entries()` 是协程。可行的是在 `append_entry` 时同步把条目记进缓存（读到的对象要有与落盘条目一样的 `customType` 与 `data`），或者在文档里写明只有快照语义。

#### F7-03. [ ] `ExtensionAPI.run_async` 用 `asyncio.get_event_loop()`（低）

- **位置**：`extensions/api.py` 末尾。
- **问题**：在运行中的循环里它与 `get_running_loop()` 等价（实测返回同一个循环）。没有运行中的循环时，Python 3.12、3.13 发 `DeprecationWarning`（3.12.7 上实测）并悄悄新建一个不会运行的循环，任务永远不执行；3.14 起直接抛 `RuntimeError`，不再隐式新建循环（CPython 3.14 的 What's New）；函数本身还在，被移除的只是隐式新建。
- **建议**：改成 `asyncio.get_running_loop()`（没有循环就立刻报错），与 bridge 里 `set_active_tool_names` 用的一致。

#### F7-04. [ ] `register_command` 不校验命令名（低）

- **位置**：`extensions/api.py::register_command`。
- **问题**：`register_tool` 校验名字，失败时在 `activate()` 阶段回滚（P7-16 的教训：坏名字要在注册处失败）；`register_command` 什么都不查。`_try_slash_dispatch` 用 `split(None, 1)` 取命令名，含空白的名字永远派发不到，而 `_advertise_commands` 把注册表里所有命令名原样广播给客户端。
- **建议**：校验命令名（规则见第七节 10），失败时回滚该扩展；`description` 的类型检查已随 P7-17 加上（批 8）。

#### F7-05. [ ] 目录扩展 import 失败后 `sys.modules` 残留（低）

- **位置**：`extensions/loader.py::_load_module_from_file`。
- **问题**：先 `sys.modules[module_name] = module` 再 `exec_module`，抛错时不清除（实测：这样手写的导入流程在模块抛错后留着条目，标准的 `import` 则会清掉它）。半初始化的模块留在 `sys.modules` 里；模块名 `_pi_ext_<名字>` 又是所有项目共用的，同一个进程里两个项目的同名扩展文件会互相覆盖这个条目。影响很小：失败已计入 `failed`，不会重复导入。
- **建议**：`except BaseException:` 里 `sys.modules.pop(module_name, None)` 再重新抛出。

#### F7-06. [ ] 扩展工具静默覆盖同名内置工具；注入路径不发 `ToolsUpdateEvent`（低）

- **位置**：`agent_harness.py::_apply_extension_registrations` 与 bridge 的 `inject_tool`，两处都直接 `self._tools[名字] = tool`。
- **问题**：扩展覆盖扩展，registry 层有 warning（后加载的赢，规格允许）；扩展覆盖内置工具（`read`、`bash` 等）没有任何日志。`set_active_tool_names` 会发 `ToolsUpdateEvent`，注册与动态注入的路径不发，订阅方无从得知工具集变了（ACP 投影目前不消费该事件，没有用户可见的影响）。
- **补充（与批 6 的关系）**：harness 的权限查询取的是 `self._tools.get(名字)` 的 annotations，所以被覆盖的内置名字按扩展工具自己的声明判定：一个声明只读的扩展工具顶替 `bash` 或 `write`，`ask` 模式下就不会被问。对被信任的扩展，这是设计内的行为（扩展本来就是进程内代码；不受信任的项目的扩展根本不会加载，见 P7-02），所以没有升级，但至少该留一条 warning。
- **建议**：见第七节 11。

#### F7-07 至 F7-09（备注，改文档）以及无需处理的 F6-01、F6-02

- **F7-07**：规格 §3.5 的事件表把 `on("session_start")` 映到 `before_agent_start`（每回合之前），§4.3 第 6 步又要求扩展加载后发一次 `session_start`。实现取后者（`_emit_hook_simple("session_start")`，一次性的平字典事件），goal-x 的恢复依赖它，每回合的钩子由 `turn_start` 承担，有测试钉住（`TestSessionStartEvent`）。要改的是 §3.5 的表。
- **F7-08**：`_tool_from_definition` 固定以四个位置参数调用 `execute(tool_call_id, params, signal, on_update)`，而 `ExecuteFn = Callable[..., ...]` 没有表达这一点。一方扩展都写了四个参数（后两个带默认值）；第三方照上游 TS 的习惯只写两个，会在运行时 `TypeError`。要写进 `ExecuteFn` 的文档串与规格 §3.4，或者换成 `Protocol`。
- **F7-09**：`_RuntimeHost.agent` 的 journal 哈希只含 `prompt` 与 `tier` / `model` / `label` / `phase` / `schema` / `cwd`，不含 `timeout_ms`（resume 时放宽超时照样回放旧答案，合理；改 label 则不回放），`cwd` 取脚本给的原始字符串（没给就是项目根），不是解析后的路径（批 5 明示的选择）。规格 §14 写的是 `hash_request("agent", {prompt, opts})`，要写明是子集。
- **F6-01、F6-02**（核对后无需处理）：矩阵的 `MATRIX_<ID>_BASE_URL` 覆盖对没有默认 base_url 的行（openai / anthropic / deepseek）也生效（`resolve_row` 的 `else` 分支），与规格 §5.2 的文字一致，让「指向自家网关的 openai 兼容端点」可测；`provider-matrix.yml` 用 `pip install -e` 而不是 uv，与 `ci.yml`、`release.yml` 一致，不构成偏差。
