// Starts the agent program that is inside a VSIX and speaks ACP to it: initialize, then session/new.
// With an empty PI_HOME the agent has no model, so session/new must fail with the login error that
// the extension turns into "Run setup". It runs the binary of THIS machine: pass a VSIX of the
// platform that you run on (linux-x64 on a Linux x64 runner).
//   node scripts/smoke-vsix.mjs out/zycode-linux-x64-0.1.0.vsix
import { spawn } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable, Writable } from "node:stream";

import * as acp from "@agentclientprotocol/sdk";

import { loadAssets, readZip } from "./lib.mjs";

export async function smokeVsix(file) {
  const assets = loadAssets();
  const entries = readZip(readFileSync(file));
  const programEntry = entries.find((e) => /^extension\/bin\/zycode-agent(\.exe)?$/.test(e.name));
  if (!programEntry) throw new Error("no agent program in the VSIX");
  const dir = mkdtempSync(path.join(tmpdir(), "zycode-smoke-"));
  const program = path.join(dir, path.basename(programEntry.name));
  writeFileSync(program, programEntry.read());
  chmodSync(program, 0o755);
  const home = path.join(dir, "home");
  const project = path.join(dir, "project");
  mkdirSync(home);
  mkdirSync(project);

  const started = Date.now();
  const child = spawn(program, [], {
    cwd: project,
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PI_HOME: home, HOME: home, USERPROFILE: home },
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => (stderr += chunk));
  const exited = new Promise((resolve) => child.once("close", (code, signal) => resolve({ code, signal })));
  const result = {};
  try {
    const connection = acp
      .client({ name: "zycode-smoke" })
      .connect(acp.ndJsonStream(Writable.toWeb(child.stdin), Readable.toWeb(child.stdout)));
    const init = await Promise.race([
      connection.agent.request(acp.methods.agent.initialize, {
        protocolVersion: acp.PROTOCOL_VERSION,
        clientCapabilities: { auth: { terminal: true } },
        clientInfo: { name: "zycode-smoke", version: "0" },
      }),
      exited.then((info) => {
        throw new Error(`the agent exited at start (${JSON.stringify(info)}):\n${stderr.slice(-2000)}`);
      }),
    ]);
    result.startupMs = Date.now() - started;
    result.agent = init.agentInfo;
    if (init.protocolVersion !== 1) throw new Error(`protocolVersion ${init.protocolVersion}`);
    if (init.agentInfo?.name !== "pi-agent-cli") throw new Error(`agent name ${init.agentInfo?.name}`);
    if (init.agentInfo?.version !== assets.version) {
      throw new Error(`agent version ${init.agentInfo?.version}, the pin is ${assets.version}`);
    }
    result.authMethods = init.authMethods;
    try {
      await connection.agent.request(acp.methods.agent.session.new, { cwd: project, mcpServers: [] });
      result.session = "opened";
    } catch (error) {
      if (!(error instanceof acp.RequestError) || error.code !== -32000) throw error;
      result.session = "auth_required";
      result.authData = error.data;
    }
    connection.close();
  } finally {
    child.stdin.end();
    const killer = setTimeout(() => child.kill("SIGKILL"), 10_000);
    result.exit = await exited;
    clearTimeout(killer);
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
      if (result.exit.code !== 0) process.exit(1);
    },
    (error) => {
      console.error(error instanceof Error ? error.message : error);
      process.exit(1);
    },
  );
}
