# AUDIT：zycode 重构 Roadmap 与 `main` 架构审计（2026-11-27）

> **审计日期**：2026-11-27。
>
> **审计范围**：`main` @ `ec49e28`（PR [#156](https://github.com/zy1233/pi-python/pull/156) 与 [#157](https://github.com/zy1233/pi-python/pull/157) 合入之后）。对象有两类：
> - **当前代码**：steer 与 `/btw` 的引擎侧实现（`packages/pi-agent-harness/`、`packages/pi-agent-cli/`）；LangChain 适配层（`pi_agent_core/adapters/`）；二进制构建与 CI（`packaging/agent/`、`.github/workflows/agent-binaries.yml`、`.github/workflows/ci.yml`）。文中代码的行号对应 `ec49e28`。
> - **规划文档**：[`docs/specs/2026-11-25-zycode-refactor-and-roadmap.md`](../specs/2026-11-25-zycode-refactor-and-roadmap.md)（607 行，2026-11-26 修订版）。文中规划的"第 N 行"都指这份文件。
>
> **方法**：
> 1. 通读规划全文。能对照代码或外部事实的陈述，逐条核对。
> 2. 对并发与生命周期问题写复现脚本，在 `ec49e28` 上运行。
> 3. 修复时先写回归测试。先确认它在旧代码上失败，再确认它在新代码上通过。
> 4. 在 Windows 上用完整 `pytest` 做基线。
>
> **本文的来源（请先读）**：本文不是聊天报告的逐字转录。写本文时，聊天报告的原文已不在手边。本文用复现脚本、测试输出、提交信息和笔记重建，并在 2026-11-27 重新核对了每项证据。因此：
> 1. C1、C2、C3、C5 沿用聊天报告的编号。C4 的内容无法恢复，本文不补写（见 §五）。
> 2. 聊天报告把规划问题编为 R1–R9。这些编号和原文都没有保留。本文重新审计了规划，编号为 `RM-n`。`RM-n` 与 R1–R9 不是一一对应的。
> 3. 级别按下面的定义重新评定，可能与聊天报告不同。
>
> **级别**：高 = 设计达不到它自己写的验收门槛，或会重新打开一条已经关闭的安全边界；中 = 会造成难以排查的错误行为或返工；低 = 文档不准确，不影响行为。
>
> **状态图例**：`[x]` 已修复并验证，修复已合并到 `main`（提交见 §6.2）；`[ ]` 未处理；`[?]` 无法核实。

---

## 一、问题总览

### 1.1 当前代码

编号 `C-n`。

| ID | 级别 | 位置 | 问题摘要 | 状态 |
|----|------|------|----------|------|
| C1 | 中 | `packages/pi-agent-harness/pi_agent_harness/agent_harness.py`：`_clear_undelivered_steers` | agent loop 在 turn 末尾把 steer 从队列取走。run 随后因 `max_turns` 或流错误结束，steer 没有送达模型，也没有被报告。用户的文字丢失，客户端收不到 `clearedSteerIds` | `[x]` |
| C2 | 中（安全） | 同上：`_steer_ids`、`steer_id_for`、`is_steered_message`；`packages/pi-agent-cli/pi_agent_cli/agent.py`：`on_event` | steer 表以 `id(message)` 为键，且不持有消息。CPython 可能把已释放消息的 `id` 给后来的消息。后来的消息被当成 steer：它的文字进入 `auto.note_user_message`，并作为 `pi/steered` 消息发给客户端。代码不检查消息的角色，所以 `toolResult` 消息也可能走这条路径（本次试验没有观察到），这会违反 Invariant 9b | `[x]` |
| C3 | 中 | 同上：`begin_abort`、`abort`、`_abort_depth`、`_idle_event` | `session/cancel` 到达时没有 run，紧接着的新 prompt 却以 `aborted` 结束（那个 `abort()` 还在发通知）。同一个 `abort()` 还会等下一次 run 结束，期间 `pi/session/steer` 一直被拒（`aborting`） | `[x]` |
| C4 | — | — | 内容未保留（见 §五） | `[?]` |
| C5 | 中 | 根 `pyproject.toml`；`pi_agent_core/adapters/`；`packaging/agent/`；`.github/workflows/{agent-binaries,ci}.yml` | 适配层重写了 LangChain 的私有方法，但 LangChain 依赖没有上界；二进制构建不锁版本；CI 不装 `langchain-deepseek`，`ChatDeepSeek` 的测试被跳过 | `[x]` |

### 1.2 规划文档

编号 `RM-n`（Roadmap Model finding）。这些是对设计的审查，不是测试结果。

| ID | 级别 | 位置 | 问题摘要 | 状态 |
|----|------|------|----------|------|
| RM-1 | 高 | §3.2（第 290、315 行）；Phase 1（第 533 行）；Phase 4 门禁（第 579 行） | `event_seq` 重放只有一个环形缓冲区，没有"缺口"规则。有界缓冲区不能满足"重连事件零丢失" | `[ ]` |
| RM-2 | 高 | §3.2（第 270 行）；§3.3.1（第 345 行） | `zycode-daemon` 一处是"常驻/按需拉起"，另一处是"嵌入 Tauri 应用"。生命周期（谁启动、谁停止、单实例、版本不一致）没有定义 | `[ ]` |
| RM-3 | 高 | §3.1（第 260 行）；§3.2（第 317、324 行）；Phase 1（第 533 行） | Daemon 在项目目录里运行 git（Worktree、Diff、Commit、Push）。规划只写"继承 Invariant 9"，没有给这些操作写信任门 | `[ ]` |
| RM-4 | 中 | §4.2（第 419 行）；Phase 0（第 519 行）；Phase 2（第 555 行）；D4（第 606 行） | `pi-rust` 的范围里没有 `workflow` 工具；D4 把 `codemode` 的默认语言从 Python 改为 JS 或 Starlark；黄金测试集没有覆盖这些功能。"100% 通过"不能证明功能对等 | `[ ]` |
| RM-5 | 中 | §4.1（第 370–381 行）；Phase 2（第 556 行）；Phase 3（第 568 行） | 两个门禁无法客观判定：性能门禁没有阈值；Phase 3 门禁要求在 macOS 与原生 Windows 上运行只能在 Linux 上运行的探针 | `[ ]` |
| RM-6 | 中 | §4.3（第 443、447 行） | `SessionBackend::subscribe` 返回 tokio `broadcast::Receiver`：慢订阅者会丢事件，每个接收者得到一份克隆。"零拷贝传递全部事件"两点都不成立 | `[ ]` |
| RM-7 | 中 | §2.4（第 231 行）；Phase 0（第 518 行）；D1（第 603 行） | 配置目录规则已有 3 份实现，并且分歧过一次（R2-5）。`~/.zycode` 回退没有写写入去哪里，也没有写两个目录同时存在时的行为 | `[ ]` |
| RM-8 | 低 | §0.1（第 20–24 行）；§2.2（第 154 行）；§5.2（第 503 行）；§6（第 588 行） | 三处文档不准确：§0.1 的数字复现不出来；§2.2 对 Open VSX 拒签原因的说明不准确；§5.2 与 §6 说"任何 run 结束都回报未送达的 steer"，修复 C1 之前代码做不到 | `[ ]` |

---

## 二、当前代码：各项详情与修复

修复是 `main` 上的两个提交（C1–C3 一个，C5 一个），见 §6.2。

### C1. `[x]` run 异常结束时，已被 agent loop 取走的 steer 丢失

- **问题**：
  1. `agent_loop` 在每个 turn 末尾调用 steering 轮询，把 steer 从 `steer_queue` 取走，准备在下一个 turn 开头注入。
  2. 如果 run 在注入之前结束（`max_turns` 用完，或流出错），这些消息已不在 `steer_queue` 里。
  3. `_clear_undelivered_steers`（第 1372 行）只检查 `steer_queue`（第 1380 行）。它看不到这些消息，所以不报告。
  4. 结果：用户输入的文字丢失。客户端收不到 `pi/queue_update` 的 `clearedSteerIds`，无法把文字放回输入框。
  5. 规划第 503 行与第 588 行写的是"任何 run 结束……均回报"。对这种情况，代码没有做到（见 RM-8）。
- **复现**：在 `ec49e28` 上，设 `max_turns=1`，在工具批次执行期间调用 `steer()`，让 turn 末尾的轮询取走它。2026-11-27 的输出：

  ```text
  stopReason          : error
  error               : Agent loop exceeded max_turns=1
  steer_queue         : []
  _steer_ids (stale)  : {...: 'steer-A'}
  user message_start events (incl. opening prompt): 1
  queue_update events : ['queue_update steer=1 undelivered=0', 'queue_update steer=0 undelivered=0']
  ```

  steer 已离开队列。模型只看到 1 条用户消息（开头的 prompt）。最后一次 `queue_update` 写 `undelivered=0`。`_steer_ids` 里留下一条过期条目。
- **修复**：每个 steer 有一个条目。条目有三种状态：`pending`、`announced`、`cleared`。`message_start` 把条目设为 `announced`。`abort()` 把条目设为 `cleared`。run 结束时，harness 报告所有 `pending` 条目，顺序与 `steer()` 的调用顺序相同。`max_turns` 的行为不变，只增加了报告。
- **测试**：`test_harness_agent.py::test_steer_taken_by_the_loop_is_reported_when_max_turns_ends_the_run`（旧代码上失败）。对照测试（新旧代码上都通过）：`test_steer_that_the_model_saw_is_not_reported_as_undelivered`、`test_aborted_steer_is_reported_once_and_its_id_is_readable_in_the_abort_event`。

### C2. `[x]` 以 `id(message)` 查找 steer：已释放消息的 `id` 可能被后来的消息复用

- **问题**：
  1. `_steer_ids` 是 `dict[int, str]`，键是 `id(message)`（第 327 行、第 1869 行）。字典不持有消息本身。
  2. Python 文档说明：生命周期不重叠的两个对象可以有相同的 [`id()`](https://docs.python.org/3/library/functions.html#id)。CPython 的 `id` 是内存地址，会被复用。
  3. 如果一条 steer 的条目没有被清掉（C1 会造成这种情况），后来的消息可能得到同一个 `id`。`steer_id_for()` 与 `is_steered_message()`（第 1874、1878 行）会把它当成 steer。
  4. 对这条消息，`agent.py` 的 `on_event` 做两件事：把它的文字交给 `auto.note_user_message`；把它作为带 `pi/steered` 的 `user_message_chunk` 发给客户端。取文字的 `_user_message_text` 不检查角色。
  5. 所以，如果被误判的是 `toolResult` 消息，工具输出会被当成用户输入。Invariant 9b 要求 Auto 只得到用户输入的文字，绝不得到工具的文字。
- **复现**：脚本先造一个过期条目（C1 的场景），再运行一个普通的后续 prompt，对每条经过 `message_start` 的消息询问 harness，与 `on_event` 的做法相同。2026-11-27 在 `ec49e28` 上运行 300 次试验：4 次试验中有一条后来的消息被判为 steer。聊天报告与 CHANGELOG 记的是 3 次。两次结果不同，因为 `id` 的复用取决于内存分配。4 条都是 `user` 消息（后续 prompt）。这 300 次试验里没有 `toolResult` 消息：代码允许这种情况，本次没有观察到它。另一个直接构造 `id` 复用的实验（2000 次分配）这次没有命中，所以这个故障只能靠自然运行的统计来观察。
- **修复**：条目持有消息对象。`steer_id_for()` 与 `is_steered_message()` 还要检查"是同一个对象"。`agent.py` 只在消息的 `role` 是 `user` 时才把它交给 Auto（第二道防线）。
- **测试**：均在旧代码上失败：`test_harness_agent.py::test_steer_entry_holds_its_message_so_the_id_cannot_be_reused`；`test_steer_and_btw.py::test_auto_note_gets_only_text_of_the_user_even_if_the_harness_calls_a_message_a_steer`。

### C3. `[x]` 空闲时的 `session/cancel` 会中止下一个 prompt，并挡住 steer

- **问题**：
  1. `begin_abort()` 把全局计数 `_abort_depth` 加 1（第 1854 行）。`abort()` 在结束时减 1（第 2010 行）。
  2. prompt 开始时，只要 `_abort_depth > 0`，就中止这次 run（第 1745 行）。
  3. 如果 `session/cancel` 在没有 run 时到达，紧接着又来一个新 prompt，而那个 `abort()` 还在发通知（例如客户端写入很慢），新 prompt 就以 `aborted` 结束。它与那次 cancel 无关。
  4. 那个 `abort()` 还会调用 `wait_for_idle()`（第 1998 行）。新 run 开始后，它要等到新 run 结束才返回。这段时间里 `is_aborting` 一直为真（第 1844 行），`pi/session/steer` 一直得到 `reason: "aborting"`。
- **复现**：模拟 `PiAcpAgent.cancel()`（`begin_abort()` 加 `abort(_from_begin=True)` 的任务）。订阅者在第一个 `queue_update` 里睡一小段时间。然后立刻发新 prompt。2026-11-27 在 `ec49e28` 上：

  ```text
  delay=  0.0s -> stopReason='aborted' depth_after=0
  delay= 0.01s -> stopReason='aborted' depth_after=0
  delay=  0.2s -> stopReason='aborted' depth_after=0
  ```

  三种延迟都出现问题，包括 0 秒。
- **修复**：prompt 开始时记下"已有多少次 cancel"（`_abort_epoch`）。只有在这之后又来了 cancel，这次 prompt 才以 `aborted` 结束。每个忙碌期有自己的 `_idle_event`。`abort()` 只等它开始时看到的那一个。
- **测试**：`test_cancel_while_idle_does_not_end_the_next_prompt`、`test_cancel_while_idle_does_not_wait_for_the_next_run`（旧代码上失败）。对照测试 `test_cancel_while_the_run_is_being_set_up_still_ends_the_prompt`（新旧代码上都通过）：在 run 准备期间到达的 cancel 仍然生效。

### C4. `[?]` 内容未保留

聊天报告列出过 C4。写本文时，C4 的原文已无法恢复。本文不补写，因为补写等于编造。要记录 C4，请把原报告中的 C4 段落补进本节。

### C5. `[x]` LangChain 私有方法没有版本保护；二进制构建不锁版本；CI 不测 `ChatDeepSeek`

- **问题**：
  1. 适配层重写了 LangChain 的**私有**方法：`ReasoningChatOpenAI._get_request_payload` 与 `_convert_chunk_to_generation_chunk`（`pi_agent_core/adapters/openai_reasoning.py`）；`ReplayingChatDeepSeek._get_request_payload`（`pi_agent_core/adapters/deepseek_replay.py`）。LangChain 不保证私有方法的签名不变。
  2. 根 `pyproject.toml` 里，`langchain-core`、`langchain-openai`、`langchain-anthropic`、`langchain-deepseek` 都没有上界。签名变化后不一定报错：思考内容可能被悄悄丢掉，或者 `reasoning_content` 回放没有发出。
  3. 二进制构建（`agent-binaries.yml`）安装构建当天最新的版本。同一个提交在不同日期构建出不同的程序。
  4. `ci.yml` 安装 `.[dev]` 和本地包。其中没有 `langchain-deepseek`（`pi-agent-cli-lc` 只把 `langchain-openai` 作为基础依赖）。用 `importorskip` 的测试文件（例如 `test_deepseek_replay.py`、`test_openai_replay.py`）在 CI 里被跳过。
- **修复**：
  1. 根 `pyproject.toml`：`langchain-core>=0.3.0,<2`；三个 extras 同样加上 `<2`。
  2. `packaging/agent/constraints.in` 与 `constraints.txt`：74 个精确版本，由 `constraints.in` 开头的命令（`uv pip compile --universal --python-version 3.12`）生成。构建任务用 `pip install -c packaging/agent/constraints.txt …` 安装。
  3. `cryptography<49`：按平台运行 `uv pip install --dry-run --only-binary :all:` 时发现，`cryptography` 49 与 50 没有可用于 macOS x86_64 的 wheel（48.x 有 `universal2`）。没有这个上界，构建在该平台上会失败。
  4. `ci.yml`：测试任务改为 `.[dev,deepseek]`。
  5. `docs/specs/2026-10-31-agent-binaries.md` 新增 §2.3（版本锁定的规则与检查）。
  6. `langchain-mcp-adapters` 故意不设上界：`pi-mcp` 只调用它的两个公开函数（`create_session`、`load_mcp_tools`），不重写它的任何类。
- **测试**：
  - `pi_agent_core/tests/test_langchain_private_api.py`（6 个测试，其中 3 个对 `ChatOpenAI` 与 `ChatDeepSeek` 各跑一次，共 9 个用例）：检查适配层调用的签名仍然成立，失败信息里带方法名。
  - `scripts/tests/test_agent_constraints.py`（8 个测试）：检查固定版本、安装步骤、`constraints.in` 与各 `pyproject.toml` 的上下界互相一致。对每个预期缺陷做了变异检查，它们都会让对应的测试失败；无害的改动（调整顺序）不会。
- **检查结果**（2026-11-27，Windows）：
  - 新增的 17 个测试（9 加 8）在 `.venv` 中通过。
  - 完整测试套件，在只装二进制构建所需包的锁定环境里：6794 通过，116 跳过，33 个被排除，退出码 0。对照基线（6805 通过，88 跳过）：多出 17 个新测试，并且因为缺少 `langchain-deepseek` 与 `langchain-anthropic` 多出 28 个跳过。**这次运行用的是加 `cryptography<49` 之前的锁定**：`cryptography` 是 50.0.2，最终的 `constraints.txt` 是 48.0.1。把 pip 的安装记录与最终的 `constraints.txt` 对照：Windows 上装上的 72 个锁定项里，只有这一个包的版本不同（另外 2 个锁定项只在别的平台安装）。加上界之后没有重跑完整套件。
  - 换成最终锁定（`cryptography` 48.0.1）之后，重跑了 5 组测试：`packages/pi-mcp/tests`、`packages/pi-codemode/tests`、`scripts/tests/test_agent_constraints.py`、`pi_agent_core/tests/test_langchain_private_api.py`、`pi_agent_core/tests/test_openai_reasoning.py`。结果：280 通过，4 跳过（4 个 `ChatDeepSeek` 的参数化用例，因为这个环境里没有 `langchain-deepseek`）。
  - 在最新版本的 CI 式环境里（`langchain-deepseek 1.2.1`、`langchain-openai 1.8.5`、`openai 3.60.0`），适配层与签名相关的 284 个测试全部通过，0 个跳过。
  - 5 个目标平台（linux-x86_64、linux-aarch64、darwin-aarch64、darwin-x86_64、windows-x86_64）的 wheel 可用性检查全部通过。
  - Windows 上用最终锁定（`cryptography` 48.0.1）构建：构建成功（`zycode-agent.exe` 42.0 MB）；冒烟测试通过；ACP 契约测试 13 个通过（路径用正斜杠，原因见 §五 第 8 条）；冻结沙箱测试 167 个通过，12 个按设计跳过（例如 Windows 没有 execute bit）。
  - Ruff：`ruff check .` 与 `ruff format --check .` 通过。运行它们的工作树与 C5 分支的最后一个提交相比，只有 `CHANGELOG.md` 与 `docs/specs/2026-10-31-agent-binaries.md` 不同，所有 `.py` 文件相同。（那个分支的 4 个提交现在压成了 `main` 上的一个提交，见 §6.2。）
  - Linux 与 macOS 的构建只在 CI 里运行，本次没有运行。

---

## 三、规划文档：各项详情

规划描述的代码（daemon、`pi-rust`、桌面端、移动端）还不存在。每项给出规划中的位置、问题和建议。状态都是 `[ ]`：本次没有修订规划文档。

### RM-1. `[ ]` `event_seq` 重放没有"缺口"规则，达不到"零丢失"门禁

- **位置**：§3.2 图（第 290 行）与 `zycode/daemon/attach`（第 315 行）；Phase 1 交付（第 533 行）；Phase 4 门禁（第 579 行）。
- **问题**：
  1. 第 290 行写"Event Bus & Replay Log"，第 533 行写"Event Ring Buffer"。环形缓冲区的容量有限。规划没有写容量，也没有写淘汰规则。
  2. 客户端离线足够久时，它的 `after_seq` 比缓冲区里最老的事件还旧。规划没有写 daemon 此时怎么回答。有界缓冲区自己不能满足第 579 行的"重连事件零丢失"。
  3. JSONL v3 文件不能填补缺口。harness 只在 `message_end` 时写入消息（`agent_harness.py` 的 `_handle_agent_event`）。流式增量（`message_update`）、工具进度、正在生成的消息都不在文件里。
  4. 规划没有"快照"操作（会话现在的状态）。`SessionBackend` 里只有 `load_session`（第 430 行），它没有和 `attach` 的缺口情况连起来。
  5. "审批零重复"还要求：离线期间产生的审批请求，重连的客户端必须能得到。这个信息是 broker 的状态，不是事件。缓冲区淘汰后它不能靠重放恢复。
- **建议**：
  1. 写明缓冲区容量（事件数与字节数）和淘汰规则。给 `event_seq` 加一个"纪元"标识：daemon 重启后缓冲区是空的，序号会重新开始。
  2. `attach(after_seq)` 遇到缺口（或纪元不同）时，返回 `gap: true` 和一份快照。
  3. 快照包含：已落盘的消息（JSONL 的尾部）、正在生成的消息的当前内容、正在运行的工具调用、待处理的审批、steer 队列。
  4. 给门禁加一个可执行的测试：断开连接，在断线期间产生比缓冲区容量更多的事件，再重连。客户端的最终状态必须与一直在线的客户端相同。

### RM-2. `[ ]` `zycode-daemon` 的形态在两处写得不同，生命周期没有定义

- **位置**：§3.2（第 270 行）；§3.3.1 目录树（第 345 行）；§4.3（`zypi attach`，第 448 行）；Phase 4（推送唤醒审批，第 577 行）。
- **问题**：
  1. 第 270 行说 `zycode-daemon` 是"常驻/按需拉起"的进程。目的是支撑多任务后台常驻，以及移动端的断线续传和跨端审批。
  2. 第 345 行说 `desktop/` 的 `src-tauri/` "嵌入 zycode-daemon"。如果 daemon 在桌面应用的进程里，应用关闭时 daemon 也停止。
  3. 这两种形态会改变其他功能。桌面应用关闭时，`zypi attach`（第 448 行）找不到 daemon。手机（第 577 行）无法审批，推送也到不了。
  4. 规划没有写：谁启动 daemon，谁停止它；两个客户端同时想启动它时怎么办（单实例锁）；应用与 daemon 版本不同时怎么办；访问令牌放在哪里。
- **建议**：先选一种形态，再写进 §3.2 与 §3.3.1。常见的做法是：daemon 是单独的可执行文件；桌面应用与 `zypi` 在找不到它时拉起它；用锁文件或 socket 做单实例；令牌文件放在用户目录，权限只给当前用户。把"桌面应用关闭后，手机仍能审批"写成 Phase 4 的验收项。

### RM-3. `[ ]` Daemon 在项目目录里运行 git，规划没有给这些操作写"项目信任"门

- **位置**：§3.1（第 260 行，每个任务一个 Git Worktree）；§3.2 的 `zycode/workspace/*`（第 317 行）与原则 3（第 324 行）；Phase 1（第 533 行，"`gix` + `similar`"）。
- **问题**：
  1. Invariant 9（`AGENTS.md`）要求：项目目录提供的东西，用户确认信任之后才使用。Agent 自己在项目里运行 git（或任何程序）的新位置，必须先问同样的信任。原因是 `git` 会运行仓库自己的 `.git/config` 指定的程序（`core.fsmonitor`、hook、filter）。
  2. 现有代码按此执行。`WorktreeManager._git()` 是"通向 git 的唯一的门"。项目不受信任时，它抛出 `UntrustedProjectError`（`packages/pi-dynamic-workflows/pi_dynamic_workflows/worktree.py` 第 59–76 行，默认 `project_trusted=False`）。
  3. 规划在第 324 行写"完整继承 Invariants 1–9"。这是一句总括。Daemon 新增的操作（创建、合并、清理 Worktree，Diff，Stage，Commit，Push；第 317 行）没有逐个写出信任门。
  4. 在 daemon 里这是主流程：第 260 行让每个新任务默认绑定一个 Worktree。实现时如果漏掉信任门，每个新项目的第一个任务就会运行项目自带的程序。
  5. 规划没有写这些操作是调用 `git` 命令，还是用 `gix`。两者不同：`git` 命令会运行 hook 与 `.git/config` 指定的程序；`gix` 是否运行，要逐项确认。
- **建议**：
  1. 在 `zycode/workspace/*` 的规范里写明：项目不在信任列表里时，创建 Worktree、Commit、Push 返回 `untrusted_project`。`git status` 与 `git diff` 也要写明（前者可运行 `core.fsmonitor`，后者可运行 `.git/config` 里的 diff 驱动）。
  2. 在 `tests/conformance/` 加一个用例：未受信任的项目上，`worktree/create` 被拒绝，并且没有 git 子进程启动。

### RM-4. `[ ]` `pi-rust` 的范围缺口，以及"100% 通过黄金测试集"的证明力

- **位置**：§3.1（第 261 行）；§4.1 路线 B（第 382 行）；§4.2（第 419 行）；Phase 0 交付 5（第 519 行）；Phase 2（第 549、555 行）；D4（第 606 行）。
- **问题**：
  1. **`workflow` 工具没有 Rust 对应物。** `zycode-extensions` 只列 web、goal、subagent、codemode（第 419 行）。但 §3.1 把"`codemode` / `workflow` 子代理进度树"列为桌面端功能（第 261 行），§4.1 路线 B 把"复杂 Python 工作流"列为 `pi-python` 的强项（第 382 行）。如果选路线 A（Rust 成为默认引擎），`workflow` 与已保存的 workflow 在默认引擎上没有对应物。规划没有写这笔代价。
  2. **D4 改变 `codemode` 的默认语言。** 现在的 `codemode` 在 `pi_agent_core.sandbox` 里运行异步 Python。D4 推荐 QuickJS 或 Starlark，Python 只是可选的 sidecar（第 419、606 行）。指定 Python 的脚本、skill 和保存的提示，在默认的 Rust 引擎上不能运行，除非装了 Python sidecar。而 sidecar 需要本地 Python 解释器，这正是 D4 想去掉的依赖。
  3. **黄金测试集没有覆盖这些功能。** Phase 0 交付 5（第 519 行）列出：JSONL v3、Auto Mode 规则语料、ACP 契约、7 个工具的边界、`steer`/`btw` 用例。没有 provider 适配层的行为（Invariants 4 至 7）、跨 provider 回放变换、`codemode`、`workflow`、MCP。Phase 2 的门禁是"100% 通过黄金测试集"（第 555 行）。测试集里没有的功能，这个门禁不能证明它们对等。
- **建议**：
  1. 在 §4.2 写明 `workflow` 的去向（Rust 实现，或只留在 `pi-python`），并写进 §4.1 路线 A 的条件。
  2. 在 D4 写出语言变更对用户脚本的影响，以及 sidecar 方案对"零依赖"的影响。
  3. 把上述缺失的功能加入 `tests/conformance/`，或者把门禁的说法收窄为"通过测试集中列出的项目"。

### RM-5. `[ ]` 两个门禁无法客观判定

- **位置**：§4.1（第 370–381 行）；Phase 2 门禁（第 555–556 行）；Phase 3 门禁（第 568 行）。
- **问题**：
  1. **性能门禁没有阈值。** §4.1 的表给出指标与测量方法（冷启动、内存、体积、工具吞吐、平台、FrontierHarness 30、维护成本），但没有任何一项写"多少算达标"。路线 A 的条件是"全面达标"（第 381 行）。Phase 2 的门禁是"报告落地并完成路线决策确认"（第 556 行）：这是一个决定，不是一个测试。如果在 v1 做完、看到数字之后再定阈值，数字会影响结论。
  2. **基线依赖机器。** §0.1 写冷启动约 1.7 s。2026-11-27 在本机（Windows）上，同一个构建的冒烟测试 `initialize` 计时：第一次启动 13.8 s，第二次 4.3 s。原因未查（可能是首次运行的文件扫描与解压）。门禁必须固定参考机器和"第几次启动"。
  3. **评测结果的可比性。** [`FRONTIER-HARNESS-30-EVAL-REPORT.md`](../benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md) 自己写明：每题只跑一次；同一题的两次运行差别可以很大（`python-statemachine`：60 轮时 F2P 70/72，100 轮时 63/72），所以单次结果不能当作稳定能力（第 93 行）。模型别名 `deepseek-flash` 经内网网关，客户端无法核实它指向哪个模型（第 92 行）。用这样的数据比较两个引擎，噪声可能大于差别。
  4. **Phase 3 门禁引用的工具只能在 Linux 上运行。** 门禁要求 `scripts/tui_pty/` 的全部探针在 Linux、macOS、原生 Windows 上通过（第 568 行）。这些探针用 `pty`、`termios` 与 `/proc`，`scripts/tui_pty/README.md` 写明"Linux only"。macOS 的 PTY 运行只记录在归档的执行日志里（第 10.7、10.10 节）。Windows 没有 `pty` 与 `termios`，所以这些探针不能在原生 Windows 上运行。
- **建议**：
  1. 在 Phase 2 开始之前，把每项指标的阈值、参考机器、运行次数写进 §4.1。FrontierHarness 30 要写明每题跑几次、怎么判、模型怎么固定。
  2. Phase 3 门禁：对 macOS 与原生 Windows，写明用什么代替这些探针（例如基于 ConPTY 的冒烟测试）；或者把门禁收窄到 Linux，再加一份手工检查清单。

### RM-6. `[ ]` `SessionBackend::subscribe` 用 `broadcast`：慢订阅者会丢事件

- **位置**：§4.3（第 443 行的 trait，第 447 行的说明）。
- **问题**：
  1. `subscribe` 返回 `tokio::sync::broadcast::Receiver<BackendEvent>`（第 443 行）。第 447 行说，经 `tokio` channel"零拷贝传递全部事件"。这两点都不成立。
  2. `broadcast` 的缓冲区有界。接收者落后太多时，它的下一次 `recv()` 返回 `RecvError::Lagged(n)`，它丢失了 `n` 条消息（[tokio 文档](https://docs.rs/tokio/latest/tokio/sync/broadcast/index.html)）。TUI 渲染卡顿时就会发生。
  3. `broadcast` 要求 `T: Clone`。每个接收者得到一份克隆，不是同一份数据的零拷贝。事件含大 Diff 时，克隆有代价。
  4. 进程内路径（`InProcessPiRustBackend`）没有 `event_seq`（`event_seq` 在 daemon 里，第 290 行）。丢事件之后，界面显示的内容会悄悄出错，除非代码处理了 `Lagged`。这是 RM-1 的进程内版本。
- **建议**：二选一。(a) 每个订阅者一个有背压的 `mpsc` 通道，慢订阅者让发送方等待，或者被断开。(b) 保留 `broadcast`，事件改为 `Arc<BackendEvent>`，并规定收到 `Lagged` 时必须用 `load_session` 的快照重新同步（与 RM-1 的快照用同一个结构）。再把"全部事件"改成与所选方案相符的说法。

### RM-7. `[ ]` 配置目录规则有 3 份实现；`~/.zycode` 回退没有写完整

- **位置**：§2.4（第 231 行）；§3.2 图（第 293 行）；Phase 0 交付 4（第 518 行）；D1（第 603 行）。
- **规则（第 231 行）**：引入 `ZYCODE_HOME` 与 `~/.zycode`。`~/.zycode` 不存在且 `~/.pi-python` 存在时，自动回退读取 `~/.pi-python`，并继续尊重 `PI_HOME`。
- **现状**：三个组件各有一份实现，并且已经分歧过一次（[R2-5](AUDIT-RUST-RUNTIME-REMOVAL-2026-11-15.md)：Rust 侧没有展开 `~`，已修）：

  | 组件 | 位置 | 规则 |
  |------|------|------|
  | Python | `pi_agent_core/home.py`：`pi_home()` | 参数，否则 `PI_HOME`，否则 `~/.pi-python`。不读 `GROK_HOME` |
  | Rust | `tui/crates/codegen/pi-home/src/lib.rs` | `PI_HOME`，否则 `GROK_HOME`，否则 `<home>/.pi-python`。默认路径对 home 做 `canonicalize`；自己展开 `~` |
  | TypeScript | `vscode/src/core/first-run.ts`：`agentConfigPath()` | `PI_HOME`，否则 `~/.pi-python`。不读 `GROK_HOME` |

- **问题**：
  1. 规则没有写 `ZYCODE_HOME`、`PI_HOME`、`GROK_HOME` 的先后顺序。现在 Rust 读 `GROK_HOME`，Python 与 TypeScript 不读。
  2. 第 231 行只写"回退读取"，没有写写入去哪里。如果某次写入创建了 `~/.zycode`，下一次启动时 `~/.zycode` 已存在，回退不再生效。用户在 `~/.pi-python` 里的 `agent.toml` 和 `sessions/` 就看不见了。
  3. 旧组件（旧 VSIX 里捆绑的旧 agent、PyPI 1.x 的安装）只认识 `~/.pi-python`。新旧组件同时存在时，会使用不同的目录。
  4. 三份实现都要改。
- **建议**：只写一条规则，包含读、写，以及"两个目录都存在"时的行为。把规则写成一张测试向量表（环境变量、已存在的目录、预期的读路径与写路径），放进 `tests/conformance/`，让 Python、Rust、TypeScript 都运行它。再选一种迁移方式：要么"回退时读写都用旧目录，直到用户显式迁移"，要么"首次启动迁移一次，并留下标记文件"。

### RM-8. `[ ]` 三处文档不准确

- **位置**：§0.1（第 20–24 行）；§2.2（第 154 行）；§5.2（第 503 行）与 §6（第 588 行）。
- **问题**：
  1. **§0.1 的数字复现不出来。** 文档没有写计数规则，所以无法判断差别是否来自规则。本次的计数规则见 §六。

     | 项 | §0.1 写的 | 2026-11-27 的计数 |
     |----|-----------|-------------------|
     | Python 非测试源码（`pi_agent_core/` 加 7 个 `packages/*`） | 35,101 行 | 36,494 行 |
     | Python 测试数 | 4,400+ | 6,926 个（6,805 通过，88 跳过，33 个被 `addopts` 排除） |
     | Rust（`tui/`） | 958,555 行；80 crates | 990,088 行；80 个 workspace 成员 |
     | VS Code 的 TypeScript | 11,110 行 | `src/` 12,037 行；`test/` 11,039 行；合计 23,086 行 |
     | `vscode/src/core/` 纯核心模块 | 23 个，零 `vscode` 依赖 | 23 个，0 个导入 `vscode`（一致） |

     差别最大的是测试数和 TypeScript 行数。
  2. **§2.2 对 Open VSX 拒签原因的说明不准确。** 第 154 行说拒签的原因是 OIDC 令牌的 `repository` claim 变了。Open VSX 的 GitHub 提供方（[`GitHubTrustedPublishingProviderSupport.matches()`](https://github.com/eclipse-openvsx/openvsx/blob/main/server/src/main/java/org/eclipse/openvsx/trustedpublishing/github/GitHubTrustedPublishingProviderSupport.java)，2026-11-27 读取 `main` 分支的源码）按四项匹配：`repository_id` 与 `repository_owner_id`（两个数字 ID，改名后不变）、`workflow_ref`（登记时不带 `@ref`，里面有仓库名，改名后会变）、`environment`。`repository` 在这里只是"名称类" claim，不参与匹配。所以改名后仍会被拒，原因是 `workflow_ref`。结论与步骤（删旧条目、建新条目）不受影响。
  3. **§5.2 与 §6 对 steer 的声明，在修复 C1 之前不成立。** 第 503 行与第 588 行写"run 结束未送达 steer 清理"和"任何 run 结束……均清空未消费 steer 并回报"。C1 说明：已被 agent loop 取走的 steer 没有被回报。C1 的修复合并到 `main` 之后（§6.2 的第一个提交），这两处声明成立，文档不用改。
- **建议**：
  1. 在 §0.1 的表里加一行"计数方法"（或给出命令），并更新数字。
  2. 改正第 154 行的原因说明。
  3. 合并 C1 的修复（已合并，见 §6.2）。

### 处理顺序（建议）

| 时间点 | 先定的项 | 原因 |
|--------|----------|------|
| Phase 0 | RM-7、RM-8 | RM-7 就是 Phase 0 的交付 4；RM-8 只是文档改动 |
| Phase 1 开始前 | RM-1、RM-2、RM-3 | 它们改变协议和 daemon 的形态，Phase 1 要实现它们 |
| Phase 2 开始前 | RM-4、RM-5 | 黄金测试集与门禁阈值，要在 `pi-rust` v1 做完之前定下 |
| Phase 3 开始前 | RM-6 | `SessionBackend` trait 在 Phase 3 抽离 |

---

## 四、核对后正确的部分

以下陈述已对照代码核对，是正确的：

1. §2.1 与 §2.2 对工作流的引用。`.github/workflows/release.yml` 第 198–215 行是 `publish-pypi` 任务（`environment: pypi`，用 `PYPI_API_TOKEN`）。`.github/workflows/vscode-extension.yml` 第 154–204 行是发布任务（`environment: vscode-publish`，`id-token: write`，`ovsx publish --trusted-publishing`，`vsce publish --oidc`，`--skip-duplicate`）。
2. §2.2 的结论：改名后必须更新 Open VSX 的登记。（原因的说明见 RM-8。）
3. §0.1：`tui/` 的 workspace 有 80 个成员。`vscode/src/core/` 有 23 个模块，没有一个导入 `vscode`。
4. §3.2 原则 3 引用的 Invariants 1–9、9b、9c、9d 都在 `AGENTS.md` 里。
5. §5.2 的 Pre-Refactor 交付存在：`snapshot_context_for_btw()`（`agent_harness.py` 第 1416 行）；X9 与 X10 已在 [ACP 扩展登记](../specs/2026-11-05-acp-extension-registry.md)里登记。
6. §3.2 原则 2（JSONL v3 是真源）与代码一致：harness 在 `message_end` 时写入消息。这个设计是对的，它的限制见 RM-1。

---

## 五、未核实的部分与限制

1. **C4 与 R1–R9 的原文。** 见文首。要记录它们，请把聊天报告里的对应段落补进本文。
2. **没有运行的测试。** 规则要求推送前在 Windows 与 WSL（Linux）上都运行完整 `pytest`。合并后的代码在两处都运行了，结果见 §6.2。没有运行的部分：macOS；Linux 与 macOS 的二进制构建（只在 CI 里运行）；Python 3.11 与 3.13（CI 的矩阵有它们，这次只用了 3.12）；TUI 的 Rust 端到端测试 `tui/crates/codegen/pi-pager/tests/python_agent_e2e.rs`（它用真实的 Python 代理测试 `session/cancel` 与 steer，推送后由 CI 的 `tui-e2e.yml` 运行）。C5 的锁定环境里的完整测试套件，是在加 `cryptography<49` 之前的锁定上运行的，之后只重跑了 5 组测试（见 C5 的检查结果）。§6.2 的两次完整运行用的是开发环境（`.venv` 与 `/tmp/wsl-venv`），不是用 `constraints.txt` 装出来的环境。
3. **VS Marketplace 的 OIDC 机制。** 只读了 Open VSX 的源码。Microsoft 一侧的匹配规则没有重读。
4. **Cloudflare Workers Builds 与 `web/` 部署。** 规划里的相关陈述没有核对。
5. **规划描述的未来代码。** daemon、`pi-rust`、桌面端、移动端还不存在。RM-1 至 RM-7 是设计审查，不是测试结果。
6. **§0.1 的体积与性能数字。** 没有重新测量。本机 Windows 构建的 `zycode-agent.exe` 是 42.0 MB（zip 41.4 MB），§0.1 写 `win32-x64` 为 42.7 MB。构建日期与依赖版本不同，所以不能直接比较。启动时间见 RM-5。
7. **构建日志里的两条警告。** PyInstaller 构建成功，冒烟测试通过，但日志里有两条警告：`langchain_openai.middleware` 收集失败（它需要没有安装的 `langchain` 包）；`Hidden import "tzdata" not found`。没有查原因。冒烟测试会运行冻结程序，所以如果以后某个版本在运行时需要 `langchain_openai.middleware`，冒烟测试会发现它。
8. **测试辅助函数的一个陷阱（未改）。** `test_acp_stdio_contract.py` 用 `shlex.split` 解析 `PI_ACP_CONTRACT_COMMAND`。在 Windows 上，带反斜杠的路径会被当成转义，被测程序的路径变成 `D:workpi-python-…`，13 个测试全部失败。CI 用正斜杠路径（`as_posix()`），所以不受影响。

---

## 六、证据与基线

### 6.1 基线

`main` @ `ec49e28`，Windows，`.venv\Scripts\python.exe -m pytest`：6805 通过，88 跳过，33 个被排除，用时 823.70 s，退出码 0。

### 6.2 合并记录与检查结果

C1–C3、C5 的修复和本文，在 `main` 上是三个提交，顺序如下，没有合并提交。修复先在各自的分支上写成，再压成这三个提交：C5 分支的 4 个提交压成一个。本文放在最后，所以它能写出前两个提交的哈希。

| 提交 | 内容 |
|------|------|
| `b73298c` | C1–C3：6 个文件，+482/−24 行；8 个新测试，其中 5 个在 `ec49e28` 上失败，3 个是对照（新旧代码上都通过） |
| `184657b` | C5：9 个文件，+789/−10 行；17 个新测试（`test_langchain_private_api.py` 9 个用例，`test_agent_constraints.py` 8 个） |
| 本文所在的提交 | 只改本文 |

**合并后的检查**（2026-11-27）。测试是在压成三个提交之前运行的（当时 `main` 是两个合并提交的结果）。压缩前后的两棵树只有本文不同，用 `git diff` 比较过，所以结果适用于 `184657b` 的代码。

| 环境 | 命令 | 结果 | 用时 |
|------|------|------|------|
| Windows 10，Python 3.12，`.venv` | `.venv\Scripts\python.exe -m pytest` | 6830 通过，88 跳过，33 个被排除，退出码 0 | 942.43 s |
| WSL 2（Ubuntu），Python 3.12.3，`/tmp/wsl-venv` | `/tmp/wsl-venv/bin/pytest`，在 `/mnt/d/work/pi-python` 里运行；运行前清除了 `PI_HOME` | 6705 通过，18 跳过，33 个被排除，退出码 0 | 521.50 s |

- 与 §6.1 的基线（6805 通过，88 跳过）相比，Windows 多 25 个通过，跳过数不变。这 25 个就是新测试：8 个（C1–C3）加 17 个（C5）。
- 两处的收集总数不同：Windows 6951 个（6830 + 88 + 33），WSL 6756 个（6705 + 18 + 33）。差 195 个，正好是 `packages/pi-mcp/tests` 的测试数。WSL 的环境没有安装 `pi-mcp-py` 与 `mcp`，所以这些测试没有被收集。用 `--collect-only` 单独核对过：Windows 收集 195 个，WSL 收集 0 个。
- 两处的 LangChain 版本不同。Windows：`langchain-core 1.5.4`、`langchain-openai 1.4.3`、`langchain-deepseek 1.1.0`、`openai 2.54.0`。WSL：`langchain-core 1.6.6`、`langchain-openai 1.6.6`、`langchain-deepseek 1.1.1`、`openai 3.22.1`。两处都没有 `langchain-anthropic`。17 个 C5 新测试在两处都通过，没有跳过（单独运行，两处都是 17 通过）。
- 没有在 `ec49e28` 上运行 WSL，所以 WSL 没有基线数字。
- 两个运行同时开始（16:43），都没有失败，没有重跑。
- Ruff：`ruff check .` 与 `ruff format --check .` 通过。

**C1–C3 在合并之前的检查结果**（2026-11-27，Windows，在 `b73298c` 上；当时它在自己的分支里）。两组测试都在该分支的独立工作树里运行。运行前打印了每个包的 `__file__`，确认模块来自该工作树，不是 `ec49e28`。

| 测试组 | 结果 | 用时 |
|--------|------|------|
| `packages/pi-agent-harness/tests` 与 `packages/pi-agent-cli/tests` | 4606 通过，48 跳过，1 个被排除，退出码 0 | 416.21 s |
| 其他调用 `AgentHarness.prompt()` 或 `abort()` 的包：`pi-mcp`、`pi-codemode`、`pi-goal-x`、`pi-dynamic-workflows` 的全部测试，加上 `pi_agent_core/tests/` 里的 `test_extensions.py`、`test_extension_packaging.py`、`test_extension_api_rules.py` | 818 通过，28 跳过，退出码 0 | 304.95 s |

- 第一组在 `ec49e28` 上收集 4647 个测试，在 `b73298c` 上收集 4655 个（4606 + 48 + 1）。多出的 8 个就是新测试（7 个在 harness，1 个在 CLI）。没有单独测量 `ec49e28` 上这一组的通过数，所以对比靠收集数。
- Ruff：`ruff check .` 与 `ruff format --check .` 在该分支上通过。
- 当时没有运行整棵树的完整 `pytest`；合并之后运行了，见上。

### 6.3 复现脚本

三个脚本写在审计者的临时目录里，不在仓库内。要重写它们，用 `packages/pi-agent-harness/tests/test_harness_agent.py` 的辅助函数（`_memory_session`、`_model`、`_tool_once_stream`、`_patch_loop_config`）：

1. **C1**：`max_turns=1`；在第二次 steering 轮询时调用 `steer()`；运行 `prompt()`；打印 `steer_queue`、`_steer_ids` 和 `queue_update` 事件。
2. **C2**：把 C1 的场景重复 300 次，再运行一个后续 prompt；对每条 `message_start` 的消息调用 `is_steered_message()`；统计被误判的试验数和消息角色。
3. **C3**：`begin_abort()` 加 `abort(_from_begin=True)` 的任务；订阅者在第一个 `queue_update` 里 `sleep(delay)`；立刻 `prompt()`；打印 `stopReason`。

### 6.4 计数规则（RM-8）

- **Python**：`pi_agent_core/` 与 `packages/*` 下的 `.py` 文件。路径含 `tests` 或 `test` 目录、文件名为 `test_*.py` 或 `*_test.py`、或文件名为 `conftest.py` 的算测试，其余算源码。每个文件按 `splitlines()` 计行。
- **Rust**：`tui/` 下的 `.rs` 文件，按行计。
- **TypeScript**：`vscode/` 下的 `.ts` 文件。`test/` 目录下或文件名含 `.test.` 的算测试，`src/` 下的算源码。
- 所有计数都排除 `.venv`、`.venv-test-real`、`node_modules`、`target`、`.git`、`__pycache__`、`dist`、`out`。
