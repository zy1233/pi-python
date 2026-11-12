import * as acp from "@agentclientprotocol/sdk";

export type {
  InitializeResponse,
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
  constructor(readonly methodIds: string[]) {
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
      if (methodIds) throw new AuthRequiredError(methodIds);
      throw error;
    }
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

  async setConfigOption(sessionId: string, configId: string, value: string): Promise<void> {
    await this.connection.agent.request(acp.methods.agent.session.setConfigOption, {
      sessionId,
      configId,
      value,
    });
  }

  async closeSession(sessionId: string): Promise<void> {
    await this.connection.agent.request(acp.methods.agent.session.close, { sessionId });
  }

  close(): void {
    this.connection.close();
  }
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
