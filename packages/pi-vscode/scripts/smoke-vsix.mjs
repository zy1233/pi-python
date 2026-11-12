// Starts the agent program that is inside a VSIX and speaks ACP to it. It runs the binary of THIS
// machine: pass a VSIX of the platform that you run on (linux-x64 on a Linux x64 runner).
// 1. An empty PI_HOME: the agent has no model, so session/new must fail with the login error that
//    the extension turns into "Run setup".
// 2. A home whose agent.toml says `permission = "always-approve"` and names a model on a closed
//    local port: session/new opens (the model is not called), the session offers the `mode` config
//    option (`ask`, `auto`), and a client can move it from `always-approve` to `ask`. This is what
//    the extension does for its sessions.
//   node scripts/smoke-vsix.mjs out/zycode-linux-x64-0.1.0.vsix
import { spawn } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable, Writable } from "node:stream";

import * as acp from "@agentclientprotocol/sdk";

import { loadAssets, readZip } from "./lib.mjs";

function startAgent(program, home, project) {
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
    .connect(acp.ndJsonStream(Writable.toWeb(child.stdin), Readable.toWeb(child.stdout)));
  return { child, connection, exited, stderr: () => stderr };
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

function modeOption(response) {
  return (response.configOptions ?? []).find((option) => option.id === "mode");
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
      if (result.exit.code !== 0 || result.exit2.code !== 0) process.exit(1);
    },
    (error) => {
      console.error(error instanceof Error ? error.message : error);
      process.exit(1);
    },
  );
}
