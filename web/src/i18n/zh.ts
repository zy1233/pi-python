import { snippets } from '~/data/site';
import type { Messages } from './types';

export const zh: Messages = {
  lang: 'zh-Hans',
  hreflang: 'zh-Hans',
  ogLocale: 'zh_CN',
  meta: {
    title: 'zycode — 可审计的编程智能体',
    description:
      'zycode 是开源的编程智能体，可用于终端和编辑器。它使用标准的 ACP 协议，遵循书面的事件契约，界面层不接触模型。',
    ogAlt: 'zycode：可审计的编程智能体',
  },
  skip: '跳到正文',
  nav: {
    label: '主导航',
    why: '理念',
    contract: '契约',
    how: '架构',
    features: '功能',
    install: '安装',
    status: '状态',
  },
  ui: {
    github: 'GitHub',
    pypi: 'PyPI',
    x: 'X',
    copy: '复制',
    copied: '已复制',
    replay: '重播演示',
    themeToDark: '切换到深色主题',
    themeToLight: '切换到浅色主题',
    langSwitchLabel: '用英文阅读本页',
    langSwitchText: 'EN',
    home: 'zycode，首页',
    newTab: '在新标签页打开',
  },
  hero: {
    badge: '测试中',
    badgeNote: '开源 · MIT 与 Apache-2.0',
    titleBefore: '一个',
    titleEm: '可审计',
    titleAfter: '的编程智能体。',
    lead: 'zycode 的智能体循环小而清晰，并有书面契约。每一轮对话、每一次工具调用，都是可以记录和测试的事件。你可以在终端里用它，也可以在任何支持 ACP 的编辑器里用它。',
    ctaPrimary: '开始使用',
    ctaSecondary: '在 GitHub 查看',
    installLabel: '安装智能体',
    installCommand: 'pip install pi-agent-cli-lc',
    note: '目前项目以 `zypi`（终端界面）和 `pi-agent-cli-lc`（智能体）的名称发布。',
  },
  terminal: {
    label: 'zypi 会话的示意图。智能体读了三个文件，改了一个文件，并运行了测试。',
    title: 'zypi',
    tag: '测试版',
    prompt: '结账测试在舍入处失败。找出原因并修复。',
    tagFree: '只读 · 不询问',
    tagAsked: '已询问 · 你已允许',
    say1: '总价用的是 `float`。`round(2.675, 2)` 的结果是 2.67，因为 2.675 没有精确的 `float` 表示。改用 `Decimal` 和 `ROUND_HALF_UP`。',
    say2: '完成。结账测试已通过。',
    caption: '示意图，不是录屏。',
  },
  principles: {
    eyebrow: '理念',
    title: '为被检查而造，而不是要你盲信。',
    lead: '编程智能体会改你的文件、跑你的命令。zycode 让每一步都看得见、有界限、能测试。',
    cards: {
      contract: {
        title: '书面的事件契约',
        body: '每次运行都按相同的顺序发出相同的事件。测试把这个顺序固定下来。你可以记录这些事件，并在其上构建。',
      },
      protocol: {
        title: '一个协议，任何客户端',
        body: '智能体通过 stdio 使用 Agent Client Protocol（ACP），没有厂商私有调用。终端界面、Zed 和其他 ACP 客户端启动的是同一个进程。',
      },
      noClient: {
        title: '界面里没有模型客户端',
        body: '`zypi` 没有模型客户端、工具执行器和 MCP 客户端，这些工作都由 Python 智能体完成。对一次真实对话轮次的审计，没有发现 `zypi` 连接机器以外的地址。',
      },
      permission: {
        title: '按声明授权',
        body: '在 `ask` 模式下，除非工具声明自己无害，否则智能体在调用前都会询问。没有任何声明的工具也会被询问。',
      },
      trust: {
        title: '先信任项目',
        body: '项目可以自带扩展、工作流、提示词和技能。只有你信任该项目之后，智能体才会加载它们。这些文件一旦变化，智能体会再次询问。',
      },
      models: {
        title: '任何模型，同一个循环',
        body: 'LangChain 只是通向模型的边界。循环、工具和事件都在我们自己的代码里。可以用 OpenAI、Anthropic、DeepSeek，或任何兼容 OpenAI 的网关。',
      },
    },
  },
  contract: {
    eyebrow: '契约',
    title: '每次运行，同样的事件。',
    lead: '核心循环忠实移植自 pi 项目的智能体循环。它的事件协议就是契约，由测试强制执行。',
    listTitle: '一轮带工具调用的对话',
    notes: {
      agentStart: '运行开始。',
      turnStart: '一轮开始。',
      userMsg: '用户消息。',
      asstStart: '助手消息开始。',
      update: '重复出现。携带文本、思考和工具调用的增量。',
      asstEnd: '助手消息结束。',
      toolStart: '每次工具调用一个事件。',
      toolEnd: '按完成顺序触发。',
      toolResult: '`toolResult` 消息。按来源顺序保存。',
      turnEnd: '这一轮结束。循环可以开始下一轮。',
      agentEnd: '运行结束。',
    },
    rulesTitle: '测试强制执行的四条规则',
    rules: {
      parallel: {
        title: '并行工具',
        body: '`tool_execution_end` 按完成顺序触发。`toolResult` 消息按来源顺序保存。',
      },
      terminate: {
        title: '终止',
        body: '只有当所有已完成的工具结果都设置了 `terminate=True` 时，循环才会跳过下一次模型调用。',
      },
      noRaise: {
        title: '不抛出异常',
        body: '`StreamFn`（调用模型的流式函数）从不抛出异常。失败会变成 `error` 事件，`stop_reason` 为 `error` 或 `aborted`。',
      },
      usage: {
        title: '用量',
        body: '令牌用量取各字段已报告快照的最大值，而不是求和。',
      },
    },
  },
  architecture: {
    eyebrow: '架构',
    title: '部件小，边界清楚。',
    lead: '每一层只做一件事。你可以替换界面、模型提供方或扩展，而不用改循环。',
    layers: {
      zypi: { title: 'zypi', body: '终端界面。负责绘制屏幕，并使用 ACP 通信。' },
      editors: { title: 'Zed 与其他 ACP 客户端', body: '编辑器。启动的是同一个智能体进程。' },
      cli: {
        title: 'pi_agent_cli',
        body: 'ACP 智能体。系统提示词引擎、权限、项目信任和无界面模式。',
      },
      harness: {
        title: 'pi_agent_harness',
        body: '会话、压缩、技能、提示词模板和本地执行环境。',
      },
      core: {
        title: 'pi_agent_core',
        body: '智能体循环、事件协议、工具执行和编程工具。',
      },
      models: {
        title: 'LangChain 流式适配层',
        body: 'OpenAI、Anthropic、DeepSeek，或任何兼容 OpenAI 的网关。',
      },
      extensions: { title: '扩展', body: '使用扩展 API 的 Python 包。' },
    },
    linkAcp: '经 stdio 的 ACP',
    linkModels: 'HTTPS',
    extensionsList: ['联网访问', '目标规划', '工作流', 'MCP'],
    licenseNote:
      'Python 包使用 MIT 许可。`zypi` 是 grok-build 的分支，已删除所有 `x.ai` 厂商扩展，保持 Apache-2.0 许可。',
  },
  features: {
    eyebrow: '功能',
    title: '今天就能用到的。',
    lead: '这份清单只写代码现在能做的事，不写计划。',
    items: {
      tools: {
        title: '七个编程工具',
        body: '`read`、`edit`、`write`、`bash`、`grep`、`find` 和 `ls`。智能体截断输出时，会有提示告诉模型如何读取其余部分。',
      },
      sessions: {
        title: '可以恢复的会话',
        body: '在界面里用 `/new` 和 `/resume`，或在命令行用 `zypi --continue`。压缩功能让长会话保持在上下文窗口之内。',
      },
      headless: {
        title: '无界面模式',
        body: '在脚本和 CI 里运行 `python -m pi_agent_cli -p "提示词"`。模型调用失败时，它向 stderr 输出错误，并以状态码 1 退出。',
      },
      context: {
        title: '技能与上下文文件',
        body: '智能体会读取 `AGENTS.md` 和 `CLAUDE.md` 上下文文件，并把你的技能列在系统提示词里。',
      },
      extensions: {
        title: '用 Python 写扩展',
        body: '联网搜索与抓取、目标规划、工作流和 MCP 都是独立的包。你也可以用扩展 API 自己写。',
      },
      sandbox: {
        title: '子进程里的工作流',
        body: '工作流脚本在带操作系统限制的子进程里运行。边界是这些限制，审计钩子只是减速带。',
      },
      limits: {
        title: '可以设置的限制',
        body: '可以设置 `max_turns`、每个工具的超时，以及首个令牌之前的重试。每次模型调用前后都会运行钩子。',
      },
      thinking: {
        title: '思考与成本',
        body: '推理参数会映射到各个提供方。思考内容以事件形式流出。智能体会统计每次运行的令牌用量和成本。',
      },
    },
  },
  install: {
    eyebrow: '安装',
    title: '装好，然后选一个模型。',
    lead: '先安装 Python 智能体，再添加终端界面，或连接你的编辑器。',
    tabListLabel: '安装方式',
    tabs: {
      tui: {
        label: '终端界面',
        heading: '终端界面（zypi）',
        codeLabel: 'bash',
        code: `# 1. 安装智能体，然后选择提供方、模型和密钥
pip install pi-agent-cli-lc
python -m pi_agent_cli --setup

# 2. 安装终端界面（Linux x86_64）
curl -L -o zypi https://github.com/zy1233/pi-python/releases/latest/download/zypi-linux-x86_64
chmod +x zypi && sudo mv zypi /usr/local/bin/

# 3. 在你的项目目录里启动
zypi`,
        notes: [
          'Linux 二进制需要 glibc 2.39 或更高版本（Ubuntu 24.04 或更新）。',
          '在 Apple 芯片的 macOS 上，使用文件 `zypi-macos-arm64`。它由 CI 构建，还没有人实际运行过，也没有签名。',
          '在 Windows 上，请从源码构建 `zypi` 并在 WSL2 中运行。Python 智能体可以直接在 Windows 上运行。',
        ],
      },
      zed: {
        label: 'Zed',
        heading: 'Zed（自定义智能体）',
        codeLabel: 'settings.json',
        code: snippets.zed,
        notes: [
          '先安装 [uv](https://docs.astral.sh/uv/)。智能体还不在 Zed 的 ACP 注册表里，所以要作为自定义智能体添加。',
          '先在终端里运行一次 `--setup`，或把模型写进 `~/.pi-python/agent.toml`。',
          '使用 OpenAI 或 Anthropic 时，把 `[deepseek]` 换成 `[openai]` 或 `[anthropic]`。',
        ],
      },
      headless: {
        label: '无界面',
        heading: '无界面（脚本与 CI）',
        codeLabel: 'bash',
        code: `pip install pi-agent-cli-lc

# 一条提示词，没有界面
python -m pi_agent_cli -p "Explain this codebase" --cwd /path/to/project

# 不需要模型和密钥：脚本化的测试替身
PI_USE_MOCK=1 python -m pi_agent_cli -p "hello" --cwd .`,
        notes: [
          '模型调用失败时，会向 stderr 输出 `error: <原因>`，并以状态码 1 退出。',
          '无界面运行从不提问。它会使用你之前对项目信任问题给出的答案。',
        ],
      },
      python: {
        label: 'Python 库',
        heading: 'Python 库',
        codeLabel: 'python',
        code: snippets.python,
        notes: [
          '用 `pip install "pi-agent-core-lc[openai]"` 安装。其他提供方使用 `[anthropic]` 或 `[deepseek]`。',
          '整个测试套件不需要 API 密钥，因为它使用模拟的流。',
        ],
      },
    },
  },
  status: {
    eyebrow: '状态',
    title: '如实的状态。',
    lead: 'zycode 处于测试阶段。下表说明哪些部分稳定，哪些还不稳定。',
    columns: { component: '组件', level: '状态', role: '作用', source: '获取方式' },
    levels: { stable: '稳定', beta: '测试', alpha: '早期' },
    where: { pypi: 'PyPI', releases: 'GitHub Releases' },
    descriptions: {
      core: '智能体循环、事件、工具、LangChain 适配器',
      harness: '会话、压缩、技能、执行环境',
      cli: 'ACP 智能体与无界面模式',
      zypi: '终端界面（预编译版：Linux x86_64）',
      webAccess: '联网搜索与抓取',
      goalX: '目标规划',
      workflows: '工作流',
      mcp: '编辑器传入的 MCP 服务器',
    },
    next: '下一步：为 macOS x86_64 和 Windows 提供预编译的 `zypi`，并支持自动更新。',
  },
  evidence: {
    eyebrow: '证据',
    title: '我们公开测试方法，也公开出过的错。',
    lead: '我们运行一个 30 题的基准：21 道来自 Terminal-Bench，9 道来自 DeepSWE。报告列出了我们在自己的方法里发现的缺陷。',
    stat: '24 / 30',
    statLabel: '题通过',
    statNote: '每题取最新的有效运行',
    readTitle: '如何理解这个数字',
    points: [
      '每题只跑一次。同一道题再跑一次，结果可能差别很大。',
      'DeepSWE 的题用了两种轮次上限：60 轮和 100 轮。',
      '模型：DeepSeek-V4-Flash。DeepSWE 的重跑通过网关别名调用模型，我们无法核实别名背后的模型。',
      '我们发现了两处会让智能体看到标准答案的泄露。第一处已修复，第二处作为已知问题公布。受影响的数字已更正。',
      '这不是排行榜成绩，也不是与其他智能体的同口径对比。',
    ],
    cta: '阅读完整报告',
  },
  open: {
    eyebrow: '开源',
    title: '可读，可运行，可修改。',
    body: 'Python 包使用 MIT 许可。`zypi` 是 `xai-org/grok-build` 的分支，保持 Apache-2.0 许可。',
    unofficial:
      'zycode 基于 pi 项目的 `pi-agent-core` 移植而来。这是一个非官方项目，与 pi 项目及其维护者没有隶属关系，也没有得到他们的认可或维护。',
    ctaStar: '在 GitHub 加星',
    ctaIssue: '提交 issue',
    ctaChangelog: '查看更新日志',
  },
  footer: {
    tagline: '一个可以审计的编程智能体。',
    groups: { project: '项目', docs: '文档', more: '相关' },
    links: {
      github: 'GitHub 源码',
      releases: '发布页',
      changelog: '更新日志',
      issues: 'Issues',
      design: '设计文档',
      tuiGuide: '终端界面指南',
      windows: 'Windows 说明',
      benchmark: '基准报告',
      acp: 'Agent Client Protocol',
      pi: 'pi 项目',
    },
    legal: '以 MIT 与 Apache-2.0 许可开源。',
    built: '使用 Astro 构建，托管在 Cloudflare。',
  },
  notFound: {
    title: '找不到页面',
    body: '这个地址没有对应的页面。This address has no page.',
    back: '返回首页',
  },
};
