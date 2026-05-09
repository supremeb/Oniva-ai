/**
 * Centralized model definitions for the application.
 * Single source of truth for supported LLM models and their capabilities.
 */

// ============================================================================
// Source / Citation Types
// ============================================================================

export interface SourceItem {
  title: string;
  url: string;
  snippet?: string;
  favicon?: string;
  sourceType?: 'url' | 'document' | 'memory';
}

// ============================================================================
// Chat Message Part Types
// ============================================================================

/**
 * Question option for agent interviews
 */
export interface QuestionOption {
    id: string;
    label: string;
    description?: string;
}

/**
 * Structured question with multiple-choice options (deep work / ask_user)
 */
export interface StructuredQuestion {
    question: string;
    options: QuestionOption[];
    multiSelect?: boolean;
}

/**
 * Task item for session-scoped task tracking (deep work)
 */
export interface TaskItem {
    id: string;
    title: string;
    description?: string;
    status: 'pending' | 'in_progress' | 'completed';
    createdAt: string;
}

/**
 * Action for confirmation prompts
 */
export interface ConfirmationAction {
    id: string;
    label: string;
    variant?: 'primary' | 'secondary' | 'destructive' | 'ghost';
    icon?: string;
}

/**
 * Tool call step for progress tracking
 */
export interface ToolCallStep {
    id: string;
    label: string;
    status: 'pending' | 'active' | 'complete' | 'error' | 'skipped';
    message?: string;
    startedAt?: string;
    completedAt?: string;
}

/**
 * Approval item for review
 */
export interface ApprovalItem {
    label: string;
    value: string;
    sensitive?: boolean;
}

/**
 * Form field definition
 */
export interface FormField {
    id: string;
    label: string;
    type: 'text' | 'number' | 'email' | 'password' | 'textarea' | 'select';
    placeholder?: string;
    required?: boolean;
    defaultValue?: string;
    options?: { value: string; label: string }[];
}

/**
 * Table column definition
 */
export interface DataTableColumn {
    key: string;
    header: string;
    sortable?: boolean;
    width?: string;
}

/**
 * SQL result column
 */
export interface SQLResultColumn {
    name: string;
    type?: string;
}

/**
 * Diff line (matches DiffViewer component)
 */
export interface DiffLine {
    type: 'add' | 'remove' | 'context' | 'header';
    content: string;
    lineNumber?: {
        old?: number;
        new?: number;
    };
}

/**
 * Timeline event (matches TimelineEvent component)
 */
export interface TimelineEvent {
    title: string;
    description?: string;
    status: 'pending' | 'active' | 'complete' | 'error' | 'skipped';
    timestamp?: string;
}

/**
 * Step in a process
 */
export interface Step {
    id: string;
    label: string;
    description?: string;
    status: 'pending' | 'active' | 'complete' | 'error' | 'skipped';
}

/**
 * Union type for all possible message parts
 * These represent the structured components of an AI chat message
 */
export type MessagePart =
    // ===================
    // TEXT & CODE
    // ===================
    | { type: 'text'; content: string; isStreaming?: boolean }
    | { type: 'code'; language?: string; content: string; filename?: string }

    // ===================
    // TOOL CALLS
    // ===================
    | {
          type: 'tool_call';
          id: string;
          toolName: string;
          status: 'pending' | 'running' | 'success' | 'error';
          args?: Record<string, unknown>;
          result?: unknown;
          error?: string;
          durationMs?: number;
          steps?: ToolCallStep[];
          label?: string;
          description?: string;
          sources?: SourceItem[];
      }

    // ===================
    // INTERACTIVE
    // ===================
    | {
          type: 'question';
          id: string;
          header: string;
          question: string;
          options: QuestionOption[];
          multiSelect?: boolean;
          timeout?: number;
          defaultOptionId?: string;
          includeOther?: boolean;
      }
    | {
          type: 'approval';
          id: string;
          title: string;
          description?: string;
          riskLevel?: 'low' | 'medium' | 'high' | 'critical';
          items?: ApprovalItem[];
          status?: 'pending' | 'approved' | 'rejected' | 'expired';
      }
    | {
          type: 'form';
          id: string;
          title: string;
          description?: string;
          fields: FormField[];
          submitLabel?: string;
      }
    | {
          type: 'input';
          id: string;
          prompt: string;
          placeholder?: string;
          inputType?: 'text' | 'number' | 'email' | 'url' | 'password';
          required?: boolean;
      }
    | {
          type: 'oauth';
          id: string;
          serviceName: string;
          description?: string;
          scopes?: string[];
          status?: 'idle' | 'authorizing' | 'success' | 'error' | 'expired';
          authUrl?: string;
          accountInfo?: Record<string, string>;
      }
    | {
          type: 'confirmation';
          id: string;
          prompt: string;
          description?: string;
          actions: ConfirmationAction[];
      }

    // ===================
    // DATA VISUALIZATION
    // ===================
    | {
          type: 'table';
          columns: DataTableColumn[];
          data: Record<string, unknown>[];
          title?: string;
          sortable?: boolean;
          searchable?: boolean;
      }
    | {
          type: 'chart';
          chartType: 'line' | 'bar' | 'pie' | 'area' | 'scatter';
          title?: string;
          description?: string;
          imageUrl?: string;
          svgContent?: string;
          data?: unknown;
          config?: Record<string, unknown>;
      }
    | {
          type: 'source';
          sourceType: 'document' | 'url' | 'database' | 'file' | 'api';
          title: string;
          description?: string;
          url?: string;
          metadata?: Record<string, unknown>;
          citationNumber?: number;
      }
    | {
          type: 'file';
          filename: string;
          size?: number;
          fileType?: string;
          downloadUrl?: string;
          content?: string;
          // Enriched artifact fields (output engine)
          title?: string;
          description?: string;
          previewUrl?: string;
          artifactType?: string;
          artifactUri?: string;
          tags?: string[];
          createdAt?: string;
          pageCount?: number;
          metadata?: Record<string, unknown>;
      }
    | {
          type: 'image';
          src: string;
          alt?: string;
          caption?: string;
          aspectRatio?: '16:9' | '4:3' | '1:1' | 'auto';
      }
    | {
          type: 'link';
          url: string;
          title?: string;
          description?: string;
          imageUrl?: string;
          faviconUrl?: string;
          siteName?: string;
      }
    | {
          type: 'sql';
          query: string;
          database?: string;
          status?: 'idle' | 'running' | 'success' | 'error';
          executionTime?: number;
          rowCount?: number;
          error?: string;
      }
    | {
          type: 'sql_result';
          columns: SQLResultColumn[];
          rows: Record<string, unknown>[];
          executionTime?: number;
          totalRows?: number;
      }
    | {
          type: 'json';
          data: unknown;
          title?: string;
          defaultCollapsedDepth?: number;
      }
    | {
          type: 'diff';
          diff?: string;
          lines?: DiffLine[];
          title?: string;
          oldFileName?: string;
          newFileName?: string;
      }
    | {
          type: 'timeline';
          events: TimelineEvent[];
      }
    | {
          type: 'metric';
          label: string;
          value: string | number;
          change?: string;
          trend?: 'up' | 'down' | 'neutral';
          prefix?: string;
          suffix?: string;
          context?: string;
      }

    // ===================
    // STATUS
    // ===================
    | {
          type: 'steps';
          steps: Step[];
          orientation?: 'horizontal' | 'vertical';
      }
    | {
          type: 'thinking';
          variant?: 'default' | 'minimal' | 'detailed' | 'brain' | 'accordion';
          message?: string;
          stage?: string;
          showElapsed?: boolean;
          /** Streaming thinking/reasoning content (for accordion variant) */
          thinkingContent?: string;
          /** Whether thinking is still in progress */
          isThinking?: boolean;
      }
    | {
          type: 'error';
          id?: string;
          message: string;
          title?: string;
          details?: string;
          retryable?: boolean;
      }
    | {
          type: 'success';
          message: string;
          title?: string;
          details?: string;
      }
    | {
          type: 'warning';
          message: string;
          title?: string;
          details?: string;
      }
    | {
          type: 'info';
          message: string;
          title?: string;
          details?: string;
      }

    // ===================
    // LEGACY (for backwards compatibility)
    // ===================
    | {
          type: 'interview_summary';
          decisions: { header: string; choice: string; optionId?: string }[];
      }
    | {
          type: 'sql_preview';
          id: string;
          sql: string;
          estimatedCost?: number;
          estimatedRows?: number;
          tablesUsed?: string[];
          requiresApproval?: boolean;
      }
    | {
          type: 'correction_request';
          queryId: string;
          originalQuestion: string;
          generatedSql: string;
      }
    | {
          type: 'step_progress';
          steps: {
              id: string;
              label: string;
              status: 'pending' | 'current' | 'complete' | 'error';
              description?: string;
          }[];
      }

    // ===================
    // DEEP WORK (plan-first autonomous)
    // ===================
    | {
          type: 'plan';
          title: string;
          steps: string[];
          status?: 'pending' | 'approved' | 'rejected' | 'editing';
      }

    // ===================
    // MEMORY
    // ===================
    | {
          type: 'memory_saved';
          memoryId: string;
          snippet: string;
          agentName?: string;
          actionType?: string;
          toolCallId?: string;
      };

/**
 * Chat message with structured parts
 */
export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant' | 'system';
    parts: MessagePart[];
    timestamp: Date;
    metadata?: {
        model?: string;
        tokens?: number;
        latencyMs?: number;
    };
}

/**
 * Interview state for tracking multi-question flows
 */
export interface InterviewState {
    sessionId: string;
    currentQuestionIndex: number;
    totalQuestions: number;
    answers: {
        questionId: string;
        selectedOptionIds: string[];
        customText?: string;
    }[];
    isComplete: boolean;
}

/**
 * Response payload for interactive message parts
 */
export interface MessagePartResponse {
    messageId: string;
    partId: string;
    responseType: 'question_answer' | 'confirmation' | 'form_submit' | 'sql_correction';
    payload: {
        selectedOptionIds?: string[];
        customText?: string;
        approved?: boolean;
        formData?: Record<string, unknown>;
        correctedSql?: string;
    };
}

// ============================================================================
// LLM Model Definitions
// ============================================================================

export interface ModelDefinition {
    id: string;
    name: string;
    provider: 'OpenAI' | 'Anthropic' | 'Google' | 'OpenRouter';
    capabilities: ('chat' | 'vision' | 'function_calling' | 'streaming' | 'thinking')[];
    contextLength?: number;
    costPerMToken?: number; // Cost per million tokens (input)
}

/**
 * Check if a model supports extended thinking / reasoning mode.
 */
export function modelSupportsThinking(modelId: string): boolean {
    const model = getModelById(modelId);
    if (model?.capabilities.includes('thinking')) return true;
    // Also check by model ID patterns for dynamically-added models
    const id = modelId.toLowerCase();
    return (
        id.includes('claude-3') ||
        id.includes('claude-4') ||
        id.includes('o1') ||
        id.includes('o3') ||
        id.includes('deepseek-r1') ||
        id.includes('deepseek-reasoner')
    );
}

/**
 * All supported models across providers.
 * Keep this in sync with backend model validation.
 */
export const SUPPORTED_MODELS: ModelDefinition[] = [
    // OpenRouter Models
    {
        id: 'nex-agi/deepseek-v3.1-nex-n1:free',
        name: 'DeepSeek V3.1 (Free)',
        provider: 'OpenRouter',
        capabilities: ['chat', 'streaming'],
        contextLength: 64000,
    },
    {
        id: 'google/gemini-2.0-flash-001',
        name: 'Gemini 2.0 Flash',
        provider: 'OpenRouter',
        capabilities: ['chat', 'vision', 'streaming'],
        contextLength: 1000000,
    },
    {
        id: 'deepseek/deepseek-chat',
        name: 'DeepSeek Chat',
        provider: 'OpenRouter',
        capabilities: ['chat', 'streaming'],
        contextLength: 64000,
    },
    {
        id: 'anthropic/claude-3.5-sonnet',
        name: 'Claude 3.5 Sonnet (via OpenRouter)',
        provider: 'OpenRouter',
        capabilities: ['chat', 'vision', 'function_calling', 'streaming', 'thinking'],
        contextLength: 200000,
    },
    {
        id: 'openai/gpt-4o-mini',
        name: 'GPT-4o Mini (via OpenRouter)',
        provider: 'OpenRouter',
        capabilities: ['chat', 'vision', 'function_calling', 'streaming'],
        contextLength: 128000,
    },
    // Direct API Models (require provider-specific API keys)
    // OpenAI - requires OPENAI_API_KEY
    {
        id: 'gpt-4o',
        name: 'GPT-4o (Direct - needs OpenAI key)',
        provider: 'OpenAI',
        capabilities: ['chat', 'vision', 'function_calling', 'streaming'],
        contextLength: 128000,
    },
    {
        id: 'gpt-4o-mini',
        name: 'GPT-4o Mini (Direct - needs OpenAI key)',
        provider: 'OpenAI',
        capabilities: ['chat', 'vision', 'function_calling', 'streaming'],
        contextLength: 128000,
    },
    // Anthropic - requires ANTHROPIC_API_KEY
    {
        id: 'claude-3-5-sonnet',
        name: 'Claude 3.5 Sonnet (Direct - needs Anthropic key)',
        provider: 'Anthropic',
        capabilities: ['chat', 'vision', 'function_calling', 'streaming', 'thinking'],
        contextLength: 200000,
    },
    {
        id: 'claude-3-opus',
        name: 'Claude 3 Opus (Direct - needs Anthropic key)',
        provider: 'Anthropic',
        capabilities: ['chat', 'vision', 'function_calling', 'streaming', 'thinking'],
        contextLength: 200000,
    },
    // Google - requires GOOGLE_API_KEY
    {
        id: 'gemini-2.0-flash',
        name: 'Gemini 2.0 Flash (Direct - needs Google key)',
        provider: 'Google',
        capabilities: ['chat', 'vision', 'function_calling', 'streaming'],
        contextLength: 1000000,
    },
];

/**
 * Get models by provider
 */
export function getModelsByProvider(provider: ModelDefinition['provider']): ModelDefinition[] {
    return SUPPORTED_MODELS.filter((m) => m.provider === provider);
}

/**
 * Get model by ID
 */
export function getModelById(id: string): ModelDefinition | undefined {
    const found = SUPPORTED_MODELS.find((m) => m.id === id);
    if (found) return found;

    // For dynamic/tenant-configured models not in the static list,
    // synthesize a display entry from the model ID string
    if (id) {
        const shortName = id.includes('/') ? id.split('/').pop()! : id;
        const displayName = shortName
            .replace(/-/g, ' ')
            .replace(/\b\w/g, (c) => c.toUpperCase());
        return {
            id,
            name: displayName,
            provider: 'OpenRouter',
            capabilities: ['chat', 'streaming'],
        };
    }
    return undefined;
}

// ============================================================================
// Project Workspace Types
// ============================================================================

export type ProjectRole = 'owner' | 'editor' | 'viewer';

export interface Project {
  id: string;
  tenant_id: string;
  owner_id: string;
  name: string;
  description: string | null;
  instructions: string | null;
  default_model: string | null;
  knowledge_base_id: string | null;
  settings: Record<string, unknown>;
  is_archived: boolean;
  row_version: number;
  member_count: number;
  file_count: number;
  session_count: number;
  user_role: ProjectRole;
  created_at: string;
  updated_at: string;
}

export interface ProjectMember {
  id: string | null;
  project_id: string;
  user_id: string;
  role: ProjectRole;
  invited_by: string | null;
  user_email: string | null;
  user_name: string | null;
  created_at: string | null;
}

export interface ProjectFile {
  id: string;
  filename: string;
  file_type: string | null;
  file_size_bytes: number;
  status: string;
  error_message: string | null;
  chunk_count: number;
  created_at: string;
}

/**
 * Legacy format for backwards compatibility with existing code
 */
export const MODELS = SUPPORTED_MODELS.map((m) => ({
    id: m.id,
    name: m.name,
    provider: m.provider,
}));


// ========================================
// Fleet Types
// ========================================

export interface FleetRunItem {
  id: string;
  agent_id: string | null;
  agent_name: string | null;
  agent_icon: string | null;
  job_type: string;
  status: string;
  trigger: string;
  triggered_by: Record<string, unknown> | null;
  progress: number;
  input_data: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  duration_ms: number | null;
  total_tokens: number | null;
}

export interface FleetRunsResponse {
  items: FleetRunItem[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface InterventionResponse {
  message: string;
  sender: string;
  timestamp: string;
}

// ========================================
// Workflow Execution Types (Agent Chat)
// ========================================

export interface WorkflowPreview {
  status: 'pending_approval';
  workflow_id: string;
  workflow_name: string;
  steps: WorkflowStep[];
  has_hitl_nodes: boolean;
  hitl_warning: string | null;
  input_summary: Record<string, any>;
}

export interface WorkflowStep {
  node_id: string;
  label: string;
  type: string;
}

export interface WorkflowProgressEvent {
  workflowId: string;
  stepId: string;
  label: string;
  status: 'active' | 'complete' | 'error' | 'halted';
  message?: string;
  elapsedMs?: number;
}

export interface WorkflowCompleteEvent {
  workflowId: string;
  workflowName: string;
  results: Record<string, any>;
  executedNodes: number;
  totalNodes: number;
  elapsedMs?: number;
}
