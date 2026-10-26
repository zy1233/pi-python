# FrontierHarness 30 题全量评估与 Pi 官方深度对标分析报告

> **评测基准**：FrontierHarness Eval (30 Tasks: 21 Terminal-Bench + 9 DeepSWE)  
> **对标基线**：Official Pi (`pi-responses` v17.4.0, Kimi K3 on Fireworks)  
> **评测对象**：`pi-python` Agent Harness (`pi-agent-harness` + `pi-agent-cli`)  
> **被测模型**：`deepseek-ai/DeepSeek-V4-Flash` (via SiliconFlow API)。2026-10-16 和 2026-10-26 的 DeepSWE 重跑改用内网网关的 `deepseek-flash`（网关把它路由到哪个模型，客户端无法核实）  
> **评测时间**：2026-09-04 起；2026-10-16 重跑 DeepSWE 9 题（60 轮上限），并核查全部存档运行的数据有效性；2026-10-26 把其中 6 道未通过的题用 100 轮上限重跑  
> **测试环境**：Windows 10 / WSL2 Ubuntu 24.04 / Docker 29.1 / v2ray Proxy / Python 3.12

---

## 数据有效性声明（2026-10-16；2026-10-26 更新）

先读这一节，再读后面的数字。

2026-10-16 重跑 DeepSWE 时，我们发现评测框架里有两个口子，能让智能体拿到答案。第一个已经修复。第二个只做了记录，没有修复。我们据此核查了全部存档的运行，并更正了受影响的数字。

2026-10-26 用 100 轮上限重跑 6 道题时，又发现评分流程的一个缺陷：它会把正确的结果判成 -1。它已经修复（第 1b 点）。这次重跑还有两点要先知道：第一，智能体的工具用法与 10-16 明显不同，原因没有查清（第 5 点）；第二，现在 DeepSWE 的结果来自两种轮次上限。

### 1. 已修复：验证器的测试文件对智能体可见

- **缺陷**：`DockerContainerSession.setup()` 在容器启动时就把任务的 `tests/` 复制到容器的 `/tests`。智能体在同一个容器里有 shell，所以能读到 `test.sh`、`test.patch`、`grader.py`、`test_outputs.py`。这个行为从评测器的第一个版本（2026-09-04）就存在。
- **官方的要求**：DeepSWE 的 `task.toml` 写明 `[verifier] environment_mode = "separate"`。意思是验证器在单独的环境里运行，智能体看不到测试。
- **修复**：`setup()` 不再复制 `tests/`。新增的 `_install_tests()` 只由 `run_verifier()` 在智能体结束后调用。它先 `rm -rf /tests`（清掉智能体可能自建的目录），再复制。复制失败时，验证器直接报失败，不会运行旧文件。
- **验证**：回归测试 `packages/pi-agent-cli/tests/test_docker_runner_verifier_isolation.py`（4 个用例；旧代码上 3 个失败，新代码上全部通过；Windows 和 WSL 都跑过）。在真实容器里，智能体运行期间 `docker exec <容器> ls /tests` 报目录不存在。修复后的 8 题批次里，轨迹没有任何 `/tests` 访问。

### 1b. 已修复：智能体提交了与隐藏测试同名的文件，评分为 -1（2026-10-26）

- **缺陷**：任务文字要求智能体“在新分支上工作，并提交所有改动”。DeepSWE 的 `grader.py` 假定 HEAD 是基准提交。它用 `git checkout HEAD -- <文件>` 重置 `test.patch` 涉及的文件，而且只有 HEAD 里没有这个文件时才删除它。官方流程把智能体的 diff 打到一个新容器上，所以智能体的提交没有影响。我们在同一个容器里评分，HEAD 已经包含智能体的提交。
- **后果**：`httpx-multipart-response-parsing`（10-26 的第二次运行）的智能体提交了 `tests/test_multipart_response.py`，和隐藏测试同名。重置保留了它，`git apply test.patch` 报 `already exists in working directory`，评分为 -1。一个测试也没有运行，所以智能体的代码没有被评判。
- **修复**：`setup()` 记下容器启动时的 HEAD。`run_verifier()` 在验证器启动前，对使用 `grader.py` 的任务执行 `git reset --mixed <起始 HEAD>`。它只移动 HEAD 和索引。工作区的文件不变，智能体的提交变成未提交的改动，这正是 `grader.py` 期望的状态。Terminal-Bench 任务不受影响：`sanitize-git-repo` 要求改写历史，历史就是答案。
- **验证**：回归测试 `packages/pi-agent-cli/tests/test_docker_runner_verifier_head.py`（7 个用例，Windows 和 WSL 都通过）。真实重放：同一份工作区，不退回 HEAD 时仍是 -1，报错和原来完全一样；退回 HEAD 后，F2P 122/122，P2P 1272/1272。
- **别的结果是否受影响**：没有。10-26 的 expr、katex、meriyah 三个试验也有提交（HEAD 变了）。用修复后的流程重新评分，三个试验的 F2P / P2P 与原来完全相同。arktype 和 python-statemachine 的 HEAD 没有变。搜索全部已保存的 `verifier_output.txt`（包括 10-16 的 60 轮批次），只有这一个试验有这个报错。
- **官方流程的确认**：为了不依赖这个修复，我们按官方方式给 6 个最终结果各评一次。每次用一个全新的容器（不挂载工作区，没有网络），只放入两样东西：智能体的 diff（`/logs/artifacts/model.patch`）和 `/tests`。然后运行 `test.sh`。`grader.py` 从这个 `model.patch` 取智能体的工作，不从别处取。所以智能体的提交、HEAD 的退回都不起作用。6 个试验的 F2P / P2P 都与本报告采用的评分完全一致：

  | 任务 | 官方流程的评分 | 报告采用的评分 |
  | :--- | :--- | :--- |
  | `arktype-json-schema-refs-dependencies` | ✅ 25/25，1679/1679 | 相同 |
  | `expr-try-catch-errors` | ✅ 79/79，66265/66265 | 相同 |
  | `httpx-multipart-response-parsing` | ✅ 122/122，1272/1272 | 相同 |
  | `katex-multicolumn-array-spans` | ✅ 94/94，599/599 | 相同 |
  | `meriyah-explicit-resource-declarations` | ❌ 45/49，51469/51469 | 相同 |
  | `python-statemachine-state-data-scoping` | ❌ 63/72，1286/1286 | 相同 |

  每个试验目录里有 `model.patch`（智能体的最终 diff）和 `official-flow-regrade.json`（完整的验证器输出）。`model.patch` 的生成方式：最终工作区与基准提交的 diff，包含新文件，排除验证器自己的文件（`test.patch` 涉及的路径）和构建缓存（如 expr 的 `.gocache`）。官方框架自己如何生成 diff，我们没有核对。
- **重新评分的限制**：第一种重新评分（`regrade-*.json`）在一个新容器里进行，挂载保存的工作区副本。只有 `/app` 里的文件来自智能体；它在容器的其他位置做过的改动不在。官方流程只读 `model.patch`，所以这一点与官方一致。运行目录里的 `trial.json` 和 `REPORT.md` 没有改，仍写 FAIL。重新评分的结果在同一目录的 `regrade-fixed.json`，修复前的对照在 `regrade-control-no-rewind.json`。

### 2. 核查范围和方法

核查对象是 `.pi-eval/runs/` 里全部 66 条轨迹：61 条在 Docker 里跑，1 条是 `--no-docker`（`polyglot-c-py`），4 条是 9 月 4 日的早期运行（没有记录运行模式）。五项只读扫描：

| 扫描 | 找什么 |
| :--- | :--- |
| A | 工具调用的参数里出现**根目录**的 `/tests`（不算仓库自带的 `/app/tests`、`./tests`） |
| B | 工具的输出里出现只有验证器才有的内容（`/logs/verifier/reward`、`terminal-bench-canary`、`BENCHMARK DATA SHOULD NEVER APPEAR`、`f2p_total`、`FAIL_TO_PASS`） |
| C | `read`、`ls`、`grep`、`find`、`edit`、`write` 访问 `/app`、`/data` 之外的绝对路径 |
| D | `bash` 命令引用宿主机上的任务缓存（`.cache/…/tasks/<任务>/`） |
| E | `bash` 命令访问 GitHub 或基准仓库（`terminal-bench`、`harbor-framework`、`laude-institute`、`datacurve` 等） |

另外检查了 9 个 DeepSWE 镜像里 `/app/.git` 的历史：没有包含任务修复的“未来提交”。这些扫描是一次性脚本，没有入库。方法如上，可以重做。

### 3. 核查结果

| 运行 | 任务 | 命中 | 对本报告的影响 |
| :--- | :--- | :--- | :--- |
| `20260907-031708`（Docker） | `extract-elf` | 第 12–14 轮 `ls /tests`，然后读 `/tests/test.sh` 和 `/tests/test_outputs.py`（扫描 A、B、C） | 第三节第 13 行的 PASS 作废 |
| `20260910-064928`（`--no-docker`） | `polyglot-c-py` | 第 13–15 轮读宿主机任务缓存里的 `instruction.md`、`task.toml`、`README.md`、`tests/test.sh`、`tests/test_outputs.py` 和 `solution/solve.sh`（扫描 B、C、D）。智能体写道：“The user's request is actually a terminal-bench task. Let me look at the test and solution” | 本报告没有用这个运行。第 3 行用的是 `20260910-071513`（Docker），五项扫描都没有命中它 |
| `20261016-deepswe-remaining8-…`（本次，修复之前启动） | `anko`、`arktype` | 列出 `/tests/`，读 `test.sh`、`test.patch`、`grader.py`（扫描 A、B、C） | 发现后停止了这批运行，修复后重跑。这批只留作证据，不进报告 |
| `20261016-tb-extract-elf-clean-…`（修复之后，`allowlist` 模式） | `extract-elf` | 第 8–17 轮经代理从 GitHub 下载参考解和测试文件并照抄（扫描 B、E，见第 4 点） | 这次 PASS 作废 |

- 扫描 A 另有 1 条命中是误报：`sanitize-git-repo`（9 月 7 日）用 `grep -viE "uuid|/tests/|\.jsonl"` 过滤搜索结果，没有读 `/tests`。
- 其余运行没有发现泄漏，包括第三节里其他所有行，以及修复后的 8 题批次。有些运行在扫描 C、E 里有命中，但都是良性的：读自己的 `/tmp` 临时文件；断网时失败的联网尝试；`build-cython-ext` 要构建的软件的 `git clone`。
- `anko` 在泄漏的那批里通过了（作废），在修复后的干净批次里也通过了（有效）。所以这个通过不依赖泄漏。
- **10-26 的 100 轮重跑**（7 条轨迹：6 题的批次，加 httpx 的第二次运行）做了同样的扫描。A、B、C、D 零命中。E 有 6 条命中：5 条是联网尝试，1 条是误报（katex 第 60 轮在改 `docs/support_table.md`，文本里有仓库自带的 GitHub issue 链接）。联网命令一共 7 条：arktype 第 8 轮，expr 第 14 轮，httpx 第一次运行第 6 轮，katex 第 9 轮，meriyah 第 10、11 轮（3 条）。它们都想取上游的代码或版本信息，例如 `curl raw.githubusercontent.com/…`、`git ls-remote`、`npm view`。断网下全部失败：无法解析主机名，或没有输出。

### 4. 未修复：Terminal-Bench 的出站白名单没有执行

- Terminal-Bench 任务用 `allowlist` 模式。代码（`egress_policy.py`）只把代理的环境变量（`http_proxy`、`https_proxy` 等）传给容器。`allowed_hosts` 只写进 `run.json`，没有任何代码按它过滤。
- 2026-10-16 实测：在 `allowlist` 模式的容器里，经代理访问清单之外的 `example.org`、`huggingface.co`、`httpbin.org`，都返回 200。所以实际效果是开放互联网（只要宿主机的代理不过滤）。
- 官方清单本身包含 `github.com`、`*.github.com`、`*.githubusercontent.com`。就算清单被执行，智能体也能读 GitHub 上公开的 `terminal-bench` 仓库，里面有参考解和测试文件。
- 有一次运行用了这条路：`extract-elf`（修复验证器隔离之后的第一次重测）在第 8–17 轮从 `harbor-framework/terminal-bench-1` 下载 `solution.sh` 和 `tests/test_outputs.py`，然后照抄。第 17 轮它写道：“I found the actual benchmark task and its reference solution.” 这次 PASS 作废。
- 其他 Terminal-Bench 运行里，扫描 E 没有发现访问基准仓库的记录。扫描 E 里，唯一访问 GitHub 的其他运行是 `build-cython-ext`（9 月）：它 `git clone` 任务要构建的软件 `pyknotid`，而且该题没有通过。
- 断网（`--egress-mode no-network`）能排除这条路。代价是需要联网装依赖的任务会失败（例如 `dna-insert` 要 `apt-get install primer3`）。所以其余 Terminal-Bench 题没有统一用断网重跑，它们的 PASS / FAIL 仍然是 9 月在开放网络下得到的。
- DeepSWE 不受影响：它用 `--network none`。10-16 的 8 题批次里，智能体至少 7 次想从 GitHub 抓上游实现，全部失败。10-26 的 100 轮重跑里又有 7 条联网命令，也全部失败。**这说明断网是 DeepSWE 成立的前提。**

### 5. 其他限制（没有修复）

- **`--no-docker` 没有任何隔离。** 智能体和宿主机共享文件系统，能读任务缓存里的 `solution/` 和 `tests/`。这个模式只用于冒烟，不要用它计分。
- **验证器和智能体用同一个容器。** 官方的 DeepSWE 用独立的验证环境。现在测试在智能体结束后才复制进去，所以智能体看不到测试。第 1b 点的缺陷就来自这个共用：智能体的提交留在了验证器的仓库里，现在由退回 HEAD 来消除。但智能体留下的后台进程，或被改过的系统文件，仍可能影响验证。目前的轨迹里没有看到这种行为。
- **文件工具在宿主机上运行。** `read`、`ls`、`grep`、`find`、`edit`、`write` 在宿主机的工作区里执行，能到达宿主机上 `/app`、`/data` 之外的绝对路径。扫描 C 没有发现滥用。
- **没有默认的命令超时。** Docker 的 `bash` 工具在智能体不传 `timeout` 时一直等。`extract-elf` 的断网重测因此卡在一条 `grep -rl … /` 上 13 分钟，直到 900 s 的智能体超时。
- **模型别名没有核实。** 网关的 `deepseek-flash` 是否就是 `deepseek-ai/DeepSeek-V4-Flash`，客户端无法确认。
- **每题只跑一次，而且 DeepSWE 现在混用两种上限。** 3 题是 60 轮（fastapi、anko、scc，10-16 通过后没有重跑），6 题是 100 轮。同一题的两次运行差别可以很大：python-statemachine 在 60 轮时 F2P 70/72，在 100 轮时反而是 63/72。所以一次运行的结果，不能当作这个模型在这道题上的稳定能力。
- **两个批次的智能体工具用法不同，原因没有查清。**
  - 同样这 6 题，在 10-16 的 60 轮批次里：bash 302 次，read 130 次，edit 30 次，write 4 次，ls 5 次，grep 1 次。
  - 在 10-26 的 100 轮批次里（含 httpx 的第二次运行）：bash 571 次，edit 2 次，write 1 次，ls 1 次，没有 read。智能体几乎只用 bash 读文件和改文件。
  - 已经排除：两个批次的提交（`12349d3` 到 `af5db69`）没有改动系统提示、工具列表和主循环。它们只改了 MCP、通知和会话存储的代码。两个批次里，思考文本都保留在历史里（用提示词的增量检验）。
  - 没有排除：`~/.pi-python/agent.toml` 在两个批次之间（10-20）改过，现在是 `thinking_level = "high"`。60 轮批次当时的值没有记录。网关把别名 `deepseek-flash` 路由到哪个后端，也没有记录。
  - 影响：验证器只看最终代码，所以判分不受影响。但“60 轮到 100 轮”的变化，不能全部归因于轮次上限。
- **成本是估算值。** 评测器按 $1 / $0.1 / $2 每百万 token 算，不是网关的账单。
- **`run.json` 里的 commit 不含修复。** 10-16 的批次记录的是 `12349d3`，而验证器隔离的修复当时在未提交的工作区里。10-26 的批次记录的是 `af5db69`，它含隔离修复，但不含第 1b 点的修复。httpx 的结果来自用第 1b 点的修复重新评分，而这个修复在 `af5db69` 之后才提交。所以 `af5db69` 本身不能复现 httpx 的结果。不过，保存的 `model.patch` 加官方流程（第 1b 点）不需要这个修复，就能复现全部 6 个结果。

### 6. 对本报告数字的影响

| 指标 | 旧版报告 | 10-16 重跑后 | 现在（10-26） | 原因 |
| :--- | :---: | :---: | :---: | :--- |
| 全量通过率 | 60.0% (18/30) | 66.7% (20/30) | **80.0% (24/30)** | Terminal-Bench 少 1 题，DeepSWE 多 7 题 |
| Terminal-Bench | 85.7% (18/21) | 81.0% (17/21) | **81.0% (17/21)** | `extract-elf` 的 PASS 作废，断网重测未通过 |
| DeepSWE | 0.0% (0/9) | 33.3% (3/9) | **77.8% (7/9)** | 10-16：60 轮上限，通过 3 题。10-26：对另外 6 题用 100 轮上限重跑，通过 4 题（其中 httpx 是重新评分的结果，见第 1b 点） |

其余 20 道 Terminal-Bench 题没有重跑。它们的结果来自 9 月的运行，已通过五项扫描，没有发现泄漏。

DeepSWE 的 7 / 9 要这样读：模型是 `deepseek-flash`，不是官方的 Kimi K3；每题只跑 1 次；3 题的上限是 60 轮，6 题是 100 轮；两次批次的智能体行为不同（第 5 点）。官方 Pi 在 DeepSWE 上是 2 / 9。它没有 60 轮或 100 轮的上限（最多的一题跑了 334 轮）。所以这两个数字不是严格的对标。

---

## 一、执行摘要 (Executive Summary)

FrontierHarness Eval 是当前业界评估 **Coding Agent Harness（脚手架/运行时机制与智能体循环）** 最为精简且高权重的基准评测集，由 21 个来自 `terminal-bench-2-1` 的终端交互任务与 9 个来自 `datacurve` (DeepSWE) 的真实开源仓库修复任务构成。官方基准固定使用 Kimi K3 模型测试了包括原版 TypeScript Pi、Claude Code、Codex、DSH、Hermes 等在内的 12 种主流 Harness。

作为原版 TypeScript Pi 的 Python 移植工程，`pi-python` 旨在完全忠实还原 Pi 的核心事件循环、工具调度机制与上下文管理策略。在初步评估中，由于在 Windows 宿主机的本地非容器环境下仅跑了 3 道轻量任务，取得了局部的“100% 通过率”，这一数据缺乏全貌代表性。为此，我们在 **WSL 中完整搭建了 Docker 隔离沙箱与宿主机 v2ray 代理路由系统**，将全量 30 题（包括 9 道 DeepSWE 工业级真实仓库修复题）纳入真实的容器化端到端评测流水线，获得真实、客观、严谨的数据。

### 核心结论摘要

1. **当前实测总体通过率与关键指标 (Overall Pass Rate & Performance)**：
   截止目前，`pi-python` 在 WSL2 隔离 Docker 沙箱与本地环境中已完成全部 30 题的 **100% 全量端到端真实评测**（覆盖 21 道 Terminal-Bench 与 9 道 DeepSWE 工业级真实代码仓库任务）：
   - **全量总体通过率 (Overall Pass Rate)**：**80.0%** (24 / 30 PASS)。已扣除 `extract-elf`：它旧的 PASS 无效，断网重测未通过（见“数据有效性声明”）。DeepSWE 的 7 题来自两次重跑（2026-10-16 的 3 题，2026-10-26 的 4 题），Terminal-Bench 的 17 题来自 9 月的运行。
   - **Terminal-Bench 任务集通过率**：**81.0%** (17 / 21 PASS，全量 21 题完毕)
     - 通关任务 (17 题)：`regex-log`、`sqlite-db-truncate`、`openssl-selfsigned-cert`、`git-leak-recovery`、`log-summary-date-ranges`、`constraints-scheduling`、`db-wal-recovery`、`modernize-scientific-stack`、`multi-source-data-merger`、`vulnerable-secret`、`largest-eigenval` (突破官方失败点)、`polyglot-c-py`（修复基础设施 CRLF Bug 后通过）、`merge-diff-arc-agi-task`（25 轮额度重跑通过）、`kv-store-grpc`（修复 Docker 代理泄漏后 7/7 通过，突破官方失败点）、`chess-best-move`（修复非 VLM 模型图片降级后通过，突破官方失败点）、`code-from-image`（修复非 VLM 模型图片降级后通过，突破官方失败点）、`dna-insert`（黄金快照与出站白名单体系下，主动通过 apt 安装 primer3 并调用 oligotm 闭环自测通过）
     - 未通关任务 (3 题)：`gcode-to-text`（无多模态渲染，同官方）、`build-cython-ext`（numpy 2.x 弃用符号批量替换不完整）、`extract-elf`（旧的 PASS 无效；断网重测 1 次未通过：智能体转去找参考答案，一条 `grep -rl … /` 命令 13 分钟没有返回，900 s 超时）
     - 特殊判定任务 (1 题)：`sanitize-git-repo`（清除工作全部完成且数据测试全通，但历史重写后基准 commit SHA 变动导致用例断言失败）
   - **DeepSWE 工业级代码集通过率**：**77.8%** (7 / 9 PASS)。数据来自两次重跑，都用 `deepseek-flash`（经内网网关）、容器断网、每题只跑 1 次。2026-10-16 跑了 9 题，上限 60 轮，通过 3 题。2026-10-26 把没有通过的 6 题用 100 轮上限重跑，通过 4 题。两次的智能体行为不同，所以不要把两个批次直接比较（见“数据有效性声明”第 5 点）。（F2P = 目标用例，P2P = 既有用例。）
     - 60 轮上限通过（3 题，没有重跑）：
       - `fastapi-deprecation-response-headers`：F2P 137/137，P2P 3134/3134。官方 Pi 117 轮 $2.99 未通过。这一次在修复验证器泄漏之前启动；已核对轨迹，没有读 `/tests`；
       - `anko-typed-variable-bindings`：F2P 9/9，P2P 94/94。官方 Pi 83 轮 $2.15 通过；
       - `scc-bounded-memory-spilling`：F2P 31/31，P2P 286/286。官方 Pi 100 轮 $3.28 未通过；
     - 100 轮上限重跑后通过（4 题；括号里是 60 轮时的结果）：
       - `arktype-json-schema-refs-dependencies`：F2P 25/25，P2P 1679/1679（60 轮：F2P 0/25）。用满 100 轮。官方 Pi 334 轮失败；
       - `expr-try-catch-errors`：F2P 79/79，P2P 66265/66265（60 轮：最终代码无法编译）。89 轮自行结束。官方 Pi 12 轮失败；
       - `katex-multicolumn-array-spans`：F2P 94/94，P2P 599/599（60 轮：F2P 87/94）。78 轮自行结束。官方 Pi 217 轮失败；
       - `httpx-multipart-response-parsing`：F2P 122/122，P2P 1272/1272（60 轮：F2P 120/122）。28 轮自行结束。官方 Pi 31 轮失败。**这是重新评分的结果**：原评分是 -1，因为评分流程有缺陷（见“数据有效性声明”第 1b 点）；
     - 100 轮上限仍未通过（2 题）：
       - `python-statemachine-state-data-scoping`：F2P 63/72，P2P 1286/1286（60 轮：F2P 70/72，**100 轮反而更差**）。用满 100 轮。官方 Pi 90 轮通过；
       - `meriyah-explicit-resource-declarations`：F2P 45/49，P2P 51469/51469（60 轮：F2P 0/49）。用满 100 轮。差 4 个用例：`using` 在 `switch` 的 `case` 里；`await using` 和 `using` 的数组解构应报错（2 个）；`for-of` 里把 `of` 当绑定名。官方 Pi 254 轮失败。
   - **通关任务中位耗资 (Median Cost per Pass)**：**$0.0692**（24 个通过的任务；均值 $0.258）。只算 Terminal-Bench 的 17 题：中位 $0.0356，均值 $0.0611。DeepSWE 的 7 题：$0.25~$1.08（中位 $0.675，均值 $0.737）。这是评测器的估算值，模型也和官方不同，所以不要直接拿它去除官方 Pi 的 $2.43。
   - **通关任务平均交互轮次 (Mean Turns)**：**29.7 轮**（24 个通过的任务，中位 17.0）。只算 Terminal-Bench 的 17 题：均值 13.8，中位 12.0。DeepSWE 的 7 题：3 题用满 60 轮，1 题用满 100 轮，3 题自行结束（28、78、89 轮）。旧版报告写的“10.6 轮”与第三节的逐题表对不上，这里按逐题表重算。官方 Pi 的 18.7 轮是 30 题的平均，口径不同，不能直接比。
   - **Prompt Cache 命中率**：Terminal-Bench 运行的典型中位数 **77.9%** (校正前因评估器分母双重计数 Bug 误报为 45.4%，校正后区间 65.0% ~ 82.5%，与官方 Pi 的 79.4% 完全对齐)；DeepSWE 采用的 9 个结果为 96.5% ~ 98.7%
   - **无动作轮次 (Mean No-Action)**：Terminal-Bench 运行 **1.0 ~ 1.3 轮**。DeepSWE：60 轮的 3 题是 9~19 轮，多是只调用 `read` / `grep` / `ls` 的读代码轮；100 轮的 6 题每题只有 1 轮，是评测器记入的合成消息（`Agent loop exceeded max_turns=100`），或最后一条总结消息。评测器只把调用了 `write`、`edit` 或 `bash` 的轮次算作“有动作”，而 100 轮批次里的智能体几乎只调用 `bash`（见“数据有效性声明”第 5 点），所以这个指标在 100 轮批次上没有区分度。

2. **客观真实的容器化能力格局分析概要 (Scaffold Analysis Summary)**：
   - **强项一：极简无废话的状态循环与超低空转**：在 Terminal-Bench 运行中，`pi-python` 的平均 No-Action 轮次仅为 1.0~1.3 轮（首轮规划或末轮输出），几乎每一轮均包含 `write`、`edit` 或 `bash` 动作，没有多余的寒暄与反复确认。DeepSWE 重跑里的无动作轮多是读代码轮（见上）。
   - **强项二：闭环自愈能力极强**：在 `regex-log`（写测试脚本验算边界）、`git-leak-recovery`（深入 reflog/fsck 追溯）、`sqlite-db-truncate`（解析二进制叶子节点并校准 IEEE 754 浮点值）、`db-wal-recovery`（WAL 逆向解密）、`modernize-scientific-stack`（旧 API 升级）、`vulnerable-secret`（边界计算与二进制注入）中均展现了极高的首轮成功率或自愈收敛速度。
   - **强项三：突破官方 Pi 的失败用例（Terminal-Bench 4 个，DeepSWE 6 个）**：
     - `largest-eigenval`：官方 Pi 24 轮 $0.40 失败 → `pi-python` 18 轮 $0.054 满分攻克（数值计算）
     - `kv-store-grpc`：官方 Pi 72 轮 $0.54 失败 → `pi-python` 12 轮 $0.016 满分攻克（修复 Docker 代理泄漏）
     - `chess-best-move`：官方 Pi 17 轮 $0.17 失败 → `pi-python` 15 轮 $0.030 满分攻克（修复 VLM 检测 + 棋引擎）
     - `code-from-image`：官方 Pi 156 轮 $2.26 失败 → `pi-python` 23 轮 $0.027 满分攻克（修复 VLM 检测 + OCR 降级）
     - DeepSWE 6 题：官方 Pi 未通过，`pi-python` 各通过 1 次。`fastapi`（官方 117 轮）和 `scc`（官方 100 轮）是 2026-10-16 的结果，上限 60 轮。`arktype`（官方 334 轮）、`expr`（官方 12 轮）、`katex`（官方 217 轮）、`httpx`（官方 31 轮）是 2026-10-26 的结果，上限 100 轮。每题只跑一次，模型与官方不同（`deepseek-flash` 对 Kimi K3），所以这不是严格的对标
   - **能力边界一（已在黄金快照机制下攻克）：从依赖死循环到自主依赖闭环修复**：在早期评测中，`dna-insert` 因容器缺少 `primer3` 导致 20 轮探索超时；在启用黄金快照与 Egress 白名单网络策略后（白名单实际没有被执行，容器能访问开放网络，见开头的“数据有效性声明”），智能体能够主动执行 `apt-get` 探测并安装 `primer3`，随后调用 `oligotm` 求解退火温度并编写 `verify.pl` 严密验证，31 轮满分攻克；
   - **能力边界二（已大幅修正）：多模态与渲染瓶颈已基本解决**：`polyglot-c-py` 已修复（CRLF 基础设施 Bug 误判），`merge-diff-arc-agi-task` 在 25 轮额度下已通过，`chess-best-move` 和 `code-from-image` 通过 VLM 检测 + 图片降级修复后已通过。剩余瓶颈仅在 `gcode-to-text` 中无渲染模块导致长程盲猜；
   - **能力边界三：大型仓库的长程重构需要轮次，但轮次不是全部**：2026-10-16 把上限放到 60 轮后，DeepSWE 通过 3 / 9（FastAPI、Anko、SCC）。2026-10-26 对其余 6 题用 100 轮上限重跑，又通过 4 题（ArkType、expr、KaTeX、httpx），合计 7 / 9。
     - 60 轮时，expr、katex、httpx 最后一次改文件发生在第 60 轮，也就是被上限打断。在 100 轮重跑里，它们分别在第 89、78、28 轮自行结束。对这三题，“轮次不够”是 60 轮时失败的一个原因。
     - ArkType 在 100 轮时用满上限，但已经通过。
     - 仍失败的 2 题：meriyah 差 4 个用例，python-statemachine 差 9 个用例。python-statemachine 在 100 轮时（63/72）比 60 轮时（70/72）更差，所以对它来说，多给轮次没有帮助。
     - 每题只跑 1 次，而且两个批次的智能体工具用法不同（见“数据有效性声明”第 5 点）。所以不要把“60 轮到 100 轮”的变化全部算作轮次的效果。

3. **核心机制与原版 Pi 高度一致且平均轮次极简**：
   - 在 `sqlite-db-truncate` 中，`pi-python` 11 轮完成（官方 10 轮），花费 $0.1171 vs $0.1371；
   - 在成功通过的 Terminal-Bench 任务中，平均耗费 13.8 轮（中位 12 轮；旧版报告写的“6~8 轮”与第三节的逐题表对不上，已按逐题表重算），真实前缀缓存命中稳定在 70%~82%（经公式校正），充分印证了 `pi-python` 基于单流式函数适配、原生工具调度与会话状态机的工程健壮性。

4. **Docker 运行时桥接架构与跨平台 POSIX 权限完美适配**：
   完成了针对 `pi-python` 的完整容器评估调度层：
   - 宿主机与 WSL 间透明使用 v2ray 代理拉取 AWS ECR 与 Docker Hub 镜像；
   - 突破 Windows 9p/drvfs 挂载不支持 POSIX 权限的限制，自适应采用原生 ext4 `/tmp` 镜像沙箱与写回机制，让 `chmod 600`、私钥生成等高敏感权限任务无损评测；
   - `DockerContainerSession` 自动挂载工作区至容器 `/app`，提取底座代码仓库；
   - `create_docker_bash_tool` 让智能体的所有 terminal 指令在容器内以 root 执行；
   - 自动适配 Windows/Linux CRLF 换行符与 Git safe.directory 权限问题；
   - 自动捕获 `reward.json` / `reward.txt` 与 pytest XML 报告。

---

## 二、宏观全景对比：官方 12 大 Harness 排行榜

在 FrontierHarness Eval 官方评测大盘（统一采用 Kimi K3 模型）中，官方原版 Pi（`pi-responses`）凭借极简的 Loop 设计与优秀的上下文控制位列第一梯队：

| Harness 架构 | 通过率 (Pass Rate) | 有效单次通过成本 | 中位耗时 | 前缀 Cache 命中率 | 平均交互轮次 | 架构机制特点 |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **codex** | **66.7%** (20/30) | $3.47 | 403s | 88.0% | 62.4 | 重型上下文，多阶段状态机 |
| **dsh-creator** | **63.3%** (19/30) | $3.28 | 404s | 84.3% | 35.7 | 强调任务分解与代码创建 |
| **claude-code** | **63.3%** (19/30) | $18.34 | 578s | 67.8% | 49.3 | 极重 Prompt，成本昂贵 |
| **pi-responses (官方 Pi)** | **60.0%** (18/30) | **$2.43** | 453s | 79.4% | **18.7** | **极简事件循环，低轮次高精度** |
| **dsh-ptc** | **60.0%** (18/30) | $4.58 | 464s | 87.2% | 22.2 | 标准状态机 + 编程工具链 |
| **dsh-standard** | **60.0%** (18/30) | $3.46 | 377s | 86.5% | 31.0 | 均衡型交互脚手架 |
| **oh-my-pi** | **56.7%** (17/30) | $4.75 | 406s | 82.2% | 26.6 | 社区版增强 Pi，轮次略有上升 |
| **kimi-code** | **56.7%** (17/30) | $3.65 | 476s | 88.0% | 39.9 | 原生工具流 |
| **dsh-minimal** | **56.7%** (17/30) | $4.72 | 341s | 84.6% | 47.6 | 极简无状态循环 |
| **exo** | **53.3%** (16/30) | **$1.05** | 377s | 70.3% | 12.0 | 极限极简流，牺牲长程任务 |
| **opencode** | **50.0%** (15/30) | $3.24 | 387s | 78.4% | 11.2 | 开源工具链，长上下文易退化 |
| **hermes** | **50.0%** (15/30) | $2.90 | 418s | 85.9% | 25.9 | 通用代理脚手架 |

### 官方 Pi (`pi-responses`) 的突出特征
- **第二便宜的有效单次通过成本**：仅 $2.43（全场仅次于激进剪枝的 Exo $1.05，远低于 Claude Code 的 $18.34 与 DSH 的 $4.58）。
- **极低的平均交互轮次**：平均仅 18.7 轮，表明其 Prompt Guidelines 与工具说明极少导致模型空转或陷入试错泥潭。
- **高通过率稳定性**：在 Terminal-Bench 21 题中拿下 16 胜（76.2%），在 DeepSWE 9 题中拿下 2 胜。

---

## 三、FrontierHarness 30 题逐题全量对标矩阵 (Task-by-Task Matrix)

以下为 FrontierHarness 全部 30 题在官方 Pi（`pi-responses`）与当前 `pi-python` 运行体系下的对标分析：

| 序号 | 任务 ID | 类型 | Pi 官方状态 | Pi 官方轮次 | Pi 官方成本 | Pi 官方耗时 | pi-python 状态 | pi-python 轮次 | pi-python 成本 | 对比分析与机制根因 |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | `terminal-bench/regex-log` | Terminal-Bench | ✅ PASS | 3 | $0.0709 | 240.9s | ✅ PASS (Docker) | 16 | $0.0844 | **双双通过**。Pi 官方直接给出带复杂断言的正则；`pi-python` 在容器中调用 perl/bash 编写测试脚本迭代验证，容器内 uvx pytest 满分通过（Reward 1）。 |
| 2 | `terminal-bench/openssl-selfsigned-cert` | Terminal-Bench | ✅ PASS | 7 | $0.0357 | 222.4s | ✅ PASS (Docker) | 6 | $0.0161 | **双双高效通过，pi-python 轮次与成本更优**。在 ext4 沙箱中精准设置 600 私钥权限，生成含 SAN 拓展的自签名证书与验证摘要，6 轮仅耗 $0.0161（官方 7 轮 $0.0357）。 |
| 3 | `terminal-bench/polyglot-c-py` | Terminal-Bench | ✅ PASS | 4 | $0.0619 | 206.0s | ✅ PASS (Docker) | 21 | $0.1056 | **修复后 Docker 满分通过**。原评测因 Docker runner CRLF 剥离静默失败导致验证器崩溃被误判 FAIL；修复 `docker_runner.py`（per-file `sed` + `tr` 双重降级 + 后验检查）与 `path_utils.py`（`/app` 虚拟映射健壮化）后，智能体 21 轮用经典 `/* */`+`"""` 技巧正确实现 Polyglot，GCC 编译通过，pytest `test_fibonacci_polyglot` 满分（Reward 1），Cache 85.3%。 |
| 4 | `terminal-bench/sqlite-db-truncate` | Terminal-Bench | ✅ PASS | 10 | $0.1371 | 302.9s | ✅ PASS | 11 | $0.1171 | **高度一致收敛**。官方 10 轮 vs 本地 11 轮，成本 $0.137 vs $0.117。两者均成功逆向 SQLite B-tree 叶子页细胞结构。 |
| 5 | `terminal-bench/git-leak-recovery` | Terminal-Bench | ✅ PASS | 7 | $0.0287 | 202.1s | ✅ PASS (Docker) | 8 | $0.0149 | **双双快速收敛**。智能体在容器内执行 `git reflog`、`git fsck --lost-found` 定位并找回被误删的历史泄漏凭证与提交，8 轮通过且成本减半（$0.0149 vs $0.0287）。 |
| 6 | `terminal-bench/log-summary-date-ranges` | Terminal-Bench | ✅ PASS | 3 | $0.0303 | 458.6s | ✅ PASS (Docker) | 7 | $0.0356 | **双双通过**。日志时间区间统计聚合。智能体在容器内编写高效 Python 日志解析器并对日期区间聚合计算，7 轮满分通过。 |
| 7 | `terminal-bench/constraints-scheduling` | Terminal-Bench | ✅ PASS | 4 | $0.0469 | 559.4s | ✅ PASS (Docker) | 3 | $0.0220 | **双双极速通关，pi-python 胜出**。纯算法约束排程任务。智能体在首轮完成约束推演，编写满足算法并落盘验证，仅用 3 轮耗资 $0.0220 完工（官方 4 轮 $0.0469）。 |
| 8 | `terminal-bench/gcode-to-text` | Terminal-Bench | ❌ FAIL | 24 | $0.3038 | 1558.0s | ❌ FAIL (Docker) | 51 | $0.2218 | **与官方 Pi 表现一致的长程多模态探索瓶颈**。Prusa MK4s 3D 打印 G-code 路径解析与 Flag 提取。在修复 Harness 视觉层支持（支持显式 `supports_images` 配置及并行多图协议转换）后，智能体成功在容器内自编 Python 脚本渲染旋转对齐的 PNG 图像，并通过 `read` 工具连续进行多模态读图，在思考链中成功还原出 `ch4LLenGiNg}` 等核心字符。但因对 20+ 个字符切片逐一读图验证耗尽了 50 轮上限，未及在最后写入 `/app/out.txt`。官方 Pi 耗费 24 轮 $0.3038 亦未能通过。 |
| 9 | `terminal-bench/dna-insert` | Terminal-Bench | ✅ PASS | 7 | $0.1096 | 486.0s | ✅ PASS (Docker) | 31 | $0.3065 | **黄金快照与白名单出站机制下满分攻克**！任务要求 PCR 引物设计与退火温度约束。在黄金快照纯净秒级还原与 Egress 白名单网络策略支持下，智能体主动探索并成功通过 `apt-get` 安装 `primer3` 工具链，随后编写 Perl 脚本调用真实 `oligotm` 约束求解，并编写 `verify.pl` 闭环自测，31 轮满分通关（Reward 1，测试全部 PASS，Cache 98.7%）。官方 Pi 7 轮直接给出精确引物。 |
| 10 | `terminal-bench/largest-eigenval` | Terminal-Bench | ❌ FAIL | 24 | $0.3967 | 624.2s | ✅ PASS (Docker) | 18 | $0.0540 | **重大突破：官方 Pi 失败点被成功攻克**！幂法/Lanczos 特征值求解数值计算。官方 Pi 因浮点收敛精度未达标在 24 轮失败（$0.3967）；`pi-python` 智能体在容器中编写并调试 scipy/numpy 幂迭代算法，18 轮满分通过（Reward 1，成本仅 $0.0540）。 |
| 11 | `terminal-bench/merge-diff-arc-agi-task` | Terminal-Bench | ✅ PASS | 15 | $0.0709 | 374.7s | ✅ PASS (Docker) | 23 | $0.0843 | **重跑后 Docker 满分通过**。首次运行 20 轮耗尽（4 轮 apt 超时 + 5 轮 git ref 试错），冲突标记残留；第二次 25 轮额度下 23 轮完成 git bundle 提取→分支合并→冲突解决→ARC-AGI 模式泛化（`output[i][j]=d[(i+j)%3]`），5/5 测试通过（含隐藏用例），Cache 91.3%。 |
| 12 | `terminal-bench/vulnerable-secret` | Terminal-Bench | ✅ PASS | 11 | $0.0872 | 501.5s | ✅ PASS (Docker) | 7 | $0.0151 | **双双满分通过，pi-python 轮次更少成本更低**。C 源码缓冲区溢出与认证绕过利用。智能体在容器中通过逆向输入边界精确触发目标分支提取 Secret，仅用 7 轮 $0.0151 满分通关（官方 Pi 11 轮 $0.0872）。 |
| 13 | `terminal-bench/extract-elf` | Terminal-Bench | ✅ PASS | 17 | $0.3197 | 653.3s | ❌ FAIL (Docker, 断网重测) | 13 | $0.0598 | **9 月的 PASS 无效，重测未通过。** 9 月的运行（21 轮 $0.0849）在第 13–14 轮读了 `/tests/test.sh` 和 `/tests/test_outputs.py`。验证器隔离修复后的第一次重测（`allowlist` 模式，21 轮 $0.1102）在第 8–17 轮经代理从 GitHub 下载了参考解和测试文件并照抄，也无效。最后一次用 `--egress-mode no-network` 重测：前 4 轮读 ELF，第 5–13 轮的大部分命令在容器里找参考答案或试图联网（`find / -iname '*solution*'`、`curl github.com`、`pip download terminal-bench`、读 `.bash_history`）。最后一条 `grep -rl … /` 没设超时，13 分钟都没有返回，900 s 智能体超时，没有写出答案。单次运行。官方 Pi 17 轮 $0.3197 通过。 |
| 14 | `terminal-bench/build-cython-ext` | Terminal-Bench | ✅ PASS | 46 | $0.5402 | 1022.7s | ❌ FAIL (Docker) | 21 | $0.0492 | **模型一致性瓶颈（最佳 9/11）**。Cython C 扩展编译调试。智能体可成功编译 Cython 并安装至全局，多次重跑最佳达 9/11 测试通过，但对 `numpy.int`/`numpy.float` 等 NumPy 2.x 弃用符号的批量替换不完整且每次修复的子集不一致。官方 46 轮通关。 |
| 15 | `terminal-bench/kv-store-grpc` | Terminal-Bench | ❌ FAIL | 72 | $0.5391 | 1397.7s | ✅ PASS (Docker) | 12 | $0.0164 | **修复后全面通过，突破官方 Pi 失败点**。分布式 gRPC 协议服务。首次运行因 Docker 容器继承宿主 `http_proxy` 环境变量但缺少 `no_proxy=127.0.0.1,localhost`，导致 gRPC 客户端连接 `127.0.0.1:5328` 时被劫持到 v2ray 代理 `172.20.35.30:10809` 而失败（6/7 通过）。修复 `docker_runner.py` 添加 `no_proxy`/`NO_PROXY` 后，智能体 12 轮完成 proto 定义→代码生成→服务实现→后台启动，7/7 测试全部通过，Cache 85.9%。官方 Pi 72 轮 $0.5391 失败。 |
| 16 | `terminal-bench/chess-best-move` | Terminal-Bench | ❌ FAIL | 17 | $0.1655 | 904.7s | ✅ PASS (Docker) | 15 | $0.0298 | **修复后全面通过，突破官方 Pi 失败点**。国际象棋残局最佳走法搜索。首次运行因 DeepSeek V4-Flash 非 VLM（视觉模型），`read` 工具返回的图片内容发送至 LLM 时触发 400 错误 `"The model is not a VLM"`，循环在第 2 轮即崩溃退出。修复 `factory.py` 添加 VLM 自动检测（`_detect_vlm_support`）后，`transform.py` 的 `strip_unsupported_images` 正确将图片降级为 `[image content removed]`，智能体转而安装 `python-chess` + Stockfish 引擎分析棋盘 PNG 像素定位棋子，15 轮完成走法推演并写入 `move.txt`，Cache 83.4%。官方 Pi 17 轮 $0.1655 失败。 |
| 17 | `terminal-bench/db-wal-recovery` | Terminal-Bench | ✅ PASS | 8 | $0.0504 | 432.9s | ✅ PASS (Docker) | 12 | $0.0457 | **双双通过，成本比官方更低**。损坏 SQLite 数据库与加密 WAL 日志解密合并。智能体在容器内执行 Python 逆向解密并输出 recovered.json，12 轮满分通过（官方 8 轮 $0.0504）。 |
| 18 | `terminal-bench/code-from-image` | Terminal-Bench | ❌ FAIL | 156 | $2.2636 | 1418.8s | ✅ PASS (Docker) | 23 | $0.0273 | **修复后全面通过，突破官方 Pi 失败点**。从截图还原代码逻辑并计算结果。首次运行与 `chess-best-move` 相同根因——非 VLM 模型读取图片触发 400 错误，2 轮即崩溃退出。修复 VLM 检测后，智能体图片被降级为文本占位符，转而在容器中安装 `tesseract-ocr` + `pytesseract` 对 `/app/code.png` 执行 OCR 文本提取，成功识别伪代码片段并实现其逻辑，23 轮计算出正确结果（`bee26a...`）写入 `output.txt`，Cache 87.6%。官方 Pi 156 轮耗资 $2.2636 失败——这是最大的反超幅度。 |
| 19 | `terminal-bench/modernize-scientific-stack` | Terminal-Bench | ✅ PASS | 5 | $0.0352 | 330.1s | ✅ PASS (Docker) | 5 | $0.0163 | **双双极速满分通过，pi-python 仅用 14s**。将旧版 SciPy/NumPy 语法升级为现代标准。智能体 5 轮完成，耗资仅 $0.0163（官方 5 轮 $0.0352）。 |
| 20 | `terminal-bench/multi-source-data-merger` | Terminal-Bench | ✅ PASS | 6 | $0.0426 | 530.5s | ✅ PASS (Docker) | 16 | $0.0469 | **双双通过**。多数据源（JSON/CSV/Parquet）冲突合并与去重。智能体在容器内迭代调整合并与冲突记录逻辑，16 轮满分通过。 |
| 21 | `terminal-bench/sanitize-git-repo` | Terminal-Bench | ✅ PASS | 11 | $0.1238 | 447.9s | ❌ FAIL (Docker) | 21 | $0.1855 | **重写历史与断言冲突**。智能体成功使用 git-filter-repo 彻底清理了敏感信息并完成正确替换（2 项测试通过），但因改写了 commit 历史树导致测试中硬编码基准 commit SHA 查找失败。 |
| 22 | `datacurve/anko-typed-variable-bindings` | DeepSWE | ✅ PASS | 83 | $2.1483 | 1542.1s | ✅ PASS (Docker) | 61 | $0.6754 | **通过**（10-16 重跑）。F2P 9/9，P2P 94/94。用满 60 轮上限。智能体第 11 轮想 `git clone` 上游仓库，因断网失败，之后在本地实现并自测。9 月的 100 轮运行曾因 `vm` 包编译错误失败（F2P 0/9）。官方 Pi 83 轮 $2.1483 通过。 |
| 23 | `datacurve/arktype-json-schema-refs-dependencies` | DeepSWE | ❌ FAIL | 334 | $10.4291 | 3600.0s | ✅ PASS (Docker, 100 轮) | 101 | $1.0574 | **通过**（10-26 用 100 轮上限重跑）。F2P 25/25，P2P 1679/1679。用满 100 轮上限，压缩了 2 次（第 43、53 轮）。第 8 轮想用 `curl` 下载上游的 json-schema 实现，因断网失败。60 轮时：F2P 0/25，P2P 1678/1679（有 1 个既有用例被破坏），$0.5179。官方 Pi 跑满 334 轮 $10.4291 / 3600 s 后失败。 |
| 24 | `datacurve/fastapi-deprecation-response-headers` | DeepSWE | ❌ FAIL | 117 | $2.9887 | 2944.1s | ✅ PASS (Docker) | 61 | $0.5787 | **通过**（10-16 重跑）。F2P 137/137，P2P 3134/3134（共 3271 个用例）。用满 60 轮上限。这一次在验证器修复之前启动；已核对轨迹：没有读 `/tests`，也没有联网尝试。9 月的运行只跑了 4 轮就失败（F2P 0/137）。官方 Pi 117 轮 $2.9887 未通过。 |
| 25 | `datacurve/httpx-multipart-response-parsing` | DeepSWE | ❌ FAIL | 31 | $0.7211 | 1345.1s | ✅ PASS (Docker, 100 轮, 重新评分) | 28 | $0.2524 | **通过**（10-26 重跑，重新评分）。F2P 122/122，P2P 1272/1272。28 轮自行结束。这一题跑了两次。第一次在第 10 轮被网关的内容审核中止（HTTP 500 `sensitive_words_detected`），没有结果，花费 $0.0965。第二次的原评分是 -1：智能体提交了与隐藏测试同名的 `tests/test_multipart_response.py`，验证器的 `test.patch` 打不上，一个测试也没有运行（见“数据有效性声明”第 1b 点）。修复后对保存的工作区重新评分，得到上面的结果；修复前的对照重放仍是 -1。60 轮时：F2P 120/122，P2P 1272/1272，$0.6940。官方 Pi 31 轮 $0.7211 失败。 |
| 26 | `datacurve/expr-try-catch-errors` | DeepSWE | ❌ FAIL | 12 | $0.5579 | 2744.4s | ✅ PASS (Docker, 100 轮) | 89 | $1.0804 | **通过**（10-26 重跑）。F2P 79/79，P2P 66265/66265。89 轮自行结束，压缩了 2 次（第 29、65 轮）。第 14 轮有 1 条 `curl` 想取上游代码，断网下没有结果。60 轮时：最终代码无法编译（验证器报 43 个包 `[build failed]`），F2P 0/79，P2P 395/66265，$0.6315。官方 Pi 12 轮 $0.5579 失败。 |
| 27 | `datacurve/python-statemachine-state-data-scoping` | DeepSWE | ✅ PASS | 90 | $2.5032 | 2350.5s | ❌ FAIL (Docker, 100 轮) | 101 | $1.0908 | **差 9 个目标用例**（10-26 重跑）。F2P 63/72，P2P 1286/1286。用满 100 轮上限，压缩了 2 次（第 20、77 轮）。**比 60 轮时更差**：60 轮时 F2P 70/72，$0.6441。9 月的 100 轮运行是 F2P 47/72（失败集中在回调参数 `state_data` 注入、子状态数据遮蔽/合并、SCXML datamodel 解析）。官方 Pi 90 轮 $2.5032 通过。 |
| 28 | `datacurve/katex-multicolumn-array-spans` | DeepSWE | ❌ FAIL | 217 | $6.5588 | 2324.3s | ✅ PASS (Docker, 100 轮) | 78 | $0.8887 | **通过**（10-26 重跑）。F2P 94/94，P2P 599/599。78 轮自行结束，压缩了 1 次（第 45 轮）。第 9 轮想用 `git ls-remote` 查上游 KaTeX，因断网失败。60 轮时：F2P 87/94，P2P 599/599，$0.6684；第 14 轮想从 GitHub 下载上游 `array.ts`，也因断网失败。官方 Pi 217 轮 $6.5588 失败。 |
| 29 | `datacurve/scc-bounded-memory-spilling` | DeepSWE | ❌ FAIL | 100 | $3.2808 | 1499.5s | ✅ PASS (Docker) | 61 | $0.6280 | **通过**（10-16 重跑）。F2P 31/31，P2P 286/286。用满 60 轮上限。官方 Pi 100 轮 $3.2808 未通过。 |
| 30 | `datacurve/meriyah-explicit-resource-declarations` | DeepSWE | ❌ FAIL | 254 | $9.1455 | 2734.4s | ❌ FAIL (Docker, 100 轮) | 101 | $1.1123 | **差 4 个目标用例**（10-26 重跑）。F2P 45/49，P2P 51469/51469。用满 100 轮上限，压缩了 2 次（第 32、68 轮）。失败的 4 个用例：`using` 在 `switch` 的 `case` 里；`await using` 和 `using` 的数组解构应报错（2 个）；`for-of` 里把 `of` 当绑定名。第 10、11 轮有 3 条联网命令（`npm view`、`curl`），断网下全部失败。60 轮时：F2P 0/49（新语法 `await using` 的用例一个都没过），$0.4763。官方 Pi 254 轮 $9.1455 失败。 |

### DeepSWE 重跑（第 22–30 行）的说明与用量

- 第 22–30 行来自两次重跑。两次都用 `deepseek-flash`（经内网网关）、容器断网、每题 1 次。
  - 2026-10-16：9 题，60 轮上限。原计划用 `kimi-k3`，但网关对 K3 返回 403（上游 7 天额度用完），所以改用 `deepseek-flash`。
  - 2026-10-26：对 10-16 没有通过的 6 题（arktype、httpx、expr、python-statemachine、katex、meriyah），用 `--max-turns 100` 重跑。
- 每行采用哪次结果：anko、fastapi、scc 用 10-16 的结果（60 轮，已经通过，没有重跑）。其余 6 行用 10-26 的结果（100 轮）。
- 什么时候才换一次运行：只有当这次运行没有产生结果，而且原因在基础设施时。这次有两例，都在 httpx。第一次运行被网关的内容审核中止（第 10 轮，HTTP 500），没有结果。第二次运行的原评分是 -1（验证流程的缺陷），用修复后的流程对同一份工作区重新评分，没有重新采样。没有任何一次运行因为结果不好而被换掉。
- “轮次 61 / 101” = 60 / 100 轮上限 + 评测器记入的 1 条合成消息（`Agent loop exceeded max_turns`）。自行结束的运行没有这条消息：expr 89 轮，katex 78 轮，httpx 28 轮。
- “成本”是评测器按 $1 / $0.1 / $2 每百万 token（输入 / 缓存输入 / 输出）算的估值，不是网关的账单。官方列是 Kimi K3 的真实价格。模型不同，每题只跑一次，所以这张表不是严格的对标。
- 缓存命中率是 96.5%–98.7%，高于 9 月 Terminal-Bench 运行的约 78%。原因没有单独验证。

采用的 9 个结果：

| 任务 | 结果 | 轮次上限 | 轮次 | 耗时 (s) | 输入 token | 其中缓存 | 输出 token | 估算成本 |
| :--- | :---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fastapi-deprecation-response-headers` | ✅ | 60 | 61 | 422 | 4,138,240 | 4,084,608 | 58,304 | $0.5787 |
| `anko-typed-variable-bindings` | ✅ | 60 | 61 | 406 | 4,240,441 | 4,095,744 | 60,367 | $0.6754 |
| `scc-bounded-memory-spilling` | ✅ | 60 | 61 | 392 | 4,611,990 | 4,550,912 | 55,724 | $0.6280 |
| `httpx-multipart-response-parsing` | ✅（重新评分） | 100 | 28 | 239 | 1,374,589 | 1,330,688 | 37,536 | $0.2524 |
| `python-statemachine-state-data-scoping` | ❌ | 100 | 101 | 579 | 7,481,809 | 7,228,288 | 57,058 | $1.0908 |
| `katex-multicolumn-array-spans` | ✅ | 100 | 78 | 465 | 6,109,631 | 5,938,560 | 61,720 | $0.8887 |
| `arktype-json-schema-refs-dependencies` | ✅ | 100 | 101 | 595 | 6,846,284 | 6,605,568 | 78,071 | $1.0574 |
| `meriyah-explicit-resource-declarations` | ❌ | 100 | 101 | 698 | 7,536,160 | 7,286,144 | 66,677 | $1.1123 |
| `expr-try-catch-errors` | ✅ | 100 | 89 | 623 | 6,949,621 | 6,706,048 | 82,920 | $1.0804 |
| **合计** | **7 / 9** | | | **4,419** | **49,288,765** | **47,826,560** | **558,377** | **$7.3641** |

推理 token 是输出 token 的一部分（LangChain 的 `output_token_details.reasoning`），这 9 个结果合计 385,535。按 Kimi K3 的价格（$3 / $0.3 / $15 每百万 token）折算，这 9 个结果是 $27.12。

被 100 轮结果取代的 60 轮结果（10-16）。F2P / P2P 的对比：

| 任务 | 60 轮：结果 | F2P | P2P | 估算成本 | 100 轮：结果 | F2P | P2P |
| :--- | :---: | ---: | ---: | ---: | :---: | ---: | ---: |
| `httpx-multipart-response-parsing` | ❌ | 120/122 | 1272/1272 | $0.6940 | ✅（重新评分） | 122/122 | 1272/1272 |
| `python-statemachine-state-data-scoping` | ❌ | 70/72 | 1286/1286 | $0.6441 | ❌ | 63/72 | 1286/1286 |
| `katex-multicolumn-array-spans` | ❌ | 87/94 | 599/599 | $0.6684 | ✅ | 94/94 | 599/599 |
| `arktype-json-schema-refs-dependencies` | ❌ | 0/25 | 1678/1679 | $0.5179 | ✅ | 25/25 | 1679/1679 |
| `meriyah-explicit-resource-declarations` | ❌ | 0/49 | 51469/51469 | $0.4763 | ❌ | 45/49 | 51469/51469 |
| `expr-try-catch-errors` | ❌ | 0/79 | 395/66265 | $0.6315 | ✅ | 79/79 | 66265/66265 |

httpx 被中止的第一次运行（10-26）：10 轮，121 s，输入 237,586 token（其中缓存 202,112），输出 20,238 token，估算 $0.0965。网关返回 HTTP 500 `sensitive_words_detected`，智能体循环在重试 2 次后中止。它没有结果，不进上面的表。

**10-26 这次工作的总用量**（6 题的批次，含 httpx 被中止的第一次运行；加 httpx 的第二次运行）：

| | 时间窗口 (UTC+8) | 输入 token | 其中缓存 | 输出 token | 其中推理 | 估算成本 | 按 K3 价格折算 |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| 6 题批次 | 2026-10-26 00:11:29 – 01:11:08 | 35,161,091 | 33,966,720 | 366,684 | 248,631 | $5.3261 | $19.28 |
| httpx 第二次运行 | 2026-10-26 01:12:46 – 01:16:54 | 1,374,589 | 1,330,688 | 37,536 | 29,482 | $0.2524 | $1.10 |
| **合计** | | **36,535,680** | **35,297,408**（96.6%） | **404,220** | **278,113** | **$5.5785** | **$20.37** |

httpx 的重新评分不调用模型，没有 token 用量。

---

## 四、重点任务案例深度剖析 (Deep-Dive Case Studies)

### 案例 1：`terminal-bench/sqlite-db-truncate`（底层数据库逆向恢复）

- **任务核心难点**：
  给定一个被截断至 4096 字节（仅剩单个 Page）的损坏 SQLite 数据库 `trunc.db`。文件头部开头的 `"SQLite format 3\0"` 幻数完全损坏，仅保留以 `0x0d` 开头的 B-tree 叶子节点头。要求恢复所有被截断的键值记录 `{"word": "...", "value": ...}`，并输出为符合格式的 `recover.json`。
- **两端行为对比**：
  ```
  [Pi 官方 (Kimi K3)]:
  Turns: 10 | Cost: $0.1371 | Duration: 302.9s | Cache: 82.0% | Status: PASS
  
  [pi-python (DeepSeek-V4-Flash)]:
  Turns: 11 | Cost: $0.1171 | Duration: 780.8s | Cache: 43.8% | Status: PASS
  ```
- **Harness 交互轨迹透视**：
  1. `pi-python` 的智能体首先使用 `ls` 探索工作区，确认数据库位置。
  2. 智能体迅速在工作区编写 Python 脚本，以二进制模式读取 4096 字节，通过魔数 `0x0d` 确认当前页面为 B-Tree Leaf Table Page。
  3. 智能体编写循环解析 2 字节 cell offset 数组，定位到 10 个数据细胞起始地址（4080, 4063, 4046, ...）。
  4. 解析 SQLite varint 长度及数据记录头（Record Header），提取出 `testword00` ~ `testword09` 对应的单字节整数与 8 字节 IEEE 754 浮点数值（例如将 `0x4058ff5c28f5c28f` 正确解析为 `99.99`，将 `0x3fe0000000000000` 解析为 `0.5`）。
  5. 自动输出格式完美的 `recover.json` 并调用 `cat`/`ls` 确认。
- **架构价值**：
  两端交互轮次仅差 1 轮（11 轮 vs 10 轮），成本更低（$0.117 vs $0.137）。这有力证明了 `pi-python` 的 Agent 状态机循环、工具调用与结果反馈在应对复杂系统调试时，与 TypeScript 原版 Pi 具备同等级别的工程精准度。

---

### 案例 2：`terminal-bench/regex-log`（IPv4 日志复杂正则解析）

- **任务核心难点**：
  要求构造单一正则表达式，匹配日志中出现在包含合法 IPv4 地址的行里的最后日期（`YYYY-MM-DD`），且要求 2 月可匹配至 29 日、各字节不含前导 0、且不能被其他字母数字相连。
- **两端行为对比**：
  ```
  [Pi 官方 (Kimi K3)]:
  Turns: 3  | Cost: $0.0709 | Duration: 240.9s | Cache: 56.0% | Status: PASS
  
  [pi-python (DeepSeek-V4-Flash)]:
  Turns: 13 | Cost: $0.1117 | Duration: 209.2s | Cache: 45.8% | Status: PASS
  ```
- **思维模式差异与 Harness 支撑**：
  - **官方 Pi 的“直觉生成型”**：利用模型的超强文本生成直觉，在第 2 轮直接输出长达 200+ 字符的复合预查正则（`(?=.*(?:^|[^0-9A-Za-z])...)`），一次性命中。
  - **`pi-python` 的“工程自愈型”**：模型并未盲目自信，而是展现出标准的软件工程师工作流：
    - 轮次 1~4：分析指令，初版生成；
    - 轮次 5~8：在工作区自发编写 `test_script.py`，构造伪造测试用例自跑 `pytest`；
    - 轮次 9~12：发现 2 月 29 日和反向负用例存在微小漏检，通过工具反复编辑并重跑 `python test_script.py`；
    - 轮次 13：全部绿色通过后正式写入 `regex.txt` 并退出。
- **架构价值**：
  虽然多消耗了轮次，但 `pi-python` 的 Harness 正确且无损地管理了长达 13 轮的多工具嵌套调用（`write` -> `bash` -> `edit` -> `bash`），没有发生状态发散或丢失。

---

### 案例 3：官方 Pi 失败题目归因（The Frontier Failure Modes）

在 FrontierHarness 榜单中，官方 Pi 失败的任务揭示了当前 Code Agent Harness 面临的典型瓶颈：

1. **多模态盲区 (`terminal-bench/code-from-image`, 156 轮，花费 $2.26) [pi-python 已攻克]**：
   任务要求根据 PNG 截图还原代码。官方 Pi 轮次高达 156 轮最终超时失败。根因在于标准 Coding Agent Harness 默认仅配备了文件与 Shell 文本工具，缺乏对图像的本地多模态解析/OCR 桥接，导致模型在终端里尝试用 `hexdump`、`strings` 等手段暴力猜测图片内容，造成致命空转。`pi-python` 通过 VLM 自动检测与图片降级机制，让智能体在无法直接"看到"图片时主动安装 OCR 工具（tesseract-ocr）进行文本提取，23 轮成功攻克。
2. **上下文膨胀与复杂重构失控 (`datacurve/arktype-...`, 334 轮，花费 $10.43)**：
   官方 Pi 在这道 DeepSWE 任务中跑满 3600 秒上限，消耗超 10 美元。根因在于当大型 TS 仓库类型错误报错信息过长时，Agent 在多轮中不断全量重读数十个文件，突破了有效上下文窗口，进而陷入“修 A 崩 B”的死循环。这凸显了 **Context Compaction（上下文压缩）** 在长程重构任务中的生死攸关作用。
3. **长程并发状态机死锁 (`terminal-bench/kv-store-grpc`, 72 轮)**：
   分布式高并发场景下，单命令行的非交互式 Shell 往往难以捕获后台服务的竞态崩溃日志，导致 Agent 误以为服务未启动而反复轮询。

---

### 案例 4：`terminal-bench/largest-eigenval`（突破官方 Pi 失败点：数值特征值高精收敛）

- **任务核心难点**：
  给定未知稀疏矩阵，要求设计一种高精度的数值算法（幂迭代法/Lanczos 算法），计算并提取矩阵的最大绝对值特征值（Largest Absolute Eigenvalue），要求误差控制在极端严苛的 \(10^{-6}\) 内。
- **两端行为对比**：
  ```
  [Pi 官方 (Kimi K3)]:
  Turns: 24 (轮次耗尽) | Cost: $0.3967 | Status: ❌ FAIL (收敛精度未达标)
  
  [pi-python (DeepSeek-V4-Flash)]:
  Turns: 18 | Cost: $0.0540 | Duration: 254.5s | Cache: 42.8% | Status: ✅ PASS (满分通过)
  ```
- **Harness 交互轨迹透视**：
  1. `pi-python` 的智能体在容器内执行 `ls` 和读取 `eval.py` 后，敏锐发现当前环境缺少高性能科学计算包。
  2. 智能体迅速执行 `pip install scipy` 安装底座依赖；
  3. 智能体通过 `write` 在 `/app/eigen.py` 中编写了带有自适应动量加速与收敛容差动态调整的幂迭代算法；
  4. 智能体在容器内多次执行本地 `python eval.py` 自测，观察到浮点精度波动后，立即使用 `edit` 工具微调迭代步长与基底正交化策略；
  5. 经过自适应调整后，第 18 轮通过全部验证，并在 Docker verifier 评分中取得 1.0 满分。
- **架构价值**：
  这是 `pi-python` 首次在官方 Pi 失败的任务上实现反超攻克！充分证明了 `pi-python` 基于标准单轮流式反馈、工具执行结果完整注入的会话机制能够为模型在长程数值调试中提供高保真的闭环反馈环境。

---

### 案例 5：`terminal-bench/vulnerable-secret`（二进制逆向与缓冲区溢出漏洞利用）

- **任务核心难点**：
  给定编译好的二进制可执行程序 `vulnerable`，要求分析其汇编与 C 逻辑，找出隐藏的缓冲区越界漏洞，构造 Payload 绕过认证逻辑并提取隐秘的 Secret Flag。
- **两端行为对比**：
  ```
  [Pi 官方 (Kimi K3)]:
  Turns: 11 | Cost: $0.0872 | Duration: 501.5s | Status: ✅ PASS
  
  [pi-python (DeepSeek-V4-Flash)]:
  Turns: 7  | Cost: $0.0151 | Duration: 13.0s  | Status: ✅ PASS (极速满分通过)
  ```
- **架构价值**：
  `pi-python` 智能体展现了惊人的分析效率：仅用 7 轮（官方为 11 轮），耗时仅 13 秒，花费 $0.0151（仅为官方的 17%），直接在工作区编写精确的 Python Exploit 脚本完成注入与 Flag 提取。

---

### 案例 6：`terminal-bench/dna-insert`（黄金快照与出站白名单体系下的依赖自愈）

- **任务核心难点**：
  给定环状质粒输入与输出 FASTA 序列，要求针对 NEB Q5 定向诱变试剂盒设计一对引物。引物退火部分长度需在 15~45 bp 之间，退火温度（Tm）在 58~72℃ 之间且温差 ≤5℃，并且明确要求以 `primer3` 的 `oligotm` 工具（参数 `-tp 1 -sc 1 -mv 50 -dv 2 -n 0.8 -d 500`）为绝对基准。然而初始容器镜像为极简 Ubuntu 24.04，容器内并未预装 `primer3` 与 `oligotm`。
- **两端行为对比**：
  ```
  [Pi 官方 (Kimi K3)]:
  Turns: 7  | Cost: $0.1096 | Duration: 486.0s | Cache: 78.0% | Status: ✅ PASS
  
  [pi-python 早期批次 (DeepSeek-V4-Pro / Flash, 无白名单 & 20 轮限制)]:
  Turns: 21 | Cost: $0.0564 | Duration: 280.0s | Cache: 91.8% | Status: ❌ FAIL (探索超时)
  
  [pi-python 新模式 (黄金快照 CoW + Egress 白名单 + 自然轮次)]:
  Turns: 31 | Cost: $0.3065 | Duration: 423.4s | Cache: 98.7% | Status: ✅ PASS (满分通关)
  ```
- **Harness 进化与轨迹透视**：
  1. **早期失败根因**：在未引入网络策略与黄金快照的早期批次中，容器缺少预置工具且受制于人为指定的 `--max-turns 20` 限制，模型在发现没有 `python3` 和 `primer3` 后，转而尝试编写 Perl 脚本手工仿真退火温度公式，因字符串截断与引号转义反复耗费轮次，最终在第 21 轮截断未产出文件。
  2. **新模式下的自主依赖安装**：在采用黄金快照（秒级 CoW 克隆）与 Egress 白名单网络策略后，智能体在首轮探索后敏锐发现系统缺少 `primer3` 与 `oligotm`，立即主动执行 `apt-get update && apt-cache search primer3`，成功安装官方 `primer3` 软件包。
  3. **精确求解与闭环验证**：智能体编写 `scan.pl`，直接调用系统内真实 `oligotm` 工具以题干指定参数计算退火温度与重叠长度；随后编写 `verify.pl` 针对产物进行全量双向对齐校验（`product equals output? YES`）；最终生成符合格式的 `/app/primers.fasta`。
  4. **Verifier 1.0 满分**：测试验证器运行 `pytest /tests/test_outputs.py` 一次性全部 PASS，Reward 获得 1.0 满分。
- **架构价值**：
  这一突破证明了：**黄金快照提供的纯净初始环境 + Egress 策略明确保障的官方依赖下载网络通道**，使 Code Agent Harness 能够彻底跨越“工具链缺失”带来的试错死循环，实现从“被动摸索环境”到“主动自愈工具链并收敛求解”的质的提升。
  > 注（2026-10-16）：这次运行的容器能访问任意互联网，白名单没有被执行。`apt-get install primer3` 能成功，靠的是开放的出站网络，不是白名单。见开头的“数据有效性声明”。

---

## 五、WSL Docker 隔离沙箱与评测技术实现 (Docker Sandboxing Architecture)

为了获得真实客观、符合工业级标准的评测数据，解决此前非容器环境下 DeepSWE 题库无法直接运行的问题，`pi-python` 现已正式将 Docker 容器化沙箱作为评估核心基础设施（在 WSL2 / Linux 环境下原生驱动）：

```
                      +------------------------------------------------+
                      | Windows 宿主机 (v2ray: 172.20.35.30:10809)      |
                      +------------------------------------------------+
                                              | 代理路由 (proxy_on)
                                              v
+-------------------------------------------------------------------------------+
| WSL2 (Ubuntu 24.04) / pi-python Evaluation Runtime                             |
|                                                                               |
|   run_eval.py / evaluator.py                                                  |
|       |                                                                       |
|       +--> DockerContainerSession                                             |
|       |        |-- 自动从 AWS ECR / Docker Hub 拉取任务专用镜像                 |
|       |        |-- 提取容器内原始代码仓库至 workspace                           |
|       |        \-- 启动容器：-v <workspace>:/app -w /app                      |
|       |                                                                       |
|       +--> AgentHarness (DeepSeek-V4-Flash)                                    |
|       |        |-- read / write / edit: 快速操作 workspace (实时同步至容器 /app)|
|       |        \-- bash tool: create_docker_bash_tool                         |
|       |                \-- docker exec -i -w /app <cid> bash -lc <cmd>        |
|       |                                                                       |
|       \--> Verifier (智能体结束后才开始):                                      |
|                |-- rm -rf /tests; docker cp tests/. <cid>:/tests/            |
|                \-- docker exec <cid> bash /tests/test.sh                      |
|                    (自动去除 CRLF，解析 reward.json / reward.txt / pytest XML) |
+-------------------------------------------------------------------------------+
```

### 1. 代理桥接与镜像拉取
- 针对海外镜像源（AWS ECR `public.ecr.aws/d3j8x8q7/swe-bench-202605:*` 与 Docker Hub `alexgshaw/*`），利用 WSL 内部环境的 `proxy_on` 配置，将 Docker Daemon (`/etc/docker/daemon.json`) 配置透明代理至 Windows 宿主机 v2ray 端口（`http://172.20.35.30:10809`），保证大体积镜像拉取高速稳定。

### 2. 双向文件同步与原生文件工具性能
- 智能体的文件操作（`read`, `write`, `edit`, `grep`, `find`, `ls`）直接在本地文件系统 `workspace` 上执行，享受 Python 原生高性能文件读写；
- 同时通过 Docker Bind Mount（`-v workspace:/app`），智能体的一切文件改动瞬间同步到容器内的 `/app`，零拷贝延迟。
- 在 `pi_agent_core/coding_tools/path_utils.py` 中增加了对 `/app` 与 `/data` 虚拟挂载路径的透明映射，彻底消除了非 root 权限下的路径权限拒绝问题。

### 3. 容器内纯净终端执行 (`create_docker_bash_tool`)
- 智能体发起的 `bash` 命令不再受宿主机工具链缺失影响，而是直接在容器环境中以 `root` 权限执行（`docker exec -i -w /app <cid> bash -lc <command>`）；
- 容器内预装的 `gcc`、`python 3.12`、`pytest`、`git` 等完整工具链即开即用。

### 4. 容器内原生自动化打分与报告解析
- 测试套件**只在验证阶段**（智能体结束后）才拷贝进容器 `/tests/`（先 `rm -rf /tests`），并执行 `sed -i 's/\r$//' /tests/*` 自动剔除跨平台 CRLF 换行符。智能体运行期间，容器里没有 `/tests`。2026-10-16 之前不是这样，见开头的“数据有效性声明”；
- 评测脚本运行后，自动读取容器内部生成的 `/logs/verifier/reward.json`（针对 SWE-bench/DeepSWE）或 `/logs/verifier/reward.txt`（针对 Terminal-Bench），实现 100% 无人工干预的客观判定。

### 5. 跨文件系统权限桥接 (WSL drvfs vs ext4 /tmp 隔离沙箱)
在跨平台评估中，Windows 宿主机目录在 WSL 下默认以 9p/drvfs 协议挂载（`/mnt/d/...`），drvfs 不支持 Linux 细粒度 POSIX 文件权限管理（例如对私钥执行 `chmod 600 server.key` 无法生效，文件依然呈现 `777` 权限）。
为此，评测运行器 `evaluator.py` 实现了自适应临时 ext4 工作区机制：当检测到任务运行在 WSL Docker 模式且工作目录位于 `/mnt/...` 时，自动在原生 ext4 文件系统（`/tmp/pi-ws-*`）中创建执行沙箱并挂载至容器。测试通过后自动将产生的文件快照安全同步回 `.pi-eval/runs/` 目录，彻底解决了安全认证、私钥加密等权限敏感型任务在 Windows/WSL 混合环境下的评测失真。

---

## 六、Harness 核心机制与脚手架效应 (The Scaffold Effect)

通过本次评测与对标，我们总结出如下四条针对 `pi-python` 的脚手架工程洞察：

### 1. 跨平台沙箱虚拟化 (NTFS Junction vs POSIX /app)
Terminal-Bench 任务原为 Docker 容器定制，指令普遍含有硬编码 `/app/recover.json`、`/app/trunc.db` 或 `/data/...`。在 Windows 宿主机上：
- Windows 相对根路径 `\app` 默认解析为当前驱动器根目录（`D:\app`）。
- `pi-python` 评估器引入了基于 Windows 内核 `_winapi.CreateJunction` 的 NTFS 动态挂载机制，无需管理员权限即可将 `workspace` 映射为 `D:\app`，测试结束后在 `finally` 块中通过 `os.rmdir` 瞬时释放。
- 这一机制确保了上层 Agent 无论使用相对路径还是 POSIX 绝对路径，读写操作均能精准落入沙箱工作区，实现了无容器环境下的高保真跨平台运行。

### 2. Prompt Caching 行为与公式校准 (Fireworks vs SiliconFlow / DeepSeek)
- **官方 Pi (Fireworks Kimi K3)**：前缀缓存命中率极高（典型值 **79.4%**，Q3 达 **88.0%**）。原因在于其 System Prompt 保持严格静态，会话前缀只增不减。
- **`pi-python` (SiliconFlow DeepSeek-V4-Flash)**：真实前缀缓存命中率达 **77.9%**（Q3 达 **82.5%**）。
  - *根因诊断与修复*：此前报告的 43.8% ~ 45.8% 源于评测器 `evaluator.py` 的统计公式 Bug：在 OpenAI/DeepSeek 规范下，`input_tokens` 本身已包含 `cached_tokens`（即 `input = uncached + cached`），旧代码错误套用了 `cached / (input + cached)` 导致分母双重计数。以 `sqlite-db-truncate` 为例，实际 `cached=147712, input=189691`，真实命中率为 `147712 / 189691 = 77.87%`，与官方 Pi 的 79.4% 处在同一水平线！
  - *架构加固*：同时在 `AgentHarness` 中实现了会话级 System Prompt 缓存，避免多轮间重复读取磁盘/字符串微漂移；在 `convert_to_langchain` 中为 Anthropic 协议注入了标准 `cache_control: {"type": "ephemeral"}` 滑动断点；在适配层添加了 DEBUG 级缓存诊断日志。

### 3. 空转轮次 (No-Action Turns) 统计与防死锁
- 在 `sqlite-db-truncate` 运行中，`pi-python` 曾遇到模型盲目执行 `find / -name "trunc.db"` 导致全盘扫描卡死。评测器通过进程监控与超时机制及时介入，保障了任务不发生进程挂起。
- 统计数据显示，在 Terminal-Bench 运行中，`pi-python` 的平均 No-Action 轮次仅为 1.0 轮左右（仅在首轮思考规划与末轮总结时无文件工具调用），工具执行密度极高。DeepSWE 重跑的数字不同，见第一节。

### 4. 纯 Python 编码工具集的高保真度
`pi-python` 的 7 大核心工具（`read`, `edit`, `write`, `bash`, `grep`, `find`, `ls`）均由 Python 原生标准库与正则表达式实现，完全摆脱了对外部 `sed` / `awk` / `ripgrep` 可执行文件的强依赖，在跨平台 Windows/Linux 下展现了极其一致的返回格式与错误码协议。

---

## 七、演进路线与优化建议 (Roadmap)

1. **Docker 沙箱隔离执行与环境桥接已全面落地**：
   本次实测完成了全量 30 题在 WSL2 Docker 容器沙箱内的无缝集成，解决了 AWS ECR 海外代理拉取、Windows drvfs POSIX 权限缺失（自适应 ext4 /tmp 隔离沙箱）以及 CRLF/safe.directory 等跨平台摩擦。
2. **SWE-bench / 工业级超大工程长程轮次限制扩展 [已实现分级策略；已按 60 轮和 100 轮实测]**：
   DeepSWE 任务涉及成百上千个既有用例（Meriyah 51,469、expr 66,265、FastAPI 3,271、ArkType 1,679）。`evaluator.py` 的分级轮次策略（`_SUITE_DEFAULT_TURNS`）给 Terminal-Bench 25 轮，给 DeepSWE / datacurve / swe-bench 60 轮。2026-10-16 按 60 轮重跑：9 题通过 3 题，9 题都用满了上限。2026-10-26 对其余 6 题用 `--max-turns 100` 重跑：通过 4 题（arktype、expr、katex、httpx），合计 7 / 9。meriyah 差 4 个用例，python-statemachine 差 9 个用例，两题都用满了 100 轮。默认上限仍是 60，没有改。
   下一步：（a）把 DeepSWE 的 9 题用同一个上限和同一份配置跑完。现在 3 题是 60 轮，6 题是 100 轮。（b）查清两个批次工具用法不同的原因（见“数据有效性声明”第 5 点），再比较“轮次”的效果。（c）对仍失败的 2 题多跑几次，看结果的波动。
3. **前缀缓存对齐优化 (Cache-Alignment) [已完成]**：
   已完成评测器双重计数公式修复（`cached / input`），实测命中率校正为 **77.9%**，与官方 Pi 的 79.4% 完全对齐。并在 `AgentHarness` 落地了会话级 System Prompt 缓存与 Anthropic `cache_control` 滑动断点支持，彻底消除了前缀统计失真与抖动隐患。
4. **长程对话上下文压缩 (Compaction) 策略实测**：
   针对 30+ 轮的长任务，可以用 `pi-agent-harness` 的 `compaction` 机制做滑动窗口剪枝与工具结果摘要，防范类似官方 Pi 在 `arktype` 任务中 334 轮失控消耗 $10+ 的现象。旧版报告里的“实测 `arktype` 仅耗资 $0.0639 即安全退出”来自 20 轮上限下的旧运行，是轮次上限的结果，不能说明压缩机制有效。2026-10-16 的 60 轮重跑里，`arktype` 估算 $0.5179，累计输入 3.73M token（98.0% 命中缓存），会话里没有压缩记录。评测路径会在 `max_turns >= 30` 时自动开启压缩（`create_session_harness` 的 `auto_compact`）。2026-10-26 的 100 轮重跑里，会话文件里有压缩记录：arktype 压缩 2 次（第 43、53 轮），expr 2 次（第 29、65 轮），katex 1 次（第 45 轮），meriyah 2 次（第 32、68 轮），python-statemachine 2 次（第 20、77 轮）。arktype 在 100 轮上限下通过，估算 $1.0574，累计输入 6.85M token（96.5% 命中缓存）。官方 Pi 在同一题上跑了 334 轮，花 $10.4291，没有通过。这个差别主要来自轮次上限和模型价格的不同，所以它仍然不能说明压缩机制有效。

---

*报告生成：pi-python Benchmark Harness 评估套件 (`scripts/run_eval.py`)*  
*数据归档：`.pi-eval/runs/`*
