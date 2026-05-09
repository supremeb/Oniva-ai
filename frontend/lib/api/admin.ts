/**
 * Admin Dashboard API client (Frontier Phase D).
 *
 * Provides org overview metrics, agent fleet management,
 * and per-agent permission controls.
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./index";

// ============================================================================
// Types
// ============================================================================

export interface OrgOverview {
  total_agents: number;
  active_agents: number;
  total_users: number;
  total_teams: number;
  executions_30d: number;
  total_feedback_30d: number;
  satisfaction_rate: number | null;
}

export interface AgentFleetItem {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  model: string | null;
  feedback_total: number;
  feedback_positive: number;
  satisfaction_rate: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentFleetResponse {
  agents: AgentFleetItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface FeedbackSummary {
  total: number;
  positive: number;
  negative: number;
  satisfaction_rate: number | null;
}

export interface AgentDetail {
  agent: Record<string, unknown>;
  feedback_summary: FeedbackSummary;
  recent_feedback: Record<string, unknown>[];
  permissions: AgentPermission[];
}

export interface AgentPermission {
  id: string;
  tenant_id: string;
  agent_id: string;
  permission_type: string;
  team_id: string;
  created_at: string | null;
}

// ============================================================================
// API Functions
// ============================================================================

export async function getOrgOverview(): Promise<OrgOverview> {
  return apiFetch("/api/v1/admin/overview");
}

export async function getAgentFleet(params?: {
  sort_by?: string;
  sort_order?: string;
  limit?: number;
  offset?: number;
}): Promise<AgentFleetResponse> {
  const searchParams = new URLSearchParams();
  if (params?.sort_by) searchParams.set("sort_by", params.sort_by);
  if (params?.sort_order) searchParams.set("sort_order", params.sort_order);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));
  const qs = searchParams.toString();
  return apiFetch(`/api/v1/admin/agents${qs ? `?${qs}` : ""}`);
}

export async function getAgentDetail(agentId: string): Promise<AgentDetail> {
  return apiFetch(`/api/v1/admin/agents/${agentId}`);
}

export async function getAgentPermissions(agentId: string): Promise<AgentPermission[]> {
  return apiFetch(`/api/v1/admin/agents/${agentId}/permissions`);
}

export async function grantPermission(data: {
  agent_id: string;
  permission_type: string;
  team_id: string;
}): Promise<AgentPermission> {
  return apiFetch("/api/v1/admin/permissions", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function revokePermission(permissionId: string): Promise<void> {
  return apiFetch(`/api/v1/admin/permissions/${permissionId}`, {
    method: "DELETE",
  });
}

// ============================================================================
// React Query Hooks
// ============================================================================

export function useOrgOverview() {
  return useQuery({
    queryKey: ["admin", "overview"],
    queryFn: getOrgOverview,
    staleTime: 30_000,
  });
}

export function useAgentFleet(params?: {
  sort_by?: string;
  sort_order?: string;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: ["admin", "agents", params],
    queryFn: () => getAgentFleet(params),
    staleTime: 15_000,
  });
}

export function useAgentDetail(agentId: string | null) {
  return useQuery({
    queryKey: ["admin", "agents", agentId],
    queryFn: () => getAgentDetail(agentId!),
    enabled: !!agentId,
    staleTime: 15_000,
  });
}

export function useGrantPermission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: grantPermission,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin"] });
    },
  });
}

export function useRevokePermission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: revokePermission,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin"] });
    },
  });
}
