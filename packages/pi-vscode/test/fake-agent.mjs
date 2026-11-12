// A scripted ACP agent for the tests. It is started as `node fake-agent.mjs` and speaks ACP over stdio.
// The prompt text picks what it does. Environment variables change how it starts:
//   FAKE_AGENT_VERSION      version in agentInfo (default 0.8.0)
//   FAKE_AGENT_NAME         name in agentInfo (default pi-agent-cli)
//   FAKE_AUTH_MARKER=<file> session/new fails with -32000 until the file exists (the setup terminal creates it)
//   FAKE_NO_MODE=1          no `mode` config option (the v0.8.0 binary)
//   FAKE_EXIT_AT_START=<n>  exit with this code at once, after a line on stderr
//   FAKE_LOG=<file>         append one line for each request or notification the agent gets
//   FAKE_AUTH_MESSAGE=<t>   the message of the -32000 error (default: the text of the real agent)
//   FAKE_STORE=<file>       sessions are kept in this file, so a new process lists them (without it: in memory)
//   FAKE_NO_LIST=1          no session/list (the agent does not advertise it)
//   FAKE_NO_DELETE=1        _pi/session/delete answers "method not found"
//   FAKE_PAGE_SIZE=<n>      session/list gives n sessions for each page (default 50)
//   FAKE_ONLY_ASK_AUTO=1    set_config_option refuses a value that is not ask or auto (as the real agent)
//   FAKE_SKIP_YOLO=1        _pi/yolo_mode_changed is ignored (no config_option_update)
//   FAKE_LIST_ERROR=1       session/list fails
//   FAKE_IGNORE_CWD=1       session/list gives the sessions of every folder (an agent that ignores the filter)
//   FAKE_TOML=<file>        like the real agent, read thinking_level from this file once, at the start (not later)
//   FAKE_LOAD_CWD=<dir>     session/load says in _meta["pi/cwd"] that the session belongs to this folder
import { existsSync, appendFileSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { Readable, Writable } from "node:stream";
import * as acp from "@agentclientprotocol/sdk";

const env = process.env;
if (env.FAKE_EXIT_AT_START) {
  process.stderr.write("fake agent: cannot start\nTraceback (most recent call last):\n  boom\n");
  process.exit(Number(env.FAKE_EXIT_AT_START));
}
const raw = { parse: (params) => params };
const record = (line) => env.FAKE_LOG && appendFileSync(env.FAKE_LOG, line + "\n");

let startLevel = "off";
if (env.FAKE_TOML && existsSync(env.FAKE_TOML)) {
  const found = /^thinking_level\s*=\s*["']([a-z]+)["']/m.exec(readFileSync(env.FAKE_TOML, "utf8"));
  if (found) startLevel = found[1];
}
if (env.FAKE_TOML) record("start thinking_level=" + startLevel);

let mode = "ask";
const cancelled = new Set();
const cwds = new Map();

// The sessions that the agent keeps: id -> {id, cwd, title, updatedAt, history: [{role, text}]}
const store = env.FAKE_STORE && existsSync(env.FAKE_STORE) ? new Map(JSON.parse(readFileSync(env.FAKE_STORE, "utf8"))) : new Map();
let sessionCounter = Math.max(0, ...[...store.keys()].map((id) => Number(/-(\d+)$/.exec(id)?.[1] ?? 0)));
let clock = Math.max(Date.now() - 3600_000, ...[...store.values()].map((entry) => entry.updatedAt));
const save = () => env.FAKE_STORE && writeFileSync(env.FAKE_STORE, JSON.stringify([...store.entries()]));
function remember(sessionId, role, value) {
  let entry = store.get(sessionId);
  if (!entry) {
    entry = { id: sessionId, cwd: cwds.get(sessionId), title: role === "user" ? value : "", updatedAt: 0, history: [] };
    store.set(sessionId, entry);
  }
  entry.updatedAt = ++clock + 1000;
  const last = entry.history.at(-1);
  if (role === "agent" && last?.role === "agent") last.text += value;
  else entry.history.push({ role, text: value });
  save();
}

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
const replay = (sessionId, kind, value) => ({ ...text(sessionId, kind, value), update: { ...text(sessionId, kind, value).update, _meta: { isReplay: true } } });
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
  const notify = (n) => {
    const update = n.update;
    if (update.sessionUpdate === "agent_message_chunk") remember(sessionId, "agent", update.content.text);
    return ctx.client.notify(acp.methods.client.session.update, n);
  };
  if (prompt.startsWith("error:")) {
    const texts = {
      key: "Error: Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-fake-123', 'type': 'invalid_request_error'}}",
      model: "Error: Error code: 404 - {'error': {'message': 'The model `nope` does not exist', 'code': 'model_not_found'}}",
      conn: "Error: Connection error.",
    };
    await notify(text(sessionId, "agent_message_chunk", texts[prompt.slice(6)] ?? "Error: boom"));
    return "end_turn";
  }
  if (prompt === "level") {
    await notify(text(sessionId, "agent_message_chunk", "level:" + startLevel));
    return "end_turn";
  }
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
  if (prompt.startsWith("file:")) return runFileTool(ctx, sessionId, JSON.parse(prompt.slice("file:".length)));
  if (prompt === "refuse") return "refusal";
  await notify(text(sessionId, "agent_message_chunk", "echo: " + prompt));
  return "end_turn";
}

// A write or edit that really changes a file under the session folder.
// {tool: "write"|"edit", path, content | edits, ask: bool, patch: string | undefined, late: bool}
async function runFileTool(ctx, sessionId, spec) {
  const notify = (n) => ctx.client.notify(acp.methods.client.session.update, n);
  const file = path.resolve(cwds.get(sessionId), spec.path);
  const rawInput = spec.tool === "write" ? { path: spec.path, content: spec.content } : { path: spec.path, edits: spec.edits };
  await notify({ sessionId, update: { sessionUpdate: "tool_call", toolCallId: "call_f", title: `${spec.tool} ${spec.path}`, kind: "edit", status: "pending", rawInput } });
  let allowed = true;
  if (spec.ask) {
    const outcome = await ask(ctx, sessionId, { toolCallId: "call_f", kind: "edit", status: "pending", title: spec.tool, rawInput }, allowReject);
    allowed = outcome.outcome === "selected" && outcome.optionId === "allow-once";
  }
  if (!allowed) {
    await notify({ sessionId, update: { sessionUpdate: "tool_call_update", toolCallId: "call_f", status: "failed", content: [{ type: "content", content: { type: "text", text: "Permission denied" } }] } });
    return "end_turn";
  }
  if (spec.late) await sleep(spec.late);
  const old = existsSync(file) ? readFileSync(file, "utf8") : "";
  let next = spec.content;
  if (spec.tool === "edit") {
    next = old;
    for (const edit of spec.edits) next = next.replace(edit.oldText, edit.newText);
  }
  writeFileSync(file, next);
  const update = { sessionUpdate: "tool_call_update", toolCallId: "call_f", status: "completed", content: [{ type: "content", content: { type: "text", text: "done" } }] };
  if (spec.tool === "edit") update.rawOutput = { content: [], details: spec.patch === undefined ? {} : { diff: spec.patch, patch: spec.patch } };
  await notify({ sessionId, update });
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
      agentCapabilities: { loadSession: true, sessionCapabilities: { close: {}, ...(env.FAKE_NO_LIST ? {} : { list: {} }) } },
      authMethods: terminal && env.FAKE_AUTH_MARKER
        ? [{ id: "pi-setup", name: "Set up", description: "setup", type: "terminal", args: ["--setup"], env: { FAKE_SETUP: "1" } }]
        : [],
    };
  })
  .onRequest(acp.methods.agent.session.new, (ctx) => {
    record("session/new " + ctx.params.cwd);
    if (env.FAKE_AUTH_MARKER && !existsSync(env.FAKE_AUTH_MARKER)) {
      throw new acp.RequestError(
        -32000,
        env.FAKE_AUTH_MESSAGE ?? "No model is set. Choose the setup login of this agent to set it up.",
        { authMethods: ["pi-setup"] },
      );
    }
    const sessionId = `fake-${++sessionCounter}`;
    cwds.set(sessionId, ctx.params.cwd);
    return {
      sessionId,
      configOptions: configOptions(),
      modes: env.FAKE_NO_MODE ? undefined : { currentModeId: mode, availableModes: [{ id: "ask", name: "Ask" }, { id: "auto", name: "Auto" }] },
    };
  })
  .onRequest(acp.methods.agent.session.setConfigOption, (ctx) => {
    record(`session/set_config_option ${ctx.params.configId}=${ctx.params.value}`);
    if (ctx.params.configId === "mode") {
      const value = String(ctx.params.value);
      if (env.FAKE_ONLY_ASK_AUTO && value !== "ask" && value !== "auto") throw new acp.RequestError(-32602, `invalid mode ${value}`);
      mode = value;
    }
    return { configOptions: configOptions() };
  })
  .onRequest(acp.methods.agent.session.list, (ctx) => {
    record(`session/list ${ctx.params.cwd ?? ""} ${ctx.params.cursor ?? ""}`);
    if (env.FAKE_LIST_ERROR) throw new acp.RequestError(-32603, "cannot read the session folder");
    const size = Number(env.FAKE_PAGE_SIZE ?? 50);
    const all = [...store.values()]
      .filter((entry) => env.FAKE_IGNORE_CWD || ctx.params.cwd === undefined || ctx.params.cwd === null || entry.cwd === ctx.params.cwd)
      .sort((a, b) => b.updatedAt - a.updatedAt);
    const from = Number(ctx.params.cursor ?? 0);
    const page = all.slice(from, from + size);
    return {
      sessions: page.map((entry) => ({ sessionId: entry.id, cwd: entry.cwd, title: entry.title, updatedAt: new Date(entry.updatedAt).toISOString() })),
      nextCursor: from + size < all.length ? String(from + size) : null,
    };
  })
  .onRequest(acp.methods.agent.session.load, async (ctx) => {
    record(`session/load ${ctx.params.sessionId} ${ctx.params.cwd}`);
    const entry = store.get(ctx.params.sessionId);
    if (!entry) throw new acp.RequestError(-32602, "unknown session");
    cwds.set(entry.id, ctx.params.cwd);
    for (const item of entry.history) {
      await ctx.client.notify(acp.methods.client.session.update, replay(entry.id, item.role === "user" ? "user_message_chunk" : "agent_message_chunk", item.text));
    }
    return { configOptions: configOptions(), _meta: { "pi/cwd": env.FAKE_LOAD_CWD ?? entry.cwd } };
  })
  .onRequest("_pi/session/delete", raw, (ctx) => {
    record(`pi/session/delete ${ctx.params.sessionId}`);
    if (env.FAKE_NO_DELETE) throw new acp.RequestError(-32601, "Method not found");
    const deleted = store.delete(ctx.params.sessionId);
    save();
    return { sessionId: ctx.params.sessionId, deleted };
  })
  .onNotification("_pi/yolo_mode_changed", raw, async (ctx) => {
    record(`pi/yolo_mode_changed ${ctx.params.permission_mode}`);
    if (env.FAKE_SKIP_YOLO) return;
    mode = String(ctx.params.permission_mode);
    for (const sessionId of cwds.keys()) {
      await ctx.client.notify(acp.methods.client.session.update, { sessionId, update: { sessionUpdate: "config_option_update", configOptions: configOptions() } });
    }
  })
  .onRequest(acp.methods.agent.session.close, (ctx) => {
    record("session/close " + ctx.params.sessionId);
    return {};
  })
  .onRequest(acp.methods.agent.session.prompt, async (ctx) => {
    const sessionId = ctx.params.sessionId;
    const prompt = ctx.params.prompt.map((block) => (block.type === "text" ? block.text : "")).join("");
    record("session/prompt " + prompt);
    remember(sessionId, "user", prompt);
    return { stopReason: await runPrompt(ctx, sessionId, prompt) };
  })
  .onNotification(acp.methods.agent.session.cancel, (ctx) => {
    record("session/cancel " + ctx.params.sessionId);
    cancelled.add(ctx.params.sessionId);
  });

const connection = app.connect(acp.ndJsonStream(Writable.toWeb(process.stdout), Readable.toWeb(process.stdin)));
connection.closed.then(() => process.exit(0));
