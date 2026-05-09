'use client';

import { useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import {
  BookOpenText,
  Plus,
  MagnifyingGlass,
  Trash,
  DotsThree,
  FileText,
  Files,
  Clock,
} from '@phosphor-icons/react';
import { useKnowledgeBases, useCreateKnowledgeBase, useDeleteKnowledgeBase } from '@/lib/api/knowledge-bases';
import type { KnowledgeBase } from '@/lib/api/knowledge-bases';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

const statusConfig: Record<string, { label: string; color: string }> = {
  empty: { label: 'Empty', color: 'bg-gray-100 text-gray-600' },
  processing: { label: 'Processing', color: 'bg-yellow-100 text-yellow-700' },
  ready: { label: 'Ready', color: 'bg-green-100 text-green-700' },
  error: { label: 'Error', color: 'bg-red-100 text-red-700' },
};

export default function KnowledgePage() {
  const router = useRouter();
  const { data: knowledgeBases, isLoading } = useKnowledgeBases();
  const createMutation = useCreateKnowledgeBase();
  const deleteMutation = useDeleteKnowledgeBase();

  const [search, setSearch] = useState('');
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [newKbName, setNewKbName] = useState('');
  const [newKbDescription, setNewKbDescription] = useState('');
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

  const filteredKBs = (knowledgeBases ?? []).filter((kb) =>
    kb.name.toLowerCase().includes(search.toLowerCase())
  );

  const handleCreate = useCallback(async () => {
    if (!newKbName.trim()) return;
    try {
      const kb = await createMutation.mutateAsync({
        name: newKbName.trim(),
        description: newKbDescription.trim() || undefined,
      });
      setCreateDialogOpen(false);
      setNewKbName('');
      setNewKbDescription('');
      router.push(`/knowledge/${kb.id}`);
    } catch (err) {
      console.error('Failed to create KB:', err);
    }
  }, [newKbName, newKbDescription, createMutation, router]);

  const handleDelete = useCallback(async () => {
    if (!deleteConfirmId) return;
    try {
      await deleteMutation.mutateAsync(deleteConfirmId);
      setDeleteConfirmId(null);
    } catch (err) {
      console.error('Failed to delete KB:', err);
    }
  }, [deleteConfirmId, deleteMutation]);

  const deleteTargetKB = knowledgeBases?.find((kb) => kb.id === deleteConfirmId);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="shrink-0 px-6 py-4 border-b border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BookOpenText className="h-5 w-5 text-primary" weight="duotone" />
            <h1 className="text-lg font-semibold">Knowledge Bases</h1>
            {knowledgeBases && (
              <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                {knowledgeBases.length}
              </span>
            )}
          </div>
          <button
            onClick={() => setCreateDialogOpen(true)}
            className="inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
          >
            <Plus className="h-4 w-4" weight="bold" />
            Create Knowledge Base
          </button>
        </div>

        {/* Search */}
        <div className="mt-3 relative">
          <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search knowledge bases..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm border border-border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        {isLoading ? (
          <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
            Loading knowledge bases...
          </div>
        ) : filteredKBs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-center">
            <BookOpenText className="h-10 w-10 text-muted-foreground/40 mb-3" />
            <p className="text-sm text-muted-foreground">
              {search
                ? 'No knowledge bases match your search.'
                : 'No knowledge bases yet. Create one to give your agents access to your documents.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredKBs.map((kb) => (
              <KBCard
                key={kb.id}
                kb={kb}
                onClick={() => router.push(`/knowledge/${kb.id}`)}
                onDelete={() => setDeleteConfirmId(kb.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Create Dialog */}
      <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Knowledge Base</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-sm font-medium">Name</label>
              <input
                type="text"
                value={newKbName}
                onChange={(e) => setNewKbName(e.target.value)}
                placeholder="e.g., Company Policies"
                className="mt-1 w-full px-3 py-2 text-sm border border-border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
                autoFocus
                onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Description</label>
              <textarea
                value={newKbDescription}
                onChange={(e) => setNewKbDescription(e.target.value)}
                placeholder="Optional description..."
                rows={3}
                className="mt-1 w-full px-3 py-2 text-sm border border-border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none"
              />
            </div>
          </div>
          <DialogFooter>
            <button
              onClick={() => setCreateDialogOpen(false)}
              className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={!newKbName.trim() || createMutation.isPending}
              className="px-3 py-1.5 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating...' : 'Create'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={!!deleteConfirmId} onOpenChange={() => setDeleteConfirmId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Knowledge Base</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground py-2">
            Are you sure you want to delete <strong>{deleteTargetKB?.name}</strong>? This will permanently remove all documents and search data. This action cannot be undone.
          </p>
          <DialogFooter>
            <button
              onClick={() => setDeleteConfirmId(null)}
              className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
            <button
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
              className="px-3 py-1.5 text-sm font-medium bg-destructive text-destructive-foreground rounded-md hover:bg-destructive/90 disabled:opacity-50"
            >
              {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function KBCard({
  kb,
  onClick,
  onDelete,
}: {
  kb: KnowledgeBase;
  onClick: () => void;
  onDelete: () => void;
}) {
  const status = statusConfig[kb.status] || statusConfig.empty;

  return (
    <div
      onClick={onClick}
      className="group border border-border rounded-lg p-4 hover:border-primary/30 hover:shadow-sm transition-all cursor-pointer bg-card"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <Files className="h-4 w-4 text-primary/70" weight="duotone" />
          <h3 className="text-sm font-medium truncate">{kb.name}</h3>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => e.stopPropagation()}
            className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-muted transition-opacity"
          >
            <DotsThree className="h-4 w-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
              className="text-destructive"
            >
              <Trash className="h-4 w-4 mr-2" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {kb.description && (
        <p className="text-xs text-muted-foreground mb-3 line-clamp-2">
          {kb.description}
        </p>
      )}

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1">
            <FileText className="h-3 w-3" />
            {kb.document_count} docs
          </span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${status.color}`}>
            {status.label}
          </span>
        </div>
        {kb.updated_at && (
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {new Date(kb.updated_at).toLocaleDateString()}
          </span>
        )}
      </div>
    </div>
  );
}
