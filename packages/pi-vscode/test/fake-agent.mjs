// A scripted ACP agent for the tests. It is started as `node fake-agent.mjs` and speaks ACP over stdio.
// The prompt text picks what it does. Environment variables change how it starts:
//   FAKE_AGENT_VERSION      version in agentInfo (default 0.8.0)
//   FAKE_AGENT_NAME         name in agentInfo (default pi-agent-cli)
//   FAKE_AUTH_MARKER=<file> session/new fails with -32000 until the file exists (the setup terminal creates it)
//   FAKE_NO_MODE=1          no `mode` config option (the v0.8.0 binary)
//   FAKE_EXIT_AT_START=<n>  exit with this code at once, after a line on stderr
//   FAKE_LOG=<file>         append one line for each request or notification the agent gets
import { existsSync, appendFileSync } from "node:fs";
import { Readable, Writable } from "node:stream";
import * as acp from "@agentclientprotocol/sdk";

const env = process.env;
if (env.FAKE_EXIT_AT_START) {
  process.stderr.write("fake agent: cannot start\nTraceback (most recent call last):\n  boom\n");
  process.exit(Number(env.FAKE_EXIT_AT_START));
}
const record = (line) => env.FAKE_LOG && appendFileSync(env.FAKE_LOG, line + "\n");

let mode = "ask";
const cancelled = new Set();
let sessionCounter = 0;

function configOptions() {
  const options = [
    { id: "model", name: "Model", category: "model", type: "select", currentValue: "fake-model", options: [{ value: "fake-model", name: "Fake model" }] },
  ];
  if (!env.FAKE_NO_MODE) {
    options.push({
      id: "mode", name: "Mode", category: "mode", type: "select", currentValue: mode,
      options: [{ value: "ask", name: "Ask" }, { value: "auto", name: "Auto" }],
    });
  }
  return options;
}

const text = (sessionId, kind, value) => ({ sessionId, update: { sessionUpdate: kind, content: { type: "text", text: value } } });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function ask(ctx, sessionId, toolCall, options) {
  const response = await ctx.client.request(acp.methods.client.session.requestPermission, { sessionId, toolCall, options });
  return response.outcome;
}

const allowReject = [
  { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
  { optionId: "reject-once", name: "Reject", kind: "reject_once" },
];

async function runPrompt(ctx, sessionId, prompt) {
  const notify = (n) => ctx.client.notify(acp.methods.client.session.update, n);
  if (prompt === "hello") {
    await notify(text(sessionId, "agent_thought_chunk", "Thinking. "));
    await notify(text(sessionId, "agent_message_chunk", "Hel"));
    await notify(text(sessionId, "agent_message_chunk", "lo"));
    return "end_turn";
  }
  if (prompt === "tool:bash") {
    await notify({ sessionId, update: { sessionUpdate: "tool_call", toolCallId: "call_b", title: "echo hi", kind: "execute", status: "pending", rawInput: { command: "echo hi" }, content: [{ type: "terminal", terminalId: "call_b" }], _meta: { terminal_info: { terminal_id: "call_b" } } } });
    await notify({ sessionId, update: { sessionUpdate: "tool_call_update", toolCallId: "call_b", status: "in_progress", content: [{ type: "terminal", terminalId: "call_b" }, { type: "content", content: { type: "text", text: "hi\n" } }], _meta: { terminal_output: { terminal_id: "call_b", data: "hi\n" } } } });
    await notify({ sessionId, update: { sessionUpdate: "tool_call_update", toolCallId: "call_b", status: "failed", content: [{ type: "terminal", terminalId: "call_b" }, { type: "content", content: { type: "text", text: "hi\n\n\nCommand exited with code 3" } }], _meta: { terminal_exit: { terminal_id: "call_b", exit_code: 3 }, terminal_output: { terminal_id: "call_b", data: "\n\nCommand exited with code 3" } } } });
    await notify(text(sessionId, "agent_message_chunk", "done"));
    return "end_turn";
  }
  if (prompt === "tool:write") {
    const rawInput = { path: "a.txt", content: "hello\n" };
    await notify({ sessionId, update: { sessionUpdate: "tool_call", toolCallId: "call_w", title: "write a.txt", kind: "edit", status: "pending", rawInput } });
    const outcome = await ask(ctx, sessionId, { toolCallId: "call_w", kind: "edit", status: "pending", title: "write", rawInput }, allowReject);
    const allowed = outcome.outcome === "selected" && outcome.optionId === "allow-once";
    await notify({ sessionId, update: { sessionUpdate: "tool_call_update", toolCallId: "call_w", status: allowed ? "completed" : "failed", content: [{ type: "content", content: { type: "text", text: allowed ? "Successfully wrote 6 bytes to a.txt" : "Permission denied" } }] } });
    await notify(text(sessionId, "agent_message_chunk", allowed ? "written" : "refused"));
    return "end_turn";
  }
  if (prompt === "trust") {
    await notify(text(sessionId, "agent_message_chunk", "This project wants to load resources of its own."));
    const outcome = await ask(
      ctx, sessionId,
      { toolCallId: "trust-abc", kind: "other", status: "pending", title: "loading this project's extensions", rawInput: { project: "/p", resources: ["extensions: x"], changed: false } },
      [
        { optionId: "dont-trust-project", name: "Don't trust", kind: "reject_once" },
        { optionId: "trust-project", name: "Trust and remember", kind: "allow_always" },
      ],
    );
    await notify(text(sessionId, "agent_message_chunk", outcome.outcome === "selected" ? `trust:${outcome.optionId}` : "trust:cancelled"));
    return "end_turn";
  }
  if (prompt === "trust:odd") {
    const outcome = await ask(
      ctx, sessionId,
      { toolCallId: "trust-odd", kind: "other", status: "pending", title: "loading this project's extensions", rawInput: { project: "/p", resources: ["x"], changed: false } },
      [
        { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
        { optionId: "reject-once", name: "Reject", kind: "reject_once" },
      ],
    );
    await notify(text(sessionId, "agent_message_chunk", outcome.outcome === "selected" ? `odd:${outcome.optionId}` : "odd:cancelled"));
    return "end_turn";
  }
  if (prompt === "hang") {
    await notify(text(sessionId, "agent_message_chunk", "working"));
    const pending = ask(ctx, sessionId, { toolCallId: "call_h", kind: "execute", status: "pending", title: "bash", rawInput: { command: "sleep 100" } }, allowReject).catch(() => undefined);
    for (let i = 0; i < 400 && !cancelled.has(sessionId); i++) await sleep(25);
    await pending;
    cancelled.delete(sessionId);
    return "cancelled";
  }
  if (prompt === "crash") {
    await notify(text(sessionId, "agent_message_chunk", "about to crash"));
    process.stderr.write("fake agent: crashing\n");
    process.exit(3);
  }
  if (prompt === "refuse") return "refusal";
  await notify(text(sessionId, "agent_message_chunk", "echo: " + prompt));
  return "end_turn";
}

const app = acp
  .agent({ name: "fake-agent" })
  .onRequest(acp.methods.agent.initialize, (ctx) => {
    record("initialize " + JSON.stringify(ctx.params.clientCapabilities));
    const terminal = ctx.params.clientCapabilities?.auth?.terminal === true;
    return {
      protocolVersion: 1,
      agentInfo: { name: env.FAKE_AGENT_NAME ?? "pi-agent-cli", title: "fake", version: env.FAKE_AGENT_VERSION ?? "0.8.0" },
      agentCapabilities: { loadSession: true, sessionCapabilities: { close: {} } },
      authMethods: terminal && env.FAKE_AUTH_MARKER
        ? [{ id: "pi-setup", name: "Set up", description: "setup", type: "terminal", args: ["--setup"], env: { FAKE_SETUP: "1" } }]
        : [],
    };
  })
  .onRequest(acp.methods.agent.session.new, (ctx) => {
    record("session/new " + ctx.params.cwd);
    if (env.FAKE_AUTH_MARKER && !existsSync(env.FAKE_AUTH_MARKER)) {
      throw new acp.RequestError(-32000, "Authentication required", { authMethods: ["pi-setup"] });
    }
    const sessionId = `fake-${++sessionCounter}`;
    return {
      sessionId,
      configOptions: configOptions(),
      modes: env.FAKE_NO_MODE ? undefined : { currentModeId: mode, availableModes: [{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }] },
    };
  })
  .onRequest(acp.methods.agent.session.setConfigOption, (ctx) => {
    record(`session/set_config_option ${ctx.params.configId}=${ctx.params.value}`);
    if (ctx.params.configId === "mode") mode = String(ctx.params.value);
    return { configOptions: configOptions() };
  })
  .onRequest(acp.methods.agent.session.close, (ctx) => {
    record("session/close " + ctx.params.sessionId);
    return {};
  })
  .onRequest(acp.methods.agent.session.prompt, async (ctx) => {
    const sessionId = ctx.params.sessionId;
    const prompt = ctx.params.prompt.map((block) => (block.type === "text" ? block.text : "")).join("");
    record("session/prompt " + prompt);
    return { stopReason: await runPrompt(ctx, sessionId, prompt) };
  })
  .onNotification(acp.methods.agent.session.cancel, (ctx) => {
    record("session/cancel " + ctx.params.sessionId);
    cancelled.add(ctx.params.sessionId);
  });

const connection = app.connect(acp.ndJsonStream(Writable.toWeb(process.stdout), Readable.toWeb(process.stdin)));
connection.closed.then(() => process.exit(0));
