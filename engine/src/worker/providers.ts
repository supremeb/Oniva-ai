/**
 * The production model registry: Anthropic plus OpenRouter. The worker sends
 * the registered names to the backend policy as `supportedProviders`, so a
 * tenant whose only stored key is OpenRouter's is routed to "openrouter".
 */
import { AnthropicProvider } from "../models/anthropic.js";
import { OPENROUTER_BASE_URL, openRouterProvider } from "../models/openai-compatible.js";
import { DEFAULT_FIRST_BYTE_TIMEOUT_MS, DEFAULT_IDLE_TIMEOUT_MS } from "../models/retry.js";
import { ModelRegistry } from "../models/stream.js";

type Env = Readonly<Record<string, string | undefined>>;

export type ModelLog = (event: string, fields?: Record<string, unknown>) => void;

/** The OpenRouter request deadlines (KTD5). */
export interface ModelTimeouts {
  firstByteTimeoutMs: number;
  idleTimeoutMs: number;
}

const DEFAULT_TIMEOUTS: ModelTimeouts = {
  firstByteTimeoutMs: DEFAULT_FIRST_BYTE_TIMEOUT_MS,
  idleTimeoutMs: DEFAULT_IDLE_TIMEOUT_MS,
};

/**
 * `ENGINE_OPENROUTER_BASE_URL` overrides the OpenRouter endpoint. `log`
 * receives `model_retry` whenever the OpenRouter adapter retries a request.
 * `timeouts` bounds a hung OpenRouter request (defaults: 120 s each).
 */
export function defaultModelRegistry(
  env: Env = {},
  log?: ModelLog,
  timeouts: ModelTimeouts = DEFAULT_TIMEOUTS,
): ModelRegistry {
  const baseURL = env.ENGINE_OPENROUTER_BASE_URL?.trim() || OPENROUTER_BASE_URL;
  return new ModelRegistry().register(new AnthropicProvider()).register(
    openRouterProvider({
      baseURL,
      headers: { "X-Title": "Oniva" },
      retry: {
        firstByteTimeoutMs: timeouts.firstByteTimeoutMs,
        idleTimeoutMs: timeouts.idleTimeoutMs,
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
