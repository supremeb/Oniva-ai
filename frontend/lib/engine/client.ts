/**
 * Browser client for /api/v1/engine/conversations. JSON routes go through
 * apiFetch (cookie auth, CSRF, one 401 refresh); the watch stream uses a raw
 * fetch with the same refresh path because apiFetch parses JSON.
 */
import { API_BASE_URL, APIError, apiFetch, attemptTokenRefresh } from '@/lib/api';

const BASE = '/api/v1/engine/conversations';

export interface SubmitInput {
  requestId: string;
  text?: string;
  content?: unknown;
  whenBusy?: 'followUp' | 'steer' | 'reject';
}

/** What the composer lets a user pick; snake_case matches the backend `Selections` model. */
export interface ChatSelectionsInput {
  agent_id?: string;
  skill_ids?: string[];
  connector_ids?: string[];
  knowledge_base_ids?: string[];
  web_search?: boolean;
  specific_sites?: string[];
}

export interface CreateResult {
  conversationId: string;
  projectId: string;
}

/** A non-OK response from the watch route. */
export class WatchHttpError extends Error {
  constructor(
    readonly status: number,
    readonly retryAfterMs: number | null
  ) {
    super(`watch failed with HTTP ${status}`);
    this.name = 'WatchHttpError';
  }
}

export interface ClientOptions {
  /** Delays between submit retries (ms). Default 0.5 s doubling, 4 attempts. */
  submitBackoffMs?: number[];
  sleep?: (ms: number) => Promise<void>;
}

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const isRetryableSubmit = (e: unknown): boolean =>
  e instanceof APIError && (e.status === undefined || e.status === 503);

function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

export class EngineConversationClient {
  private readonly backoff: number[];
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(opts: ClientOptions = {}) {
    this.backoff = opts.submitBackoffMs ?? [500, 1000, 2000];
    this.sleep = opts.sleep ?? defaultSleep;
  }

  /** Create a conversation. The server builds the agent config from the selections (KTD2). */
  create(projectId: string, selections?: ChatSelectionsInput): Promise<CreateResult> {
    return apiFetch<CreateResult>(BASE, {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, ...(selections ? { selections } : {}) }),
      timeout: 15000,
    });
  }

  /** Change the conversation's skill/connector/KB/web selections mid-conversation. */
  updateSelections(conversationId: string, selections: ChatSelectionsInput): Promise<unknown> {
    return apiFetch<unknown>(`${BASE}/${encodeURIComponent(conversationId)}/selections`, {
      method: 'PATCH',
      body: JSON.stringify(selections),
    });
  }

  /**
   * Submit with the same `requestId` and Idempotency-Key on every attempt, so a
   * retry after a timeout or 503 can never duplicate the input.
   */
  async submit(conversationId: string, input: SubmitInput): Promise<unknown> {
    const body = JSON.stringify(input);
    for (let attempt = 0; ; attempt++) {
      try {
        return await apiFetch<unknown>(`${BASE}/${encodeURIComponent(conversationId)}/submissions`, {
          method: 'POST',
          headers: { 'Idempotency-Key': input.requestId },
          body,
          timeout: 15000,
        });
      } catch (e) {
        if (!isRetryableSubmit(e) || attempt >= this.backoff.length) throw e;
        await this.sleep(this.backoff[attempt] as number);
      }
    }
  }

  abort(conversationId: string, reason?: string): Promise<unknown> {
    return apiFetch<unknown>(`${BASE}/${encodeURIComponent(conversationId)}/abort`, {
      method: 'POST',
      body: JSON.stringify(reason ? { reason } : {}),
    });
  }

  /** Withdraw a still-queued input. Rejects with a 409 APIError once it started. */
  cancel(conversationId: string, submissionId: string): Promise<unknown> {
    return apiFetch<unknown>(
      `${BASE}/${encodeURIComponent(conversationId)}/submissions/${encodeURIComponent(submissionId)}`,
      { method: 'DELETE' }
    );
  }

  decide(
    conversationId: string,
    toolTaskId: string,
    decision: 'approve' | 'deny',
    note?: string
  ): Promise<unknown> {
    return apiFetch<unknown>(
      `${BASE}/${encodeURIComponent(conversationId)}/approvals/${encodeURIComponent(toolTaskId)}/decision`,
      { method: 'POST', body: JSON.stringify({ decision, ...(note ? { note } : {}) }) }
    );
  }

  /** Answer an `ask_user` question. `text` is free text; `optionIds` the chosen options. */
  answer(
    conversationId: string,
    toolTaskId: string,
    answer: { optionIds?: string[]; text?: string }
  ): Promise<unknown> {
    return apiFetch<unknown>(
      `${BASE}/${encodeURIComponent(conversationId)}/questions/${encodeURIComponent(toolTaskId)}/answer`,
      { method: 'POST', body: JSON.stringify({ answer }) }
    );
  }

  /**
   * Open the watch stream. `cursor` resumes via Last-Event-ID; null asks for a
   * snapshot. One 401 triggers a token refresh and a single retry.
   */
  async watch(conversationId: string, cursor: number | null, signal: AbortSignal): Promise<Response> {
    const url = `${API_BASE_URL}${BASE}/${encodeURIComponent(conversationId)}/watch`;
    for (let attempt = 0; ; attempt++) {
      const headers: Record<string, string> = { Accept: 'text/event-stream' };
      if (cursor !== null) headers['Last-Event-ID'] = String(cursor);
      const csrf = getCookie('csrf_token');
      if (csrf) headers['X-CSRF-Token'] = csrf;
      const res = await fetch(url, { headers, credentials: 'include', signal, cache: 'no-store' });
      if (res.status === 401 && attempt === 0 && (await attemptTokenRefresh())) continue;
      if (!res.ok || !res.body) {
        const ra = Number(res.headers.get('Retry-After'));
        throw new WatchHttpError(res.status, Number.isFinite(ra) && ra > 0 ? ra * 1000 : null);
      }
      return res;
    }
  }
}
