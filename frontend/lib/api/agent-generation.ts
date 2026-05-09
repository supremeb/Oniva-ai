/**
 * Agent Generation API client (Phase A: Chat-First Creation).
 *
 * SSE stream for generating agent configurations from natural language.
 */

import { API_BASE_URL } from './index';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ClarifyQuestion {
  id: string;
  text: string;
  type: 'multiple_choice' | 'free_text';
  options?: string[];
}

export interface ClarifyResponse {
  questions: ClarifyQuestion[];
  should_generate: boolean;
}

export interface GeneratedAgentConfig {
  name: string;
  description: string;
  system_prompt: string;
  model: string;
  temperature: number;
  max_tokens: number;
  skills: string[];
  connectors: string[];
  icon: string;
  agent_type: string;
  reasoning: {
    skill_reasons: Record<string, string>;
    connector_reasons: Record<string, string>;
    model_reason: string;
  };
}

export interface SuggestedSkill {
  id: string;
  reason: string;
}

export interface SuggestedConnector {
  id: string;
  reason: string;
}

export interface AgentGenerationCallbacks {
  onAgentName: (name: string) => void;
  onAgentDescription: (description: string) => void;
  onInstructionChunk: (delta: string) => void;
  onInstructionComplete: () => void;
  onSkillsSuggested: (skills: SuggestedSkill[]) => void;
  onConnectorsSuggested: (connectors: SuggestedConnector[]) => void;
  onModelRecommended: (data: {
    model: string;
    temperature: number;
    max_tokens: number;
    reason: string;
  }) => void;
  onGenerationComplete: (config: GeneratedAgentConfig) => void;
  onError: (message: string) => void;
}

// ---------------------------------------------------------------------------
// Clarify API
// ---------------------------------------------------------------------------

/**
 * Get clarifying questions before generating an agent.
 */
export async function clarifyAgent(
  description: string,
  context?: Array<{ question: string; answer: string }>,
): Promise<ClarifyResponse> {
  const csrfToken = document.cookie
    .split('; ')
    .find((row) => row.startsWith('csrf_token='))
    ?.split('=')[1];

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (csrfToken) {
    headers['X-CSRF-Token'] = csrfToken;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/agents/clarify`, {
      method: 'POST',
      headers,
      credentials: 'include',
      signal: controller.signal,
      body: JSON.stringify({ description, context }),
    });
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    let detail = `HTTP error ${response.status}`;
    try {
      const err = await response.json();
      detail = err.detail || err.message || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }

  return response.json();
}

// ---------------------------------------------------------------------------
// SSE Stream
// ---------------------------------------------------------------------------

/**
 * Start an SSE stream to generate an agent from a description.
 * Returns an abort function to cancel the stream.
 */
export function generateAgent(
  description: string,
  callbacks: AgentGenerationCallbacks,
  clarificationContext?: Array<{ question: string; answer: string }>,
): () => void {
  const abortController = new AbortController();

  // Get CSRF token from cookie
  const csrfToken = document.cookie
    .split('; ')
    .find((row) => row.startsWith('csrf_token='))
    ?.split('=')[1];

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (csrfToken) {
    headers['X-CSRF-Token'] = csrfToken;
  }

  fetch(`${API_BASE_URL}/api/v1/agents/generate`, {
    method: 'POST',
    headers,
    credentials: 'include',
    signal: abortController.signal,
    body: JSON.stringify({
      description,
      ...(clarificationContext && { clarification_context: clarificationContext }),
    }),
  })
    .then(async (response) => {
      if (!response.ok) {
        let detail = `HTTP error ${response.status}`;
        try {
          const err = await response.json();
          detail = err.detail || err.message || detail;
        } catch {
          // ignore
        }
        callbacks.onError(detail);
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        callbacks.onError('No response body');
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';
      let currentEventType: string | null = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line) {
            currentEventType = null;
            continue;
          }
          if (line.startsWith(':')) continue; // SSE ping/comment

          if (line.startsWith('event:')) {
            currentEventType = line.slice(6).trim();
            continue;
          }

          if (line.startsWith('data:')) {
            const data = line.slice(5).trim();
            if (!data) continue;

            try {
              const parsed = JSON.parse(data);
              handleEvent(currentEventType, parsed, callbacks);
            } catch {
              // ignore malformed JSON
            }
          }
        }
      }

      // Flush remaining buffer
      if (buffer.trim()) {
        const line = buffer.trim();
        if (line.startsWith('data:')) {
          try {
            const parsed = JSON.parse(line.slice(5).trim());
            handleEvent(currentEventType, parsed, callbacks);
          } catch {
            // ignore
          }
        }
      }
    })
    .catch((error) => {
      if (error.name !== 'AbortError') {
        callbacks.onError(error.message || 'Connection failed');
      }
    });

  return () => abortController.abort();
}

function handleEvent(
  eventType: string | null,
  data: Record<string, unknown>,
  callbacks: AgentGenerationCallbacks,
) {
  switch (eventType) {
    case 'agent_name':
      callbacks.onAgentName(data.name as string);
      break;
    case 'agent_description':
      callbacks.onAgentDescription(data.description as string);
      break;
    case 'instruction_chunk':
      callbacks.onInstructionChunk(data.delta as string);
      break;
    case 'instruction_complete':
      callbacks.onInstructionComplete();
      break;
    case 'skills_suggested':
      callbacks.onSkillsSuggested(data.skills as SuggestedSkill[]);
      break;
    case 'connectors_suggested':
      callbacks.onConnectorsSuggested(data.connectors as SuggestedConnector[]);
      break;
    case 'model_recommended':
      callbacks.onModelRecommended(data as {
        model: string;
        temperature: number;
        max_tokens: number;
        reason: string;
      });
      break;
    case 'generation_complete':
      callbacks.onGenerationComplete(data.config as GeneratedAgentConfig);
      break;
    case 'generation_error':
      callbacks.onError(data.message as string);
      break;
  }
}
