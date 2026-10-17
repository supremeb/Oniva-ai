/**
 * viewToDeepWork: the Deep Work panel as a pure function of the engine view
 * (plan 2026-10-15-002, U8, KTD8/KTD9).
 *
 * The orchestrator is the root conversation's model. Its tool calls carry the
 * whole story:
 *   dw_start_round(round, workers[])  -> rounds, and the plan (round 1)
 *   research_worker(prompt)           -> one worker per call, joined to
 *                                        `view.subagents[toolTaskId]` for live activity
 *   reflector(prompt)                 -> a reflection from its JSON answer
 *   dw_write_section(heading, md)     -> report sections, by heading order
 *   output_agent / sandbox tools      -> generating_output
 *   dw_finalize / publish_artifact    -> artifacts, completed
 * Every read of wire data is guarded; a malformed payload degrades one field,
 * it never throws.
 *
 * `createDeepWorkAdapter()` memoizes: a patch that changes one worker leaves
 * every other worker (and unchanged arrays) referentially equal.
 */
import type {
  DeepWorkArtifact,
  DeepWorkSource,
  EnginePhase,
  EngineWorkerState,
} from '@/lib/types/deep-work';
import type { ConversationView, EntryRecord, JsonValue, SubagentView, UsageTally } from './types';

// ---------------------------------------------------------------------------
// Public shape
// ---------------------------------------------------------------------------

/**
 * The card's phase. `planning_workers` is adapter-only: it covers the gap
 * between a round's `dw_start_round` registration and its first worker
 * subagent, while the orchestrator's delegation turn is still streaming (U3,
 * KTD3). It is not an engine wire phase.
 */
export type DeepWorkPhase = EnginePhase | 'planning_workers';

/** Card status. The adapter derives the last four; the earlier ones belong to the intake flow. */
export type DeepWorkRunStatus =
  | 'clarifying'
  | 'planning'
  | 'awaiting_approval'
  | 'executing'
  | 'completed'
  | 'error'
  | 'cancelled';

export interface DeepWorkRunMeta {
  /** Backend session status (deep_work_runs.status / full-state), used when the view alone is ambiguous. */
  status?: string | null;
  /** Backend error text, shown when the view has none. */
  error?: string | null;
  /** Plan title from the plan step (the view only carries the sub-queries). */
  planTitle?: string | null;
}

export interface DeepWorkWorker extends EngineWorkerState {
  /** Stable key: the engine tool call id (server-assigned, survives patches). */
  key: string;
  callId: string;
  /** The tool task id once known (result entry, tool output or subagent order). */
  taskId: string | null;
  /** The worker's child conversation, for the transcript drill-down. */
  childConversationId: string | null;
  round: number;
  summary?: string;
  sources: DeepWorkSource[];
  startedAt: string | null;
  endedAt: string | null;
}

export interface DeepWorkRound {
  round: number;
  /** Round 1 is the approved plan; later rounds are gap tasks from reflection. */
  kind: 'plan' | 'gap';
  workerIds: string[];
  status: 'running' | 'done';
}

export interface DeepWorkReflection {
  round: number;
  score: number;
  gaps: string[];
  continuing: boolean;
}

export interface DeepWorkSection {
  heading: string;
  markdown: string;
}

export interface DeepWorkState {
  status: DeepWorkRunStatus;
  /** Null once completed or stopped. */
  phase: DeepWorkPhase | null;
  /** Round 1's sub-queries as plan steps; null before the first round is registered. */
  plan: { title: string; steps: string[] } | null;
  rounds: DeepWorkRound[];
  workers: DeepWorkWorker[];
  reflections: DeepWorkReflection[];
  sections: DeepWorkSection[];
  /** Sections joined, in order (no Sources block: see `sources`). */
  report: string;
  sources: DeepWorkSource[];
  artifacts: DeepWorkArtifact[];
  usage: { requests: number; inputTokens: number; outputTokens: number; totalTokens: number; workerTokens: number };
  /** An interrupted result or a run-resumed marker has appeared. Never cleared. */
  resumed: boolean;
  /** `resumed` and the orchestrator has not produced anything since. Clears on the first later turn. */
  recovering: boolean;
  error: string | null;
  /** Epoch ms of the run start; null before any entry. */
  startedAt: number | null;
  endedAt: number | null;
}

// ---------------------------------------------------------------------------
// Guards
// ---------------------------------------------------------------------------

type Obj = { [key: string]: JsonValue };
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined);
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);
const strList = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x !== '') : [];

function textOf(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((c) => (isObj(c) && c.type === 'text' ? (str(c.text) ?? '') : '')).join('');
  }
  return '';
}

/** Parse a JSON object from a model answer: bare, fenced, or surrounded by prose. Never throws. */
export function parseLooseJson(text: string): Obj | null {
  const t = text.trim();
  const attempts: string[] = [t];
  const fence = /```(?:json)?\s*([\s\S]*?)```/i.exec(t);
  if (fence?.[1]) attempts.push(fence[1].trim());
  const first = t.indexOf('{');
  const last = t.lastIndexOf('}');
  if (first >= 0 && last > first) attempts.push(t.slice(first, last + 1));
  for (const a of attempts) {
    try {
      const v: unknown = JSON.parse(a);
      if (isObj(v)) return v;
    } catch {
      /* try the next shape */
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Tool names
// ---------------------------------------------------------------------------

const WORKER_TOOL = 'research_worker';
const SUBAGENT_TOOLS = new Set([WORKER_TOOL, 'reflector', 'output_agent']);
const OUTPUT_TOOLS = new Set([
  'output_agent',
  'shell_exec',
  'dw_browser_navigate',
  'dw_browser_screenshot',
  'inspect_artifact',
  'publish_artifact',
]);

function phaseOfTool(name: string): EnginePhase | null {
  if (name === 'dw_start_round' || name === WORKER_TOOL) return 'researching';
  if (name === 'reflector' || name === 'dw_record_reflection') return 'reflecting';
  if (name === 'dw_write_section' || name === 'dw_get_learnings' || name === 'dw_get_report') return 'synthesizing';
  if (OUTPUT_TOOLS.has(name)) return 'generating_output';
  return null;
}

// ---------------------------------------------------------------------------
// Entry scan
// ---------------------------------------------------------------------------

interface Call {
  id: string;
  name: string;
  args: Obj;
  at: number;
  index: number;
}

interface Result {
  status: string;
  content: string;
  taskId: string | null;
  at: number;
  index: number;
  details: Obj | null;
}

const ok = (r: Result | undefined): r is Result => r !== undefined && r.status === 'ok';

interface Scan {
  calls: Call[];
  results: Map<string, Result>;
  lastAssistantIndex: number;
  /** Index of the latest interrupted result or resume marker, or -1. */
  resumeIndex: number;
  runError: { message: string; code: string | null } | null;
  firstAt: number | null;
  lastAt: number | null;
}

const RESUME_KINDS = new Set(['run_resumed', 'resumed', 'run_resume']);

function scan(entries: readonly EntryRecord[]): Scan {
  const s: Scan = {
    calls: [],
    results: new Map(),
    lastAssistantIndex: -1,
    resumeIndex: -1,
    runError: null,
    firstAt: null,
    lastAt: null,
  };
  entries.forEach((e, index) => {
    if (s.firstAt === null) s.firstAt = e.createdAt;
    s.lastAt = e.createdAt;
    const p = isObj(e.payload) ? e.payload : null;
    if (RESUME_KINDS.has(e.kind)) s.resumeIndex = index;
    if (!p) return;
    if (e.kind === 'assistant') {
      s.lastAssistantIndex = index;
      if (p.partial === true || !isObj(p.message) || !Array.isArray(p.message.blocks)) return;
      for (const b of p.message.blocks) {
        if (isObj(b) && b.type === 'tool_call' && typeof b.id === 'string' && typeof b.name === 'string') {
          s.calls.push({ id: b.id, name: b.name, args: isObj(b.arguments) ? b.arguments : {}, at: e.createdAt, index });
        }
      }
    } else if (e.kind === 'tool_result' && typeof p.toolCallId === 'string') {
      const status = str(p.status) ?? 'ok';
      s.results.set(p.toolCallId, {
        status,
        content: textOf(p.content),
        taskId: e.taskId,
        at: e.createdAt,
        index,
        details: isObj(p.details) ? p.details : null,
      });
      if (status === 'interrupted') s.resumeIndex = index;
    } else if (e.kind === 'run_error') {
      const err = isObj(p.error) ? p.error : p;
      s.runError = {
        message: str(err.message) ?? 'The run failed.',
        code: str(err.kind) === 'aborted' ? 'aborted' : (str(err.code) ?? str(err.kind) ?? null),
      };
    }
  });
  return s;
}

// ---------------------------------------------------------------------------
// Derivations
// ---------------------------------------------------------------------------

interface RegisteredWorker {
  worker_id: string;
  sub_query: string;
}

interface RoundDef {
  round: number;
  workers: RegisteredWorker[];
}

function roundDefs(scanned: Scan): RoundDef[] {
  const byRound = new Map<number, RoundDef>();
  for (const c of scanned.calls) {
    if (c.name !== 'dw_start_round') continue;
    const r = scanned.results.get(c.id);
    if (r !== undefined && !ok(r)) continue; // refused: nothing registered
    const round = num(c.args.round);
    if (round === undefined || !Array.isArray(c.args.workers)) continue;
    const workers: RegisteredWorker[] = [];
    for (const w of c.args.workers) {
      if (!isObj(w)) continue;
      const id = str(w.worker_id);
      if (id) workers.push({ worker_id: id, sub_query: str(w.sub_query) ?? '' });
    }
    byRound.set(round, { round, workers }); // a repeated round replaces the earlier registration
  }
  return [...byRound.values()].sort((a, b) => a.round - b.round);
}

const WORKER_ID_RE = /\bworker[_\s-]?id\b["'`]?\s*[:=]\s*["'`]?([A-Za-z0-9_-]{1,64})/i;
const LEADING_ID_RE = /^\s*[[(]?\s*(?:worker\s+)?([wg]\d+)\b/i;

/** The worker id from the first lines of the delegation prompt, or null. */
export function parseWorkerId(prompt: string): string | null {
  const head = prompt.split('\n').slice(0, 4).join('\n');
  return WORKER_ID_RE.exec(head)?.[1] ?? LEADING_ID_RE.exec(head)?.[1] ?? null;
}

function domainOf(url: string): string | undefined {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return undefined;
  }
}

function toSource(raw: unknown): DeepWorkSource | null {
  if (!isObj(raw)) return null;
  const url = str(raw.url) ?? str(raw.source_url);
  const title = str(raw.title) ?? str(raw.source_title);
  if (!url && !title) return null;
  const kind = str(raw.type) ?? str(raw.source_type);
  const type: DeepWorkSource['type'] = kind === 'knowledge_base' ? 'knowledge_base' : url ? 'url' : 'search';
  const domain = url ? domainOf(url) : undefined;
  return { type, ...(title ? { title } : {}), ...(url ? { url } : {}), ...(domain ? { domain } : {}) };
}

function sourcesOf(list: unknown): DeepWorkSource[] {
  return Array.isArray(list) ? list.map(toSource).filter((x): x is DeepWorkSource => x !== null) : [];
}

const isoMs = (iso: string | undefined): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
};

interface WorkerInputs {
  call: Call;
  result: Result | undefined;
  taskId: string | null;
  sub: SubagentView | undefined;
  workerId: string;
  subQuery: string;
  round: number;
}

function buildWorker(i: WorkerInputs): DeepWorkWorker {
  const { call, result, sub } = i;
  const answer = ok(result) ? parseLooseJson(result.content) : null;
  const recent = sub ? strList(sub.recent) : [];
  const done = result !== undefined;
  const failed = done ? !ok(result) : sub?.status === 'failed' || sub?.status === 'aborted';
  const status: EngineWorkerState['status'] = failed ? 'error' : done ? 'complete' : 'researching';

  const sources = sourcesOf(answer?.sources);
  const findings = strList(answer?.top_findings);
  const learningCount = num(answer?.learning_count) ?? 0;
  const startedAt = sub?.startedAt ?? null;
  const endedAt = sub?.endedAt ?? null;
  const t0 = isoMs(startedAt ?? undefined);
  const t1 = isoMs(endedAt ?? undefined);
  const duration =
    t0 !== null && t1 !== null ? Math.max(0, (t1 - t0) / 1000) : done ? Math.max(0, (result.at - call.at) / 1000) : 0;

  const worker: DeepWorkWorker = {
    key: call.id,
    callId: call.id,
    taskId: i.taskId,
    childConversationId: sub?.conversationId ?? null,
    round: i.round,
    worker_id: i.workerId,
    sub_query: i.subQuery,
    status,
    learning_count: learningCount,
    sources,
    startedAt,
    endedAt,
  };
  if (recent.length) {
    worker.recentActions = recent;
    worker.activity = recent[recent.length - 1];
  }
  if (result !== undefined && result.status === 'ok') {
    const raw = answer ? (str(answer.summary) ?? '') : result.content.trim();
    if (raw) worker.summary = raw.length > 600 ? `${raw.slice(0, 600)}…` : raw;
    worker.completionSummary = {
      findings,
      sourceCount: num(answer?.source_count) ?? sources.length,
      duration,
      toolCallCount: sub?.toolCalls ?? 0,
    };
  } else if (result !== undefined) {
    const msg = result.content.trim();
    worker.error = msg ? (msg.length > 300 ? `${msg.slice(0, 300)}…` : msg) : `Worker ${result.status}`;
  } else if (failed) {
    worker.error = `Worker ${sub?.status ?? 'failed'}`;
  }
  return worker;
}

function reflectionsOf(scanned: Scan): DeepWorkReflection[] {
  const out: DeepWorkReflection[] = [];
  for (const c of scanned.calls) {
    if (c.name !== 'reflector') continue;
    const r = scanned.results.get(c.id);
    if (!ok(r)) continue;
    const a = parseLooseJson(r.content);
    const score = num(a?.score);
    if (!a || score === undefined) continue;
    out.push({
      round: num(a.round) ?? out.length + 1,
      score,
      gaps: strList(a.gaps),
      continuing: a.continuing === true || a.should_continue === true,
    });
  }
  return out;
}

function sectionsOf(scanned: Scan): DeepWorkSection[] {
  const out: DeepWorkSection[] = [];
  for (const c of scanned.calls) {
    if (c.name !== 'dw_write_section') continue;
    const r = scanned.results.get(c.id);
    if (r !== undefined && !ok(r)) continue;
    const heading = str(c.args.heading)?.trim();
    const markdown = str(c.args.markdown);
    if (!heading || markdown === undefined) continue;
    const at = out.findIndex((s) => s.heading === heading);
    if (at >= 0) out[at] = { heading, markdown };
    else out.push({ heading, markdown });
  }
  return out;
}

function artifactOf(raw: unknown): DeepWorkArtifact | null {
  if (typeof raw === 'string' && raw) return { type: 'file', filename: raw };
  if (!isObj(raw)) return null;
  const filename = str(raw.filename) ?? str(raw.name) ?? str(raw.path);
  const uri = str(raw.artifact_uri);
  const url = str(raw.download_url);
  if (!filename && !uri && !url) return null;
  const size = num(raw.size) ?? num(raw.size_bytes);
  return {
    type: 'file',
    filename: filename ?? (uri ?? url ?? 'artifact'),
    ...(size !== undefined ? { size } : {}),
    ...(url ? { downloadUrl: url } : {}),
    ...(uri ? { artifactUri: uri } : {}),
  };
}

function artifactsOf(scanned: Scan): DeepWorkArtifact[] {
  const out: DeepWorkArtifact[] = [];
  const seen = new Set<string>();
  const add = (raw: unknown) => {
    const a = artifactOf(raw);
    if (!a) return;
    const key = a.artifactUri ?? a.downloadUrl ?? a.filename;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(a);
  };
  for (const c of scanned.calls) {
    const r = scanned.results.get(c.id);
    if (!ok(r)) continue;
    if (c.name === 'publish_artifact') {
      const body = parseLooseJson(r.content);
      if (body && (body.status === undefined || body.status === 'published')) add(body);
    } else if (c.name === 'dw_finalize') {
      const body = parseLooseJson(r.content);
      if (body && Array.isArray(body.artifacts)) body.artifacts.forEach(add);
    }
  }
  return out;
}

function mergeSources(lists: DeepWorkSource[][]): DeepWorkSource[] {
  const seen = new Set<string>();
  const out: DeepWorkSource[] = [];
  for (const s of lists.flat()) {
    const key = s.url ?? s.title ?? '';
    if (key === '' || seen.has(key)) continue;
    seen.add(key);
    out.push(s);
  }
  return out;
}

function tally(t: UsageTally | undefined): { input: number; output: number } {
  return { input: num(t?.inputTokens) ?? 0, output: num(t?.outputTokens) ?? 0 };
}

// ---------------------------------------------------------------------------
// The adapter
// ---------------------------------------------------------------------------

export type DeepWorkAdapter = (view: ConversationView | null, meta?: DeepWorkRunMeta) => DeepWorkState;

const EMPTY: DeepWorkState = Object.freeze({
  status: 'executing',
  phase: null,
  plan: null,
  rounds: [],
  workers: [],
  reflections: [],
  sections: [],
  report: '',
  sources: [],
  artifacts: [],
  usage: { requests: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0, workerTokens: 0 },
  resumed: false,
  recovering: false,
  error: null,
  startedAt: null,
  endedAt: null,
}) as DeepWorkState;

/** A memoizing adapter: structurally equal parts keep their previous reference. */
export function createDeepWorkAdapter(): DeepWorkAdapter {
  const memo = new Map<string, { json: string; value: unknown }>();
  /** Return the previous value when `value` serializes identically. */
  const keep = <T>(key: string, value: T): T => {
    const json = JSON.stringify(value) ?? 'null';
    const prev = memo.get(key);
    if (prev && prev.json === json) return prev.value as T;
    memo.set(key, { json, value });
    return value;
  };
  let last: DeepWorkState | null = null;

  return (view, meta = {}) => {
    if (!view) return keep('empty', EMPTY);
    const scanned = scan(view.entries);
    const subagents = view.subagents ?? {};

    // -- link each call to its tool task (result entry, then tool output) --
    const taskByCall = new Map<string, string>();
    for (const [callId, r] of scanned.results) if (r.taskId) taskByCall.set(callId, r.taskId);
    for (const [taskId, out] of Object.entries(view.toolOutput ?? {})) {
      if (typeof out.toolCallId === 'string' && !taskByCall.has(out.toolCallId)) taskByCall.set(out.toolCallId, taskId);
    }
    // Calls still without a task: the unclaimed subagents, oldest first, in call order.
    const claimed = new Set(taskByCall.values());
    const spare = Object.entries(subagents)
      .filter(([taskId]) => !claimed.has(taskId))
      .sort(([ka, a], [kb, b]) => (a.startedAt < b.startedAt ? -1 : a.startedAt > b.startedAt ? 1 : ka < kb ? -1 : 1))
      .map(([taskId]) => taskId);
    for (const c of scanned.calls) {
      if (!SUBAGENT_TOOLS.has(c.name) || taskByCall.has(c.id) || scanned.results.has(c.id)) continue;
      const next = spare.shift();
      if (next) taskByCall.set(c.id, next);
    }

    // -- rounds and workers --
    const defs = roundDefs(scanned);
    const registered = new Map<string, { round: number; sub_query: string }>();
    for (const d of defs) for (const w of d.workers) registered.set(w.worker_id, { round: d.round, sub_query: w.sub_query });

    const workers: DeepWorkWorker[] = [];
    const usedIds = new Set<string>();
    let currentRound = 0;
    for (const c of scanned.calls) {
      if (c.name === 'dw_start_round') {
        const r = num(c.args.round);
        const res = scanned.results.get(c.id);
        if (r !== undefined && (res === undefined || ok(res))) currentRound = r;
        continue;
      }
      if (c.name !== WORKER_TOOL) continue;
      const prompt = str(c.args.prompt) ?? '';
      let workerId = parseWorkerId(prompt);
      if (workerId === null || usedIds.has(workerId)) {
        const open = defs
          .find((d) => d.round === currentRound)
          ?.workers.find((w) => !usedIds.has(w.worker_id));
        workerId = open?.worker_id ?? `w${workers.length + 1}`;
      }
      usedIds.add(workerId);
      const reg = registered.get(workerId);
      const taskId = taskByCall.get(c.id) ?? null;
      const built = buildWorker({
        call: c,
        result: scanned.results.get(c.id),
        taskId,
        sub: taskId ? subagents[taskId] : undefined,
        workerId,
        subQuery: reg?.sub_query ?? prompt.split('\n').find((l) => l.trim() !== '')?.trim().slice(0, 300) ?? '',
        round: reg?.round ?? (currentRound || 1),
      });
      workers.push(keep(`worker:${c.id}`, built));
    }

    const rounds: DeepWorkRound[] = defs.map((d) => {
      const inRound = workers.filter((w) => w.round === d.round);
      const done = inRound.length > 0 && inRound.length >= d.workers.length && inRound.every((w) => w.status !== 'researching');
      return { round: d.round, kind: d.round <= 1 ? 'plan' : 'gap', workerIds: d.workers.map((w) => w.worker_id), status: done ? 'done' : 'running' };
    });
    const first = defs[0];
    const plan = first
      ? { title: meta.planTitle ?? '', steps: first.workers.map((w) => w.sub_query).filter((q) => q !== '') }
      : null;

    const reflections = reflectionsOf(scanned);
    const sections = sectionsOf(scanned);
    const artifacts = artifactsOf(scanned);
    const finalize = scanned.calls.findLast((c) => c.name === 'dw_finalize' && ok(scanned.results.get(c.id)));
    const finalBody = finalize ? parseLooseJson((scanned.results.get(finalize.id) as Result).content) : null;
    const sources = mergeSources([...workers.map((w) => w.sources), sourcesOf(finalBody?.sources)]);

    // -- status and phase --
    const runActive = view.run?.active ?? view.live.runId != null;
    const aborted = scanned.runError?.code === 'aborted' || scanned.runError?.code === 'cancelled';
    const backend = meta.status ?? null;
    let status: DeepWorkRunStatus;
    let error: string | null = null;
    if (finalize) status = 'completed';
    else if (backend === 'cancelled' || backend === 'stopped' || aborted) status = 'cancelled';
    else if (backend === 'failed' || backend === 'error') {
      status = 'error';
      error = meta.error ?? scanned.runError?.message ?? 'The run failed.';
    } else if (runActive) status = 'executing';
    else if (scanned.runError) {
      status = 'error';
      error = scanned.runError.message;
    } else status = 'executing'; // idle with no verdict yet: the backend reconcile decides
    if (status === 'error' && error === null) error = scanned.runError?.message ?? meta.error ?? null;

    let phase: DeepWorkPhase | null = null;
    if (status === 'executing') {
      // KTD3: the orchestrator's own turn is live (`live.runId`), the latest
      // registered round has no worker subagent yet. The predicate holds from
      // the view alone; a named `research_worker` block in `live.message` only
      // ever makes it more specific, so it is not consulted.
      const currentRound = defs.length > 0 ? defs[defs.length - 1].round : null;
      const planningWorkers =
        runActive &&
        view.live.runId != null &&
        currentRound !== null &&
        !workers.some((w) => w.round === currentRound && w.childConversationId !== null);
      if (planningWorkers) {
        phase = 'planning_workers';
      } else {
        const running = scanned.calls.filter((c) => !scanned.results.has(c.id));
        const byRunning = [...running].reverse().map((c) => phaseOfTool(c.name)).find((p) => p !== null);
        const byLast = [...scanned.calls].reverse().map((c) => phaseOfTool(c.name)).find((p) => p !== null);
        phase = byRunning ?? byLast ?? 'researching';
      }
    }

    // -- usage --
    const root = tally(view.usage?.total);
    let workerIn = 0;
    let workerOut = 0;
    for (const s of Object.values(subagents)) {
      const t = tally(s.usage);
      workerIn += t.input;
      workerOut += t.output;
    }
    const usage = {
      requests: num(view.usage?.requests) ?? 0,
      inputTokens: root.input,
      outputTokens: root.output,
      totalTokens: root.input + root.output,
      workerTokens: workerIn + workerOut,
    };

    // -- recovery --
    const resumed = scanned.resumeIndex >= 0;
    const recovering = resumed && scanned.lastAssistantIndex < scanned.resumeIndex;

    const next: DeepWorkState = {
      status,
      phase,
      plan: keep('plan', plan),
      rounds: keep('rounds', rounds),
      workers: stableList(memo, 'workers', workers),
      reflections: keep('reflections', reflections),
      sections: keep('sections', sections),
      report: sections.map((s) => `## ${s.heading}\n\n${s.markdown}`).join('\n\n'),
      sources: keep('sources', sources),
      artifacts: keep('artifacts', artifacts),
      usage: keep('usage', usage),
      resumed,
      recovering,
      error,
      startedAt: isoMs(view.run?.startedAt) ?? scanned.firstAt,
      endedAt: status === 'completed' || status === 'error' || status === 'cancelled' ? scanned.lastAt : null,
    };
    const prev = last;
    if (prev && shallowEqual(prev, next)) return prev;
    last = next;
    return next;
  };
}

/** Reuse the previous array when every element is referentially the same. */
function stableList<T>(memo: Map<string, { json: string; value: unknown }>, key: string, list: T[]): T[] {
  const prev = memo.get(`list:${key}`)?.value as T[] | undefined;
  if (prev && prev.length === list.length && prev.every((x, i) => x === list[i])) return prev;
  memo.set(`list:${key}`, { json: '', value: list });
  return list;
}

function shallowEqual(a: DeepWorkState, b: DeepWorkState): boolean {
  const ka = Object.keys(a) as (keyof DeepWorkState)[];
  return ka.every((k) => a[k] === b[k]);
}

/** Pure form: a fresh adapter per call (no memoization across calls). */
export function viewToDeepWork(view: ConversationView | null, meta: DeepWorkRunMeta = {}): DeepWorkState {
  return createDeepWorkAdapter()(view, meta);
}
