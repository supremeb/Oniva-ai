/**
 * Mentions API client for the Hybrid Document Editor (Phase B).
 *
 * Provides autocomplete search and batch resolution for @mentions
 * in the Tiptap instructions editor.
 */

import { apiFetch } from './index';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MentionItem {
  entity_type: 'skill' | 'connector' | 'agent' | 'knowledge_base';
  entity_id: string;
  display_name: string;
  icon: string;
  description: string;
  enabled: boolean;
  deleted: boolean;
}

export interface MentionSearchResponse {
  results: MentionItem[];
  total: number;
}

export interface MentionResolveResponse {
  items: MentionItem[];
}

// ---------------------------------------------------------------------------
// API Functions
// ---------------------------------------------------------------------------

/**
 * Search for mentionable entities (autocomplete).
 *
 * @param query - Search query string
 * @param types - Optional comma-separated entity types to filter
 */
export async function searchMentions(
  query: string,
  types?: string,
): Promise<MentionSearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (types) params.set('types', types);
  return apiFetch(`/api/v1/mentions/search?${params.toString()}`);
}

/**
 * Batch resolve mention IDs for editor load.
 *
 * @param ids - Array of `{entity_type}:{entity_id}` strings
 */
export async function resolveMentions(
  ids: string[],
): Promise<MentionResolveResponse> {
  if (ids.length === 0) return { items: [] };
  const params = new URLSearchParams({ ids: ids.join(',') });
  return apiFetch(`/api/v1/mentions/resolve?${params.toString()}`);
}
