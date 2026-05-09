/**
 * Cross-tab auth coordination utilities.
 *
 * - acquireRefreshLock: Ensures only one tab refreshes tokens at a time
 *   using navigator.locks (with localStorage fallback for older browsers).
 * - AuthChannel: BroadcastChannel wrapper for cross-tab auth notifications.
 *   Messages are notification-only — no tokens or user data in messages.
 */

// ── Cross-tab refresh lock ──────────────────────────────────────────

const LOCK_NAME = 'token-refresh';
const LOCK_TIMEOUT_MS = 10_000;

// localStorage fallback constants
const LS_LOCK_KEY = 'token_refresh_lock';

interface LSLock {
  timestamp: number;
  tabId: string;
}

const TAB_ID = typeof crypto !== 'undefined'
  ? crypto.randomUUID()
  : Math.random().toString(36).slice(2);

/**
 * Acquire a cross-tab lock before performing token refresh.
 * Uses navigator.locks when available, falls back to localStorage timestamps.
 *
 * If the lock cannot be acquired within LOCK_TIMEOUT_MS, the callback
 * proceeds anyway — the server handles duplicate rotations gracefully.
 */
export async function acquireRefreshLock<T>(callback: () => Promise<T>): Promise<T | null> {
  if (typeof navigator !== 'undefined' && 'locks' in navigator) {
    return acquireWithNavigatorLocks(callback);
  }
  return acquireWithLocalStorage(callback);
}

async function acquireWithNavigatorLocks<T>(callback: () => Promise<T>): Promise<T | null> {
  try {
    const result = await Promise.race([
      navigator.locks.request(LOCK_NAME, async () => callback()),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), LOCK_TIMEOUT_MS)),
    ]);

    // On timeout, proceed anyway — server handles duplicate rotation
    if (result === null) {
      return callback();
    }
    return result;
  } catch {
    // Lock API error — proceed without lock
    return callback();
  }
}

async function acquireWithLocalStorage<T>(callback: () => Promise<T>): Promise<T | null> {
  if (typeof localStorage === 'undefined') {
    return callback();
  }

  const now = Date.now();

  // Check if another tab holds the lock
  try {
    const existing = localStorage.getItem(LS_LOCK_KEY);
    if (existing) {
      const lock: LSLock = JSON.parse(existing);
      if (lock.tabId !== TAB_ID && now - lock.timestamp < LOCK_TIMEOUT_MS) {
        // Another tab is refreshing — skip
        return null;
      }
    }
  } catch {
    // Corrupt lock data — proceed
  }

  // Acquire lock
  localStorage.setItem(LS_LOCK_KEY, JSON.stringify({ timestamp: now, tabId: TAB_ID }));

  try {
    return await callback();
  } finally {
    // Release lock
    try {
      const current = localStorage.getItem(LS_LOCK_KEY);
      if (current) {
        const lock: LSLock = JSON.parse(current);
        if (lock.tabId === TAB_ID) {
          localStorage.removeItem(LS_LOCK_KEY);
        }
      }
    } catch {
      localStorage.removeItem(LS_LOCK_KEY);
    }
  }
}

// ── Cross-tab auth channel ──────────────────────────────────────────

type AuthMessageType = 'auth_changed' | 'logout';

interface AuthMessage {
  type: AuthMessageType;
}

type AuthMessageHandler = (message: AuthMessage) => void;

/**
 * BroadcastChannel wrapper for cross-tab auth state notifications.
 *
 * Messages are notification-only — they carry a type string, never tokens
 * or user data, to avoid XSS amplification through channel injection.
 */
export class AuthChannel {
  private channel: BroadcastChannel | null = null;
  private handlers: AuthMessageHandler[] = [];

  constructor() {
    if (typeof BroadcastChannel === 'undefined') return;

    try {
      this.channel = new BroadcastChannel('auth');
      this.channel.onmessage = (event: MessageEvent<AuthMessage>) => {
        const msg = event.data;
        if (msg && (msg.type === 'auth_changed' || msg.type === 'logout')) {
          this.handlers.forEach((handler) => handler(msg));
        }
      };
    } catch {
      // BroadcastChannel not available — silent degradation
    }
  }

  /** Broadcast a notification to other tabs. */
  broadcast(type: AuthMessageType): void {
    try {
      this.channel?.postMessage({ type } satisfies AuthMessage);
    } catch {
      // Channel closed or unavailable
    }
  }

  /** Subscribe to cross-tab auth notifications. */
  subscribe(handler: AuthMessageHandler): () => void {
    this.handlers.push(handler);
    return () => {
      this.handlers = this.handlers.filter((h) => h !== handler);
    };
  }

  /** Close the channel and remove all handlers. */
  close(): void {
    this.handlers = [];
    try {
      this.channel?.close();
    } catch {
      // Already closed
    }
    this.channel = null;
  }
}

// Singleton instance — shared across the app
export const authChannel = new AuthChannel();
