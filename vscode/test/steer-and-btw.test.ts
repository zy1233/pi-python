import { afterEach, describe, expect, it } from "vitest";

import { parsePiCapabilities } from "../src/core/acp-connection.js";
import { buildTrimmedBtwHistory } from "../src/core/chat-controller.js";
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

  it("automatically falls back to a normal prompt when steer returns idle while controller is still busy (#4)", async () => {
    const { controller, messages } = startRig({ FAKE_STEER_BTW: "1" });
    await controller.start();

    const turn = controller.send("steer-idle-race");
    await waitFor(
      () => ofType(messages, "agentText").some((m) => m.text.includes("finishing")),
      "steer-idle-race finishing chunk",
    );
    expect(controller.stateMessage().status).toBe("busy");

    // Agent already transitioned to idle before prompt() returned; controller queues fallback and sends after turnEnd.
    const ok = await controller.send("follow up automatically");
    expect(ok).toBe(true);
    await turn;

    await waitFor(
      () =>
        controller
          .transcript()
          .some((item) => item.type === "agent" && item.text.includes("echo: follow up automatically")),
      "automatic fallback prompt ran and echoed",
    );
    expect(ofType(messages, "restoreDraft")).toHaveLength(0);
  });

  it("sends every idle-fallback steer, in order, as one prompt after the turn ends (S3)", async () => {
    const { controller, messages } = startRig({ FAKE_STEER_BTW: "1" });
    await controller.start();

    const turn = controller.send("steer-idle-race");
    await waitFor(
      () => ofType(messages, "agentText").some((m) => m.text.includes("finishing")),
      "steer-idle-race finishing chunk",
    );

    expect(await controller.send("first fallback")).toBe(true);
    expect(await controller.send("second fallback")).toBe(true);
    await turn;

    await waitFor(
      () =>
        controller
          .transcript()
          .some((item) => item.type === "agent" && item.text.includes("echo: first fallback\n\nsecond fallback")),
      "both fallbacks ran in one prompt",
    );
    expect(ofType(messages, "restoreDraft")).toHaveLength(0);
    expect(controller.stateMessage().pendingSteers).toBeUndefined();
  });

  it("disables steer capability and restores draft when agent returns method-not-found (-32601)", async () => {
    const { controller, messages } = startRig({
      FAKE_STEER_BTW: "1",
      FAKE_STEER_METHOD_NOT_FOUND: "1",
    });
    await controller.start();
    expect(controller.stateMessage().supportsSteer).toBe(true);

    const turn = controller.send("steer-hang");
    await waitFor(
      () => ofType(messages, "agentText").some((m) => m.text.includes("working")),
      "steer-hang working",
    );

    const ok = await controller.send("my unsent steer");
    expect(ok).toBe(false);
    expect(controller.stateMessage().supportsSteer).toBe(false);
    expect(ofType(messages, "restoreDraft").at(-1)?.text).toBe("my unsent steer");
    expect(ofType(messages, "notice").some((n) => n.text.includes("does not support mid-turn steering"))).toBe(true);

    await controller.cancel();
    await turn;
  });

  it("restores unconsumed pendingSteers to draft when a turn ends without cancellation (#6)", async () => {
    const { controller, messages } = startRig({ FAKE_STEER_BTW: "1" });
    await controller.start();

    const turn = controller.send("steer-unconsumed-end");
    await waitFor(
      () => ofType(messages, "agentText").some((m) => m.text.includes("working")),
      "steer-unconsumed-end working",
    );

    await controller.send("leftover steer text");
    await turn;

    expect(controller.stateMessage().status).toBe("ready");
    expect(controller.stateMessage().pendingSteers).toBeUndefined();
    const restored = ofType(messages, "restoreDraft");
    expect(restored).toHaveLength(1);
    expect(restored[0]!.text).toBe("leftover steer text");
  });

  it("trims /btw follow-up history to 32 KiB UTF-8 bytes so long multi-turn threads keep working (#5)", async () => {
    // Unit check on buildTrimmedBtwHistory with multi-byte UTF-8 characters.
    const trimmed = buildTrimmedBtwHistory([
      { id: "1", question: "q1", answer: "你".repeat(8000), thought: "", status: "done" }, // 24 KB
      { id: "2", question: "q2", answer: "好".repeat(8000), thought: "", status: "done" }, // 24 KB
    ]);
    expect(trimmed).toHaveLength(2);
    expect(trimmed[0]!.text).toBe("q2");

    // End-to-end check against fake-agent enforcing 32 KiB server limit.
    const { controller } = startRig({ FAKE_STEER_BTW: "1" });
    await controller.start();

    await controller.askBtw("big-btw"); // 20 KB answer
    await controller.askBtw("big-btw"); // another 20 KB answer -> 40 KB total if untrimmed!
    await controller.askBtw("third question");

    const turns = controller.btwState()?.turns ?? [];
    expect(turns).toHaveLength(3);
    expect(turns[2]!.status).toBe("done");
    // Only the newest 20 KB turn (2 entries) fits under 32 KiB.
    expect(turns[2]!.answer).toBe("btw(2): third question");
  });

  it("cancels in-flight /btw request when closing the /btw panel during streaming", async () => {
    const { controller } = startRig({ FAKE_STEER_BTW: "1" });
    await controller.start();

    const hanging = controller.askBtw("hang-btw");
    await waitFor(
      () => controller.btwState()?.turns.at(0)?.answer === "partial...",
      "hanging btw started streaming",
    );

    await controller.closeBtw();
    await hanging;

    expect(controller.btwState()).toBeNull();
  });

  it("blocks slash commands from being sent as plain steer while busy (9A)", async () => {
    const { controller, messages } = startRig({
      FAKE_STEER_BTW: "1",
      FAKE_COMMANDS: JSON.stringify([{ name: "auto-log", description: "Show Auto decisions" }]),
    });
    await controller.start();
    await waitFor(
      () => (controller.stateMessage().commands?.length ?? 0) > 0,
      "agent commands advertised",
    );

    const turn = controller.send("steer-hang");
    await waitFor(
      () => ofType(messages, "agentText").some((m) => m.text.includes("working")),
      "steer-hang working",
    );

    // Client command `/model` and advertised agent command `/auto-log` must NOT be sent as steer.
    expect(await controller.send("/model gpt-4o")).toBe(false);
    expect(await controller.send("/auto-log")).toBe(false);
    expect(controller.stateMessage().pendingSteers).toBeUndefined();
    expect(ofType(messages, "restoreDraft").map((m) => m.text)).toEqual(["/model gpt-4o", "/auto-log"]);
    expect(ofType(messages, "notice").some((n) => n.text.includes("Slash commands cannot be sent as a mid-turn steer"))).toBe(
      true,
    );

    // Normal paths like `/usr/bin/python` are NOT slash commands and still queue as steer.
    expect(await controller.send("/usr/bin/python check this")).toBe(true);
    expect(controller.stateMessage().pendingSteers).toHaveLength(1);

    await controller.cancel();
    await turn;
  });
});
