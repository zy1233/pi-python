import * as acp from "@agentclientprotocol/sdk";

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

export interface ConnectionHandlers {
  /** Called once for each `session/update`, in the order they arrive. Must not block. */
  onUpdate(notification: acp.SessionNotification): void;
  /** The user's answer to `session/request_permission`. `signal` aborts when the agent cancels the request. */
  requestPermission(
    request: acp.RequestPermissionRequest,
    signal: AbortSignal,
  ): Promise<acp.RequestPermissionResponse>;
}

const AUTH_REQUIRED_CODE = -32000;
const METHOD_NOT_FOUND_CODE = -32601;

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
      clientCapabilities: { auth: { terminal: true } },
      clientInfo: { name: "zycode-vscode", title: "zycode for VS Code", version: clientVersion },
    });
  }

  /** Opens a session. Throws `AuthRequiredError` when the agent needs the terminal login first. */
  async newSession(cwd: string): Promise<acp.NewSessionResponse> {
    try {
      return await this.connection.agent.request(acp.methods.agent.session.new, { cwd, mcpServers: [] });
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
  loadSession(cwd: string, sessionId: string): Promise<acp.LoadSessionResponse> {
    return this.connection.agent.request(acp.methods.agent.session.load, { cwd, sessionId, mcpServers: [] });
  }

  /**
   * `pi/session/delete` (register item X1, wire name `_pi/session/delete`). The agent does not
   * announce it in `initialize`. An agent without it answers `method_not_found`.
   */
  async deleteSession(sessionId: string): Promise<void> {
    await this.connection.agent.request("_pi/session/delete", { sessionId });
  }

  /**
   * `pi/yolo_mode_changed` (register item X8, wire name `_pi/yolo_mode_changed`). The only way to
   * put a live session in `always-approve`: the standard `mode` option takes `ask` and `auto` only.
   * The agent applies it to every open session and keeps it as the start mode of later sessions.
   */
  notifyPermissionMode(mode: string): Promise<void> {
    return this.connection.agent.notify("_pi/yolo_mode_changed", { permission_mode: mode });
  }

  async prompt(sessionId: string, text: string): Promise<acp.StopReason> {
    const response = await this.connection.agent.request(acp.methods.agent.session.prompt, {
      sessionId,
      prompt: [{ type: "text", text }],
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
