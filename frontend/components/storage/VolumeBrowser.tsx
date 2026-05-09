'use client';

import React, { useState, useEffect, useMemo } from 'react';
import {
  listVolumes,
  listVolumeFiles,
  createVolume,
  deleteVolume,
  VolumeInfo,
  VolumeFileInfo,
  VolumeListResponse,
  VolumeFileListResponse,
} from '@/lib/api/volumes';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Search,
  RefreshCw,
  AlertCircle,
  FolderOpen,
  File,
  HardDrive,
  Plus,
  Trash2,
  ChevronRight,
  Database,
} from 'lucide-react';

interface VolumeBrowserProps {
  onSelectFile?: (volumeName: string, filePath: string) => void;
  sessionId?: string;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function VolumeBrowser({ onSelectFile, sessionId }: VolumeBrowserProps) {
  const [volumes, setVolumes] = useState<VolumeInfo[]>([]);
  const [selectedVolume, setSelectedVolume] = useState<VolumeInfo | null>(null);
  const [files, setFiles] = useState<VolumeFileInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [filesLoading, setFilesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [newVolumeName, setNewVolumeName] = useState('');
  const [newVolumeDescription, setNewVolumeDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [deleteVolumeName, setDeleteVolumeName] = useState<string | null>(null);

  const fetchVolumes = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listVolumes();
      setVolumes(response.volumes);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to fetch volumes';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const fetchFiles = async (volumeName: string) => {
    setFilesLoading(true);
    try {
      const response = await listVolumeFiles(volumeName);
      setFiles(response.files);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to fetch files';
      setError(message);
    } finally {
      setFilesLoading(false);
    }
  };

  useEffect(() => {
    fetchVolumes();
  }, []);

  useEffect(() => {
    if (selectedVolume) {
      fetchFiles(selectedVolume.name);
    } else {
      setFiles([]);
    }
  }, [selectedVolume]);

  const filteredVolumes = useMemo(() => {
    return volumes.filter((volume) =>
      volume.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      volume.description?.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [volumes, searchQuery]);

  const handleCreateVolume = async () => {
    if (!newVolumeName.trim()) return;

    setCreating(true);
    try {
      await createVolume({
        name: newVolumeName.trim(),
        description: newVolumeDescription.trim() || undefined,
      });
      setShowCreateDialog(false);
      setNewVolumeName('');
      setNewVolumeDescription('');
      fetchVolumes();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to create volume';
      setError(message);
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteVolume = (volumeName: string) => {
    setDeleteVolumeName(volumeName);
  };

  const confirmDeleteVolume = async () => {
    if (!deleteVolumeName) return;
    try {
      await deleteVolume(deleteVolumeName);
      if (selectedVolume?.name === deleteVolumeName) {
        setSelectedVolume(null);
      }
      fetchVolumes();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to delete volume';
      setError(message);
    }
  };

  const handleFileClick = (file: VolumeFileInfo) => {
    if (selectedVolume && onSelectFile) {
      onSelectFile(selectedVolume.name, file.file_path);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center">
        <div className="flex items-center gap-2">
          <Database className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold">Volumes</h2>
          <Badge variant="secondary">{volumes.length}</Badge>
        </div>

        <div className="flex gap-2 w-full sm:w-auto">
          <div className="relative flex-1 sm:w-64">
            <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search volumes..."
              className="pl-8"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <Button variant="outline" size="icon" onClick={fetchVolumes} title="Refresh">
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>

          <Button onClick={() => setShowCreateDialog(true)}>
            <Plus className="h-4 w-4 mr-2" />
            New Volume
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Volume List */}
        <div className="lg:col-span-1">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Available Volumes</CardTitle>
              <CardDescription>Pre-warmed input data</CardDescription>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[400px] pr-4">
                {loading ? (
                  <div className="space-y-2">
                    {[...Array(4)].map((_, i) => (
                      <div key={i} className="h-16 bg-muted rounded animate-pulse" />
                    ))}
                  </div>
                ) : filteredVolumes.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    No volumes found
                  </div>
                ) : (
                  <div className="space-y-2">
                    {filteredVolumes.map((volume) => (
                      <div
                        key={volume.name}
                        className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                          selectedVolume?.name === volume.name
                            ? 'bg-primary/10 border-primary'
                            : 'hover:bg-muted'
                        }`}
                        onClick={() => setSelectedVolume(volume)}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <HardDrive className="h-4 w-4 text-muted-foreground" />
                            <span className="font-medium text-sm">{volume.name}</span>
                          </div>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteVolume(volume.name);
                            }}
                          >
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {volume.file_count} files &middot; {formatBytes(volume.size_bytes)}
                        </div>
                        {volume.description && (
                          <div className="mt-1 text-xs text-muted-foreground truncate">
                            {volume.description}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        {/* File Browser */}
        <div className="lg:col-span-2">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center gap-2">
                <FolderOpen className="h-4 w-4" />
                <CardTitle className="text-sm font-medium">
                  {selectedVolume ? selectedVolume.name : 'Select a Volume'}
                </CardTitle>
              </div>
              {selectedVolume && (
                <CardDescription>
                  {formatBytes(selectedVolume.size_bytes)} &middot; Created{' '}
                  {formatDate(selectedVolume.created_at)}
                </CardDescription>
              )}
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[400px] pr-4">
                {!selectedVolume ? (
                  <div className="flex items-center justify-center h-full text-muted-foreground">
                    Select a volume to browse files
                  </div>
                ) : filesLoading ? (
                  <div className="space-y-2">
                    {[...Array(6)].map((_, i) => (
                      <div key={i} className="h-10 bg-muted rounded animate-pulse" />
                    ))}
                  </div>
                ) : files.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    No files in this volume
                  </div>
                ) : (
                  <div className="space-y-1">
                    {files.map((file) => (
                      <div
                        key={file.file_path}
                        className={`flex items-center justify-between p-2 rounded hover:bg-muted ${
                          onSelectFile ? 'cursor-pointer' : ''
                        }`}
                        onClick={() => handleFileClick(file)}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <File className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                          <span className="text-sm truncate">{file.file_path}</span>
                        </div>
                        <div className="flex items-center gap-4 text-xs text-muted-foreground flex-shrink-0">
                          <span>{formatBytes(file.size_bytes)}</span>
                          <span>{formatDate(file.last_modified)}</span>
                          {onSelectFile && <ChevronRight className="h-4 w-4" />}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Create Volume Dialog */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create New Volume</DialogTitle>
            <DialogDescription>
              Volumes store pre-warmed input data that agents can read from.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Volume Name</label>
              <Input
                placeholder="e.g., market_data, sec_filings"
                value={newVolumeName}
                onChange={(e) => setNewVolumeName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Description (optional)</label>
              <Input
                placeholder="Brief description of the volume contents"
                value={newVolumeDescription}
                onChange={(e) => setNewVolumeDescription(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCreateDialog(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreateVolume} disabled={!newVolumeName.trim() || creating}>
              {creating ? 'Creating...' : 'Create Volume'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmationDialog
        open={!!deleteVolumeName}
        onOpenChange={(open) => { if (!open) setDeleteVolumeName(null); }}
        title="Delete Volume"
        description={`This will permanently delete "${deleteVolumeName ?? ''}" and all its contents. This cannot be undone.`}
        onConfirm={confirmDeleteVolume}
      />
    </div>
  );
}
