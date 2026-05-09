import { apiFetch } from './index';

/**
 * Workflow Version API types and functions
 * Supports versioning, drafts, and deployment similar to agent versioning
 */

// --- Types ---

export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version_number: number;
  version_tag: string | null;
  description: string | null;
  is_active: boolean;
  is_draft: boolean;
  node_count: number;
  edge_count: number;
  spec_hash: string | null;
  created_at: string;
  published_at: string | null;
  created_by: string | null;
}

export interface WorkflowVersionFull extends WorkflowVersion {
  spec: {
    nodes: unknown[];
    edges: unknown[];
  };
}

export interface CreateVersionRequest {
  description?: string;
  version_tag?: string;
  is_draft?: boolean;
}

export interface UpdateVersionRequest {
  description?: string;
  version_tag?: string;
}

export interface PublishRequest {
  version_tag: string;  // Required semantic version like "1.0.0"
  description?: string;
  change_type?: 'patch' | 'minor' | 'major';
}

export interface DraftStatus {
  has_draft: boolean;
  draft: WorkflowVersion | null;
  based_on_version: WorkflowVersion | null;
  latest_published: WorkflowVersion | null;
  suggested_versions: {
    patch: string;
    minor: string;
    major: string;
  };
}

export interface VersionDiff {
  version_a: number;
  version_b: number;
  nodes_added: string[];
  nodes_removed: string[];
  nodes_modified: string[];
  edges_added: string[];
  edges_removed: string[];
  summary: string;
}

// --- API Functions ---

/**
 * List all versions for a workflow
 */
export async function listWorkflowVersions(
  workflowId: string,
  options?: {
    includeDrafts?: boolean;
    limit?: number;
    offset?: number;
  }
): Promise<WorkflowVersion[]> {
  const params = new URLSearchParams();
  if (options?.includeDrafts !== undefined) {
    params.append('include_drafts', String(options.includeDrafts));
  }
  if (options?.limit) {
    params.append('limit', String(options.limit));
  }
  if (options?.offset) {
    params.append('offset', String(options.offset));
  }

  const queryString = params.toString();
  const url = `/api/v1/workflows/${workflowId}/versions${queryString ? `?${queryString}` : ''}`;

  return apiFetch<WorkflowVersion[]>(url);
}

/**
 * Get a specific version with full spec
 */
export async function getWorkflowVersion(
  workflowId: string,
  versionId: string
): Promise<WorkflowVersionFull> {
  return apiFetch<WorkflowVersionFull>(
    `/api/v1/workflows/${workflowId}/versions/${versionId}`
  );
}

/**
 * Get version by version number
 */
export async function getWorkflowVersionByNumber(
  workflowId: string,
  versionNumber: number
): Promise<WorkflowVersionFull> {
  return apiFetch<WorkflowVersionFull>(
    `/api/v1/workflows/${workflowId}/versions/by-number/${versionNumber}`
  );
}

/**
 * Get the currently active (deployed) version
 */
export async function getActiveWorkflowVersion(
  workflowId: string
): Promise<WorkflowVersionFull | null> {
  try {
    return await apiFetch<WorkflowVersionFull>(
      `/api/v1/workflows/${workflowId}/versions/active`
    );
  } catch (error: unknown) {
    // Return null if no active version found
    if ((error as { status?: number })?.status === 404) {
      return null;
    }
    throw error;
  }
}

/**
 * Get draft status including suggested next versions
 */
export async function getDraftStatus(
  workflowId: string
): Promise<DraftStatus> {
  return apiFetch<DraftStatus>(
    `/api/v1/workflows/${workflowId}/versions/draft`
  );
}

/**
 * Create a new version from current workflow state
 */
export async function createWorkflowVersion(
  workflowId: string,
  request: CreateVersionRequest = {}
): Promise<WorkflowVersion> {
  return apiFetch<WorkflowVersion>(
    `/api/v1/workflows/${workflowId}/versions`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Update version metadata (description, tag)
 */
export async function updateWorkflowVersion(
  workflowId: string,
  versionId: string,
  request: UpdateVersionRequest
): Promise<WorkflowVersion> {
  return apiFetch<WorkflowVersion>(
    `/api/v1/workflows/${workflowId}/versions/${versionId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Activate (deploy) a specific version
 * This also publishes the version if it was a draft
 */
export async function activateWorkflowVersion(
  workflowId: string,
  versionId: string
): Promise<WorkflowVersion> {
  return apiFetch<WorkflowVersion>(
    `/api/v1/workflows/${workflowId}/versions/${versionId}/activate`,
    {
      method: 'POST',
    }
  );
}

/**
 * Publish current workflow state as a new version with semantic versioning
 */
export async function publishWorkflow(
  workflowId: string,
  request: PublishRequest
): Promise<WorkflowVersion> {
  return apiFetch<WorkflowVersion>(
    `/api/v1/workflows/${workflowId}/versions/publish`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Rollback workflow to a specific version
 * Updates the current workflow spec to match the version's spec
 */
export async function rollbackToVersion(
  workflowId: string,
  versionId: string
): Promise<{
  success: boolean;
  message: string;
  version_number: number;
  workflow_id: string;
}> {
  return apiFetch(
    `/api/v1/workflows/${workflowId}/versions/${versionId}/rollback`,
    {
      method: 'POST',
    }
  );
}

/**
 * Compare two versions and return diff
 */
export async function compareWorkflowVersions(
  workflowId: string,
  versionA: number,
  versionB: number
): Promise<VersionDiff> {
  const params = new URLSearchParams({
    version_a: String(versionA),
    version_b: String(versionB),
  });

  return apiFetch<VersionDiff>(
    `/api/v1/workflows/${workflowId}/versions/compare?${params.toString()}`
  );
}

/**
 * Delete a version (only drafts or inactive versions can be deleted)
 */
export async function deleteWorkflowVersion(
  workflowId: string,
  versionId: string
): Promise<{ success: boolean; message: string }> {
  return apiFetch(
    `/api/v1/workflows/${workflowId}/versions/${versionId}`,
    {
      method: 'DELETE',
    }
  );
}

/**
 * Helper to get version status label
 */
export function getVersionStatusLabel(version: WorkflowVersion): string {
  if (version.is_active) return 'Active';
  if (version.is_draft) return 'Draft';
  return 'Published';
}

/**
 * Helper to get version status color
 */
export function getVersionStatusColor(version: WorkflowVersion): string {
  if (version.is_active) return 'green';
  if (version.is_draft) return 'yellow';
  return 'gray';
}
