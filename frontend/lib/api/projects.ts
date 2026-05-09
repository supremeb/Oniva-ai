/**
 * Projects API client functions.
 * Handles CRUD, member management, file operations, and sessions for project workspaces.
 */
import { apiFetch } from './index';

// --- Types ---

export interface Project {
  id: string;
  tenant_id: string;
  owner_id: string;
  name: string;
  description: string | null;
  instructions: string | null;
  default_model: string | null;
  knowledge_base_id: string | null;
  settings: Record<string, unknown>;
  is_archived: boolean;
  row_version: number;
  member_count: number;
  file_count: number;
  session_count: number;
  user_role: 'owner' | 'editor' | 'viewer';
  created_at: string;
  updated_at: string;
}

export interface ProjectListResponse {
  projects: Project[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProjectMember {
  id: string | null;
  project_id: string;
  user_id: string;
  role: 'owner' | 'editor' | 'viewer';
  invited_by: string | null;
  user_email: string | null;
  user_name: string | null;
  created_at: string | null;
}

export interface ProjectFile {
  id: string;
  filename: string;
  file_type: string | null;
  file_size_bytes: number;
  status: string;
  error_message: string | null;
  chunk_count: number;
  created_at: string;
}

// --- Project CRUD ---

export async function fetchProjects(params?: {
  page?: number;
  page_size?: number;
  include_archived?: boolean;
}): Promise<ProjectListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.append('page', String(params.page));
  if (params?.page_size) searchParams.append('page_size', String(params.page_size));
  if (params?.include_archived) searchParams.append('include_archived', 'true');
  const query = searchParams.toString();
  return apiFetch<ProjectListResponse>(`/api/v1/projects${query ? `?${query}` : ''}`);
}

export async function fetchProject(projectId: string): Promise<Project> {
  return apiFetch<Project>(`/api/v1/projects/${projectId}`);
}

export async function createProject(data: {
  name: string;
  description?: string;
  instructions?: string;
  default_model?: string;
}): Promise<Project> {
  return apiFetch<Project>('/api/v1/projects', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateProject(
  projectId: string,
  data: {
    name?: string;
    description?: string;
    instructions?: string;
    default_model?: string;
    settings?: Record<string, unknown>;
    row_version: number;
  }
): Promise<Project> {
  return apiFetch<Project>(`/api/v1/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function archiveProject(
  projectId: string,
  is_archived: boolean
): Promise<Project> {
  return apiFetch<Project>(`/api/v1/projects/${projectId}/archive`, {
    method: 'PATCH',
    body: JSON.stringify({ is_archived }),
  });
}

// --- Members ---

export async function fetchProjectMembers(
  projectId: string
): Promise<{ members: ProjectMember[] }> {
  return apiFetch<{ members: ProjectMember[] }>(
    `/api/v1/projects/${projectId}/members`
  );
}

export async function addProjectMember(
  projectId: string,
  data: { user_id: string; role: 'editor' | 'viewer' }
): Promise<ProjectMember> {
  return apiFetch<ProjectMember>(`/api/v1/projects/${projectId}/members`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateMemberRole(
  projectId: string,
  userId: string,
  role: 'editor' | 'viewer'
): Promise<ProjectMember> {
  return apiFetch<ProjectMember>(
    `/api/v1/projects/${projectId}/members/${userId}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    }
  );
}

export async function removeMember(
  projectId: string,
  userId: string
): Promise<void> {
  return apiFetch<void>(`/api/v1/projects/${projectId}/members/${userId}`, {
    method: 'DELETE',
  });
}

export async function transferOwnership(
  projectId: string,
  newOwnerId: string
): Promise<Project> {
  return apiFetch<Project>(
    `/api/v1/projects/${projectId}/transfer-ownership`,
    {
      method: 'POST',
      body: JSON.stringify({ new_owner_id: newOwnerId }),
    }
  );
}

// --- Files ---

export async function uploadProjectFile(
  projectId: string,
  file: File
): Promise<ProjectFile> {
  const formData = new FormData();
  formData.append('file', file);
  return apiFetch<ProjectFile>(`/api/v1/projects/${projectId}/files`, {
    method: 'POST',
    body: formData,
    timeout: 120000,
  });
}

export async function fetchProjectFiles(
  projectId: string
): Promise<{ files: ProjectFile[] }> {
  return apiFetch<{ files: ProjectFile[] }>(
    `/api/v1/projects/${projectId}/files`
  );
}

export async function deleteProjectFile(
  projectId: string,
  fileId: string
): Promise<void> {
  return apiFetch<void>(`/api/v1/projects/${projectId}/files/${fileId}`, {
    method: 'DELETE',
  });
}

// --- Sessions ---

export async function fetchProjectSessions(
  projectId: string
): Promise<{ sessions: Record<string, unknown>[] }> {
  return apiFetch<{ sessions: Record<string, unknown>[] }>(
    `/api/v1/projects/${projectId}/sessions`
  );
}
