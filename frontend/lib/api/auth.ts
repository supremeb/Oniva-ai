/**
 * Authentication API functions
 *
 * SECURITY: Tokens are in httpOnly cookies (set by backend).
 * These functions no longer read/write localStorage for tokens.
 * Cookies are sent automatically via credentials: 'include' in apiFetch.
 */
import { apiFetch } from './index';

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: string;
  tenant_id: string;
  tenant_slug: string;
  groups: string[];
  is_platform_operator: boolean;
  features: Record<string, boolean>;
}

export interface LoginResponse {
  access_token: string;
  refresh_token?: string;
  token_type: string;
  expires_in: number;
  refresh_token_expires_in?: number;
  user: AuthUser;
}

export interface AuthProvider {
  name: string;
  protocol: string;
  is_sso: boolean;
}

export interface ProvidersResponse {
  providers: AuthProvider[];
  default: string;
}

export interface StubUser {
  email: string;
  name: string;
  role: string;
  tenant_slug: string;
}

/**
 * Login with email and password.
 * Backend sets httpOnly cookies in the response.
 */
export async function login(
  email: string,
  password: string,
  provider?: string
): Promise<LoginResponse> {
  const body: Record<string, string> = { email, password };
  if (provider) {
    body.provider = provider;
  }
  return apiFetch<LoginResponse>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * Get available auth providers
 */
export async function getProviders(): Promise<ProvidersResponse> {
  return apiFetch<ProvidersResponse>('/api/v1/auth/providers');
}

/**
 * Get stub users for development
 */
export async function getStubUsers(): Promise<StubUser[]> {
  return apiFetch<StubUser[]>('/api/v1/auth/stub-users');
}

/**
 * Get current user info.
 * Auth cookie is sent automatically.
 */
export async function getCurrentUser(): Promise<AuthUser> {
  return apiFetch<AuthUser>('/api/v1/auth/me');
}

/**
 * Refresh the access token.
 * Refresh token is sent via cookie automatically.
 */
export async function refreshAccessToken(): Promise<LoginResponse | null> {
  try {
    return await apiFetch<LoginResponse>('/api/v1/auth/refresh', {
      method: 'POST',
      body: JSON.stringify({}),
      skipTokenRefresh: true, // Avoid infinite refresh loop
    });
  } catch {
    return null;
  }
}

/**
 * Logout — clear cookies via backend and revoke tokens.
 */
export async function logout(logoutAll: boolean = false): Promise<void> {
  try {
    await apiFetch('/api/v1/auth/logout', {
      method: 'POST',
      body: JSON.stringify({
        logout_all: logoutAll,
      }),
      skipTokenRefresh: true,
    });
  } catch {
    // Ignore logout errors — cookies will expire anyway
  }
}
