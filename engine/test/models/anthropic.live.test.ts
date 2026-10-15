/**
 * Live smoke test against the Anthropic API. Skipped unless ANTHROPIC_API_KEY
 * is set; costs a fraction of a cent on the summary model.
 */
import { describe, expect, it } from "vitest";
import { Context } from "../../src/core/context.js";
import { AnthropicProvider } from "../../src/models/anthropic.js";
import { ModelRegistry } from "../../src/models/stream.js";

const apiKey = process.env.ANTHROPIC_API_KEY;

describe("AnthropicProvider (live)", () => {
  it.skipIf(!apiKey)(
    "returns a one-word answer",
    async () => {
      const registry = new ModelRegistry().register(new AnthropicProvider());
      const result = await registry
        .resolveAndStream(
          Context.background(),
          async () => ({
            ok: true,
            provider: "anthropic",
            model: "claude-haiku-5-5",
            apiKey: apiKey as string,
            maxTokens: 2048,
            thinking: "low",
          }),
          {
            messages: [
              {
                role: "user",
                content: "Reply with exactly one word, no punctuation: the capital of France.",
              },
            ],
          },
        )
        .result();
      if (result.type !== "done") throw new Error(`live call failed: ${result.error.message}`);
      const text = result.message.blocks
        .map((b) => (b.type === "text" ? b.text : ""))
        .join("")
        .trim();
      expect(text.split(/\s+/)).toHaveLength(1);
      expect(text.toLowerCase()).toContain("paris");
      expect(result.usage.outputTokens).toBeGreaterThan(0);
    },
    60_000,
  );
});
