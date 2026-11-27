import * as acp from "@agentclientprotocol/sdk";

import type { McpServerConfig } from "./host.js";
import type { ImageAttachment } from "./protocol.js";

export type {
  InitializeResponse,
  ListSessionsResponse,
  LoadSessionResponse,
  NewSessionResponse,
  RequestPermissionRequest,
  RequestPermissionResponse,
  SessionConfigOption,
  SessionNotification,
  SessionUpdate,
  StopReason,
} from "@agentclientprotocol/sdk";

/** `session/new` was refused because the agent has no model or key, and the client can run the login. */
export class AuthRequiredError extends Error {
  constructor(
    readonly methodIds: string[],
    /** The text of the agent: "No model is set. ..." or "The variable NAME has no value. ...". */
    readonly reason: string = "",
  ) {
    super("The agent needs a model and an API key (authentication required).");
    this.name = "AuthRequiredError";
  }
}

export interface PiCapabilities {
  steer: boolean;
  btw: boolean;
}

export type SteerResponse =
  | { sessionId: string; steerId: string; queued: true; queueLength: number }
  | { sessionId: string; steerId: string; queued: false; reason: string };

export interface QueueUpdateNotification {
  sessionId: string;
  steer: { steerId: string; preview: string }[];
  clearedByAbort: boolean;
  clearedSteerIds?: string[];
}

export interface BtwHistoryTurn {
  role: "user" | "assistant";
  text: string;
}

export interface BtwUpdateNotification {
  sessionId: string;
  btwId: string;
  kind: "thought_delta" | "text_delta" | "done" | "error";
  delta: string;
  error?: string | null;
  stopReason?: string;
}

export interface BtwResponse {
  sessionId: string;
  btwId: string;
  stopReason: "end_turn" | "cancelled" | "error";
  error: string | null;
}

export function parsePiCapabilities(init: acp.InitializeResponse | undefined): PiCapabilities {
  const meta = (init as { _meta?: unknown } | undefined)?._meta;
  if (typeof meta !== "object" || meta === null || Array.isArray(meta)) {
    return { steer: false, btw: false };
  }
  const caps = (meta as Record<string, unknown>)["pi/capabilities"];
  if (typeof caps !== "object" || caps === null || Array.isArray(caps)) {
    return { steer: false, btw: false };
  }
  const record = caps as Record<string, unknown>;
  return {
    steer: record.steer === true,
    btw: record.btw === true,
  };
}

export interface ConnectionHandlers {
  /** Called once for each `session/update`, in the order they arrive. Must not block. */
  onUpdate(notification: acp.SessionNotification): void;
  /** The user's answer to `session/request_permission`. `signal` aborts when the agent cancels the request. */
  requestPermission(
    request: acp.RequestPermissionRequest,
    signal: AbortSignal,
  ): Promise<acp.RequestPermissionResponse>;
  /** Called when the agent sends `_pi/queue_update` (register item X9). */
  onQueueUpdate?(notification: QueueUpdateNotification): void;
  /** Called when the agent sends `_pi/btw_update` (register item X10). */
  onBtwUpdate?(notification: BtwUpdateNotification): void;
}

const AUTH_REQUIRED_CODE = -32000;
const METHOD_NOT_FOUND_CODE = -32601;

function parseQueueUpdate(raw: unknown): QueueUpdateNotification | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const rec = raw as Record<string, unknown>;
  if (typeof rec.sessionId !== "string") return null;
  const steer: { steerId: string; preview: string }[] = [];
  if (Array.isArray(rec.steer)) {
    for (const item of rec.steer) {
      if (typeof item === "object" && item !== null && !Array.isArray(item)) {
        const entry = item as Record<string, unknown>;
        steer.push({
          steerId: typeof entry.steerId === "string" ? entry.steerId : "",
          preview: typeof entry.preview === "string" ? entry.preview : "",
        });
      }
    }
  }
  const clearedSteerIds = Array.isArray(rec.clearedSteerIds)
    ? rec.clearedSteerIds.filter((v): v is string => typeof v === "string")
    : undefined;
  return {
    sessionId: rec.sessionId,
    steer,
    clearedByAbort: rec.clearedByAbort === true,
    ...(clearedSteerIds && clearedSteerIds.length > 0 ? { clearedSteerIds } : {}),
  };
}

function parseBtwUpdate(raw: unknown): BtwUpdateNotification | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const rec = raw as Record<string, unknown>;
  if (typeof rec.sessionId !== "string" || typeof rec.btwId !== "string") return null;
  const kind = rec.kind;
  if (kind !== "thought_delta" && kind !== "text_delta" && kind !== "done" && kind !== "error") {
    return null;
  }
  return {
    sessionId: rec.sessionId,
    btwId: rec.btwId,
    kind,
    delta: typeof rec.delta === "string" ? rec.delta : "",
    error: typeof rec.error === "string" ? rec.error : null,
    ...(typeof rec.stopReason === "string" ? { stopReason: rec.stopReason } : {}),
  };
}

function promptBlocks(text: string, images?: readonly ImageAttachment[]): acp.ContentBlock[] {
  const imageBlocks: acp.ContentBlock[] =
    images?.map((img) => ({ type: "image" as const, mimeType: img.mimeType, data: img.data })) ?? [];
  const textBlocks: acp.ContentBlock[] =
    text !== "" || imageBlocks.length === 0 ? [{ type: "text" as const, text }] : [];
  return [...imageBlocks, ...textBlocks];
}

/**
 * The ACP client side (docs/specs/2026-11-04-vscode-extension-design.md, section 4), over the
 * SDK. It declares one client capability, `auth.terminal`. The agent uses no `fs/*` and no
 * `terminal/*` method, so the client does not declare them.
 */
export class AcpConnection {
  private constructor(private readonly connection: acp.ClientConnection) {}

  static open(
    stream: { input: WritableStream<Uint8Array>; output: ReadableStream<Uint8Array> },
    handlers: ConnectionHandlers,
  ): AcpConnection {
    const app = acp
      .client({ name: "zycode-vscode" })
      .onRequest(acp.methods.client.session.requestPermission, (context) =>
        handlers.requestPermission(context.params, context.signal),
      )
      .onNotification(acp.methods.client.session.update, (context) => {
        handlers.onUpdate(context.params);
      })
      .onNotification("_pi/queue_update", parseQueueUpdate, (context) => {
        if (context.params) handlers.onQueueUpdate?.(context.params);
      })
      .onNotification("_pi/btw_update", parseBtwUpdate, (context) => {
        if (context.params) handlers.onBtwUpdate?.(context.params);
      });
    return new AcpConnection(app.connect(acp.ndJsonStream(stream.input, stream.output)));
  }

  get closed(): Promise<void> {
    return this.connection.closed;
  }

  get isClosed(): boolean {
    return this.connection.signal.aborted;
  }

  initialize(clientVersion: string): Promise<acp.InitializeResponse> {
    return this.connection.agent.request(acp.methods.agent.initialize, {
      protocolVersion: acp.PROTOCOL_VERSION,
      clientCapabilities: {
        auth: { terminal: true },
        _meta: { "pi/capabilities": { steer: true, btw: true } },
      },
      clientInfo: { name: "zycode-vscode", title: "zycode for VS Code", version: clientVersion },
    });
  }

  /** Opens a session. Throws `AuthRequiredError` when the agent needs the terminal login first. */
  async newSession(cwd: string, mcpServers: McpServerConfig[] = []): Promise<acp.NewSessionResponse> {
    try {
      return await this.connection.agent.request(acp.methods.agent.session.new, { cwd, mcpServers });
    } catch (error) {
      const methodIds = authMethodIds(error);
      if (methodIds) throw new AuthRequiredError(methodIds, error instanceof Error ? error.message : "");
      throw error;
    }
  }

  /** One page of `session/list` for a folder. The agent filters by the exact `cwd` text. */
  listSessions(cwd: string, cursor?: string): Promise<acp.ListSessionsResponse> {
    return this.connection.agent.request(acp.methods.agent.session.list, cursor === undefined ? { cwd } : { cwd, cursor });
  }

  /** `session/load`: the agent replays the history as `session/update` notifications with `_meta.isReplay`. */
  loadSession(cwd: string, sessionId: string, mcpServers: McpServerConfig[] = []): Promise<acp.LoadSessionResponse> {
    return this.connection.agent.request(acp.methods.agent.session.load, { cwd, sessionId, mcpServers });
  }

  /**
   * `pi/session/delete` (register item X1, wire name `_pi/session/delete`). The agent does not
   * announce it in `initialize`. An agent without it answers `method_not_found`.
   */
  async deleteSession(sessionId: string): Promise<void> {
    await this.connection.agent.request("_pi/session/delete", { sessionId });
  }

  /** `pi/session/steer` (register item X9, wire name `_pi/session/steer`). */
  async steerSession(
    sessionId: string,
    text: string,
    steerId: string,
    images?: readonly ImageAttachment[],
  ): Promise<SteerResponse> {
    const raw = (await this.connection.agent.request("_pi/session/steer", {
      sessionId,
      steerId,
      prompt: promptBlocks(text, images),
    })) as Record<string, unknown>;
    if (raw.queued === true) {
      return {
        sessionId: typeof raw.sessionId === "string" ? raw.sessionId : sessionId,
        steerId: typeof raw.steerId === "string" ? raw.steerId : steerId,
        queued: true,
        queueLength: typeof raw.queueLength === "number" ? raw.queueLength : 1,
      };
    }
    return {
      sessionId: typeof raw.sessionId === "string" ? raw.sessionId : sessionId,
      steerId: typeof raw.steerId === "string" ? raw.steerId : steerId,
      queued: false,
      reason: typeof raw.reason === "string" ? raw.reason : "idle",
    };
  }

  /** `pi/session/btw` (register item X10, wire name `_pi/session/btw`). */
  async askBtw(
    sessionId: string,
    btwId: string,
    text: string,
    history: readonly BtwHistoryTurn[] = [],
    images?: readonly ImageAttachment[],
  ): Promise<BtwResponse> {
    const raw = (await this.connection.agent.request("_pi/session/btw", {
      sessionId,
      btwId,
      prompt: promptBlocks(text, images),
      ...(history.length > 0 ? { history } : {}),
    })) as Record<string, unknown>;
    const stopReason =
      raw.stopReason === "cancelled" || raw.stopReason === "error" ? raw.stopReason : "end_turn";
    return {
      sessionId: typeof raw.sessionId === "string" ? raw.sessionId : sessionId,
      btwId: typeof raw.btwId === "string" ? raw.btwId : btwId,
      stopReason,
      error: typeof raw.error === "string" ? raw.error : null,
    };
  }

  /** `pi/session/btw_cancel` (register item X10, wire name `_pi/session/btw_cancel`). */
  async cancelBtw(sessionId: string, btwId: string): Promise<void> {
    await this.connection.agent.request("_pi/session/btw_cancel", { sessionId, btwId });
  }

  /**
   * `pi/yolo_mode_changed` (register item X8, wire name `_pi/yolo_mode_changed`). The only way to
   * put a live session in `always-approve`: the standard `mode` option takes `ask` and `auto` only.
   * The agent applies it to every open session and keeps it as the start mode of later sessions.
   */
  notifyPermissionMode(mode: string): Promise<void> {
    return this.connection.agent.notify("_pi/yolo_mode_changed", { permission_mode: mode });
  }

  async prompt(sessionId: string, text: string, images?: readonly ImageAttachment[]): Promise<acp.StopReason> {
    const response = await this.connection.agent.request(acp.methods.agent.session.prompt, {
      sessionId,
      prompt: promptBlocks(text, images),
    });
    return response.stopReason;
  }

  cancel(sessionId: string): Promise<void> {
    return this.connection.agent.notify(acp.methods.agent.session.cancel, { sessionId });
  }

  async setConfigOption(sessionId: string, configId: string, value: string): Promise<acp.SessionConfigOption[]> {
    const response = await this.connection.agent.request(acp.methods.agent.session.setConfigOption, {
      sessionId,
      configId,
      value,
    });
    return response.configOptions ?? [];
  }

  async closeSession(sessionId: string): Promise<void> {
    await this.connection.agent.request(acp.methods.agent.session.close, { sessionId });
  }

  close(): void {
    this.connection.close();
  }
}

/** The agent has no such method (`-32601`): an old agent without `pi/session/delete`. */
export function isMethodNotFound(error: unknown): boolean {
  return error instanceof acp.RequestError && error.code === METHOD_NOT_FOUND_CODE;
}

function authMethodIds(error: unknown): string[] | undefined {
  if (!(error instanceof acp.RequestError) || error.code !== AUTH_REQUIRED_CODE) return undefined;
  const data = error.data;
  if (typeof data !== "object" || data === null) return undefined;
  const methods = (data as { authMethods?: unknown }).authMethods;
  if (!Array.isArray(methods)) return undefined;
  const ids = methods.filter((id): id is string => typeof id === "string");
  return ids.length > 0 ? ids : undefined;
}
