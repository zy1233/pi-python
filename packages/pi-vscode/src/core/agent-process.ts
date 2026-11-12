import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { Readable, Writable } from "node:stream";

import type { Logger } from "./host.js";
import type { AgentCommand } from "./locate.js";

export interface ExitInfo {
  code: number | null;
  signal: NodeJS.Signals | null;
}

export interface StopTimings {
  /** Time to wait for the exit after stdin is closed. */
  graceMs: number;
  /** Time to wait after SIGTERM before SIGKILL. */
  termMs: number;
}

const DEFAULT_TIMINGS: StopTimings = { graceMs: 3000, termMs: 2000 };
const STDERR_KEEP_BYTES = 1024 * 1024;

/**
 * The agent as a child process (docs/specs/2026-11-04-vscode-extension-design.md, section 3).
 * stdout is the ACP channel. stderr goes to the log, line by line, and a tail is kept for the
 * message that says why the agent exited.
 */
export class AgentProcess {
  readonly input: WritableStream<Uint8Array>;
  readonly output: ReadableStream<Uint8Array>;
  readonly exited: Promise<ExitInfo>;

  private readonly child: ChildProcessWithoutNullStreams;
  private readonly stderrLines: string[] = [];
  private stderrBytes = 0;
  private exitInfo: ExitInfo | undefined;

  private constructor(child: ChildProcessWithoutNullStreams, private readonly log: Logger) {
    this.child = child;
    // A write to an agent that has exited gives EPIPE. The connection sees the close; do not crash.
    child.stdin.on("error", () => undefined);
    this.input = Writable.toWeb(child.stdin) as WritableStream<Uint8Array>;
    this.output = Readable.toWeb(child.stdout) as ReadableStream<Uint8Array>;
    this.exited = new Promise<ExitInfo>((resolve) => {
      child.once("close", (code, signal) => {
        this.exitInfo = { code, signal };
        resolve(this.exitInfo);
      });
    });
    this.readStderr();
  }

  /** Starts the program. Rejects with the spawn error (`ENOENT`, `EACCES`, ...). */
  static start(
    command: AgentCommand,
    options: { cwd: string; env?: NodeJS.ProcessEnv; log: Logger },
  ): Promise<AgentProcess> {
    return new Promise((resolve, reject) => {
      const child = spawn(command.command, command.args, {
        cwd: options.cwd,
        env: options.env ?? process.env,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
        shell: false,
      });
      const onError = (error: Error) => reject(error);
      child.once("error", onError);
      child.once("spawn", () => {
        child.off("error", onError);
        options.log.info(`agent started (pid ${child.pid ?? "?"}, ${command.source})`);
        resolve(new AgentProcess(child as ChildProcessWithoutNullStreams, options.log));
      });
    });
  }

  get pid(): number | undefined {
    return this.child.pid;
  }

  get hasExited(): boolean {
    return this.exitInfo !== undefined;
  }

  /** The last `maxChars` characters of what the agent wrote to stderr. */
  stderrTail(maxChars = 4000): string {
    const text = this.stderrLines.join("\n");
    return text.length > maxChars ? text.slice(text.length - maxChars) : text;
  }

  /**
   * Closes stdin (the agent stops its tools and exits 0 on that), then SIGTERM, then SIGKILL.
   * Resolves when the process is gone.
   */
  async stop(timings: StopTimings = DEFAULT_TIMINGS): Promise<ExitInfo> {
    if (this.exitInfo) return this.exitInfo;
    this.child.stdin.end();
    let exit = await this.waitFor(timings.graceMs);
    if (exit) return exit;
    this.log.warn("agent did not exit after stdin closed; sending SIGTERM");
    this.child.kill("SIGTERM");
    exit = await this.waitFor(timings.termMs);
    if (exit) return exit;
    this.log.warn("agent did not exit after SIGTERM; sending SIGKILL");
    this.child.kill("SIGKILL");
    return this.exited;
  }

  private async waitFor(ms: number): Promise<ExitInfo | undefined> {
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<undefined>((resolve) => {
      timer = setTimeout(() => resolve(undefined), ms);
    });
    try {
      return await Promise.race([this.exited, timeout]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  private readStderr(): void {
    this.child.stderr.setEncoding("utf8");
    let partial = "";
    const keep = (line: string) => {
      this.stderrLines.push(line);
      this.stderrBytes += Buffer.byteLength(line) + 1;
      while (this.stderrBytes > STDERR_KEEP_BYTES && this.stderrLines.length > 1) {
        const dropped = this.stderrLines.shift() ?? "";
        this.stderrBytes -= Buffer.byteLength(dropped) + 1;
      }
      this.log.info(`[agent] ${line}`);
    };
    this.child.stderr.on("data", (chunk: string) => {
      partial += chunk;
      let newline = partial.indexOf("\n");
      while (newline >= 0) {
        keep(partial.slice(0, newline).replace(/\r$/, ""));
        partial = partial.slice(newline + 1);
        newline = partial.indexOf("\n");
      }
    });
    this.child.stderr.on("end", () => {
      if (partial !== "") keep(partial);
      partial = "";
    });
  }
}
