/**
 * API functions for VM0-Inspired Artifact Management
 *
 * Artifacts are persistent outputs with 7-year retention for compliance.
 * Examples: analysis reports, compliance audits, research summaries
 */
import { apiFetch } from './index';

// ============================================================================
// Types
// ============================================================================

export interface ArtifactInfo {
  file_path: string;
  size_bytes: number;
  last_modified: string;
  content_type: string;
  s3_uri?: string;
}

export interface ArtifactMetadata {
  session_id: string;
  file_path: string;
  size_bytes: number;
  content_type: string;
  created_at: string;
  checksum?: string;
  user_id?: string;
  agent_id?: string;
  retention_until?: string;
  s3_uri?: string;
  download_url?: string;
}

export interface ArtifactListResponse {
  session_id: string;
  artifacts: ArtifactInfo[];
  count: number;
  total_size_bytes: number;
}

export interface ArtifactUploadResponse {
  file_path: string;
  size_bytes: number;
  content_type: string;
  storage_uri: string;
  checksum: string;
}

export interface DownloadUrlResponse {
  session_id: string;
  file_path: string;
  download_url: string;
  expires_in: number;
}

export interface DeleteResponse {
  deleted: boolean;
  count: number;
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * List all artifacts for a session
 */
export async function listSessionArtifacts(sessionId: string): Promise<ArtifactListResponse> {
  return apiFetch<ArtifactListResponse>(
    `/api/v1/artifacts/sessions/${encodeURIComponent(sessionId)}`
  );
}

/**
 * Get metadata for a specific artifact
 */
export async function getArtifactMetadata(
  sessionId: string,
  filePath: string
): Promise<ArtifactMetadata> {
  return apiFetch<ArtifactMetadata>(
    `/api/v1/artifacts/sessions/${encodeURIComponent(sessionId)}/metadata/${encodeURIComponent(filePath)}`
  );
}

/**
 * Get a download URL for an artifact
 */
export async function getArtifactDownloadUrl(
  sessionId: string,
  filePath: string,
  expiresIn: number = 3600
): Promise<DownloadUrlResponse> {
  return apiFetch<DownloadUrlResponse>(
    `/api/v1/artifacts/sessions/${encodeURIComponent(sessionId)}/download/${encodeURIComponent(filePath)}?expires_in=${expiresIn}`
  );
}

/**
 * Read artifact content directly
 */
export async function readArtifactContent(
  sessionId: string,
  filePath: string
): Promise<Blob> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const response = await fetch(
    `${baseUrl}/api/v1/artifacts/sessions/${encodeURIComponent(sessionId)}/content/${encodeURIComponent(filePath)}`,
    {
      headers: {
        Connection: 'keep-alive',
      },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to read artifact: ${response.statusText}`);
  }

  return response.blob();
}

/**
 * Upload an artifact to a session
 */
export async function uploadArtifact(
  sessionId: string,
  file: File,
  options?: {
    userId?: string;
    agentId?: string;
  }
): Promise<ArtifactUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const params = new URLSearchParams();
  if (options?.userId) params.append('user_id', options.userId);
  if (options?.agentId) params.append('agent_id', options.agentId);
  const query = params.toString();

  const response = await fetch(
    `${baseUrl}/api/v1/artifacts/sessions/${encodeURIComponent(sessionId)}${query ? `?${query}` : ''}`,
    {
      method: 'POST',
      body: formData,
      headers: {
        Connection: 'keep-alive',
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || 'Upload failed');
  }

  return response.json();
}

/**
 * Write an artifact to a specific path
 */
export async function writeArtifact(
  sessionId: string,
  filePath: string,
  content: Blob | File,
  options?: {
    contentType?: string;
    userId?: string;
    agentId?: string;
  }
): Promise<ArtifactUploadResponse> {
  const formData = new FormData();
  formData.append('file', content);

  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const params = new URLSearchParams();
  params.append('file_path', filePath);
  if (options?.contentType) params.append('content_type', options.contentType);
  if (options?.userId) params.append('user_id', options.userId);
  if (options?.agentId) params.append('agent_id', options.agentId);

  const response = await fetch(
    `${baseUrl}/api/v1/artifacts/sessions/${encodeURIComponent(sessionId)}/write?${params.toString()}`,
    {
      method: 'POST',
      body: formData,
      headers: {
        Connection: 'keep-alive',
      },
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || 'Write failed');
  }

  return response.json();
}

/**
 * Delete an artifact
 */
export async function deleteArtifact(
  sessionId: string,
  filePath: string
): Promise<DeleteResponse> {
  return apiFetch<DeleteResponse>(
    `/api/v1/artifacts/sessions/${encodeURIComponent(sessionId)}/${encodeURIComponent(filePath)}`,
    { method: 'DELETE' }
  );
}

/**
 * Delete all artifacts for a session
 */
export async function deleteSessionArtifacts(sessionId: string): Promise<DeleteResponse> {
  return apiFetch<DeleteResponse>(
    `/api/v1/artifacts/sessions/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' }
  );
}
