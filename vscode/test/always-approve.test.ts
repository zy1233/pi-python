import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import type { HostMessage } from "../src/core/protocol.js";
import { TestHost, newController, ofType, readLog, tempDir, waitFor } from "./helpers.js";

const hosts: TestHost[] = [];
const controllers: ChatController[] = [];
const dirs: string[] = [];
let restoreEnv: (() => void) | undefined;

function withEnv(values: Record<string, string>): void {
  const saved: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(values)) {
    saved[key] = process.env[key];
    process.env[key] = value;
  }
  restoreEnv = () => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
}

function setup(options: ConstructorParameters<typeof TestHost>[0] = {}) {
  const host = new TestHost(options);
  hosts.push(host);
  const made = newController(host);
  controllers.push(made.controller);
  return { host, ...made };
}

afterEach(async () => {
  restoreEnv?.();
  restoreEnv = undefined;
  await Promise.all(controllers.splice(0).map((c) => c.dispose()));
  for (const host of hosts.splice(0)) host.cleanup();
  dirs.length = 0;
});

const modeOf = (controller: ChatController) => controller.stateMessage().mode;
const notices = (messages: HostMessage[]) => ofType(messages, "notice").map((n) => n.text);

describe("always approve in the mode menu", () => {
  it("is the third option of the menu in a trusted folder", async () => {
    const { controller } = setup();
    await controller.start();
    expect(modeOf(controller)?.options.map((o) => o.id)).toEqual(["ask", "auto", "always-approve"]);
  });

  it("is not offered in an untrusted workspace, and the agent does not start", async () => {
    const { controller } = setup({ trusted: false });
    await controller.start();
    expect(controller.currentStatus).toBe("untrusted");
    expect(modeOf(controller)).toBeUndefined();
  });

  it("is not offered after the trust is taken back", async () => {
    const { controller, host, messages } = setup({ confirm: true });
    await controller.start();
    host.trusted = false;
    expect(modeOf(controller)?.options.map((o) => o.id)).toEqual(["ask", "auto"]);
    await controller.applyPermissionMode("always-approve");
    expect(host.confirmations).toHaveLength(0);
    expect(modeOf(controller)?.current).toBe("ask");
    expect(notices(messages).some((t) => t.includes("not available"))).toBe(true);
  });

  it("asks first with a modal that says every call runs, bash too, and changes nothing on No", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    withEnv({ FAKE_LOG: log });
    const { controller, host } = setup({ confirm: false });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    expect(host.confirmations).toHaveLength(1);
    expect(host.confirmations[0]?.message).toMatch(/Always approve/);
    expect(host.confirmations[0]?.detail).toMatch(/bash/);
    expect(host.confirmations[0]?.detail).toMatch(/no question/i);
    expect(modeOf(controller)?.current).toBe("ask");
    expect(readLog(log).some((l) => l.startsWith("pi/yolo_mode_changed"))).toBe(false);
  });

  it("turns on after Yes, shows the mode that the agent reports, and tells the user", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    withEnv({ FAKE_LOG: log });
    const { controller, messages } = setup({ confirm: true });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    expect(modeOf(controller)?.current).toBe("always-approve");
    expect(readLog(log)).toContain("pi/yolo_mode_changed always-approve");
    expect(ofType(messages, "notice").some((n) => n.level === "warning" && n.text.includes("every tool call runs"))).toBe(true);
    const last = ofType(messages, "state").at(-1);
    expect(last?.mode?.current).toBe("always-approve");
  });

  it("says so when the agent does not confirm", async () => {
    withEnv({ FAKE_SKIP_YOLO: "1" });
    const { controller, messages } = setup({ confirm: true });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    expect(modeOf(controller)?.current).toBe("ask");
    expect(notices(messages).some((t) => t.includes("did not confirm"))).toBe(true);
  }, 15_000);

  it("goes back to ask from the menu, and puts the start mode of the agent back", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    withEnv({ FAKE_LOG: log, FAKE_ONLY_ASK_AUTO: "1" });
    const { controller } = setup({ confirm: true });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    await controller.applyPermissionMode("ask");
    expect(modeOf(controller)?.current).toBe("ask");
    await waitFor(() => readLog(log).includes("pi/yolo_mode_changed ask"), "the reset of the start mode");
    const lines = readLog(log);
    expect(lines.indexOf("session/set_config_option mode=ask")).toBeLessThan(lines.indexOf("pi/yolo_mode_changed ask"));
  });

  it("never sets always-approve through set_config_option", async () => {
    const log = path.join(tempDir(dirs, "log-"), "agent.log");
    withEnv({ FAKE_LOG: log, FAKE_ONLY_ASK_AUTO: "1" });
    const { controller } = setup({ confirm: true });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    expect(readLog(log).some((l) => l === "session/set_config_option mode=always-approve")).toBe(false);
  });

  it("a new chat starts in ask, not in always-approve", async () => {
    withEnv({ FAKE_ONLY_ASK_AUTO: "1" });
    const { controller } = setup({ confirm: true });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    expect(modeOf(controller)?.current).toBe("always-approve");
    await controller.newChat();
    expect(modeOf(controller)?.current).toBe("ask");
  });

  it("a new chat starts in auto when the setting says auto", async () => {
    withEnv({ FAKE_ONLY_ASK_AUTO: "1" });
    const { controller } = setup({ confirm: true, settings: { permissionMode: "auto" } });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    await controller.newChat();
    expect(modeOf(controller)?.current).toBe("auto");
  });

  it("is not restored after a restart of the agent or of the extension", async () => {
    withEnv({ FAKE_ONLY_ASK_AUTO: "1" });
    const first = setup({ confirm: true });
    await first.controller.start();
    await first.controller.applyPermissionMode("always-approve");
    await first.controller.restart();
    expect(modeOf(first.controller)?.current).toBe("ask");
    await first.controller.applyPermissionMode("always-approve");
    await first.controller.dispose();
    const second = setup({ confirm: true });
    await second.controller.start();
    expect(modeOf(second.controller)?.current).toBe("ask");
  });

  it("a past chat that is opened does not keep always-approve", async () => {
    const store = path.join(tempDir(dirs, "store-"), "sessions.json");
    withEnv({ FAKE_STORE: store, FAKE_ONLY_ASK_AUTO: "1" });
    const { controller } = setup({ confirm: true });
    await controller.start();
    await controller.send("one");
    await waitFor(() => controller.currentStatus === "ready" && controller.sessions.kind === "ready", "list");
    await controller.newChat();
    await controller.applyPermissionMode("always-approve");
    const row = controller.sessions.kind === "ready" ? controller.sessions.rows[0] : undefined;
    await controller.openPastSession(row!.id);
    expect(modeOf(controller)?.current).toBe("ask");
  });

  it("drops always-approve when the user refuses the trust question of the agent", async () => {
    withEnv({ FAKE_ONLY_ASK_AUTO: "1" });
    const { controller, messages } = setup({ confirm: true });
    await controller.start();
    await controller.applyPermissionMode("always-approve");
    const sent = controller.send("trust");
    await waitFor(() => ofType(messages, "permission").length > 0, "the trust question");
    const request = ofType(messages, "permission")[0]!;
    controller.answerPermission(request.requestId, "dont-trust-project");
    await sent;
    expect(modeOf(controller)?.options.map((o) => o.id)).toEqual(["ask", "auto"]);
    expect(modeOf(controller)?.current).toBe("ask");
    expect(notices(messages).some((t) => /trust/i.test(t))).toBe(true);
  });

  it("has no always-approve for an agent without a mode option", async () => {
    withEnv({ FAKE_NO_MODE: "1" });
    const { controller } = setup({ confirm: true });
    await controller.start();
    expect(modeOf(controller)).toBeUndefined();
  });
});
