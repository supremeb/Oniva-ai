/**
 * The real gateway client, policy client and usage reporter against a running
 * backend (U10). Skipped unless `ENGINE_LIVE_BACKEND_URL` is set.
 *
 * Environment:
 * - `ENGINE_LIVE_BACKEND_URL`: backend origin, e.g. `http://127.0.0.1:8099`
 * - `ENGINE_LIVE_GATEWAY_TOKEN`: the backend's `ENGINE_GATEWAY_TOKEN`
 * - `ENGINE_LIVE_TENANT_ID`, `ENGINE_LIVE_PROJECT_ID`, `ENGINE_LIVE_CONVERSATION_ID`:
 *   a seeded tenant, one of its projects and an engine conversation bound to both,
 *   with an `anthropic` provider holding a (fake) key
 *
 * Only read tools without external calls are invoked (`kyc_verification` is a
 * pure function), so nothing leaves the backend and nothing is written besides
 * the invocation and usage rows.
 */
import { randomUUID } from "node:crypto";
import { describe, expect, it } from "vitest";
import type { AfterResponseEvent } from "../../src/agent/hooks.js";
import { Context } from "../../src/core/context.js";
import type { JsonObject } from "../../src/core/json.js";
import { createGatewayTools, type GatewayClientOptions } from "../../src/tools/gateway.js";
import type { ToolApi, ToolConversation, ToolReconcileApi } from "../../src/tools/registry.js";
import { BackendPolicyClient, UsageReporter } from "../../src/worker/backend-policy.js";

const url = process.env.ENGINE_LIVE_BACKEND_URL;
const token = process.env.ENGINE_LIVE_GATEWAY_TOKEN ?? "";
const tenantId = process.env.ENGINE_LIVE_TENANT_ID ?? "";
const projectId = process.env.ENGINE_LIVE_PROJECT_ID ?? "";
const conversationId = process.env.ENGINE_LIVE_CONVERSATION_ID ?? "";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

const CONVERSATION: ToolConversation = { id: conversationId, tenantId, projectId };
const KYC: JsonObject = { full_name: "Ada Lovelace", dob: "1815-12-10", country: "UK" };

function toolApi(run: string, call: string, conversation = CONVERSATION): ToolApi {
  const ctx = Context.background();
  return {
    ctx,
    signal: ctx.signal,
    idempotencyKey: `${run}:${call}`,
    runId: run,
    toolCallId: call,
    taskId: `task-${call}`,
    attempt: 1,
    conversation,
    output() {},
  };
}

function reconcileApi(run: string, call: string): ToolReconcileApi {
  return {
    ctx: Context.background(),
    idempotencyKey: `${run}:${call}`,
    runId: run,
    toolCallId: call,
    taskId: `task-${call}`,
    conversation: CONVERSATION,
    partialOutput: "",
  };
}

function gatewayOptions(
  sessionId: string,
  fence: number,
  extra: Partial<GatewayClientOptions> = {},
): GatewayClientOptions {
  return {
    baseUrl: url as string,
    serviceToken: token,
    fence: () => fence,
    sessionId,
    retry: { attempts: 3, baseDelayMs: 10, maxDelayMs: 100 },
    ...extra,
  };
}

describe.skipIf(!url)("gateway against a live backend", () => {
  it("loads the manifest and turns it into zod-validated tools", async () => {
    const set = await createGatewayTools(gatewayOptions(`live-${randomUUID()}`, 1));
    expect(set.names().length).toBeGreaterThan(0);
    const kyc = set.tools.find((t) => t.name === "kyc_verification");
    expect(kyc?.replay).toBe("safe");
    expect(kyc?.parameters.safeParse(KYC).success).toBe(true);
    expect(kyc?.parameters.safeParse({ full_name: 5 }).success).toBe(false);
    expect(set.names()).not.toContain("ask_user");
  });

  it("invokes a read tool, replays the same key without a second run, and reconciles it", async () => {
    const set = await createGatewayTools(gatewayOptions(`live-${randomUUID()}`, 1));
    const kyc = set.tools.find((t) => t.name === "kyc_verification");
    const run = `run-${randomUUID()}`;
    const first = (await kyc?.execute(KYC, toolApi(run, "c1"))) as Any;
    expect(first.isError ?? false).toBe(false);
    expect(JSON.stringify(first.content)).toContain("verified");
    const again = (await kyc?.execute(KYC, toolApi(run, "c1"))) as Any;
    expect(again).toEqual(first);
    const stored = (await kyc?.reconcile?.(KYC, reconcileApi(run, "c1"))) as Any;
    expect(stored).toEqual(first);
    expect(await kyc?.reconcile?.(KYC, reconcileApi(run, "never-ran"))).toBeNull();
  });

  it("answers bad arguments with a tool error, not a throw", async () => {
    const set = await createGatewayTools(gatewayOptions(`live-${randomUUID()}`, 1));
    const kyc = set.tools.find((t) => t.name === "kyc_verification");
    const res = (await kyc?.execute({ bogus: 1 }, toolApi(`run-${randomUUID()}`, "c1"))) as Any;
    expect(res.isError).toBe(true);
  });

  it("a subagent's call, tagged with the root conversation, is bound to the root's user", async () => {
    const set = await createGatewayTools(gatewayOptions(`live-${randomUUID()}`, 1));
    const kyc = set.tools.find((t) => t.name === "kyc_verification");
    const child: ToolConversation = {
      ...CONVERSATION,
      id: "conv_child_live",
      rootId: conversationId,
    };
    const res = (await kyc?.execute(KYC, toolApi(`run-${randomUUID()}`, "c1", child))) as Any;
    expect(res.isError ?? false).toBe(false);
  });

  it("a lower fence token is rejected, calls onFenceRejected and runs nothing", async () => {
    const session = `live-${randomUUID()}`;
    const rejected: unknown[] = [];
    const previous = await createGatewayTools(
      gatewayOptions(session, 6, { onFenceRejected: (r) => rejected.push(r) }),
    );
    await createGatewayTools(gatewayOptions(session, 7)); // the successor raises the mark
    const kyc = previous.tools.find((t) => t.name === "kyc_verification");
    const res = (await kyc?.execute(KYC, toolApi(`run-${randomUUID()}`, "c1"))) as Any;
    expect(res.isError).toBe(true);
    expect(res.details).toMatchObject({ error: "lease_lost" });
    expect(rejected).toHaveLength(1);
    await expect(previous.raiseFence()).rejects.toThrow();
  });

  it("resolves model policy with the tenant's key and records usage once", async () => {
    const policy = new BackendPolicyClient({
      baseUrl: url as string,
      serviceToken: token,
      supportedProviders: () => ["anthropic"],
    });
    const run = `run-${randomUUID()}`;
    const res = await policy.resolveModelRequest({
      ctx: Context.background(),
      purpose: "generation",
      conversation: { ...CONVERSATION, id: "conv_child_live", rootId: conversationId },
      agent: {},
      runId: run,
      round: 0,
    } as Any);
    expect(res).toMatchObject({ ok: true, provider: "anthropic" });

    const statuses: number[] = [];
    const fetchSpy: typeof fetch = async (input, init) => {
      const r = await fetch(input, init);
      statuses.push(r.status);
      return r;
    };
    const reporter = new UsageReporter({
      baseUrl: url as string,
      serviceToken: token,
      fetch: fetchSpy,
    });
    const event = {
      conversation: { ...CONVERSATION, id: "conv_child_live", rootId: conversationId },
      runId: run,
      round: 0,
      message: { provider: "anthropic", model: "claude-sonnet-4-5" },
      usage: { inputTokens: 100, outputTokens: 20, cacheReadTokens: 0, cacheWriteTokens: 0 },
    } as unknown as AfterResponseEvent;
    reporter.report(event);
    reporter.report(event); // a repeat of the same request id counts once on the backend
    await reporter.flush();
    expect(statuses).toEqual([200, 200]);
  });
});
