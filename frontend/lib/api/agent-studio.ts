/**
 * Agent Studio API client
 *
 * Handles chat sessions, test cases, evaluation, and deploy gates
 */

import { apiFetch, APIError } from './index';

const AGENT_STUDIO_PREFIX = '/api/v1/agent-studio';

// ============================================================================
// SSE Stream Utilities
// ============================================================================

/**
 * Parsed SSE event from the server
 */
export interface SSEEvent {
  type: string;
  data?: unknown;
  message?: string;
  error?: string;
  content?: string;
  message_id?: string;
}

/**
 * Configuration for SSE stream processing
 */
export interface SSEStreamConfig<T> {
  url: string;
  method?: 'GET' | 'POST';
  body?: unknown;
  onEvent: (event: SSEEvent) => void;
  onComplete?: () => void;
  onError: (error: Error) => void;
}

/**
 * Parse a single SSE line into an event object
 */
function parseSSELine(line: string): SSEEvent | null {
  if (!line.startsWith('data: ')) return null;

  try {
    return JSON.parse(line.slice(6)) as SSEEvent;
  } catch {
    return null;
  }
}

/**
 * Create an SSE stream processor with automatic buffering and parsing
 * Returns an abort function to cancel the stream
 */
export function createSSEStream<T>(config: SSEStreamConfig<T>): () => void {
  const abortController = new AbortController();

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };

  const fetchOptions: RequestInit = {
    method: config.method || 'POST',
    headers,
    credentials: 'include',
    signal: abortController.signal,
  };

  if (config.body) {
    fetchOptions.body = JSON.stringify(config.body);
  }

  fetch(config.url, fetchOptions)
    .then(async (response) => {
      if (!response.ok) {
        // Extract detailed error message from response body
        let errorDetail = `HTTP error ${response.status}`;
        try {
          const errorData = await response.json();
          errorDetail = errorData.detail || errorData.message || errorDetail;
        } catch {
          // Response body not JSON, use status text
          errorDetail = response.statusText || errorDetail;
        }
        throw new Error(errorDetail);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No response body');
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const event = parseSSELine(line);
          if (event) {
            config.onEvent(event);
          }
        }
      }

      // Process any remaining buffer
      if (buffer) {
        const event = parseSSELine(buffer);
        if (event) {
          config.onEvent(event);
        }
      }

      config.onComplete?.();
    })
    .catch((error) => {
      if (error.name !== 'AbortError') {
        config.onError(error);
      }
    });

  return () => abortController.abort();
}

// ============================================================================
// Types
// ============================================================================

// Chat Session Types
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  feedback?: 'positive' | 'negative' | null;
  test_case_id?: string | null;
  tokens_used?: number;
  latency_ms?: number;
}

export interface CreateChatSessionRequest {
  name?: string;
  version_id?: string;
}

export interface ChatSessionSummary {
  id: string;
  agent_id: string;
  agent_version_id?: string;
  name?: string;
  status: string;
  total_messages: number;
  positive_feedback_count: number;
  negative_feedback_count: number;
  created_at: string;
  updated_at: string;
  last_message_at?: string;
}

export interface ChatSessionResponse extends ChatSessionSummary {
  tenant_id: string;
  user_id: string;
  messages: ChatMessage[];
  total_tokens_used: number;
  total_latency_ms: number;
}

export interface SendMessageRequest {
  content: string;
  context?: Record<string, unknown>;
}

export interface FeedbackRequest {
  feedback: 'positive' | 'negative';
  notes?: string;
  create_test_case?: boolean;
}

export interface FeedbackResponse {
  message_id: string;
  feedback: string;
  test_case_id?: string | null;
  test_case_name?: string | null;
  test_case_created: boolean;
}

// Test Case Types
export interface TestCaseLastResult {
  passed: boolean;
  actual_output: string;
  score?: number;
  evaluated_at: string;
  duration_ms?: number;
}

export interface CreateTestCaseRequest {
  name?: string;
  description?: string;
  category?: string;
  input: string;
  expected_output?: string;
  context?: Record<string, unknown>;
  is_positive?: boolean;
  evaluation_type?: 'exact' | 'contains' | 'llm_judge';
  evaluation_criteria?: Record<string, unknown>;
  is_baseline?: boolean;
}

export interface UpdateTestCaseRequest {
  name?: string;
  description?: string;
  category?: string;
  input?: string;
  expected_output?: string;
  context?: Record<string, unknown>;
  is_positive?: boolean;
  evaluation_type?: 'exact' | 'contains' | 'llm_judge';
  evaluation_criteria?: Record<string, unknown>;
  is_baseline?: boolean;
  is_active?: boolean;
}

export type EvaluationType = 'exact' | 'contains' | 'llm_judge';

export interface TestCaseResponse {
  id: string;
  agent_id: string;
  name?: string;
  description?: string;
  category?: string;
  input: string;
  expected_output?: string;
  context?: Record<string, unknown>;
  source: 'chat_feedback' | 'manual' | 'imported';
  is_positive: boolean;
  evaluation_type: EvaluationType;
  evaluation_criteria?: Record<string, unknown>;
  last_result?: TestCaseLastResult;
  pass_count: number;
  fail_count: number;
  is_active: boolean;
  is_baseline: boolean;
  created_at: string;
  updated_at: string;
}

export interface TestCaseListResponse {
  items: TestCaseResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface ImportTestCasesResult {
  imported: number;
  skipped: number;
  errors: string[];
}

// Evaluation Types
export interface EvaluationRequest {
  version_id?: string;
  baseline_only?: boolean;
  test_case_ids?: string[];
}

export interface CategoryBreakdown {
  accuracy: number;
  passed: number;
  failed: number;
  total: number;
}

export interface AccuracySummary {
  agent_id: string;
  version_id?: string;
  accuracy: number;
  total_tests: number;
  passed_tests: number;
  failed_tests: number;
  category_breakdown: Record<string, CategoryBreakdown>;
  last_evaluated_at?: string;
}

export interface AccuracyHistoryEntry {
  id: string;
  version_id?: string;
  version?: string;
  accuracy: number;
  total_tests: number;
  passed_tests: number;
  failed_tests: number;
  accuracy_delta?: number;
  evaluated_at: string;
}

export interface TestRunResult {
  test_case_id: string;
  passed: boolean;
  actual_output: string;
  expected_output?: string;
  score?: number;
  evaluation_type: string;
  duration_ms: number;
  tokens_used?: number;
}

export interface EvaluationProgress {
  total: number;
  completed: number;
  passed: number;
  failed: number;
  current_test_case_id?: string;
  current_test_case_name?: string;
}

export interface EvaluationResult {
  evaluation_run_id: string;
  agent_id: string;
  version_id?: string;
  accuracy: number;
  total_tests: number;
  passed_tests: number;
  failed_tests: number;
  duration_ms: number;
  category_breakdown: Record<string, CategoryBreakdown>;
  test_results: TestRunResult[];
  evaluated_at: string;
}

// Deploy Gate Types
export interface DeployGateCheck {
  name: string;
  passed: boolean;
  value: string;
  threshold?: string;
  message: string;
}

export interface DeployGateStatus {
  can_deploy: boolean;
  checks: DeployGateCheck[];
  current_accuracy?: number;
  new_accuracy?: number;
  accuracy_delta?: number;
  current_version?: string;
  new_version: string;
}

export interface DeployRequest {
  force?: boolean;
  notes?: string;
  force_reason?: string;
}

export interface DeployResult {
  success: boolean;
  version_id: string;
  version: string;
  action: 'deploy' | 'blocked';
  gate_checks: DeployGateCheck[];
  accuracy_at_deploy?: number;
  block_reason?: string;
  deployed_at?: string;
}

export interface RollbackRequest {
  target_version_id: string;
  reason: string;
}

export interface RollbackResult {
  success: boolean;
  previous_version_id: string;
  current_version_id: string;
  reason: string;
  rolled_back_at: string;
}

export interface DeployHistoryEntry {
  id: string;
  version_id: string;
  version: string;
  action: string;
  status: string;
  gate_check_passed: boolean;
  accuracy_at_deploy?: number;
  previous_accuracy?: number;
  accuracy_delta?: number;
  block_reason?: string;
  deployed_by: string;
  notes?: string;
  force_deployed?: boolean;
  force_reason?: string;
  created_at: string;
}

// ============================================================================
// Resilient SSE Stream with Automatic Reconnection (Phase A.1)
// ============================================================================

export interface ResilientStreamConfig {
  maxRetries?: number;
  baseDelay?: number;
  maxDelay?: number;
  onReconnect?: (attempt: number) => void;
}

export type StreamState = 'connecting' | 'streaming' | 'reconnecting' | 'completed' | 'failed' | 'aborted';

/**
 * Create a resilient agent message stream with automatic retry on failure.
 *
 * Features:
 * - Exponential backoff (1s → 2s → 4s → 8s → 16s → 30s cap)
 * - Max retry limit (default: 5)
 * - Connection state tracking
 * - Transparent recovery for transient failures
 */
export function createResilientAgentStream(
  agentId: string,
  sessionId: string,
  request: StreamMessageRequest,
  callbacks: {
    onToken: (token: string) => void;
    onComplete: (messageId: string, fullResponse: string) => void;
    onError: (error: string) => void;
    onReconnecting?: (attempt: number) => void;
    onReconnected?: () => void;
  },
  config: ResilientStreamConfig = {}
): { abort: () => void; retry: () => void; getState: () => StreamState } {
  const {
    maxRetries = 5,
    baseDelay = 1000,
    maxDelay = 30000,
  } = config;

  let retryCount = 0;
  let retryTimeout: ReturnType<typeof setTimeout> | null = null;
  let currentAbort: (() => void) | null = null;
  let fullResponse = '';
  let isAborted = false;
  let state: StreamState = 'connecting';

  const attemptStream = () => {
    if (isAborted) return;

    state = retryCount > 0 ? 'reconnecting' : 'connecting';
    if (retryCount > 0) {
      callbacks.onReconnecting?.(retryCount);
    }

    currentAbort = sendMessageStream(
      agentId,
      sessionId,
      request,
      {
        onToken: (token) => {
          state = 'streaming';
          if (retryCount > 0) {
            // Successfully reconnected
            callbacks.onReconnected?.();
            retryCount = 0;
          }
          fullResponse += token;
          callbacks.onToken(token);
        },
        onComplete: (messageId, _response) => {
          state = 'completed';
          callbacks.onComplete(messageId, fullResponse);
        },
        onError: (error) => {
          if (isAborted) return;

          // Check if we should retry
          const isTransient = isTransientError(error);

          if (isTransient && retryCount < maxRetries) {
            retryCount++;
            const delay = Math.min(baseDelay * Math.pow(2, retryCount - 1), maxDelay);
            state = 'reconnecting';
            callbacks.onReconnecting?.(retryCount);

            retryTimeout = setTimeout(() => {
              attemptStream();
            }, delay);
          } else {
            state = 'failed';
            callbacks.onError(
              retryCount >= maxRetries
                ? `Connection failed after ${maxRetries} retries: ${error}`
                : error
            );
          }
        },
      }
    );
  };

  // Start the initial stream
  attemptStream();

  return {
    abort: () => {
      isAborted = true;
      state = 'aborted';
      if (retryTimeout) {
        clearTimeout(retryTimeout);
      }
      currentAbort?.();
    },
    retry: () => {
      if (state === 'failed' || state === 'aborted') {
        isAborted = false;
        retryCount = 0;
        fullResponse = '';
        attemptStream();
      }
    },
    getState: () => state,
  };
}

/**
 * Check if an error is transient (worth retrying)
 */
function isTransientError(error: string): boolean {
  const transientPatterns = [
    /network/i,
    /timeout/i,
    /connection/i,
    /fetch/i,
    /ECONNRESET/i,
    /ETIMEDOUT/i,
    /5\d{2}/, // 5xx errors
  ];

  return transientPatterns.some(pattern => pattern.test(error));
}

// ============================================================================
// Chat Session API
// ============================================================================

export async function createChatSession(
  agentId: string,
  request: CreateChatSessionRequest = {}
): Promise<ChatSessionResponse> {
  return apiFetch<ChatSessionResponse>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/chat/sessions`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

export async function listChatSessions(
  agentId: string,
  options: { limit?: number; offset?: number; status?: string } = {}
): Promise<ChatSessionSummary[]> {
  const params = new URLSearchParams();
  if (options.limit) params.set('limit', String(options.limit));
  if (options.offset) params.set('offset', String(options.offset));
  if (options.status) params.set('status', options.status);

  const queryString = params.toString();
  const url = `${AGENT_STUDIO_PREFIX}/agents/${agentId}/chat/sessions${queryString ? `?${queryString}` : ''}`;

  return apiFetch<ChatSessionSummary[]>(url);
}

export async function getChatSession(
  agentId: string,
  sessionId: string
): Promise<ChatSessionResponse> {
  return apiFetch<ChatSessionResponse>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/chat/sessions/${sessionId}`
  );
}

export async function deleteChatSession(
  agentId: string,
  sessionId: string
): Promise<void> {
  await apiFetch<void>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/chat/sessions/${sessionId}`,
    { method: 'DELETE' }
  );
}

export async function submitFeedback(
  agentId: string,
  sessionId: string,
  messageId: string,
  request: FeedbackRequest
): Promise<FeedbackResponse> {
  return apiFetch<FeedbackResponse>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/chat/sessions/${sessionId}/messages/${messageId}/feedback`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

export interface StreamMessageRequest {
  content: string;
  model?: string;
  system_prompt?: string;
  context?: Record<string, unknown>;
  enhancedMode?: boolean;
}

/**
 * Tool call event from enhanced SSE stream
 */
export interface ToolCallEvent {
  toolCallId: string;
  toolName: string;
  args?: Record<string, unknown>;
  status?: 'running' | 'success' | 'error';
  result?: unknown;
  error?: string;
  durationMs?: number;
}

/**
 * Enhanced SSE callbacks for rich rendering
 */
export interface EnhancedStreamCallbacks {
  onToken: (token: string) => void;
  onComplete: (messageId: string, fullResponse: string) => void;
  onError: (error: string) => void;
  onToolCallStart?: (event: ToolCallEvent) => void;
  onToolCallComplete?: (event: ToolCallEvent) => void;
  onReasoning?: (content: string) => void;
}

/**
 * Send a message and receive streaming response via SSE.
 * When enhancedMode is true, sends enhanced SSE events with tool calls, reasoning, etc.
 */
export function sendMessageStream(
  agentId: string,
  sessionId: string,
  request: StreamMessageRequest,
  callbacks: EnhancedStreamCallbacks,
): () => void {
  const url = `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}${AGENT_STUDIO_PREFIX}/agents/${agentId}/chat/sessions/${sessionId}/messages`;
  const abortController = new AbortController();

  let fullResponse = '';
  let messageId = '';

  // Use EventSource-style SSE parsing for enhanced mode
  const enhanced = request.enhancedMode;

  // Read CSRF token from cookie (same as apiFetch)
  const csrfMatch = typeof document !== 'undefined'
    ? document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)
    : null;
  const csrfToken = csrfMatch ? decodeURIComponent(csrfMatch[1]) : undefined;

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken;

  fetch(url, {
    method: 'POST',
    headers,
    credentials: 'include',
    signal: abortController.signal,
    body: JSON.stringify(request),
  })
    .then(async (response) => {
      if (!response.ok) {
        let errorDetail = `HTTP error ${response.status}`;
        try {
          const errorData = await response.json();
          errorDetail = errorData.detail || errorData.message || errorDetail;
        } catch {
          errorDetail = response.statusText || errorDetail;
        }
        throw new Error(errorDetail);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';
      let currentEventType = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (enhanced) {
            // Enhanced SSE: parse event: and data: lines
            if (line.startsWith('event: ')) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                handleEnhancedEvent(currentEventType, data);
              } catch { /* skip unparseable */ }
              currentEventType = '';
            }
          } else {
            // Legacy: parse data: lines only
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.slice(6)) as SSEEvent;
                handleLegacyEvent(event);
              } catch { /* skip */ }
            }
          }
        }
      }

      // Process remaining buffer
      if (buffer.startsWith('data: ')) {
        try {
          const data = JSON.parse(buffer.slice(6));
          if (enhanced) {
            handleEnhancedEvent(currentEventType, data);
          } else {
            handleLegacyEvent(data as SSEEvent);
          }
        } catch { /* skip */ }
      }

      // Stream ended without done event
      if (fullResponse && !messageId) {
        callbacks.onComplete(`msg-${Date.now()}`, fullResponse);
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        callbacks.onError(err.message || 'Network error');
      }
    });

  function handleLegacyEvent(event: SSEEvent) {
    if ((event.type === 'token' || event.type === 'delta') && event.content) {
      fullResponse += event.content;
      callbacks.onToken(event.content);
    } else if (event.type === 'message_id' && event.message_id) {
      messageId = event.message_id;
    } else if (event.type === 'message' && event.message_id) {
      messageId = event.message_id;
    } else if (event.type === 'done') {
      callbacks.onComplete(messageId, fullResponse);
    } else if (event.type === 'error') {
      callbacks.onError(event.error || event.message || 'Unknown error');
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function handleEnhancedEvent(eventType: string, data: any) {
    switch (eventType) {
      case 'text_delta':
        if (data.delta) {
          fullResponse += data.delta;
          callbacks.onToken(data.delta);
        }
        break;
      case 'reasoning':
        callbacks.onReasoning?.(data.content || '');
        break;
      case 'tool_call_start':
        callbacks.onToolCallStart?.({
          toolCallId: data.toolCallId,
          toolName: data.toolName,
          args: data.args,
          status: 'running',
        });
        break;
      case 'tool_call_complete':
        callbacks.onToolCallComplete?.({
          toolCallId: data.toolCallId,
          toolName: data.toolName || '',
          status: data.status === 'success' ? 'success' : 'error',
          result: data.result,
          error: data.error,
          durationMs: data.durationMs,
        });
        break;
      case 'done':
        messageId = data.messageId || messageId;
        callbacks.onComplete(messageId, fullResponse);
        break;
      case 'error':
        callbacks.onError(data.message || data.error || 'Unknown error');
        break;
      default:
        // Handle any other events (source, part, etc.) - could be extended later
        break;
    }
  }

  return () => abortController.abort();
}

// ============================================================================
// Workflow Execution API
// ============================================================================

/**
 * Execute a workflow proposed by the agent during chat.
 * Returns a ReadableStream for SSE progress events.
 */
export async function executeWorkflow(
  agentId: string,
  sessionId: string,
  workflowId: string,
  inputs: Record<string, any>,
): Promise<Response> {
  const url = `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}${AGENT_STUDIO_PREFIX}/agents/${agentId}/chat/sessions/${sessionId}/workflow-execute`;

  const csrfMatch = typeof document !== 'undefined'
    ? document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)
    : null;
  const csrfToken = csrfMatch ? decodeURIComponent(csrfMatch[1]) : undefined;

  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken;

  const response = await fetch(url, {
    method: 'POST',
    headers,
    credentials: 'include',
    body: JSON.stringify({ workflow_id: workflowId, inputs }),
  });

  if (!response.ok) {
    let errorDetail = `HTTP error ${response.status}`;
    try {
      const errorData = await response.json();
      errorDetail = errorData.detail || errorData.message || errorDetail;
    } catch {
      errorDetail = response.statusText || errorDetail;
    }
    throw new Error(errorDetail);
  }

  return response;
}

// ============================================================================
// Test Case API
// ============================================================================

export async function listTestCases(
  agentId: string,
  options: {
    category?: string;
    is_baseline?: boolean;
    source?: string;
    limit?: number;
    offset?: number;
  } = {}
): Promise<TestCaseListResponse> {
  const params = new URLSearchParams();
  if (options.category) params.set('category', options.category);
  if (options.is_baseline !== undefined) params.set('is_baseline', String(options.is_baseline));
  if (options.source) params.set('source', options.source);
  if (options.limit) params.set('limit', String(options.limit));
  if (options.offset) params.set('offset', String(options.offset));

  const queryString = params.toString();
  const url = `${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases${queryString ? `?${queryString}` : ''}`;

  return apiFetch<TestCaseListResponse>(url);
}

export async function createTestCase(
  agentId: string,
  request: CreateTestCaseRequest
): Promise<TestCaseResponse> {
  return apiFetch<TestCaseResponse>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

export async function getTestCase(
  agentId: string,
  testCaseId: string
): Promise<TestCaseResponse> {
  return apiFetch<TestCaseResponse>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases/${testCaseId}`
  );
}

export async function updateTestCase(
  agentId: string,
  testCaseId: string,
  request: UpdateTestCaseRequest
): Promise<TestCaseResponse> {
  return apiFetch<TestCaseResponse>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases/${testCaseId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(request),
    }
  );
}

export async function deleteTestCase(
  agentId: string,
  testCaseId: string
): Promise<void> {
  await apiFetch<void>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases/${testCaseId}`,
    { method: 'DELETE' }
  );
}

export async function importTestCases(
  agentId: string,
  file: File
): Promise<ImportTestCasesResult> {
  const formData = new FormData();
  formData.append('file', file);

  const url = `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases/import`;

  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  });

  if (!response.ok) {
    throw new APIError(`Import failed: ${response.status}`, response.status);
  }

  return response.json();
}

export async function exportTestCases(
  agentId: string,
  format: 'json' | 'csv' = 'json'
): Promise<Blob> {
  const url = `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases/export?format=${format}`;

  const response = await fetch(url, { credentials: 'include' });

  if (!response.ok) {
    throw new APIError(`Export failed: ${response.status}`, response.status);
  }

  return response.blob();
}

// ============================================================================
// Evaluation API
// ============================================================================

export async function getAccuracySummary(
  agentId: string,
  versionId?: string
): Promise<AccuracySummary> {
  const params = versionId ? `?version_id=${versionId}` : '';
  return apiFetch<AccuracySummary>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/accuracy${params}`
  );
}

export async function getAccuracyHistory(
  agentId: string,
  limit: number = 30
): Promise<AccuracyHistoryEntry[]> {
  return apiFetch<AccuracyHistoryEntry[]>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/accuracy/history?limit=${limit}`
  );
}

export async function runSingleTest(
  agentId: string,
  testCaseId: string
): Promise<TestRunResult> {
  return apiFetch<TestRunResult>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases/${testCaseId}/run`,
    {
      method: 'POST',
      timeout: 60000, // 60 seconds - LLM calls can take time
    }
  );
}

/**
 * Run evaluation with streaming progress updates
 */
export function runEvaluation(
  agentId: string,
  request: EvaluationRequest = {},
  callbacks: {
    onProgress: (progress: EvaluationProgress) => void;
    onResult: (result: EvaluationResult) => void;
    onError: (error: Error) => void;
  }
): () => void {
  const url = `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}${AGENT_STUDIO_PREFIX}/agents/${agentId}/evaluate`;

  return createSSEStream({
    url,
    body: request,
    onEvent: (event) => {
      if (event.type === 'progress' && event.data) {
        callbacks.onProgress(event.data as EvaluationProgress);
      } else if (event.type === 'result' && event.data) {
        callbacks.onResult(event.data as EvaluationResult);
      } else if (event.type === 'error') {
        callbacks.onError(new Error(event.message || 'Unknown error'));
      }
    },
    onError: callbacks.onError,
  });
}

// ============================================================================
// Deploy Gate API
// ============================================================================

export async function getDeployGateStatus(
  agentId: string,
  versionId: string
): Promise<DeployGateStatus> {
  return apiFetch<DeployGateStatus>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/deploy-gate/status?version_id=${versionId}`
  );
}

export async function deployVersion(
  agentId: string,
  versionId: string,
  request: DeployRequest = {}
): Promise<DeployResult> {
  return apiFetch<DeployResult>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/versions/${versionId}/deploy`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

export async function rollbackVersion(
  agentId: string,
  versionId: string,
  request: RollbackRequest
): Promise<RollbackResult> {
  return apiFetch<RollbackResult>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/versions/${versionId}/rollback`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

export async function getDeployHistory(
  agentId: string,
  limit: number = 20
): Promise<DeployHistoryEntry[]> {
  return apiFetch<DeployHistoryEntry[]>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/deploy-history?limit=${limit}`
  );
}


// ============================================================================
// Test Case Generation
// ============================================================================

export interface GeneratedTestCase {
  input: string;
  expected_output: string;
  category: string;
  evaluation_type: 'exact' | 'contains' | 'llm_judge';
}

export interface GenerateTestsResponse {
  test_cases: GeneratedTestCase[];
}

export async function generateTestCases(
  agentId: string,
  instructions: string,
  count: number = 5,
): Promise<GenerateTestsResponse> {
  return apiFetch<GenerateTestsResponse>(
    `${AGENT_STUDIO_PREFIX}/agents/${agentId}/test-cases/generate`,
    {
      method: 'POST',
      body: JSON.stringify({ instructions, count }),
    }
  );
}
