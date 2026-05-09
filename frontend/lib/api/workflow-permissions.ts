/**
 * Workflow Permissions API Client
 *
 * Enterprise feature: Workflow sharing and collaboration with granular permission controls.
 * Supports sharing with users, roles, and organization-wide access.
 */

import { apiFetch } from './index';

// --- Types ---

export type GranteeType = 'user' | 'role' | 'organization';
export type PermissionLevel = 'view' | 'execute' | 'edit' | 'admin';
export type GrantSource = 'direct' | 'role' | 'organization' | 'owner';

export interface WorkflowPermission {
  id: string;
  workflow_id: string;
  grantee_type: GranteeType;
  grantee_id: string;
  permission: PermissionLevel;
  granted_by: string | null;
  granted_at: string;
  expires_at: string | null;
  is_expired: boolean;
  created_at: string;
  // Enriched fields
  grantee_name?: string;
  grantee_email?: string;
  granter_name?: string;
}

export interface WorkflowShareSummary {
  workflow_id: string;
  owner_id: string;
  owner_name: string | null;
  is_shared: boolean;
  share_count: number;
  org_wide: boolean;
  org_permission: PermissionLevel | null;
  roles: string[];
  user_count: number;
  permissions: WorkflowPermission[];
}

export interface SharedWorkflow {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  owner_name: string | null;
  permission: PermissionLevel;
  grant_source: GrantSource;
  shared_at: string;
  expires_at: string | null;
}

export interface CheckPermissionResult {
  has_permission: boolean;
  reason: string;
  effective_permission: PermissionLevel | null;
  grant_source: GrantSource | null;
}

// --- Request Types ---

export interface CreatePermissionRequest {
  grantee_type: GranteeType;
  grantee_id: string;
  permission: PermissionLevel;
  expires_at?: string | null;
}

export interface UpdatePermissionRequest {
  permission?: PermissionLevel;
  expires_at?: string | null;
}

export interface BulkCreatePermissionsRequest {
  permissions: CreatePermissionRequest[];
}

export interface BulkDeletePermissionsRequest {
  permission_ids: string[];
}

export interface CheckPermissionRequest {
  user_id: string;
  user_role: string;
  required_permission: PermissionLevel;
}

// --- API Functions ---

/**
 * List all permissions for a workflow
 */
export async function listWorkflowPermissions(
  workflowId: string
): Promise<WorkflowPermission[]> {
  return apiFetch<WorkflowPermission[]>(`/api/v1/workflows/${workflowId}/permissions`);
}

/**
 * Create a new permission (share workflow)
 */
export async function createWorkflowPermission(
  workflowId: string,
  request: CreatePermissionRequest
): Promise<WorkflowPermission> {
  return apiFetch<WorkflowPermission>(
    `/api/v1/workflows/${workflowId}/permissions`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Bulk create permissions
 */
export async function bulkCreatePermissions(
  workflowId: string,
  request: BulkCreatePermissionsRequest
): Promise<WorkflowPermission[]> {
  return apiFetch<WorkflowPermission[]>(
    `/api/v1/workflows/${workflowId}/permissions/bulk`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Update a permission
 */
export async function updateWorkflowPermission(
  workflowId: string,
  permissionId: string,
  request: UpdatePermissionRequest
): Promise<WorkflowPermission> {
  return apiFetch<WorkflowPermission>(
    `/api/v1/workflows/${workflowId}/permissions/${permissionId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Delete a permission (unshare)
 */
export async function deleteWorkflowPermission(
  workflowId: string,
  permissionId: string
): Promise<void> {
  await apiFetch<void>(
    `/api/v1/workflows/${workflowId}/permissions/${permissionId}`,
    { method: 'DELETE' }
  );
}

/**
 * Bulk delete permissions
 */
export async function bulkDeletePermissions(
  workflowId: string,
  request: BulkDeletePermissionsRequest
): Promise<void> {
  await apiFetch<void>(
    `/api/v1/workflows/${workflowId}/permissions/bulk`,
    {
      method: 'DELETE',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Get share summary for a workflow
 */
export async function getWorkflowShareSummary(
  workflowId: string
): Promise<WorkflowShareSummary> {
  return apiFetch<WorkflowShareSummary>(`/api/v1/workflows/${workflowId}/share-summary`);
}

/**
 * Check if a user has a specific permission on a workflow
 */
export async function checkWorkflowPermission(
  workflowId: string,
  request: CheckPermissionRequest
): Promise<CheckPermissionResult> {
  return apiFetch<CheckPermissionResult>(
    `/api/v1/workflows/${workflowId}/check-permission`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

/**
 * List workflows shared with the current user
 */
export async function listSharedWorkflows(
  skip = 0,
  limit = 50
): Promise<SharedWorkflow[]> {
  const params = new URLSearchParams({
    skip: String(skip),
    limit: String(limit),
  });
  return apiFetch<SharedWorkflow[]>(`/api/v1/workflows/shared-with-me?${params}`);
}

// --- Helper Functions ---

/**
 * Permission level hierarchy check
 */
export function permissionIncludes(
  held: PermissionLevel,
  required: PermissionLevel
): boolean {
  const hierarchy: Record<PermissionLevel, number> = {
    view: 0,
    execute: 1,
    edit: 2,
    admin: 3,
  };
  return hierarchy[held] >= hierarchy[required];
}

/**
 * Get display name for permission level
 */
export function getPermissionDisplayName(permission: PermissionLevel): string {
  const names: Record<PermissionLevel, string> = {
    view: 'View Only',
    execute: 'Can Execute',
    edit: 'Can Edit',
    admin: 'Full Access',
  };
  return names[permission];
}

/**
 * Get description for permission level
 */
export function getPermissionDescription(permission: PermissionLevel): string {
  const descriptions: Record<PermissionLevel, string> = {
    view: 'Can view workflow details and execution history',
    execute: 'Can run the workflow (includes view)',
    edit: 'Can modify the workflow (includes execute)',
    admin: 'Full control including sharing and deletion',
  };
  return descriptions[permission];
}

/**
 * Get display name for grantee type
 */
export function getGranteeTypeDisplayName(type: GranteeType): string {
  const names: Record<GranteeType, string> = {
    user: 'User',
    role: 'Role',
    organization: 'Everyone',
  };
  return names[type];
}

/**
 * Get icon name for grantee type (using Phosphor icon names)
 */
export function getGranteeTypeIcon(type: GranteeType): string {
  const icons: Record<GranteeType, string> = {
    user: 'User',
    role: 'UsersThree',
    organization: 'Buildings',
  };
  return icons[type];
}
