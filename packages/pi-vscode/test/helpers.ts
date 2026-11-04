import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ChatController, type ControllerOptions } from "../src/core/chat-controller.js";
import type { Host, Logger, Settings, SetupTerminalRequest } from "../src/core/host.js";
import type { HostMessage } from "../src/core/protocol.js";

export const FAKE_AGENT = path.join(path.dirname(fileURLToPath(import.meta.url)), "fake-agent.mjs");

export class RecordingLog implements Logger {
  readonly lines: string[] = [];
  info(message: string): void {
    this.lines.push(`info ${message}`);
  }
  warn(message: string): void {
    this.lines.push(`warn ${message}`);
  }
  error(message: string): void {
    this.lines.push(`error ${message}`);
  }
}

export interface TestHostOptions {
  settings?: Partial<Settings>;
  trusted?: boolean;
  folder?: string | null;
  dirty?: string[];
  pin?: string;
  extensionPath?: string;
  setup?: (request: SetupTerminalRequest) => Promise<{ exitCode: number | undefined }>;
}

export function tempDir(registry: string[] | undefined, prefix: string): string {
  const dir = mkdtempSync(path.join(tmpdir(), `zycode-${prefix}`));
  registry?.push(dir);
  return dir;
}

export class TestHost implements Host {
  readonly log = new RecordingLog();
  readonly extensionPath: string;
  readonly extensionVersion = "0.1.0";
  readonly agentVersionPin: string;
  readonly platform = process.platform;
  readonly notifications: { level: string; text: string }[] = [];
  readonly setupRequests: SetupTerminalRequest[] = [];
  private readonly folder: string | undefined;
  private readonly tempDirs: string[] = [];

  constructor(private readonly options: TestHostOptions = {}) {
    this.extensionPath = options.extensionPath ?? tempDir(this.tempDirs, "ext-");
    this.agentVersionPin = options.pin ?? "0.8.0";
    this.folder = options.folder === null ? undefined : (options.folder ?? tempDir(this.tempDirs, "proj-"));
  }

  settings(): Settings {
    return { agentPath: process.execPath, agentArgs: [FAKE_AGENT], permissionMode: "ask", ...this.options.settings };
  }
  isTrusted(): boolean {
    return this.options.trusted ?? true;
  }
  sessionFolder(): string | undefined {
    return this.folder;
  }
  dirtyFiles(): string[] {
    return this.options.dirty ?? [];
  }
  async runSetupTerminal(request: SetupTerminalRequest): Promise<{ exitCode: number | undefined }> {
    this.setupRequests.push(request);
    return this.options.setup ? this.options.setup(request) : { exitCode: 0 };
  }
  notify(level: "info" | "warning" | "error", text: string): void {
    this.notifications.push({ level, text });
  }
  cleanup(): void {
    for (const dir of this.tempDirs) rmSync(dir, { recursive: true, force: true });
  }
}

export function newController(
  host: Host,
  extra: ControllerOptions = {},
): { controller: ChatController; messages: HostMessage[] } {
  const controller = new ChatController(host, { stopTimings: { graceMs: 2000, termMs: 1000 }, ...extra });
  const messages: HostMessage[] = [];
  controller.onMessage((message) => messages.push(message));
  return { controller, messages };
}

export async function waitFor(check: () => boolean, what: string, timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!check()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

export function readLog(file: string): string[] {
  return existsSync(file) ? readFileSync(file, "utf8").split("\n").filter(Boolean) : [];
}

export function ofType<T extends HostMessage["type"]>(
  messages: HostMessage[],
  type: T,
): Extract<HostMessage, { type: T }>[] {
  return messages.filter((m): m is Extract<HostMessage, { type: T }> => m.type === type);
}
