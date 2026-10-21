/**
 * The HTTP client model adapters use by default.
 *
 * Model calls go through one dedicated connection pool that speaks HTTP/1.1
 * only. The global `fetch` of newer Node releases negotiates HTTP/2, and when a
 * provider ends a pooled HTTP/2 session the pool can keep handing out the dead
 * session (`ERR_HTTP2_INVALID_SESSION`): every retry then fails the same way
 * and the run fails. HTTP/1.1 also matches what the production image (Node 22)
 * has always used. Tests inject their own fetch and never reach this pool.
 */
import { Agent, fetch as poolFetch } from "undici";

const pool = new Agent({ allowH2: false, keepAliveTimeout: 10_000, keepAliveMaxTimeout: 60_000 });

/** `fetch` over the model connection pool. */
export const modelFetch = ((input: string | URL | Request, init?: RequestInit) =>
  poolFetch(
    input as never,
    { ...(init as object), dispatcher: pool } as never,
  )) as unknown as typeof fetch;
