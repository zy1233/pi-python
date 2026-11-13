/**
 * The first-run guide (docs/specs/2026-11-04-vscode-extension-design.md, section 7). It adds no wire
 * item and no agent change: it reads what the agent already says, and it uses `--setup`.
 *
 * What the agent says, measured on the 0.9.0 binary with an empty home (packages/pi-agent-cli/AGENTS.md,
 * "First run"):
 * - no `agent.toml`: `session/new` answers `auth_required` (-32000), "No model is set. ...";
 * - a `[model]` whose key variable has no value: `auth_required`, "The variable NAME has no value. ...";
 * - a key that the provider rejects, a wrong model id, a wrong `base_url`: the session opens, and the
 *   turn answers an agent message `Error: <text of the provider>` (`events.py`, `project_event`).
 */

import os from "node:os";
import path from "node:path";

import type { NoticeAction, SetupInfo } from "./protocol.js";

/** What `session/new` said when the agent has no model or key. */
export function classifyAuthRequired(message: string): SetupInfo {
  if (/no model is set/i.test(message)) return { reason: "no-model" };
  const variable = /variable\s+(\S+)\s+has no value/i.exec(message)?.[1];
  if (variable !== undefined) return { reason: "no-key", variable };
  return { reason: "unknown" };
}

export function setupText(info: SetupInfo | undefined): string {
  switch (info?.reason) {
    case "no-model":
      return "zycode has no model yet. Run the setup: it asks for a provider, a model and an API key.";
    case "no-key":
      return `The model is set, but its key is missing: the variable ${info.variable ?? "of the key"} has no value. Run the setup to save the key.`;
    default:
      return "zycode needs a model and an API key. Run the setup in a terminal.";
  }
}

export type TurnFailure = "key" | "model" | "connection";

const KEY = /error code:\s*(401|403)\b|incorrect api key|invalid[ _]api[ _]key|missing credentials|authentication|unauthori[sz]ed|permission denied for this key/i;
const MODEL = /error code:\s*404\b|model\b[^\n]{0,80}(not found|does not exist|not exist)|no such model|unknown model|unsupported model/i;
const CONNECTION = /connection error|timed? ?out|econn(refused|reset)|enotfound|getaddrinfo|name or service not known|certificate verify|ssl:/i;

/**
 * The kind of failure that an agent message names, or `undefined`. The agent writes a failed model
 * call as one message `Error: <text>`. Only the text from the last `Error: ` on is read, so the
 * words of a normal answer do not count.
 */
export function classifyTurnFailure(agentText: string): TurnFailure | undefined {
  const at = agentText.lastIndexOf("Error: ");
  if (at < 0) return undefined;
  const text = agentText.slice(at, at + 1500);
  if (KEY.test(text)) return "key";
  if (MODEL.test(text)) return "model";
  if (CONNECTION.test(text)) return "connection";
  return undefined;
}

export function failureNotice(kind: TurnFailure): { text: string; actions: NoticeAction[] } {
  switch (kind) {
    case "key":
      return {
        text:
          "The model provider did not accept the API key. Run the setup again and replace the key. " +
          "Type the key only in the setup terminal, never in this chat.",
        actions: [
          { id: "runSetup", label: "Change the key" },
          { id: "openGuide", label: "Open the guide" },
        ],
      };
    case "model":
      return {
        text: "The provider does not know this model. Check the model id and the base URL in agent.toml, then restart the agent.",
        actions: [
          { id: "openAgentConfig", label: "Open agent.toml" },
          { id: "openGuide", label: "Open the guide" },
        ],
      };
    case "connection":
      return {
        text: "zycode could not reach the model server. Check the base URL in agent.toml and your network, then try again.",
        actions: [
          { id: "openAgentConfig", label: "Open agent.toml" },
          { id: "openLog", label: "Show log" },
        ],
      };
  }
}

/** `agent.toml` of the agent: `PI_HOME` if set, else `~/.pi-python` (`pi_agent_core.home.pi_home`). */
export function agentConfigPath(env: NodeJS.ProcessEnv = process.env, homeDir: string = os.homedir()): string {
  const raw = env.PI_HOME;
  const home = raw !== undefined && raw !== "" ? expandHome(raw, homeDir) : path.join(homeDir, ".pi-python");
  return path.join(home, "agent.toml");
}

function expandHome(value: string, homeDir: string): string {
  if (value === "~") return homeDir;
  if (value.startsWith("~/") || value.startsWith("~\\")) return path.join(homeDir, value.slice(2));
  return value;
}
