# Phase 4 Coding Agent CLI — 设计方案

> Scope: Fork [xai-org/grok-build](https://github.com/xai-org/grok-build) 的 Rust TUI，经 **标准 ACP** 驱动本仓库 `AgentHarness`。
> Python 不实现任何 `x.ai/*` 扩展；不自研 Textual/Rich TUI。
> 状态：P0 Spike 构建门禁已过；`tui/` 已迁入。P1 `pi-agent-cli` 已落地。P2 已接线：spawn Python、丢弃 `x.ai/*`、产品二进制 `pi`。Cargo crate 已从 `xai-*` 改名为 `pi-*`。P3：`/new`→`session/new`，`/resume`→`session/list`+`session/load`；`@` 本地列目录；`pi -p` 纯 Python headless。P4：`config.toml`、coding system prompt、skills 注入、`[agent].command` spawn、Windows 说明（`docs/WINDOWS.md`）。详见 `docs/AUDIT/SPIKE-P0-GROK-TUI.md`。

---

## 1. 目标与非目标

### 1.1 目标

将已完成的引擎层（Phase 1–3 + P6）组装为可日常使用的 Coding Agent CLI：

- 全屏 TUI：复用 grok-build 的 `pi-pager`（scrollback、Markdown/diff、权限 modal、主题）。
- 引擎：`AgentHarness` + `coding_tools` + `langchain_stream`；循环语义仍在 `agent_loop.py`。
- 边界：TUI 是 ACP **Client**；Python 是 ACP **Agent**。线上协议只走 [Agent Client Protocol](https://agentclientprotocol.com) 标准方法。

### 1.2 非目标

- 不在 Python 里写 TUI。
- **不实现 `x.ai/*` 扩展**，不仿 grok `_meta`（如 `yoloMode`）。
- 不把 grok crate 打进 Python wheel。
- 不把官方 `grok` 二进制当产品入口（命令名用 `pi`）。
- 本期不做 grok marketplace / plugins / worktree / subagent / share URL。
- 无标准 ACP 对照的斜杠命令（`/compact`、`/model`、`/rewind`、`/effort`、`/context`、`/fork`）从 TUI 菜单拿掉；auto-compact 可在 harness 内静默执行。

### 1.3 已确认决策

1. **Fork TUI，不 embed。** grok TUI 是 Rust/ratatui ACP 客户端，与 `xai-grok-shell` 链在同一 composition root；官方无「指向第三方 agent」开关。
2. **整树迁入 `tui/`，不抽纯 pager crate。** pager 依赖 shell/tools/agent/workspace/acp-lib 等；根 `Cargo.toml` 约 80 个 member。
3. **先仓外 clone，再进 monorepo。** Spike 通过后再复制工作区。
4. **双许可证分区。** 根与 Python 包 MIT；`tui/` Apache-2.0（保留 LICENSE + NOTICE）。

---

## 2. 架构

```
User ──► pi (forked pi-pager) ──标准 ACP stdio──► python -m pi_agent_cli
Zed/Neovim ──────────────────────────────────────────► python -m pi_agent_cli
                                                              │
                                                              ▼
                                                       AgentHarness
                                                              │
                                    ┌─────────────────────────┼─────────────────────────┐
                                    ▼                         ▼                         ▼
                              agent_loop                 coding_tools              JSONL v3
                                    ▼
                            langchain_stream
```

### 2.1 入口

| 入口 | 实现 | 说明 |
|---|---|---|
| `pi` | Rust TUI | spawn `python -m pi_agent_cli`，只发标准 ACP |
| `pi acp` / Zed `agent_servers` | 同一 Python agent | 编辑器直接 stdio |
| `pi -p "..."` | 纯 Python headless | 不经过 TUI，不复用 grok headless |

会话真源是 **AgentHarness JSONL v3**。TUI 本地 `updates.jsonl` 只是 client 缓存。`session/list` / `session/load` 映射到 `JsonlSessionRepo`。

### 2.2 事件投影（不改变 core 不变量）

| AgentEvent | ACP `session/update` |
|---|---|
| `text_delta` | `agent_message_chunk` |
| `thinking_delta` | `agent_thought_chunk` |
| `tool_execution_start` | `tool_call`（pending） |
| `tool_execution_update` / `end` | `tool_call_update` |
| `agent_end` | `PromptResponse(stop_reason=…)` |

工具 `kind`：`read`→read；`edit`/`write`→edit（`details` 中 unified-diff 填 ACP diff）；`bash`→execute；`grep`/`find`/`ls`→search。TUI 按 kind+title+diff 渲染，不改 coding_tools 名称去迎合 grok 的 `read_file` / `run_terminal_command`。

权限：harness `before_tool_call` 对 `bash`/`edit`/`write` 发 `session/request_permission`；拒绝则 `block=True`。always-approve 走我方 config，不走 grok `_meta`。

---

## 3. 仓库布局

一个 git 仓、两套工具链（uv Python + Cargo Rust）。

```
pi-python/
  pi_agent_core/                 # MIT
  packages/pi-agent-harness/
  packages/pi-agent-cli/         # 标准 ACP agent（P1）
  tui/                           # Apache-2.0，独立 Cargo workspace（已迁入）
    Cargo.toml                   # grok-build 生成的 workspace 根
    SOURCE_REV                   # 上游 commit SHA
    NOTICE                       # Apache §4 变更声明
    crates/codegen/pi-pager/
    crates/codegen/pi-pager-bin/
    ...                          # 其余 crate 先全留
```

操作顺序：

1. `git clone` 到仓外（例如 `d:\work\grok-build`），**不进本仓**。
2. 该目录 `cargo check -p pi-pager-bin`，并列出 pager 的 `x.ai/*` 调用（TUI **拆除清单**）。
3. Spike 通过后，把整棵 workspace（`Cargo.lock`、`.cargo/`、`rust-toolchain.toml`、`third_party/`、`bin/`）放进 `tui/`。
4. 根 `.gitignore` 含 `tui/target/`；hatch/wheel **不 include** `tui/`。
5. 开发：Python 用 `.venv`；TUI 用 `cd tui && cargo run -p pi-pager-bin`（产物二进制名 `pi`）。CI 可另加 cargo check job。

第一轮允许 pager **继续链接** `xai-grok-shell`（编译期），但运行时不走它的 loop。变瘦（减 member）不是迁入前提。

---

## 4. TUI 改造（最小切口）

优先改 pager-bin `main.rs` 与 pager `src/acp/`、`app/effects.rs`，少动 scrollback/render。

必须改：

1. 交互路径 spawn 可配置命令（默认 `python -m pi_agent_cli`），stdio 只走标准 ACP；关掉 in-process / leader grok-shell agent。
2. 拆除全部 `x.ai/*` 客户端调用（fs、git、search、session、auth、terminal、compact、rewind、telemetry）。依赖它们的 slash、模态、启动探针删除或改成本地/标准 ACP。TUI 不得在 method-not-found 上 panic。
3. 跳过 xAI 登录（`ensure_authenticated` / 浏览器 OAuth）。LLM key 只走 Python 环境变量。
4. 关掉 auto-update、otel、`obfstr`/`cryptify`、marketplace。
5. 品牌与家目录：`~/.grok` → `~/.pi-python`（或 `PI_HOME`）。产品二进制名 `pi`。

可保留：Markdown/diff scrollback、流式文本/thinking/tool card、权限 modal、主题、多行输入。`@` 若保留，改为 TUI 进程内列目录，不打 `x.ai/search/fuzzy`。

标准 ACP 映射的 slash：`/new`→`session/new`，`/resume`→`session/load`，`/quit` 退出。其余无标准方法的命令从菜单拿掉。

---

## 5. Python ACP 包（P1）

新包 `packages/pi-agent-cli`，依赖 `agent-client-protocol`、`pi-agent-core-lc`、`pi-agent-harness-lc`。

标准方法：`initialize`（`authMethods=[]`）、`session/new`、`session/prompt`、`session/cancel`、`session/load`、`session/list`、`session/close`。`session/new` 绑定 `create_all_tools(cwd)`、`LocalExecutionEnv`、coding system prompt；权限模式来自 `~/.pi-python/config.toml`。

测试用 mock `stream_fn`，断言 agent **不注册**任何 `x.ai/` 方法。不改 `agent_loop` 不变量。

---

## 6. 配置

`~/.pi-python/config.toml`：

- 默认 model/provider/`base_url`/env key（沿用 LangChain 解析）。
- permission：`ask` | `auto` | `always-approve`。
- skills 目录、max_turns、thinking_level。
- `agent.command`：TUI spawn 的 Python 可执行文件。

Phase 4 带一版可用的 coding system prompt（工具策略、自纠、安全边界 + skills XML）。更深的 prompt 工程仍属 Phase 5。

---

## 7. 分批

| 批次 | 内容 | 状态 |
|---|---|---|
| P0 Spike | 仓外 clone；`cargo check -p pi-pager-bin`；`x.ai/*` 拆除清单；迁入 `tui/` | 完成 |
| P1 | `pi-agent-cli` 标准 ACP + 事件映射 + 权限 hook | 完成 |
| P2 | `tui/` 接线：spawn Python、拆 x.ai 调用、关 auth/update；crate `xai-*`→`pi-*` | 完成 |
| P3 | `/new` `/resume`；本地 `@`；`pi -p` headless | 完成 |
| P4 | config、system prompt、Windows 发布说明 | 完成 |

P0 失败则改构建策略（WSL / CI 出 Windows 二进制），不改「TUI=ACP client、Python=ACP agent」架构。

---

## 8. 风险

- pager 编译期依赖 grok-shell：第一轮链接、运行时不用其 loop。
- 拆除面大：启动路径可能硬编码扩展 RPC；P0 清单必须覆盖 welcome → 第一轮 prompt。
- grok-build 官方称 Windows 源码构建 best-effort。
- 禁止再写一套 grok session 格式当真源。

---

## 9. 参照

- TUI / 运行时：[xai-org/grok-build](https://github.com/xai-org/grok-build)（Apache-2.0，不接受外部 PR）
- ACP：[agentclientprotocol.com](https://agentclientprotocol.com)、[python-sdk](https://github.com/agentclientprotocol/python-sdk)
- 本仓库引擎：`docs/DESIGN.md`、Phase 3 / P6 spec

---

## 10. P0 Spike 记录（2026-08-25）

仓外 clone：**`d:\work\grok-build`**（`--depth 1`，不进本仓）。

| 项 | 值 |
|---|---|
| git HEAD | `c2ad97f87aea4303b6000a2c22128bc91ee76c9b`（`c2ad97f Synced from monorepo`） |
| 上游 `SOURCE_REV` 文件 | `437c7c928f3fcd13e9d37a51d887f41d7f84185d` |
| `cargo check -p pi-pager-bin` | **通过**（WSL2，`rustc 1.94.0`，约 4m26s）。产物目录 `~/grok-build-target` |
| 迁入 `tui/` | **已完成**（`git archive` HEAD `c2ad97f`，LF；含 `SOURCE_REV` / `LICENSE` / 新增 `NOTICE`） |

WSL 代理：Clash 等监听 `0.0.0.0:10809`，但 **不要**用 WSL 网关 `172.26.176.1`（防火墙超时）。用主机局域网 IP（本次为 `172.20.35.30:10809`）。Windows 上 git clone 会使 `bin/protoc` 带 CRLF，Linux 上 shebang 变成 `dotslash\r`；check 前需 `sed -i 's/\r$//' bin/*`，并 `cargo install dotslash`。

拆除清单（从 `pi-pager/src` 抽出的 `"x.ai/…"` 字符串，含 RPC、通知、`_meta` 键）见 [docs/AUDIT/SPIKE-P0-GROK-TUI.md](../AUDIT/SPIKE-P0-GROK-TUI.md)。集中调度在 `src/app/effects/mod.rs`。启动认证走 `session_startup.rs` → `pi_shell::auth::ensure_authenticated_or_noninteractive`。

下一步：P1 `packages/pi-agent-cli`（标准 ACP only）；P2 再改 `tui/` 接线。
