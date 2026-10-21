/**
 * Base API configuration and utilities
 *
 * SECURITY: Uses httpOnly cookie authentication (cookies sent automatically
 * with credentials: 'include'). CSRF protection via X-CSRF-Token header
 * on state-changing requests (POST/PUT/PATCH/DELETE).
 *
 * Features:
 * - Cookie-based auth with automatic CSRF token handling
 * - Automatic token refresh on 401 errors
 * - HTTP keep-alive for connection reuse
 * - Configurable timeouts and retries
 */

// API base URL. NEXT_PUBLIC_API_URL is inlined by Next.js at build time
// (static property access is required for inlining); falls back to local dev.
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export class APIError extends Error {
  constructor(
    message: string,
    public status?: number,
    public data?: unknown
  ) {
    super(message);
    this.name = 'APIError';
  }
}

/**
 * Read a cookie value by name (for non-httpOnly cookies only).
 */
function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

/** Methods that modify state and require CSRF protection */
const STATE_CHANGING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

// Token refresh state to prevent multiple concurrent refresh attempts
let isRefreshing = false;
let refreshPromise: Promise<boolean> | null = null;

/**
 * Attempt to refresh the access token via cookie-based refresh.
 * Returns true if successful, false otherwise.
 */
export async function attemptTokenRefresh(): Promise<boolean> {
  // Prevent multiple concurrent refresh attempts
  if (isRefreshing && refreshPromise) {
    return refreshPromise;
  }

  isRefreshing = true;
  refreshPromise = (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include', // Send refresh_token cookie
        body: JSON.stringify({}), // Body can be empty — refresh token comes from cookie
      });

      if (!response.ok) {
        return false;
      }

      // Cookies are set by the backend response automatically
      const data = await response.json();

      // Update in-memory user state if returned
      if (data.user && Object.keys(data.user).length > 0) {
        const { useAuthStore } = await import('@/lib/stores/auth');
        useAuthStore.getState().setAuth(data.user);
      }

      return true;
    } catch {
      // Network error during refresh - don't clear auth, might be temporary
      return false;
    } finally {
      isRefreshing = false;
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

/**
 * Create an AbortSignal with timeout
 */
function createTimeoutSignal(timeoutMs: number): AbortSignal {
  const controller = new AbortController();
  setTimeout(() => controller.abort(), timeoutMs);
  return controller.signal;
}

/**
 * Base fetch wrapper with cookie auth, CSRF protection, and automatic token refresh.
 *
 * Features:
 * - credentials: 'include' on all requests (cookies sent automatically)
 * - X-CSRF-Token header on state-changing requests
 * - HTTP keep-alive enabled
 * - Request timeouts (default 5s)
 * - Automatic token refresh on 401 errors
 */
export async function apiFetch<T>(
  endpoint: string,
  options: RequestInit & { timeout?: number; retries?: number; skipTokenRefresh?: boolean } = {}
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;
  const timeout = options.timeout ?? 5000;
  const maxRetries = options.retries ?? 0;
  const skipTokenRefresh = options.skipTokenRefresh ?? false;

  const makeRequest = async (isRetryAfterRefresh = false): Promise<T> => {
    const defaultHeaders: Record<string, string> = {
      'Connection': 'keep-alive',
    };

    // Only set Content-Type for non-FormData requests.
    // FormData needs the browser to auto-set multipart/form-data with boundary.
    if (!(options.body instanceof FormData)) {
      defaultHeaders['Content-Type'] = 'application/json';
    }

    // Add CSRF token for state-changing requests
    const method = (options.method || 'GET').toUpperCase();
    if (STATE_CHANGING_METHODS.has(method)) {
      const csrfToken = getCookie('csrf_token');
      if (csrfToken) {
        defaultHeaders['X-CSRF-Token'] = csrfToken;
      }
    }

    const config: RequestInit = {
      ...options,
      headers: {
        ...defaultHeaders,
        ...options.headers,
      },
      credentials: 'include', // Send cookies with every request
      keepalive: true,
    };

    let lastError: Error | null = null;

    // Retry loop for network errors
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        // Add timeout to request
        const timeoutSignal = createTimeoutSignal(timeout);
        const requestConfig = {
          ...config,
          signal: timeoutSignal,
        };

        const response = await fetch(url, requestConfig);

        if (!response.ok) {
          // Handle 401 Unauthorized - attempt token refresh
          if (response.status === 401 && !skipTokenRefresh && !isRetryAfterRefresh) {
            const refreshSuccessful = await attemptTokenRefresh();
            if (refreshSuccessful) {
              // Retry the original request with new cookies
              return makeRequest(true);
            }
            // Refresh failed — force clean logout to avoid broken semi-auth state
            const { useAuthStore } = await import('@/lib/stores/auth');
            useAuthStore.getState().clearAuth();
            window.location.href = '/login';
            throw new APIError('Session expired', 401);
          }

          // Handle CSRF 403 - refresh tokens (which refreshes CSRF cookie too), then retry
          if (response.status === 403 && !isRetryAfterRefresh) {
            let errorBody;
            try { errorBody = await response.clone().json(); } catch { /* ignore */ }
            const detail = typeof errorBody?.detail === 'string' ? errorBody.detail : '';
            if (detail.includes('CSRF')) {
              const refreshSuccessful = await attemptTokenRefresh();
              if (refreshSuccessful) {
                return makeRequest(true);
              }
            }
          }

          let errorData;
          try {
            errorData = await response.json();
          } catch {
            errorData = await response.text();
          }
          // Extract error message
          let errorMessage = `HTTP error ${response.status}`;
          if (typeof errorData === 'string') {
            errorMessage = errorData;
          } else if (errorData?.detail) {
            errorMessage = typeof errorData.detail === 'string'
              ? errorData.detail
              : JSON.stringify(errorData.detail);
          } else if (errorData?.message) {
            errorMessage = errorData.message;
          }
          throw new APIError(
            errorMessage,
            response.status,
            errorData
          );
        }

        // Handle 204 No Content
        if (response.status === 204) {
          return undefined as T;
        }

        return await response.json();
      } catch (error) {
        lastError = error as Error;

        // Don't retry on API errors or if this is the last attempt
        if (error instanceof APIError || attempt === maxRetries) {
          break;
        }

        // Wait before retrying (exponential backoff: 100ms, 200ms, 400ms...)
        await new Promise(resolve => setTimeout(resolve, 100 * Math.pow(2, attempt)));
      }
    }

    // Throw the last error
    if (lastError instanceof APIError) {
      throw lastError;
    }
    throw new APIError(
      lastError instanceof Error ? lastError.message : 'Network error occurred'
    );
  };

  return makeRequest();
}

// Re-export all API modules
export * from './evaluation';
export * from './volumes';
export * from './artifacts';
export * from './compliance';
export * from './knowledge-bases';
export * from './schedules';
export * from './inbox';
export * from './project-memory';
