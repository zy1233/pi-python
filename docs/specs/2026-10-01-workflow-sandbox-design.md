# Workflow 脚本沙盒 — 设计方案

> Scope：`packages/pi-dynamic-workflows` 运行 LLM 所写 workflow 脚本的方式。
> 取代 Phase 7 规格 §9.3 里「受限命名空间 + 进程内 `exec`」的运行时；
> 同时约束脚本给子 agent 指定的 `cwd`（§8）。
>
> 来源：`docs/AUDIT/AUDIT-PHASE6-PHASE7-2026-09-30.md` 第五节第 1、2 行与第七节第 2、3 项（批 5）。
>
> 代码：`pi_dynamic_workflows/sandbox/{host,child,winjob}.py`、`paths.py`、`runtime.py`。
> 测试：`tests/test_sandbox_isolation.py`、`tests/test_sandbox_protocol.py`、`tests/test_subagent_cwd.py`。

---

## 1. 背景与目标

旧运行时把脚本在宿主自己的解释器里 `exec`，只收窄了 `__builtins__`。审计（P7-01）实测两条路径都能走出命名空间：

- `agent.__globals__['__builtins__']['__import__']`：`agent` 是宿主模块的函数，它的 `__globals__` 就是宿主模块的全局；
- `().__class__.__base__.__subclasses__()` → `catch_warnings.__init__.__globals__` → `sys`。

走出去之后，脚本拿到的是宿主的一切：环境变量（API key）、文件、网络、事件循环。同一个进程里还有一个与「逃逸」无关的问题：脚本里的 `while True: pass` 会让宿主的事件循环永远停住（`cancel`、超时都无从谈起）。

**目标**

1. 脚本跑在**独立进程**里，那里没有宿主的环境、没有宿主的代码、没有宿主的事件循环。
2. 内核给这个进程**资源限制**：内存、CPU 时间、进程数、文件描述符；脚本卡死时宿主照常运转，还能杀掉它。
3. 一切有副作用的事（起子 agent、记日志、问预算）都走管道，由宿主执行；宿主不信任管道另一头发来的任何东西。
4. 脚本的写法与语义不变（`agent` / `parallel` / `pipeline` / `phase` / `log` / `budget` / `args` / `cwd` / `result`），已有 workflow 与 journal 照常工作。

**非目标**：对「专门写来突破沙盒的脚本」提供容器级隔离。第 9 节逐平台列出突破审计钩子之后脚本还能做什么；要对抗这类脚本，请把整个 agent 放进容器或受限用户里运行。

---

## 2. 威胁模型

脚本出自 LLM，而 LLM 的输入里有工作区文件、网页、工具结果，都可能带注入。两类风险：

| 类别 | 例子 | 本设计的回答 |
|------|------|-------------|
| 失控 | 死循环、吃光内存、刷屏、递归调用 `agent` | 进程边界 + 内核限制 + 宿主侧的数量上限与背压 |
| 越权 | 读环境变量、读写文件、起进程、联网 | 清空环境、审计钩子（挡住常规写法）、内核层的限制（挡住钩子被绕过后的一部分） |

防线按强度分层，**只有前三层之外的才算边界**：

| 层 | 是什么 | 性质 |
|----|--------|------|
| L0 | 命名空间只暴露编排用的几个名字 | 引导脚本，不是边界 |
| L1 | 审计钩子（PEP 578）拒绝 `open`、`os.*`、`socket.*`、`subprocess.*`、`ctypes.*` 等事件与相应模块的首次导入 | 速度栏：钩子是 Python，Python 代码改得掉它 |
| L2 | 独立进程；清空环境；空的临时工作目录；`-I -S -B -X utf8`；fd 0/1 指向空设备 | 进程边界 |
| L3 | 内核限制：POSIX 的 rlimit、`NO_NEW_PRIVS`、`RLIMIT_NPROC=0`；Windows 的 Job Object、低完整性 | 钩子被绕过后仍然有效 |
| L4 | 宿主把管道另一头当作不可信输入（§4.3） | 脚本不能借协议伤到宿主 |
| L5 | 子 agent 的每次工具调用走权限策略（`tool_call_gate`，Phase 7 规格 §12） | 副作用的真正约束，不因本设计改变 |

---

## 3. 架构

```
WorkflowRuntime.execute()
   │  ScriptSandbox.run(script, args, cwd, budget_total, host=_RuntimeHost)
   ▼
┌──────────────── 宿主进程 ────────────────┐        ┌──────── 脚本进程（child.py）────────┐
│ host.py: ScriptSandbox / _Child          │ stdin  │ 只有标准库                          │
│  · 起进程、套 Job Object、墙钟、清理     │───────▶│ 读线程 → 事件循环                   │
│  · 读消息 → 校验 → 交给 _RuntimeHost     │ stdout │ 命名空间里是 agent/parallel/...     │
│  · 回复 call、转发 log/phase             │◀───────│ 每次 agent() 发一条 call，等 reply  │
└──────────────────────────────────────────┘ 行式JSON└─────────────────────────────────────┘
   _RuntimeHost: 计数、预算、journal 重放/写入、信号量、SubagentExecutor
```

| 文件 | 职责 |
|------|------|
| `sandbox/host.py` | `ScriptSandbox`、`SandboxLimits`、错误类型、协议校验、进程生命周期 |
| `sandbox/child.py` | 脚本进程里跑的程序：只依赖标准库，不导入本包 |
| `sandbox/winjob.py` | Windows Job Object（ctypes），仅 Windows 导入 |
| `runtime.py` | `WorkflowRuntime` 把运行交给沙盒；`_RuntimeHost` 实现沙盒向宿主要的那几件事 |
| `paths.py` | 子 agent 的 `cwd` 约束（§8） |

### 3.1 进程怎么起

`python -I -S -B -X utf8 <child.py> --memory N --cpu N --nofile N --max-message N [--no-audit]`

- **解释器**用 `sys._base_executable`：Windows 上 venv 的 `python.exe` 是个启动器，会再起真正的解释器，那样第二个进程会被「只允许一个进程」的 Job 拒绝。
- **环境**清空。Windows 保留 `SYSTEMROOT` / `SYSTEMDRIVE` / `WINDIR`（没有它们起不了 socket，而事件循环要用）；Linux 上 Python 自己会补 `LC_CTYPE`。
- **工作目录**是空的临时目录 `pi-workflow-*`，结束后删除；脚本里的 `cwd` 变量只是宿主告诉它的一个字符串。
- **fd 0 / 1**：子进程先 `dup` 出管道，再把 0 和 1 指向空设备，所以脚本（或它调用的库）打印什么都进不了协议；`print` 在命名空间里就是 `log`。
- **会话**：POSIX 用 `start_new_session=True`，Windows 用 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`，终端上的 Ctrl-C 不会直接打到它。
- **只用标准库**：`-S` 之后 site-packages 不在 `sys.path`，脚本进程里没有宿主的任何代码可以借道。

### 3.2 一次运行的顺序

1. 宿主把 `run{script, args, cwd, budget}` 编码好，超过 `max_message_bytes` 就在起进程之前报错。
2. 起进程；Windows 上随即把它放进 Job Object。
3. 子进程：dup 管道、空设备、事件循环、读线程；POSIX 施加 rlimit；Windows 降到低完整性；然后忘掉 `ctypes`（§5）；发 `ready`。
4. 宿主收 `ready`（限时 `startup_seconds`），发 `run`。
5. 子进程收到 `run` 后**才**挂上审计钩子，发 `started{layers}`，再运行脚本：`exec` 脚本、如果有 `async def main` 就 `await main()`。
6. 脚本运行期间，子进程发 `call` / `log` / `phase` / `result`，宿主发 `reply`。
7. 结束：`done{meta}`，或 `error{etype, message, line}`。宿主 `finally` 里先杀进程，再取消并等待仍在进行的宿主任务，最后清理管道、Job、临时目录。

---

## 4. 协议（v1）

每条消息是一行 JSON（`ensure_ascii`，所以不含裸换行）。

| 方向 | `t` | 字段 | 含义 |
|------|-----|------|------|
| 子→宿主 | `ready` | `protocol` | 握手 |
| 宿主→子 | `run` | `script`、`args`、`cwd`、`budget{total,spent}` | 开始 |
| 子→宿主 | `started` | `layers[]` | 钩子已挂上；报告子进程实际施加了哪些层 |
| 子→宿主 | `call` | `id`、`fn="agent"`、`prompt`、`opts{}` | 请求起一个子 agent |
| 宿主→子 | `reply` | `id`、`ok`、`value` 或 `etype`+`message`、`spent` | 答复；`spent` 让脚本里的 `budget` 视图跟上 |
| 子→宿主 | `cancel` | `id` | 脚本放弃了一个还没答复的调用 |
| 子→宿主 | `log` | `msg` | `log()` / `print()` |
| 子→宿主 | `phase` | `title` | `phase()` |
| 子→宿主 | `result` | `value` | `result(value)`，最后一次为准 |
| 子→宿主 | `done` | `meta` | 脚本正常结束 |
| 子→宿主 | `error` | `etype`、`message`、`line` | 脚本抛了异常 |

### 4.1 值怎么过边界

值以 JSON 过边界：`args`、`result(...)`、`agent()` 的返回值与参数。不是 JSON 的对象按 `default=str` 变成字符串。`result` 太大（超过 `max_message_bytes`）在**脚本里**当场抛 `ValueError`，不是到宿主才失败。

### 4.2 `agent()` 的选项

脚本给的选项是不可信数据，宿主逐项检查类型再用：`tier` / `model` / `label` / `phase` / `cwd` 必须是字符串，`timeout_ms` 必须是数字（不接受 `bool`），`schema` 必须是 dict。不合格的调用在**占用 agent 名额之前**以 `TypeError` 回给脚本。

**一次调用属于它发出时的 phase。** 宿主把每个 `call` 交给自己的一个任务去处理，等任务跑起来，脚本可能已经进入下一个 phase（并行的另一个 thunk 调了 `phase()`）。所以子进程在 `call` 里带上当前 phase（脚本显式传的 `phase=` 优先），宿主据此执行并计算 journal 哈希。宿主一侧不再维护「当前 phase」。

### 4.3 宿主怎么对待子进程

子进程发来的一切都按不可信输入处理：

| 情形 | 处理 |
|------|------|
| 一行超过 `max_message_bytes` | `SandboxProtocolError`，杀进程（`StreamReader` 的上限是该值加 1024） |
| 不是 JSON / 嵌套太深解不出 / 不是带字符串 `t` 的对象 | `SandboxProtocolError` |
| 未知的 `t`、字段缺失或类型不对（`bool` 不算整数）、`fn` 不是 `agent` | `SandboxProtocolError` |
| 重复使用一个仍在进行的 `call` id | `SandboxProtocolError` |
| `cancel` 一个不存在的 id | 忽略 |
| 在途 `call` 达到 `max_inflight_calls` | 宿主**不再读**，直到有调用答复（背压，不是拒绝） |
| `log` 超过 `max_log_lines` / `max_log_chars` | 丢弃，并记一条「后续日志已丢弃」 |
| `phase` 超过 `max_phases` 个不同标题 | 忽略新标题（回到已见过的标题仍然有效）；标题截到 500 字符 |
| 进程在一条消息中途结束 | `SandboxCrashed`（不是协议错误） |
| 伪造 `error` | 等同于脚本自己抛异常，脚本本来就能这样做 |

---

## 5. 逐平台实际施加的层

`ScriptOutcome.layers` 报告**实际生效**的层（子进程自己报告，宿主补上它负责的），测试按平台断言。

| 层（名称） | Linux | macOS | Windows |
|-----------|-------|-------|---------|
| 独立进程、清空环境、空 cwd、fd 0/1 空设备（`process`） | ✓ | ✓ | ✓ |
| 审计钩子（`audit-hook`） | ✓ | ✓ | ✓ |
| `RLIMIT_CORE`=0、`RLIMIT_AS`、`RLIMIT_CPU`、`RLIMIT_NOFILE`、`RLIMIT_FSIZE`=0，软硬同值，脚本改不回去（`rlimit-core` / `-as` / `-cpu` / `-nofile` / `-fsize`） | ✓ | 能设置的才算（`RLIMIT_AS` 不一定） | — |
| `PR_SET_NO_NEW_PRIVS`（`no-new-privs`） | ✓ | — | — |
| `RLIMIT_NPROC`=0：不能 `fork`、不能再起线程；在读线程起来**之后**才设（`rlimit-nproc`） | ✓（root 不受约束） | — | — |
| Job Object：进程内存上限、CPU 时间上限、`ActiveProcessLimit=1`、UI 限制、`DIE_ON_UNHANDLED_EXCEPTION`、`KILL_ON_JOB_CLOSE`（`job-object`） | — | — | ✓ |
| 低完整性（`low-integrity`） | — | — | ✓ |
| 墙钟（`wall_seconds`，默认关闭）：到点杀进程 | ✓ | ✓ | ✓ |

macOS 没有测过（CI 只有 Linux，开发机是 Windows 与 WSL）。

**Job 的指派**：宿主在进程刚起来就把它放进 Job，早于脚本的任何代码。放进去失败按 `SandboxUnavailable` 处理，什么都不跑。宿主进程崩溃时，Job 句柄随进程关闭，`KILL_ON_JOB_CLOSE` 杀掉脚本进程；管道也会断，子进程的读线程见到 EOF 就 `os._exit(3)`，两条路各自独立。

**低完整性**：子进程用 `SetTokenInformation(TokenIntegrityLevel)` 把自己降到低完整性，不需要特权，也升不回去。之后系统无视用户自己的权限，拒绝它写处于普通完整性的对象（实测：临时目录与用户目录里的文件），拒绝读同用户普通完整性进程的内存（宿主也在其中）和复制它们的句柄（实测）。读文件与联网不受影响。

**忘掉 `ctypes`**：设置限制要用 `ctypes`（`prctl`、令牌 API）。用完后把 `ctypes` / `_ctypes` 从 `sys.modules` 里摘掉：钩子只在模块**需要被加载**时看到 `import` 事件，留着就等于把现成的 `ctypes` 递给走出命名空间的脚本。

---

## 6. 限制与默认值

`SandboxLimits`（`WorkflowRuntime(sandbox_limits=...)`）：

| 项 | 默认 | 说明 |
|----|------|------|
| `memory_bytes` | 512 MiB | POSIX 是地址空间，Windows 是已提交内存 |
| `cpu_seconds` | 60 | 进程自己用掉的 CPU 时间，不含等待；Windows 上检查比较粗（实测 1 s 的限制约 8 s 才生效） |
| `wall_seconds` | `None` | 整体时限，`None` 表示直到运行被中止 |
| `open_files` | 64 | POSIX |
| `max_message_bytes` | 4 MiB | 单条消息，两个方向都受限 |
| `max_inflight_calls` | 64 | 宿主还没答复的 `call` 数 |
| `max_log_lines` / `max_log_chars` | 5000 / 1 MiB | |
| `max_phases` | 200 | 不同标题的个数 |
| `startup_seconds` | 30 | 等 `ready` |

---

## 7. 失败语义

| 情形 | 调用方看到 |
|------|-----------|
| 脚本抛异常 | `WorkflowScriptError(RuntimeError)`：`str()` 就是脚本自己的消息（不加前缀），另有 `error_type`、`line`（脚本里最内层的那一行；`SyntaxError` 带自己的行号）。消息截到 20000 字符 |
| 宿主一侧的错误（预算、数量上限、选项类型、`cwd` 越界、子 agent 异常）| 在脚本里以同名内建异常抛出（`Exception` / `RuntimeError` / `ValueError` / `TypeError` / `KeyError` / `IndexError`），其他类型变成 `RuntimeError`；脚本可以 `except` |
| 超过墙钟 | `SandboxLimitExceeded` |
| 超出内存 / CPU 被内核终止、进程崩溃 | `SandboxCrashed`，写明退出码或信号名，并附子进程 stderr 的末尾 2 KiB |
| 协议被破坏 | `SandboxProtocolError`（§4.3） |
| 起不来（解释器找不到、Job 套不上、事件循环不支持子进程、握手超时或版本不符、脚本本身大到发不出去） | `SandboxUnavailable` / `SandboxLimitExceeded`，什么都没跑 |

`SandboxError` 的各子类都是 `RuntimeError`，`workflow` 工具照旧显示 `Workflow failed: {e}`。

**取消**：宿主收到取消（任务被取消，或工具所在 turn 被中止）时，`finally` 先杀进程（此后不再读、也没有东西在运行或花钱），再取消并等待所有在途的宿主任务（子 agent 在此收尾），最后清理。子进程里，一个未答复的 `agent()` 被取消时发 `cancel`，宿主据此停止对应的工作。

---

## 8. 与运行时的衔接

- **预算**：`budget` 在脚本里是只读视图（`total` / `spent()` / `remaining()` / `exceeded()`），账本在宿主；每条 `reply` 带 `spent`，视图取最大值。旧运行时里脚本能 `budget.add`，现在不能。
- **journal**：哈希的构成与旧运行时完全相同（prompt、tier、model、label、phase、schema、cwd），所以已有的 journal 仍能重放；失败不写入 journal。
- **并发**：`max_agents`、`concurrency` 信号量、`_count_lock` 都在宿主，语义不变。
- **进程数**：一次 `execute` 一个脚本进程（不是一次 `agent()` 一个）。起进程的代价：Windows 约 0.14 s，Linux（WSL）约 0.06 s。
- **事件循环**：Windows 上需要默认的 Proactor 循环，`SelectorEventLoop` 起不了子进程，按 `SandboxUnavailable` 报。

### 8.1 子 agent 的 `cwd` 约束

脚本用 `agent(..., cwd=)` 给子 agent 指定工作目录，现在必须落在项目目录（`WorkflowRuntime.cwd`）之内：

| 规则 | 说明 |
|------|------|
| 相对路径相对**项目目录**解析 | 不是宿主进程的当前目录 |
| 符号链接与目录联接（junction）被跟随后再比较 | 链接指向项目外就是越界 |
| 越界抛 `ValueError`；不是字符串抛 `TypeError`；含 NUL 字符抛 `ValueError` | 都在脚本里以同名异常出现，可以 `except` |
| 检查发生在**占用 agent 名额之前**，也在 journal 重放之前 | 收紧后的策略不能被一次 resume 绕过 |
| journal 哈希保留脚本给的原始 `cwd` | 与旧运行时兼容 |
| 执行器收到的是解析后的真实路径；`HarnessSubagentExecutor` 在建 worktree 之前也检查一次 | 独立使用执行器时同样受约束 |

---

## 9. 残余风险与后续

**审计钩子是速度栏。** 它是 Python 代码，走出命名空间的脚本可以改它（例如通过 `agent.__globals__` 重新绑定 `_DENIED_FAMILIES`）。所以边界是 L2 与 L3：下表是钩子被绕过后，脚本在各平台上**还能做**什么（Linux 栏与 Windows 栏的「实测」项都在本机跑过；其余是依据机制的推断，标明）。

| 能力 | Linux | Windows |
|------|-------|---------|
| 读用户能读的文件 | 能（实测） | 能（实测） |
| 写文件内容 | 不能：`RLIMIT_FSIZE=0` 使 `write` 返回 `EFBIG`（实测） | 不能：低完整性（实测：临时目录与用户目录；写入低完整性标记的位置推断可以） |
| 清空、删除、改名、建目录 | **能**：`open(path, "w")` 把现有文件截成 0，`unlink` / `rename` / `mkdir` 都行（实测） | 不能（同上；删除与改名同属写权限，推断） |
| 联网 | 能（实测：连本地 TCP 服务） | 能（推断：低完整性不限制网络；没有逐项实测） |
| 起进程、起线程 | 不能（`fork` 得 `EAGAIN`，起线程得 `RuntimeError`；root 不受约束）（实测） | 不能（Job：`WinError 1816`）（实测） |
| 读宿主的环境变量与内存 | **能**：`/proc/<pid>/environ` 同用户可读，宿主与同用户的其他进程都在内；没有启用 Yama 的系统（例如 WSL2 的内核）上还能 `ptrace` 附着并读 `/proc/<pid>/mem`（实测，附着成功）。清空子进程环境挡不住这条路 | 不能：读内存与复制句柄都被拒（实测） |
| 杀掉宿主 | 能（同用户可发信号；实测只确认了权限） | 能（实测：低完整性下仍能以 `PROCESS_TERMINATE` 打开宿主） |
| 耗尽内存 / CPU / 描述符 | 受限 | 受限 |

其余已知限制：

- **root 不受 `RLIMIT_NPROC` 与权限位约束**；以 root 运行 agent 时 Linux 上几乎没有 OS 层的保护。不要这样做。
- **Windows CPU 限制检查粗**：`cpu_seconds=1` 的脚本约 8 秒后才被杀。墙钟（`wall_seconds`）更及时，但默认关闭。
- **worktree 模式忽略 `cwd` 的子目录**：`isolation=True` 时子 agent 在 worktree 根运行（`cwd` 约束本身仍然检查）。
- **符号链接竞态**：`cwd` 在检查与使用之间可被改动（TOCTOU）。脚本自己不能写文件，要改只能通过已受权限策略约束的子 agent。
- **脚本能让宿主忙**：循环调用 `agent()` 捕获错误再调用，每条消息宿主都要处理。有上限与背压，宿主仍可响应和取消，但不是零成本；旧运行时里同样的写法会冻住宿主。

**后续（未做）**

1. **Linux 文件系统与 `/proc` 的隔离**。Landlock（内核 ≥ 5.13 管文件，≥ 6.7 还管 TCP）是最合适的：非特权、按路径。本机 WSL 内核是 5.10，没有 Landlock，无法验证，所以没做。用户命名空间 + 挂载命名空间也能做到，但依赖发行版配置（Ubuntu 24.04 默认限制非特权用户命名空间；Docker 默认禁止 `unshare`），要有回退。
2. **seccomp**：只能是黑名单（拒 `ptrace`、`process_vm_*`、`socket`、写方式的 `openat`、`unlink*` 等），还要区分架构；白名单要随 glibc / Python 版本维护。排在 Landlock 之后。
3. **Windows 限制读文件与联网**：AppContainer 或受限令牌。按机制推断会很脆弱（受限令牌下 Python 可能读不了自己的标准库，因为用户目录下的 ACL 不含 Everyone），没有尝试，所以没做。
4. **macOS 的限制层**（`sandbox-exec` 已被弃用）没有验证过。

---

## 10. 测试与验证

| 文件 | 内容 |
|------|------|
| `tests/test_sandbox_isolation.py` | 进程、环境、cwd、打印不进协议、解释器标志；钩子拒绝的常规路径；钩子的策略逐条列出（被拒的事件族与被拒的导入各一例，因为有的族身后另有一道墙，掉出名单也不会被常规路径察觉）；钩子关掉后内核层仍然成立（POSIX 的 rlimit 数值与软硬同值、`NO_NEW_PRIVS`、不能起进程 / 线程；Windows 的低完整性、不能读宿主内存、Job 本身的 `KILL_ON_JOB_CLOSE` / `terminate` / 进程数）；低完整性只在系统确实接受时才记为一层；CPU、内存、墙钟；脚本空转时宿主的事件循环照常走 |
| `tests/test_sandbox_protocol.py` | 值过边界；脚本异常的消息、类型、行号；伪造 / 超长 / 嵌套过深 / 中途断掉的消息；洪泛上限与背压；取消的传播；预算视图（恰在总额处算用尽、剩余不为负）；`parallel()` 里有 thunk 起不来时不留已经起的任务；宿主被杀后不留进程；起不来的各种情形；`WorkflowRuntime` 的衔接（选项检查、cwd / 参数 / 预算送到脚本、phase 随调用且只列一次、meta） |
| `tests/test_subagent_cwd.py` | `cwd` 约束的规则表、与 journal 的兼容、执行器自检 |
| `packages/pi-agent-cli/tests/test_acp_agent.py` | 经真实 ACP agent：脚本不能把子 agent 派到工作区之外 |

先红后绿；另外对沙盒的宿主、子进程、`winjob`、运行时衔接，以及 `cwd` 约束，各做了手工变异检查，数字见审计追踪文档第六节。

---

## 11. 兼容性与迁移

| 变化 | 影响 |
|------|------|
| 值以 JSON 过边界 | `args`、`result()`、`agent()` 的返回值是 JSON 数据；对象按 `str` 转换。内置 workflow 与已有测试不受影响 |
| `budget.add` 在脚本里没有了 | 账本在宿主，脚本只能读 |
| 异常类型 | `WorkflowScriptError`（`RuntimeError` 的子类，`str()` 不变）；限制与起不来是 `SandboxError`（也是 `RuntimeError`） |
| 命名空间内容 | 不变：没有 `open`、`__import__`、`BaseException`；`print` 是 `log` |
| 起进程的代价 | 每次运行额外约 0.06–0.15 s |
| Python 版本 | ≥ 3.11（审计钩子从 3.8 起就有） |
