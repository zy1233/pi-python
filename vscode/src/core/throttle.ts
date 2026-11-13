/** The clock and the timers that `throttle` uses. The webview gives the real ones, a test gives its own. */
export interface Scheduler {
  now(): number;
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

export interface Throttled {
  /** Asks for a run. The run reads the newest state when it starts, so a run that is planned already is enough. */
  request(): void;
  /** Drops a run that is planned. A later `request` works as before. */
  cancel(): void;
}

/**
 * Runs `run` at most once in `intervalMs`, counted from the end of the last run.
 * - The first request, and a request after a pause, run at once.
 * - Requests in the pause join into one run at its end. The last request is never lost.
 *
 * The webview uses it to draw streamed text. A draw makes the whole message again (parse and DOM), so
 * it costs more as the message gets longer, and the model sends a new piece many times in a second.
 */
export function throttle(run: () => void, intervalMs: number, scheduler: Scheduler): Throttled {
  let last = Number.NEGATIVE_INFINITY;
  let handle: unknown;
  let planned = false;

  const fire = () => {
    planned = false;
    handle = undefined;
    try {
      run();
    } finally {
      last = scheduler.now();
    }
  };

  return {
    request() {
      if (planned) return;
      const wait = last + intervalMs - scheduler.now();
      if (wait <= 0) {
        fire();
        return;
      }
      planned = true;
      handle = scheduler.setTimeout(fire, wait);
    },
    cancel() {
      if (!planned) return;
      scheduler.clearTimeout(handle);
      planned = false;
      handle = undefined;
    },
  };
}
