'use client';

/**
 * ArtifactModal — Full-screen modal overlay for viewing artifact content.
 *
 * Opens when clicking on a FilePreview card. Shows title bar with
 * filename, copy/download/close buttons, and scrollable rendered content.
 */

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  X,
  DownloadSimple,
  Copy,
  Check,
  FileText,
  FileCode,
  FilePdf,
  FileXls,
  FileCsv,
  FilePy,
  FileJs,
  FileHtml,
  File,
} from '@phosphor-icons/react';
import * as Dialog from '@radix-ui/react-dialog';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export interface ArtifactModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;
  content?: string;
  fileType?: string;
  downloadUrl?: string;
  lastModified?: string;
}

// Map file extensions to icons
function getFileTypeIcon(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  switch (ext) {
    case 'py': return FilePy;
    case 'js': case 'jsx': case 'ts': case 'tsx': return FileJs;
    case 'html': case 'htm': return FileHtml;
    case 'pdf': return FilePdf;
    case 'xls': case 'xlsx': return FileXls;
    case 'csv': return FileCsv;
    case 'json': case 'yaml': case 'yml': case 'xml': case 'sql': return FileCode;
    case 'md': case 'mdx': case 'txt': return FileText;
    default: return File;
  }
}

function isMarkdownFile(filename: string): boolean {
  return /\.(md|mdx|markdown)$/i.test(filename);
}

function isCodeFile(filename: string): boolean {
  return /\.(py|js|jsx|ts|tsx|json|yaml|yml|xml|sql|csv|html|htm|css|scss|toml|ini|cfg|sh|bash|go|rs|java|c|cpp|h|rb)$/i.test(filename);
}

export const ArtifactModal = memo(function ArtifactModal({
  open,
  onOpenChange,
  filename,
  content,
  fileType,
  downloadUrl,
  lastModified,
}: ArtifactModalProps) {
  const [copied, setCopied] = useState(false);
  const FileIcon = getFileTypeIcon(filename);
  const ext = filename.split('.').pop()?.toUpperCase() || 'FILE';

  const handleDownload = useCallback(() => {
    if (downloadUrl) {
      window.open(downloadUrl, '_blank');
    } else if (content) {
      const blob = new Blob([content], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    }
  }, [content, downloadUrl, filename]);

  const handleCopy = useCallback(async () => {
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may not be available
    }
  }, [content]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out" />
        <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[90vw] max-w-4xl h-[85vh] bg-background rounded-xl shadow-2xl border border-border flex flex-col data-[state=open]:animate-scale-in">
          {/* Title bar */}
          <div className="flex items-center gap-3 px-6 py-4 border-b border-border shrink-0">
            <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center shrink-0">
              <FileIcon size={18} weight="fill" className="text-muted-foreground" />
            </div>
            <div className="flex-1 min-w-0">
              <Dialog.Title className="text-base font-semibold truncate">
                {filename}
              </Dialog.Title>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>{ext}</span>
                {lastModified && (
                  <>
                    <span className="text-muted-foreground/30">·</span>
                    <span>{lastModified}</span>
                  </>
                )}
              </div>
            </div>
            <div className="flex items-center gap-1">
              {content && (
                <button
                  onClick={handleCopy}
                  className="p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                  title={copied ? 'Copied!' : 'Copy content'}
                >
                  {copied ? <Check size={16} /> : <Copy size={16} />}
                </button>
              )}
              {(downloadUrl || content) && (
                <button
                  onClick={handleDownload}
                  className="p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                  title="Download"
                >
                  <DownloadSimple size={16} />
                </button>
              )}
              <Dialog.Close className="p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors" title="Close">
                <X size={16} />
              </Dialog.Close>
            </div>
          </div>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto p-6">
            {content ? (
              isMarkdownFile(filename) ? (
                <div className="prose prose-sm dark:prose-invert max-w-none prose-p:leading-relaxed prose-headings:mt-4 prose-headings:mb-2">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                </div>
              ) : isCodeFile(filename) ? (
                <pre className="text-sm font-mono bg-muted/30 rounded-lg p-4 overflow-x-auto leading-relaxed">
                  <code>{content}</code>
                </pre>
              ) : (
                <pre className="text-sm whitespace-pre-wrap leading-relaxed">{content}</pre>
              )
            ) : (
              <p className="text-muted-foreground text-sm">No content available for preview.</p>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
});
