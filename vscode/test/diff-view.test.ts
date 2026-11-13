import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatController } from "../src/core/chat-controller.js";
import { TestHost, newController, ofType, waitFor } from "./helpers.js";

const hosts: TestHost[] = [];
const controllers: ChatController[] = [];

function setup(options: ConstructorParameters<typeof TestHost>[0] = {}) {
  const host = new TestHost(options);
  hosts.push(host);
  const made = newController(host);
  controllers.push(made.controller);
  return { host, ...made };
}

afterEach(async () => {
  await Promise.all(controllers.splice(0).map((c) => c.dispose()));
  for (const host of hosts.splice(0)) host.cleanup();
});

const text = (before: string, after: string) => ({ before, after });
function texts(controller: ChatController, toolCallId: string) {
  const info = controller.diffFor(toolCallId);
  if (!info) return undefined;
  return { ...text(controller.diffStore.get(info.key, "before")!, controller.diffStore.get(info.key, "after")!), info };
}

function patchOf(before: string, after: string): string {
  const a = before.split("\n");
  const b = after.split("\n");
  if (a.at(-1) === "") a.pop();
  if (b.at(-1) === "") b.pop();
  return `--- f\n+++ f\n@@ -1,${a.length} +1,${b.length} @@\n${a.map((l) => "-" + l).join("\n")}\n${b.map((l) => "+" + l).join("\n")}\n`;
}

describe("diff view: write", () => {
  it("offers a proposed diff on the permission card, then the result diff after the call", async () => {
    const { controller, messages, host } = setup();
    writeFileSync(path.join(host.sessionFolder()!, "a.txt"), "old\n");
    await controller.start();
    const spec = { tool: "write", path: "a.txt", content: "new\nline\n", ask: true };
    const turn = controller.send("file:" + JSON.stringify(spec));
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    const card = ofType(messages, "permission")[0]!;
    expect(card.canDiff).toBe(true);
    expect(texts(controller, "call_f")).toMatchObject({ before: "old\n", after: "new\nline\n", info: { title: "a.txt (proposed)" } });
    controller.answerPermission(card.requestId, "allow-once");
    await turn;
    await waitFor(() => ofType(messages, "toolCall").at(-1)?.toolCall.canDiff === true, "the diff flag");
    expect(texts(controller, "call_f")).toMatchObject({ before: "old\n", after: "new\nline\n", info: { title: "a.txt (written)" } });
    expect(readFileSync(path.join(host.sessionFolder()!, "a.txt"), "utf8")).toBe("new\nline\n");
  });

  it("has an empty before side for a new file", async () => {
    const { controller, messages } = setup();
    await controller.start();
    const turn = controller.send("file:" + JSON.stringify({ tool: "write", path: "new.txt", content: "x\n", ask: true }));
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    expect(texts(controller, "call_f")).toMatchObject({ before: "", after: "x\n" });
    controller.answerPermission(ofType(messages, "permission")[0]!.requestId, "reject-once");
    await turn;
  });

  it("has no diff after a refused call and writes nothing", async () => {
    const { controller, messages, host } = setup();
    await controller.start();
    const turn = controller.send("file:" + JSON.stringify({ tool: "write", path: "no.txt", content: "x\n", ask: true }));
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    controller.answerPermission(ofType(messages, "permission")[0]!.requestId, "reject-once");
    await turn;
    await waitFor(() => ofType(messages, "toolCall").at(-1)?.toolCall.status === "failed", "the failed call");
    await new Promise((r) => setTimeout(r, 50));
    expect(ofType(messages, "toolCall").at(-1)?.toolCall.canDiff).toBeFalsy();
    expect(existsSync(path.join(host.sessionFolder()!, "no.txt"))).toBe(false);
  });

  it("says that the old content is not available when the pre-image read finishes after the call", async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const { controller, messages, host } = setup({
      readFile: async (file) => {
        await gate;
        return existsSync(file) ? readFileSync(file, "utf8") : null;
      },
    });
    writeFileSync(path.join(host.sessionFolder()!, "a.txt"), "old\n");
    await controller.start();
    const turn = controller.send("file:" + JSON.stringify({ tool: "write", path: "a.txt", content: "new\n", ask: false }));
    await waitFor(() => ofType(messages, "toolCall").at(-1)?.toolCall.status === "completed", "the completed call");
    release!();
    await turn;
    await waitFor(() => ofType(messages, "toolCall").at(-1)?.toolCall.canDiff === true, "the diff flag");
    expect(texts(controller, "call_f")).toMatchObject({
      before: "",
      after: "new\n",
      info: { note: "old content not available", title: "a.txt (old content not available)" },
    });
  });

  it("shows no diff when the file cannot be read", async () => {
    const { controller, messages } = setup({ readFile: async () => undefined });
    await controller.start();
    const turn = controller.send("file:" + JSON.stringify({ tool: "write", path: "big.bin", content: "x", ask: true }));
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    const card = ofType(messages, "permission")[0]!;
    expect(card.canDiff).toBe(false);
    expect(card.diffNote).toContain("cannot be read");
    controller.answerPermission(card.requestId, "reject-once");
    await turn;
  });
});

describe("diff view: edit", () => {
  const edits = [{ oldText: "two", newText: "TWO" }];

  it("proposes the replacements, then rebuilds the old file from the patch of the result", async () => {
    const { controller, messages, host } = setup();
    const file = path.join(host.sessionFolder()!, "e.txt");
    writeFileSync(file, "one\ntwo\nthree\n");
    await controller.start();
    const patch = patchOf("one\ntwo\nthree\n", "one\nTWO\nthree\n");
    const turn = controller.send("file:" + JSON.stringify({ tool: "edit", path: "e.txt", edits, ask: true, patch }));
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    expect(ofType(messages, "permission")[0]!.canDiff).toBe(true);
    expect(texts(controller, "call_f")).toMatchObject({ before: "one\ntwo\nthree\n", after: "one\nTWO\nthree\n" });
    controller.answerPermission(ofType(messages, "permission")[0]!.requestId, "allow-once");
    await turn;
    await waitFor(() => ofType(messages, "toolCall").at(-1)?.toolCall.canDiff === true, "the diff flag");
    expect(texts(controller, "call_f")).toMatchObject({
      before: "one\ntwo\nthree\n",
      after: "one\nTWO\nthree\n",
      info: { title: "e.txt (edited)" },
    });
  });

  it("has no proposed diff when an oldText is not unique, and says so", async () => {
    const { controller, messages, host } = setup();
    writeFileSync(path.join(host.sessionFolder()!, "d.txt"), "x\nx\n");
    await controller.start();
    const turn = controller.send(
      "file:" + JSON.stringify({ tool: "edit", path: "d.txt", edits: [{ oldText: "x", newText: "y" }], ask: true }),
    );
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    const card = ofType(messages, "permission")[0]!;
    expect(card.canDiff).toBe(false);
    expect(card.diffNote).toBe("diff not available");
    controller.answerPermission(card.requestId, "reject-once");
    await turn;
  });

  it("falls back to the pre-image against the file on disk when the patch does not fit", async () => {
    const { controller, messages, host } = setup();
    writeFileSync(path.join(host.sessionFolder()!, "e.txt"), "one\ntwo\nthree\n");
    await controller.start();
    const turn = controller.send(
      "file:" + JSON.stringify({ tool: "edit", path: "e.txt", edits, ask: true, patch: "--- f\n+++ f\n@@ -1,1 +1,1 @@\n-zzz\n+yyy\n" }),
    );
    await waitFor(() => ofType(messages, "permission").length === 1, "the card");
    controller.answerPermission(ofType(messages, "permission")[0]!.requestId, "allow-once");
    await turn;
    await waitFor(() => ofType(messages, "toolCall").at(-1)?.toolCall.canDiff === true, "the diff flag");
    expect(texts(controller, "call_f")).toMatchObject({
      before: "one\ntwo\nthree\n",
      after: "one\nTWO\nthree\n",
      info: { note: "the file changed after the call" },
    });
  });
});

describe("diff view: settings", () => {
  it("sends the showThinking setting with the state", async () => {
    const { controller } = setup({ settings: { showThinking: false } });
    expect(controller.stateMessage().showThinking).toBe(false);
  });
});
