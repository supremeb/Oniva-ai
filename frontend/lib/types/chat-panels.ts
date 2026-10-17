import type { RunStatus } from '@/lib/run/status';
/**
 * Types for integrated chat panels feature.
 */

// Chat Message
export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

// Chat Session
export interface ChatSession {
  id: string;
  tenant_id?: string;
  user_id?: string;
  agent_id?: string;
  workflow_id?: string;
  context_id?: string;  // Generic context reference (agent_id or workflow_id)
  title?: string;
  context_type: 'agent' | 'workflow';
  messages: ChatMessage[];
  message_count?: number;
  is_draft: boolean;
  is_pinned?: boolean;
  is_local?: boolean;  // True when session is local-only (backend unavailable)
  created_at: string;
  updated_at: string;
  last_message_at?: string;
}

export interface ChatSessionCreate {
  title?: string;
  context_type: 'agent' | 'workflow';
  agent_id?: string;
  workflow_id?: string;
  is_draft?: boolean;
  messages?: Array<{
    role: 'user' | 'assistant' | 'system';
    content: string;
    metadata?: Record<string, unknown>;
  }>;
}

export interface ChatSessionUpdate {
  title?: string;
  is_draft?: boolean;
  is_pinned?: boolean;
}

export interface ChatSessionListResponse {
  sessions: ChatSession[];
  total: number;
  page: number;
  page_size: number;
}

// Agent Version
export interface AgentVersion {
  id: string;
  tenant_id: string;
  agent_id: string;
  version_number?: number;
  legacy_version?: string;
  name?: string;
  config: Record<string, unknown>;
  is_deployed: boolean;
  deployed_at?: string;
  deployed_by?: string;
  accuracy?: number;
  accuracy_at_deploy?: number;
  deploy_blocked?: boolean;
  block_reason?: string;
  visibility?: string;
  published_at?: string;
  compliance_tags?: string[];
  created_by?: string;
  notes?: string;
  created_at: string;
}

export interface AgentVersionCreate {
  agent_id: string;
  name: string;
  notes?: string;
}

export interface AgentVersionListResponse {
  versions: AgentVersion[];
  total: number;
  current_deployed?: string;
  production_version_number?: number;
}

export interface DraftStatusResponse {
  has_changes: boolean;
  latest_version_number?: number;
  changed_fields: string[];
  production_version_number?: number;
}

export interface AgentAlias {
  id: string;
  tenant_id: string;
  agent_id: string;
  alias_name: string;
  version_id: string;
  version_number?: number;
  version_name?: string;
  updated_by?: string;
  updated_at?: string;
  created_at?: string;
}

// Background Job
/**
 * A job's status is the shared run status (plan 003, KTD10). Rows written before the
 * enum landed (legacy jobs) can still carry `pending`; `normalizeStatus` maps it to `queued`.
 */
export type JobStatus = RunStatus | 'pending';
export type JobType = 'workflow_execution' | 'agent_chat' | 'batch_processing' | 'deep_work';

export interface ExecutionTraceStep {
  step_id: string;
  node_id?: string;
  node_type?: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  error?: string;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  reasoning?: string;
  // Deep work specific fields
  topic?: string;
  search_query?: string;
  sources_found?: number;
}

// Deep Work types (legacy plan-accept-iterate)
export interface ResearchPlanStep {
  id: string;
  label: string;
}

export interface ResearchPlan {
  plan_id: string;
  title: string;
  summary: string;
  steps: ResearchPlanStep[];
  sub_topics: Array<{
    topic: string;
    search_queries: string[];
    depth: 'shallow' | 'deep';
  }>;
  estimated_searches: number;
  sources: {
    connectors?: string[];
    sites?: string[];
    knowledgeBases?: string[];
  };
}

export interface BackgroundJob {
  id: string;
  tenant_id: string;
  user_id: string;
  workflow_id?: string;
  agent_id?: string;
  session_id?: string;
  job_type: JobType;
  status: JobStatus;
  progress: number;
  input_data?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string;
  execution_trace: ExecutionTraceStep[];
  started_at?: string;
  completed_at?: string;
  created_at: string;
  duration_ms?: number;
  // Detail response fields
  workflow_name?: string;
  agent_name?: string;
}

export interface BackgroundJobCreate {
  job_type: JobType;
  workflow_id?: string;
  agent_id?: string;
  input_data?: Record<string, unknown>;
}

export interface BackgroundJobListResponse {
  jobs: BackgroundJob[];
  total: number;
  page: number;
  page_size: number;
}

// SSE Events
export interface JobProgressEvent {
  job_id: string;
  status: string;
  progress: number;
  current_step?: ExecutionTraceStep;
  message?: string;
  timestamp: string;
}

export interface JobCompletedEvent {
  job_id: string;
  status: string;
  result?: Record<string, unknown>;
  error?: string;
  duration_ms: number;
  execution_trace: ExecutionTraceStep[];
  completed_at: string;
}
