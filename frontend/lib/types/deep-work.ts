/**
 * Deep Work types for autonomous agent execution with plan-first UX.
 */

// ── Session status ──────────────────────────────────────────
export type DeepWorkStatus =
  | 'idle'
  | 'clarifying'
  | 'planning'
  | 'awaiting_approval'
  | 'executing'
  | 'completed'
  | 'error'
  | 'stopped';

// ── Todo item ───────────────────────────────────────────────
export interface DeepWorkTodo {
  text: string;
  status: 'pending' | 'in_progress' | 'completed';
}

// ── Plan ────────────────────────────────────────────────────
export interface DeepWorkPlan {
  title: string;
  steps: string[];
}

// ── Step event from SSE ─────────────────────────────────────
export interface DeepWorkStep {
  step_number: number;
  total_steps: number;
  iteration?: number;  // Tool call iteration count (separate from plan step)
  tool_name: string | null;
  status: 'success' | 'error';
  result: Record<string, unknown> | null;
  agent_message: string | null;
  duration_ms: number;
}

// ── Artifact file ───────────────────────────────────────────
export interface DeepWorkArtifact {
  type: 'file';
  filename: string;
  size?: number;
  fileType?: string;
  downloadUrl?: string;
  title?: string;
  description?: string;
  artifactType?: string;   // pdf_report | presentation | spreadsheet | dashboard | chart | document | data_export
  artifactUri?: string;
  tags?: string[];
  pageCount?: number | null;
}

// ── SSE events ──────────────────────────────────────────────
export interface DeepWorkStartEvent {
  session_id: string;
  plan: string;
  todos: DeepWorkTodo[];
  agent_id: string;
}

export interface StepCompleteEvent {
  step_number: number;
  total_steps: number;
  tool_name: string | null;
  status: 'success' | 'error';
  result: Record<string, unknown> | null;
  agent_message: string | null;
  duration_ms: number;
}

export interface TodoUpdateEvent {
  todos: DeepWorkTodo[];
}

export interface HumanInputRequiredEvent {
  question: string;
  session_id: string;
}

export interface DeepWorkCompleteEvent {
  summary: string;
  key_findings: string[];
  artifacts: string[];
  total_time: number;
  total_steps: number;
}

export interface DeepWorkErrorEvent {
  error: string;
  last_step: number;
  can_retry: boolean;
}

export interface DeepWorkPausedEvent {
  reason: 'user_requested' | 'budget_exceeded' | 'time_limit_exceeded';
  resumable: boolean;
}

export interface WorkspaceUpdateEvent {
  action: 'create' | 'update' | 'delete';
  path: string;
  size?: number;
}

// ── Workspace file in store ────────────────────────────────
export interface WorkspaceFile {
  path: string;
  action: 'create' | 'update' | 'delete';
  size?: number;
}

// ── API request/response ────────────────────────────────────
// ── Source / connector tracking ──────────────────────────────
export interface DeepWorkSource {
  type: 'search' | 'url' | 'knowledge_base';
  title?: string;
  url?: string;
  domain?: string;
  query?: string;
}

export interface DeepWorkConnector {
  name: string;
  type: string;
}

// ── Source update SSE event ──────────────────────────────────
export interface SourceUpdateEvent {
  sources: DeepWorkSource[];
  connectors: DeepWorkConnector[];
}

// ── Deep Work Engine (orchestrator-worker) types ────────────

export type EnginePhase = 'researching' | 'reflecting' | 'synthesizing' | 'generating_output';


export type EngineWorkerStatus = 'idle' | 'researching' | 'complete' | 'error';

export interface WorkerCompletionSummary {
  findings: string[];
  sourceCount: number;
  duration: number;
  toolCallCount: number;
}

export interface WorkerScreenshot {
  url: string;
  pageUrl: string;
  timestamp: number;
}

export interface EngineWorkerState {
  worker_id: string;
  sub_query: string;
  status: EngineWorkerStatus;
  learning_count: number;
  activity?: string;
  recentActions?: string[];
  error?: string | null;
  completionSummary?: WorkerCompletionSummary;
  lastScreenshot?: WorkerScreenshot;
}

export interface EngineReflection {
  score: number;
  gaps: string[];
  round: number;
  continuing: boolean;
}

export interface StartEngineRequest {
  query: string;
  session_id?: string;
  conversation_id?: string;
  plan?: Record<string, unknown>;
}

export interface StartEngineResponse {
  session_id: string;
  status: string;
}

export interface EnginePlanResponse {
  title: string;
  steps: string[];
  raw_plan: Record<string, unknown>;
}

// ── Intake clarification ────────────────────────────────────

export interface IntakeQuestion {
  text: string;
  options: string[] | null;
}

export interface IntakeResult {
  action: 'ask' | 'proceed';
  questions: IntakeQuestion[] | null;
  enriched_query: string | null;
}
