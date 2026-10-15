import { describe, expect, it } from "vitest";
import {
  MAX_TIMER_DELAY_MS,
  ManualClock,
  realClock,
  setLongTimer,
  type TimerHost,
} from "../../src/core/clock.js";

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

/** A fake timer host that records every arm and fires on demand. */
function fakeHost() {
  const armed: Array<{ id: number; delay: number; fire: () => void }> = [];
  const cleared: number[] = [];
  let next = 0;
  const host: TimerHost<number> = {
    setTimeout(callback, delay) {
      const id = next++;
      armed.push({ id, delay, fire: callback });
      return id;
    },
    clearTimeout(id) {
      cleared.push(id);
    },
  };
  const fireLast = () => armed[armed.length - 1]?.fire();
  return { host, armed, cleared, fireLast };
}

describe("setLongTimer", () => {
  it("arms short delays directly", () => {
    const { host, armed, fireLast } = fakeHost();
    let hit = 0;
    setLongTimer(1500, () => hit++, host);
    expect(armed.map((a) => a.delay)).toEqual([1500]);
    fireLast();
    expect(hit).toBe(1);
  });

  it("chunks delays beyond the setTimeout limit and fires only at the end", () => {
    const { host, armed, fireLast } = fakeHost();
    const thirtyDays = 30 * 24 * 60 * 60 * 1000;
    let hit = 0;
    setLongTimer(thirtyDays, () => hit++, host);
    expect(armed[0]?.delay).toBe(MAX_TIMER_DELAY_MS);
    fireLast();
    expect(hit).toBe(0);
    expect(armed.map((a) => a.delay)).toEqual([
      MAX_TIMER_DELAY_MS,
      thirtyDays - MAX_TIMER_DELAY_MS,
    ]);
    fireLast();
    expect(hit).toBe(1);
    expect(armed).toHaveLength(2);
  });

  it("cancels the chunk that is currently armed", () => {
    const { host, armed, cleared, fireLast } = fakeHost();
    let hit = 0;
    const cancel = setLongTimer(3 * MAX_TIMER_DELAY_MS, () => hit++, host);
    fireLast();
    cancel();
    expect(cleared).toEqual([armed[1]?.id]);
    fireLast(); // a late fire after cancel does nothing
    expect(hit).toBe(0);
    expect(armed).toHaveLength(2);
  });

  it("treats negative and NaN delays as zero", () => {
    const { host, armed } = fakeHost();
    setLongTimer(-5, () => {}, host);
    setLongTimer(Number.NaN, () => {}, host);
    expect(armed.map((a) => a.delay)).toEqual([0, 0]);
  });

  it("realClock does not fire a beyond-limit timer early", async () => {
    let hit = false;
    const cancel = realClock.setTimer(MAX_TIMER_DELAY_MS + 10_000, () => {
      hit = true;
    });
    await new Promise((r) => setTimeout(r, 20));
    cancel();
    expect(hit).toBe(false);
  });
});
