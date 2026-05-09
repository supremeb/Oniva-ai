/**
 * API functions for agent versions (sequential integer versioning)
 */
import { apiFetch } from './index';
import type {
  AgentVersion,
  AgentVersionCreate,
  AgentVersionListResponse,
  DraftStatusResponse,
  AgentAlias,
} from '@/lib/types/chat-panels';

/**
 * Create a new agent version (sequential: v1, v2, v3...)
 */
export async function createAgentVersion(data: AgentVersionCreate): Promise<AgentVersion> {
  return apiFetch<AgentVersion>('/api/v1/agent-versions/', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Get all versions for an agent
 */
export async function getAgentVersions(agentId: string): Promise<AgentVersionListResponse> {
  return apiFetch<AgentVersionListResponse>(`/api/v1/agent-versions/agent/${agentId}`);
}

/**
 * Get draft change status for an agent
 */
export async function getDraftStatus(agentId: string): Promise<DraftStatusResponse> {
  return apiFetch<DraftStatusResponse>(`/api/v1/agent-versions/agent/${agentId}/draft-status`);
}

/**
 * Get a specific version
 */
export async function getAgentVersion(versionId: string): Promise<AgentVersion> {
  return apiFetch<AgentVersion>(`/api/v1/agent-versions/${versionId}`);
}

/**
 * Update a version (notes, name)
 */
export async function updateAgentVersion(
  versionId: string,
  data: { notes?: string; name?: string }
): Promise<AgentVersion> {
  return apiFetch<AgentVersion>(`/api/v1/agent-versions/${versionId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a version
 */
export async function deleteAgentVersion(versionId: string): Promise<void> {
  return apiFetch<void>(`/api/v1/agent-versions/${versionId}`, {
    method: 'DELETE',
  });
}

/**
 * Deploy a version (creates/updates PRODUCTION alias)
 */
export async function deployAgentVersion(versionId: string): Promise<{
  success: boolean;
  version_number?: number;
  legacy_version?: string;
  deployed_at: string;
  message: string;
}> {
  return apiFetch('/api/v1/agent-versions/deploy', {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId }),
  });
}

/**
 * Compare two versions
 */
export async function compareVersions(
  versionA: string,
  versionB: string
): Promise<{
  version_a: number;
  version_b: number;
  differences: Array<{ field: string; old_value: unknown; new_value: unknown }>;
  summary: string;
}> {
  return apiFetch('/api/v1/agent-versions/compare', {
    method: 'POST',
    body: JSON.stringify({ version_a: versionA, version_b: versionB }),
  });
}

/**
 * Rollback to a version (moves PRODUCTION alias)
 */
export async function rollbackToVersion(
  versionId: string,
  reason?: string
): Promise<{
  success: boolean;
  from_version?: number;
  to_version?: number;
  message: string;
}> {
  return apiFetch('/api/v1/agent-versions/rollback', {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId, reason }),
  });
}

// ===== Agent Alias API =====

/**
 * List aliases for an agent
 */
export async function getAgentAliases(agentId: string): Promise<{ aliases: AgentAlias[]; total: number }> {
  return apiFetch(`/api/v1/agents/${agentId}/aliases`);
}

/**
 * Get a specific alias
 */
export async function getAgentAlias(agentId: string, aliasName: string): Promise<AgentAlias> {
  return apiFetch(`/api/v1/agents/${agentId}/aliases/${aliasName}`);
}

/**
 * Create or update an alias (deploy/rollback via pointer swap)
 */
export async function setAgentAlias(
  agentId: string,
  aliasName: string,
  versionId: string
): Promise<AgentAlias> {
  return apiFetch(`/api/v1/agents/${agentId}/aliases/${aliasName}`, {
    method: 'PUT',
    body: JSON.stringify({ version_id: versionId }),
  });
}

/**
 * Delete an alias
 */
export async function deleteAgentAlias(agentId: string, aliasName: string): Promise<void> {
  return apiFetch(`/api/v1/agents/${agentId}/aliases/${aliasName}`, {
    method: 'DELETE',
  });
}

// ===== Helpers =====

/**
 * Format agent sequence number as AGT-NNN
 */
export function formatAgentId(sequenceNumber: number | undefined | null): string {
  if (!sequenceNumber) return '';
  return `AGT-${String(sequenceNumber).padStart(3, '0')}`;
}

/**
 * Format version number as vN
 */
export function formatVersionNumber(versionNumber: number | undefined | null): string {
  if (!versionNumber) return '';
  return `v${versionNumber}`;
}
