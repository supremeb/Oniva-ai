'use client';

import { memo, useState, useCallback, useEffect } from 'react';
import { File, Folder, FolderOpen, Download, Eye } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { listWorkspaceFiles, readWorkspaceFile, type WorkspaceFileInfo } from '@/lib/api/sandbox';

export interface WorkspaceFileBrowserProps {
  sessionId: string;
  onFileSelect?: (path: string, content: string) => void;
}

interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
  modified?: string;
  children: TreeNode[];
}

function buildTree(files: WorkspaceFileInfo[]): TreeNode[] {
  const root: TreeNode[] = [];

  for (const file of files) {
    const parts = file.name.split('/');
    let current = root;

    for (let i = 0; i < parts.length; i++) {
      const name = parts[i];
      const path = parts.slice(0, i + 1).join('/');
      const isLast = i === parts.length - 1;

      let existing = current.find(n => n.name === name);
      if (!existing) {
        existing = {
          name,
          path,
          type: isLast ? file.type : 'directory',
          size: isLast ? file.size : undefined,
          modified: isLast ? file.modified : undefined,
          children: [],
        };
        current.push(existing);
      }
      current = existing.children;
    }
  }

  return root;
}

function formatSize(bytes?: number): string {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function FileTreeNode({
  node,
  depth,
  expanded,
  selected,
  onToggle,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  selected: string | null;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  const isExpanded = expanded.has(node.path);
  const isSelected = selected === node.path;
  const isDir = node.type === 'directory' || node.children.length > 0;

  return (
    <div>
      <button
        onClick={() => isDir ? onToggle(node.path) : onSelect(node.path)}
        className={cn(
          'flex items-center gap-1.5 w-full text-left px-2 py-1 rounded text-[11px] hover:bg-accent',
          isSelected && 'bg-accent',
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {isDir ? (
          isExpanded ? (
            <FolderOpen className="h-3.5 w-3.5 text-amber-500 flex-shrink-0" weight="fill" />
          ) : (
            <Folder className="h-3.5 w-3.5 text-amber-500 flex-shrink-0" weight="fill" />
          )
        ) : (
          <File className="h-3.5 w-3.5 text-slate-400 flex-shrink-0" />
        )}
        <span className="truncate flex-1">{node.name}</span>
        {node.size != null && (
          <span className="text-[10px] text-muted-foreground flex-shrink-0">
            {formatSize(node.size)}
          </span>
        )}
      </button>
      {isDir && isExpanded && (
        <div>
          {node.children.map(child => (
            <FileTreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              selected={selected}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function WorkspaceFileBrowserComponent({ sessionId, onFileSelect }: WorkspaceFileBrowserProps) {
  const [files, setFiles] = useState<WorkspaceFileInfo[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    listWorkspaceFiles(sessionId)
      .then(res => setFiles(res.files))
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
  }, [sessionId]);

  const toggleFolder = useCallback((path: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });
  }, []);

  const selectFile = useCallback(async (path: string) => {
    setSelected(path);
    try {
      const res = await readWorkspaceFile(sessionId, path);
      setPreview(res.content);
      onFileSelect?.(path, res.content);
    } catch {
      setPreview(null);
    }
  }, [sessionId, onFileSelect]);

  const tree = buildTree(files);

  if (loading) {
    return (
      <div className="border rounded-lg p-4 text-sm text-muted-foreground">
        Loading workspace...
      </div>
    );
  }

  if (files.length === 0) {
    return (
      <div className="border rounded-lg p-4 text-sm text-muted-foreground">
        No files in workspace
      </div>
    );
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="bg-slate-50 px-3 py-2 border-b">
        <p className="text-[11px] font-medium text-slate-600">
          Workspace ({files.length} file{files.length !== 1 ? 's' : ''})
        </p>
      </div>
      <div className="max-h-64 overflow-y-auto py-1">
        {tree.map(node => (
          <FileTreeNode
            key={node.path}
            node={node}
            depth={0}
            expanded={expanded}
            selected={selected}
            onToggle={toggleFolder}
            onSelect={selectFile}
          />
        ))}
      </div>
      {preview !== null && selected && (
        <div className="border-t">
          <div className="bg-slate-50 px-3 py-1.5 flex items-center gap-1.5">
            <Eye className="h-3 w-3 text-muted-foreground" />
            <span className="text-[10px] text-muted-foreground truncate">{selected}</span>
          </div>
          <pre className="text-[11px] p-3 overflow-auto max-h-40 bg-white">
            <code>{preview}</code>
          </pre>
        </div>
      )}
    </div>
  );
}

export const WorkspaceFileBrowser = memo(WorkspaceFileBrowserComponent);
