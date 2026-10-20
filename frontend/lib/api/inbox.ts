/**
 * API functions for the "Needs attention" inbox (Approvals & Policy plan, U9).
 */
import { apiFetch } from './index';

export type InboxKind = 'approval' | 'review' | 'question' | 'compliance' | 'fleet' | 'deep_work';

export const INBOX_KINDS: InboxKind[] = ['approval', 'review', 'question', 'compliance', 'fleet', 'deep_work'];

export interface InboxAction {
  type: string;
  endpoint?: string;
  requiresRole?: string;
  allowed: boolean;
  disabledReason?: string;
  [key: string]: unknown;
}

export interface InboxItem {
  id: string;
  kind: InboxKind;
  title: string;
  summary: string;
  severity: 'high' | 'medium' | 'low' | string;
  createdAt?: string | null;
  dueAt?: string | null;
  source: { type: string; id: string };
  actions: InboxAction[];
  href: string;
}

export interface InboxResponse {
  items: InboxItem[];
  nextCursor?: string | null;
}

export interface InboxCount {
  total: number;
  byKind: Record<string, number>;
}

export async function listInbox(params?: {
  kinds?: InboxKind[];
  limit?: number;
  cursor?: string;
}): Promise<InboxResponse> {
  const sp = new URLSearchParams();
  if (params?.kinds?.length) sp.set('kinds', params.kinds.join(','));
  if (params?.limit !== undefined) sp.set('limit', String(params.limit));
  if (params?.cursor) sp.set('cursor', params.cursor);
  const q = sp.toString();
  return apiFetch<InboxResponse>(`/api/v1/inbox${q ? `?${q}` : ''}`);
}

export async function inboxCount(): Promise<InboxCount> {
  return apiFetch<InboxCount>('/api/v1/inbox/count');
}
