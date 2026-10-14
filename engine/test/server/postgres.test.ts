import { describe, expect, it } from "vitest";
import { loadConversationView } from "../../src/watch/view.js";
import { postgresBackend } from "../agent/support.js";
import { usePostgres } from "../support/pg.js";
import { ClientView } from "../watch/support.js";
import { AUTH, call, SseClient, serve } from "./support.js";

const backend = postgresBackend(usePostgres());

describe("engine HTTP server (postgres)", () => {
  it("creates, submits idempotently and watches over SSE", async () => {
    const fx = await backend.fixture();
    const w = await fx.open();
    const srv = await serve(w.harness);
    try {
      const created = await call(srv.url, "POST", "/v1/conversations", { headers: AUTH, body: {} });
      expect(created.status).toBe(201);
      const id = created.body.conversationId as string;
      const sse = await SseClient.open(`${srv.url}/v1/conversations/${id}/watch`, AUTH);
      const client = new ClientView();
      client.apply((await sse.frame()).frame);

      const body = { text: "note", type: "write", requestId: "r1" };
      const path = `/v1/conversations/${id}/submissions`;
      const a = await call(srv.url, "POST", path, { headers: AUTH, body });
      const b = await call(srv.url, "POST", path, { headers: AUTH, body });
      expect(b.body.submissionId).toBe(a.body.submissionId);
      await call(srv.url, "POST", path, { headers: AUTH, body: { text: "two", type: "write" } });

      for (const f of await sse.frames()) client.apply(f);
      expect(client.view).toEqual(await loadConversationView(w.harness.session, id));
      expect((client.view as { entries: unknown[] }).entries).toHaveLength(2);
      sse.close();

      const forbidden = await call(srv.url, "GET", `/v1/conversations/${id}/watch`, {
        headers: { ...AUTH, "x-oniva-tenant": "other" },
      });
      expect(forbidden.status).toBe(403);
    } finally {
      await srv.close();
      await w.crash();
    }
  });
});
