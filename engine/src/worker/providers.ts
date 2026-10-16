/**
 * The production model registry: Anthropic plus OpenRouter. The worker sends
 * the registered names to the backend policy as `supportedProviders`, so a
 * tenant whose only stored key is OpenRouter's is routed to "openrouter".
 */
import { AnthropicProvider } from "../models/anthropic.js";
import { OPENROUTER_BASE_URL, openRouterProvider } from "../models/openai-compatible.js";
import { ModelRegistry } from "../models/stream.js";

type Env = Readonly<Record<string, string | undefined>>;

/** `ENGINE_OPENROUTER_BASE_URL` overrides the OpenRouter endpoint. */
export function defaultModelRegistry(env: Env = {}): ModelRegistry {
  const baseURL = env.ENGINE_OPENROUTER_BASE_URL?.trim() || OPENROUTER_BASE_URL;
  return new ModelRegistry().register(new AnthropicProvider()).register(
    openRouterProvider({
      baseURL,
      headers: { "X-Title": "Oniva" },
    }),
  );
}
