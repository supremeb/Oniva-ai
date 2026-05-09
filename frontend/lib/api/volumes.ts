/**
 * API functions for VM0-Inspired Volume Management
 *
 * Volumes are pre-warmed input data (read-only) that can be mounted to sessions.
 * Examples: market_data, sec_filings, research_corpus
 */
import { apiFetch } from './index';

// ============================================================================
// Types
// ============================================================================

export interface VolumeInfo {
  name: string;
  size_bytes: number;
  file_count: number;
  created_at: string;
  description?: string;
  last_accessed?: string;
  s3_uri?: string;
}

export interface VolumeListResponse {
  volumes: VolumeInfo[];
  count: number;
  total_size_bytes: number;
}

export interface VolumeFileInfo {
  file_path: string;
  size_bytes: number;
  last_modified: string;
  content_type?: string;
}

export interface VolumeFileListResponse {
  volume_name: string;
  files: VolumeFileInfo[];
  count: number;
  total_size_bytes: number;
}

export interface VolumeMountResponse {
  volume_name: string;
  session_id: string;
  mount_path: string;
  mounted_at: string;
}

export interface VolumeCreateRequest {
  name: string;
  description?: string;
}

export interface VolumeCreateResponse {
  name: string;
  path: string;
  created_at: string;
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * List all available volumes
 */
export async function listVolumes(): Promise<VolumeListResponse> {
  return apiFetch<VolumeListResponse>('/api/v1/volumes');
}

/**
 * Get details for a specific volume
 */
export async function getVolume(volumeName: string): Promise<VolumeInfo> {
  return apiFetch<VolumeInfo>(`/api/v1/volumes/${encodeURIComponent(volumeName)}`);
}

/**
 * Create a new volume
 */
export async function createVolume(data: VolumeCreateRequest): Promise<VolumeCreateResponse> {
  return apiFetch<VolumeCreateResponse>('/api/v1/volumes', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Delete a volume
 */
export async function deleteVolume(volumeName: string): Promise<{ deleted: boolean; name: string }> {
  return apiFetch(`/api/v1/volumes/${encodeURIComponent(volumeName)}`, {
    method: 'DELETE',
  });
}

/**
 * List files in a volume
 */
export async function listVolumeFiles(
  volumeName: string,
  params?: {
    prefix?: string;
    limit?: number;
  }
): Promise<VolumeFileListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.prefix) {
    searchParams.append('prefix', params.prefix);
  }
  if (params?.limit) {
    searchParams.append('limit', String(params.limit));
  }
  const query = searchParams.toString();
  return apiFetch<VolumeFileListResponse>(
    `/api/v1/volumes/${encodeURIComponent(volumeName)}/files${query ? `?${query}` : ''}`
  );
}

/**
 * Read a file from a volume
 */
export async function readVolumeFile(
  volumeName: string,
  filePath: string
): Promise<Blob> {
  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/volumes/${encodeURIComponent(volumeName)}/files/${encodeURIComponent(filePath)}`,
    {
      headers: {
        Connection: 'keep-alive',
      },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to read file: ${response.statusText}`);
  }

  return response.blob();
}

/**
 * Mount a volume to a session
 */
export async function mountVolume(
  volumeName: string,
  sessionId: string
): Promise<VolumeMountResponse> {
  return apiFetch<VolumeMountResponse>(
    `/api/v1/volumes/${encodeURIComponent(volumeName)}/mount/${encodeURIComponent(sessionId)}`,
    { method: 'POST' }
  );
}

/**
 * Unmount a volume from a session
 */
export async function unmountVolume(
  volumeName: string,
  sessionId: string
): Promise<{ unmounted: boolean }> {
  return apiFetch(
    `/api/v1/volumes/${encodeURIComponent(volumeName)}/unmount/${encodeURIComponent(sessionId)}`,
    { method: 'POST' }
  );
}

/**
 * Upload a file to a volume
 */
export async function uploadVolumeFile(
  volumeName: string,
  file: File,
  filePath?: string
): Promise<{ success: boolean; file_path: string; size_bytes: number }> {
  const formData = new FormData();
  formData.append('file', file);

  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const pathParam = filePath ? `?file_path=${encodeURIComponent(filePath)}` : '';

  const response = await fetch(
    `${baseUrl}/api/v1/volumes/${encodeURIComponent(volumeName)}/files${pathParam}`,
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
