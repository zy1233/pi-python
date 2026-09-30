# AUDIT：Phase 6 / Phase 7 设计与实现审计（2026-09-30）

> **审计日期**：2026-09-30（发现与三批修复在同一天完成）
>
> **审计范围**：
> - Phase 6：`packages/pi-agent-cli/pi_agent_cli/{git_context,prompt_options,system_prompt,factory}.py`、`pi_agent_core/adapters/langchain_stream.py`、`pi_agent_core/tests/{provider_matrix,test_provider_matrix}.py`、`.github/workflows/provider-matrix.yml`
> - Phase 7：`pi_agent_core/extensions/`、`AgentHarness` 的扩展集成与生命周期、CLI 的扩展接线 / 权限层 / ACP 会话、`packages/pi-web-access/`、`packages/pi-goal-x/`、`packages/pi-dynamic-workflows/`
>
> **对照设计**：`docs/specs/2026-09-24-phase6-extended-integrations-design.md`、`docs/specs/2026-09-22-phase7-extension-api-design.md`
>
> **方法**：代码阅读；在 Windows（zh-CN 区域设置）上用探针脚本实测；provider 文档（DeepSeek 等）对照核对。探针是临时脚本，没有入库；每一项的回归测试入库，并且都先红后绿。
>
> **修复批次**：批 1 `4c28f4e`、批 2 `6f687b5`、批 3 随本文档一起提交（`git log -- docs/AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md`）
>
> **最终测试状态**：
> - Windows：`1352 passed, 30 deselected`（审计开始时 `576 passed, 30 skipped`）
> - WSL：`1347 passed, 5 skipped, 30 deselected`（4 个 skip 是 WSL 里没有 `rg`，1 个是只在 Windows 上有意义的路径大小写用例）
> - `ruff check .` + `ruff format --check .`：All checks passed
>
> **状态图例**：`[x]` 已修复并验证 · `[x/~]` 主路径已修复，有已记录的残留（见第五节）

> **编号说明**：本文件的 `P6-xx` / `P7-xx` 是本次审计的编号；代码与测试里写作 `audit P7-xx`、`audit P6-xx` 的，指本文件。
> `AUDIT-PHASE7-EXTENSION-API.md` 是更早的一轮审计，它的 `P7-01` … `P7-15`、`P7R3-xx` 与本文件重名但含义不同；测试文件里孤立的 `# P7-02: failed activate must roll back …` 这类注释属于旧文件（已在旧文件开头加了一条指向本文件的说明）。

---

## 一、问题总览

### 高危

| ID | 问题 | 批次 | 状态 |
|----|------|------|------|
| P6-01 | Windows 中文区域设置下 git 快照编码错误：会话不可用或分支名乱码 | 1 | `[x]` |
| P6-02 | `<git_status>` 在会话内被冻结，与规格「每个 turn 刷新」相反 | 2 | `[x]` |
| P7-01 | `workflow` 等价于无需授权的任意代码执行，子 agent 绕过 `ask` 权限 | 2 | `[x/~]` |
| P7-02 | 项目目录中的扩展无需信任确认，打开项目即执行 | 2 + 3 | `[x/~]` |
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
- **修复**（批 2）：`workflow` 加入 `PERMISSION_TOOLS`（`ask` 模式下启动 workflow 要用户批准）；子 agent 的每次工具调用都走父会话的 `tool_call` 链（`AgentHarness.check_tool_call`，扩展经 `HarnessBridge.tool_call_gate` 使用；出错时拒绝），调用 id 为 `subagent-<hex>:<model>`，`ToolCallEvent.origin = {"kind": "subagent", "cwd": ..., "label": ...}`，ACP 授权提示注明来自子 agent 与它的工作目录；批准 `workflow` 不等于批准它的子 agent 写文件，每次调用逐个询问（只读工具与会话自己一样不询问，但扩展 hook 照样生效）；同一会话的授权提示串行，并行子 agent 不会叠出多个对话框。文档不再称运行时为「沙盒」。
- **验证**：先红后绿，包括经真实 ACP agent、真实 workflow 扩展、真实 git worktree 的端到端用例。
- **规格**：Phase 7 规格 §9.3、§12（权限继承）。
- **残留**：见第五节 1–3。

### P7-02. [x/~] 项目目录中的扩展无需信任确认

- **问题**：`<cwd>/.pi-python/extensions` 在 `session/new` 时立即 `exec_module`：克隆一个恶意仓库并打开，就会在用户权限下执行仓库里的 Python 代码。
- **决策**（用户选择）：默认不加载，靠显式开关信任；不做交互式提示。
- **修复**（批 2）：项目扩展默认不导入（连模块都不加载）。信任只来自项目自己写不到的地方：home 的 `agent.toml` 里的 `[extensions] trusted_projects = [...]`（含子目录）/ `trust_project_extensions = true`、环境变量 `PI_TRUST_PROJECT_EXTENSIONS=1`、命令行 `--trust-project-extensions`（ACP 与 headless 都有）。被跳过的扩展会记日志，并告诉用户（ACP 在 `session/new` 之后发一条 agent 消息，headless 写 stderr）。顺带修复了 cwd 为 home 时 `~/.pi-python/extensions` 被扫描两次。库 API：`AgentHarness(trust_project_extensions=False)`、`ExtensionLoader.load_all(trust_project_extensions=False)`、`skipped`。
- **修复**（批 3，对齐上游）：项目的 `.pi/SYSTEM.md`、`.pi/APPEND_SYSTEM.md` 和项目相对的 `[skills].paths`（如 `.pi/skills`）同样受这条信任规则约束；不受信任时回落到用户自己的 `~/.pi-python/agent/SYSTEM.md` / `APPEND_SYSTEM.md`；通知里也列出这些文件。
- **验证**：批 2 的信任判定 14 个变异体、批 3 的提示文件 / skills 14 个变异体，全部被杀。
- **规格**：Phase 7 规格新增 §4.4（信任模型）。
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
- **残留**：见第五节 13。**没有对真实 API 验证**（没有 `DEEPSEEK_API_KEY`）；每周矩阵是第一次会验证它的地方。
- **行为变化**：官方 DeepSeek 上 `off`（默认）与 `Model.reasoning=False` 现在发 `thinking: disabled`，此前什么都不发。

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
- **验证**：`test_harness_lifecycle.py`、`test_workflow_lifecycle.py`、`test_session_lifecycle.py`；53 个变异体全部被杀。
- **规格**：Phase 7 规格 §12（Bridge 扩展）、§13（结果交付、不可信输出的信封、生命周期）。
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
| Phase 7 规格 §9.3 | workflow 脚本在受限命名空间中「沙盒」执行 | 不再称沙盒，并把它的工具调用放进权限链（P7-01） |
| Phase 7 规格 §3.5 | `tool_call` 是唯一具有阻塞能力的事件 | 首个 block 短路，与上游一致（P7-08） |
| `AUDIT-PHASE7-EXTENSION-API.md` 的最终判定 | 10 个高影响、20 个中影响问题全部关闭 | 该文件全文没有沙盒、信任、权限、SSRF 相关条目（关键字检索无匹配），本文件补上。它的已记录限制 1（未跟踪的文件不会回写 worktree）已被 P7-10 取代：新文件现在会回写；限制 2（结果遇到运行中的 turn 时并入该 turn）仍然成立，交付方式由 P7-07 改成带类型的 custom 消息。旧文件只在开头加了一条编号说明 |

### 系统性缺口的收口情况

| 审计时归纳的缺口 | 现状 |
|------------------|------|
| 没有信任 / 能力模型：只定义了发现与覆盖，没有回答「谁的代码可以运行」「嵌套调用如何过权限」；权限是按工具名的白名单 | 项目信任（P7-02，最小子集，按路径）与子 agent 的权限继承（P7-01）已补上；权限仍是按工具名的白名单（第五节 3） |
| 特性之间没有对账：提示缓存与每 turn 刷新；tier 路由与 provider / key；`trigger_prompt` 与 `close()` 及 ACP turn 模型 | 三处都已对账：git 段标注为会话快照（P6-02）；tier 与 key 按 provider 限定（P7-03）；`close()` 之后不再开启 turn，后台结果改用 custom 消息（P7-07） |
| 「忠实移植」在扩展语义上有偏差：`tool_call` 不做首个 block 短路、嵌套调用不过钩子、没有 `project_trust` | 首个 block 短路（P7-08）；子 agent 的调用过钩子（P7-01）；`project_trust` 的最小子集（P7-02，无提示、无 `/trust`） |
| 失败语义不统一：有的静默降级（Mock 回退、journal 缓存 `None`、`suppress(Exception)`），有的硬失败（git 编码） | 不再退回 Mock（P7-14）；失败不入 journal（P7-05）；git 失败省略该段（P6-01）；扩展加载失败对用户可见（P7-12）。其他位置的 `suppress(Exception)` 不在本次修复范围 |

### 审计确认没有问题的部分

- 扩展 API 的表面小而清晰，`HarnessBridge` 把核心库与 harness 隔开，发现顺序与覆盖语义与上游一致。
- hook 抛异常时 fail-closed（`_prepare_tool_call` 把异常转成错误结果），与上游「`tool_call` handler 失败即阻止工具」一致。
- Phase 6 的 git 段只读、有界、可关闭，且不写入 JSONL，不会把过期 diff 固化进会话。
- 矩阵按数据行驱动，缺密钥自动 skip，无密钥环境下不会失败。

---

## 五、已记录的残留与限制

| # | 关联 | 内容 |
|---|------|------|
| 1 | P7-01 | workflow 运行时是受限命名空间，不是沙箱；需要隔离要用独立进程加资源限制 |
| 2 | P7-01 | 脚本用 `agent(..., cwd=)` 给子 agent 指定的工作目录会显示在授权提示里，但不限制在工作区内 |
| 3 | P7-01 | `auto` / `always-approve` 模式下，子 agent 可以做会话允许的一切；没有 gate 的 executor（独立使用，或 bridge 没有 `tool_call_gate`）里的子 agent 不受限，`activate()` 只记一条 warning；权限本身仍是按工具名的白名单，审计建议的「默认需授权、按 annotations 放行只读工具」没有做 |
| 4 | P7-02 | 信任按路径判断，不按内容：白名单内的项目之后才出现的扩展（例如 `git pull` 带来的）会被直接运行；授权以整个项目目录为单位，不区分单个扩展或资源；没有交互式信任提示，也没有内容哈希存储；TUI 自己的目录信任（`/hooks trust`）不适用于此；`[extensions]` 这个配置名同时管所有项目资源（扩展、提示文件、skills） |
| 5 | P7-02 | `AGENTS.md` / `CLAUDE.md`（上游不论信任与否都会加载）、`agent.toml` 里直接给出的 prompt、绝对路径或 `~` 开头的 skills 路径不受门控 |
| 6 | P7-02 | 项目扩展一旦被信任，就是完整的进程内代码执行 |
| 7 | P7-05、P7-10 | journal 重放不会重做「请求没变的步骤」曾对工作区做的事（比如改过的文件）；worktree 回写失败（补丁与源目录此刻的状态冲突）时，该子 agent 的改动丢失；快照不复制源目录里未跟踪的文件 |
| 8 | P7-06 | 不合作的运行（脚本吞掉取消）在 10 秒宽限期后被放弃并记 warning：它仍在后台跑到自己结束，但不再交付结果 |
| 9 | P7-07 | custom 消息在 LLM 层仍是 user 角色，信封是文本约定，不是协议级隔离；它只进会话与 LLM 上下文，不进 `session/load` 的历史回放 |
| 10 | P7-07 | 后台结果触发的 turn 没有对应的 `session/prompt` 请求：ACP v1 没有禁止 turn 之外的 `session/update`，但客户端未必渲染；v2 草案的 `state_update`（idle / running）没有处理，客户端兼容性风险未验证 |
| 11 | P7-09 | 走代理时，代理把公开域名解析到私网地址拦不住，也没有「校验的地址即连接的地址」的保证（需要更强保证就在代理一侧限制出口，或不设代理走直连）；工具不响应 turn 的 abort 信号，靠 30 秒总时限兜底，超时后 `trafilatura` 的工作线程会自己跑完；服务端无视 `Accept-Encoding: identity` 发压缩正文时，一次读取的 64 KiB 压缩数据最多瞬时膨胀到约 64 MiB，之后被 5 MiB 上限截断 |
| 12 | P7-11 | 提醒次数只在本进程内计，不随状态保存 |
| 13 | P6-03 | 官方 DeepSeek 上，开启思考且带工具时需要回传 `reasoning_content`，`langchain-deepseek` 1.1.0 / 1.1.1 都不回传，预期第一轮工具调用后 400；CLI 从不设置 `Model.reasoning`，经 CLI 使用官方 DeepSeek 始终不思考；以上都没有对真实 API 验证 |
| 14 | P7-15 | 扩展的版本下限必须随下一次核心发布一起上调（测试会拦住），这批扩展用到了比 0.4.0 更新的 API（`ExtensionAPI.home`、`pi_agent_core.home`、`HarnessBridge.tool_call_gate`、`HarnessBridge.trigger_message`） |
| 15 | P7-03 | 模型 id 写成 `provider/model`，按第一个 `/` 拆分，对自身含 `/` 的网关模型 id（如 `Qwen/Qwen3-8B`）有歧义，需写成 `<provider>/Qwen/Qwen3-8B` |
| 16 | 编号 | 本文件与 `AUDIT-PHASE7-EXTENSION-API.md` 的编号重名，见文件开头的说明 |

---

## 六、验证方法与结果

- **先红后绿**：每项修复的回归测试在修复前必须失败。批 1 共 52 个，批 2 含经真实 ACP agent / 真实 workflow 扩展 / 真实 git worktree 的端到端用例，批 3 每一簇都先写测试再改实现。
- **手工变异检查**：改坏实现的一处，跑对应测试，看有没有被抓住；幸存的变异体要么补测试、要么证明是等价变异。下表是在提交前的最终代码上重跑的结果（重跑时有 5 个变异体的匹配模式因后来的重构或格式化失效，已更新后单独重跑，均被杀）。

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
  | 合计 | 383 | 382 被杀 |

- **探针重跑**：审计时用探针脚本复现问题，修复后重跑，行为已变成预期（探针是临时脚本，没有入库；对应的回归测试入库）。
- **跨平台**：Windows 与 WSL 各跑一遍全量。WSL 首跑暴露了 4 个失败：这台机器的 WSL 导出了 `PI_HOME`，让「把 `Path.home()` 指向临时目录」的测试读到真实目录（P7-13 让加载器也认 `PI_HOME` 之后才出现）。根目录 `conftest.py` 现在为每个测试清掉 `PI_HOME`，`test_home.py` 里有回归测试。
- **provider 文档**：DeepSeek 的模型名与下线日期、`thinking` / `reasoning_effort` 参数、`reasoning_content` 回传要求，以及 DeepSeek 为 pi 写的接入文档（`reasoningEffortMap`），都对照官方文档核对过；与真实 API 的对接没有验证（没有密钥）。

---

## 七、后续工作（未做，按价值排序）

1. **信任的交互与内容哈希**：ACP 里的交互式信任提示，加上路径与内容哈希的存储，替代目前的纯路径信任（第五节 4、5）。
2. **子 agent 的 `cwd` 约束**：把脚本选的 `cwd` 限制在项目内（第五节 2）。
3. **workflow 真正的隔离**：独立进程加资源限制，取代受限命名空间（第五节 1）。
4. **DeepSeek 的 `reasoning_content` 回放**，并在 CLI 里接入 `Model.reasoning`（第五节 13）。
5. **默认需授权的权限模型**：按工具 annotations 放行只读工具（第五节 3）。
6. **ACP `state_update`**（第五节 10）。
7. **发布时**：上调三个扩展包的版本下限（第五节 14）。
