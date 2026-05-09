import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getAgentVersions,
  getAgentVersion,
  createAgentVersion,
  updateAgentVersion,
  deleteAgentVersion,
  deployAgentVersion,
  compareVersions,
  rollbackToVersion,
  getDraftStatus,
  getAgentAliases,
  setAgentAlias,
} from '@/lib/api/agent-versions';
import type {
  AgentVersion,
  AgentVersionCreate,
} from '@/lib/types/chat-panels';

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Hook to list all versions for an agent
 */
export function useAgentVersions(agentId: string) {
  return useQuery({
    queryKey: ['agent-versions', agentId],
    queryFn: () => getAgentVersions(agentId),
    enabled: !!agentId && UUID_REGEX.test(agentId),
    staleTime: 30000,
  });
}

/**
 * Hook to get draft change status
 */
export function useDraftStatus(agentId: string) {
  return useQuery({
    queryKey: ['agent-versions', 'draft-status', agentId],
    queryFn: () => getDraftStatus(agentId),
    enabled: !!agentId && UUID_REGEX.test(agentId),
    staleTime: 10000, // Refresh frequently for draft detection
  });
}

/**
 * Hook to list aliases for an agent
 */
export function useAgentAliases(agentId: string) {
  return useQuery({
    queryKey: ['agent-aliases', agentId],
    queryFn: () => getAgentAliases(agentId),
    enabled: !!agentId && UUID_REGEX.test(agentId),
    staleTime: 30000,
  });
}

/**
 * Hook to get a specific agent version
 */
export function useAgentVersion(versionId: string) {
  return useQuery({
    queryKey: ['agent-versions', 'detail', versionId],
    queryFn: () => getAgentVersion(versionId),
    enabled: !!versionId,
    staleTime: 30000,
  });
}

/**
 * Hook to create a new agent version
 */
export function useCreateAgentVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: AgentVersionCreate) => createAgentVersion(data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ['agent-versions', variables.agent_id],
      }).catch(() => {});
      queryClient.invalidateQueries({
        queryKey: ['agent-versions', 'draft-status', variables.agent_id],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to update agent version metadata
 */
export function useUpdateAgentVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      versionId,
      data,
    }: {
      versionId: string;
      data: { notes?: string; name?: string };
    }) => updateAgentVersion(versionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['agent-versions'],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to delete an agent version
 */
export function useDeleteAgentVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (versionId: string) => deleteAgentVersion(versionId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['agent-versions'],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to deploy an agent version (creates/updates PRODUCTION alias)
 */
export function useDeployAgentVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (versionId: string) => deployAgentVersion(versionId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['agent-versions'],
      }).catch(() => {});
      queryClient.invalidateQueries({
        queryKey: ['agent-aliases'],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to set an alias (deploy/rollback via pointer swap)
 */
export function useSetAgentAlias() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      agentId,
      aliasName,
      versionId,
    }: {
      agentId: string;
      aliasName: string;
      versionId: string;
    }) => setAgentAlias(agentId, aliasName, versionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-aliases'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['agent-versions'] }).catch(() => {});
    },
  });
}

/**
 * Hook to compare two agent versions
 */
export function useCompareAgentVersions(versionA: string, versionB: string) {
  return useQuery({
    queryKey: ['agent-versions', 'compare', versionA, versionB],
    queryFn: () => compareVersions(versionA, versionB),
    enabled: !!versionA && !!versionB,
    staleTime: 60000,
  });
}

/**
 * Hook to rollback to a specific version
 */
export function useRollbackAgentVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      versionId,
      reason,
    }: {
      versionId: string;
      reason?: string;
    }) => rollbackToVersion(versionId, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-versions'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['agent-aliases'] }).catch(() => {});
    },
  });
}
