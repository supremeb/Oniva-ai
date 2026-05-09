'use client';

import { memo, useCallback, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { X, Files, LinkSimple, FolderOpen, Globe } from '@phosphor-icons/react';
import { useChatUIStore, type RightPanelContent } from '@/lib/stores/chat-ui';
import { FileTree, type FileNode } from './FileTree';
import { ArtifactView } from './ArtifactView';
import { SourceCard } from './shared/data/SourceCard';

export interface FileMeta {
  content?: string;
  downloadUrl?: string;
  mimeType?: string;
  size?: number;
}

interface RightPanelProps {
  files: FileNode[];
  onFileSelect?: (path: string) => void;
  /** Map of file path → metadata for artifact view */
  fileMeta?: Record<string, FileMeta>;
  artifactContent?: string;
  sources: Array<{ type?: string; title?: string; url?: string }>;
  className?: string;
}

export const RightPanel = memo(function RightPanel({
  files,
  onFileSelect: onFileSelectProp,
  fileMeta,
  artifactContent,
  sources,
  className,
}: RightPanelProps) {
  const {
    rightPanelContent,
    setRightPanelContent,
    closeRightPanel,
    openArtifact,
  } = useChatUIStore();

  // Only show Sources tab when there are actual sources (deep work / KB RAG)
  // Web search sources are shown inline via SourcesPill, not in the sidebar
  const tabs = useMemo(() => {
    const t: { id: 'files' | 'sources'; label: string; icon: typeof Files }[] = [
      { id: 'files', label: 'Files', icon: Files },
    ];
    if (sources.length > 0) {
      t.push({ id: 'sources', label: 'Sources', icon: LinkSimple });
    }
    return t;
  }, [sources.length]);

  const activeTab = rightPanelContent.tab;

  const handleTabClick = useCallback(
    (tab: 'files' | 'sources') => {
      setRightPanelContent({ tab });
    },
    [setRightPanelContent],
  );

  const handleFileSelect = useCallback(
    (path: string) => {
      if (onFileSelectProp) {
        onFileSelectProp(path);
      } else {
        openArtifact(path);
      }
    },
    [onFileSelectProp, openArtifact],
  );

  const handleArtifactBack = useCallback(() => {
    setRightPanelContent({ tab: 'files' });
  }, [setRightPanelContent]);

  // Artifact view (full panel takeover)
  if (activeTab === 'artifact' && 'path' in rightPanelContent) {
    const meta = fileMeta?.[rightPanelContent.path];
    return (
      <div className={cn('flex flex-col h-full bg-white dark:bg-neutral-900', className)}>
        <ArtifactView
          path={rightPanelContent.path}
          content={artifactContent ?? meta?.content}
          downloadUrl={meta?.downloadUrl}
          mimeType={meta?.mimeType}
          size={meta?.size}
          onBack={handleArtifactBack}
        />
      </div>
    );
  }

  return (
    <div className={cn('flex flex-col h-full bg-white dark:bg-neutral-900', className)}>
      {/* Header with tabs and close */}
      <div className="flex items-center border-b border-border">
        <div className="flex items-center flex-1">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => handleTabClick(id)}
              className={cn(
                'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors border-b-2',
                activeTab === id
                  ? 'text-neutral-900 dark:text-neutral-100 border-neutral-900 dark:border-neutral-100'
                  : 'text-neutral-500 border-transparent hover:text-neutral-700 dark:hover:text-neutral-300',
              )}
            >
              <Icon className="h-4 w-4" weight={activeTab === id ? 'fill' : 'regular'} />
              {label}
            </button>
          ))}
        </div>
        <button
          onClick={closeRightPanel}
          className="p-2 mr-1 rounded hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors text-neutral-500"
          title="Close panel"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-auto">
        {activeTab === 'files' && (
          files.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-neutral-400">
              <FolderOpen size={24} className="mb-2" />
              <p className="text-sm">Files and artifacts will appear here</p>
            </div>
          ) : (
            <FileTree files={files} onFileSelect={handleFileSelect} />
          )
        )}
        {activeTab === 'sources' && (
          sources.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-neutral-400">
              <Globe size={24} className="mb-2" />
              <p className="text-sm">No sources yet</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2 p-3">
              {sources.map((s, i) => (
                <SourceCard
                  key={`${i}-${s.url || ''}`}
                  type={s.type === 'knowledge_base' ? 'database' : 'url'}
                  title={s.title ?? 'Source'}
                  url={s.url}
                  compact
                />
              ))}
            </div>
          )
        )}
      </div>
    </div>
  );
});
