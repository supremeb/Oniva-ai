import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listWorkflowVersions,
  getWorkflowVersion,
  getWorkflowVersionByNumber,
  getActiveWorkflowVersion,
  getDraftStatus,
  createWorkflowVersion,
  updateWorkflowVersion,
  activateWorkflowVersion,
  publishWorkflow,
  rollbackToVersion,
  compareWorkflowVersions,
  deleteWorkflowVersion,
  CreateVersionRequest,
  UpdateVersionRequest,
  PublishRequest,
} from '@/lib/api/workflow-versions';

/**
 * Hook to list all versions for a workflow
 */
export function useWorkflowVersions(
  workflowId: string,
  options?: {
    includeDrafts?: boolean;
    limit?: number;
    offset?: number;
  }
) {
  return useQuery({
    queryKey: ['workflow-versions', workflowId, options],
    queryFn: () => listWorkflowVersions(workflowId, options),
    enabled: !!workflowId && !workflowId.startsWith('local-'),
    staleTime: 30000,
  });
}

/**
 * Hook to get a specific version with full spec
 */
export function useWorkflowVersion(workflowId: string, versionId: string) {
  return useQuery({
    queryKey: ['workflow-versions', workflowId, versionId],
    queryFn: () => getWorkflowVersion(workflowId, versionId),
    enabled: !!workflowId && !!versionId && !workflowId.startsWith('local-'),
    staleTime: 30000,
  });
}

/**
 * Hook to get a version by version number
 */
export function useWorkflowVersionByNumber(workflowId: string, versionNumber: number) {
  return useQuery({
    queryKey: ['workflow-versions', workflowId, 'by-number', versionNumber],
    queryFn: () => getWorkflowVersionByNumber(workflowId, versionNumber),
    enabled: !!workflowId && versionNumber > 0 && !workflowId.startsWith('local-'),
    staleTime: 30000,
  });
}

/**
 * Hook to get the currently active (deployed) version
 */
export function useActiveWorkflowVersion(workflowId: string) {
  return useQuery({
    queryKey: ['workflow-versions', workflowId, 'active'],
    queryFn: () => getActiveWorkflowVersion(workflowId),
    enabled: !!workflowId && !workflowId.startsWith('local-'),
    staleTime: 30000,
  });
}

/**
 * Hook to get draft status including suggested next versions
 */
export function useDraftStatus(workflowId: string) {
  return useQuery({
    queryKey: ['workflow-versions', workflowId, 'draft'],
    queryFn: () => getDraftStatus(workflowId),
    enabled: !!workflowId && !workflowId.startsWith('local-'),
    staleTime: 10000, // Refresh more frequently for draft status
  });
}

/**
 * Hook to create a new version from current workflow state
 */
export function useCreateWorkflowVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      workflowId,
      request,
    }: {
      workflowId: string;
      request?: CreateVersionRequest;
    }) => createWorkflowVersion(workflowId, request),
    onSuccess: (_, variables) => {
      // Invalidate version list for this workflow
      queryClient.invalidateQueries({
        queryKey: ['workflow-versions', variables.workflowId],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to update version metadata
 */
export function useUpdateWorkflowVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      workflowId,
      versionId,
      request,
    }: {
      workflowId: string;
      versionId: string;
      request: UpdateVersionRequest;
    }) => updateWorkflowVersion(workflowId, versionId, request),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ['workflow-versions', variables.workflowId],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to activate (deploy) a version
 */
export function useActivateWorkflowVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      workflowId,
      versionId,
    }: {
      workflowId: string;
      versionId: string;
    }) => activateWorkflowVersion(workflowId, versionId),
    onSuccess: (_, variables) => {
      // Invalidate version list and active version
      queryClient.invalidateQueries({
        queryKey: ['workflow-versions', variables.workflowId],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to publish workflow with semantic versioning
 */
export function usePublishWorkflow() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      workflowId,
      request,
    }: {
      workflowId: string;
      request: PublishRequest;
    }) => publishWorkflow(workflowId, request),
    onSuccess: (_, variables) => {
      // Invalidate all version-related queries
      queryClient.invalidateQueries({
        queryKey: ['workflow-versions', variables.workflowId],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to rollback to a specific version
 */
export function useRollbackToVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      workflowId,
      versionId,
    }: {
      workflowId: string;
      versionId: string;
    }) => rollbackToVersion(workflowId, versionId),
    onSuccess: (_, variables) => {
      // Invalidate both workflow and versions
      queryClient.invalidateQueries({
        queryKey: ['workflows', variables.workflowId],
      }).catch(() => {});
      queryClient.invalidateQueries({
        queryKey: ['workflow-versions', variables.workflowId],
      }).catch(() => {});
    },
  });
}

/**
 * Hook to compare two versions
 */
export function useCompareWorkflowVersions(
  workflowId: string,
  versionA: number,
  versionB: number
) {
  return useQuery({
    queryKey: ['workflow-versions', workflowId, 'compare', versionA, versionB],
    queryFn: () => compareWorkflowVersions(workflowId, versionA, versionB),
    enabled: !!workflowId && versionA > 0 && versionB > 0 && !workflowId.startsWith('local-'),
    staleTime: 60000, // Diffs don't change, cache longer
  });
}

/**
 * Hook to delete a version
 */
export function useDeleteWorkflowVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      workflowId,
      versionId,
    }: {
      workflowId: string;
      versionId: string;
    }) => deleteWorkflowVersion(workflowId, versionId),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ['workflow-versions', variables.workflowId],
      }).catch(() => {});
    },
  });
}
