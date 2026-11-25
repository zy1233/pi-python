import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

type Item = { id?: string; setting?: string; preset?: string; label: string; description?: string; detail?: string };

const fake = vi.hoisted(() => ({
  values: {} as Record<string, unknown>,
  updates: [] as { key: string; value: unknown; target: unknown }[],
  quickPicks: [] as { items: unknown[]; options: unknown }[],
  quickPickAnswers: [] as ((items: unknown[]) => unknown)[],
  openDialog: undefined as { fsPath: string }[] | undefined,
  infoMessages: [] as { message: string; buttons: string[] }[],
  infoAnswer: undefined as string | undefined,
  executed: [] as unknown[][],
  getSession: vi.fn(),
}));

vi.mock("vscode", () => ({
  ConfigurationTarget: { Global: 1 },
  authentication: { getSession: (...args: unknown[]) => fake.getSession(...args) },
  workspace: {
    getConfiguration: () => ({
      get: (key: string, fallback?: unknown) => (key in fake.values ? fake.values[key] : fallback),
      update: async (key: string, value: unknown, target: unknown) => {
        fake.updates.push({ key, value, target });
        fake.values[key] = value;
      },
    }),
  },
  window: {
    showQuickPick: async (items: unknown[], options: unknown) => {
      fake.quickPicks.push({ items, options });
      const answer = fake.quickPickAnswers.shift();
      return answer ? answer(items) : undefined;
    },
    showOpenDialog: async () => fake.openDialog,
    showInformationMessage: async (message: string, ...buttons: string[]) => {
      fake.infoMessages.push({ message, buttons });
      return fake.infoAnswer;
    },
  },
  commands: {
    executeCommand: async (...args: unknown[]) => {
      fake.executed.push(args);
    },
  },
}));

import { createAvatarCommands } from "../src/ui/avatar-commands.js";

const session = { accessToken: "t", account: { id: "1" } };

function setup(options: { storageDir?: string; model?: [string | undefined, string | undefined] } = {}) {
  const avatars = { clearCustomCache: vi.fn(), clearAccountCache: vi.fn() };
  const refresh = vi.fn();
  const notify = vi.fn();
  const commands = createAvatarCommands({
    avatars,
    refresh,
    notify,
    storageDir: options.storageDir,
    activeModel: () => options.model ?? [undefined, undefined],
  });
  return { commands, avatars, refresh, notify };
}

/** A Quick Pick answer: the item that has this `id`, `setting` or `preset`. */
const pick = (name: string) => (items: unknown[]) =>
  (items as Item[]).find((item) => item.id === name || item.setting === name || item.preset === name);

afterEach(() => {
  fake.values = {};
  fake.updates = [];
  fake.quickPicks = [];
  fake.quickPickAnswers = [];
  fake.openDialog = undefined;
  fake.infoMessages = [];
  fake.infoAnswer = undefined;
  fake.executed = [];
  fake.getSession.mockReset();
});

describe("chooseAccountAvatar", () => {
  it.each([
    ["github", ["read:user"]],
    ["microsoft", ["User.Read"]],
  ])("saves %s only after VS Code allowed the account", async (provider, scopes) => {
    fake.getSession.mockResolvedValue(session);
    fake.quickPickAnswers.push(pick(provider));
    const { commands, avatars, refresh, notify } = setup();

    await commands.chooseAccountAvatar();

    expect(fake.getSession).toHaveBeenCalledTimes(1);
    expect(fake.getSession).toHaveBeenCalledWith(provider, scopes, { createIfNone: true });
    expect(fake.updates).toEqual([{ key: "accountAvatar", value: provider, target: 1 }]);
    expect(avatars.clearAccountCache).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(notify).not.toHaveBeenCalled();
  });

  it.each([
    ["the user says no", () => fake.getSession.mockResolvedValue(undefined)],
    ["VS Code rejects the call", () => fake.getSession.mockRejectedValue(new Error("User did not consent to login."))],
  ])("changes nothing when %s", async (_name, arrange) => {
    arrange();
    fake.values = { userAvatar: "/home/me/me.png" };
    fake.quickPickAnswers.push(pick("github"));
    const { commands, avatars, refresh, notify } = setup();

    await commands.chooseAccountAvatar();

    expect(fake.updates).toEqual([]);
    expect(avatars.clearAccountCache).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith("info", expect.stringContaining("did not allow"));
  });

  it("turns the account picture off without asking VS Code", async () => {
    fake.values = { accountAvatar: "github" };
    fake.quickPickAnswers.push(pick("off"));
    const { commands, avatars } = setup();

    await commands.chooseAccountAvatar();

    expect(fake.getSession).not.toHaveBeenCalled();
    expect(fake.updates).toEqual([{ key: "accountAvatar", value: "off", target: 1 }]);
    expect(avatars.clearAccountCache).toHaveBeenCalledTimes(1);
  });

  it("does nothing when the user closes the list", async () => {
    const { commands, refresh } = setup();
    await commands.chooseAccountAvatar();
    expect(fake.getSession).not.toHaveBeenCalled();
    expect(fake.updates).toEqual([]);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("removes an image of the user's own, because that image would win over the account picture", async () => {
    fake.getSession.mockResolvedValue(session);
    fake.values = { userAvatar: "/home/me/me.png" };
    fake.quickPickAnswers.push(pick("github"));
    const { commands } = setup();

    await commands.chooseAccountAvatar();

    expect(fake.updates).toEqual([
      { key: "userAvatar", value: "", target: 1 },
      { key: "accountAvatar", value: "github", target: 1 },
    ]);
  });

  it("says which server gets the token before the user chooses, and marks the current choice", async () => {
    fake.values = { accountAvatar: "microsoft" };
    const { commands } = setup();

    await commands.chooseAccountAvatar();

    const items = fake.quickPicks[0]?.items as Item[];
    expect(items.map((item) => item.setting)).toEqual(["github", "microsoft", "off"]);
    expect(items[0]?.detail).toContain("api.github.com");
    expect(items[1]?.detail).toContain("graph.microsoft.com");
    expect(items[1]?.description).toBe("current");
    expect(items[0]?.description).toBeUndefined();
    expect(items[2]?.detail).toContain("no request");
    expect(fake.getSession).not.toHaveBeenCalled();
  });
});

describe("pickAvatar", () => {
  it("offers the account picture in the menu of the user avatar, and shows the choice that is on", async () => {
    const { commands } = setup();

    await commands.pickAvatar("user");
    const plain = fake.quickPicks[0]?.items as Item[];
    expect(plain.map((item) => item.id)).toEqual(["choose", "account", "settings"]);
    expect(plain[1]?.label).toContain("Use account picture");

    fake.values = { accountAvatar: "github", userAvatar: "/home/me/me.png" };
    await commands.pickAvatar("user");
    const chosen = fake.quickPicks[1]?.items as Item[];
    expect(chosen.map((item) => item.id)).toEqual(["choose", "account", "reset", "settings"]);
    expect(chosen[1]?.label).toContain("GitHub");
    expect(chosen[2]?.description).toContain("GitHub");
  });

  it("opens the account question from the menu of the user avatar", async () => {
    fake.getSession.mockResolvedValue(session);
    fake.quickPickAnswers.push(pick("account"), pick("microsoft"));
    const { commands } = setup();

    await commands.pickAvatar("user");

    expect(fake.quickPicks).toHaveLength(2);
    expect(fake.getSession).toHaveBeenCalledWith("microsoft", ["User.Read"], { createIfNone: true });
    expect(fake.updates).toEqual([{ key: "accountAvatar", value: "microsoft", target: 1 }]);
  });

  it("resets the image of the user and opens the setting from the menu", async () => {
    fake.values = { userAvatar: "/home/me/me.png" };
    fake.quickPickAnswers.push(pick("reset"), pick("settings"));
    const { commands, refresh } = setup();

    await commands.pickAvatar("user");
    expect(fake.updates).toEqual([{ key: "userAvatar", value: "", target: 1 }]);
    expect(refresh).toHaveBeenCalledTimes(1);

    await commands.pickAvatar("user");
    expect(fake.executed).toEqual([["workbench.action.openSettings", "zycode.userAvatar"]]);
  });

  it("turns the auto-switch of the zycode avatar on and off from its menu", async () => {
    fake.values = { autoModelAvatar: true };
    fake.quickPickAnswers.push(pick("auto"));
    const { commands } = setup();

    await commands.pickAvatar("assistant");

    expect(fake.updates).toEqual([{ key: "autoModelAvatar", value: false, target: 1 }]);
  });
});

describe("chooseAvatar", () => {
  it("saves a copy of the chosen image and writes the path of the copy to the setting", async () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "zycode-avatar-cmd-"));
    try {
      const source = path.join(tmp, "me.png");
      writeFileSync(source, Buffer.from([137, 80, 78, 71, 1]));
      fake.openDialog = [{ fsPath: source }];
      const storageDir = path.join(tmp, "storage");
      const { commands, refresh } = setup({ storageDir });

      await commands.chooseAvatar("user");

      expect(fake.updates).toHaveLength(1);
      expect(fake.updates[0]).toMatchObject({ key: "userAvatar", target: 1 });
      expect(String(fake.updates[0]?.value)).toContain(path.join(storageDir, "avatars", "user-"));
      expect(refresh).toHaveBeenCalledTimes(1);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("warns, and changes no setting, when the copy cannot be saved", async () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "zycode-avatar-cmd-"));
    try {
      const source = path.join(tmp, "me.png");
      writeFileSync(source, Buffer.from([137, 80, 78, 71, 1]));
      // A storage folder below a plain file cannot be made, on every platform.
      const blocker = path.join(tmp, "blocker");
      writeFileSync(blocker, "a file, not a folder");
      fake.openDialog = [{ fsPath: source }];
      const { commands, notify, refresh } = setup({ storageDir: path.join(blocker, "storage") });

      await commands.chooseAvatar("user");

      expect(fake.updates).toEqual([]);
      expect(refresh).not.toHaveBeenCalled();
      expect(notify).toHaveBeenCalledWith("warning", expect.stringContaining("Could not save a copy"));
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("does nothing when the user closes the file dialog", async () => {
    const { commands, notify } = setup();
    await commands.chooseAvatar("assistant");
    expect(fake.updates).toEqual([]);
    expect(notify).not.toHaveBeenCalled();
  });
});

describe("chooseAssistantPreset", () => {
  it("clears an image of the user's own and offers to turn the auto-switch off", async () => {
    fake.values = { assistantAvatar: "/home/me/bot.png", autoModelAvatar: true };
    fake.quickPickAnswers.push(pick("gemini"));
    fake.infoAnswer = "Turn off auto-switch";
    const { commands } = setup({ model: ["gemini-2.5-pro", undefined] });

    await commands.chooseAssistantPreset();

    await vi.waitFor(() =>
      expect(fake.updates.map((update) => update.key)).toEqual(["assistantAvatarPreset", "assistantAvatar", "autoModelAvatar"]),
    );
    expect(fake.updates[0]).toEqual({ key: "assistantAvatarPreset", value: "gemini", target: 1 });
    expect(fake.updates[2]).toEqual({ key: "autoModelAvatar", value: false, target: 1 });
    expect(fake.infoMessages[0]?.buttons).toEqual(["Turn off auto-switch"]);
  });

  it("does not offer it when the auto-switch is off or the model has no mascot", async () => {
    fake.quickPickAnswers.push(pick("kimi"), pick("kimi"));
    fake.values = { autoModelAvatar: false };
    await setup({ model: ["gemini-2.5-pro", undefined] }).commands.chooseAssistantPreset();
    fake.values = { autoModelAvatar: true };
    await setup({ model: ["my-local-model", undefined] }).commands.chooseAssistantPreset();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fake.infoMessages).toEqual([]);
  });
});
