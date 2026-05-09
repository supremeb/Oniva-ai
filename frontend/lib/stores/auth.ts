/**
 * Auth store using Zustand - cookie-based authentication
 *
 * SECURITY: JWT tokens are stored in httpOnly cookies (set by backend).
 * This store only holds user info in memory (Zustand) — never in localStorage.
 * On mount, hydrate() calls GET /api/v1/auth/me to check auth state.
 * The `is_authenticated` cookie (non-httpOnly) provides a synchronous
 * routing guard before the async /me call completes.
 */
import { create } from 'zustand';
import type { AuthUser } from '@/lib/api/auth';
import { clearTenantData } from '@/lib/tenant-storage';
import { API_BASE_URL, attemptTokenRefresh } from '@/lib/api/index';

interface AuthState {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;

  // Actions
  setAuth: (user: AuthUser) => void;
  clearAuth: () => void;
  setLoading: (loading: boolean) => void;
  hydrate: () => void;
}

/**
 * Read a cookie value by name (for non-httpOnly cookies only).
 */
function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

let isHydrating = false;

export const useAuthStore = create<AuthState>()((set) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,

  setAuth: (user) => {
    set({ user, isAuthenticated: true, isLoading: false });
  },

  clearAuth: () => {
    // Clear tenant-specific app data from localStorage on logout
    if (typeof window !== 'undefined') {
      const currentUser = useAuthStore.getState().user;
      const tenantId = currentUser?.tenant_id;

      if (tenantId) {
        clearTenantData(tenantId);
      }

      // Clear legacy localStorage keys
      const legacyKeysToRemove = [
        // Auth tokens (clean up from pre-cookie migration)
        'access_token',
        'refresh_token',
        'auth_user',
        // App data
        'oniva-custom',
        'oniva-versions',
        'oniva-workflows',
        'agent_id_migration_map',
        'oniva-chat-sessions',
        'oniva-chat-panel',
        'oniva-chat',
        'oniva-ui',
        'oniva:bottom-panel:v2',
        'oniva:agent-studio-layout:v1',
      ];
      legacyKeysToRemove.forEach(key => localStorage.removeItem(key));
    }
    set({ user: null, isAuthenticated: false, isLoading: false });
  },

  setLoading: (loading) => set({ isLoading: loading }),

  // Hydrate auth state from backend via GET /api/v1/auth/me
  hydrate: () => {
    if (typeof window === 'undefined') {
      set({ isLoading: false });
      return;
    }

    // Guard against concurrent hydration calls
    if (isHydrating) return;
    isHydrating = true;

    const hasAuthCookie = getCookie('is_authenticated') === '1';

    /** Fetch /me and update store. Returns true on success. */
    const fetchMe = (): Promise<boolean> =>
      fetch(`${API_BASE_URL}/api/v1/auth/me`, { credentials: 'include' })
        .then(res => {
          if (!res.ok) throw new Error('Not authenticated');
          return res.json();
        })
        .then(user => {
          set({ user: user as AuthUser, isAuthenticated: true, isLoading: false });
          return true;
        });

    /** Attempt token refresh, then fetch /me */
    const tryRefreshThenFetchMe = () =>
      attemptTokenRefresh()
        .then(ok => {
          if (!ok) throw new Error('Refresh failed');
          return fetchMe();
        })
        .catch(() => set({ user: null, isAuthenticated: false, isLoading: false }))
        .finally(() => { isHydrating = false; });

    if (hasAuthCookie) {
      // Fast path: cookie present → verify with backend
      fetchMe()
        .catch(() => tryRefreshThenFetchMe())
        .finally(() => { isHydrating = false; });
    } else {
      // No is_authenticated cookie — try refresh token (httpOnly, still may be valid)
      tryRefreshThenFetchMe();
    }
  },
}));
