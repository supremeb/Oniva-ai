/**
 * Knowledge Base API client for Oniva.
 *
 * Phase C: Full Knowledge Management - CRUD, document upload, search.
 * Also preserves backward-compatible getKnowledgeBases() for ConfigPanel.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from './index';

// ============================================================================
// Types
// ============================================================================

export interface KnowledgeBase {
  id: string;
  tenant_id?: string;
  team_id?: string | null;
  name: string;
  description?: string | null;
  status: string;
  document_count: number;
  total_chunks: number;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  /** Legacy alias for backward compat */
  documentCount?: number;
}

export interface KnowledgeDocument {
  id: string;
  knowledge_base_id: string;
  filename: string;
  file_type: string;
  file_size_bytes: number;
  version: number;
  status: string;
  error_message?: string | null;
  chunk_count: number;
  uploaded_by?: string | null;
  created_at?: string | null;
}

export interface KnowledgeSearchResult {
  id: string;
  content: string;
  chunk_index: number;
  metadata: Record<string, unknown>;
  document_id: string;
  knowledge_base_id?: string;
  score: number;
}

interface KnowledgeBaseListResponse {
  knowledge_bases: KnowledgeBase[];
  count: number;
}

interface KnowledgeDocumentListResponse {
  documents: KnowledgeDocument[];
  count: number;
}

interface KnowledgeSearchResponse {
  results: KnowledgeSearchResult[];
  count: number;
  query: string;
}

// ============================================================================
// API Functions
// ============================================================================

const KB_BASE = '/api/v1/knowledge/bases';

/** Legacy: Get KBs from old metadata-derived endpoint (backward compat) */
export async function getKnowledgeBases(): Promise<KnowledgeBase[]> {
  try {
    const response = await apiFetch<KnowledgeBaseListResponse>(KB_BASE);
    return response.knowledge_bases ?? [];
  } catch {
    // Fallback to old endpoint if new one not available
    const response = await apiFetch<KnowledgeBaseListResponse>('/api/v1/knowledge-bases');
    return response.knowledge_bases ?? [];
  }
}

export async function createKnowledgeBase(data: {
  name: string;
  description?: string;
  team_id?: string;
}): Promise<KnowledgeBase> {
  return apiFetch<KnowledgeBase>(KB_BASE, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getKnowledgeBase(id: string): Promise<KnowledgeBase> {
  return apiFetch<KnowledgeBase>(`${KB_BASE}/${id}`);
}

export async function updateKnowledgeBase(
  id: string,
  data: { name?: string; description?: string; team_id?: string }
): Promise<KnowledgeBase> {
  return apiFetch<KnowledgeBase>(`${KB_BASE}/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteKnowledgeBase(id: string): Promise<void> {
  await apiFetch(`${KB_BASE}/${id}`, { method: 'DELETE' });
}

export async function uploadDocument(kbId: string, file: File): Promise<KnowledgeDocument> {
  const formData = new FormData();
  formData.append('file', file);
  return apiFetch<KnowledgeDocument>(`${KB_BASE}/${kbId}/documents`, {
    method: 'POST',
    body: formData,
    headers: {}, // Let browser set Content-Type with boundary
    timeout: 120000, // 2 min for large files
  });
}

export async function listDocuments(kbId: string): Promise<KnowledgeDocument[]> {
  const response = await apiFetch<KnowledgeDocumentListResponse>(
    `${KB_BASE}/${kbId}/documents`
  );
  return response.documents ?? [];
}

export async function deleteDocument(kbId: string, docId: string): Promise<void> {
  await apiFetch(`${KB_BASE}/${kbId}/documents/${docId}`, { method: 'DELETE' });
}

export async function searchKnowledgeBase(
  kbId: string,
  query: string,
  limit: number = 5
): Promise<KnowledgeSearchResult[]> {
  const response = await apiFetch<KnowledgeSearchResponse>(
    `${KB_BASE}/${kbId}/search`,
    {
      method: 'POST',
      body: JSON.stringify({ query, limit }),
    }
  );
  return response.results ?? [];
}

export async function listDocumentVersions(
  kbId: string,
  docId: string
): Promise<KnowledgeDocument[]> {
  const response = await apiFetch<KnowledgeDocumentListResponse>(
    `${KB_BASE}/${kbId}/documents/${docId}/versions`
  );
  return response.documents ?? [];
}

// ============================================================================
// React Query Hooks
// ============================================================================

export function useKnowledgeBases(teamId?: string) {
  return useQuery({
    queryKey: ['knowledge-bases', teamId],
    queryFn: () => getKnowledgeBases(),
  });
}

export function useKnowledgeBase(id: string | undefined) {
  return useQuery({
    queryKey: ['knowledge-base', id],
    queryFn: () => getKnowledgeBase(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const kb = query.state.data;
      if (!kb) return false;
      return kb.status === 'processing' ? 3000 : false;
    },
  });
}

export function useKnowledgeDocuments(kbId: string | undefined) {
  return useQuery({
    queryKey: ['knowledge-documents', kbId],
    queryFn: () => listDocuments(kbId!),
    enabled: !!kbId,
    refetchInterval: (query) => {
      const docs = query.state.data;
      if (!docs) return false;
      const hasProcessing = docs.some(
        (doc) => doc.status === 'uploading' || doc.status === 'processing'
      );
      return hasProcessing ? 2000 : false;
    },
  });
}

export function useCreateKnowledgeBase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createKnowledgeBase,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] });
    },
  });
}

export function useDeleteKnowledgeBase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteKnowledgeBase,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] });
    },
  });
}

export function useUploadDocument(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadDocument(kbId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-documents', kbId] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-base', kbId] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] });
    },
  });
}

export function useDeleteDocument(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (docId: string) => deleteDocument(kbId, docId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-documents', kbId] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-base', kbId] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] });
    },
  });
}
