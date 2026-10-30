import type { Level, PackageId } from '~/i18n/types';

/** The GitHub repository. Every link to the source starts here. */
export const repo = 'https://github.com/zy1233/pi-python';

/** Links that do not change with the language. */
export const links = {
  repo,
  /** The PyPI page of the main package. The install command on the page uses this package. */
  pypi: 'https://pypi.org/project/pi-agent-cli-lc/',
  releases: `${repo}/releases`,
  issues: `${repo}/issues`,
  changelog: `${repo}/blob/main/CHANGELOG.md`,
  design: `${repo}/blob/main/docs/DESIGN.md`,
  tuiGuide: `${repo}/blob/main/docs/TUI-AND-CODE-AGENT.md`,
  windows: `${repo}/blob/main/docs/WINDOWS.md`,
  benchmark: `${repo}/blob/main/docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md`,
  acp: 'https://agentclientprotocol.com',
  pi: 'https://github.com/earendil-works/pi',
  grokBuild: 'https://github.com/xai-org/grok-build',
} as const;

export interface PackageInfo {
  id: PackageId;
  /** The name on PyPI, or the name of the binary. */
  name: string;
  level: Level;
  source: 'pypi' | 'releases';
  href: string;
}

/**
 * The components and their status. Keep this list equal to the table "Status" in
 * README.md. The text for each row is in the message files, under status.descriptions.
 */
export const packages: PackageInfo[] = [
  { id: 'core', name: 'pi-agent-core-lc', level: 'stable', source: 'pypi', href: 'https://pypi.org/project/pi-agent-core-lc/' },
  { id: 'harness', name: 'pi-agent-harness-lc', level: 'stable', source: 'pypi', href: 'https://pypi.org/project/pi-agent-harness-lc/' },
  { id: 'cli', name: 'pi-agent-cli-lc', level: 'beta', source: 'pypi', href: 'https://pypi.org/project/pi-agent-cli-lc/' },
  { id: 'zypi', name: 'zypi', level: 'beta', source: 'releases', href: links.releases },
  { id: 'webAccess', name: 'pi-web-access-py', level: 'beta', source: 'pypi', href: 'https://pypi.org/project/pi-web-access-py/' },
  { id: 'goalX', name: 'pi-goal-x-py', level: 'beta', source: 'pypi', href: 'https://pypi.org/project/pi-goal-x-py/' },
  { id: 'workflows', name: 'pi-dynamic-workflows-py', level: 'beta', source: 'pypi', href: 'https://pypi.org/project/pi-dynamic-workflows-py/' },
  { id: 'mcp', name: 'pi-mcp-py', level: 'alpha', source: 'pypi', href: 'https://pypi.org/project/pi-mcp-py/' },
];

/** Code that is the same in every language. */
export const snippets = {
  zed: `{
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
}`,
  python: `import asyncio
from pi_agent_core import Agent, Model
from pi_agent_core.adapters import langchain_stream


async def main():
    agent = Agent(
        initial_state={
            "system_prompt": "You are a helpful assistant.",
            "model": Model(provider="openai", model_id="gpt-4o-mini"),
        },
        stream_fn=langchain_stream,
    )
    agent.subscribe(lambda event, signal: print(event.type))
    await agent.prompt("Hello!")
    await agent.wait_for_idle()


asyncio.run(main())`,
} as const;
