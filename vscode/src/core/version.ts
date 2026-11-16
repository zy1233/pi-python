import type { AgentSource } from "./locate.js";

export const EXPECTED_AGENT_NAME = "pi-agent-cli";
export const EXPECTED_PROTOCOL_VERSION = 1;

export type VersionVerdict =
  | { kind: "ok"; log?: string }
  | { kind: "warn"; text: string }
  | { kind: "fail"; text: string };

/**
 * What can follow the release numbers of a version that is a final release: more numbers
 * (`1.0.0.1`), a post-release (`.post1`) and a local part (`+local`). A pre-release (`rc1`, `a1`,
 * `.dev0`) and any text that is not a PEP 440 suffix do not match.
 */
const FINAL_SUFFIX = /^(?:\.\d+)*(?:[.\-_]?post[.\-_]?\d*)?(?:\+[0-9a-z.\-_]+)?$/i;

/**
 * Whether `version` is `min` or later. `version` is `agentInfo.version` of the agent: the version of
 * the Python package, written as PEP 440 (`1.0.0`, `1.0.0rc1`, `1.0.1.dev3`, `0.0.0+unknown`). `min`
 * is a plain release (`1.0.0`).
 *
 * The answer is `false` when it is not sure. A version that cannot be read, a pre-release of `min`
 * (it may come from before the change that `min` stands for), and a version with a suffix that is not
 * PEP 440 are all `false`. A pre-release of a later release (`1.0.1rc1`) is `true`.
 */
export function agentVersionAtLeast(version: string | undefined, min: string): boolean {
  const have = parseRelease(version);
  const need = parseRelease(min);
  if (!have || !need) return false;
  for (let i = 0; i < 3; i++) {
    if (have.numbers[i] !== need.numbers[i]) return have.numbers[i]! > need.numbers[i]!;
  }
  return FINAL_SUFFIX.test(have.suffix);
}

/** The first three numbers of a version (a missing one is 0), and the text after them. */
function parseRelease(text: string | undefined): { numbers: [number, number, number]; suffix: string } | undefined {
  const match = /^(\d+)(?:\.(\d+))?(?:\.(\d+))?/.exec(text ?? "");
  if (!match) return undefined;
  return {
    numbers: [Number(match[1]), Number(match[2] ?? 0), Number(match[3] ?? 0)],
    suffix: (text ?? "").slice(match[0].length),
  };
}

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
