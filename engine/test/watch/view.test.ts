import { describe, expect, it } from "vitest";
import { COMPACTION_TASK_KIND, compactionDoc } from "../../src/agent/compaction.js";
import { GENERATION_TASK_KIND } from "../../src/agent/generation.js";
import { toolOutputDoc } from "../../src/agent/progress.js";
import { questionsDoc } from "../../src/agent/questions.js";
import { TOOL_TASK_KIND } from "../../src/agent/tool-task.js";
import { newId } from "../../src/core/ids.js";
import type { CommitEvent } from "../../src/session/publish.js";
import type { Session } from "../../src/session/session.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import type { SessionStorage, TaskRecord } from "../../src/storage/types.js";
import { applyCommitToView, loadViewState } from "../../src/watch/view.js";

const task = (conversationId: string, kind: string): TaskRecord => ({
  id: newId("task"),
  kind,
  conversationId,
  parentTaskId: null,
  status: "running",
  phase: "start",
  checkpoint: null,
  input: null,
  waiting: null,
  signals: {},
  outcome: null,
  abandonOnRestart: false,
  createdAt: 1,
  updatedAt: 1,
});

async function seed() {
  const storage = await new MemoryStorageProvider().open("sess-view");
  const conversationId = newId("conversation");
  const tools = [task(conversationId, TOOL_TASK_KIND), task(conversationId, TOOL_TASK_KIND)];
  const silent = task(conversationId, TOOL_TASK_KIND);
  const other = task(conversationId, "agent.run");
  const output = (text: string, i = 0) => ({
    text,
    truncated: false,
    attempt: 1,
    toolCallId: `call-${i}`,
    toolName: "work",
  });
  await storage.commit(
    {
      createConversations: [
        { id: conversationId, tenantId: "t", projectId: "p", ownerTaskId: null, createdAt: 1 },
      ],
      createTasks: [...tools, silent, other],
      putDocuments: [
        ...tools.map((t, i) => ({
          kind: toolOutputDoc.kind,
          scopeId: t.id,
          data: output(`out-${i}`, i),
          updatedAt: 2,
        })),
        // Not a tool task: its document is not part of the view.
        { kind: toolOutputDoc.kind, scopeId: other.id, data: output("x"), updatedAt: 2 },
      ],
    },
    null,
  );
  return { storage, conversationId, tools, silent, output };
}

/** A session stub whose reader counts document reads. */
function sessionOver(storage: SessionStorage, batched: boolean) {
  const calls = { getDocument: 0, getDocuments: 0 };
  const reader = {
    getConversation: (id: string) => storage.getConversation(id),
    listEntries: (id: string, o: never) => storage.listEntries(id, o),
    listTasks: (o: never) => storage.listTasks(o),
    getDocument: (kind: string, scopeId: string) => {
      calls.getDocument++;
      return storage.getDocument(kind, scopeId);
    },
    ...(batched
      ? {
          getDocuments: (kind: string, scopeIds: readonly string[]) => {
            calls.getDocuments++;
            return storage.getDocuments(kind, scopeIds);
          },
        }
      : {}),
  };
  const session = {
    reader,
    document: async (def: { initial: unknown }) => structuredClone(def.initial),
  } as unknown as Session;
  return { session, calls };
}

describe("view load", () => {
  it("reads tool outputs with one batched document read", async () => {
    const { storage, conversationId, tools, silent, output } = await seed();
    const { session, calls } = sessionOver(storage, true);
    const state = await loadViewState(session, conversationId);
    expect(calls).toEqual({ getDocument: 0, getDocuments: 1 });
    expect(state.view.toolOutput).toEqual({
      [tools[0]?.id as string]: output("out-0", 0),
      [tools[1]?.id as string]: output("out-1", 1),
    });
    // Each output names the call it belongs to.
    expect(state.view.toolOutput[tools[1]?.id as string]).toMatchObject({
      toolCallId: "call-1",
      toolName: "work",
    });
    expect([...state.toolTasks].sort()).toEqual([...tools.map((t) => t.id), silent.id].sort());
  });
});

describe("view compaction", () => {
  const started = 1_700_000_000_000;

  async function conversation() {
    const storage = await new MemoryStorageProvider().open("sess-compaction");
    const conversationId = newId("conversation");
    await storage.commit(
      {
        createConversations: [
          { id: conversationId, tenantId: "t", projectId: "p", ownerTaskId: null, createdAt: 1 },
        ],
      },
      null,
    );
    return { storage, conversationId };
  }

  const event = (
    conversationId: string,
    running: string | null,
    createTasks: TaskRecord[] = [],
  ): CommitEvent => ({
    sessionId: "sess-compaction",
    seq: 1,
    batch: { createTasks },
    documents: [
      {
        kind: compactionDoc.kind,
        scopeId: conversationId,
        before: null,
        after: { running, ready: null, backgroundFailedAt: null },
      },
    ],
  });

  it("is idle when no compaction document exists", async () => {
    const { storage, conversationId } = await conversation();
    const { session } = sessionOver(storage, true);
    const state = await loadViewState(session, conversationId);
    expect(state.view.compaction).toEqual({ running: false });
  });

  it("loads a running compaction with its cause and start, without task ids", async () => {
    const { storage, conversationId } = await conversation();
    const t = {
      ...task(conversationId, COMPACTION_TASK_KIND),
      input: { runId: "r", round: 1, cause: "threshold" },
      createdAt: started,
    };
    await storage.commit(
      {
        createTasks: [t],
        putDocuments: [
          {
            kind: compactionDoc.kind,
            scopeId: conversationId,
            data: { running: t.id, ready: null, backgroundFailedAt: null },
            updatedAt: 2,
          },
        ],
      },
      null,
    );
    const session = {
      ...sessionOver(storage, true).session,
      document: async (def: { kind: string; initial: unknown }, scopeId: string) =>
        (await storage.getDocument(def.kind, scopeId))?.data ?? structuredClone(def.initial),
    } as unknown as Session;
    const state = await loadViewState(session, conversationId);
    expect(state.view.compaction).toEqual({
      running: true,
      cause: "threshold",
      startedAt: new Date(started).toISOString(),
    });
    expect(JSON.stringify(state.view.compaction)).not.toContain(t.id);
  });

  it("patches when a compaction starts and clears when it lands", async () => {
    const { storage, conversationId } = await conversation();
    const { session } = sessionOver(storage, true);
    const state = await loadViewState(session, conversationId);
    const t = {
      ...task(conversationId, COMPACTION_TASK_KIND),
      input: { runId: "r", round: 1, cause: "background" },
      createdAt: started,
    };

    const start = applyCommitToView(state, conversationId, event(conversationId, t.id, [t]));
    expect(state.view.compaction).toEqual({
      running: true,
      cause: "background",
      startedAt: new Date(started).toISOString(),
    });
    expect(start.length).toBeGreaterThan(0);
    expect(start.every((op) => op.path.startsWith("/compaction"))).toBe(true);

    // A commit that leaves the compaction running changes nothing.
    expect(applyCommitToView(state, conversationId, event(conversationId, t.id))).toEqual([]);

    const land = applyCommitToView(state, conversationId, event(conversationId, null));
    expect(state.view.compaction).toEqual({ running: false });
    expect(land.length).toBeGreaterThan(0);
    expect(land.every((op) => op.path.startsWith("/compaction"))).toBe(true);
  });

  it("ignores another conversation's compaction", async () => {
    const { storage, conversationId } = await conversation();
    const { session } = sessionOver(storage, true);
    const state = await loadViewState(session, conversationId);
    const ops = applyCommitToView(state, conversationId, event("other", "task-x"));
    expect(ops).toEqual([]);
    expect(state.view.compaction).toEqual({ running: false });
  });
});

describe("view run", () => {
  async function conversation() {
    const storage = await new MemoryStorageProvider().open("sess-run");
    const conversationId = newId("conversation");
    await storage.commit(
      {
        createConversations: [
          { id: conversationId, tenantId: "t", projectId: "p", ownerTaskId: null, createdAt: 1 },
        ],
      },
      null,
    );
    return { storage, conversationId };
  }
  const ev = (batch: CommitEvent["batch"]): CommitEvent => ({
    sessionId: "sess-run",
    seq: 1,
    batch,
    documents: [],
  });

  it("is idle without a run and ignores child generations", async () => {
    const { storage, conversationId } = await conversation();
    const state = await loadViewState(sessionOver(storage, true).session, conversationId);
    expect(state.view.run).toEqual({ active: false });
    const child = { ...task(conversationId, GENERATION_TASK_KIND), parentTaskId: "x" };
    expect(applyCommitToView(state, conversationId, ev({ createTasks: [child] }))).toEqual([]);
    expect(state.view.run).toEqual({ active: false });
  });

  it("loads an active run from committed tasks", async () => {
    const { storage, conversationId } = await conversation();
    const g = { ...task(conversationId, GENERATION_TASK_KIND), createdAt: 1_700_000_000_000 };
    await storage.commit({ createTasks: [g] }, null);
    const state = await loadViewState(sessionOver(storage, true).session, conversationId);
    expect(state.view.run).toEqual({
      active: true,
      runId: g.id,
      startedAt: new Date(1_700_000_000_000).toISOString(),
    });
  });

  it("patches when a run starts and stays active until the root task is terminal", async () => {
    const { storage, conversationId } = await conversation();
    const state = await loadViewState(sessionOver(storage, true).session, conversationId);
    const g = { ...task(conversationId, GENERATION_TASK_KIND), createdAt: 1_700_000_000_000 };
    const start = applyCommitToView(state, conversationId, ev({ createTasks: [g] }));
    expect(state.view.run.active).toBe(true);
    expect(start.length).toBeGreaterThan(0);
    expect(start.every((op) => op.path.startsWith("/run"))).toBe(true);
    // A waiting root (tools running, approval pending) is still active.
    const waiting = { ...g, status: "waiting" as const, updatedAt: 2 };
    expect(applyCommitToView(state, conversationId, ev({ replaceTasks: [waiting] }))).toEqual([]);
    const done = { ...g, status: "terminal" as const, updatedAt: 3 };
    const end = applyCommitToView(state, conversationId, ev({ replaceTasks: [done] }));
    expect(state.view.run).toEqual({ active: false });
    expect(end.every((op) => op.path.startsWith("/run"))).toBe(true);
  });

  it("ignores another conversation's run", async () => {
    const { storage, conversationId } = await conversation();
    const state = await loadViewState(sessionOver(storage, true).session, conversationId);
    const g = task("other", GENERATION_TASK_KIND);
    expect(applyCommitToView(state, conversationId, ev({ createTasks: [g] }))).toEqual([]);
    expect(state.view.run).toEqual({ active: false });
  });
});

describe("view questions", () => {
  const item = (n: number, status: "pending" | "answered" = "pending") => ({
    id: `ques_${n}`,
    toolTaskId: `task_${n}`,
    runId: "r",
    toolCallId: `c${n}`,
    question: "Which colour?",
    options: [],
    multiSelect: false,
    allowFreeText: true,
    requestedAt: 1,
    expiresAt: null,
    status,
    answer: null,
    closedAt: null,
  });
  const change = (conversationId: string, before: unknown, after: unknown): CommitEvent => ({
    sessionId: "sess-q",
    seq: 1,
    batch: {},
    documents: [{ kind: questionsDoc.kind, scopeId: conversationId, before, after }] as never,
  });

  async function bare() {
    const storage = await new MemoryStorageProvider().open("sess-q");
    const conversationId = newId("conversation");
    await storage.commit(
      {
        createConversations: [
          { id: conversationId, tenantId: "t", projectId: "p", ownerTaskId: null, createdAt: 1 },
        ],
      },
      null,
    );
    const { session } = sessionOver(storage, true);
    return { session, conversationId };
  }

  it("is an empty member when the conversation has no questions document", async () => {
    const { session, conversationId } = await bare();
    const state = await loadViewState(session, conversationId);
    expect(state.view.questions).toEqual({ items: [] });
  });

  it("patches /questions/items/<n> when a question opens and when it is answered", async () => {
    const { session, conversationId } = await bare();
    const state = await loadViewState(session, conversationId);
    const opened = applyCommitToView(
      state,
      conversationId,
      change(conversationId, null, { items: [item(0)] }),
    );
    expect(opened).toEqual([{ op: "add", path: "/questions/items/0", value: item(0) }]);
    const answered = applyCommitToView(
      state,
      conversationId,
      change(conversationId, { items: [item(0)] }, { items: [item(0, "answered")] }),
    );
    expect(answered).toEqual([
      { op: "replace", path: "/questions/items/0/status", value: "answered" },
    ]);
    expect(state.view.questions.items[0]?.status).toBe("answered");
  });

  it("ignores another conversation's questions", async () => {
    const { session, conversationId } = await bare();
    const state = await loadViewState(session, conversationId);
    const res = applyCommitToView(
      state,
      conversationId,
      change("other", null, { items: [item(0)] }),
    );
    expect(res).toEqual([]);
    expect(state.view.questions.items).toEqual([]);
  });
});
