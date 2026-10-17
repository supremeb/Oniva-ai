/**
 * React Query hooks for Compliance & Audit system
 *
 * Provides hooks for:
 * - Dashboard stats
 * - Audit log listing with pagination
 * - Review ticket management (assign, approve, reject)
 * - Workflow replay
 * - CSV export
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getComplianceStats,
  getAuditLogs,
  exportAuditLogs,
  verifySignature,
  getWorkflowReplay,
  type ComplianceAuditLog,
} from '@/lib/api/compliance';

// ============================================================================
// Query Keys
// ============================================================================

export const complianceKeys = {
  all: ['compliance'] as const,
  stats: () => [...complianceKeys.all, 'stats'] as const,
  auditLogs: (filters?: Record<string, unknown>) =>
    [...complianceKeys.all, 'audit-logs', filters] as const,
  workflowReplay: (workflowId: string) =>
    [...complianceKeys.all, 'workflow-replay', workflowId] as const,
};

// ============================================================================
// Query Hooks (Read Operations)
// ============================================================================

export function useComplianceStats() {
  return useQuery({
    queryKey: complianceKeys.stats(),
    queryFn: getComplianceStats,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

export function useAuditLogs(filters?: {
  workflow_id?: string;
  actor?: string;
  risk_level?: string;
  start_date?: string;
  end_date?: string;
  reviewed?: boolean;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: complianceKeys.auditLogs(filters),
    queryFn: () => getAuditLogs(filters),
    staleTime: 10_000,
  });
}

export function useWorkflowReplay(workflowId: string | null) {
  return useQuery({
    queryKey: complianceKeys.workflowReplay(workflowId ?? ''),
    queryFn: () => getWorkflowReplay(workflowId!),
    enabled: !!workflowId,
  });
}

// ============================================================================
// Mutation Hooks (Write Operations)
// ============================================================================

export function useExportAuditLogs() {
  return useMutation({
    mutationFn: (params: { format: 'json' | 'csv' }) =>
      exportAuditLogs(params),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `audit-logs-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    },
  });
}

export function useVerifySignature() {
  return useMutation({
    mutationFn: (id: string) => verifySignature(id),
  });
}
