// Compliance API Client - Regulatory Audit & Review Management
import { apiFetch } from './index';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

export interface ComplianceAuditLog {
  id: string;
  agent_id: string;
  decision: string;
  rationale: string;
  data_sources: string[];
  workflow_state: string;
  workflow_id?: string;
  session_id?: string;
  timestamp: string;
  signature?: string;
}

export interface ReviewTicket {
  id: string;
  tenant_id: string;
  workflow_id?: string;
  session_id?: string;
  agent_id: string;
  requested_by?: string;
  title: string;
  description: string;
  risk_level: string;
  priority: string;
  review_type: string;
  review_data: Record<string, any>;
  status: string;
  assigned_to?: string;
  assigned_at?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  decision?: string;
  review_notes?: string;
  due_at?: string;
  sla_breached: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkflowReplay {
  workflow_id: string;
  states: Array<{
    timestamp: string;
    state_name: string;
    variables: Record<string, any>;
    tools_used: string[];
    decision_point?: {
      options: string[];
      selected: string;
      confidence: number;
    };
  }>;
  total_duration_ms: number;
  checkpoints: number;
  can_replay: boolean;
}

export interface WorkflowReplaySummary {
  workflow_id: string;
  total_states: number;
  total_duration_ms: number;
  risk_events: number;
  approval_points: number;
  data_accessed: string[];
  compliance_violations: string[];
  final_outcome: 'SUCCESS' | 'FAILED' | 'PARTIAL';
}

export interface ChainVerificationResult {
  workflow_id: string;
  is_valid: boolean;
  total_entries: number;
  valid_entries: number;
  invalid_entries: number;
  missing_entries: number;
  verification_timestamp: string;
  details: Array<{
    entry_id: string;
    is_valid: boolean;
    reason?: string;
  }>;
}

export interface ComplianceStats {
  total_audit_logs: number;
  pending_reviews: number;
  chain_integrity: {
    status: 'VALID' | 'INVALID' | 'CHECKING';
    last_verified: string;
    issues_found: number;
  };
  risk_distribution: {
    low: number;
    medium: number;
    high: number;
    critical: number;
  };
  recent_activity: {
    last_24h: number;
    last_7d: number;
    last_30d: number;
  };
}

// ============================================================================
// API FUNCTIONS
// ============================================================================

export async function getComplianceStats(): Promise<ComplianceStats> {
  return apiFetch<ComplianceStats>('/api/v1/audit/compliance/stats');
}

export async function getAuditLogs(filters?: {
  workflow_id?: string;
  actor?: string;
  risk_level?: string;
  start_date?: string;
  end_date?: string;
  reviewed?: boolean;
  limit?: number;
  offset?: number;
}): Promise<ComplianceAuditLog[]> {
  const queryParams = new URLSearchParams();
  if (filters?.workflow_id) queryParams.append('workflow_id', filters.workflow_id);
  if (filters?.actor) queryParams.append('actor', filters.actor);
  if (filters?.risk_level) queryParams.append('risk_level', filters.risk_level);
  if (filters?.start_date) queryParams.append('start_date', filters.start_date);
  if (filters?.end_date) queryParams.append('end_date', filters.end_date);
  if (filters?.reviewed !== undefined) queryParams.append('reviewed', String(filters.reviewed));
  if (filters?.limit !== undefined) queryParams.append('limit', String(filters.limit));
  if (filters?.offset !== undefined) queryParams.append('offset', String(filters.offset));

  const url = `/api/v1/audit/compliance/logs${queryParams.toString() ? '?' + queryParams.toString() : ''}`;
  return apiFetch<ComplianceAuditLog[]>(url);
}

export async function getAuditLog(id: string): Promise<ComplianceAuditLog> {
  return apiFetch<ComplianceAuditLog>(`/api/v1/audit/compliance/logs/${id}`);
}

export async function exportAuditLogs(params: {
  format: 'json' | 'csv';
  workflow_id?: string;
  start_date?: string;
  end_date?: string;
}): Promise<Blob> {
  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/audit/compliance/logs/export`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }
  );

  if (!response.ok) {
    throw new Error(`Export failed: ${response.status}`);
  }

  return response.blob();
}

export async function verifySignature(
  id: string
): Promise<{ valid: boolean; details: string }> {
  return apiFetch(`/api/v1/audit/compliance/logs/${id}/verify`, {
    method: 'POST',
  });
}

export async function getWorkflowReplay(
  workflowId: string
): Promise<WorkflowReplay> {
  return apiFetch<WorkflowReplay>(`/api/v1/audit/replay/${workflowId}`);
}

export async function getWorkflowReplaySummary(
  workflowId: string
): Promise<WorkflowReplaySummary> {
  return apiFetch<WorkflowReplaySummary>(
    `/api/v1/audit/replay/${workflowId}/summary`
  );
}

export async function verifyWorkflowChain(
  workflowId: string
): Promise<ChainVerificationResult> {
  return apiFetch<ChainVerificationResult>(
    `/api/v1/audit/replay/${workflowId}/verify-chain`,
    { method: 'POST' }
  );
}

export async function getReviewTickets(filters?: {
  status?: ReviewTicket['status'];
  priority?: ReviewTicket['priority'];
  assigned_to?: string;
  limit?: number;
  offset?: number;
}): Promise<ReviewTicket[]> {
  const queryParams = new URLSearchParams();
  if (filters?.status) queryParams.append('status', filters.status);
  if (filters?.priority) queryParams.append('priority', filters.priority);
  if (filters?.assigned_to) queryParams.append('assigned_to', filters.assigned_to);
  if (filters?.limit !== undefined) queryParams.append('limit', String(filters.limit));
  if (filters?.offset !== undefined) queryParams.append('offset', String(filters.offset));

  const url = `/api/v1/audit/reviews${queryParams.toString() ? '?' + queryParams.toString() : ''}`;
  return apiFetch<ReviewTicket[]>(url);
}

export async function getReviewTicket(id: string): Promise<ReviewTicket> {
  return apiFetch<ReviewTicket>(`/api/v1/audit/reviews/${id}`);
}

export async function assignReviewer(
  ticketId: string,
  reviewerId: string
): Promise<ReviewTicket> {
  return apiFetch<ReviewTicket>(
    `/api/v1/audit/reviews/${ticketId}/assign?assignee_id=${encodeURIComponent(reviewerId)}`,
    { method: 'POST' }
  );
}

export async function approveReview(
  ticketId: string,
  reason: string
): Promise<ReviewTicket> {
  return apiFetch<ReviewTicket>(
    `/api/v1/audit/reviews/${ticketId}/decision`,
    {
      method: 'POST',
      body: JSON.stringify({ decision: 'approved', notes: reason }),
    }
  );
}

export async function rejectReview(
  ticketId: string,
  reason: string
): Promise<ReviewTicket> {
  return apiFetch<ReviewTicket>(
    `/api/v1/audit/reviews/${ticketId}/decision`,
    {
      method: 'POST',
      body: JSON.stringify({ decision: 'rejected', notes: reason }),
    }
  );
}
