import type { AgentSource } from "./locate.js";

export const EXPECTED_AGENT_NAME = "pi-agent-cli";
export const EXPECTED_PROTOCOL_VERSION = 1;

export type VersionVerdict =
  | { kind: "ok"; log?: string }
  | { kind: "warn"; text: string }
  | { kind: "fail"; text: string };

/**
 * Checks what `initialize` returned (docs/specs/2026-11-04-vscode-extension-design.md, section 3.5).
 * A program that does not speak the protocol fails. A bundled binary of another version warns.
 * A program the user chose only gets a log line.
 */
export function checkAgentIdentity(input: {
  protocolVersion: number;
  agentName: string | undefined;
  agentVersion: string | undefined;
  source: AgentSource;
  expectedVersion: string;
}): VersionVerdict {
  if (input.protocolVersion !== EXPECTED_PROTOCOL_VERSION || input.agentName !== EXPECTED_AGENT_NAME) {
    return { kind: "fail", text: "This program does not speak the zycode agent protocol." };
  }
  const version = input.agentVersion ?? "unknown";
  if (input.source === "bundled") {
    if (version !== input.expectedVersion) {
      return {
        kind: "warn",
        text: `The bundled agent reports version ${version}, the extension expects ${input.expectedVersion}.`,
      };
    }
    return { kind: "ok" };
  }
  return { kind: "ok", log: `The agent from zycode.agentPath reports version ${version}.` };
}
