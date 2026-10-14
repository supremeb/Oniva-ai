import { describe, expect, it } from "vitest";
import { ManualClock, realClock } from "../../src/core/clock.js";

describe("ManualClock", () => {
  it("fires timers in deadline order and never early", () => {
    const clock = new ManualClock(1000);
    const fired: Array<[string, number]> = [];
    clock.setTimer(300, () => fired.push(["c", clock.now()]));
    clock.setTimer(100, () => fired.push(["a", clock.now()]));
    clock.setTimer(200, () => fired.push(["b", clock.now()]));
    clock.setTimer(200, () => fired.push(["b2", clock.now()]));

    clock.advance(99);
    expect(fired).toEqual([]);
    clock.advance(1);
    expect(fired).toEqual([["a", 1100]]);
    clock.advance(150);
    expect(fired).toEqual([
      ["a", 1100],
      ["b", 1200],
      ["b2", 1200],
    ]);
    expect(clock.now()).toBe(1250);
    clock.advance(1000);
    expect(fired.map(([n]) => n)).toEqual(["a", "b", "b2", "c"]);
    expect(fired[3]?.[1]).toBe(1300);
    expect(clock.now()).toBe(2250);
  });

  it("fires timers scheduled by callbacks within the same advance", () => {
    const clock = new ManualClock(0);
    const fired: number[] = [];
    clock.setTimer(10, () => {
      fired.push(clock.now());
      clock.setTimer(5, () => fired.push(clock.now()));
    });
    clock.advance(20);
    expect(fired).toEqual([10, 15]);
  });

  it("cancelled timers do not fire", () => {
    const clock = new ManualClock(0);
    let hit = false;
    const cancel = clock.setTimer(10, () => {
      hit = true;
    });
    cancel();
    clock.advance(100);
    expect(hit).toBe(false);
    expect(clock.pendingTimers()).toBe(0);
  });

  it("sleep resolves only once the deadline passes, and rejects on abort", async () => {
    const clock = new ManualClock(0);
    let done = false;
    const p = clock.sleep(50).then(() => {
      done = true;
    });
    clock.advance(49);
    await Promise.resolve();
    expect(done).toBe(false);
    clock.advance(1);
    await p;
    expect(done).toBe(true);

    const ac = new AbortController();
    const s = clock.sleep(50, ac.signal);
    ac.abort(new Error("stop"));
    await expect(s).rejects.toThrow("stop");
    expect(clock.pendingTimers()).toBe(0);
  });

  it("monotonic time follows advances and rejects negative steps", () => {
    const clock = new ManualClock(0);
    const m0 = clock.monotonic();
    clock.advance(25);
    expect(clock.monotonic() - m0).toBe(25);
    expect(() => clock.advance(-1)).toThrow();
  });
});

describe("realClock", () => {
  it("sleeps and reports time", async () => {
    const before = realClock.monotonic();
    await realClock.sleep(5);
    expect(realClock.monotonic() - before).toBeGreaterThanOrEqual(4);
    expect(Math.abs(realClock.now() - Date.now())).toBeLessThan(50);
  });
});
