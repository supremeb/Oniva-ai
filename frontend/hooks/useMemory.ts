/**
 * React Query hooks for Enhanced Memory System
 *
 * Provides hooks for:
 * - Fetching and searching memories
 * - Managing memory feedback and updates
 * - Memory statistics
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  storeDecision,
  searchDecisions,
  recallMemories,
  listMemories,
  getMemory,
  provideFeedback,
  updateMemory,
  updateImportance,
  deleteMemory,
  archiveMemories,
  getMemoryStats,
  getMemoryHealth,
  consolidateMemories,
  getOrgProfile,
  updateOrgProfile,
  listSharedMemories,
  StoreDecisionRequest,
  SearchRequest,
  HybridSearchRequest,
  ListMemoriesRequest,
  FeedbackRequest,
  UpdateMemoryRequest,
  ArchiveRequest,
  OrgProfileUpdateRequest,
  SearchMode,
} from '@/lib/api/memory';

// ============================================================================
// Query Hooks (Read Operations)
// ============================================================================

/**
 * Fetch a single memory by ID
 */
export function useMemory(memoryId: string) {
  return useQuery({
    queryKey: ['memories', memoryId],
    queryFn: () => getMemory(memoryId),
    enabled: !!memoryId,
    staleTime: 30000, // Consider data fresh for 30s
  });
}

/**
 * Get memory statistics
 */
export function useMemoryStats() {
  return useQuery({
    queryKey: ['memories', 'stats'],
    queryFn: getMemoryStats,
    staleTime: 60000, // Stats can be stale for 1 minute
  });
}

/**
 * Get memory system health
 */
export function useMemoryHealth() {
  return useQuery({
    queryKey: ['memories', 'health'],
    queryFn: getMemoryHealth,
    staleTime: 30000,
    refetchInterval: 60000, // Auto-refresh health every minute
  });
}

/**
 * List all memories with optional filters (no search query needed)
 */
export function useListMemories(request?: ListMemoriesRequest) {
  return useQuery({
    queryKey: ['memories', 'list', request],
    queryFn: () => listMemories(request),
    staleTime: 30000, // Consider data fresh for 30s
  });
}

// ============================================================================
// Search Hooks
// ============================================================================

/**
 * Search memories using legacy semantic search
 * Best for simple similarity-based queries
 */
export function useSearchMemories(request: SearchRequest | null) {
  return useQuery({
    queryKey: ['memories', 'search', request],
    queryFn: () => searchDecisions(request!),
    enabled: !!request?.query,
    staleTime: 10000, // Search results fresh for 10s
  });
}

/**
 * Search memories using hybrid search (semantic + BM25)
 * Best for complex queries with importance/recency weighting
 */
export function useRecallMemories(
  request: HybridSearchRequest | null,
  options?: {
    searchMode?: SearchMode;
    includeArchived?: boolean;
  }
) {
  return useQuery({
    queryKey: ['memories', 'recall', request, options],
    queryFn: () => recallMemories(request!, options),
    enabled: !!request?.query,
    staleTime: 10000,
  });
}

// ============================================================================
// Mutation Hooks (Write Operations)
// ============================================================================

/**
 * Store a new decision in memory
 */
export function useStoreDecision() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: StoreDecisionRequest) => storeDecision(request),
    onSuccess: () => {
      // Invalidate stats and search results
      queryClient.invalidateQueries({ queryKey: ['memories', 'stats'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'search'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'recall'] }).catch(() => {});
    },
  });
}

/**
 * Provide feedback on a memory (helpful/not helpful)
 * This adjusts the memory's importance score
 */
export function useProvideFeedback() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ memoryId, feedback }: { memoryId: string; feedback: FeedbackRequest }) =>
      provideFeedback(memoryId, feedback),
    onSuccess: (data, variables) => {
      // Invalidate the specific memory and stats
      queryClient.invalidateQueries({ queryKey: ['memories', variables.memoryId] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'stats'] }).catch(() => {});
    },
  });
}

/**
 * Update memory properties (visibility, tags, archived)
 */
export function useUpdateMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ memoryId, updates }: { memoryId: string; updates: UpdateMemoryRequest }) =>
      updateMemory(memoryId, updates),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['memories', variables.memoryId] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'stats'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'search'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'recall'] }).catch(() => {});
    },
  });
}

/**
 * Update memory importance score directly
 */
export function useUpdateImportance() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ memoryId, importance }: { memoryId: string; importance: number }) =>
      updateImportance(memoryId, importance),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['memories', variables.memoryId] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'stats'] }).catch(() => {});
    },
  });
}

/**
 * Delete a memory permanently
 */
export function useDeleteMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (memoryId: string) => deleteMemory(memoryId),
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['memories', variables] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'stats'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'search'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['memories', 'recall'] }).catch(() => {});
    },
  });
}

/**
 * Archive old, low-access memories
 */
export function useArchiveMemories() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request?: ArchiveRequest) => archiveMemories(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memories'] }).catch(() => {});
    },
  });
}

/**
 * Trigger memory consolidation (cleanup duplicates, archive old)
 */
export function useConsolidateMemories() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: consolidateMemories,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memories'] }).catch(() => {});
    },
  });
}

// ============================================================================
// Organization Context Hooks
// ============================================================================

/**
 * Fetch the organization profile
 */
export function useOrgProfile() {
  return useQuery({
    queryKey: ['org-profile'],
    queryFn: getOrgProfile,
    staleTime: 60000,
    retry: false, // Don't retry if USE_ORG_CONTEXT is disabled (503)
  });
}

/**
 * Update organization profile (admin/owner only)
 */
export function useUpdateOrgProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: OrgProfileUpdateRequest) => updateOrgProfile(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-profile'] }).catch(() => {});
    },
  });
}

/**
 * List shared memories across the organization
 */
export function useSharedMemories(request?: ListMemoriesRequest) {
  return useQuery({
    queryKey: ['memories', 'shared', request],
    queryFn: () => listSharedMemories(request),
    staleTime: 30000,
  });
}
