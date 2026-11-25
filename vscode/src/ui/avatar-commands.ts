import * as vscode from "vscode";

import { AVATAR_PRESETS, matchModelAvatarPreset, normalizeAvatarPreset } from "../core/avatar-presets.js";
import {
  ACCOUNT_AVATAR_SETTING_KEY,
  AVATAR_IMAGE_EXTENSIONS,
  AVATAR_SETTING_KEY,
  cleanupStoredAvatars,
  importLocalAvatarFile,
  normalizeAccountAvatar,
  requestAccountSession,
  type AccountAvatarProvider,
  type AccountAvatarSetting,
  type AvatarProvider,
  type AvatarRole,
} from "./avatars.js";

/** What the avatar commands need from the extension. */
export interface AvatarCommandDeps {
  /** The caches that a change of a setting makes stale. */
  avatars: Pick<AvatarProvider, "clearCustomCache" | "clearAccountCache">;
  /** Posts the avatars to the chat view again. */
  refresh(): void;
  notify(level: "info" | "warning", text: string): void;
  /** `globalStorageUri` of the extension: the folder where imported images live. */
  storageDir: string | undefined;
  /** The id and the name of the model of the chat that is open. */
  activeModel(): [string | undefined, string | undefined];
}

export interface AvatarCommands {
  chooseAvatar(role: AvatarRole): Promise<void>;
  chooseAssistantPreset(): Promise<void>;
  /** Asks which account picture to show for the user, and asks VS Code to allow it. This is the only way to turn the account picture on. */
  chooseAccountAvatar(): Promise<void>;
  resetAvatar(role: AvatarRole): Promise<void>;
  /** The menu of the avatar that the user clicked in the chat. */
  pickAvatar(role: AvatarRole): Promise<void>;
}

const ACCOUNT_LABELS: Record<AccountAvatarProvider, string> = {
  github: "GitHub",
  microsoft: "Microsoft",
};

export function createAvatarCommands(deps: AvatarCommandDeps): AvatarCommands {
  const { avatars, storageDir } = deps;
  const settings = () => vscode.workspace.getConfiguration("zycode");
  const refresh = (): void => {
    avatars.clearCustomCache();
    deps.refresh();
  };

  const promptDisableAutoModelIfActive = async (): Promise<void> => {
    const config = settings();
    if (config.get<boolean>("autoModelAvatar", true) && matchModelAvatarPreset(...deps.activeModel()) !== undefined) {
      const choice = await vscode.window.showInformationMessage(
        "zycode: Auto-switch avatar by model is on, so the active model's mascot is currently shown.",
        "Turn off auto-switch",
      );
      if (choice === "Turn off auto-switch") {
        await config.update("autoModelAvatar", false, vscode.ConfigurationTarget.Global);
        refresh();
      }
    }
  };

  const chooseAvatar = async (role: AvatarRole): Promise<void> => {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: false,
      openLabel: role === "user" ? "Select user avatar" : "Select zycode avatar",
      filters: { Images: [...AVATAR_IMAGE_EXTENSIONS] },
    });
    const uri = picked?.[0];
    if (!uri) return;
    const imported = await importLocalAvatarFile(uri.fsPath, role, storageDir);
    if (!imported.ok) {
      deps.notify("warning", `zycode: ${imported.reason}`);
      return;
    }
    await settings().update(AVATAR_SETTING_KEY[role], imported.path, vscode.ConfigurationTarget.Global);
    refresh();
    if (role === "assistant") {
      void promptDisableAutoModelIfActive();
    }
  };

  const chooseAssistantPreset = async (): Promise<void> => {
    const config = settings();
    const currentPreset = normalizeAvatarPreset(config.get<string>("assistantAvatarPreset", "claude"));
    const items = AVATAR_PRESETS.map((preset) => ({
      preset: preset.id,
      label: preset.label,
      description: preset.id === currentPreset ? `${preset.description} (current)` : preset.description,
    }));
    const picked = await vscode.window.showQuickPick(items, {
      placeHolder: "Choose a built-in zycode mascot avatar",
    });
    if (!picked) return;
    await config.update("assistantAvatarPreset", picked.preset, vscode.ConfigurationTarget.Global);
    if (config.get<string>("assistantAvatar", "").trim() !== "") {
      await config.update("assistantAvatar", "", vscode.ConfigurationTarget.Global);
      await cleanupStoredAvatars("assistant", storageDir);
    }
    refresh();
    void promptDisableAutoModelIfActive();
  };

  const chooseAccountAvatar = async (): Promise<void> => {
    const config = settings();
    const current = normalizeAccountAvatar(config.get<string>(ACCOUNT_AVATAR_SETTING_KEY, "off"));
    const items: { setting: AccountAvatarSetting; label: string; description?: string; detail: string }[] = [
      {
        setting: "github",
        label: ACCOUNT_LABELS.github,
        description: current === "github" ? "current" : undefined,
        detail:
          "VS Code asks you to sign in, or to allow zycode to use the account. Then zycode sends your GitHub sign-in token to api.github.com, and reads the picture of your account.",
      },
      {
        setting: "microsoft",
        label: ACCOUNT_LABELS.microsoft,
        description: current === "microsoft" ? "current" : undefined,
        detail:
          "VS Code asks you to sign in, or to allow zycode to use the account. Then zycode sends your Microsoft sign-in token to graph.microsoft.com, and reads the photo of your account.",
      },
      {
        setting: "off",
        label: "Off",
        description: current === "off" ? "current" : undefined,
        detail: "Show the default icon. zycode makes no request for an account picture.",
      },
    ];
    const picked = await vscode.window.showQuickPick(items, {
      placeHolder: "Show the picture of which account as your avatar?",
    });
    if (!picked) return;

    if (picked.setting !== "off") {
      // The setting is saved only after VS Code has the consent of the user: a refused or closed
      // question leaves everything as it was.
      const allowed = await requestAccountSession(picked.setting);
      if (!allowed) {
        deps.notify(
          "info",
          `zycode: VS Code did not allow zycode to use a ${picked.label} account, so the avatar did not change.`,
        );
        return;
      }
      // An image of the user's own wins over the account picture, so it must go for the choice to show.
      if (config.get<string>(AVATAR_SETTING_KEY.user, "").trim() !== "") {
        await config.update(AVATAR_SETTING_KEY.user, "", vscode.ConfigurationTarget.Global);
        await cleanupStoredAvatars("user", storageDir);
      }
    }
    await config.update(ACCOUNT_AVATAR_SETTING_KEY, picked.setting, vscode.ConfigurationTarget.Global);
    avatars.clearAccountCache();
    refresh();
  };

  const resetAvatar = async (role: AvatarRole): Promise<void> => {
    await settings().update(AVATAR_SETTING_KEY[role], "", vscode.ConfigurationTarget.Global);
    await cleanupStoredAvatars(role, storageDir);
    refresh();
  };

  const pickAssistantAvatar = async (): Promise<void> => {
    const config = settings();
    const current = config.get<string>(AVATAR_SETTING_KEY.assistant, "").trim();
    const autoModel = config.get<boolean>("autoModelAvatar", true);
    const items: { id: "auto" | "preset" | "choose" | "reset" | "settings"; label: string; description: string }[] = [
      {
        id: "auto",
        label: `$(sparkle) Auto-switch by model: ${autoModel ? "On" : "Off"}`,
        description: autoModel ? "Click to turn off and always use your chosen avatar" : "Click to turn on automatic model mascot switching",
      },
      {
        id: "preset",
        label: "$(smiley) Choose built-in mascot…",
        description: "Claude, Gemini, DeepSeek, OpenAI, Grok, Kimi, Qwen",
      },
      {
        id: "choose",
        label: "$(file-media) Choose local image…",
        description: "Import an image file from your computer",
      },
    ];
    if (current !== "") {
      items.push({
        id: "reset",
        label: "$(discard) Reset custom image",
        description: "Use the selected built-in mascot",
      });
    }
    items.push({
      id: "settings",
      label: "$(gear) Open avatar settings",
      description: "Edit auto-switch, built-in mascot, or custom image in Settings",
    });
    const choice = await vscode.window.showQuickPick(items, {
      placeHolder: "Change zycode chat avatar",
    });
    if (!choice) return;
    if (choice.id === "auto") {
      await config.update("autoModelAvatar", !autoModel, vscode.ConfigurationTarget.Global);
      refresh();
    } else if (choice.id === "preset") {
      await chooseAssistantPreset();
    } else if (choice.id === "choose") {
      await chooseAvatar("assistant");
    } else if (choice.id === "reset") {
      await resetAvatar("assistant");
    } else {
      await vscode.commands.executeCommand("workbench.action.openSettings", "zycode.assistantAvatar");
    }
  };

  const pickUserAvatar = async (): Promise<void> => {
    const config = settings();
    const current = config.get<string>(AVATAR_SETTING_KEY.user, "").trim();
    const account = normalizeAccountAvatar(config.get<string>(ACCOUNT_AVATAR_SETTING_KEY, "off"));
    const items: { id: "choose" | "account" | "reset" | "settings"; label: string; description: string }[] = [
      { id: "choose", label: "$(file-media) Choose local image…", description: "Import an image file from your computer" },
      account === "off"
        ? {
            id: "account",
            label: "$(account) Use account picture…",
            description: "GitHub or Microsoft. VS Code asks you first",
          }
        : {
            id: "account",
            label: `$(account) Account picture: ${ACCOUNT_LABELS[account]}`,
            description: "Change it, or turn it off",
          },
    ];
    if (current !== "") {
      items.push({
        id: "reset",
        label: "$(discard) Reset to default",
        description: account === "off" ? "Use the default icon" : `Use the picture of your ${ACCOUNT_LABELS[account]} account`,
      });
    }
    items.push({
      id: "settings",
      label: "$(gear) Open avatar setting",
      description: "Edit file path, HTTPS URL, or data URI in Settings",
    });
    const choice = await vscode.window.showQuickPick(items, {
      placeHolder: "Change your chat avatar (You)",
    });
    if (!choice) return;
    if (choice.id === "choose") await chooseAvatar("user");
    else if (choice.id === "account") await chooseAccountAvatar();
    else if (choice.id === "reset") await resetAvatar("user");
    else await vscode.commands.executeCommand("workbench.action.openSettings", "zycode.userAvatar");
  };

  const pickAvatar = (role: AvatarRole): Promise<void> => (role === "assistant" ? pickAssistantAvatar() : pickUserAvatar());

  return { chooseAvatar, chooseAssistantPreset, chooseAccountAvatar, resetAvatar, pickAvatar };
}
