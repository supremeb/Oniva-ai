/**
 * React Query hook for knowledge base data.
 *
 * Knowledge bases change infrequently (only when documents are uploaded/deleted),
 * so we use a 5-minute stale time to reduce API calls.
 */

import { useQuery } from '@tanstack/react-query';
import { getKnowledgeBases, type KnowledgeBase } from '@/lib/api/knowledge-bases';

/**
 * Fetch all knowledge bases from the API.
 *
 * @returns Query result with knowledge bases array
 *
 * @example
 * ```tsx
 * const { data: knowledgeBases = [], isLoading } = useKnowledgeBases();
 * ```
 */
export function useKnowledgeBases() {
  return useQuery({
    queryKey: ['knowledge-bases'],
    queryFn: getKnowledgeBases,
    staleTime: 5 * 60 * 1000, // 5 minutes - KBs change rarely
  });
}

// Re-export the type for convenience
export type { KnowledgeBase };
