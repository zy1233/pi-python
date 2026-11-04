import { chmod, stat } from "node:fs/promises";
import path from "node:path";

export type AgentSource = "setting" | "bundled";

export interface AgentCommand {
  command: string;
  args: string[];
  source: AgentSource;
}

export type ResolveResult = { ok: true; command: AgentCommand } | { ok: false; reason: string };

export function bundledBinaryName(platform: NodeJS.Platform): string {
  return platform === "win32" ? "zycode-agent.exe" : "zycode-agent";
}

function pathFlavour(platform: NodeJS.Platform): path.PlatformPath {
  return platform === "win32" ? path.win32 : path.posix;
}

/**
 * Which program to start: the setting, then the bundled binary. Nothing else: the extension does
 * not search `PATH` for a program the user did not name.
 */
export function resolveAgentCommand(input: {
  agentPath: string;
  agentArgs: string[];
  extensionPath: string;
  platform: NodeJS.Platform;
  exists: (file: string) => boolean;
}): ResolveResult {
  const flavour = pathFlavour(input.platform);
  const configured = input.agentPath.trim();
  if (configured !== "") {
    const hasFolderPart = configured.includes("/") || configured.includes("\\");
    if (hasFolderPart && !flavour.isAbsolute(configured)) {
      return {
        ok: false,
        reason: `zycode.agentPath is a relative path (${configured}). Use an absolute path, or a program name that is found on PATH.`,
      };
    }
    return { ok: true, command: { command: configured, args: [...input.agentArgs], source: "setting" } };
  }
  const bundled = flavour.join(input.extensionPath, "bin", bundledBinaryName(input.platform));
  if (input.exists(bundled)) {
    return { ok: true, command: { command: bundled, args: [], source: "bundled" } };
  }
  return {
    ok: false,
    reason:
      "This copy of zycode has no bundled agent for this platform. Set zycode.agentPath to a zycode agent program (for a Python install: path python, arguments -m pi_agent_cli).",
  };
}

/**
 * A VSIX installed by VS Code can lose the exec bit, so the bundled binary gets mode 0755 before
 * it starts. An error here is not fatal: the start then shows the real error.
 * Returns the problem text, or `undefined`.
 */
export async function prepareBundledBinary(file: string, platform: NodeJS.Platform): Promise<string | undefined> {
  if (platform === "win32") return undefined;
  try {
    await chmod(file, 0o755);
    return undefined;
  } catch (error) {
    return `Could not set the exec bit on ${file}: ${error instanceof Error ? error.message : String(error)}`;
  }
}

export async function fileExists(file: string): Promise<boolean> {
  try {
    return (await stat(file)).isFile();
  } catch {
    return false;
  }
}
