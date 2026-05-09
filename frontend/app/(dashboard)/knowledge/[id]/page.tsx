'use client';

import { useState, useCallback, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  ArrowLeft,
  FileText,
  MagnifyingGlass,
  GearSix,
  Upload,
  Trash,
  Check,
  SpinnerGap,
  WarningCircle,
  Clock,
  CaretDown,
  CaretRight,
} from '@phosphor-icons/react';
import {
  useKnowledgeBase,
  useKnowledgeDocuments,
  useUploadDocument,
  useDeleteDocument,
  updateKnowledgeBase,
  deleteKnowledgeBase,
  searchKnowledgeBase,
} from '@/lib/api/knowledge-bases';
import type { KnowledgeDocument, KnowledgeSearchResult } from '@/lib/api/knowledge-bases';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { useQueryClient } from '@tanstack/react-query';

type Tab = 'documents' | 'search' | 'settings';

const statusIcon: Record<string, React.ReactNode> = {
  uploading: <SpinnerGap className="h-3.5 w-3.5 text-blue-500 animate-spin" />,
  processing: <SpinnerGap className="h-3.5 w-3.5 text-yellow-500 animate-spin" />,
  ready: <Check className="h-3.5 w-3.5 text-green-500" />,
  error: <WarningCircle className="h-3.5 w-3.5 text-red-500" />,
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function KnowledgeDetailPage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const kbId = params.id as string;

  const { data: kb, isLoading: kbLoading } = useKnowledgeBase(kbId);
  const { data: documents, isLoading: docsLoading } = useKnowledgeDocuments(kbId);
  const uploadMutation = useUploadDocument(kbId);
  const deleteMutation = useDeleteDocument(kbId);

  const [activeTab, setActiveTab] = useState<Tab>('documents');
  const [deleteDocId, setDeleteDocId] = useState<string | null>(null);

  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  // Settings state
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [settingsInited, setSettingsInited] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [deleteKBConfirm, setDeleteKBConfirm] = useState(false);

  // File input ref
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Init settings form when KB loads
  if (kb && !settingsInited) {
    setEditName(kb.name);
    setEditDescription(kb.description || '');
    setSettingsInited(true);
  }

  const handleFileUpload = useCallback(
    async (files: FileList | null) => {
      if (!files) return;
      for (const file of Array.from(files)) {
        try {
          await uploadMutation.mutateAsync(file);
        } catch (err) {
          console.error(`Failed to upload ${file.name}:`, err);
        }
      }
    },
    [uploadMutation]
  );

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) return;
    setIsSearching(true);
    try {
      const results = await searchKnowledgeBase(kbId, searchQuery.trim());
      setSearchResults(results);
    } catch (err) {
      console.error('Search failed:', err);
    } finally {
      setIsSearching(false);
    }
  }, [kbId, searchQuery]);

  const handleSaveSettings = useCallback(async () => {
    setIsSaving(true);
    try {
      await updateKnowledgeBase(kbId, {
        name: editName.trim(),
        description: editDescription.trim() || undefined,
      });
      queryClient.invalidateQueries({ queryKey: ['knowledge-base', kbId] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] });
    } catch (err) {
      console.error('Failed to save settings:', err);
    } finally {
      setIsSaving(false);
    }
  }, [kbId, editName, editDescription, queryClient]);

  const handleDeleteKB = useCallback(async () => {
    try {
      await deleteKnowledgeBase(kbId);
      router.push('/knowledge');
    } catch (err) {
      console.error('Failed to delete KB:', err);
    }
  }, [kbId, router]);

  const handleDeleteDoc = useCallback(async () => {
    if (!deleteDocId) return;
    try {
      await deleteMutation.mutateAsync(deleteDocId);
      setDeleteDocId(null);
    } catch (err) {
      console.error('Failed to delete document:', err);
    }
  }, [deleteDocId, deleteMutation]);

  if (kbLoading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Loading...
      </div>
    );
  }

  if (!kb) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Knowledge base not found
      </div>
    );
  }

  const tabs: { key: Tab; label: string; icon: React.ElementType }[] = [
    { key: 'documents', label: 'Documents', icon: FileText },
    { key: 'search', label: 'Search Preview', icon: MagnifyingGlass },
    { key: 'settings', label: 'Settings', icon: GearSix },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="shrink-0 px-6 py-4 border-b border-border">
        <div className="flex items-center gap-3 mb-3">
          <button
            onClick={() => router.push('/knowledge')}
            className="p-1 rounded hover:bg-muted transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <h1 className="text-lg font-semibold">{kb.name}</h1>
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${
            kb.status === 'ready' ? 'bg-green-100 text-green-700' :
            kb.status === 'processing' ? 'bg-yellow-100 text-yellow-700' :
            kb.status === 'error' ? 'bg-red-100 text-red-700' :
            'bg-gray-100 text-gray-600'
          }`}>
            {kb.status}
          </span>
        </div>
        {kb.description && (
          <p className="text-sm text-muted-foreground mb-3">{kb.description}</p>
        )}

        {/* Tabs */}
        <div className="flex gap-1">
          {tabs.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md transition-colors ${
                activeTab === key
                  ? 'bg-primary/10 text-primary font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-auto p-6">
        {activeTab === 'documents' && (
          <DocumentsTab
            documents={documents ?? []}
            isLoading={docsLoading}
            isUploading={uploadMutation.isPending}
            fileInputRef={fileInputRef}
            onUpload={handleFileUpload}
            onDelete={setDeleteDocId}
          />
        )}
        {activeTab === 'search' && (
          <SearchTab
            query={searchQuery}
            onQueryChange={setSearchQuery}
            onSearch={handleSearch}
            results={searchResults}
            isSearching={isSearching}
          />
        )}
        {activeTab === 'settings' && (
          <SettingsTab
            name={editName}
            description={editDescription}
            onNameChange={setEditName}
            onDescriptionChange={setEditDescription}
            onSave={handleSaveSettings}
            isSaving={isSaving}
            onDelete={() => setDeleteKBConfirm(true)}
          />
        )}
      </div>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.csv,.md"
        className="hidden"
        onChange={(e) => handleFileUpload(e.target.files)}
      />

      {/* Delete Document Dialog */}
      <Dialog open={!!deleteDocId} onOpenChange={() => setDeleteDocId(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Document</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground py-2">
            This will permanently remove this document and its search data.
          </p>
          <DialogFooter>
            <button onClick={() => setDeleteDocId(null)} className="px-3 py-1.5 text-sm text-muted-foreground">
              Cancel
            </button>
            <button
              onClick={handleDeleteDoc}
              disabled={deleteMutation.isPending}
              className="px-3 py-1.5 text-sm font-medium bg-destructive text-destructive-foreground rounded-md hover:bg-destructive/90 disabled:opacity-50"
            >
              Delete
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete KB Dialog */}
      <Dialog open={deleteKBConfirm} onOpenChange={setDeleteKBConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Knowledge Base</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground py-2">
            Are you sure? This will permanently delete <strong>{kb.name}</strong> and all its documents.
          </p>
          <DialogFooter>
            <button onClick={() => setDeleteKBConfirm(false)} className="px-3 py-1.5 text-sm text-muted-foreground">
              Cancel
            </button>
            <button
              onClick={handleDeleteKB}
              className="px-3 py-1.5 text-sm font-medium bg-destructive text-destructive-foreground rounded-md hover:bg-destructive/90"
            >
              Delete Knowledge Base
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ============================================================================
// Documents Tab
// ============================================================================

function DocumentsTab({
  documents,
  isLoading,
  isUploading,
  fileInputRef,
  onUpload,
  onDelete,
}: {
  documents: KnowledgeDocument[];
  isLoading: boolean;
  isUploading: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onUpload: (files: FileList | null) => void;
  onDelete: (id: string) => void;
}) {
  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      onUpload(e.dataTransfer.files);
    },
    [onUpload]
  );

  return (
    <div className="space-y-4">
      {/* Upload zone */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className="border-2 border-dashed border-border rounded-lg p-8 text-center hover:border-primary/40 hover:bg-primary/5 transition-colors cursor-pointer"
      >
        <Upload className="h-8 w-8 text-muted-foreground/50 mx-auto mb-2" />
        <p className="text-sm text-muted-foreground">
          {isUploading ? 'Uploading...' : 'Drop files here or click to upload'}
        </p>
        <p className="text-xs text-muted-foreground/60 mt-1">
          PDF, DOCX, TXT, CSV, Markdown (max 50MB each)
        </p>
      </div>

      {/* Document list */}
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading documents...</p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-8">
          No documents uploaded yet.
        </p>
      ) : (
        <div className="border border-border rounded-lg divide-y divide-border">
          {documents.map((doc) => (
            <div key={doc.id} className="flex items-center gap-3 px-4 py-3 group">
              <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium truncate">{doc.filename}</span>
                  {doc.version > 1 && (
                    <span className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                      v{doc.version}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                  <span>{formatBytes(doc.file_size_bytes)}</span>
                  <span>{doc.chunk_count} chunks</span>
                  {doc.created_at && (
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {new Date(doc.created_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {statusIcon[doc.status] || null}
                {doc.error_message && (
                  <span className="text-xs text-red-500 max-w-[200px] truncate" title={doc.error_message}>
                    {doc.error_message}
                  </span>
                )}
                <button
                  onClick={() => onDelete(doc.id)}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-destructive/10 hover:text-destructive transition-all"
                >
                  <Trash className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Search Preview Tab
// ============================================================================

function SearchTab({
  query,
  onQueryChange,
  onSearch,
  results,
  isSearching,
}: {
  query: string;
  onQueryChange: (q: string) => void;
  onSearch: () => void;
  results: KnowledgeSearchResult[];
  isSearching: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onSearch()}
            placeholder="Test a query against this knowledge base..."
            className="w-full pl-9 pr-3 py-2 text-sm border border-border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>
        <button
          onClick={onSearch}
          disabled={!query.trim() || isSearching}
          className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
        >
          {isSearching ? 'Searching...' : 'Search'}
        </button>
      </div>

      {results.length > 0 ? (
        <div className="space-y-3">
          {results.map((result, i) => (
            <div key={result.id} className="border border-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-muted-foreground">
                  Result {i + 1} &middot; Score: {(result.score * 100).toFixed(1)}%
                </span>
                <span className="text-xs text-muted-foreground">
                  Chunk #{result.chunk_index}
                </span>
              </div>
              <p className="text-sm whitespace-pre-wrap">{result.content}</p>
            </div>
          ))}
        </div>
      ) : query && !isSearching ? (
        <p className="text-sm text-muted-foreground text-center py-8">
          No results found. Try a different query or upload more documents.
        </p>
      ) : null}
    </div>
  );
}

// ============================================================================
// Settings Tab
// ============================================================================

function SettingsTab({
  name,
  description,
  onNameChange,
  onDescriptionChange,
  onSave,
  isSaving,
  onDelete,
}: {
  name: string;
  description: string;
  onNameChange: (v: string) => void;
  onDescriptionChange: (v: string) => void;
  onSave: () => void;
  isSaving: boolean;
  onDelete: () => void;
}) {
  return (
    <div className="max-w-lg space-y-6">
      <div>
        <label className="text-sm font-medium">Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          className="mt-1 w-full px-3 py-2 text-sm border border-border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </div>
      <div>
        <label className="text-sm font-medium">Description</label>
        <textarea
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          rows={3}
          className="mt-1 w-full px-3 py-2 text-sm border border-border rounded-md bg-background focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none"
        />
      </div>
      <button
        onClick={onSave}
        disabled={isSaving || !name.trim()}
        className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
      >
        {isSaving ? 'Saving...' : 'Save Changes'}
      </button>

      <div className="pt-6 border-t border-border">
        <h3 className="text-sm font-medium text-destructive mb-2">Danger Zone</h3>
        <p className="text-xs text-muted-foreground mb-3">
          Permanently delete this knowledge base and all its documents.
        </p>
        <button
          onClick={onDelete}
          className="px-4 py-2 text-sm font-medium bg-destructive text-destructive-foreground rounded-md hover:bg-destructive/90"
        >
          Delete Knowledge Base
        </button>
      </div>
    </div>
  );
}
