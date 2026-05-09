import { apiFetch } from './index';

export interface SandboxExecuteRequest {
  code: string;
  session_id: string;
  input_files?: string[];
}

export interface SandboxExecutionResponse {
  id: string;
  session_id: string;
  tenant_id: string;
  sandbox_id: string;
  code: string;
  status: string;
  stdout?: string;
  stderr?: string;
  duration_seconds?: number;
  created_at?: string;
  completed_at?: string;
}

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

export async function executeSandbox(request: SandboxExecuteRequest): Promise<SandboxExecutionResponse> {
  return apiFetch<SandboxExecutionResponse>('/api/v1/sandbox/execute', {
    method: 'POST',
    body: JSON.stringify(request),
    timeout: 10000,
  });
}

export async function getSandboxExecution(executionId: string): Promise<SandboxExecutionResponse> {
  return apiFetch<SandboxExecutionResponse>(`/api/v1/sandbox/${executionId}`);
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
