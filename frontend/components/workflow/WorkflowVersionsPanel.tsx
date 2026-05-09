'use client';

import { useState, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  ClockCounterClockwise,
  CheckCircle,
  Rocket,
  CircleNotch,
  ArrowCounterClockwise,
  Trash,
  Warning,
  PencilSimple,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { formatDistanceToNow, parseISO } from 'date-fns';
import {
  useWorkflowVersions,
  useActivateWorkflowVersion,
  useRollbackToVersion,
  useDeleteWorkflowVersion,
} from '@/hooks/useWorkflowVersions';
import { toast } from 'sonner';
import type { WorkflowVersion } from '@/lib/api/workflow-versions';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

interface WorkflowVersionsPanelProps {
  workflowId: string;
  currentVersionId?: string;
  onVersionActivated?: () => void;
  onRollback?: () => void;
}

export function WorkflowVersionsPanel({
  workflowId,
  currentVersionId,
  onVersionActivated,
  onRollback,
}: WorkflowVersionsPanelProps) {
  const { data: versions, isLoading, refetch } = useWorkflowVersions(workflowId);
  const activateMutation = useActivateWorkflowVersion();
  const rollbackMutation = useRollbackToVersion();
  const deleteMutation = useDeleteWorkflowVersion();

  const [confirmRollback, setConfirmRollback] = useState<WorkflowVersion | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<WorkflowVersion | null>(null);

  // Get active version
  const activeVersion = versions?.find((v) => v.is_active);

  const handleActivate = useCallback(
    async (version: WorkflowVersion) => {
      try {
        await activateMutation.mutateAsync({
          workflowId,
          versionId: version.id,
        });
        toast('Version Activated', { description: `Version ${version.version_tag || version.version_number} is now active` });
        refetch();
        onVersionActivated?.();
      } catch {
        toast.error('Error', { description: 'Failed to activate version' });
      }
    },
    [workflowId, activateMutation, refetch, onVersionActivated]
  );

  const handleRollback = useCallback(
    async (version: WorkflowVersion) => {
      try {
        await rollbackMutation.mutateAsync({
          workflowId,
          versionId: version.id,
        });
        toast('Rolled Back', { description: `Workflow rolled back to version ${version.version_tag || version.version_number}` });
        refetch();
        onRollback?.();
      } catch {
        toast.error('Error', { description: 'Failed to rollback to version' });
      }
      setConfirmRollback(null);
    },
    [workflowId, rollbackMutation, refetch, onRollback]
  );

  const handleDelete = useCallback(
    async (version: WorkflowVersion) => {
      try {
        await deleteMutation.mutateAsync({
          workflowId,
          versionId: version.id,
        });
        toast('Version Deleted', { description: `Version ${version.version_tag || version.version_number} has been deleted` });
        refetch();
      } catch {
        toast.error('Error', { description: 'Failed to delete version' });
      }
      setConfirmDelete(null);
    },
    [workflowId, deleteMutation, refetch]
  );

  const getStatusBadge = (version: WorkflowVersion) => {
    if (version.is_active) {
      return (
        <Badge variant="default" className="bg-green-600 text-[10px] py-0 h-4">
          <CheckCircle className="h-2.5 w-2.5 mr-0.5" weight="fill" />
          Active
        </Badge>
      );
    }
    if (version.is_draft) {
      return (
        <Badge variant="outline" className="bg-amber-100 text-amber-800 border-amber-300 text-[10px] py-0 h-4">
          <PencilSimple className="h-2.5 w-2.5 mr-0.5" weight="fill" />
          Draft
        </Badge>
      );
    }
    return (
      <Badge variant="secondary" className="text-[10px] py-0 h-4">
        Published
      </Badge>
    );
  };

  return (
    <div className="flex flex-col h-full bg-background">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b">
        <div className="flex items-center gap-2">
          <ClockCounterClockwise className="h-5 w-5 text-muted-foreground" />
          <span className="font-medium">Versions</span>
          <Badge variant="secondary" className="text-xs">
            {versions?.length || 0}
          </Badge>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-32">
          <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="p-3 space-y-4">
            {/* Active Version Summary */}
            {activeVersion && (
              <div className="p-3 rounded-lg border bg-green-50 border-green-200">
                <div className="flex items-center gap-2 mb-2">
                  <CheckCircle className="h-4 w-4 text-green-600" weight="fill" />
                  <span className="text-sm font-medium text-green-800">
                    Active Version: v{activeVersion.version_tag || activeVersion.version_number}
                  </span>
                </div>
                <div className="text-xs text-green-700 space-y-1">
                  <div className="flex justify-between">
                    <span>Nodes:</span>
                    <span className="font-medium">{activeVersion.node_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Edges:</span>
                    <span className="font-medium">{activeVersion.edge_count}</span>
                  </div>
                  {activeVersion.published_at && (
                    <div className="flex justify-between">
                      <span>Published:</span>
                      <span className="font-medium">
                        {formatDistanceToNow(parseISO(activeVersion.published_at), { addSuffix: true })}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Version List */}
            <div className="space-y-2">
              <span className="text-xs font-medium text-muted-foreground">
                Version History
              </span>
              {!versions || versions.length === 0 ? (
                <div className="text-center py-6 text-muted-foreground">
                  <ClockCounterClockwise className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">No versions yet</p>
                  <p className="text-xs mt-1">Save your workflow to create a version</p>
                </div>
              ) : (
                versions.map((version) => (
                  <div
                    key={version.id}
                    className={cn(
                      'p-3 rounded-lg border transition-colors',
                      version.is_active
                        ? 'bg-green-50 border-green-200'
                        : version.is_draft
                        ? 'bg-amber-50 border-amber-200'
                        : 'bg-card hover:bg-accent/50'
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">v{version.version_tag || version.version_number}</span>
                        {getStatusBadge(version)}
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {version.node_count} nodes
                      </span>
                    </div>

                    {version.description && (
                      <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                        {version.description}
                      </p>
                    )}

                    <div className="flex items-center justify-between mt-2 text-xs text-muted-foreground">
                      <span>
                        {formatDistanceToNow(parseISO(version.created_at), { addSuffix: true })}
                      </span>
                    </div>

                    {/* Actions */}
                    {!version.is_active && (
                      <div className="mt-2 flex gap-2">
                        {version.is_draft ? (
                          <Button
                            variant="default"
                            size="sm"
                            className="flex-1 h-7 text-xs gap-1"
                            onClick={() => handleActivate(version)}
                            disabled={activateMutation.isPending}
                          >
                            {activateMutation.isPending ? (
                              <CircleNotch className="h-3 w-3 animate-spin" />
                            ) : (
                              <Rocket className="h-3 w-3" />
                            )}
                            Publish
                          </Button>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            className="flex-1 h-7 text-xs gap-1"
                            onClick={() => handleActivate(version)}
                            disabled={activateMutation.isPending}
                          >
                            {activateMutation.isPending ? (
                              <CircleNotch className="h-3 w-3 animate-spin" />
                            ) : (
                              <CheckCircle className="h-3 w-3" />
                            )}
                            Activate
                          </Button>
                        )}
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs gap-1"
                          onClick={() => setConfirmRollback(version)}
                          disabled={rollbackMutation.isPending}
                        >
                          <ArrowCounterClockwise className="h-3 w-3" />
                          Rollback
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0 text-destructive hover:text-destructive"
                          onClick={() => setConfirmDelete(version)}
                          disabled={deleteMutation.isPending}
                        >
                          <Trash className="h-3 w-3" />
                        </Button>
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </ScrollArea>
      )}

      {/* Rollback Confirmation Dialog */}
      <AlertDialog open={!!confirmRollback} onOpenChange={() => setConfirmRollback(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Warning className="h-5 w-5 text-amber-500" />
              Rollback to Version {confirmRollback?.version_tag || confirmRollback?.version_number}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This will replace your current workflow with the spec from version{' '}
              {confirmRollback?.version_tag || confirmRollback?.version_number}. Any unsaved changes will be lost.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => confirmRollback && handleRollback(confirmRollback)}
              className="bg-amber-600 hover:bg-amber-700"
            >
              Rollback
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={!!confirmDelete} onOpenChange={() => setConfirmDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Trash className="h-5 w-5 text-destructive" />
              Delete Version {confirmDelete?.version_tag || confirmDelete?.version_number}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete version{' '}
              {confirmDelete?.version_tag || confirmDelete?.version_number}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => confirmDelete && handleDelete(confirmDelete)}
              className="bg-destructive hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
