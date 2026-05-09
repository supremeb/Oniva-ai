/**
 * Onboarding API functions for user registration and organization creation
 *
 * Public endpoints (no auth):
 * - checkEmail: Check if email is available for registration
 * - register: Register a new user (self-service or invited)
 * - checkSlug: Check if organization slug is available
 *
 * Protected endpoints (requires auth):
 * - createOrg: Create a new organization for existing user
 */
import { apiFetch } from './index';

// ============================================
// Types
// ============================================

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  settings?: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
}

export interface User {
  id: string;
  email: string;
  name?: string;
  role: string;
  is_active: boolean;
  auth_provider: string;
}

// Email Check
export interface EmailCheckResponse {
  available: boolean;
  has_pending_invitation: boolean;
  org_name?: string;
  tenant_slug?: string;
}

// Registration
export interface RegisterRequest {
  email: string;
  password: string;
  name: string;
  org_name?: string;
  org_slug?: string;
}

export interface RegisterResponse {
  user: User;
  tenant: Tenant;
  access_token: string;
  token_type: string;
  expires_in: number;
}

// Slug Check
export interface SlugCheckResponse {
  available: boolean;
  suggestions: string[];
}

// Create Organization
export interface CreateOrgResponse {
  tenant: Tenant;
  role: string;
}

// ============================================
// API Functions
// ============================================

/**
 * Check if email is available for registration.
 *
 * @param email - Email address to check
 * @returns Email availability status and invitation info
 */
export async function checkEmail(email: string): Promise<EmailCheckResponse> {
  return apiFetch<EmailCheckResponse>('/api/v1/onboarding/check-email', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

/**
 * Register a new user.
 *
 * Supports two flows:
 * 1. Invited user (has pending invitation) → joins org with pre-assigned role
 * 2. Self-service (no invitation) → creates new org and becomes owner
 *
 * @param data - Registration data
 * @returns User, tenant, and access token
 */
export async function register(data: RegisterRequest): Promise<RegisterResponse> {
  return apiFetch<RegisterResponse>('/api/v1/onboarding/register', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Check if organization slug is available.
 *
 * @param slug - Slug to check
 * @returns Availability status and suggestions if not available
 */
export async function checkSlug(slug: string): Promise<SlugCheckResponse> {
  return apiFetch<SlugCheckResponse>(`/api/v1/onboarding/check-slug/${encodeURIComponent(slug)}`);
}

/**
 * Create a new organization for existing user.
 *
 * Requires authentication. User becomes owner of the new org.
 *
 * @param name - Organization display name
 * @param slug - URL-safe organization identifier
 * @returns Created tenant and user's role
 */
export async function createOrg(name: string, slug: string): Promise<CreateOrgResponse> {
  return apiFetch<CreateOrgResponse>('/api/v1/onboarding/create-org', {
    method: 'POST',
    body: JSON.stringify({ name, slug }),
  });
}
