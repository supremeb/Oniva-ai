import { describe, expect, it } from "vitest";
import { AnthropicProvider } from "../../src/models/anthropic.js";
import { openRouterProvider } from "../../src/models/openai-compatible.js";
import type { ModelRequest } from "../../src/models/types.js";

const base: ModelRequest = {
  model: "claude-opus-5-5",
  apiKey: "k",
  maxTokens: 10,
  messages: [
    { role: "system", text: "sys" },
    { role: "user", content: "q" },
  ],
};

describe("prompt-cache TTL", () => {
  it("anthropic: default system breakpoint is 5 minutes (no ttl field)", () => {
    const params = new AnthropicProvider().buildParams(base);
    expect(params.system).toEqual([
      { type: "text", text: "sys", cache_control: { type: "ephemeral" } },
    ]);
  });

  it("anthropic: cacheTtl 1h puts ttl on the system breakpoint only", () => {
    const params = new AnthropicProvider().buildParams({ ...base, cacheTtl: "1h" });
    expect(params.system).toEqual([
      { type: "text", text: "sys", cache_control: { type: "ephemeral", ttl: "1h" } },
    ]);
    const user = params.messages[0]?.content as Array<{ cache_control?: unknown }>;
    expect(user[0]?.cache_control).toEqual({ type: "ephemeral" });
  });

  it("anthropic: cacheTtl 1h is ignored when caching is off", () => {
    const params = new AnthropicProvider().buildParams({ ...base, cacheTtl: "1h", cache: false });
    expect(JSON.stringify(params.system)).not.toContain("cache_control");
  });

  it("openrouter: cacheTtl 1h puts ttl on the Claude system breakpoint", () => {
    const provider = openRouterProvider({ fetch: (async () => new Response("")) as typeof fetch });
    const params = provider.buildParams({
      ...base,
      model: "anthropic/claude-sonnet-5.5",
      cacheTtl: "1h",
    });
    expect(params.messages[0]).toEqual({
      role: "system",
      content: [{ type: "text", text: "sys", cache_control: { type: "ephemeral", ttl: "1h" } }],
    });
    expect(params.messages[1]).toEqual({
      role: "user",
      content: [{ type: "text", text: "q", cache_control: { type: "ephemeral" } }],
    });
  });
});
