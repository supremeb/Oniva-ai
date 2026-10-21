import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { Context } from "../../src/core/context.js";
import { BackendPolicyClient } from "../../src/worker/backend-policy.js";
import { defaultModelRegistry } from "../../src/worker/providers.js";
import { FakeGateway } from "../support/fake-gateway.js";

let gateways: FakeGateway[] = [];
afterEach(async () => {
  await Promise.all(gateways.map((g) => g.close()));
  gateways = [];
});

describe("defaultModelRegistry", () => {
  it("registers anthropic and openrouter", () => {
    expect(defaultModelRegistry({}).names()).toEqual(["anthropic", "openrouter"]);
  });

  it("reports openrouter to the backend policy as a supported provider", async () => {
    const gw = await FakeGateway.start([]);
    gateways.push(gw);
    const models = defaultModelRegistry({});
    const client = new BackendPolicyClient({
      baseUrl: gw.baseUrl,
      serviceToken: gw.serviceToken,
      supportedProviders: () => models.names(),
    });
    await client.resolveModelRequest({
      ctx: Context.background(),
      purpose: "generation",
      conversation: { id: "c", tenantId: "t1", projectId: "p1" },
      agent: {},
      runId: "r",
      round: 0,
    } as never);
    expect(gw.policyRequests()[0]?.body).toMatchObject({
      supportedProviders: ["anthropic", "openrouter"],
    });
  });

  it("honours ENGINE_OPENROUTER_BASE_URL", async () => {
    // A real server: model calls go through the adapters' own connection pool, not the global fetch.
    const urls: string[] = [];
    const server = createServer((req, res) => {
      urls.push(req.url ?? "");
      res.writeHead(400, { "content-type": "application/json" });
      res.end("{}");
    });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const port = (server.address() as AddressInfo).port;
    try {
      const models = defaultModelRegistry({
        ENGINE_OPENROUTER_BASE_URL: `http://127.0.0.1:${port}/api`,
      });
      const stream = models.stream(Context.background(), {
        provider: "openrouter",
        model: "m",
        apiKey: "k",
        maxTokens: 1,
        messages: [{ role: "user", content: "x" }],
      });
      await stream.result();
    } finally {
      await new Promise<void>((r) => server.close(() => r()));
    }
    expect(urls).toEqual(["/api/chat/completions"]);
  });
});
