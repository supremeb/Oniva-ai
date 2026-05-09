'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  File,
  FileCode,
  FileDoc,
  FileImage,
  FilePdf,
  Folder,
  FolderOpen,
  CaretRight,
  CaretDown,
} from '@phosphor-icons/react';

export interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
  children?: FileNode[];
}

interface FileTreeProps {
  files: FileNode[];
  onFileSelect: (path: string) => void;
  className?: string;
}

function getFileIcon(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'py':
    case 'ts':
    case 'tsx':
    case 'js':
    case 'jsx':
    case 'json':
    case 'yaml':
    case 'yml':
      return FileCode;
    case 'md':
    case 'txt':
    case 'csv':
      return FileDoc;
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
    case 'svg':
    case 'webp':
      return FileImage;
    case 'pdf':
      return FilePdf;
    default:
      return File;
  }
}

const FileTreeNode = memo(function FileTreeNode({
  node,
  depth,
  onFileSelect,
}: {
  node: FileNode;
  depth: number;
  onFileSelect: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 1);

  const handleClick = useCallback(() => {
    if (node.type === 'directory') {
      setExpanded((prev) => !prev);
    } else {
      onFileSelect(node.path);
    }
  }, [node, onFileSelect]);

  const Icon =
    node.type === 'directory'
      ? expanded
        ? FolderOpen
        : Folder
      : getFileIcon(node.name);

  const Caret =
    node.type === 'directory' ? (expanded ? CaretDown : CaretRight) : null;

  return (
    <div>
      <button
        onClick={handleClick}
        className={cn(
          'flex items-center gap-1.5 w-full px-2 py-1 text-left text-sm rounded-md',
          'hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors',
          node.type === 'file' && 'text-neutral-700 dark:text-neutral-300',
          node.type === 'directory' && 'text-neutral-900 dark:text-neutral-100 font-medium',
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {Caret && <Caret className="h-3 w-3 text-neutral-400 shrink-0" />}
        {!Caret && <span className="w-3" />}
        <Icon
          className={cn(
            'h-4 w-4 shrink-0',
            node.type === 'directory' ? 'text-amber-500' : 'text-neutral-500',
          )}
          weight={node.type === 'directory' ? 'fill' : 'regular'}
        />
        <span className="truncate">{node.name}</span>
        {node.type === 'file' && node.size != null && (
          <span className="ml-auto text-xs text-neutral-400 shrink-0">
            {formatFileSize(node.size)}
          </span>
        )}
      </button>

      {node.type === 'directory' && expanded && node.children && (
        <div>
          {node.children.map((child) => (
            <FileTreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              onFileSelect={onFileSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
});

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

export const FileTree = memo(function FileTree({
  files,
  onFileSelect,
  className,
}: FileTreeProps) {
  if (!files.length) {
    return (
      <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
        <Folder className="h-10 w-10 text-neutral-300 mb-3" weight="thin" />
        <p className="text-sm text-neutral-500">No files yet</p>
        <p className="text-xs text-neutral-400 mt-1">
          Files created during conversation will appear here
        </p>
      </div>
    );
  }

  return (
    <div className={cn('py-1', className)}>
      {files.map((node) => (
        <FileTreeNode
          key={node.path}
          node={node}
          depth={0}
          onFileSelect={onFileSelect}
        />
      ))}
    </div>
  );
});
