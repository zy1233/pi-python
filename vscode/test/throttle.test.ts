import { describe, expect, it } from "vitest";

import { throttle, type Scheduler } from "../src/core/throttle.js";

/** A clock and timers that a test moves by hand. */
function fakeScheduler() {
  let now = 1000;
  let nextHandle = 1;
  const timers = new Map<number, { at: number; callback: () => void }>();
  const scheduler: Scheduler = {
    now: () => now,
    setTimeout: (callback, ms) => {
      const handle = nextHandle++;
      timers.set(handle, { at: now + ms, callback });
      return handle;
    },
    clearTimeout: (handle) => {
      timers.delete(handle as number);
    },
  };
  return {
    scheduler,
    pending: () => timers.size,
    /** Moves the clock and runs the timers that are due, in time order. */
    advance(ms: number) {
      const target = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= target).sort(([, a], [, b]) => a.at - b.at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = Math.max(now, due[1].at);
        due[1].callback();
      }
      now = target;
    },
  };
}

describe("throttle", () => {
  it("runs the first request at once", () => {
    const clock = fakeScheduler();
    let runs = 0;
    throttle(() => runs++, 80, clock.scheduler).request();
    expect(runs).toBe(1);
    expect(clock.pending()).toBe(0);
  });

  it("joins the requests in the pause into one run at the end of it", () => {
    const clock = fakeScheduler();
    let runs = 0;
    const t = throttle(() => runs++, 80, clock.scheduler);
    t.request();
    clock.advance(10);
    t.request();
    t.request();
    clock.advance(10);
    t.request();
    expect(runs).toBe(1);
    expect(clock.pending()).toBe(1);
    clock.advance(59);
    expect(runs).toBe(1);
    clock.advance(1);
    expect(runs).toBe(2);
    expect(clock.pending()).toBe(0);
  });

  it("does not lose the last request: the planned run reads the newest state", () => {
    const clock = fakeScheduler();
    let state = "";
    const drawn: string[] = [];
    const t = throttle(() => drawn.push(state), 80, clock.scheduler);
    for (const piece of ["a", "b", "c", "d", "e"]) {
      state += piece;
      t.request();
      clock.advance(5);
    }
    clock.advance(200);
    expect(drawn).toEqual(["a", "abcde"]);
  });

  it("runs a request after a pause at once, and counts the pause from the end of the last run", () => {
    const clock = fakeScheduler();
    let runs = 0;
    const t = throttle(() => runs++, 80, clock.scheduler);
    t.request();
    clock.advance(80);
    t.request();
    expect(runs).toBe(2);
    clock.advance(79);
    t.request();
    expect(runs).toBe(2);
    clock.advance(1);
    expect(runs).toBe(3);
  });

  it("counts the pause from the end of a slow run", () => {
    const clock = fakeScheduler();
    const starts: number[] = [];
    const t = throttle(
      () => {
        starts.push(clock.scheduler.now());
        clock.advance(30); // the run takes 30 ms
      },
      80,
      clock.scheduler,
    );
    t.request();
    t.request();
    clock.advance(1000);
    // The first run starts at 1000 and ends at 1030. The next one starts 80 ms after the end.
    expect(starts).toEqual([1000, 1110]);
  });

  it("drops a planned run on cancel, and works again after it", () => {
    const clock = fakeScheduler();
    let runs = 0;
    const t = throttle(() => runs++, 80, clock.scheduler);
    t.request();
    t.request();
    expect(clock.pending()).toBe(1);
    t.cancel();
    expect(clock.pending()).toBe(0);
    clock.advance(500);
    expect(runs).toBe(1);
    t.request();
    expect(runs).toBe(2);
    t.cancel(); // nothing is planned: no effect
  });

  it("keeps working after a run that throws, and does not stay in a planned state", () => {
    const clock = fakeScheduler();
    let runs = 0;
    const t = throttle(
      () => {
        runs++;
        if (runs === 1) throw new Error("boom");
      },
      80,
      clock.scheduler,
    );
    expect(() => t.request()).toThrow("boom");
    clock.advance(80);
    t.request();
    expect(runs).toBe(2);
  });
});
