/**
 * User Management API functions
 */
import { apiFetch } from './index';

// =========================================
// Types
// =========================================

export interface User {
  id: string;
  email: string;
  name: string | null;
  role: 'owner' | 'admin' | 'member' | 'viewer';
  is_active: boolean;
  auth_provider: string;
  added_by: string | null;
  added_at: string | null;
  created_at: string;
}

export interface PendingUser {
  id: string;
  email: string;
  role: 'admin' | 'member' | 'viewer';
  added_by: string | null;
  created_at: string;
}

export interface UsersListResponse {
  users: User[];
  total: number;
}

export interface PendingUsersListResponse {
  pending_users: PendingUser[];
  total: number;
}

export interface CheckEmailResponse {
  can_signup: boolean;
  message: string;
}

export interface SignupResponse {
  message: string;
  user: {
    id: string;
    email: string;
    name: string;
    role: string;
    tenant_id: string;
  };
}

// =========================================
// User Management API
// =========================================

/**
 * List users in the organization
 */
export async function listUsers(params?: {
  search?: string;
  role?: string;
  is_active?: boolean;
  limit?: number;
  offset?: number;
}): Promise<UsersListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.search) searchParams.set('search', params.search);
  if (params?.role) searchParams.set('role', params.role);
  if (params?.is_active !== undefined) searchParams.set('is_active', String(params.is_active));
  if (params?.limit) searchParams.set('limit', String(params.limit));
  if (params?.offset) searchParams.set('offset', String(params.offset));

  const query = searchParams.toString();
  return apiFetch<UsersListResponse>(`/api/v1/users${query ? `?${query}` : ''}`);
}

/**
 * Get a specific user
 */
export async function getUser(userId: string): Promise<User> {
  return apiFetch<User>(`/api/v1/users/${userId}`);
}

/**
 * Update user profile
 */
export async function updateUser(userId: string, data: { name?: string }): Promise<User> {
  return apiFetch<User>(`/api/v1/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Change user role
 */
export async function changeUserRole(userId: string, role: string): Promise<User> {
  return apiFetch<User>(`/api/v1/users/${userId}/role`, {
    method: 'POST',
    body: JSON.stringify({ role }),
  });
}

/**
 * Deactivate a user
 */
export async function deactivateUser(userId: string): Promise<User> {
  return apiFetch<User>(`/api/v1/users/${userId}/deactivate`, {
    method: 'POST',
  });
}

/**
 * Reactivate a user
 */
export async function reactivateUser(userId: string): Promise<User> {
  return apiFetch<User>(`/api/v1/users/${userId}/reactivate`, {
    method: 'POST',
  });
}

/**
 * Admin reset user password
 */
export async function adminResetPassword(userId: string, newPassword: string): Promise<User> {
  return apiFetch<User>(`/api/v1/users/${userId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ new_password: newPassword }),
  });
}

// =========================================
// Pending Users API
// =========================================

/**
 * List pending users
 */
export async function listPendingUsers(params?: {
  limit?: number;
  offset?: number;
}): Promise<PendingUsersListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.limit) searchParams.set('limit', String(params.limit));
  if (params?.offset) searchParams.set('offset', String(params.offset));

  const query = searchParams.toString();
  return apiFetch<PendingUsersListResponse>(`/api/v1/pending-users${query ? `?${query}` : ''}`);
}

/**
 * Add a pending user
 */
export async function addPendingUser(email: string, role: string): Promise<PendingUser> {
  return apiFetch<PendingUser>('/api/v1/pending-users', {
    method: 'POST',
    body: JSON.stringify({ email, role }),
  });
}

/**
 * Remove a pending user
 */
export async function deletePendingUser(pendingUserId: string): Promise<void> {
  await apiFetch(`/api/v1/pending-users/${pendingUserId}`, {
    method: 'DELETE',
  });
}

// =========================================
// Auth/Signup API
// =========================================

/**
 * Check if an email can sign up
 */
export async function checkEmail(email: string): Promise<CheckEmailResponse> {
  return apiFetch<CheckEmailResponse>(`/api/v1/auth/check-email/${encodeURIComponent(email)}`);
}

/**
 * Sign up a new user
 */
export async function signup(email: string, password: string, name: string): Promise<SignupResponse> {
  return apiFetch<SignupResponse>('/api/v1/auth/signup', {
    method: 'POST',
    body: JSON.stringify({ email, password, name }),
  });
}

/**
 * Change password (authenticated user)
 */
export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await apiFetch('/api/v1/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}
