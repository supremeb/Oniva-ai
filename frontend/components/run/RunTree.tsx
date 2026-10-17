'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import type { RunNode } from '@/lib/run/tree';
import { isActive } from '@/lib/run/status';
import { RunNodeRow } from './RunNodeRow';

const PAGE = 20;
/** Nodes at this depth or deeper start collapsed. */
const DEFAULT_OPEN_DEPTH = 2;

export interface RunTreeProps {
  nodes: readonly RunNode[];
  /** Epoch ms used for elapsed time of unfinished nodes. Defaults to a ticking clock while anything is active. */
  now?: number;
  className?: string;
  'aria-label'?: string;
}

interface Visible {
  node: RunNode;
  parentId: string | null;
}

export function RunTree({ nodes, now, className, 'aria-label': ariaLabel = 'Run tree' }: RunTreeProps) {
  // id -> explicit user choice; absent means "default by depth".
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [shown, setShown] = useState<Record<string, number>>({});
  const [focusId, setFocusId] = useState<string | null>(null);
  const rootRef = useRef<HTMLUListElement>(null);

  const anyActive = useMemo(() => {
    const walk = (list: readonly RunNode[]): boolean => list.some((n) => isActive(n.status) || walk(n.children));
    return now === undefined && walk(nodes);
  }, [nodes, now]);
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    if (!anyActive) return;
    const t = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, [anyActive]);
  const clock = now ?? tick;

  const isOpen = useCallback((id: string, depth: number) => open[id] ?? depth < DEFAULT_OPEN_DEPTH, [open]);

  const visible = useMemo(() => {
    const out: Visible[] = [];
    const walk = (list: readonly RunNode[], depth: number, parentId: string | null) => {
      const limit = parentId === null ? list.length : (shown[parentId] ?? PAGE);
      for (const n of list.slice(0, limit)) {
        out.push({ node: n, parentId });
        if (n.children.length && isOpen(n.id, depth)) walk(n.children, depth + 1, n.id);
      }
    };
    walk(nodes, 0, null);
    return out;
  }, [nodes, shown, isOpen]);

  const tabbableId = visible.some((v) => v.node.id === focusId) ? focusId : (visible[0]?.node.id ?? null);

  const focusNode = useCallback((id: string) => {
    setFocusId(id);
    const el = rootRef.current?.querySelector<HTMLElement>(`[data-run-id="${CSS.escape(id)}"]`);
    el?.focus();
  }, []);

  const toggle = useCallback(
    (id: string, depth?: number) => {
      setOpen((o) => {
        const current = o[id] ?? (depth ?? 0) < DEFAULT_OPEN_DEPTH;
        return { ...o, [id]: !current };
      });
    },
    [],
  );

  const depthOf = useMemo(() => {
    const m = new Map<string, number>();
    const walk = (list: readonly RunNode[], d: number) => list.forEach((n) => (m.set(n.id, d), walk(n.children, d + 1)));
    walk(nodes, 0);
    return m;
  }, [nodes]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>, node: RunNode) => {
      const i = visible.findIndex((v) => v.node.id === node.id);
      if (i < 0) return;
      const depth = depthOf.get(node.id) ?? 0;
      const expanded = node.children.length > 0 && isOpen(node.id, depth);
      let handled = true;
      switch (e.key) {
        case 'ArrowDown':
          if (i + 1 < visible.length) focusNode(visible[i + 1].node.id);
          break;
        case 'ArrowUp':
          if (i > 0) focusNode(visible[i - 1].node.id);
          break;
        case 'Home':
          focusNode(visible[0].node.id);
          break;
        case 'End':
          focusNode(visible[visible.length - 1].node.id);
          break;
        case 'ArrowRight':
          if (node.children.length === 0) break;
          if (!expanded) toggle(node.id, depth);
          else focusNode(node.children[0].id);
          break;
        case 'ArrowLeft':
          if (expanded) toggle(node.id, depth);
          else if (visible[i].parentId) focusNode(visible[i].parentId as string);
          break;
        case 'Enter':
        case ' ':
          if (node.children.length) toggle(node.id, depth);
          break;
        default:
          handled = false;
      }
      if (handled) e.preventDefault();
    },
    [visible, depthOf, isOpen, focusNode, toggle],
  );

  const renderList = (list: readonly RunNode[], depth: number, parentId: string | null) => {
    const limit = parentId === null ? list.length : (shown[parentId] ?? PAGE);
    const slice = list.slice(0, limit);
    return (
      <>
        {slice.map((n) => {
          const expanded = n.children.length > 0 && isOpen(n.id, depth);
          return (
            <RunNodeRow
              key={n.id}
              node={n}
              depth={depth}
              now={clock}
              expanded={expanded}
              tabbable={n.id === tabbableId}
              childCount={n.children.length}
              onToggle={(id) => toggle(id, depth)}
              onKeyDown={onKeyDown}
            >
              {expanded && <ul role="group">{renderList(n.children, depth + 1, n.id)}</ul>}
            </RunNodeRow>
          );
        })}
        {list.length > slice.length && parentId !== null && (
          <li role="none" className="list-none" style={{ paddingLeft: 8 + depth * 16 + 20 }}>
            <button
              type="button"
              onClick={() => setShown((s) => ({ ...s, [parentId]: (s[parentId] ?? PAGE) + PAGE }))}
              className="py-1 text-xs text-blue-600 hover:underline"
            >
              Show more ({list.length - slice.length} more)
            </button>
          </li>
        )}
      </>
    );
  };

  return (
    <ul ref={rootRef} role="tree" aria-label={ariaLabel} className={className}>
      {renderList(nodes, 0, null)}
    </ul>
  );
}
