import { apiFetch, APIError, API_BASE_URL } from './index';
import { Workflow, WorkflowSpec, WorkflowResult } from '@/types/api';
import { getTenantKey } from '@/lib/tenant-storage';

/**
 * Base LocalStorage key for workflows (local-first fallback)
 * SECURITY: Actual key is tenant-prefixed via getTenantKey()
 */
const WORKFLOWS_KEY_BASE = 'oniva-workflows';

/**
 * Get tenant-prefixed localStorage key for workflows.
 * Prevents cross-tenant data leakage when users switch tenants.
 */
function getWorkflowsKey(): string {
  return getTenantKey(WORKFLOWS_KEY_BASE);
}

/**
 * Workflow execution types
 */
export interface ExecuteWorkflowRequest {
  spec: WorkflowSpec;
  inputs?: Record<string, unknown>;
  workflowId?: string;
}

/**
 * Output from a workflow Output node.
 * Part of Phase 3: User Experience improvements.
 */
export interface WorkflowOutputNode {
  node_id: string;
  node_name: string;
  output_type: 'text' | 'json' | 'markdown' | 'table';
  content: unknown;
  timestamp: string;
}

export interface ExecuteWorkflowResponse {
  status: 'success' | 'error' | 'running';
  message?: string;
  results: Record<string, unknown>;
  execution_time?: number;
  error?: string;
  executed_nodes?: number;
  total_nodes?: number;
  // NEW: Structured outputs from Output nodes
  outputs: WorkflowOutputNode[];
}

/**
 * LocalStorage functions for workflows (local-first fallback)
 * SECURITY: All keys are tenant-prefixed to prevent cross-tenant data leakage
 */
function getLocalWorkflows(): Workflow[] {
  if (typeof window === 'undefined') return [];
  const stored = localStorage.getItem(getWorkflowsKey());
  if (!stored) return [];
  try {
    return JSON.parse(stored);
  } catch {
    return [];
  }
}

function saveLocalWorkflows(workflows: Workflow[]): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(getWorkflowsKey(), JSON.stringify(workflows));
}

function getLocalWorkflow(id: string): Workflow | undefined {
  const workflows = getLocalWorkflows();
  return workflows.find(w => w.id === id);
}

function createLocalWorkflow(spec: WorkflowSpec, name?: string): Workflow {
  const workflows = getLocalWorkflows();
  const now = new Date().toISOString();

  const newWorkflow: Workflow = {
    id: `local-${Date.now()}-${Math.random().toString(36).substring(7)}`,
    name: name || 'Untitled Workflow',
    description: '',
    spec: {
      ...spec,
      nodes: spec.nodes || [],
      edges: spec.edges || [],
    },
    created_at: now,
    updated_at: now,
  };

  workflows.push(newWorkflow);
  saveLocalWorkflows(workflows);
  return newWorkflow;
}

function updateLocalWorkflow(id: string, spec?: WorkflowSpec, name?: string): Workflow | null {
  const workflows = getLocalWorkflows();
  const index = workflows.findIndex(w => w.id === id);

  if (index === -1) return null;

  const updated = {
    ...workflows[index],
    ...(spec && { spec }),
    ...(name && { name }),
    updated_at: new Date().toISOString(),
  };

  workflows[index] = updated;
  saveLocalWorkflows(workflows);
  return updated;
}

function deleteLocalWorkflow(id: string): boolean {
  const workflows = getLocalWorkflows();
  const filtered = workflows.filter(w => w.id !== id);
  if (filtered.length === workflows.length) return false;
  saveLocalWorkflows(filtered);
  return true;
}

/**
 * List all workflows (tries API first, falls back to localStorage)
 */
export async function listWorkflows(): Promise<Workflow[]> {
  try {
    const response = await apiFetch<{ items: Workflow[] }>('/api/v1/workflows', { timeout: 10000 });
    return response.items;
  } catch (error) {
    console.log('Backend unavailable, using local workflows:', error);
    return getLocalWorkflows();
  }
}

/**
 * Get a specific workflow by ID (tries API first, falls back to localStorage)
 */
export async function getWorkflow(id: string): Promise<Workflow> {
  // Check local first for local IDs
  if (id.startsWith('local-')) {
    const local = getLocalWorkflow(id);
    if (local) return local;
  }

  try {
    return await apiFetch<Workflow>(`/api/v1/workflows/${id}`, { timeout: 10000 });
  } catch (error) {
    console.log('Backend unavailable, checking local workflows:', error);
    const local = getLocalWorkflow(id);
    if (local) return local;
    throw new APIError('Workflow not found', 404);
  }
}

/**
 * Create a new workflow (tries API first, falls back to localStorage)
 */
export async function createWorkflow(
  spec: WorkflowSpec,
  name?: string
): Promise<Workflow> {
  try {
    return await apiFetch<Workflow>('/api/v1/workflows', {
      method: 'POST',
      body: JSON.stringify({
        name: name || 'Untitled Workflow',
        spec,
      }),
      timeout: 10000,
    });
  } catch (error) {
    console.log('Backend unavailable, creating local workflow:', error);
    return createLocalWorkflow(spec, name);
  }
}

/**
 * Update an existing workflow (tries API first, falls back to localStorage)
 */
export async function updateWorkflow(
  id: string,
  spec?: WorkflowSpec,
  name?: string
): Promise<Workflow> {
  const body: { spec?: WorkflowSpec; name?: string } = {};
  if (spec !== undefined) {
    body.spec = spec;
  }
  if (name !== undefined) {
    body.name = name;
  }

  // Handle local IDs
  if (id.startsWith('local-')) {
    const updated = updateLocalWorkflow(id, spec, name);
    if (updated) return updated;
    throw new APIError('Local workflow not found', 404);
  }

  try {
    return await apiFetch<Workflow>(`/api/v1/workflows/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
      timeout: 10000,
    });
  } catch (error) {
    console.log('Backend unavailable, updating local workflow:', error);
    const updated = updateLocalWorkflow(id, spec, name);
    if (updated) return updated;
    throw error;
  }
}

/**
 * Delete a workflow (tries API first, falls back to localStorage)
 */
export async function deleteWorkflow(id: string): Promise<void> {
  // Handle local IDs
  if (id.startsWith('local-')) {
    deleteLocalWorkflow(id);
    return;
  }

  try {
    return await apiFetch<void>(`/api/v1/workflows/${id}`, {
      method: 'DELETE',
      timeout: 10000,
    });
  } catch (error) {
    console.log('Backend unavailable, deleting local workflow:', error);
    deleteLocalWorkflow(id);
  }
}

/**
 * Execute a workflow
 */
export async function executeWorkflow(
  request: ExecuteWorkflowRequest
): Promise<ExecuteWorkflowResponse> {
  return apiFetch<ExecuteWorkflowResponse>('/api/v1/workflows/execute', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

/**
 * SSE event parsed from the streaming response.
 */
export interface WorkflowStreamEvent {
  type: string;
  [key: string]: unknown;
}

/**
 * Execute a workflow with streaming (SSE) via fetch POST.
 *
 * Returns an async generator of parsed SSE events. Uses POST to avoid
 * URL length limits and sends auth credentials + CSRF token properly
 * (EventSource is GET-only and cannot send custom headers or a body).
 */
export async function* executeWorkflowStream(
  request: ExecuteWorkflowRequest
): AsyncGenerator<WorkflowStreamEvent> {
  const csrfToken = typeof document !== 'undefined'
    ? document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)?.[1]
    : undefined;

  const response = await fetch(`${API_BASE_URL}/api/v1/workflows/execute-stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': decodeURIComponent(csrfToken) } : {}),
    },
    credentials: 'include',
    body: JSON.stringify({
      nodes: request.spec.nodes,
      edges: request.spec.edges,
      inputs: request.inputs ?? {},
      workflow_id: request.workflowId ?? null,
    }),
  });

  if (!response.ok) {
    throw new APIError(
      `Streaming execution failed: ${response.status}`,
      response.status
    );
  }

  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    // Keep the last (possibly incomplete) line in the buffer
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data: ')) continue;
      try {
        yield JSON.parse(trimmed.slice(6)) as WorkflowStreamEvent;
      } catch {
        // skip malformed JSON
      }
    }
  }

  // Flush any remaining data in the buffer
  if (buffer.trim().startsWith('data: ')) {
    try {
      yield JSON.parse(buffer.trim().slice(6)) as WorkflowStreamEvent;
    } catch {
      // skip malformed JSON
    }
  }
}

/**
 * Cancel a running workflow execution.
 *
 * Enterprise feature: Stop in-flight executions gracefully.
 * Current node completes, then execution stops with 'cancelled' status.
 */
export async function cancelExecution(
  executionId: string
): Promise<{
  status: string;
  message: string;
  execution_id: string;
}> {
  return apiFetch(`/api/v1/workflows/${executionId}/cancel`, {
    method: 'POST',
  });
}

// Re-export types from @/types/api
export type { WorkflowSpec, WorkflowNode, WorkflowEdge, WorkflowNodeType } from '@/types/api';
