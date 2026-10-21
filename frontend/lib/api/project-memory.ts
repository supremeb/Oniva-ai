/**
 * Project memory API client (backend/app/api/v1/project_memory.py).
 * Every route is 404 when ENABLE_PROJECT_MEMORY is off; writes are 403 for viewers;
 * stale versions and non-pending approvals are 409.
 */
import { apiFetch } from './index';

export interface MemoryFlag {
  kind: 'imperative' | 'url' | 'email' | 'tool_name' | string;
  match: string;
}

/** Server-owned JSON column; only the keys below are read by the UI. */
export interface MemoryRowFlags {
  instruction_flags?: MemoryFlag[];
  tainted?: boolean;
  approved_by?: string;
  [key: string]: unknown;
}

export interface MemoryFile {
  id: string;
  path: string;
  content: string;
  version: number;
  status: 'active' | 'pending' | string;
  source_kind: string;
  source_conversation_id: string | null;
  flags: MemoryRowFlags | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface MemoryBullet {
  id: string;
  bullet_key: string;
  section: string;
  text: string;
  helpful: number;
  harmful: number;
  status: 'active' | 'pending' | string;
  source_kind: string;
  source_conversation_id: string | null;
  source_run_id: string | null;
  flags: MemoryRowFlags | null;
  last_used_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface MemoryOverview {
  project_id: string;
  files: MemoryFile[];
  bullets: MemoryBullet[];
  playbook: string | null;
  pending_count: number;
}

export interface MemoryDecision {
  id: string;
  kind: 'file' | 'bullet';
  status: 'active' | 'rejected';
}

export interface MemoryPurgeResult {
  files_deleted: number;
  bullets_deleted: number;
}

export interface MemoryExport {
  project_id: string;
  exported_at: string;
  files: MemoryFile[];
  bullets: MemoryBullet[];
  playbook: string | null;
  meta: Record<string, unknown>;
}

const base = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/memory`;

/** Paths may contain slashes (the route is `{path:path}`); encode each segment. */
const encodePath = (path: string) => path.split('/').map(encodeURIComponent).join('/');

export function fetchProjectMemory(projectId: string): Promise<MemoryOverview> {
  return apiFetch<MemoryOverview>(base(projectId));
}

export function putMemoryFile(
  projectId: string,
  path: string,
  content: string,
  expectedVersion?: number
): Promise<MemoryFile> {
  return apiFetch<MemoryFile>(`${base(projectId)}/files/${encodePath(path)}`, {
    method: 'PUT',
    body: JSON.stringify({
      content,
      ...(expectedVersion !== undefined ? { expected_version: expectedVersion } : {}),
    }),
  });
}

export function deleteMemoryFile(projectId: string, path: string, expectedVersion?: number): Promise<void> {
  const q = expectedVersion !== undefined ? `?expected_version=${expectedVersion}` : '';
  return apiFetch<void>(`${base(projectId)}/files/${encodePath(path)}${q}`, { method: 'DELETE' });
}

export function patchMemoryBullet(
  projectId: string,
  bulletId: string,
  patch: { text?: string; section?: string }
): Promise<MemoryBullet> {
  return apiFetch<MemoryBullet>(`${base(projectId)}/bullets/${encodeURIComponent(bulletId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export function deleteMemoryBullet(projectId: string, bulletId: string): Promise<void> {
  return apiFetch<void>(`${base(projectId)}/bullets/${encodeURIComponent(bulletId)}`, { method: 'DELETE' });
}

export function approvePendingMemory(projectId: string, itemId: string): Promise<MemoryDecision> {
  return apiFetch<MemoryDecision>(`${base(projectId)}/pending/${encodeURIComponent(itemId)}/approve`, {
    method: 'POST',
  });
}

export function rejectPendingMemory(projectId: string, itemId: string): Promise<MemoryDecision> {
  return apiFetch<MemoryDecision>(`${base(projectId)}/pending/${encodeURIComponent(itemId)}/reject`, {
    method: 'POST',
  });
}

export function exportProjectMemory(projectId: string): Promise<MemoryExport> {
  return apiFetch<MemoryExport>(`${base(projectId)}/export`, { method: 'POST' });
}

export function purgeProjectMemory(projectId: string): Promise<MemoryPurgeResult> {
  return apiFetch<MemoryPurgeResult>(base(projectId), { method: 'DELETE' });
}
