/**
 * React hooks for Agent Studio features
 *
 * Provides TanStack Query hooks for test cases, accuracy, and deploy gates
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listTestCases,
  createTestCase,
  updateTestCase,
  deleteTestCase,
  getAccuracySummary,
  getAccuracyHistory,
  getDeployGateStatus,
  deployVersion,
  rollbackVersion,
  getDeployHistory,
  listChatSessions,
  createChatSession,
  deleteChatSession,
  type TestCaseResponse,
  type CreateTestCaseRequest,
  type UpdateTestCaseRequest,
  type AccuracySummary,
  type AccuracyHistoryEntry,
  type DeployGateStatus,
  type DeployRequest,
  type DeployResult,
  type RollbackRequest,
  type RollbackResult,
  type DeployHistoryEntry,
  type ChatSessionSummary,
  type ChatSessionResponse,
  type CreateChatSessionRequest,
} from '@/lib/api/agent-studio';

// ============================================================================
// Test Case Hooks
// ============================================================================

export function useTestCases(agentId: string, options?: { category?: string; limit?: number }) {
  return useQuery({
    queryKey: ['agent-studio', 'test-cases', agentId, options],
    queryFn: () => listTestCases(agentId, options),
    enabled: !!agentId,
  });
}

export function useCreateTestCase(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (request: CreateTestCaseRequest) => createTestCase(agentId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'test-cases', agentId] });
    },
  });
}

export function useUpdateTestCase(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ testCaseId, request }: { testCaseId: string; request: UpdateTestCaseRequest }) =>
      updateTestCase(agentId, testCaseId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'test-cases', agentId] });
    },
  });
}

export function useDeleteTestCase(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (testCaseId: string) => deleteTestCase(agentId, testCaseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'test-cases', agentId] });
    },
  });
}

// ============================================================================
// Accuracy Hooks
// ============================================================================

export function useAccuracySummary(agentId: string, versionId?: string) {
  return useQuery({
    queryKey: ['agent-studio', 'accuracy', agentId, versionId],
    queryFn: () => getAccuracySummary(agentId, versionId),
    enabled: !!agentId,
  });
}

export function useAccuracyHistory(agentId: string, limit: number = 30) {
  return useQuery({
    queryKey: ['agent-studio', 'accuracy-history', agentId, limit],
    queryFn: () => getAccuracyHistory(agentId, limit),
    enabled: !!agentId,
  });
}

// ============================================================================
// Deploy Gate Hooks
// ============================================================================

export function useDeployGateStatus(agentId: string, versionId: string) {
  return useQuery({
    queryKey: ['agent-studio', 'deploy-gate', agentId, versionId],
    queryFn: () => getDeployGateStatus(agentId, versionId),
    enabled: !!agentId && !!versionId,
  });
}

export function useDeployVersion(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ versionId, request }: { versionId: string; request: DeployRequest }) =>
      deployVersion(agentId, versionId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'deploy-history', agentId] });
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'accuracy', agentId] });
    },
  });
}

export function useRollbackVersion(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ versionId, request }: { versionId: string; request: RollbackRequest }) =>
      rollbackVersion(agentId, versionId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'deploy-history', agentId] });
    },
  });
}

export function useDeployHistory(agentId: string, limit: number = 20) {
  return useQuery({
    queryKey: ['agent-studio', 'deploy-history', agentId, limit],
    queryFn: () => getDeployHistory(agentId, limit),
    enabled: !!agentId,
  });
}

// ============================================================================
// Chat Session Hooks
// ============================================================================

export function useChatSessions(agentId: string, options?: { limit?: number; status?: string }) {
  return useQuery({
    queryKey: ['agent-studio', 'chat-sessions', agentId, options],
    queryFn: () => listChatSessions(agentId, options),
    enabled: !!agentId,
  });
}

export function useCreateChatSession(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (request?: CreateChatSessionRequest) => createChatSession(agentId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'chat-sessions', agentId] });
    },
  });
}

export function useDeleteChatSession(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => deleteChatSession(agentId, sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'chat-sessions', agentId] });
    },
  });
}

// ============================================================================
// Invalidation Helpers
// ============================================================================

/**
 * Hook to get functions for invalidating Agent Studio queries
 */
export function useAgentStudioInvalidation(agentId: string) {
  const queryClient = useQueryClient();

  return {
    invalidateTestCases: () =>
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'test-cases', agentId] }),
    invalidateAccuracy: () =>
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'accuracy', agentId] }),
    invalidateDeployHistory: () =>
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'deploy-history', agentId] }),
    invalidateChatSessions: () =>
      queryClient.invalidateQueries({ queryKey: ['agent-studio', 'chat-sessions', agentId] }),
    invalidateAll: () =>
      queryClient.invalidateQueries({ queryKey: ['agent-studio'] }),
  };
}
