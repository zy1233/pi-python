// Starts the agent program that is inside a VSIX and speaks ACP to it. It runs the binary of THIS
// machine: pass a VSIX of the platform that you run on (linux-x64 on a Linux x64 runner).
// 1. An empty PI_HOME: the agent has no model, so session/new must fail with the login error that
//    the extension turns into "Run setup".
// 2. A home whose agent.toml says `permission = "always-approve"` and names a model on a closed
//    local port: session/new opens (the model is not called), the session offers the `mode` config
//    option (`ask`, `auto`), and a client can move it from `always-approve` to `ask`. This is what
//    the extension does for its sessions.
// 3. A home with a model on a fake OpenAI-format server on 127.0.0.1: one turn, then the session
//    list (`session/list` of the folder, and of another folder), `session/load` from a second agent
//    process with the replay of the history, `_pi/yolo_mode_changed` (the mode must follow, and a
//    new session must not start in it after the extension puts `ask` back), and `_pi/session/delete`.
//    This is what the session list and the mode menu of the extension use.
//   node scripts/smoke-vsix.mjs out/zycode-linux-x64-*.vsix
import { spawn } from "node:child_process";
import http from "node:http";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable, Writable } from "node:stream";

import * as acp from "@agentclientprotocol/sdk";

import { loadAssets, readZip } from "./lib.mjs";

function startAgent(program, home, project) {
  const updates = [];
  const child = spawn(program, [], {
    cwd: project,
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PI_HOME: home, HOME: home, USERPROFILE: home, SMOKE_API_KEY: "not-a-key" },
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => (stderr += chunk));
  const exited = new Promise((resolve) => child.once("close", (code, signal) => resolve({ code, signal })));
  const connection = acp
    .client({ name: "zycode-smoke" })
    .onNotification(acp.methods.client.session.update, (context) => {
      updates.push(context.params);
    })
    .connect(acp.ndJsonStream(Writable.toWeb(child.stdin), Readable.toWeb(child.stdout)));
  return { child, connection, exited, updates, stderr: () => stderr };
}

async function stopAgent(agent) {
  agent.child.stdin.end();
  const killer = setTimeout(() => agent.child.kill("SIGKILL"), 10_000);
  const exit = await agent.exited;
  clearTimeout(killer);
  return exit;
}

async function initialize(agent, assets) {
  const started = Date.now();
  const init = await Promise.race([
    agent.connection.agent.request(acp.methods.agent.initialize, {
      protocolVersion: acp.PROTOCOL_VERSION,
      clientCapabilities: { auth: { terminal: true } },
      clientInfo: { name: "zycode-smoke", version: "0" },
    }),
    agent.exited.then((info) => {
      throw new Error(`the agent exited at start (${JSON.stringify(info)}):\n${agent.stderr().slice(-2000)}`);
    }),
  ]);
  if (init.protocolVersion !== 1) throw new Error(`protocolVersion ${init.protocolVersion}`);
  if (init.agentInfo?.name !== "pi-agent-cli") throw new Error(`agent name ${init.agentInfo?.name}`);
  if (init.agentInfo?.version !== assets.version) {
    throw new Error(`agent version ${init.agentInfo?.version}, the pin is ${assets.version}`);
  }
  return { init, startupMs: Date.now() - started };
}

const raw = (value) => `data: ${JSON.stringify(value)}\n\n`;

/** An OpenAI-format server that answers every request with the text SMOKE-ANSWER. */
async function startFakeModel() {
  const server = http.createServer((req, res) => {
    req.resume();
    req.on("end", () => {
      const base = { id: "chatcmpl-smoke", object: "chat.completion.chunk", created: 0, model: "smoke-model" };
      res.writeHead(200, { "Content-Type": "text/event-stream", Connection: "close" });
      res.write(raw({ ...base, choices: [{ index: 0, delta: { role: "assistant", content: "SMOKE-ANSWER" }, finish_reason: null }] }));
      res.write(raw({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] }));
      res.write(raw({ ...base, choices: [], usage: { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5 } }));
      res.end("data: [DONE]\n\n");
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { port: server.address().port, close: () => server.close() };
}

async function until(check, what, ms = 15_000) {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

function modeOption(response) {
  return (response.configOptions ?? []).find((option) => option.id === "mode");
}

const textOf = (update) => update?.content?.text ?? "";

async function smokeSessions(program, dir, project, assets) {
  const model = await startFakeModel();
  const home = path.join(dir, "sessions-home");
  mkdirSync(home);
  writeFileSync(
    path.join(home, "agent.toml"),
    `[model]\nprovider = "openai"\nid = "smoke-model"\nbase_url = "http://127.0.0.1:${model.port}/v1"\napi_key_env = "SMOKE_API_KEY"\n`,
  );
  const out = {};
  try {
    const first = startAgent(program, home, project);
    let firstId;
    try {
      const { init } = await initialize(first, assets);
      const caps = init.agentCapabilities ?? {};
      out.capabilities = { loadSession: caps.loadSession, list: caps.sessionCapabilities?.list !== undefined };
      if (!caps.loadSession || caps.sessionCapabilities?.list === undefined) throw new Error("the agent has no session/load or session/list");
      const opened = await first.connection.agent.request(acp.methods.agent.session.new, { cwd: project, mcpServers: [] });
      firstId = opened.sessionId;
      await first.connection.agent.request(acp.methods.agent.session.prompt, {
        sessionId: firstId,
        prompt: [{ type: "text", text: "hello smoke" }],
      });
      await until(() => first.updates.some((u) => u.update.sessionUpdate === "agent_message_chunk" && textOf(u.update).includes("SMOKE-ANSWER")), "the answer");

      const listed = await first.connection.agent.request(acp.methods.agent.session.list, { cwd: project });
      const mine = listed.sessions.find((x) => x.sessionId === firstId);
      if (!mine) throw new Error(`session/list of the folder does not show the chat: ${JSON.stringify(listed)}`);
      if (mine.title !== "hello smoke" || mine.cwd !== project) throw new Error(`row ${JSON.stringify(mine)}`);
      out.row = { title: mine.title, hasTime: typeof mine.updatedAt === "string" };
      const other = await first.connection.agent.request(acp.methods.agent.session.list, { cwd: path.join(dir, "another-folder") });
      if (other.sessions.length !== 0) throw new Error(`a chat of this folder shows for another folder: ${JSON.stringify(other)}`);

      // The mode menu: always-approve only by the agent's own signal, and ask again before a new chat.
      await first.connection.agent.notify("_pi/yolo_mode_changed", { permission_mode: "always-approve" });
      await until(() => first.updates.some((u) => u.update.sessionUpdate === "config_option_update" && JSON.stringify(u.update).includes('"always-approve"')), "the mode update");
      const back = await first.connection.agent.request(acp.methods.agent.session.setConfigOption, { sessionId: firstId, configId: "mode", value: "ask" });
      await first.connection.agent.notify("_pi/yolo_mode_changed", { permission_mode: "ask" });
      const fresh = await first.connection.agent.request(acp.methods.agent.session.new, { cwd: project, mcpServers: [] });
      out.mode = { afterSet: modeOption(back)?.currentValue, newSession: modeOption(fresh)?.currentValue };
      if (out.mode.afterSet !== "ask" || out.mode.newSession !== "ask") throw new Error(`mode ${JSON.stringify(out.mode)}`);
      await first.connection.agent.request(acp.methods.agent.session.close, { sessionId: fresh.sessionId });
      first.connection.close();
    } finally {
      out.exit = await stopAgent(first);
    }

    const second = startAgent(program, home, project);
    try {
      await initialize(second, assets);
      const listed = await second.connection.agent.request(acp.methods.agent.session.list, { cwd: project });
      if (!listed.sessions.some((x) => x.sessionId === firstId)) throw new Error("after a restart the chat is not in the list");
      const loaded = await second.connection.agent.request(acp.methods.agent.session.load, { sessionId: firstId, cwd: project, mcpServers: [] });
      const replay = second.updates.filter((u) => u.update._meta?.isReplay === true);
      const user = replay.find((u) => u.update.sessionUpdate === "user_message_chunk");
      const agent = replay.find((u) => u.update.sessionUpdate === "agent_message_chunk");
      if (textOf(user?.update) !== "hello smoke" || !textOf(agent?.update).includes("SMOKE-ANSWER")) {
        throw new Error(`the replay is ${JSON.stringify(replay.map((u) => u.update))}`);
      }
      if (loaded._meta?.["pi/cwd"] !== project) throw new Error(`pi/cwd is ${JSON.stringify(loaded._meta)}`);
      out.replay = { user: textOf(user.update), agent: textOf(agent.update), cwd: loaded._meta["pi/cwd"] };
      await second.connection.agent.request("_pi/session/delete", { sessionId: firstId });
      const after = await second.connection.agent.request(acp.methods.agent.session.list, { cwd: project });
      if (after.sessions.some((x) => x.sessionId === firstId)) throw new Error("the chat is still in the list after the delete");
      out.deleted = true;
      second.connection.close();
    } finally {
      out.exit2 = await stopAgent(second);
    }
  } finally {
    model.close();
  }
  return out;
}

export async function smokeVsix(file) {
  const assets = loadAssets();
  const entries = readZip(readFileSync(file));
  const programEntry = entries.find((e) => /^extension\/bin\/zycode-agent(\.exe)?$/.test(e.name));
  if (!programEntry) throw new Error("no agent program in the VSIX");
  const dir = mkdtempSync(path.join(tmpdir(), "zycode-smoke-"));
  const program = path.join(dir, path.basename(programEntry.name));
  writeFileSync(program, programEntry.read());
  chmodSync(program, 0o755);
  const project = path.join(dir, "project");
  mkdirSync(project);
  const result = {};
  try {
    const emptyHome = path.join(dir, "empty-home");
    mkdirSync(emptyHome);
    const first = startAgent(program, emptyHome, project);
    try {
      const { init, startupMs } = await initialize(first, assets);
      result.startupMs = startupMs;
      result.agent = init.agentInfo;
      result.authMethods = init.authMethods;
      try {
        await first.connection.agent.request(acp.methods.agent.session.new, { cwd: project, mcpServers: [] });
        result.session = "opened";
      } catch (error) {
        if (!(error instanceof acp.RequestError) || error.code !== -32000) throw error;
        result.session = "auth_required";
        result.authData = error.data;
      }
      first.connection.close();
    } finally {
      result.exit = await stopAgent(first);
    }
    if (result.session !== "auth_required") throw new Error(`empty home: session was ${result.session}`);

    const configuredHome = path.join(dir, "configured-home");
    mkdirSync(configuredHome);
    writeFileSync(
      path.join(configuredHome, "agent.toml"),
      'permission = "always-approve"\n\n[model]\nprovider = "openai"\nid = "smoke-model"\n' +
        'base_url = "http://127.0.0.1:9/v1"\napi_key_env = "SMOKE_API_KEY"\n',
    );
    const second = startAgent(program, configuredHome, project);
    try {
      await initialize(second, assets);
      const opened = await second.connection.agent.request(acp.methods.agent.session.new, {
        cwd: project,
        mcpServers: [],
      });
      const option = modeOption(opened);
      if (!option) throw new Error("configured home: the session offers no `mode` option");
      const values = (option.options ?? []).flatMap((o) => (o.options ? o.options.map((x) => x.value) : [o.value]));
      result.mode = { before: option.currentValue, values };
      if (option.currentValue !== "always-approve") throw new Error(`mode starts as ${option.currentValue}`);
      if (!values.includes("ask") || !values.includes("auto")) {
        throw new Error(`mode values ${JSON.stringify(values)}`);
      }
      const changed = await second.connection.agent.request(acp.methods.agent.session.setConfigOption, {
        sessionId: opened.sessionId,
        configId: "mode",
        value: "ask",
      });
      result.mode.after = modeOption(changed)?.currentValue;
      if (result.mode.after !== "ask") throw new Error(`mode after set is ${result.mode.after}`);
      second.connection.close();
    } finally {
      result.exit2 = await stopAgent(second);
    }

    result.sessions = await smokeSessions(program, dir, project, assets);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  return result;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const file = process.argv[2];
  if (!file) {
    console.error("usage: node scripts/smoke-vsix.mjs <file.vsix>");
    process.exit(2);
  }
  smokeVsix(file).then(
    (result) => {
      console.log(JSON.stringify(result, null, 2));
      if ([result.exit, result.exit2, result.sessions?.exit, result.sessions?.exit2].some((e) => e?.code !== 0)) process.exit(1);
    },
    (error) => {
      console.error(error instanceof Error ? error.message : error);
      process.exit(1);
    },
  );
}
