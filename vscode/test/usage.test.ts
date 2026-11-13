import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import type { HostMessage } from "../src/core/protocol.js";
import { TestHost, newController, ofType, waitFor } from "./helpers.js";

const hosts: TestHost[] = [];
const controllers: ChatController[] = [];

function setup(options: Parameters<typeof newController>[1] = {}) {
  const host = new TestHost();
  hosts.push(host);
  const made = newController(host, options);
  controllers.push(made.controller);
  return { host, ...made };
}

afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(controllers.splice(0).map((c) => c.dispose()));
  for (const host of hosts.splice(0)) host.cleanup();
});

const usagePrompt = (fields: Record<string, unknown>) => `usage:${JSON.stringify(fields)}`;

/**
 * Starts the turn that waits for a cancel, and waits until the agent has asked its question (it is in the
 * turn then). The promise is in an object: an async function would wait for it, and the turn does not end.
 */
async function startHang(controller: ChatController, messages: HostMessage[]): Promise<{ sent: Promise<boolean> }> {
  const sent = controller.send("hang");
  await waitFor(() => ofType(messages, "permission").length === 1, "the card");
  return { sent };
}

describe("context window from the agent (design section 7d)", () => {
  it("has no usage until the agent sends a usage_update", async () => {
    const { controller, messages } = setup();
    await controller.start();
    await controller.send("hello");
    expect(controller.stateMessage().usage).toBeUndefined();
    expect(ofType(messages, "state").some((m) => m.usage !== undefined)).toBe(false);
  });

  it("keeps used, size and cost of a usage_update, and tells the view", async () => {
    const { controller, messages } = setup();
    await controller.start();
    await controller.send(usagePrompt({ used: 54200, size: 128000, cost: { amount: 0.12, currency: "USD" } }));
    const expected = { used: 54200, size: 128000, cost: { amount: 0.12, currency: "USD" } };
    expect(controller.stateMessage().usage).toEqual(expected);
    expect(ofType(messages, "state").some((m) => m.status === "busy" && m.usage !== undefined)).toBe(true);
    expect(ofType(messages, "state").at(-1)?.usage).toEqual(expected);
  });

  it("replaces the old numbers with the newest ones (after a compaction the count goes down)", async () => {
    const { controller } = setup();
    await controller.start();
    await controller.send(usagePrompt({ used: 100000, size: 128000 }));
    await controller.send(usagePrompt({ used: 20000, size: 128000 }));
    expect(controller.stateMessage().usage).toEqual({ used: 20000, size: 128000 });
  });

  it("keeps the count and leaves out a size that cannot be used", async () => {
    const { controller } = setup();
    await controller.start();
    await controller.send(usagePrompt({ used: 500, size: 0 }));
    expect(controller.stateMessage().usage).toEqual({ used: 500 });
  });

  it("drops an update that fails the ACP schema (the SDK does it): the numbers stay and the turn ends", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined); // the SDK reports the dropped update there
    const { controller } = setup();
    await controller.start();
    await controller.send(usagePrompt({ used: 700, size: 1000 }));
    await controller.send(usagePrompt({ size: 5 })); // no `used`
    await controller.send(usagePrompt({ used: "many", size: 5 })); // `used` is not a number
    expect(controller.currentStatus).toBe("ready");
    expect(controller.stateMessage().usage).toEqual({ used: 700, size: 1000 });
  });

  it("forgets the numbers when the chat is new, and the view is told at once", async () => {
    const { controller, messages } = setup();
    await controller.start();
    await controller.send(usagePrompt({ used: 54200, size: 128000 }));
    messages.length = 0;
    await controller.newChat();
    expect(controller.stateMessage().usage).toBeUndefined();
    expect(ofType(messages, "state").some((m) => m.usage !== undefined)).toBe(false);
    // The first state after the reset has no numbers, so the view does not show the old chat's context.
    const reset = messages.findIndex((m) => m.type === "reset");
    expect(reset).toBeGreaterThanOrEqual(0);
    const next = messages.slice(reset).find((m) => m.type === "state");
    expect(next?.type).toBe("state");
  });

  it("forgets the numbers when the agent restarts and when it stops", async () => {
    const { controller } = setup();
    await controller.start();
    await controller.send(usagePrompt({ used: 1, size: 2 }));
    await controller.restart();
    expect(controller.stateMessage().usage).toBeUndefined();
    await controller.send(usagePrompt({ used: 3, size: 4 }));
    expect(controller.stateMessage().usage).toEqual({ used: 3, size: 4 });
    await controller.stop();
    expect(controller.stateMessage().usage).toBeUndefined();
  });
});

describe("the clock of the turn (design section 7d)", () => {
  it("says how long the turn has run while it runs, and how long it took after it ended", async () => {
    let t = 1_000;
    const { controller, messages } = setup({ now: () => t });
    await controller.start();
    expect(controller.stateMessage().turn).toBeUndefined();

    const { sent } = await startHang(controller, messages);
    expect(controller.currentStatus).toBe("busy");
    // The state that starts the turn says 0 ms.
    expect(ofType(messages, "state").find((m) => m.status === "busy")?.turn).toEqual({ elapsedMs: 0 });
    t = 4_500;
    expect(controller.stateMessage().turn).toEqual({ elapsedMs: 3_500 });

    t = 9_400;
    await controller.cancel();
    expect(controller.stateMessage().turn).toEqual({ elapsedMs: 8_400 }); // the turn runs until it ends
    await sent;
    expect(controller.currentStatus).toBe("ready");
    expect(controller.stateMessage().turn).toEqual({ lastMs: 8_400 });
    expect(ofType(messages, "state").at(-1)?.turn).toEqual({ lastMs: 8_400 });
  });

  it("starts again with the next turn: the time of the last turn goes, and the new one counts from its own start", async () => {
    let t = 0;
    const { controller, messages } = setup({ now: () => t });
    await controller.start();
    t = 100;
    await controller.send("hello");
    expect(controller.stateMessage().turn).toEqual({ lastMs: 0 });
    const { sent } = await startHang(controller, messages);
    t = 600;
    expect(controller.stateMessage().turn).toEqual({ elapsedMs: 500 }); // no `lastMs` while a turn runs
    await controller.cancel();
    await sent;
    expect(controller.stateMessage().turn).toEqual({ lastMs: 500 });
  });

  it("forgets the time of the last turn when the chat is new", async () => {
    const { controller } = setup({ now: () => 0 });
    await controller.start();
    await controller.send("hello");
    expect(controller.stateMessage().turn).toEqual({ lastMs: 0 });
    await controller.newChat();
    expect(controller.stateMessage().turn).toBeUndefined();
  });

  it("never gives a negative time when the clock goes back", async () => {
    let t = 10_000;
    const { controller, messages } = setup({ now: () => t });
    await controller.start();
    const { sent } = await startHang(controller, messages);
    t = 5_000;
    expect(controller.stateMessage().turn).toEqual({ elapsedMs: 0 });
    await controller.cancel();
    await sent;
    expect(controller.stateMessage().turn).toEqual({ lastMs: 0 });
  });
});
