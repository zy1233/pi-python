import type { PermissionRequestView } from "./protocol.js";

export type FallbackOptionItem =
  | { title: string; kind: "answer"; optionId: string }
  | { title: string; kind: "reveal" };

export interface FallbackDialog {
  message: string;
  detail: string;
  items: FallbackOptionItem[];
}

export type FallbackChoiceResult =
  | { kind: "answer"; optionId: string }
  | { kind: "reveal" }
  | { kind: "dismiss" };

/**
 * Builds the fallback modal dialog shown when a permission or project-trust request arrives while
 * the zycode chat view is hidden (design section 5.1). Preserves the exact option order from the
 * agent, never adds an "always" option, and never truncates `bash` commands.
 */
export function buildPermissionFallback(request: PermissionRequestView): FallbackDialog {
  const message =
    request.kind === "trust" ? `zycode — Project trust: ${request.title}` : `zycode — Allow: ${request.title}`;
  const detailLines: string[] = [];
  if (request.summary !== "") {
    detailLines.push(request.summary);
  }
  if (request.canDiff) {
    detailLines.push("Open chat to inspect the diff before deciding.");
  } else if (request.diffNote) {
    detailLines.push(request.diffNote);
  }
  detailLines.push("Closing this dialog keeps the request waiting in the zycode chat.");

  const items: FallbackOptionItem[] = request.options.map((option) => ({
    title: option.name,
    kind: "answer",
    optionId: option.id,
  }));
  items.push({ title: "Open chat", kind: "reveal" });

  return {
    message,
    detail: detailLines.join("\n\n"),
    items,
  };
}

/**
 * Maps the user's modal selection to an action. Closing or dismissing (`undefined`) leaves the
 * request waiting in the chat rather than auto-answering (design decision D4).
 */
export function resolveFallbackChoice(choice: FallbackOptionItem | undefined): FallbackChoiceResult {
  if (!choice) return { kind: "dismiss" };
  if (choice.kind === "reveal") return { kind: "reveal" };
  return { kind: "answer", optionId: choice.optionId };
}
