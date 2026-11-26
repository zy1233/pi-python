import { afterEach, describe, expect, it } from "vitest";

import { parsePiCapabilities } from "../src/core/acp-connection.js";
import { parseWebviewMessage } from "../src/core/protocol.js";
import { TestHost, newController, ofType, waitFor } from "./helpers.js";

function withEnv(vars: Record<string, string | undefined>): () => void {
  const saved: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(vars)) {
    saved[key] = process.env[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  return () => {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
}

let restoreEnv: (() => void) | undefined;
const hosts: TestHost[] = [];
const stops: (() => Promise<void>)[] = [];

afterEach(async () => {
  while (stops.length > 0) await stops.pop()!();
  while (hosts.length > 0) hosts.pop()!.cleanup();
  restoreEnv?.();
  restoreEnv = undefined;
});

function startRig(env: Record<string, string | undefined> = {}) {
  restoreEnv = withEnv(env);
  const host = new TestHost();
  hosts.push(host);
  const { controller, messages } = newController(host);
  stops.push(() => controller.stop());
  return { host, controller, messages };
}

describe("steer and btw protocol helpers", () => {
  it("parses pi/capabilities from initialize _meta", () => {
    const asInit = (meta?: Record<string, unknown>) =>
      ({ protocolVersion: 1, ...(meta ? { _meta: meta } : {}) }) as Parameters<typeof parsePiCapabilities>[0];
    expect(parsePiCapabilities(undefined)).toEqual({ steer: false, btw: false });
    expect(parsePiCapabilities(asInit({}))).toEqual({ steer: false, btw: false });
    expect(parsePiCapabilities(asInit({ "pi/capabilities": { steer: true } }))).toEqual({ steer: true, btw: false });
    expect(parsePiCapabilities(asInit({ "pi/capabilities": { steer: true, btw: true } }))).toEqual({
      steer: true,
      btw: true,
    });
    expect(parsePiCapabilities(asInit({ "pi/capabilities": "bad" }))).toEqual({ steer: false, btw: false });
  });

  it("validates btw webview messages", () => {
    expect(parseWebviewMessage({ type: "btwAsk", text: "what is X?" })).toEqual({ type: "btwAsk", text: "what is X?" });
    expect(parseWebviewMessage({ type: "btwAsk", text: "   " })).toBeUndefined();
    expect(parseWebviewMessage({ type: "btwCancel" })).toBeUndefined();
    expect(parseWebviewMessage({ type: "btwCancel", btwId: "btw-1" })).toEqual({ type: "btwCancel", btwId: "btw-1" });
    expect(parseWebviewMessage({ type: "btwCancel", btwId: 123 })).toBeUndefined();
    expect(parseWebviewMessage({ type: "btwClose" })).toEqual({ type: "btwClose" });
  });
});

describe("ChatController steer and /btw", () => {
  it("queues a mid-turn steer, records steered user message on injection, and completes the turn", async () => {
    const { controller, messages } = startRig({ FAKE_STEER_BTW: "1" });
    await controller.start();

    expect(controller.stateMessage().supportsSteer).toBe(true);
    expect(controller.stateMessage().supportsBtw).toBe(true);

    const turn = controller.send("steer-wait");
    await waitFor(
      () => ofType(messages, "agentText").some((m) => m.text.includes("working")),
      "initial agent working chunk",
    );
    expect(controller.stateMessage().status).toBe("busy");

    await controller.send("use pytest");
    await turn;

    expect(controller.stateMessage().status).toBe("ready");
    expect(controller.stateMessage().pendingSteers).toBeUndefined();

    const steeredUsers = controller.transcript().filter((i) => i.type === "user" && i.steered === true);
    expect(steeredUsers).toHaveLength(1);
    expect(steeredUsers[0]).toMatchObject({ type: "user", text: "use pytest", steered: true });

    const agentText = controller
      .transcript()
      .filter((i) => i.type === "agent")
      .map((i) => i.text)
      .join("");
    expect(agentText).toContain("steered: use pytest");
  });

  it("restores unconsumed queued steer to draft when turn is cancelled", async () => {
    const { controller, messages } = startRig({ FAKE_STEER_BTW: "1" });
    await controller.start();

    const turn = controller.send("steer-hang");
    await waitFor(
      () => ofType(messages, "agentText").some((m) => m.text.includes("working")),
      "steer-hang working chunk",
    );

    await controller.send("keep migrations untouched");
    await waitFor(
      () => (controller.stateMessage().pendingSteers?.length ?? 0) === 1,
      "pending steer visible in state",
    );

    await controller.cancel();
    await turn;

    const restored = ofType(messages, "restoreDraft");
    expect(restored).toHaveLength(1);
    expect(restored[0]!.text).toBe("keep migrations untouched");
    expect(controller.stateMessage().pendingSteers).toBeUndefined();
  });

  it("streams /btw side chat while busy without mutating main transcript, supports follow-ups and independent cancel", async () => {
    const { controller, messages } = startRig({ FAKE_STEER_BTW: "1" });
    await controller.start();

    const turn = controller.send("steer-hang");
    await waitFor(
      () => ofType(messages, "agentText").some((m) => m.text.includes("working")),
      "main turn working",
    );
    const transcriptBefore = JSON.stringify(controller.transcript());

    await controller.send("/btw what does foo do?");
    await waitFor(
      () => controller.btwState()?.turns.at(0)?.status === "done",
      "first btw turn completed",
    );
    expect(controller.btwState()?.turns[0]).toMatchObject({
      question: "what does foo do?",
      answer: "btw(0): what does foo do?",
      status: "done",
    });

    await controller.askBtw("and bar?");
    await waitFor(
      () => controller.btwState()?.turns.at(1)?.status === "done",
      "second btw turn completed with history",
    );
    expect(controller.btwState()?.turns[1]).toMatchObject({
      question: "and bar?",
      answer: "btw(2): and bar?",
      status: "done",
    });

    // Independent cancel of an active btw turn does not cancel the main turn.
    const hangingBtw = controller.askBtw("hang-btw");
    await waitFor(
      () => controller.btwState()?.turns.at(2)?.answer === "partial...",
      "hanging btw partial chunk",
    );
    const hangBtwId = controller.btwState()!.turns[2]!.id;
    await controller.cancelBtw(hangBtwId);
    await hangingBtw;

    expect(controller.btwState()?.turns[2]?.status).toBe("cancelled");
    expect(controller.stateMessage().status).toBe("busy");

    // Main transcript must remain completely untouched by /btw.
    expect(JSON.stringify(controller.transcript())).toBe(transcriptBefore);

    controller.closeBtw();
    expect(controller.btwState()).toBeNull();

    await controller.cancel();
    await turn;
  });

  it("degrades gracefully when an older agent does not advertise steer or btw capabilities", async () => {
    const { controller, messages } = startRig({ FAKE_STEER_BTW: undefined });
    await controller.start();

    expect(controller.stateMessage().supportsSteer).toBe(false);
    expect(controller.stateMessage().supportsBtw).toBe(false);

    await controller.send("/btw hello?");
    const notices = ofType(messages, "notice");
    expect(notices.some((n) => n.text.includes("does not support /btw"))).toBe(true);
    expect(controller.btwState()).toBeNull();
  });
});
