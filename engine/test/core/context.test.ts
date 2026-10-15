import { describe, expect, it } from "vitest";
import { ManualClock } from "../../src/core/clock.js";
import { Context } from "../../src/core/context.js";
import { Cancelled, DeadlineExceeded } from "../../src/core/errors.js";

describe("Context", () => {
  it("cascades a parent abort to children with the parent's reason", () => {
    const root = Context.background();
    const child = root.child();
    const grandchild = child.child();
    const reason = new Error("lease lost");
    root.cancel(reason);
    expect(child.cancelled).toBe(true);
    expect(grandchild.cancelled).toBe(true);
    expect(child.reason).toBe(reason);
    expect(grandchild.reason).toBe(reason);
    expect(grandchild.signal.aborted).toBe(true);
  });

  it("a child abort leaves the parent and siblings untouched", () => {
    const root = Context.background();
    const a = root.child();
    const b = root.child();
    a.cancel();
    expect(a.cancelled).toBe(true);
    expect(a.reason).toBeInstanceOf(Cancelled);
    expect(root.cancelled).toBe(false);
    expect(b.cancelled).toBe(false);
  });

  it("a child of an already-cancelled parent starts cancelled", () => {
    const root = Context.background();
    root.cancel("gone");
    const child = root.child();
    expect(child.cancelled).toBe(true);
    expect(child.reason).toBe("gone");
  });

  it("cancels at its deadline using the injected clock", () => {
    const clock = new ManualClock(0);
    const root = Context.background(clock);
    const ctx = root.child({ timeoutMs: 100 });
    expect(ctx.deadline).toBe(100);
    clock.advance(99);
    expect(ctx.cancelled).toBe(false);
    clock.advance(1);
    expect(ctx.cancelled).toBe(true);
    expect(ctx.reason).toBeInstanceOf(DeadlineExceeded);
    expect(root.cancelled).toBe(false);
  });

  it("a child never outlives its parent's deadline", () => {
    const clock = new ManualClock(0);
    const parent = Context.background(clock).child({ deadline: 50 });
    const child = parent.child({ timeoutMs: 1000 });
    expect(child.deadline).toBe(50);
    clock.advance(50);
    expect(child.cancelled).toBe(true);
  });

  it("throwIfCancelled throws the reason", () => {
    const ctx = Context.background();
    expect(() => ctx.throwIfCancelled()).not.toThrow();
    const reason = new Error("x");
    ctx.cancel(reason);
    expect(() => ctx.throwIfCancelled()).toThrow(reason);
  });

  it("cancel is idempotent and keeps the first reason", () => {
    const ctx = Context.background();
    const first = new Error("first");
    ctx.cancel(first);
    ctx.cancel(new Error("second"));
    expect(ctx.reason).toBe(first);
  });

  it("detaches from the parent once cancelled", () => {
    const clock = new ManualClock(0);
    const root = Context.background(clock);
    for (let i = 0; i < 50; i++) root.child({ timeoutMs: 10 }).cancel();
    expect(clock.pendingTimers()).toBe(0);
    expect(root.childCount()).toBe(0);
  });
});
