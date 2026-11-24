# zycode (`pi-python`)

[English](README.md) | 简体中文

[![CI](https://github.com/zy1233/pi-python/actions/workflows/ci.yml/badge.svg)](https://github.com/zy1233/pi-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pi-agent-cli-lc)](https://pypi.org/project/pi-agent-cli-lc/)
[![Python](https://img.shields.io/pypi/pyversions/pi-agent-cli-lc)](https://pypi.org/project/pi-agent-cli-lc/)
[![License](https://img.shields.io/pypi/l/pi-agent-cli-lc)](https://github.com/zy1233/pi-python/blob/main/LICENSE)

**zycode** 是一个面向终端与编辑器的开源编程智能体（Coding Agent）。它通过 stdio 使用标准的 [Agent Client Protocol (ACP)](https://agentclientprotocol.com) 协议，可在终端界面（`zypi`）、VS Code（`zycode.zycode`）、Zed 或无界面命令行（`-p`）中运行，底层基于模块化的 Python 智能体运行时构建。

官网：[zycode.dev/zh/](https://zycode.dev/zh/)（[English](https://zycode.dev)）

> 非官方项目：本仓库与官方 [pi](https://github.com/earendil-works/pi) 项目及其维护者没有任何隶属关系，也未获其认可或维护。

## 架构概览

```text
  zypi（Rust 终端界面） / VS Code（zycode.zycode） / Zed 及其他 ACP 客户端
                                  │
                           经 stdio 的 ACP
                                  ▼
             zycode-agent / pi-agent-cli-lc（ACP 与无界面 CLI）
                                  │
                                  ▼
             pi-agent-harness-lc（会话、上下文压缩、技能）
                                  │
                                  ▼
             pi-agent-core-lc（智能体循环、事件协议、编程工具）
                                  │
                                  ▼
             LangChain StreamFn 适配层（OpenAI / Anthropic / DeepSeek / 兼容网关）
```

- **客户端**：
  - `zypi`（`tui/`）：全屏 Rust 终端界面，基于 [grok-build](https://github.com/xai-org/grok-build)（Apache-2.0）分支维护，已移除全部 `x.ai/*` 厂商私有扩展。`zypi` 本身不含模型客户端、工具执行器与 MCP 客户端，只通过 ACP 与智能体通信。
  - `zycode.zycode`（[`vscode/`](vscode/README.md)）：VS Code / Open VSX 扩展，提供侧边聊天视图，并已内置 `zycode-agent` 可执行文件。
  - 任意支持 ACP 的编辑器或客户端（例如 [Zed](#在-zed-中使用自定义智能体) 或 [DeepSeek Harness](docs/DEEPSEEK-HARNESS.md)）。
- **编程智能体（`pi-agent-cli-lc`）**（[`packages/pi-agent-cli/`](packages/pi-agent-cli/README.md)）：标准 ACP stdio 智能体与无界面 `-p` 命令行工具，同时打包为独立二进制 `zycode-agent`。
- **扩展包**：按需安装的 Python 扩展，包括联网搜索与抓取（[`pi-web-access-py`](packages/pi-web-access/README.md)）、目标规划（[`pi-goal-x-py`](packages/pi-goal-x/README.md)）、子智能体工作流编排（[`pi-dynamic-workflows-py`](packages/pi-dynamic-workflows/README.md)）、代码模式（[`pi-codemode-py`](packages/pi-codemode/README.md)）和编辑器 MCP 桥接（[`pi-mcp-py`](packages/pi-mcp/README.md)）。
- **底层 Python SDK**：忠实移植自 `pi-agent-core` 的轻量核心，LangChain 仅作为 `StreamFn` 边界适配层使用。完整底层文档已拆分至对应目录：
  - [`pi_agent_core/README.md`](pi_agent_core/README.md)：核心 Agent Loop、事件协议、LangChain / MCP 工具适配、结构化输出、护栏钩子、内置编程工具与 pi 概念映射。
  - [`packages/pi-agent-harness/README.md`](packages/pi-agent-harness/README.md)：`AgentHarness`、树状会话存储、上下文压缩、自定义消息、技能模板与 `LocalExecutionEnv`。

## 组件状态

| 组件 | 状态 | 发布渠道 |
|------|------|----------|
| `zypi`（Rust 终端界面） | **稳定** | [GitHub Releases](https://github.com/zy1233/pi-python/releases)（Linux x86_64 与 aarch64、macOS arm64） |
| `zycode.zycode`（VS Code 扩展） | **稳定** | [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=zycode.zycode) 与 [Open VSX](https://open-vsx.org/extension/zycode/zycode)（五个平台；项目所有者已在 Windows 上测试 1.0.0 安装与 0.1.0→1.0.0 更新（2026-11-12），并于 2026-11-17 在 Windows 上用真实模型实测 1.4.0 的 `max` 思考等级与 Context 行；Windows 其余流程、macOS 与 Linux 真实 VS Code 环境尚未验证） |
| `pi-agent-cli-lc` | **稳定** | [PyPI](https://pypi.org/project/pi-agent-cli-lc/) |
| `pi-agent-core-lc` | **稳定** | [PyPI](https://pypi.org/project/pi-agent-core-lc/) |
| `pi-agent-harness-lc` | **稳定** | [PyPI](https://pypi.org/project/pi-agent-harness-lc/) |
| `pi-web-access-py` | **稳定** | [PyPI](https://pypi.org/project/pi-web-access-py/) |
| `pi-goal-x-py` | **稳定** | [PyPI](https://pypi.org/project/pi-goal-x-py/) |
| `pi-dynamic-workflows-py` | **稳定** | [PyPI](https://pypi.org/project/pi-dynamic-workflows-py/) |
| `pi-mcp-py` | **稳定** | [PyPI](https://pypi.org/project/pi-mcp-py/)（自 0.6.0 起） |
| `pi-codemode-py` | **稳定** | [PyPI](https://pypi.org/project/pi-codemode-py/)（自 1.1.0 起） |

## 核心特性

- **标准 ACP 编程智能体与无界面 CLI（`pi-agent-cli-lc`）**
  - 通过 stdio 提供标准 ACP 协议接口，可直接接入 `zypi`、VS Code、Zed 及其他 ACP 客户端，无厂商私有扩展。
  - 无界面单次运行模式（`python -m pi_agent_cli -p "提示词"`），适用于脚本与 CI；模型调用失败时向 stderr 输出 `error: <原因>` 并以状态码 1 退出。
  - 首次运行交互式向导（`--setup` 或 `zypi --setup`），将模型配置写入 `~/.pi-python/agent.toml`，API 密钥独立保存于 `~/.pi-python/local.env`（不写入 TOML）。可通过 `PI_HOME` 迁移整个主目录。
  - 支持 `/new`、`/resume`、`/quit` 会话管理、`@` 目录浏览、上下文压缩以及 `/model` 模型切换。
  - 与 pi 对齐的系统提示词引擎：支持工具说明注入、`AGENTS.md` / `CLAUDE.md` 项目上下文文件、`<available_skills>`，以及可选的 `<git_status>` 快照（仅在受信任项目中启用；可通过 `--no-git-context` 或 `[git] enabled = false` 关闭）。
- **全屏终端界面（`zypi`）**
  - Rust 编写的交互式 TUI，支持会话历史、Markdown 渲染、Mermaid 图表以及 Shift+Tab 切换权限模式。
  - 提供独立压缩包（`zycode-<target>-<version>.tar.gz`），同时打包 `zypi` 与 `zycode-agent`，无需预先安装 Python。
  - 详见 [TUI 与编程智能体指南](docs/TUI-AND-CODE-AGENT.md)。
- **VS Code 扩展（`zycode.zycode`）**
  - 内置聊天视图、非只读工具审批卡片、`write`/`edit` 的 **Show diff** 差异预览、**Past chats** 历史会话列表、新手引导、权限模式菜单（**Ask** / **Auto** / **Always approve**）、**Thinking** 思考等级菜单，以及 **Context** 上下文用量条（在 `agent.toml` 中设置 `context_window` 后显示）。
  - 覆盖五个平台并内置 `zycode-agent`，详见 [`vscode/README.md`](vscode/README.md)。
- **内置编程工具与 Python 扩展**
  - 内置七个编程工具：`read`、`edit`、`write`、`bash`、`grep`、`find`、`ls`，具备与 pi 一致的截断策略与分页提示（详见 [`pi_agent_core/README.md`](pi_agent_core/README.md#built-in-coding-tools)）。
  - 可选扩展：联网搜索与抓取（`pi-web-access-py`）、目标规划（`pi-goal-x-py`）、子进程隔离工作流（`pi-dynamic-workflows-py`）、代码模式与延迟工具发现（`pi-codemode-py`），以及 MCP 桥接（`pi-mcp-py`，连接 ACP 客户端传入的 `stdio`/`http`/`sse` 服务器，工具名统一为 `mcp__<server>__<tool>`）。
  - 加载失败的扩展会被跳过并在会话启动时向用户报告，不会静默消失。
  - **macOS 上的 `workflow` 限制：** 脚本子进程在 macOS 上没有内核级内存与进程数上限（CI 在 `macos-15` 与 `macos-15-intel` 上实测确认），仅依赖进程内审计钩子；Linux 与 Windows 具备内核限制。在 macOS 上请保持 `ask` 模式（`auto` 同样会在每次 `workflow` 调用前询问）。详见 [`packages/pi-dynamic-workflows/README.md#macos-weaker-limits`](packages/pi-dynamic-workflows/README.md#macos-weaker-limits)。
- **三种权限模式（`ask`、`auto`、`always-approve`）**
  - **`ask`（默认）：** 按声明授权——除非工具通过 `ToolAnnotations` 声明无害（如只读），否则执行前均会询问；未作声明的工具一律询问。
  - **`auto`：** 只读及规则明确允许范围内的操作自动放行，规则未决的 `bash` 命令交由模型步骤复核（失败闭环为 `ask`；设置 `[auto] classifier = "rules"` 可关闭模型步骤）。
  - **`always-approve`：** 跳过全部工具调用确认（无界面 `-p` 模式亦采用此策略）。
  - **Auto 不是沙箱。** Auto 只约束调用写入或执行的内容，不约束调用读取或外发的内容（`read`、`grep`、`find`、`ls`、`fetch_url`、`web_search` 以及 `bash` 中的递归读取均不询问）。如需隔离边界，请使用沙箱（`zypi --sandbox`、容器或虚拟机）。
- **项目信任机制**
  - 项目自带的扩展（`<project>/.pi-python/extensions`）、保存的工作流（`<project>/.pi-python/workflows`）、`.pi/SYSTEM.md`、`.pi/APPEND_SYSTEM.md`、项目相对路径技能、`<git_status>` 快照（会在仓库中执行 `git`）以及 `workflow` 的 `isolation` worktree 仅在项目受信任后启用。
  - 交互式 ACP 客户端会按目录询问一次，并将受控文件的 SHA-256 指纹保存至 `~/.pi-python/agent/trust.json`，文件变动时会重新询问。也可在 `~/.pi-python/agent.toml` 中配置 `[extensions] trusted_projects`、`default_project_trust`（`ask` / `never` / `always`），或通过 `--trust-project-extensions` / `PI_TRUST_PROJECT_EXTENSIONS=1` 指定。无界面 `-p` 运行从不弹窗询问，但会遵循已保存的信任记录。

## 安装与使用

### 终端界面（`zypi`）

从 [GitHub Releases](https://github.com/zy1233/pi-python/releases) 下载完整独立包（同时包含 `zypi` 与 `zycode-agent`，无需 Python），或单独下载 Linux（`x86_64`、`aarch64`）/ macOS（`arm64`）的 `zypi` 二进制：

```bash
# 方式一：完整独立包（内置 zypi 与 zycode-agent，无需安装 Python）
# 以下为 Linux x86_64 示例；其他平台为 zycode-linux-aarch64-1.1.0.tar.gz 与 zycode-darwin-aarch64-1.1.0.tar.gz。
curl -L -o zycode.tar.gz https://github.com/zy1233/pi-python/releases/download/v1.1.0/zycode-linux-x86_64-1.1.0.tar.gz
tar -xzf zycode.tar.gz
sudo mv zypi zycode-agent /usr/local/bin/

# 在终端中配置模型与 API 密钥，随后启动：
zypi --setup
zypi
```

独立包文件名带有版本号，因此下载链接需使用具体 tag（`v1.1.0`），而非 `latest`。

```bash
# 方式二：单独使用 zypi 二进制 + pip 安装的 Python 智能体
curl -L -o zypi https://github.com/zy1233/pi-python/releases/latest/download/zypi-linux-x86_64
chmod +x zypi
sudo mv zypi /usr/local/bin/

# 指定安装了 pi-agent-cli-lc 的 Python 解释器：
export PI_PYTHON=python3
zypi --setup
```

也可从源码构建 `zypi`（需 Rust 工具链；Windows 下请在 WSL2 中构建并运行）：

```bash
cd tui && cargo build --profile release-dist -p pi-pager-bin
# 产物路径：target/release-dist/zypi
```

平台限制与详细配置（Linux 需 glibc 2.39+、未签名 macOS 构建、WSL2 说明）见 [TUI 与编程智能体指南](docs/TUI-AND-CODE-AGENT.md) 和 [Windows 说明](docs/WINDOWS.md)。

### VS Code 扩展

扩展 `zycode.zycode`（[`vscode/README.md`](vscode/README.md)）将 zycode 集成到 VS Code 的聊天视图中，并自带智能体二进制，无需预先安装 Python 智能体。本扩展为非官方项目，与 pi 及 Microsoft 无隶属关系。

- **VS Code：** 前往 [Visual Studio Marketplace](https://marketplace.visualstudio.com/items?itemName=zycode.zycode) 安装，或执行 `code --install-extension zycode.zycode`。
- **VSCodium / Open VSX：** 前往 [Open VSX](https://open-vsx.org/extension/zycode/zycode) 安装。
- **支持平台：** `linux-x64`、`linux-arm64`、`darwin-arm64`、`darwin-x64` 与 `win32-x64`。
- **当前限制：** 内置智能体未作代码签名，且仅包含 OpenAI 格式提供方（如需 Anthropic 或 DeepSeek 提供方，请安装 Python 版 `pi-agent-cli-lc` 并配置 `zycode.agentPath`）。项目所有者已在 Windows 上测试 1.0.0 安装与 0.1.0→1.0.0 更新（2026-11-12），并于 2026-11-17 在 Windows 上用真实模型实测 1.4.0（`max` 思考等级，以及在 `agent.toml` 中设置 `context_window` 后正常显示 Context 行）。Windows 其余流程、全部 macOS 流程以及 Linux 真实 VS Code 环境尚未验证。发布记录见 [`vscode/RELEASING.md`](vscode/RELEASING.md)。

### 在 Zed 中使用（自定义智能体）

由于智能体通过 stdio 提供标准 ACP 接口，可在 [Zed](https://zed.dev) 中将其配置为自定义智能体。先安装 [uv](https://docs.astral.sh/uv/)，然后在 Zed 的 `settings.json` 中添加：

```json
{
  "agent_servers": {
    "pi-python": {
      "type": "custom",
      "command": "uvx",
      "args": [
        "--from", "pi-agent-cli-lc",
        "--with", "pi-agent-core-lc[deepseek]",
        "pi-agent-cli"
      ],
      "env": {}
    }
  }
}
```

- `--with` 用于安装对应的 LangChain 模型提供方包（使用 OpenAI 或 Anthropic 时替换为 `[openai]` 或 `[anthropic]`）。
- 首次运行时，Zed 会提供“Set up model and API key”终端登录入口（执行 `--setup` 写入 `~/.pi-python/agent.toml` 与 `~/.pi-python/local.env`），也可在终端手动运行 `uvx --from pi-agent-cli-lc --with "pi-agent-core-lc[deepseek]" pi-agent-cli --setup`。（该终端登录流程已通过模拟 ACP 客户端验证，尚未在 Zed 本体中实测。）
- 手动配置可参考 [`packages/pi-agent-cli/agent.example.toml`](packages/pi-agent-cli/agent.example.toml)，API 密钥通过环境变量或 `~/.pi-python/local.env` 提供，切勿写入 `agent.toml`。
- 如需运行本地仓库代码，可将 `command` 指向虚拟环境的 Python，并将 `args` 设为 `["-m", "pi_agent_cli"]`。

### 无界面 CLI 与 Python 包（PyPI）

```bash
# 安装编程智能体 CLI 及模型提供方：
pip install pi-agent-cli-lc
pip install "pi-agent-core-lc[openai]"     # 或 [anthropic]、[deepseek]

# 首次运行交互式配置：
python -m pi_agent_cli --setup

# 无界面单次运行（适用于脚本与 CI）：
python -m pi_agent_cli -p "Explain this codebase" --cwd /path/to/project

# 可选扩展包：
pip install pi-web-access-py          # 联网搜索与网页抓取
pip install pi-goal-x-py              # 目标驱动规划（/goal）
pip install pi-dynamic-workflows-py   # 子智能体工作流编排
pip install pi-mcp-py                 # 连接 ACP 编辑器传入的 MCP 服务器
```

如果你希望直接以 Python 库形式使用底层的 Agent Loop 或 Harness，请参阅 [`pi_agent_core/README.md`](pi_agent_core/README.md) 和 [`packages/pi-agent-harness/README.md`](packages/pi-agent-harness/README.md)。

<details>
<summary>本地开发安装（可编辑模式）</summary>

```bash
pip install -e ".[dev]"
pip install -e "./packages/pi-agent-harness"
pip install -e "./packages/pi-agent-cli"
pip install -e "./packages/pi-web-access"
pip install -e "./packages/pi-goal-x"
pip install -e "./packages/pi-dynamic-workflows"
pip install -e "./packages/pi-mcp"
```

</details>

### 其他 ACP 客户端（DeepSeek Harness）

zycode 也可接入 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`dsh`），作为委派子智能体（`@deepseek-ai/dsh-subagent-acp`）运行，或通过社区插件（`@zaimokuza/dsh-acp-adapter`）以完整会话形式使用。配置方式、权限/信任注意事项及验证范围见 [docs/DEEPSEEK-HARNESS.md](docs/DEEPSEEK-HARNESS.md)。

## 基准评估

项目在 **FrontierHarness 30**（21 道 Terminal-Bench + 9 道 DeepSWE 工业任务，覆盖 Windows 本地与 WSL2 Docker 沙箱运行时）上进行了评测，并附带与官方 Pi 的对比及评测方法局限说明。

完整报告：[FRONTIER-HARNESS-30-EVAL-REPORT.md](docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md)。

## 开发与测试

```bash
# Python 测试套件（无需 API 密钥；默认排除 real_llm 用例）
uv run --extra dev --extra harness python -m pytest
ruff check . && ruff format --check .

# Rust TUI 编译检查（Linux / WSL2）
cd tui && cargo check -p pi-pager-bin

# VS Code 扩展检查
cd vscode && npm run check
```

真实 LLM 回归测试、多提供方矩阵与冒烟脚本见 [`pi_agent_core/README.md#testing`](pi_agent_core/README.md#testing)；项目约束与贡献指南见 [`AGENTS.md`](AGENTS.md)。

## 文档索引

| 文档 | 说明 |
|------|------|
| [**TUI 与编程智能体指南**](docs/TUI-AND-CODE-AGENT.md) | `zypi` 终端界面与 `pi-agent-cli-lc` 的安装、配置与使用 |
| [**VS Code 扩展文档**](vscode/README.md) | `zycode.zycode` 的功能、权限、思考等级菜单、设置与限制 |
| [**ACP CLI 包文档**](packages/pi-agent-cli/README.md) | `pi-agent-cli-lc` 入口、无界面参数、配置与向导 |
| [**核心运行时与编程工具**](pi_agent_core/README.md) | `pi-agent-core-lc`：Agent Loop、LangChain 适配、结构化输出、护栏、编程工具、事件契约与 pi 概念映射 |
| [**AgentHarness 运行时**](packages/pi-agent-harness/README.md) | `pi-agent-harness-lc`：会话树、上下文压缩、自定义消息、技能模板与 `LocalExecutionEnv` |
| [**DeepSeek Harness 集成指南**](docs/DEEPSEEK-HARNESS.md) | 在 DeepSeek Harness 中运行 zycode（子智能体与社区 ACP 适配器） |
| [Windows 使用说明](docs/WINDOWS.md) | Windows 配置、WSL2 下 TUI 构建与启动说明 |
| [基准评估报告](docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md) | FrontierHarness 30 任务评测与 Pi 对比 |
| [架构与设计规格](docs/DESIGN.md) | 项目整体架构；各阶段规格见 `docs/specs/`，审计记录见 `docs/AUDIT/` |
| [AGENTS.md](AGENTS.md) | 仓库模块索引、核心不变量与开发工作流 |

## 路线图

核心运行时、`AgentHarness`、编程工具、ACP CLI、`zypi` TUI 及扩展包均已发布 `1.1.0` 稳定版，VS Code 扩展已在 VS Code Marketplace 与 Open VSX 发布稳定版（完整演进记录见 [`CHANGELOG.md`](CHANGELOG.md) 与 [`vscode/CHANGELOG.md`](vscode/CHANGELOG.md)）。

**下一步：**

- **TUI 平台扩展** — 计划中，暂无时间表：提供 Windows 预编译版 `zypi`。暂无且不计划提供 macOS `x86_64` 版 `zypi`，亦不提供自动更新。

## 许可证

Python 包（`pi-agent-core-lc`、`pi-agent-harness-lc`、`pi-agent-cli-lc` 及各扩展包）与 VS Code 扩展（`vscode/`）采用 **MIT** 许可证。

`tui/` 目录为 [xai-org/grok-build](https://github.com/xai-org/grok-build) 的分支，保留 **Apache License 2.0** 许可证，详见 `tui/LICENSE`、`tui/NOTICE` 与 `tui/THIRD-PARTY-NOTICES`。`tui/` 不包含在 Python sdist 或 wheel 发布包中。
