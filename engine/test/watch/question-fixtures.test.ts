/**
 * Golden chat views with `ask_user` questions (plan 2026-10-17-002, U12): the
 * conversation views the real engine produces for a pending, an answered
 * (options plus free text) and an expired question. The frontend adapter tests
 * read the file.
 *
 *     ONIVA_RECORD_CONTRACTS=1 npx vitest run --project unit question-fixtures   # re-record
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { askUserTool } from "../../src/agent/ask-user-tool.js";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { type ConversationView, loadConversationView } from "../../src/watch/view.js";
import { memoryBackend, waitFor } from "../agent/support.js";

const FILE = join(__dirname, "../../../frontend/lib/engine/__fixtures__/question_views.json");
const RECORD = process.env.ONIVA_RECORD_CONTRACTS === "1";
const AGENT = { systemPrompt: "s", tools: ["ask_user"] };
const ASK = {
  question: "Which environments should I deploy to?",
  options: [
    { id: "staging", label: "Staging" },
    { id: "prod", label: "Production", description: "customer facing" },
  ],
  multiSelect: true,
  allowFreeText: true,
};

const ID = /(?:[a-z]+_)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g;
function normalize<T>(value: T): T {
  const ids = new Map<string, string>();
  const text = JSON.stringify(value).replace(ID, (m) => {
    const prefix = /^[a-z]+_/.exec(m)?.[0] ?? "";
    const raw = m.slice(prefix.length);
    if (!ids.has(raw)) ids.set(raw, `<id-${ids.size + 1}>`);
    return `${prefix}${ids.get(raw)}`;
  });
  return JSON.parse(text) as T;
}

async function scenario(answer: "answer" | "expire") {
  const fx = await memoryBackend.fixture();
  fx.provider.enqueue(fakeToolCall("ask_user", ASK, { id: "call_ask_1" }), fakeText("Done."));
  const w = await fx.open({ tools: [askUserTool], questionTtlMs: 30 * 60_000 });
  const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1", agent: AGENT });
  const sub = await conv.submit({ text: "deploy it" });
  await waitFor(async () => (await w.harness.questions(conv.id)).some((i) => i.status === "pending"));
  await w.harness.waitForIdle();
  const out: Record<string, ConversationView> = {};
  out.pending = structuredClone(await loadConversationView(w.harness.session, conv.id));
  const item = (await w.harness.questions(conv.id))[0];
  if (answer === "answer") {
    await w.harness.answerQuestion({
      conversationId: conv.id,
      toolTaskId: (item as { toolTaskId: string }).toolTaskId,
      answer: { optionIds: ["staging"], text: "and the canary" },
      principal: "user:alice",
    });
  } else {
    await fx.clock.advanceAsync(31 * 60_000);
  }
  expect((await sub.wait()).status).toBe("done");
  out.settled = structuredClone(await loadConversationView(w.harness.session, conv.id));
  await w.crash();
  return out;
}

describe("question golden views", () => {
  it("pending, answered and expired views match the recorded fixture", async () => {
    const answered = await scenario("answer");
    const expired = await scenario("expire");
    const next = normalize({
      question_pending: answered.pending,
      question_answered: answered.settled,
      question_expired: expired.settled,
    });
    expect(next.question_pending?.questions.items[0]?.status).toBe("pending");
    expect(next.question_answered?.questions.items[0]?.status).toBe("answered");
    expect(next.question_expired?.questions.items[0]?.status).toBe("expired");
    if (RECORD) writeFileSync(FILE, `${JSON.stringify(next, null, 2)}\n`);
    else expect(next).toEqual(JSON.parse(readFileSync(FILE, "utf8")));
  });
});
