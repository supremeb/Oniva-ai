import { describe, expect, it } from "vitest";
import { toolOutputDoc } from "../../src/agent/progress.js";
import { TOOL_TASK_KIND } from "../../src/agent/tool-task.js";
import { newId } from "../../src/core/ids.js";
import type { Session } from "../../src/session/session.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import type { SessionStorage, TaskRecord } from "../../src/storage/types.js";
import { loadViewState } from "../../src/watch/view.js";

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
