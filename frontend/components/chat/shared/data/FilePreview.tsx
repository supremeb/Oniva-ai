'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  File,
  FileDoc,
  FilePdf,
  FileXls,
  FilePpt,
  FileCode,
  FileImage,
  FileVideo,
  FileAudio,
  FileZip,
  FileCsv,
  FileJs,
  FilePy,
  FileHtml,
  FileCss,
  FileText,
  DownloadSimple,
  ArrowSquareOut,
  Copy,
  Check,
} from '@phosphor-icons/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface ArtifactModalData {
  filename: string;
  content?: string;
  fileType?: string;
  downloadUrl?: string;
}

export interface FilePreviewProps {
  /** File name */
  filename: string;
  /** File size in bytes */
  size?: number;
  /** File type/extension */
  type?: string;
  /** MIME type */
  mimeType?: string;
  /** Download URL */
  downloadUrl?: string;
  /** Preview URL (for supported types) */
  previewUrl?: string;
  /** Inline text content for expandable preview */
  content?: string;
  /** Last modified date */
  modifiedAt?: string;
  /** Compact display */
  compact?: boolean;
  /** Show download button */
  showDownload?: boolean;
  /** Callback when clicked */
  onClick?: () => void;
  /** Callback to open artifact modal overlay */
  onOpenModal?: (data: ArtifactModalData) => void;
  /** Additional CSS classes */
  className?: string;
  // Enriched artifact fields (output engine)
  /** Display title for the artifact */
  title?: string;
  /** Description of the artifact */
  description?: string;
  /** Artifact type (pdf_report, presentation, etc.) */
  artifactType?: string;
  /** Persistent artifact URI */
  artifactUri?: string;
  /** Tags for categorization */
  tags?: string[];
  /** Page/slide count */
  pageCount?: number;
}

// File extension to icon mapping
const getFileIcon = (filename: string, mimeType?: string) => {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const mime = mimeType?.toLowerCase() || '';

  // Images
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(ext) || mime.startsWith('image/')) {
    return { icon: FileImage, color: 'bg-green-500/10 text-green-500' };
  }

  // Videos
  if (['mp4', 'mov', 'avi', 'mkv', 'webm'].includes(ext) || mime.startsWith('video/')) {
    return { icon: FileVideo, color: 'bg-purple-500/10 text-purple-500' };
  }

  // Audio
  if (['mp3', 'wav', 'ogg', 'flac', 'm4a'].includes(ext) || mime.startsWith('audio/')) {
    return { icon: FileAudio, color: 'bg-pink-500/10 text-pink-500' };
  }

  // Documents
  if (['pdf'].includes(ext) || mime === 'application/pdf') {
    return { icon: FilePdf, color: 'bg-red-500/10 text-red-500' };
  }
  if (['doc', 'docx'].includes(ext) || mime.includes('word')) {
    return { icon: FileDoc, color: 'bg-blue-500/10 text-blue-500' };
  }
  if (['xls', 'xlsx'].includes(ext) || mime.includes('excel') || mime.includes('spreadsheet')) {
    return { icon: FileXls, color: 'bg-green-600/10 text-green-600' };
  }
  if (['ppt', 'pptx'].includes(ext) || mime.includes('powerpoint') || mime.includes('presentation')) {
    return { icon: FilePpt, color: 'bg-orange-500/10 text-orange-500' };
  }
  if (['csv'].includes(ext) || mime === 'text/csv') {
    return { icon: FileCsv, color: 'bg-emerald-500/10 text-emerald-500' };
  }

  // Archives
  if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2'].includes(ext)) {
    return { icon: FileZip, color: 'bg-amber-500/10 text-amber-500' };
  }

  // Code files
  if (['js', 'jsx', 'ts', 'tsx', 'mjs'].includes(ext)) {
    return { icon: FileJs, color: 'bg-yellow-500/10 text-yellow-500' };
  }
  if (['py', 'pyw'].includes(ext)) {
    return { icon: FilePy, color: 'bg-blue-400/10 text-blue-400' };
  }
  if (['html', 'htm'].includes(ext)) {
    return { icon: FileHtml, color: 'bg-orange-400/10 text-orange-400' };
  }
  if (['css', 'scss', 'sass', 'less'].includes(ext)) {
    return { icon: FileCss, color: 'bg-blue-500/10 text-blue-500' };
  }
  if (['json', 'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg'].includes(ext)) {
    return { icon: FileCode, color: 'bg-gray-500/10 text-gray-500' };
  }
  if (['md', 'mdx', 'txt', 'rtf'].includes(ext) || mime.startsWith('text/')) {
    return { icon: FileText, color: 'bg-gray-500/10 text-gray-500' };
  }

  // Default
  return { icon: File, color: 'bg-muted text-muted-foreground' };
};

// Format file size
const formatFileSize = (bytes?: number): string => {
  if (bytes === undefined || bytes === null) return '';
  if (bytes === 0) return '0 B';

  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const k = 1024;
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${units[i]}`;
};

function isMarkdown(filename: string): boolean {
  return filename.endsWith('.md') || filename.endsWith('.mdx');
}

/**
 * FilePreview - Display file information with optional inline expandable content
 *
 * Features:
 * - File type icons
 * - Size display
 * - Download option
 * - Compact mode
 * - Inline expandable content (like DeepWorkCard) when `content` is provided
 */
export const FilePreview = memo(function FilePreview({
  filename,
  size,
  type,
  mimeType,
  downloadUrl,
  previewUrl,
  content,
  modifiedAt,
  compact = false,
  showDownload = true,
  onClick,
  onOpenModal,
  className,
  title,
  description,
  artifactType,
  artifactUri,
  tags,
  pageCount,
}: FilePreviewProps) {
  const isEnrichedArtifact = !!artifactType;
  const { icon: FileIcon, color } = getFileIcon(filename, mimeType);
  const displayType = type || filename.split('.').pop()?.toUpperCase() || 'FILE';
  const [copied, setCopied] = useState(false);

  const handleCardClick = useCallback(() => {
    if (onOpenModal && content) {
      onOpenModal({ filename, content, fileType: type || mimeType, downloadUrl });
    } else if (onClick) {
      onClick();
    }
  }, [onOpenModal, onClick, filename, content, type, mimeType, downloadUrl]);

  const handleDownload = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (content) {
      // Download from inline content as blob
      const blob = new Blob([content], { type: mimeType || 'text/plain' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } else if (downloadUrl) {
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = filename;
      link.click();
    }
  }, [content, downloadUrl, filename, mimeType]);

  const handleCopy = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may not be available
    }
  }, [content]);

  if (compact) {
    return (
      <div
        className={cn(
          'inline-flex items-center gap-2 px-2 py-1.5 rounded-md border border-border',
          'text-sm',
          onClick && 'cursor-pointer hover:bg-muted/50',
          transitions.colors,
          className
        )}
        onClick={onClick}
      >
        <div className={cn('flex items-center justify-center w-6 h-6 rounded', color)}>
          <FileIcon className="h-3.5 w-3.5" weight="fill" />
        </div>
        <span className="truncate max-w-[200px]">{filename}</span>
        {size !== undefined && (
          <span className="text-xs text-muted-foreground shrink-0">
            {formatFileSize(size)}
          </span>
        )}
        {showDownload && (downloadUrl || content) && (
          <button
            onClick={handleDownload}
            className={cn(
              'p-0.5 rounded text-muted-foreground hover:text-foreground',
              animations.press,
              transitions.colors
            )}
          >
            <DownloadSimple className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    );
  }

  return (
    <FadeSlide>
      <div
        className={cn(
          'rounded-xl border border-border overflow-hidden',
          (content || onClick) && 'cursor-pointer hover:bg-muted/30',
          transitions.colors,
          className,
        )}
        onClick={handleCardClick}
      >
        {/* Header row: icon + info + actions */}
        <div className="flex items-center gap-3 px-4 py-3">
          {/* Icon */}
          <div
            className={cn(
              'flex items-center justify-center w-10 h-10 rounded-lg shrink-0',
              color
            )}
          >
            <FileIcon className="h-5 w-5" weight="fill" />
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <div className="font-medium text-sm truncate">{title || filename}</div>
            {isEnrichedArtifact && title && (
              <div className="text-xs text-muted-foreground truncate">{filename}</div>
            )}
            <div className="flex items-center gap-2 mt-0.5 text-xs text-muted-foreground">
              <span className="uppercase">{displayType}</span>
              {size !== undefined && (
                <>
                  <span className="text-muted-foreground/30">·</span>
                  <span>{formatFileSize(size)}</span>
                </>
              )}
              {pageCount !== undefined && pageCount > 0 && (
                <>
                  <span className="text-muted-foreground/30">·</span>
                  <span>{pageCount} {pageCount === 1 ? 'page' : 'pages'}</span>
                </>
              )}
              {modifiedAt && (
                <>
                  <span className="text-muted-foreground/30">·</span>
                  <span>{modifiedAt}</span>
                </>
              )}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-1 shrink-0">
            {content && (
              <button
                onClick={handleCopy}
                className={cn(
                  'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                  transitions.colors,
                )}
                title={copied ? 'Copied!' : 'Copy content'}
              >
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              </button>
            )}
            {previewUrl && (
              <a
                href={previewUrl}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className={cn(
                  'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                  transitions.colors
                )}
                title="Preview"
              >
                <ArrowSquareOut className="h-4 w-4" />
              </a>
            )}
            {showDownload && (downloadUrl || content) && (
              <button
                onClick={handleDownload}
                className={cn(
                  'p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted',
                  transitions.colors,
                )}
                title="Download"
              >
                <DownloadSimple className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* Enriched artifact description + tags */}
        {isEnrichedArtifact && (description || (tags && tags.length > 0)) && (
          <div className="px-4 pb-2 space-y-1.5">
            {description && (
              <p className="text-xs text-muted-foreground leading-relaxed">{description}</p>
            )}
            {tags && tags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {tags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-muted text-muted-foreground"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Dashboard iframe preview */}
        {artifactType === 'dashboard' && downloadUrl && (
          <div className="px-4 pb-3">
            <iframe
              src={downloadUrl}
              sandbox="allow-scripts"
              className="w-full h-[500px] rounded-lg border border-border"
              title={title || filename}
            />
          </div>
        )}

        {/* Content preview with gradient fade */}
        {content && (
          <div className="relative px-4 pb-3">
            <div className="relative max-h-[150px] overflow-hidden">
              <div className="prose prose-xs prose-neutral dark:prose-invert max-w-none">
                {isMarkdown(filename) ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.slice(0, 500)}</ReactMarkdown>
                ) : (
                  <pre className="text-xs font-mono whitespace-pre-wrap leading-relaxed text-neutral-600 dark:text-neutral-300">
                    {content.slice(0, 500)}
                  </pre>
                )}
              </div>
              {/* Gradient fade-out */}
              <div className="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-background to-transparent pointer-events-none" />
            </div>
          </div>
        )}
      </div>
    </FadeSlide>
  );
});

export type { FilePreviewProps as FilePreviewComponentProps };
