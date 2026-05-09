/**
 * Chat Message Types for AI Workflow Builder
 *
 * Supports various message types for rich interactions:
 * - text: Simple text message
 * - oauth_request: OAuth buttons for missing connectors
 * - steps_preview: Preview of proposed steps
 * - confirmation: Success/completion message
 * - question: Question with clickable options
 * - error: Error message
 * - thinking: AI is processing indicator
 */

import type { BaseStep } from './complex-steps';

// Message types enum
export type ChatMessageType =
  | 'text'
  | 'oauth_request'
  | 'steps_preview'
  | 'confirmation'
  | 'question'
  | 'error'
  | 'thinking';

// Question option for interactive questions
export interface QuestionOption {
  id: string;
  label: string;
  value: string;
}

// Connector info for OAuth requests
export interface ConnectorInfo {
  id: string;
  name: string;
  icon?: string;
  isConnected: boolean;
}

// Steps preview metadata
export interface StepsPreviewMetadata {
  steps: BaseStep[];
  applied: boolean;
  pendingApproval: boolean;
  /** Per-message proposed nodes — avoids shared global state overwrite bug */
  proposedNodes?: import('@xyflow/react').Node[];
  /** Per-message proposed edges */
  proposedEdges?: import('@xyflow/react').Edge[];
}

// OAuth request metadata
export interface OAuthRequestMetadata {
  connectors: ConnectorInfo[];
  message?: string;
}

// Question metadata
export interface QuestionMetadata {
  options: QuestionOption[];
  allowMultiple?: boolean;
  selectedValues?: string[];
}

// Union type for all metadata
export type ChatMessageMetadata =
  | { type: 'steps_preview'; data: StepsPreviewMetadata }
  | { type: 'oauth_request'; data: OAuthRequestMetadata }
  | { type: 'question'; data: QuestionMetadata }
  | { type: 'text' | 'confirmation' | 'error' | 'thinking'; data?: undefined };

// Chat message interface
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  type: ChatMessageType;
  content: string;
  timestamp: Date;
  metadata?: ChatMessageMetadata;
}

// Streaming status for AI responses
export type StreamingStatus = 'idle' | 'connecting' | 'streaming' | 'complete' | 'error';

// Chat session state
export interface ChatSession {
  workflowId: string;
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingStatus: StreamingStatus;
  lastUpdated: Date;
}

// Factory function to create a user message
export function createUserMessage(content: string): ChatMessage {
  return {
    id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    role: 'user',
    type: 'text',
    content,
    timestamp: new Date(),
  };
}

// Factory function to create an assistant text message
export function createAssistantMessage(content: string): ChatMessage {
  return {
    id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    role: 'assistant',
    type: 'text',
    content,
    timestamp: new Date(),
  };
}

// Factory function to create a thinking message
export function createThinkingMessage(): ChatMessage {
  return {
    id: `msg-thinking-${Date.now()}`,
    role: 'assistant',
    type: 'thinking',
    content: '',
    timestamp: new Date(),
  };
}

// Factory function to create an OAuth request message
export function createOAuthRequestMessage(connectors: ConnectorInfo[], message?: string): ChatMessage {
  return {
    id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    role: 'assistant',
    type: 'oauth_request',
    content: message || `I need access to ${connectors.map((c) => c.name).join(', ')} to proceed.`,
    timestamp: new Date(),
    metadata: {
      type: 'oauth_request',
      data: { connectors, message },
    },
  };
}

// Factory function to create a steps preview message
export function createStepsPreviewMessage(content: string, steps: BaseStep[]): ChatMessage {
  return {
    id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    role: 'assistant',
    type: 'steps_preview',
    content,
    timestamp: new Date(),
    metadata: {
      type: 'steps_preview',
      data: {
        steps,
        applied: false,
        pendingApproval: true,
      },
    },
  };
}

// Factory function to create a question message
export function createQuestionMessage(content: string, options: QuestionOption[]): ChatMessage {
  return {
    id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    role: 'assistant',
    type: 'question',
    content,
    timestamp: new Date(),
    metadata: {
      type: 'question',
      data: {
        options,
        allowMultiple: false,
        selectedValues: [],
      },
    },
  };
}

// Factory function to create a confirmation message
export function createConfirmationMessage(content: string): ChatMessage {
  return {
    id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    role: 'assistant',
    type: 'confirmation',
    content,
    timestamp: new Date(),
  };
}

// Factory function to create an error message
export function createErrorMessage(content: string): ChatMessage {
  return {
    id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    role: 'assistant',
    type: 'error',
    content,
    timestamp: new Date(),
  };
}

// Welcome message factory
export function createWelcomeMessage(stepCount: number): ChatMessage {
  const content =
    stepCount > 1
      ? `I see your workflow has ${stepCount} steps. How can I help?`
      : 'What would you like to build today?';

  return createAssistantMessage(content);
}
