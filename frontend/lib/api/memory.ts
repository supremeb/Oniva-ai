/**
 * API functions for Enhanced Memory System
 *
 * Provides client functions for:
 * - Episodic memory (past decisions with hybrid search)
 * - Memory management (feedback, visibility, tags)
 * - Memory statistics
 */
import { apiFetch } from './index';

// ============================================================================
// Types
// ============================================================================

export type MemoryType = 'decision' | 'fact' | 'preference' | 'conversation';
export type FeedbackType = 'helpful' | 'not_helpful';
export type VisibilityType = 'private' | 'shared';
export type SourceType = 'chat' | 'workflow' | 'api' | 'import';
export type SearchMode = 'hybrid' | 'semantic' | 'keyword';
export type ActionType = 'analysis' | 'compliance_check' | 'recommendation' | 'research' | 'other';

export interface EpisodicMemory {
  id: string;
  session_id?: string;
  agent_name: string;
  action_type: ActionType;
  context_decision: string;
  rationale?: string;
  outcome?: Record<string, unknown>;
  entities: string[];
  similarity?: number;
  importance: number;
  archived: boolean;
  access_count: number;
  created_at: string;
  memory_type: MemoryType;
  feedback?: FeedbackType;
  feedback_at?: string;
  visibility: VisibilityType;
  source_type?: SourceType;
  source_id?: string;
  tags: string[];
}

export interface StoreDecisionRequest {
  session_id?: string;
  agent_name: string;
  action_type: ActionType;
  context: string;
  decision: string;
  rationale?: string;
  entities?: string[];
  visibility?: VisibilityType;
}

export interface StoreDecisionResponse {
  id: string;
  status: string;
}

export interface SearchRequest {
  query: string;
  limit?: number;
  min_similarity?: number;
  agent_name?: string;
  action_type?: ActionType;
  include_shared?: boolean;
}

export interface HybridSearchRequest {
  query: string;
  limit?: number;
  importance_weight?: number;
  recency_weight?: number;
  agent_name?: string;
  action_type?: ActionType;
  include_shared?: boolean;
}

export interface SearchResponse {
  results: EpisodicMemory[];
  count: number;
  search_mode?: SearchMode;
}

export interface FeedbackRequest {
  feedback: FeedbackType;
}

export interface FeedbackResponse {
  status: string;
  memory_id: string;
  feedback: FeedbackType;
  new_importance: number;
}

export interface UpdateMemoryRequest {
  visibility?: VisibilityType;
  tags?: string[];
  archived?: boolean;
  context?: string;
  decision?: string;
  rationale?: string;
  entities?: string[];
}

export interface UpdateMemoryResponse {
  status: string;
  memory_id: string;
  updated_fields: string[];
}

export interface DeleteMemoryResponse {
  status: string;
  memory_id: string;
}

export interface MemoryStats {
  total_memories: number;
  by_type: Record<string, number>;
  by_feedback: Record<string, number>;
  by_visibility: Record<string, number>;
  archived_count: number;
  avg_importance: number;
}

export interface MemoryHealth {
  status: string;
  total_memories: number;
  oldest_memory?: string;
  newest_memory?: string;
  index_status: string;
}

export interface ArchiveRequest {
  days_threshold?: number;
  min_access_count?: number;
}

export interface ListMemoriesRequest {
  limit?: number;
  offset?: number;
  include_archived?: boolean;
  memory_type?: MemoryType;
  agent_name?: string;
  visibility?: VisibilityType;
}

export interface ArchiveResponse {
  status: string;
  count: number;
  days_threshold: number;
}

// ============================================================================
// Core Memory Functions
// ============================================================================

/**
 * Store a decision to episodic memory
 */
export async function storeDecision(request: StoreDecisionRequest): Promise<StoreDecisionResponse> {
  return apiFetch<StoreDecisionResponse>('/api/v1/memory/episodic', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

/**
 * Search for similar past decisions (legacy semantic search)
 */
export async function searchDecisions(request: SearchRequest): Promise<SearchResponse> {
  return apiFetch<SearchResponse>('/api/v1/memory/episodic/search', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

/**
 * Recall memories using hybrid search (semantic + BM25)
 */
export async function recallMemories(
  request: HybridSearchRequest,
  options?: {
    searchMode?: SearchMode;
    includeArchived?: boolean;
  }
): Promise<SearchResponse> {
  const params = new URLSearchParams();
  if (options?.searchMode) params.append('search_mode', options.searchMode);
  if (options?.includeArchived) params.append('include_archived', 'true');

  const query = params.toString();
  return apiFetch<SearchResponse>(
    `/api/v1/memory/episodic/recall${query ? `?${query}` : ''}`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

/**
 * List all memories (with optional filters)
 */
export async function listMemories(request?: ListMemoriesRequest): Promise<SearchResponse> {
  const params = new URLSearchParams();
  if (request?.limit) params.append('limit', request.limit.toString());
  if (request?.offset) params.append('offset', request.offset.toString());
  if (request?.include_archived) params.append('include_archived', 'true');
  if (request?.memory_type) params.append('memory_type', request.memory_type);
  if (request?.agent_name) params.append('agent_name', request.agent_name);
  if (request?.visibility) params.append('visibility', request.visibility);

  const query = params.toString();
  return apiFetch<SearchResponse>(`/api/v1/memory/episodic${query ? `?${query}` : ''}`);
}

/**
 * Get a single memory by ID
 */
export async function getMemory(memoryId: string): Promise<EpisodicMemory> {
  return apiFetch<EpisodicMemory>(`/api/v1/memory/episodic/${memoryId}`);
}

// ============================================================================
// Memory Management Functions
// ============================================================================

/**
 * Provide feedback on a memory (helpful/not helpful)
 */
export async function provideFeedback(
  memoryId: string,
  request: FeedbackRequest
): Promise<FeedbackResponse> {
  return apiFetch<FeedbackResponse>(
    `/api/v1/memory/episodic/${memoryId}/feedback`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Update memory properties (visibility, tags, archived)
 */
export async function updateMemory(
  memoryId: string,
  request: UpdateMemoryRequest
): Promise<UpdateMemoryResponse> {
  return apiFetch<UpdateMemoryResponse>(
    `/api/v1/memory/episodic/${memoryId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Update memory importance score
 */
export async function updateImportance(
  memoryId: string,
  importance: number
): Promise<{ status: string; memory_id: string; importance: number }> {
  return apiFetch<{ status: string; memory_id: string; importance: number }>(
    `/api/v1/memory/episodic/${memoryId}/importance`,
    {
      method: 'PATCH',
      body: JSON.stringify({ importance }),
    }
  );
}

/**
 * Delete a memory permanently
 */
export async function deleteMemory(memoryId: string): Promise<DeleteMemoryResponse> {
  return apiFetch<DeleteMemoryResponse>(
    `/api/v1/memory/episodic/${memoryId}`,
    { method: 'DELETE' }
  );
}

/**
 * Archive old, low-access memories
 */
export async function archiveMemories(request?: ArchiveRequest): Promise<ArchiveResponse> {
  return apiFetch<ArchiveResponse>('/api/v1/memory/episodic/archive', {
    method: 'POST',
    body: JSON.stringify(request || {}),
  });
}

// ============================================================================
// Statistics and Health
// ============================================================================

/**
 * Get memory statistics
 */
export async function getMemoryStats(): Promise<MemoryStats> {
  return apiFetch<MemoryStats>('/api/v1/memory/episodic/stats');
}

/**
 * Get memory system health
 */
export async function getMemoryHealth(): Promise<MemoryHealth> {
  return apiFetch<MemoryHealth>('/api/v1/memory/health');
}

/**
 * Trigger memory consolidation
 */
export async function consolidateMemories(): Promise<{
  status: string;
  duplicates_removed: number;
  archived: number;
  duration_ms: number;
}> {
  return apiFetch('/api/v1/memory/consolidate', { method: 'POST' });
}

// ============================================================================
// Organization Context
// ============================================================================

export interface OrgProfile {
  tenant_id: string;
  industry?: string;
  description?: string;
  compliance_frameworks: string[];
  default_context?: string;
  shared_memory_count: number;
  updated_at?: string;
}

export interface OrgProfileUpdateRequest {
  industry?: string;
  description?: string;
  compliance_frameworks?: string[];
  default_context?: string;
}

/**
 * Get organization profile for context injection
 */
export async function getOrgProfile(): Promise<OrgProfile> {
  return apiFetch<OrgProfile>('/api/v1/memory/org-profile');
}

/**
 * Update organization profile (admin/owner only)
 */
export async function updateOrgProfile(request: OrgProfileUpdateRequest): Promise<OrgProfile> {
  return apiFetch<OrgProfile>('/api/v1/memory/org-profile', {
    method: 'PATCH',
    body: JSON.stringify(request),
  });
}

/**
 * List shared memories across the organization
 */
export async function listSharedMemories(request?: ListMemoriesRequest): Promise<SearchResponse> {
  const params = new URLSearchParams();
  if (request?.limit) params.append('limit', request.limit.toString());
  if (request?.offset) params.append('offset', request.offset.toString());
  if (request?.include_archived) params.append('include_archived', 'true');
  if (request?.memory_type) params.append('memory_type', request.memory_type);
  if (request?.agent_name) params.append('agent_name', request.agent_name);
  params.append('visibility', 'shared');

  const query = params.toString();
  return apiFetch<SearchResponse>(`/api/v1/memory/episodic${query ? `?${query}` : ''}`);
}
