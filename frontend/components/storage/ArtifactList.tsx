'use client';

import React, { useState, useEffect, useMemo } from 'react';
import {
  listSessionArtifacts,
  getArtifactDownloadUrl,
  deleteArtifact,
  deleteSessionArtifacts,
  getArtifactMetadata,
  ArtifactInfo,
  ArtifactMetadata,
} from '@/lib/api/artifacts';
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Search,
  RefreshCw,
  AlertCircle,
  FileText,
  Download,
  Trash2,
  MoreVertical,
  FileJson,
  FileCode,
  FileImage,
  File,
  Archive,
  Info,
  Clock,
  User,
  Bot,
  Shield,
} from 'lucide-react';

interface ArtifactListProps {
  sessionId: string;
  onSelectArtifact?: (artifact: ArtifactInfo) => void;
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

function getFileIcon(contentType: string) {
  if (contentType.includes('json')) return FileJson;
  if (contentType.includes('javascript') || contentType.includes('python') || contentType.includes('code'))
    return FileCode;
  if (contentType.includes('image')) return FileImage;
  if (contentType.includes('text')) return FileText;
  if (contentType.includes('zip') || contentType.includes('tar') || contentType.includes('gzip'))
    return Archive;
  return File;
}

function getFileTypeLabel(contentType: string): string {
  const typeMap: Record<string, string> = {
    'application/json': 'JSON',
    'text/plain': 'Text',
    'text/markdown': 'Markdown',
    'text/csv': 'CSV',
    'application/pdf': 'PDF',
    'image/png': 'PNG',
    'image/jpeg': 'JPEG',
    'application/javascript': 'JavaScript',
  };
  return typeMap[contentType] || contentType.split('/')[1]?.toUpperCase() || 'File';
}

export function ArtifactList({ sessionId, onSelectArtifact }: ArtifactListProps) {
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([]);
  const [totalSize, setTotalSize] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedMetadata, setSelectedMetadata] = useState<ArtifactMetadata | null>(null);
  const [showMetadataDialog, setShowMetadataDialog] = useState(false);
  const [loadingMetadata, setLoadingMetadata] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ArtifactInfo | null>(null);
  const [showDeleteAllDialog, setShowDeleteAllDialog] = useState(false);

  const fetchArtifacts = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listSessionArtifacts(sessionId);
      setArtifacts(response.artifacts);
      setTotalSize(response.total_size_bytes);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to fetch artifacts';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (sessionId) {
      fetchArtifacts();
    }
  }, [sessionId]);

  const filteredArtifacts = useMemo(() => {
    return artifacts.filter((artifact) =>
      artifact.file_path.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [artifacts, searchQuery]);

  const handleDownload = async (artifact: ArtifactInfo) => {
    try {
      const response = await getArtifactDownloadUrl(sessionId, artifact.file_path);
      window.open(response.download_url, '_blank');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to get download URL';
      setError(message);
    }
  };

  const handleDelete = (artifact: ArtifactInfo) => {
    setDeleteTarget(artifact);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteArtifact(sessionId, deleteTarget.file_path);
      fetchArtifacts();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to delete artifact';
      setError(message);
    }
  };

  const handleDeleteAll = () => {
    setShowDeleteAllDialog(true);
  };

  const confirmDeleteAll = async () => {
    try {
      await deleteSessionArtifacts(sessionId);
      fetchArtifacts();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to delete artifacts';
      setError(message);
    }
  };

  const handleViewMetadata = async (artifact: ArtifactInfo) => {
    setLoadingMetadata(true);
    setShowMetadataDialog(true);
    try {
      const metadata = await getArtifactMetadata(sessionId, artifact.file_path);
      setSelectedMetadata(metadata);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load metadata';
      setError(message);
      setShowMetadataDialog(false);
    } finally {
      setLoadingMetadata(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center">
        <div className="flex items-center gap-2">
          <Archive className="h-5 w-5 text-primary" />
          <h2 className="text-heading-3">Artifacts</h2>
          <Badge variant="secondary">{artifacts.length}</Badge>
          <Badge variant="outline">{formatBytes(totalSize)}</Badge>
        </div>

        <div className="flex gap-2 w-full sm:w-auto">
          <div className="relative flex-1 sm:w-64">
            <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search artifacts..."
              className="pl-8"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <Button variant="outline" size="icon" onClick={fetchArtifacts} title="Refresh">
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>

          {artifacts.length > 0 && (
            <Button variant="destructive" size="sm" onClick={handleDeleteAll}>
              <Trash2 className="h-4 w-4 mr-2" />
              Delete All
            </Button>
          )}
        </div>
      </div>

      {/* Info Banner */}
      <div className="bg-muted/50 rounded-lg p-3 flex items-center gap-2 text-sm">
        <Shield className="h-4 w-4 text-green-600" />
        <span className="text-muted-foreground">
          Artifacts are retained for 7 years for regulatory compliance
        </span>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Artifact List */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-card-title">Session Artifacts</CardTitle>
          <CardDescription>Analysis reports, compliance audits, and other outputs</CardDescription>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-[400px] pr-4">
            {loading ? (
              <div className="space-y-2">
                {[...Array(5)].map((_, i) => (
                  <div key={i} className="h-14 bg-muted rounded animate-pulse" />
                ))}
              </div>
            ) : filteredArtifacts.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
                <Archive className="h-12 w-12 mb-4 opacity-50" />
                <p className="text-body-muted">No artifacts found</p>
                <p className="text-detail mt-1">
                  Artifacts will appear here when agents generate reports
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {filteredArtifacts.map((artifact) => {
                  const IconComponent = getFileIcon(artifact.content_type);
                  return (
                    <div
                      key={artifact.file_path}
                      className={`flex items-center justify-between p-3 rounded-lg border hover:bg-muted/50 transition-colors ${
                        onSelectArtifact ? 'cursor-pointer' : ''
                      }`}
                      onClick={() => onSelectArtifact?.(artifact)}
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="p-2 rounded-lg bg-muted">
                          <IconComponent className="h-4 w-4 text-muted-foreground" />
                        </div>
                        <div className="min-w-0">
                          <div className="text-card-title truncate">
                            {artifact.file_path.split('/').pop()}
                          </div>
                          <div className="text-detail truncate">
                            {artifact.file_path}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-4">
                        <div className="text-right hidden sm:block">
                          <Badge variant="outline" className="text-xs">
                            {getFileTypeLabel(artifact.content_type)}
                          </Badge>
                          <div className="text-xs text-muted-foreground mt-1">
                            {formatBytes(artifact.size_bytes)}
                          </div>
                        </div>

                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => handleViewMetadata(artifact)}>
                              <Info className="h-4 w-4 mr-2" />
                              View Metadata
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleDownload(artifact)}>
                              <Download className="h-4 w-4 mr-2" />
                              Download
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className="text-destructive"
                              onClick={() => handleDelete(artifact)}
                            >
                              <Trash2 className="h-4 w-4 mr-2" />
                              Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </CardContent>
      </Card>

      {/* Metadata Dialog */}
      <Dialog open={showMetadataDialog} onOpenChange={setShowMetadataDialog}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Artifact Metadata</DialogTitle>
            <DialogDescription>
              Detailed information about this artifact
            </DialogDescription>
          </DialogHeader>

          {loadingMetadata ? (
            <div className="space-y-3 py-4">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="h-6 bg-muted rounded animate-pulse" />
              ))}
            </div>
          ) : selectedMetadata ? (
            <div className="space-y-4 py-4">
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <div className="flex items-center gap-2 text-muted-foreground mb-1">
                    <FileText className="h-3 w-3" />
                    <span>File Path</span>
                  </div>
                  <span className="font-medium break-all">{selectedMetadata.file_path}</span>
                </div>
                <div>
                  <div className="flex items-center gap-2 text-muted-foreground mb-1">
                    <Archive className="h-3 w-3" />
                    <span>Size</span>
                  </div>
                  <span className="font-medium">{formatBytes(selectedMetadata.size_bytes)}</span>
                </div>
                <div>
                  <div className="flex items-center gap-2 text-muted-foreground mb-1">
                    <Clock className="h-3 w-3" />
                    <span>Created</span>
                  </div>
                  <span className="font-medium">{formatDate(selectedMetadata.created_at)}</span>
                </div>
                <div>
                  <div className="flex items-center gap-2 text-muted-foreground mb-1">
                    <File className="h-3 w-3" />
                    <span>Content Type</span>
                  </div>
                  <span className="font-medium">{selectedMetadata.content_type}</span>
                </div>
                {selectedMetadata.user_id && (
                  <div>
                    <div className="flex items-center gap-2 text-muted-foreground mb-1">
                      <User className="h-3 w-3" />
                      <span>User ID</span>
                    </div>
                    <span className="font-medium font-mono text-xs">
                      {selectedMetadata.user_id}
                    </span>
                  </div>
                )}
                {selectedMetadata.agent_id && (
                  <div>
                    <div className="flex items-center gap-2 text-muted-foreground mb-1">
                      <Bot className="h-3 w-3" />
                      <span>Agent</span>
                    </div>
                    <span className="font-medium">{selectedMetadata.agent_id}</span>
                  </div>
                )}
                {selectedMetadata.retention_until && (
                  <div className="col-span-2">
                    <div className="flex items-center gap-2 text-muted-foreground mb-1">
                      <Shield className="h-3 w-3" />
                      <span>Retention Until</span>
                    </div>
                    <span className="font-medium text-green-600">
                      {formatDate(selectedMetadata.retention_until)}
                    </span>
                  </div>
                )}
                {selectedMetadata.checksum && (
                  <div className="col-span-2">
                    <div className="flex items-center gap-2 text-muted-foreground mb-1">
                      <Info className="h-3 w-3" />
                      <span>Checksum (SHA256)</span>
                    </div>
                    <span className="font-mono text-xs break-all">
                      {selectedMetadata.checksum}
                    </span>
                  </div>
                )}
              </div>
            </div>
          ) : null}

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowMetadataDialog(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmationDialog
        open={!!deleteTarget}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}
        title="Delete Artifact"
        description={`This will permanently delete "${deleteTarget?.file_path.split('/').pop() ?? ''}". This cannot be undone.`}
        onConfirm={confirmDelete}
      />

      <ConfirmationDialog
        open={showDeleteAllDialog}
        onOpenChange={setShowDeleteAllDialog}
        title="Delete All Artifacts"
        description={`This will permanently delete all ${artifacts.length} artifacts in this session. This cannot be undone.`}
        confirmLabel="Delete All"
        onConfirm={confirmDeleteAll}
      />
    </div>
  );
}
