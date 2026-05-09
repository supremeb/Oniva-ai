'use client';

/**
 * ChainOfThought — Unified collapsible chain-of-thought + response renderer.
 *
 * Groups thinking text + tool calls + info messages into a clean collapsible
 * section with a descriptive summary trigger (e.g., "Searched the web and ran code").
 * Closed by default — auto-expands during active streaming only.
 * Thinking segments are shown inline (visible) for full transparency on decisions.
 *
 * Three-phase render: initial response → chain (reasoning + tools) → final response.
 */

import { memo, useState, useEffect, useMemo, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import {
  Brain,
  CaretDown,
  CaretRight,
  MagnifyingGlass,
  Globe,
  Database,
  Code,
  File,
  Wrench,
  BookOpen,
  Plugs,
  Terminal,
  ArrowSquareOut,
} from '@phosphor-icons/react';
import { Avatar, AvatarImage, AvatarFallback } from '@/components/ui/avatar';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import type { MessagePart, SourceItem } from '@/lib/models';
import type { ToolCallMeta } from '@/hooks/useStreamingParts';
import { MessageRenderer, type MessageRendererProps } from './MessageRenderer';
import { StaggerContainer } from './primitives/StaggerContainer';
import { MemoryEditModal } from './interactive/MemoryEditModal';
import { ToolCallError } from './tools/ToolCallError';
import { ArtifactCreationSkeleton } from './ArtifactCreationSkeleton';
import { StreamingCode } from './streaming/StreamingCode';
import { SourcesPill } from './data/SourcesPill';
import { getFaviconUrl } from '@/lib/utils/favicon';

// ─── Tool icon mapping ──────────────────────────────────────────────

const toolIconMap: Record<string, typeof Wrench> = {
  knowledge: BookOpen,
  rag: BookOpen,
  document: BookOpen,
  memory: Brain,
  decision: Brain,
  recall: Brain,
  web: Globe,
  browse: Globe,
  fetch: Globe,
  http: Globe,
  api: Globe,
  search: MagnifyingGlass,
  find: MagnifyingGlass,
  database: Database,
  query: Database,
  sql: Database,
  run: Terminal,
  shell: Terminal,
  code: Code,
  execute: Code,
  python: Code,
  javascript: Code,
  file: File,
  read: File,
  write: File,
  connector: Plugs,
  default: Wrench,
};

function getToolIcon(toolName: string): React.ReactNode {
  const lower = toolName.toLowerCase();
  for (const [key, Icon] of Object.entries(toolIconMap)) {
    if (key !== 'default' && lower.includes(key)) {
      return <Icon className="h-3.5 w-3.5" weight="regular" />;
    }
  }
  const FallbackIcon = toolIconMap.default;
  return <FallbackIcon className="h-3.5 w-3.5" weight="regular" />;
}

// ─── Summary builder ────────────────────────────────────────────────

/** Maps tool name patterns to [present tense, past tense] action descriptions */
const TOOL_ACTIONS: [RegExp, string, string][] = [
  [/web_search|browse_url|fetch_url|browse|fetch/, 'searching the web', 'searched the web'],
  [/search_knowledge|search_documents|rag/, 'searching documents', 'searched documents'],
  [/run_code|execute_code|analyze_code/, 'running code', 'ran code'],
  [/run_shell/, 'running a command', 'ran a command'],
  [/read_file|write_file|read_volume/, 'reading files', 'read files'],
  [/^execute_.+_api$/, 'calling an API', 'called an API'],
  [/search_past_decisions|search_entities|recall_memory/, 'recalling memories', 'recalled memories'],
];

/** Builds a human-readable summary from chain parts (e.g., "Searched the web and ran code") */
function buildSummary(chainParts: MessagePart[], isActive: boolean): string {
  const toolNames = chainParts
    .filter((p) => p.type === 'tool_call')
    .map((p) => ((p as any).toolName || '').toLowerCase());

  const hasThinking = chainParts.some((p) => p.type === 'thinking');

  if (toolNames.length === 0 && hasThinking) {
    return isActive ? 'Thinking...' : 'Thought about this';
  }
  if (toolNames.length === 0) {
    return isActive ? 'Working...' : 'Processed your request';
  }

  const seen = new Set<number>();
  const actions: string[] = [];

  for (const name of toolNames) {
    for (let j = 0; j < TOOL_ACTIONS.length; j++) {
      if (seen.has(j)) continue;
      const [pattern, present, past] = TOOL_ACTIONS[j];
      if (pattern.test(name)) {
        seen.add(j);
        actions.push(isActive ? present : past);
        break;
      }
    }
  }

  if (actions.length === 0) {
    return isActive ? 'Using tools...' : 'Used tools';
  }

  const capitalized = actions.map((a, i) =>
    i === 0 ? a.charAt(0).toUpperCase() + a.slice(1) : a,
  );

  if (capitalized.length === 1) return capitalized[0];
  if (capitalized.length === 2) return `${capitalized[0]} and ${capitalized[1]}`;
  return capitalized.slice(0, -1).join(', ') + ' and ' + capitalized[capitalized.length - 1];
}

// ─── Part classification ─────────────────────────────────────────────

const HIDDEN_TOOLS = new Set(['list_workspace', 'list_files']);
const CODE_TOOLS = new Set(['run_code', 'execute_code', 'analyze_code']);

function isCodeTool(toolName: string): boolean {
  if (CODE_TOOLS.has(toolName)) return true;
  return /^execute_.+_api$/.test(toolName);
}

function isChainPart(part: MessagePart): boolean {
  if (part.type === 'tool_call') {
    const toolName = ((part as any).toolName || '').toLowerCase();
    if (HIDDEN_TOOLS.has(toolName)) return false;
  }
  return part.type === 'thinking' || part.type === 'tool_call' || part.type === 'info';
}

// ─── Source deduplication ────────────────────────────────────────────

function deduplicateSources(sources: SourceItem[]): SourceItem[] {
  const seen = new Set<string>();
  return sources.filter((s) => {
    if (seen.has(s.url)) return false;
    seen.add(s.url);
    return true;
  });
}

// ─── Tool type classifiers ───────────────────────────────────────────

const WEB_SEARCH_TOOLS = new Set(['web_search', 'search_web', 'browse_url', 'fetch_url']);
const KB_TOOLS = new Set(['search_knowledge', 'search_documents', 'rag_search']);

function isWebSearchTool(name: string): boolean {
  return WEB_SEARCH_TOOLS.has(name) || /web.*search|browse|fetch_url/.test(name);
}

function isKBTool(name: string): boolean {
  return KB_TOOLS.has(name) || /knowledge|rag|document.*search/.test(name);
}

const MEMORY_TOOLS = new Set(['search_past_decisions', 'search_entities', 'recall_memory', 'store_decision']);

function isMemoryTool(name: string): boolean {
  return MEMORY_TOOLS.has(name) || /memory|past_decisions|recall/.test(name);
}

// ─── URL pill for web search results ─────────────────────────────────

function getDomain(url: string): string {
  try { return new URL(url).hostname.replace('www.', ''); }
  catch { return url; }
}

// ─── Shared pill style tokens ─────────────────────────────────────────
const PILL_BASE = [
  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1',
  'bg-white dark:bg-neutral-900',
  'border border-neutral-200/80 dark:border-neutral-800',
  'text-xs text-neutral-600 dark:text-neutral-300',
  'transition-colors',
] as const;

const PILL_INTERACTIVE = 'cursor-pointer hover:bg-neutral-50 dark:hover:bg-neutral-800/80';

function UrlPill({ url, title, className, style }: { url: string; title?: string; className?: string; style?: React.CSSProperties }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(...PILL_BASE, PILL_INTERACTIVE, 'pl-1.5 max-w-[220px]', className)}
      style={style}
      title={title || url}
    >
      <Avatar className="h-4 w-4 shrink-0">
        <AvatarImage src={getFaviconUrl(url, 16)} alt="" />
        <AvatarFallback className="text-[7px] font-medium">
          {getDomain(url).charAt(0).toUpperCase()}
        </AvatarFallback>
      </Avatar>
      <span className="truncate">{getDomain(url)}</span>
    </a>
  );
}

// ─── Document pill for KB results ────────────────────────────────────

function DocPill({ name, className, style }: { name: string; className?: string; style?: React.CSSProperties }) {
  return (
    <span className={cn(...PILL_BASE, 'max-w-[220px]', className)} style={style}>
      <BookOpen className="h-3 w-3 shrink-0 text-neutral-400" weight="regular" />
      <span className="truncate">{name}</span>
    </span>
  );
}

// ─── Memory pill for recalled memories ───────────────────────────────

function MemoryPill({ snippet, similarity, entities, memoryId, onClick, className, style }: {
  snippet: string;
  similarity?: number;
  entities?: string[];
  memoryId?: string;
  onClick?: (memoryId: string) => void;
  className?: string;
  style?: React.CSSProperties;
}) {
  const truncated = snippet.length > 60 ? snippet.slice(0, 60) + '...' : snippet;
  const isClickable = !!(memoryId && onClick);
  return (
    <span
      className={cn(
        ...PILL_BASE,
        'max-w-[280px]',
        isClickable && PILL_INTERACTIVE,
        className,
      )}
      style={style}
      title={snippet}
      role={isClickable ? 'button' : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onClick={isClickable ? () => onClick(memoryId) : undefined}
      onKeyDown={isClickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(memoryId); } } : undefined}
    >
      <Brain className="h-3 w-3 shrink-0 text-neutral-400" weight="duotone" />
      <span className="truncate">{truncated}</span>
      {similarity != null && (
        <span className="shrink-0 text-[10px] font-medium text-neutral-400 dark:text-neutral-500">
          {Math.round(similarity * 100)}%
        </span>
      )}
      {entities && entities.length > 0 && (
        <>
          {entities.slice(0, 2).map((e) => (
            <span
              key={e}
              className="shrink-0 rounded-full bg-neutral-100 dark:bg-neutral-800 px-1.5 py-px text-[10px] text-neutral-500 dark:text-neutral-400"
            >
              {e}
            </span>
          ))}
        </>
      )}
    </span>
  );
}

// ─── Parse tool result (handles string JSON or object) ──────────────

function parseResultObject(result: unknown): Record<string, unknown> | null {
  if (!result) return null;
  if (typeof result === 'object') return result as Record<string, unknown>;
  if (typeof result === 'string') {
    try {
      const parsed = JSON.parse(result);
      if (typeof parsed === 'object' && parsed !== null) return parsed;
    } catch { /* not JSON */ }
  }
  return null;
}

// ─── Extract URLs from web search result ────────────────────────────

function extractSearchUrls(result: unknown): { url: string; title?: string }[] {
  const r = parseResultObject(result);
  if (!r) return [];
  const results = r.results as Array<Record<string, unknown>> | undefined;
  if (!Array.isArray(results)) return [];
  return results
    .filter((item) => typeof item.url === 'string')
    .map((item) => ({ url: item.url as string, title: item.title as string | undefined }));
}

// ─── Extract document names from KB result ──────────────────────────

function extractDocNames(result: unknown): string[] {
  const r = parseResultObject(result);
  if (!r) return [];
  // Various KB result formats
  const results = (r.results || r.documents || r.chunks) as Array<Record<string, unknown>> | undefined;
  if (!Array.isArray(results)) return [];
  const seen = new Set<string>();
  return results
    .map((item) => (item.document_name || item.source || item.title || item.name) as string | undefined)
    .filter((name): name is string => {
      if (!name || seen.has(name)) return false;
      seen.add(name);
      return true;
    });
}

// ─── Extract memory results from recall tool output ─────────────────

interface MemoryResult {
  id?: string;
  snippet: string;
  similarity?: number;
  entities?: string[];
  agentName?: string;
  actionType?: string;
}

function extractMemoryResults(result: unknown): MemoryResult[] {
  const r = parseResultObject(result);
  if (!r) return [];
  const results = r.results as Array<Record<string, unknown>> | undefined;
  if (!Array.isArray(results)) return [];
  const seen = new Set<string>();
  return results.slice(0, 5).map((item) => ({
    id: item.id as string | undefined,
    snippet: ((item.context_decision || item.context || item.decision || '') as string).slice(0, 120),
    similarity: item.similarity as number | undefined,
    entities: Array.isArray(item.entities) ? (item.entities as string[]) : [],
    agentName: item.agent_name as string | undefined,
    actionType: item.action_type as string | undefined,
  })).filter((m) => {
    if (m.snippet.length === 0) return false;
    if (m.id) {
      if (seen.has(m.id)) return false;
      seen.add(m.id);
    }
    return true;
  });
}

// ─── Tool children renderer ─────────────────────────────────────────

function renderToolChildren(
  part: MessagePart,
  meta?: ToolCallMeta,
  seenMemoryIds?: Set<string>,
  onMemoryClick?: (memoryId: string) => void,
): ReactNode {
  const children: ReactNode[] = [];
  const toolName = (part as any).toolName || '';
  const status = (part as any).status || 'pending';

  const args: Record<string, unknown> | undefined = meta?.args ?? (part as any).args;
  const result: unknown = meta?.result ?? (part as any).result;
  const error: string | undefined = meta?.error ?? (part as any).error;
  const producesFile: boolean | undefined = meta?.producesFile ?? (part as any).producesFile;

  // ── Web search: show favicon pills for each URL visited
  if (isWebSearchTool(toolName) && status === 'success' && result) {
    const urls = extractSearchUrls(result);
    if (urls.length > 0) {
      children.push(
        <StaggerContainer key="urls" className="flex flex-wrap gap-1.5" animation="fade-slide-right">
          {urls.map(({ url, title }) => (
            <UrlPill key={url} url={url} title={title} />
          ))}
        </StaggerContainer>,
      );
    }
    // Don't show raw JSON for search results — favicon pills are sufficient
  }
  // ── Knowledge base: show document name pills
  else if (isKBTool(toolName) && status === 'success' && result) {
    const docs = extractDocNames(result);
    if (docs.length > 0) {
      children.push(
        <StaggerContainer key="docs" className="flex flex-wrap gap-1.5" animation="fade-slide-right">
          {docs.map((name) => (
            <DocPill key={name} name={name} />
          ))}
        </StaggerContainer>,
      );
    }
  }
  // ── Memory recall: show memory pills with snippets + similarity
  else if (isMemoryTool(toolName) && status === 'success' && result) {
    let memories = extractMemoryResults(result);
    // Cross-call dedup: skip memories already shown by a previous tool call
    if (seenMemoryIds) {
      memories = memories.filter((mem) => {
        if (mem.id && seenMemoryIds.has(mem.id)) return false;
        if (mem.id) seenMemoryIds.add(mem.id);
        return true;
      });
    }
    if (memories.length > 0) {
      children.push(
        <StaggerContainer key="memories" className="flex flex-wrap gap-1.5" animation="fade-slide-right">
          {memories.map((mem, i) => (
            <MemoryPill
              key={mem.id || i}
              snippet={mem.snippet}
              similarity={mem.similarity}
              entities={mem.entities}
              memoryId={mem.id}
              onClick={onMemoryClick}
            />
          ))}
        </StaggerContainer>,
      );
    }
  }
  // ── Code execution: show code input + output
  else if (isCodeTool(toolName)) {
    if (args?.code) {
      children.push(
        <StreamingCode
          key="code-input"
          content={String(args.code)}
          language="python"
          collapsible
          defaultCollapsed={status !== 'running'}
        />,
      );
    }
    // Show stdout/output for code tools
    if (status === 'success' && result != null) {
      const r = typeof result === 'object' && result !== null ? result as Record<string, unknown> : null;
      const stdout = r ? (r.stdout || r.output) : (typeof result === 'string' ? result : null);
      if (stdout && typeof stdout === 'string' && stdout.trim()) {
        children.push(
          <StreamingCode
            key="code-output"
            content={String(stdout).trim()}
            language="text"
            collapsible
            defaultCollapsed={String(stdout).length > 300}
          />,
        );
      }
    }
  }
  // ── Shell commands: show command + output
  else if (toolName === 'run_shell') {
    if (args?.command) {
      children.push(
        <StreamingCode
          key="shell-input"
          content={String(args.command)}
          language="bash"
          collapsible={false}
        />,
      );
    }
    if (status === 'success' && result != null) {
      const r = typeof result === 'object' && result !== null ? result as Record<string, unknown> : null;
      const stdout = r ? (r.stdout || r.output) : (typeof result === 'string' ? result : null);
      if (stdout && typeof stdout === 'string' && stdout.trim()) {
        children.push(
          <StreamingCode
            key="shell-output"
            content={String(stdout).trim()}
            language="text"
            collapsible
            defaultCollapsed={String(stdout).length > 300}
          />,
        );
      }
    }
  }
  // ── File creation: show skeleton while running
  else if (producesFile && status === 'running') {
    const filename = args?.path || args?.filename;
    children.push(
      <ArtifactCreationSkeleton
        key="skeleton"
        filename={filename ? String(filename) : undefined}
        toolName={toolName}
      />,
    );
  }
  // ── Other tools: show collapsible result (stdout/output or JSON)
  else if (status === 'success' && result != null && !producesFile) {
    const r = typeof result === 'object' && result !== null ? result as Record<string, unknown> : null;
    const stdout = r ? (r.stdout || r.output) : null;
    if (stdout && typeof stdout === 'string' && stdout.trim()) {
      children.push(
        <StreamingCode key="result" content={String(stdout).trim()} language="text" collapsible defaultCollapsed={String(stdout).length > 200} />,
      );
    } else {
      // Show structured result as collapsible JSON (was hidden before)
      const jsonStr = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
      if (jsonStr && jsonStr.trim() && jsonStr.length < 50_000) {
        children.push(
          <StreamingCode key="result" content={jsonStr} language="json" collapsible defaultCollapsed />,
        );
      }
    }
  }

  // ── Errors always shown
  if (status === 'error' && error) {
    children.push(<ToolCallError key="error" message={error} />);
  }

  return children.length > 0 ? <div className="space-y-2">{children}</div> : null;
}

// ─── ChainOfThought ─────────────────────────────────────────────────

export interface ChainOfThoughtProps {
  parts: MessagePart[];
  compact?: boolean;
  animated?: boolean;
  handlers?: MessageRendererProps['handlers'];
  toolCallMeta?: Map<string, ToolCallMeta>;
  sources?: SourceItem[];
  className?: string;
}

export const ChainOfThought = memo(function ChainOfThought({
  parts,
  compact = false,
  animated = true,
  handlers,
  toolCallMeta,
  sources,
  className,
}: ChainOfThoughtProps) {
  const [editMemoryId, setEditMemoryId] = useState<string | null>(null);

  // ── Three-phase split: pre-response → chain → post-response ──
  // Parts arrive in temporal order from useStreamingParts:
  //   [preToolText?, ...chainParts, ...infoParts, postToolText?, ...sources, ...files]
  // This split ensures: initial text → reasoning/tools → final text
  const { preResponseParts, chainParts, postResponseParts } = useMemo(() => {
    const pre: MessagePart[] = [];
    const chain: MessagePart[] = [];
    const post: MessagePart[] = [];
    let phase: 'pre' | 'chain' | 'post' = 'pre';

    for (const part of parts) {
      if (part.type === 'tool_call') {
        const toolName = ((part as any).toolName || '').toLowerCase();
        if (HIDDEN_TOOLS.has(toolName)) continue;
      }

      if (phase === 'pre') {
        if (isChainPart(part)) {
          phase = 'chain';
          chain.push(part);
        } else {
          pre.push(part);
        }
      } else if (phase === 'chain') {
        if (isChainPart(part)) {
          chain.push(part);
        } else {
          phase = 'post';
          post.push(part);
        }
      } else {
        post.push(part);
      }
    }

    return { preResponseParts: pre, chainParts: chain, postResponseParts: post };
  }, [parts]);

  // ── Derive sources: prefer prop, fallback to extracting from richParts
  const effectiveSources = useMemo(() => {
    if (sources && sources.length > 0) return sources;
    return parts
      .filter((p) => p.type === 'source')
      .map((p) => ({
        title: (p as any).title || '',
        url: (p as any).url || '',
        snippet: (p as any).description || (p as any).snippet || '',
        sourceType: (p as any).sourceType || undefined,
      })) as SourceItem[];
  }, [sources, parts]);

  // ── Active streaming state
  const isThinking = chainParts.some(
    (p) =>
      (p.type === 'thinking' && (p as any).isThinking) ||
      (p.type === 'tool_call' && (p as any).status === 'running'),
  );

  // ── Closed by default — auto-expand only during active streaming
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    if (isThinking) {
      setIsOpen(true);
    }
  }, [isThinking]);

  // ── Descriptive trigger label
  const triggerLabel = buildSummary(chainParts, isThinking);

  if (chainParts.length === 0 && preResponseParts.length === 0 && postResponseParts.length === 0) return null;

  return (
    <div className={cn('space-y-3', className)}>
      {/* ── 1. Initial response text (before tools started) */}
      {preResponseParts.length > 0 && (
        <MessageRenderer
          parts={preResponseParts}
          compact={compact}
          animated={animated}
          handlers={handlers}
        />
      )}

      {/* ── 2. Activity feed (reasoning + tool calls) — between initial and final response */}
      {chainParts.length > 0 && (
        <Collapsible open={isOpen} onOpenChange={setIsOpen}>
          <CollapsibleTrigger
            className={cn(
              'flex items-center gap-2 text-sm w-full transition-colors',
              'text-neutral-500 hover:text-neutral-700',
              'dark:text-neutral-400 dark:hover:text-neutral-200',
            )}
          >
            <Brain
              className={cn('h-4 w-4 shrink-0', isThinking && 'animate-pulse')}
              weight={isThinking ? 'fill' : 'duotone'}
            />
            <span className={cn('flex-1 text-left font-medium', isThinking && 'text-shimmer')}>{triggerLabel}</span>
            {isOpen ? (
              <CaretDown className="h-3 w-3 shrink-0" />
            ) : (
              <CaretRight className="h-3 w-3 shrink-0" />
            )}
          </CollapsibleTrigger>

          <CollapsibleContent>
            <div className="mt-2">
              {(() => {
                // Pre-filter to visible items
                const visible = chainParts.filter((p) => {
                  if (p.type === 'thinking') {
                    return ((p as any).thinkingContent || '').trim().length > 0;
                  }
                  return true;
                });

                // Group thinking segments with their following tool call.
                // Each group: { reasoning?: string, isThinking?: boolean, item: MessagePart }
                // Reasoning text appears under each tool step for transparency.
                interface ChainGroup {
                  reasoning?: string;
                  isThinking?: boolean;
                  item: MessagePart;
                }

                const groups: ChainGroup[] = [];
                for (let i = 0; i < visible.length; i++) {
                  const part = visible[i];
                  if (part.type === 'thinking') {
                    const next = visible[i + 1];
                    if (next && (next.type === 'tool_call' || next.type === 'info')) {
                      // Merge thinking into the next tool/info step
                      groups.push({
                        reasoning: (part as any).thinkingContent || '',
                        isThinking: (part as any).isThinking,
                        item: next,
                      });
                      i++; // skip the tool_call, already consumed
                    } else {
                      // Standalone thinking (no following tool — e.g., trailing reasoning)
                      groups.push({ item: part });
                    }
                  } else {
                    groups.push({ item: part });
                  }
                }

                // Cross-call dedup: shared set tracks memory IDs already rendered
                const seenMemoryIds = new Set<string>();

                return groups.map((group, gi) => {
                  const isLast = gi === groups.length - 1;
                  const part = group.item;

                  if (part.type === 'tool_call') {
                    const tcId = (part as any).id || `tc-${gi}`;
                    const meta = toolCallMeta?.get(tcId);
                    const toolName = (part as any).toolName || 'tool';
                    const status = (part as any).status || 'running';
                    const durationMs = meta?.durationMs ?? (part as any).durationMs;
                    const toolChildren = renderToolChildren(part, meta, seenMemoryIds, setEditMemoryId);

                    return (
                      <div key={tcId} className="flex gap-2.5">
                        <div className="flex flex-col items-center pt-0.5">
                          <span
                            className={cn(
                              'shrink-0 text-neutral-400 dark:text-neutral-500',
                              status === 'running' && 'animate-pulse text-neutral-600 dark:text-neutral-300',
                              status === 'error' && 'text-red-400 dark:text-red-500',
                            )}
                          >
                            {getToolIcon(toolName)}
                          </span>
                          {!isLast && (
                            <div className="w-px flex-1 bg-neutral-200 dark:bg-neutral-700/60 my-1" />
                          )}
                        </div>
                        <div className={cn('flex-1 min-w-0', !isLast && 'pb-2')}>
                          <div className="flex items-center gap-2">
                            <span
                              className={cn(
                                'text-sm flex-1 truncate',
                                'text-neutral-600 dark:text-neutral-300',
                                status === 'error' && 'text-red-500 dark:text-red-400',
                              )}
                            >
                              {meta?.label || (part as any).label ||
                                toolName
                                  .replace(/_/g, ' ')
                                  .replace(/\b\w/g, (c: string) => c.toUpperCase())}
                            </span>
                            {durationMs != null && (
                              <span className="text-xs text-neutral-400 dark:text-neutral-500 tabular-nums shrink-0">
                                {(durationMs / 1000).toFixed(1)}s
                              </span>
                            )}
                            {status === 'running' && (
                              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse shrink-0" />
                            )}
                          </div>
                          {/* Reasoning / narration for this step — visible for transparency */}
                          {(() => {
                            // Prefer thinking-segment reasoning; fall back to narration (meta.description)
                            const reasoningText = group.reasoning || meta?.description || (part as any).description || '';
                            if (!reasoningText) return null;
                            return (
                              <div className="mt-1 text-xs text-neutral-500 dark:text-neutral-400 whitespace-pre-wrap leading-relaxed line-clamp-3">
                                {reasoningText}
                                {group.isThinking && (
                                  <span className="inline-block w-1 h-2.5 bg-neutral-400 dark:bg-neutral-500 ml-0.5 animate-pulse rounded-sm" />
                                )}
                              </div>
                            );
                          })()}
                          {toolChildren && <div className="mt-1.5">{toolChildren}</div>}
                        </div>
                      </div>
                    );
                  }

                  if (part.type === 'thinking') {
                    // Standalone thinking (not followed by a tool call)
                    const content = (part as any).thinkingContent || '';
                    const isPartThinking = (part as any).isThinking;

                    return (
                      <div key={`thinking-${gi}`} className="flex gap-2.5">
                        <div className="flex flex-col items-center pt-0.5">
                          <span
                            className={cn(
                              'shrink-0 text-neutral-400 dark:text-neutral-500',
                              isPartThinking && 'animate-pulse',
                            )}
                          >
                            <Brain className="h-3.5 w-3.5" weight="duotone" />
                          </span>
                          {!isLast && (
                            <div className="w-px flex-1 bg-neutral-200 dark:bg-neutral-700/60 my-1" />
                          )}
                        </div>
                        <div className={cn('flex-1 min-w-0', !isLast && 'pb-2')}>
                          <div className="text-xs text-neutral-500 dark:text-neutral-400 whitespace-pre-wrap leading-relaxed">
                            {content}
                            {isPartThinking && (
                              <span className="inline-block w-1 h-2.5 bg-neutral-400 dark:bg-neutral-500 ml-0.5 animate-pulse rounded-sm" />
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  }

                  if (part.type === 'info') {
                    return (
                      <div key={`info-${gi}`} className="flex gap-2.5">
                        <div className="flex flex-col items-center pt-0.5">
                          <span className="shrink-0 text-neutral-400 dark:text-neutral-500">
                            <Brain className="h-3.5 w-3.5" weight="duotone" />
                          </span>
                          {!isLast && (
                            <div className="w-px flex-1 bg-neutral-200 dark:bg-neutral-700/60 my-1" />
                          )}
                        </div>
                        <div className={cn('flex-1 min-w-0', !isLast && 'pb-2')}>
                          <span className="text-sm text-neutral-600 dark:text-neutral-300">
                            {(part as any).label || 'Info'}
                          </span>
                          {/* Reasoning for this info step */}
                          {group.reasoning && (
                            <div className="mt-1 text-xs text-neutral-500 dark:text-neutral-400 whitespace-pre-wrap leading-relaxed line-clamp-3">
                              {group.reasoning}
                            </div>
                          )}
                          {(part as any).content && (
                            <div className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
                              {(part as any).content}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  }

                  return null;
                });
              })()}
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}

      {/* ── 3. Final response text (after tools completed) */}
      {postResponseParts.length > 0 && (
        <MessageRenderer
          parts={postResponseParts}
          compact={compact}
          animated={animated}
          handlers={handlers}
        />
      )}

      {/* ── Aggregated sources pill — only shown after streaming completes */}
      {!animated && effectiveSources.length > 0 && (
        <div className="mt-2">
          <SourcesPill sources={deduplicateSources(effectiveSources)} />
        </div>
      )}

      {/* ── Memory edit modal (opened by clicking a MemoryPill) */}
      {editMemoryId && (
        <MemoryEditModal
          memoryId={editMemoryId}
          open={!!editMemoryId}
          onOpenChange={(open) => { if (!open) setEditMemoryId(null); }}
        />
      )}
    </div>
  );
});
