/**
 * HTTP helpers for server tests: JSON requests and an SSE reader over fetch.
 */
import type { Harness } from "../../src/agent/harness.js";
import {
  createEngineServer,
  type EngineServer,
  type EngineServerOptions,
} from "../../src/server/http.js";
import type { WatchFrame } from "../../src/watch/stream.js";

export const TOKEN = "svc-token-1234567890";
export const TENANT = { "x-oniva-tenant": "t1", "x-oniva-project": "p1" };
export const AUTH = { authorization: `Bearer ${TOKEN}`, ...TENANT };

export interface Running {
  engine: EngineServer;
  url: string;
  close(): Promise<void>;
}

export async function serve(
  harness: Harness,
  options: Partial<Omit<EngineServerOptions, "harness">> = {},
): Promise<Running> {
  const engine = createEngineServer({
    harness,
    ownerId: "worker-a",
    serviceToken: TOKEN,
    heartbeatMs: 50,
    ...options,
  });
  const { url } = await engine.listen(0, "127.0.0.1");
  return { engine, url, close: () => engine.close() };
}

export interface JsonResponse {
  status: number;
  // biome-ignore lint/suspicious/noExplicitAny: test responses
  body: any;
  headers: Headers;
}

export async function call(
  url: string,
  method: string,
  path: string,
  options: { headers?: Record<string, string>; body?: unknown; raw?: string } = {},
): Promise<JsonResponse> {
  const init: RequestInit = { method, headers: { ...(options.headers ?? {}) } };
  if (options.raw !== undefined) {
    init.body = options.raw;
    (init.headers as Record<string, string>)["content-type"] = "application/json";
  } else if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
    (init.headers as Record<string, string>)["content-type"] = "application/json";
  }
  const res = await fetch(`${url}${path}`, init);
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null, headers: res.headers };
}

export interface SseEvent {
  id: string | null;
  event: string | null;
  data: string;
}

/** An open SSE stream: `next()` resolves the next event (comments are skipped). */
export class SseClient {
  readonly #reader: ReadableStreamDefaultReader<Uint8Array>;
  readonly #controller: AbortController;
  readonly #decoder = new TextDecoder();
  #buffer = "";
  readonly status: number;
  readonly headers: Headers;

  private constructor(res: Response, controller: AbortController) {
    this.status = res.status;
    this.headers = res.headers;
    this.#controller = controller;
    this.#reader = (res.body as ReadableStream<Uint8Array>).getReader();
  }

  static async open(url: string, headers: Record<string, string>): Promise<SseClient> {
    const controller = new AbortController();
    const res = await fetch(url, { headers, signal: controller.signal });
    return new SseClient(res, controller);
  }

  async next(timeoutMs = 5_000): Promise<SseEvent> {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const event = this.#take();
      if (event) return event;
      const left = deadline - Date.now();
      if (left <= 0) throw new Error("no SSE event in time");
      let timer: NodeJS.Timeout | undefined;
      const chunk = await Promise.race([
        this.#reader.read(),
        new Promise<null>((r) => {
          timer = setTimeout(() => r(null), left);
        }),
      ]);
      clearTimeout(timer);
      if (chunk === null) continue;
      if (chunk.done) throw new Error("SSE stream ended");
      this.#buffer += this.#decoder.decode(chunk.value, { stream: true });
    }
  }

  async frame(timeoutMs?: number): Promise<{ id: string | null; frame: WatchFrame }> {
    const e = await this.next(timeoutMs);
    return { id: e.id, frame: JSON.parse(e.data) as WatchFrame };
  }

  /** Frames already received or arriving within `quietMs`. */
  async frames(quietMs = 150): Promise<WatchFrame[]> {
    const out: WatchFrame[] = [];
    for (;;) {
      try {
        out.push((await this.frame(quietMs)).frame);
      } catch {
        return out;
      }
    }
  }

  close(): void {
    this.#controller.abort();
  }

  #take(): SseEvent | null {
    for (;;) {
      const end = this.#buffer.indexOf("\n\n");
      if (end < 0) return null;
      const block = this.#buffer.slice(0, end);
      this.#buffer = this.#buffer.slice(end + 2);
      const event: SseEvent = { id: null, event: null, data: "" };
      let hasData = false;
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;
        const i = line.indexOf(":");
        const field = i < 0 ? line : line.slice(0, i);
        const value = i < 0 ? "" : line.slice(i + 1).replace(/^ /, "");
        if (field === "id") event.id = value;
        else if (field === "event") event.event = value;
        else if (field === "data") {
          event.data += hasData ? `\n${value}` : value;
          hasData = true;
        }
      }
      if (hasData) return event;
    }
  }
}
