import { afterEach, describe, expect, it, vi } from "vitest";
import { StorageRequestError } from "../../src/core/errors.js";
import { memoryBackend } from "../agent/support.js";
import { AUTH, call, serve } from "./support.js";

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  for (const fn of cleanups.splice(0).reverse()) await fn();
});

describe("engine HTTP error mapping", () => {
  it("answers 409 conflict, not 500, when storage refuses one write", async () => {
    const fx = await memoryBackend.fixture();
    const w = await fx.open({ settings: { progressIntervalMs: 0 } });
    const errors: unknown[] = [];
    const srv = await serve(w.harness, { onError: (e) => errors.push(e) });
    cleanups.push(() => w.crash());
    cleanups.push(() => srv.close());

    const created = await call(srv.url, "POST", "/v1/conversations", { headers: AUTH, body: {} });
    expect(created.status).toBe(201);
    const id = created.body.conversationId as string;

    // A submit that loses a requestId unique-key race in storage.
    const real = w.harness.conversation.bind(w.harness);
    vi.spyOn(w.harness, "conversation").mockImplementationOnce((cid) => {
      const handle = real(cid);
      handle.submit = () =>
        Promise.reject(
          new StorageRequestError(`conflicting write: duplicate requestId req-1 in ${cid}`, {
            retryable: false,
          }),
        );
      return handle;
    });
    const path = `/v1/conversations/${id}/submissions`;
    const body = { text: "hi", requestId: "req-1" };
    const res = await call(srv.url, "POST", path, { headers: AUTH, body });
    expect(res.status).toBe(409);
    expect(res.body).toEqual({ error: "conflict", reason: "storage_request" });
    expect(JSON.stringify(res.body)).not.toContain("req-1");
    expect(errors).toEqual([]);
    // The session is still healthy: a retry succeeds.
    const retry = await call(srv.url, "POST", path, { headers: AUTH, body });
    expect(retry.status).toBe(202);
  });
});
