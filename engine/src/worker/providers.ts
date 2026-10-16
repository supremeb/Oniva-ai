/**
 * The production model registry: Anthropic plus OpenRouter. The worker sends
 * the registered names to the backend policy as `supportedProviders`, so a
 * tenant whose only stored key is OpenRouter's is routed to "openrouter".
 */
import { AnthropicProvider } from "../models/anthropic.js";
import { OPENROUTER_BASE_URL, openRouterProvider } from "../models/openai-compatible.js";
import { ModelRegistry } from "../models/stream.js";

type Env = Readonly<Record<string, string | undefined>>;

export type ModelLog = (event: string, fields?: Record<string, unknown>) => void;

/**
 * `ENGINE_OPENROUTER_BASE_URL` overrides the OpenRouter endpoint. `log`
 * receives `model_retry` whenever the OpenRouter adapter retries a request.
 */
export function defaultModelRegistry(env: Env = {}, log?: ModelLog): ModelRegistry {
  const baseURL = env.ENGINE_OPENROUTER_BASE_URL?.trim() || OPENROUTER_BASE_URL;
  return new ModelRegistry().register(new AnthropicProvider()).register(
    openRouterProvider({
      baseURL,
      headers: { "X-Title": "Oniva" },
      retry: {
        onRetry: (info) =>
          log?.("model_retry", {
            provider: info.provider,
            model: info.model,
            attempt: info.attempt,
            delayMs: info.delayMs,
            kind: info.error.kind,
            status: info.error.status,
            error: info.error.message,
          }),
      },
    }),
  );
}
