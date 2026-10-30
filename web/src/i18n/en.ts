import { snippets } from '~/data/site';
import type { Messages } from './types';

export const en: Messages = {
  lang: 'en',
  hreflang: 'en',
  ogLocale: 'en_US',
  meta: {
    title: 'zycode — a coding agent you can audit',
    description:
      'zycode is an open-source coding agent for your terminal and your editor. It speaks ACP, follows a written event contract, and keeps model access out of the UI.',
    ogAlt: 'zycode: a coding agent you can audit',
  },
  skip: 'Skip to content',
  nav: {
    label: 'Main',
    why: 'Why',
    contract: 'Contract',
    how: 'Architecture',
    features: 'Features',
    install: 'Install',
    status: 'Status',
  },
  ui: {
    github: 'GitHub',
    pypi: 'PyPI',
    copy: 'Copy',
    copied: 'Copied',
    replay: 'Replay the demo',
    themeToDark: 'Switch to the dark theme',
    themeToLight: 'Switch to the light theme',
    langSwitchLabel: 'Read this page in Chinese',
    langSwitchText: '中文',
    home: 'zycode, home page',
    newTab: 'opens in a new tab',
  },
  hero: {
    badge: 'In beta',
    badgeNote: 'Open source · MIT and Apache-2.0',
    titleBefore: 'A coding agent you can\u00a0',
    titleEm: 'audit',
    titleAfter: '.',
    lead: 'zycode runs a small agent loop with a written contract. Every turn and every tool call is an event that you can log and test. Use it in your terminal, or in any editor that speaks ACP.',
    ctaPrimary: 'Get started',
    ctaSecondary: 'View on GitHub',
    installLabel: 'Install the agent',
    installCommand: 'pip install pi-agent-cli-lc',
    note: 'Today the project ships as `zypi` (the terminal UI) and `pi-agent-cli-lc` (the agent).',
  },
  terminal: {
    label: 'An illustration of a zypi session. The agent reads three files, edits one, and runs the tests.',
    title: 'zypi',
    tag: 'beta',
    prompt: 'The checkout test fails on rounding. Find the cause and fix it.',
    tagFree: 'read-only · no prompt',
    tagAsked: 'asked · you allowed',
    say1: 'The totals use `float`. `round(2.675, 2)` gives 2.67, because 2.675 has no exact `float` form. Use `Decimal` with `ROUND_HALF_UP`.',
    say2: 'Done. The checkout tests pass.',
    caption: 'Illustration. This is not a recording.',
  },
  principles: {
    eyebrow: 'Why',
    title: 'Built to be checked, not trusted.',
    lead: 'A coding agent changes your files and runs your commands. zycode makes each of these steps visible, limited and testable.',
    cards: {
      contract: {
        title: 'A written event contract',
        body: 'Each run emits the same events in the same order. Tests keep that order fixed. You can log the events and build on them.',
      },
      protocol: {
        title: 'One protocol, any client',
        body: 'The agent speaks the Agent Client Protocol (ACP) over stdio, with no vendor-specific calls. The terminal UI, Zed and other ACP clients start the same process.',
      },
      noClient: {
        title: 'A UI with no model client',
        body: '`zypi` has no model client, no tool runner and no MCP client. The Python agent does that work. An audit of one real turn found no connection from `zypi` to an address outside the machine.',
      },
      permission: {
        title: 'Permission by declaration',
        body: 'In `ask` mode, the agent asks before a tool call unless the tool declares that it is harmless. A tool that declares nothing is asked about.',
      },
      trust: {
        title: 'Project trust',
        body: 'A project can ship extensions, workflows, prompts and skills. The agent loads them only after you trust the project. It asks again when one of those files changes.',
      },
      models: {
        title: 'Any model, one loop',
        body: 'LangChain is only the boundary to the model. The loop, the tools and the events stay in our code. Use OpenAI, Anthropic, DeepSeek or an OpenAI-compatible gateway.',
      },
    },
  },
  contract: {
    eyebrow: 'Contract',
    title: 'The same events, every run.',
    lead: 'The core loop is a faithful port of the agent loop of the pi project. Its event protocol is a contract, and tests enforce it.',
    listTitle: 'One turn with a tool call',
    notes: {
      agentStart: 'The run starts.',
      turnStart: 'A turn starts.',
      userMsg: 'The user message.',
      asstStart: 'The assistant message starts.',
      update: 'Repeats. Carries text, thinking and tool-call deltas.',
      asstEnd: 'The assistant message ends.',
      toolStart: 'One event for each tool call.',
      toolEnd: 'Fires in completion order.',
      toolResult: 'The `toolResult` messages. Saved in source order.',
      turnEnd: 'The turn ends. The loop can start another turn.',
      agentEnd: 'The run ends.',
    },
    rulesTitle: 'Four rules that tests enforce',
    rules: {
      parallel: {
        title: 'Parallel tools',
        body: '`tool_execution_end` fires in completion order. `toolResult` messages are saved in source order.',
      },
      terminate: {
        title: 'Terminate',
        body: 'The loop skips the next model call only when all finalized tool results set `terminate=True`.',
      },
      noRaise: {
        title: 'No raised errors',
        body: 'The stream function never raises. A failure becomes an `error` event, with `stop_reason` set to `error` or `aborted`.',
      },
      usage: {
        title: 'Usage',
        body: 'Token usage is the per-field maximum of the reported snapshots, not their sum.',
      },
    },
  },
  architecture: {
    eyebrow: 'Architecture',
    title: 'Small parts with clear borders.',
    lead: 'Each layer has one job. You can replace the UI, the model provider or an extension without a change to the loop.',
    layers: {
      zypi: { title: 'zypi', body: 'The terminal UI. It draws the screen and talks ACP.' },
      editors: { title: 'Zed and other ACP clients', body: 'Editors. They start the same agent process.' },
      cli: {
        title: 'pi_agent_cli',
        body: 'The ACP agent. System prompt engine, permissions, project trust and headless mode.',
      },
      harness: {
        title: 'pi_agent_harness',
        body: 'Sessions, compaction, skills, prompt templates and the local execution environment.',
      },
      core: {
        title: 'pi_agent_core',
        body: 'The agent loop, the event protocol, tool execution and the coding tools.',
      },
      models: {
        title: 'LangChain stream function',
        body: 'OpenAI, Anthropic, DeepSeek or any OpenAI-compatible gateway.',
      },
      extensions: { title: 'Extensions', body: 'Python packages that use the extension API.' },
    },
    linkAcp: 'ACP over stdio',
    linkModels: 'HTTPS',
    extensionsList: ['web access', 'goal planning', 'workflows', 'MCP'],
    licenseNote:
      'The Python packages use the MIT license. `zypi` is a fork of grok-build with all `x.ai` vendor extensions removed. It keeps the Apache-2.0 license.',
  },
  features: {
    eyebrow: 'Features',
    title: 'What you get today.',
    lead: 'This list shows what the code does now. It does not show plans.',
    items: {
      tools: {
        title: 'Seven coding tools',
        body: '`read`, `edit`, `write`, `bash`, `grep`, `find` and `ls`. When the agent cuts an output, a notice tells the model how to read the rest.',
      },
      sessions: {
        title: 'Sessions that you can resume',
        body: 'Use `/new` and `/resume` in the UI, or `zypi --continue` on the command line. Compaction keeps a long session inside the context window.',
      },
      headless: {
        title: 'Headless mode',
        body: 'Run `python -m pi_agent_cli -p "prompt"` in scripts and CI. If the model call fails, it prints the error to stderr and exits with status 1.',
      },
      context: {
        title: 'Skills and context files',
        body: 'The agent reads `AGENTS.md` and `CLAUDE.md` context files. It lists your skills in the system prompt.',
      },
      extensions: {
        title: 'Extensions in Python',
        body: 'Web search and fetch, goal planning, workflows and MCP ship as separate packages. You can write your own with the extension API.',
      },
      sandbox: {
        title: 'Workflows in a child process',
        body: 'Workflow scripts run in a child process with operating-system limits. The limits are the boundary. The audit hook is only a speed bump.',
      },
      limits: {
        title: 'Limits that you can set',
        body: 'Set `max_turns`, a timeout for each tool, and retries before the first token. Hooks run before and after each model call.',
      },
      thinking: {
        title: 'Thinking and cost',
        body: 'Reasoning parameters map to each provider. Thinking streams as events. The agent tracks token usage and cost for each run.',
      },
    },
  },
  install: {
    eyebrow: 'Install',
    title: 'Install it, then pick a model.',
    lead: 'Install the Python agent first. Then add the terminal UI, or connect your editor.',
    tabListLabel: 'Ways to install',
    tabs: {
      tui: {
        label: 'Terminal UI',
        heading: 'Terminal UI (zypi)',
        codeLabel: 'bash',
        code: `# 1. Install the agent, then choose a provider, a model and a key
pip install pi-agent-cli-lc
python -m pi_agent_cli --setup

# 2. Install the terminal UI (Linux x86_64)
curl -L -o zypi https://github.com/zy1233/pi-python/releases/latest/download/zypi-linux-x86_64
chmod +x zypi && sudo mv zypi /usr/local/bin/

# 3. Start it in your project
zypi`,
        notes: [
          'The Linux binary needs glibc 2.39 or newer (Ubuntu 24.04 or later).',
          'On macOS with Apple silicon, use the file `zypi-macos-arm64`. CI builds it. A person has not run it yet. It is not signed.',
          'On Windows, build `zypi` from source and run it in WSL2. The Python agent runs on Windows.',
        ],
      },
      zed: {
        label: 'Zed',
        heading: 'Zed (custom agent)',
        codeLabel: 'settings.json',
        code: snippets.zed,
        notes: [
          'Install [uv](https://docs.astral.sh/uv/) first. The agent is not in the Zed ACP registry, so add it as a custom agent.',
          'Run `--setup` once in a terminal, or put the model in `~/.pi-python/agent.toml`.',
          'Use `[openai]` or `[anthropic]` instead of `[deepseek]` for those providers.',
        ],
      },
      headless: {
        label: 'Headless',
        heading: 'Headless (scripts and CI)',
        codeLabel: 'bash',
        code: `pip install pi-agent-cli-lc

# One prompt, no UI
python -m pi_agent_cli -p "Explain this codebase" --cwd /path/to/project

# No model, no key: a scripted test double
PI_USE_MOCK=1 python -m pi_agent_cli -p "hello" --cwd .`,
        notes: [
          'A failed model call prints `error: <reason>` to stderr and exits with status 1.',
          'Headless runs never ask a question. They use an answer that you gave before about project trust.',
        ],
      },
      python: {
        label: 'Python library',
        heading: 'Python library',
        codeLabel: 'python',
        code: snippets.python,
        notes: [
          'Install with `pip install "pi-agent-core-lc[openai]"`. Use `[anthropic]` or `[deepseek]` for other providers.',
          'The whole test suite runs without an API key, because it uses a mock stream.',
        ],
      },
    },
  },
  status: {
    eyebrow: 'Status',
    title: 'The status, as it is.',
    lead: 'zycode is in beta. This table shows what is stable and what is not.',
    columns: { component: 'Component', level: 'Status', role: 'What it does', source: 'Get it from' },
    levels: { stable: 'Stable', beta: 'Beta', alpha: 'Alpha' },
    where: { pypi: 'PyPI', releases: 'GitHub Releases' },
    descriptions: {
      core: 'Agent loop, events, tools, LangChain adapter',
      harness: 'Sessions, compaction, skills, execution environment',
      cli: 'ACP agent and headless mode',
      zypi: 'Terminal UI (prebuilt for Linux x86_64)',
      webAccess: 'Web search and fetch',
      goalX: 'Goal planning',
      workflows: 'Workflows',
      mcp: 'MCP servers that the editor passes',
    },
    next: 'Next: prebuilt `zypi` binaries for macOS x86_64 and Windows, and automatic updates.',
  },
  evidence: {
    eyebrow: 'Evidence',
    title: 'We publish how we test, and what went wrong.',
    lead: 'We run a benchmark of 30 tasks: 21 from Terminal-Bench and 9 from DeepSWE. The report lists the flaws that we found in our own method.',
    stat: '24 / 30',
    statLabel: 'tasks passed',
    statNote: 'Latest valid run of each task',
    readTitle: 'How to read this number',
    points: [
      'One trial for each task. A second run of the same task can give a very different result.',
      'The DeepSWE tasks ran with two turn limits: 60 turns and 100 turns.',
      'Model: DeepSeek-V4-Flash. The DeepSWE reruns used a gateway alias, and we cannot verify the model behind it.',
      'We found two ways for the agent to see the answers. We fixed the first. We published the second as a known issue. We corrected the numbers that they touched.',
      'This is not a leaderboard entry. It is not a like-for-like comparison with other agents.',
    ],
    cta: 'Read the full report',
  },
  open: {
    eyebrow: 'Open source',
    title: 'Read it. Run it. Change it.',
    body: 'The Python packages use the MIT license. `zypi` is a fork of xai-org/grok-build and keeps the Apache-2.0 license.',
    unofficial:
      'zycode builds on a port of `pi-agent-core` from the pi project. This is an unofficial project. It is not affiliated with, endorsed by or maintained by the pi project or its maintainers.',
    ctaStar: 'Star on GitHub',
    ctaIssue: 'Open an issue',
    ctaChangelog: 'Read the changelog',
  },
  footer: {
    tagline: 'A coding agent you can audit.',
    groups: { project: 'Project', docs: 'Documents', more: 'Related' },
    links: {
      github: 'Source on GitHub',
      releases: 'Releases',
      changelog: 'Changelog',
      issues: 'Issues',
      design: 'Design',
      tuiGuide: 'Terminal UI guide',
      windows: 'Windows notes',
      benchmark: 'Benchmark report',
      acp: 'Agent Client Protocol',
      pi: 'The pi project',
    },
    legal: 'Open source under the MIT and Apache-2.0 licenses.',
    built: 'Built with Astro. Hosted on Cloudflare.',
  },
  notFound: {
    title: 'Page not found',
    body: 'This address has no page. 这个地址没有对应的页面。',
    back: 'Back to the home page',
  },
};
