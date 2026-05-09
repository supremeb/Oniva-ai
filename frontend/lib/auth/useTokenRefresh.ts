/**
 * Global proactive token refresh hook.
 *
 * Refreshes the access token at 80% of TTL (24 min for 30-min tokens)
 * using cross-tab locking to prevent duplicate rotations. Also handles
 * visibility-based refresh when a user returns to a hidden tab.
 *
 * Consumed by AuthProvider — no rendering logic, pure side-effect hook.
 */
'use client';

import { useEffect, useRef, useCallback } from 'react';
import { useAuthStore } from '@/lib/stores/auth';
import { attemptTokenRefresh } from '@/lib/api';
import { acquireRefreshLock, authChannel } from '@/lib/auth/cross-tab';

// Coordinated constants — must match backend ACCESS_TOKEN_EXPIRE_MINUTES
const ACCESS_TOKEN_TTL_MS = 30 * 60 * 1000; // 30 minutes
const REFRESH_INTERVAL_MS = ACCESS_TOKEN_TTL_MS * 0.8; // 24 minutes

// Visibility-based refresh thresholds
const VISIBILITY_DEBOUNCE_MS = 5_000; // 5 seconds
const VISIBILITY_MIN_AGE_MS = ACCESS_TOKEN_TTL_MS * 0.5; // 15 minutes — skip if token is fresher

/**
 * Attempt a token refresh with cross-tab lock coordination.
 * Returns true if refresh succeeded, false otherwise.
 */
async function coordinatedRefresh(): Promise<boolean> {
  const result = await acquireRefreshLock(async () => {
    const success = await attemptTokenRefresh();
    if (success) {
      authChannel.broadcast('auth_changed');
    }
    return success;
  });
  // null means another tab holds the lock — treat as success (they're refreshing)
  return result ?? true;
}

export function useTokenRefresh() {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const clearAuth = useAuthStore((state) => state.clearAuth);
  const lastRefreshRef = useRef<number>(Date.now());
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Graceful logout on unrecoverable refresh failure (401 from refresh endpoint)
  const handleForcedLogout = useCallback(() => {
    clearAuth();
    window.location.href = '/login';
  }, [clearAuth]);

  // ── Proactive interval refresh (Unit 4) ────────────────────────
  useEffect(() => {
    if (!isAuthenticated) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    lastRefreshRef.current = Date.now();

    intervalRef.current = setInterval(async () => {
      await coordinatedRefresh();
      lastRefreshRef.current = Date.now();
    }, REFRESH_INTERVAL_MS);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [isAuthenticated]);

  // ── Visibility-based refresh (Unit 5) ──────────────────────────
  useEffect(() => {
    if (!isAuthenticated) return;

    const handleVisibility = async () => {
      if (document.visibilityState !== 'visible') return;

      const timeSinceLastRefresh = Date.now() - lastRefreshRef.current;

      // Debounce: skip if refreshed very recently (rapid tab switching)
      if (timeSinceLastRefresh < VISIBILITY_DEBOUNCE_MS) return;

      // Skip if token is still young (under 50% of TTL)
      if (timeSinceLastRefresh < VISIBILITY_MIN_AGE_MS) return;

      const success = await coordinatedRefresh();
      if (success) {
        lastRefreshRef.current = Date.now();
        return;
      }

      // First attempt failed — retry once after 2 seconds
      await new Promise((r) => setTimeout(r, 2_000));
      const retrySuccess = await coordinatedRefresh();
      if (retrySuccess) {
        lastRefreshRef.current = Date.now();
        return;
      }

      // Both attempts failed — don't force-logout here.
      // The reactive 401 interceptor in apiFetch handles logout
      // on the user's next actual API call. This avoids unnecessary
      // logouts on transient network errors.
    };

    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleVisibility);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleVisibility);
    };
  }, [isAuthenticated, handleForcedLogout]);

  // ── Cross-tab auth channel listener ────────────────────────────
  useEffect(() => {
    if (!isAuthenticated) return;

    const unsubscribe = authChannel.subscribe((message) => {
      if (message.type === 'logout') {
        clearAuth();
        window.location.href = '/login';
      } else if (message.type === 'auth_changed') {
        // Another tab refreshed — update our timestamp
        lastRefreshRef.current = Date.now();
      }
    });

    return unsubscribe;
  }, [isAuthenticated, clearAuth]);
}
