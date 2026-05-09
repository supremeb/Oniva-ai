import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listWorkflows,
  getWorkflow,
  createWorkflow,
  updateWorkflow,
  deleteWorkflow,
  executeWorkflow,
  WorkflowSpec,
  ExecuteWorkflowRequest,
} from '@/lib/api/workflows';

export function useWorkflows() {
  return useQuery({
    queryKey: ['workflows'],
    queryFn: listWorkflows,
  });
}

export function useWorkflow(id: string) {
  return useQuery({
    queryKey: ['workflows', id],
    queryFn: () => getWorkflow(id),
    enabled: !!id,
    staleTime: 30000, // Consider data fresh for 30s to avoid refetches
    retry: 1, // Only retry once on failure
  });
}

export function useCreateWorkflow() {
  return useMutation({
    mutationFn: ({ spec, name }: { spec: WorkflowSpec; name?: string }) =>
      createWorkflow(spec, name),
    // Removed onSuccess query invalidation - let component handle refetch
  });
}

export function useUpdateWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, spec, name }: { id: string; spec?: WorkflowSpec; name?: string }) =>
      updateWorkflow(id, spec, name),
    onSuccess: (data, variables) => {
      // Invalidate queries but don't await - prevents timeout errors from propagating
      // The mutation already succeeded, cache invalidation is best-effort
      queryClient.invalidateQueries({ queryKey: ['workflows'] }).catch(() => {
        // Silently ignore invalidation errors - the save already succeeded
      });
      queryClient.invalidateQueries({ queryKey: ['workflows', variables.id] }).catch(() => {
        // Silently ignore invalidation errors - the save already succeeded
      });
    },
  });
}

export function useDeleteWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteWorkflow,
    onSuccess: () => {
      // Invalidate queries but don't await - prevents timeout errors from propagating
      queryClient.invalidateQueries({ queryKey: ['workflows'] }).catch(() => {
        // Silently ignore - the delete already succeeded
      });
    },
  });
}

export function useExecuteWorkflow() {
  return useMutation({
    mutationFn: (request: ExecuteWorkflowRequest) => executeWorkflow(request),
  });
}
