/**
 * `steer_worker` (U21, R18, KTD14), run against every storage backend.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { createSteerWorkerTool, STEER_PREFIX } from "../../src/agent/steer-worker-tool.js";
import { createSubagentTool } from "../../src/agent/subagents.js";
import type { JsonObject } from "../../src/core/json.js";
import { type FakeResponse, fakeText } from "../../src/models/fake.js";
import type { ModelRequest } from "../../src/models/types.js";
import type { EntryRecord } from "../../src/storage/types.js";
import { defineTool } from "../../src/tools/registry.js";
import { type AgentBackend, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;
const results = (entries: EntryRecord[]): Any[] =>
  entries.filter((e) => e.kind === "tool_result").map((e) => e.payload as Any);
const users = (entries: EntryRecord[]): string[] =>
  entries
    .filter((e) => e.kind === "user")
    .map((e) => {
      const c = (e.payload as Any).content;
      return typeof c === "string" ? c : c.map((p: Any) => p.text).join("");
    });

const AGENT = { systemPrompt: "parent prompt", tools: ["research", "steer_worker"], metadata: {} };

type Call = { name: string; arguments: JsonObject; id: string };
const calls = (...cs: Call[]): FakeResponse => ({
  steps: cs.map((c) => ({ type: "tool_call" as const, ...c })),
});

/** Holds `hold` calls (per name) until the test releases them. */
function holds() {
  const entered = new Set<string>();
  const open = new Map<string, () => void>();
  const gates = new Map<string, Promise<void>>();
  const gate = (name: string) => {
    let g = gates.get(name);
    if (!g) {
      g = new Promise<void>((resolve) => open.set(name, resolve));
      gates.set(name, g);
    }
    return g;
  };
  const tool = defineTool({
    name: "hold",
    description: "Holds until released",
    parameters: z.object({ name: z.string() }),
    replay: "safe",
    execute: async ({ name }, api) => {
      entered.add(name);
      await Promise.race([
        gate(name),
        new Promise<never>((_, reject) => {
          api.signal.addEventListener("abort", () => reject(api.signal.reason), { once: true });
        }),
      ]);
      return { content: "held" };
    },
  });
  return {
    tool,
    entered,
    release: (name: string) => {
      gate(name);
      open.get(name)?.();
    },
  };
}

const text = (m: ModelRequest["messages"][number] | undefined): string => {
  if (!m) return "";
  if (m.role === "user" || m.role === "tool_result") {
    return typeof m.content === "string" ? m.content : m.content.map((c) => c.text).join("");
  }
  return "";
};

/**
 * Child requests (system prompt "child prompt") hold once, then answer and say
 * whether the orchestrator's message reached them. Parents use `parent`.
 */
function route(parent: (req: ModelRequest) => FakeResponse) {
  return (req: ModelRequest): FakeResponse => {
    const sys = req.messages[0];
    if (sys?.role === "system" && sys.text === "child prompt") {
      const name = /CHILD-(\w+)/.exec(text(req.messages[1]))?.[1] ?? "?";
      if (req.messages.length === 2 && text(req.messages[1]).includes("FAST")) {
        return fakeText(`${name} fast`);
      }
      if (req.messages.length === 2) {
        return calls({ name: "hold", arguments: { name }, id: `h-${name}` });
      }
      return fakeText(`${name} steered=${JSON.stringify(req.messages).includes(STEER_PREFIX)}`);
    }
    return parent(req);
  };
}

const research = () =>
  createSubagentTool({ name: "research", systemPrompt: "child prompt", tools: ["hold"] });

export function defineSteerWorkerSuite(backend: AgentBackend): void {
  describe(`steer_worker (${backend.name})`, () => {
    const setup = async (
      parent: (req: ModelRequest) => FakeResponse,
      extra: Array<ReturnType<typeof createSteerWorkerTool>> = [createSteerWorkerTool()],
    ) => {
      const fx = await backend.fixture();
      const h = holds();
      fx.provider.enqueue(...Array.from({ length: 60 }, () => route(parent)));
      const tools = [research(), h.tool, ...extra];
      const w = await fx.open({ tools });
      // A steer sent in the same round as its worker waits on the engine clock
      // (a manual one here): keep it ticking while the test runs.
      const tick = setInterval(() => fx.clock.advance(50), 5);
      const crash = w.crash.bind(w);
      w.crash = async () => {
        clearInterval(tick);
        await crash();
      };
      return { fx, h, w, tools, tick };
    };
    const childByLabel = (w: Any, convId: string, label: string) =>
      w.harness.runtime
        .tasks({ conversationId: convId })
        .find((t: Any) => t.kind === "agent.tool" && t.checkpoint?.label === label)?.checkpoint
        ?.subagent?.conversationId as string | undefined;
    const steerDone = async (conv: Any) =>
      waitFor(async () => results(await conv.entries()).some((r) => r.toolName === "steer_worker"));

    it("steering w1 reaches w1's next model request and not w2's", async () => {
      const { fx, h, w } = await setup((req) =>
        req.messages.length === 2
          ? calls(
              { name: "research", arguments: { prompt: "CHILD-w1", label: "w1" }, id: "r1" },
              { name: "research", arguments: { prompt: "CHILD-w2", label: "w2" }, id: "r2" },
              {
                name: "steer_worker",
                arguments: { label: "w1", message: "turn left" },
                id: "s1",
              },
            )
          : fakeText("parent done"),
      );
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      await steerDone(conv);
      const out = results(await conv.entries()).find((r) => r.toolName === "steer_worker");
      expect(out).toMatchObject({ status: "ok", isError: false });
      expect(out.details.label).toBe("w1");
      expect(out.details.steered).toHaveLength(1);
      h.release("w1");
      h.release("w2");
      const settled = await sub.wait();
      expect(settled.status).toBe("done");

      const byName = Object.fromEntries(
        results(await conv.entries())
          .filter((r) => r.toolName === "research")
          .map((r) => [r.toolCallId, r.content]),
      );
      expect(byName.r1).toBe("w1 steered=true");
      expect(byName.r2).toBe("w2 steered=false");
      // Only w1's conversation holds the wrapped message, as a user entry after the tool round.
      const w1 = childByLabel(w, conv.id, "w1") as string;
      const w2 = childByLabel(w, conv.id, "w2") as string;
      expect(users(await w.harness.conversation(w1).entries())).toEqual([
        "CHILD-w1",
        `${STEER_PREFIX}turn left`,
      ]);
      expect(users(await w.harness.conversation(w2).entries())).toEqual(["CHILD-w2"]);
      expect(fx.provider.requests.length).toBeGreaterThan(0);
      await w.crash();
    });

    it("an unknown label is an error naming the live labels; a finished child is not matched", async () => {
      // Steer only once worker "f" has finished, so the outcome does not depend on timing.
      const real = createSteerWorkerTool();
      const phase = real.enginePhase as NonNullable<typeof real.enginePhase>;
      const afterF = {
        ...real,
        enginePhase: {
          phase: phase.phase,
          run: async (...a: Parameters<typeof phase.run>) => {
            const [, , rt] = a;
            await waitFor(() =>
              rt.runtime
                .tasks()
                .some(
                  (t) =>
                    t.kind === "agent.tool" &&
                    t.status === "terminal" &&
                    (t.input as Any).call.arguments.label === "f",
                ),
            );
            return phase.run(...a);
          },
        },
      };
      const { h, w } = await setup(
        (req) =>
          req.messages.length === 2
            ? calls(
                { name: "research", arguments: { prompt: "CHILD-w1", label: "w1" }, id: "r1" },
                { name: "research", arguments: { prompt: "FAST CHILD-f", label: "f" }, id: "r2" },
                { name: "steer_worker", arguments: { label: "nope", message: "x" }, id: "s1" },
                { name: "steer_worker", arguments: { label: "f", message: "x" }, id: "s2" },
              )
            : fakeText("parent done"),
        [afterF],
      );
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      await waitFor(
        async () =>
          results(await conv.entries()).filter((r) => r.toolName === "steer_worker").length === 2,
      );
      const steers = results(await conv.entries()).filter((r) => r.toolName === "steer_worker");
      const nope = steers.find((r) => r.toolCallId === "s1");
      expect(nope).toMatchObject({ status: "error", isError: true });
      expect(nope.content).toContain('"nope"');
      expect(nope.content).toContain("w1");
      const fin = steers.find((r) => r.toolCallId === "s2");
      expect(fin).toMatchObject({ status: "error", isError: true });
      expect(fin.content).toContain('No running worker has the label "f"');
      h.release("w1");
      expect((await sub.wait()).status).toBe("done");
      // Nothing was sent to the finished child.
      const f = childByLabel(w, conv.id, "f") as string;
      expect(users(await w.harness.conversation(f).entries())).toEqual(["FAST CHILD-f"]);
      await w.crash();
    });

    it("with nothing labelled running the error says so", async () => {
      const { w } = await setup((req) =>
        req.messages.length === 2
          ? calls({ name: "steer_worker", arguments: { label: "w1", message: "x" }, id: "s1" })
          : fakeText("parent done"),
      );
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      expect((await (await conv.submit({ text: "go" })).wait()).status).toBe("done");
      const [r] = results(await conv.entries());
      expect(r).toMatchObject({ status: "error", isError: true });
      expect(r.content).toContain("No labelled worker is running");
      await w.crash();
    });

    it("rejects a malformed label as invalid_arguments", async () => {
      const { w } = await setup((req) =>
        req.messages.length === 2
          ? calls({
              name: "steer_worker",
              arguments: { label: "bad label", message: "x" },
              id: "s1",
            })
          : fakeText("parent done"),
      );
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      await (await conv.submit({ text: "go" })).wait();
      expect(results(await conv.entries())[0]).toMatchObject({
        status: "invalid_arguments",
        isError: true,
      });
      await w.crash();
    });

    it("a duplicate call with the same call id sends one steer", async () => {
      const { h, w } = await setup((req) =>
        req.messages.length === 2
          ? calls(
              { name: "research", arguments: { prompt: "CHILD-w1", label: "w1" }, id: "r1" },
              { name: "steer_worker", arguments: { label: "w1", message: "once" }, id: "dup" },
              { name: "steer_worker", arguments: { label: "w1", message: "once" }, id: "dup" },
            )
          : fakeText("parent done"),
      );
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      await steerDone(conv);
      // The same call again (a second tool task with the same call id) added nothing.
      await waitFor(
        async () =>
          results(await conv.entries()).filter((r) => r.toolName === "steer_worker").length === 2,
      );
      expect(
        results(await conv.entries())
          .filter((r) => r.toolName === "steer_worker")
          .every((r) => r.status === "ok"),
      ).toBe(true);
      const w1 = childByLabel(w, conv.id, "w1") as string;
      const subs = (await w.harness.session.reader.listSubmissions(w1, { limit: 10 })).items;
      const steers = subs.filter((s) => s.requestId.startsWith("steer:"));
      expect(steers).toHaveLength(1);
      expect(steers[0]?.requestId).toMatch(/^steer:.+:dup$/);
      h.release("w1");
      await sub.wait();
      expect(users(await w.harness.conversation(w1).entries())).toEqual([
        "CHILD-w1",
        `${STEER_PREFIX}once`,
      ]);
      await w.crash();
    });

    it("a steer that was committed before a crash is delivered once and not re-sent", async () => {
      const { fx, h, w, tools, tick } = await setup((req) =>
        req.messages.length === 2
          ? calls(
              { name: "research", arguments: { prompt: "CHILD-w1", label: "w1" }, id: "r1" },
              { name: "steer_worker", arguments: { label: "w1", message: "once" }, id: "s1" },
            )
          : fakeText("parent done"),
      );
      const conv = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const sub = await conv.submit({ text: "go" });
      await steerDone(conv);
      const w1 = childByLabel(w, conv.id, "w1") as string;
      await w.crash();

      const w2 = await fx.open({ tools });
      const tick2 = setInterval(() => fx.clock.advance(50), 5);
      await waitFor(() => h.entered.has("w1"));
      h.release("w1");
      expect((await w2.harness.submission(sub.id).wait()).status).toBe("done");
      const steers = (
        await w2.harness.session.reader.listSubmissions(w1, { limit: 10 })
      ).items.filter((s) => s.requestId.startsWith("steer:"));
      expect(steers).toHaveLength(1);
      expect(users(await w2.harness.conversation(w1).entries())).toEqual([
        "CHILD-w1",
        `${STEER_PREFIX}once`,
      ]);
      clearInterval(tick2);
      clearInterval(tick);
      await w2.crash();
    });

    it("cannot steer another run's or another tenant's children", async () => {
      const { h, w } = await setup((req) => {
        const who = text(req.messages[1]);
        if (req.messages.length !== 2) return fakeText("parent done");
        return who.startsWith("A")
          ? calls(
              { name: "research", arguments: { prompt: "CHILD-a1", label: "w1" }, id: "r1" },
              { name: "steer_worker", arguments: { label: "w1", message: "for A" }, id: "s1" },
            )
          : calls({ name: "research", arguments: { prompt: "CHILD-b1", label: "w1" }, id: "r1" });
      });
      const a = await w.harness.createConversation({
        tenantId: "t1",
        projectId: "p1",
        agent: AGENT,
      });
      const b = await w.harness.createConversation({
        tenantId: "t2",
        projectId: "p2",
        agent: AGENT,
      });
      const subB = await b.submit({ text: "B go" });
      const subA = await a.submit({ text: "A go" });
      await steerDone(a);
      await waitFor(() => h.entered.has("b1"));
      const childA = childByLabel(w, a.id, "w1") as string;
      const childB = childByLabel(w, b.id, "w1") as string;
      expect(childA).not.toBe(childB);
      h.release("a1");
      h.release("b1");
      await subA.wait();
      await subB.wait();
      expect(users(await w.harness.conversation(childA).entries())).toEqual([
        "CHILD-a1",
        `${STEER_PREFIX}for A`,
      ]);
      expect(users(await w.harness.conversation(childB).entries())).toEqual(["CHILD-b1"]);
      const subsB = (await w.harness.session.reader.listSubmissions(childB, { limit: 10 })).items;
      expect(subsB.some((s) => s.requestId.startsWith("steer:"))).toBe(false);
      await w.crash();
    });
  });
}
