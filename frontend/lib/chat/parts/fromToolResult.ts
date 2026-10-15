/**
 * File and source parts derived from a tool's result, ported from the legacy
 * backend heuristics in backend/app/api/v1/chat.py (`_extract_file_info`, the
 * web_search / memory / knowledge-base source emitters). The engine path has no
 * server-side emitter, so the adapter runs these on the tool results it holds.
 */
import type { MessagePart } from '@/lib/models';
import { buildFilePart } from './file';
import { buildSourcePart } from './source';

type Dict = Record<string, unknown>;
const isDict = (v: unknown): v is Dict => typeof v === 'object' && v !== null && !Array.isArray(v);

const MIME_MAP: Record<string, string> = {
  md: 'text/markdown', txt: 'text/plain', pdf: 'application/pdf',
  csv: 'text/csv', json: 'application/json', html: 'text/html',
  py: 'text/x-python', png: 'image/png', jpg: 'image/jpeg',
};

const FILE_PRODUCING_TOOLS = new Set([
  'save_analysis_report', 'save_research_report', 'save_compliance_report',
  'write_file', 'save_artifact', 'generate_chart', 'run_code', 'run_shell',
]);
const EXECUTE_API_RE = /^execute_(.+)_api$/;
const FILE_RESULT_KEYS = ['artifact_uri', 'file_path', 'path', 'filename'];

const basename = (p: string) => p.split('/').pop() ?? p;

function mimeFor(filename: string): string | undefined {
  const dot = filename.lastIndexOf('.');
  const ext = dot > 0 ? filename.slice(dot + 1).toLowerCase() : '';
  return ext ? (MIME_MAP[ext] ?? `application/${ext}`) : undefined;
}

/** Parse a tool result's text content: a JSON object when it is one, else the raw string. */
export function parseToolResultContent(text: string): unknown {
  const t = text.trim();
  if (t.startsWith('{') || t.startsWith('[')) {
    try {
      return JSON.parse(t);
    } catch {
      /* fall through to raw text */
    }
  }
  return text;
}

function outputFileName(of: unknown): string | undefined {
  if (typeof of === 'string') return basename(of);
  if (isDict(of)) {
    return (of.filename as string | undefined) || (typeof of.path === 'string' ? basename(of.path) : undefined) || undefined;
  }
  return undefined;
}

function isFileProducingTool(toolName: string): boolean {
  return FILE_PRODUCING_TOOLS.has(toolName) || EXECUTE_API_RE.test(toolName);
}

/**
 * File parts for a successful-looking tool result. `content` is never set (the
 * legacy stash of the written text is not carried over; see the parity table).
 * Duplicates by filename are dropped (legacy emitted the first output file twice).
 */
export function deriveFileParts(toolName: string, toolCallId: string, result: unknown): MessagePart[] {
  if (!isDict(result)) return [];
  const parts: MessagePart[] = [];
  const seen = new Set<string>();
  const push = (p: Parameters<typeof buildFilePart>[0]) => {
    if (!p.filename || seen.has(p.filename)) return;
    seen.add(p.filename);
    parts.push(buildFilePart({ ...p, toolCallId }));
  };

  if (Array.isArray(result.output_files)) {
    for (const of of result.output_files) {
      const filename = outputFileName(of);
      if (filename) push({ filename, fileType: mimeFor(filename) });
    }
  }

  if (result.success) {
    if (!isFileProducingTool(toolName) && !FILE_RESULT_KEYS.some((k) => k in result)) return parts;
    const filePath = (result.file_path || result.path || result.artifact_uri) as string | undefined;
    let filename = result.filename as string | undefined;
    if (!filename && filePath) filename = basename(String(filePath));
    if (filename) {
      push({
        filename,
        size: typeof result.size === 'number' && result.size ? result.size : undefined,
        fileType: mimeFor(filename),
        downloadUrl: (result.download_url || result.artifact_uri || undefined) as string | undefined,
      });
    }
  }
  return parts;
}

/** Source parts for web search, memory recall and knowledge-base search results. */
export function deriveSourceParts(toolName: string, result: unknown): MessagePart[] {
  if (!isDict(result) || !Array.isArray(result.results)) return [];
  const rows = result.results as unknown[];
  const parts: MessagePart[] = [];

  if (toolName === 'web_search' || toolName === 'search_web') {
    for (const sr of rows) {
      if (isDict(sr) && sr.url) {
        parts.push(buildSourcePart({
          sourceType: 'url',
          title: (sr.title as string) || 'Web result',
          url: sr.url as string,
          snippet: (sr.snippet as string) ?? '',
        }));
      }
    }
  } else if (toolName === 'search_past_decisions' || toolName === 'search_entities') {
    for (const mem of rows.slice(0, 5)) {
      if (!isDict(mem)) continue;
      parts.push(buildSourcePart({
        sourceType: 'memory',
        title: `Memory: ${(mem.action_type as string) || 'decision'}`,
        url: `/memory?id=${(mem.id as string) ?? ''}`,
        snippet: String(mem.context_decision ?? '').slice(0, 120),
      }));
    }
  } else if (toolName === 'search_knowledge') {
    const seenDocs = new Set<string>();
    for (const r of rows) {
      if (!isDict(r)) continue;
      const docId = (r.document_id as string) ?? '';
      if (seenDocs.has(docId)) continue;
      seenDocs.add(docId);
      parts.push(buildSourcePart({
        sourceType: 'document',
        title: (r.document_name as string) || (r.source as string) || 'Unknown document',
        url: `/knowledge-bases/${(r.knowledge_base_id as string) ?? ''}/documents/${docId}`,
        snippet: String(r.content ?? '').slice(0, 200),
      }));
    }
  }
  return parts;
}
