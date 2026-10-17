import { apiFetch } from './index';

export interface WorkspaceFileInfo {
  name: string;
  type: 'file' | 'directory';
  size?: number;
  modified?: string;
}

export interface WorkspaceFileListResponse {
  files: WorkspaceFileInfo[];
  total_count: number;
  session_id: string;
}

export interface WorkspaceFileContentResponse {
  path: string;
  content: string;
  size: number;
  session_id: string;
}

export interface WorkspaceTodoResponse {
  content: string;
  session_id: string;
}

export async function listWorkspaceFiles(sessionId: string): Promise<WorkspaceFileListResponse> {
  return apiFetch<WorkspaceFileListResponse>(`/api/v1/workspace/${sessionId}/files`);
}

export async function readWorkspaceFile(sessionId: string, path: string): Promise<WorkspaceFileContentResponse> {
  return apiFetch<WorkspaceFileContentResponse>(`/api/v1/workspace/${sessionId}/files/${path}`);
}

export async function readWorkspaceTodo(sessionId: string): Promise<WorkspaceTodoResponse> {
  return apiFetch<WorkspaceTodoResponse>(`/api/v1/workspace/${sessionId}/todo`);
}
