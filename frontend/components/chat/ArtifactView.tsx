'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  ArrowLeft,
  Copy,
  DownloadSimple,
  ArrowsOutSimple,
  File,
  Warning,
} from '@phosphor-icons/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const PREVIEWABLE_MIME_PREFIXES = ['text/', 'image/', 'application/json', 'application/pdf'];
const MAX_PREVIEW_SIZE = 10 * 1024 * 1024; // 10MB

interface ArtifactViewProps {
  path: string;
  content?: string;
  mimeType?: string;
  size?: number;
  downloadUrl?: string;
  onBack: () => void;
  className?: string;
}

function isPreviewable(mimeType?: string): boolean {
  if (!mimeType) return true; // Assume text if unknown
  return PREVIEWABLE_MIME_PREFIXES.some((prefix) => mimeType.startsWith(prefix));
}

function getLanguage(path: string): string | undefined {
  const ext = path.split('.').pop()?.toLowerCase();
  const langMap: Record<string, string> = {
    py: 'python',
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    json: 'json',
    yaml: 'yaml',
    yml: 'yaml',
    css: 'css',
    html: 'html',
    sql: 'sql',
    sh: 'bash',
    bash: 'bash',
  };
  return ext ? langMap[ext] : undefined;
}

function isMarkdown(path: string): boolean {
  return path.endsWith('.md') || path.endsWith('.mdx');
}

function isImage(mimeType?: string): boolean {
  return !!mimeType?.startsWith('image/');
}

export const ArtifactView = memo(function ArtifactView({
  path,
  content,
  mimeType,
  size,
  downloadUrl,
  onBack,
  className,
}: ArtifactViewProps) {
  const [copied, setCopied] = useState(false);
  const filename = path.split('/').pop() || path;

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

  // Too large to preview
  if (size && size > MAX_PREVIEW_SIZE) {
    return (
      <div className={cn('flex flex-col h-full', className)}>
        <ArtifactHeader filename={filename} onBack={onBack} />
        <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
          <File className="h-10 w-10 text-neutral-300 mb-3" weight="thin" />
          <p className="text-sm text-neutral-600 mb-1">File too large to preview</p>
          <p className="text-xs text-neutral-400 mb-4">
            {size ? `${(size / (1024 * 1024)).toFixed(1)} MB` : 'Unknown size'}
          </p>
          {downloadUrl && (
            <a
              href={downloadUrl}
              download={filename}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-neutral-900 text-white text-sm hover:bg-neutral-800 transition-colors"
            >
              <DownloadSimple className="h-4 w-4" />
              Download
            </a>
          )}
        </div>
      </div>
    );
  }

  // Non-previewable MIME type
  if (!isPreviewable(mimeType)) {
    return (
      <div className={cn('flex flex-col h-full', className)}>
        <ArtifactHeader filename={filename} onBack={onBack} />
        <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
          <Warning className="h-10 w-10 text-amber-400 mb-3" weight="regular" />
          <p className="text-sm text-neutral-600 mb-1">Unable to preview this file type</p>
          <p className="text-xs text-neutral-400 mb-4">{mimeType || 'Unknown type'}</p>
          {downloadUrl && (
            <a
              href={downloadUrl}
              download={filename}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-neutral-900 text-white text-sm hover:bg-neutral-800 transition-colors"
            >
              <DownloadSimple className="h-4 w-4" />
              Download instead
            </a>
          )}
        </div>
      </div>
    );
  }

  // Image preview
  if (isImage(mimeType) && downloadUrl) {
    return (
      <div className={cn('flex flex-col h-full', className)}>
        <ArtifactHeader
          filename={filename}
          onBack={onBack}
          downloadUrl={downloadUrl}
        />
        <div className="flex-1 flex items-center justify-center p-4 overflow-auto">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={downloadUrl} alt={filename} className="max-w-full max-h-full object-contain rounded" />
        </div>
      </div>
    );
  }

  // Text/code/markdown preview
  return (
    <div className={cn('flex flex-col h-full', className)}>
      <ArtifactHeader
        filename={filename}
        onBack={onBack}
        onCopy={content ? handleCopy : undefined}
        copied={copied}
        downloadUrl={downloadUrl}
      />
      <div className="flex-1 overflow-auto p-4">
        {isMarkdown(path) && content ? (
          <div className="prose prose-sm prose-neutral dark:prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        ) : (
          <pre className={cn(
            'text-sm font-mono whitespace-pre-wrap leading-relaxed',
            'text-neutral-800 dark:text-neutral-200',
          )}>
            {content || 'No content available.'}
          </pre>
        )}
      </div>
    </div>
  );
});

const ArtifactHeader = memo(function ArtifactHeader({
  filename,
  onBack,
  onCopy,
  copied,
  downloadUrl,
}: {
  filename: string;
  onBack: () => void;
  onCopy?: () => void;
  copied?: boolean;
  downloadUrl?: string;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-muted/30">
      <button
        onClick={onBack}
        className="p-1 rounded hover:bg-neutral-200 dark:hover:bg-neutral-700 transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
      </button>
      <span className="text-sm font-medium truncate flex-1">{filename}</span>
      <div className="flex items-center gap-1">
        {onCopy && (
          <button
            onClick={onCopy}
            className="p-1.5 rounded hover:bg-neutral-200 dark:hover:bg-neutral-700 transition-colors text-neutral-500"
            title={copied ? 'Copied!' : 'Copy'}
          >
            <Copy className="h-3.5 w-3.5" weight={copied ? 'fill' : 'regular'} />
          </button>
        )}
        {downloadUrl && (
          <a
            href={downloadUrl}
            download={filename}
            className="p-1.5 rounded hover:bg-neutral-200 dark:hover:bg-neutral-700 transition-colors text-neutral-500"
            title="Download"
          >
            <DownloadSimple className="h-3.5 w-3.5" />
          </a>
        )}
      </div>
    </div>
  );
});
