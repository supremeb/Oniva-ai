/**
 * Chat Persistence Utilities for AI Workflow Builder
 *
 * Persists chat history per workflow using localStorage.
 * Features:
 * - Automatic save on message changes
 * - Max message limit to avoid quota issues
 * - Orphaned history cleanup
 * - SECURITY: Tenant-prefixed keys to prevent cross-tenant data leakage
 */

import type { ChatMessage } from './models/chat-messages';
import { parseTimestamp } from './utils';
import { getCurrentTenantId } from './tenant-storage';

const STORAGE_KEY_PREFIX_BASE = 'workflow_chat_';
const MAX_MESSAGES_STORED = 50; // Limit to avoid localStorage quota issues

/**
 * Get tenant-prefixed storage key for workflow chat history.
 * SECURITY: Prevents cross-tenant data leakage when users switch tenants.
 */
function getStorageKeyPrefix(): string {
  const tenantId = getCurrentTenantId();
  return tenantId
    ? `${STORAGE_KEY_PREFIX_BASE}${tenantId}_`
    : `${STORAGE_KEY_PREFIX_BASE}guest_`;
}

interface PersistedChat {
  workflowId: string;
  messages: ChatMessage[];
  lastUpdated: string; // ISO timestamp
}

/**
 * Load chat history for a workflow.
 * Returns empty array if no history exists.
 * SECURITY: Uses tenant-prefixed key to isolate data between tenants.
 */
export function loadChatHistory(workflowId: string): ChatMessage[] {
  if (typeof window === 'undefined') return [];

  try {
    const key = `${getStorageKeyPrefix()}${workflowId}`;
    const stored = localStorage.getItem(key);
    if (!stored) return [];

    const parsed: PersistedChat = JSON.parse(stored);

    // Rehydrate Date objects (JSON serializes them as strings)
    return parsed.messages.map((msg) => ({
      ...msg,
      timestamp: parseTimestamp(msg.timestamp as unknown as string),
    }));
  } catch (error) {
    console.error('Failed to load chat history:', error);
    return [];
  }
}

/**
 * Save chat history for a workflow.
 * Keeps only the last MAX_MESSAGES_STORED messages to avoid quota issues.
 * SECURITY: Uses tenant-prefixed key to isolate data between tenants.
 */
export function saveChatHistory(workflowId: string, messages: ChatMessage[]): void {
  if (typeof window === 'undefined') return;

  try {
    const key = `${getStorageKeyPrefix()}${workflowId}`;

    // Trim to max messages (keep most recent)
    const trimmedMessages = messages.slice(-MAX_MESSAGES_STORED);

    const data: PersistedChat = {
      workflowId,
      messages: trimmedMessages,
      lastUpdated: new Date().toISOString(),
    };

    localStorage.setItem(key, JSON.stringify(data));
  } catch (error) {
    // localStorage full or unavailable - fail silently
    console.error('Failed to save chat history:', error);
  }
}

/**
 * Clear chat history for a workflow.
 * Called when user explicitly clears chat or deletes workflow.
 * SECURITY: Uses tenant-prefixed key to isolate data between tenants.
 */
export function clearChatHistory(workflowId: string): void {
  if (typeof window === 'undefined') return;

  const key = `${getStorageKeyPrefix()}${workflowId}`;
  localStorage.removeItem(key);
}

/**
 * Get all workflow IDs that have stored chat history.
 * Useful for cleanup of orphaned chat histories.
 * SECURITY: Only returns IDs for the current tenant's chat histories.
 */
export function getAllStoredWorkflowIds(): string[] {
  if (typeof window === 'undefined') return [];

  const prefix = getStorageKeyPrefix();
  const ids: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(prefix)) {
      ids.push(key.replace(prefix, ''));
    }
  }
  return ids;
}

/**
 * Clean up chat histories for workflows that no longer exist.
 * Call this with a list of valid workflow IDs to remove orphaned histories.
 */
export function cleanupOrphanedHistories(validWorkflowIds: string[]): number {
  if (typeof window === 'undefined') return 0;

  const storedIds = getAllStoredWorkflowIds();
  const validSet = new Set(validWorkflowIds);
  let removed = 0;

  for (const id of storedIds) {
    if (!validSet.has(id)) {
      clearChatHistory(id);
      removed++;
    }
  }

  return removed;
}

/**
 * Get the last updated timestamp for a workflow's chat history.
 * Returns null if no history exists.
 * SECURITY: Uses tenant-prefixed key to isolate data between tenants.
 */
export function getChatHistoryLastUpdated(workflowId: string): Date | null {
  if (typeof window === 'undefined') return null;

  try {
    const key = `${getStorageKeyPrefix()}${workflowId}`;
    const stored = localStorage.getItem(key);
    if (!stored) return null;

    const parsed: PersistedChat = JSON.parse(stored);
    return parseTimestamp(parsed.lastUpdated);
  } catch {
    return null;
  }
}
