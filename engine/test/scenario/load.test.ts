/**
 * Load: 10 concurrent conversations in one session, each streaming a ~4k-token
 * fake reply with progress commits throttled to 100 ms (KTD11). Records p50 /
 * p99 storage commit latency and bytes per commit; the kill criterion is a
 * p99 above 250 ms.
 */
import { describe, expect, it } from "vitest";
import { AUTH, call, createConversation, submit, useScenario } from "./support.js";

const sc = useScenario();

const CONVERSATIONS = 10;
const REPLY_CHARS = 16_000; // ~4k tokens at 4 chars per token
const P99_LIMIT_MS = 250;

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return Number.NaN;
  const i = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, i)] as number;
}

describe("load", () => {
  it("keeps p99 commit latency under 250 ms with 10 streaming conversations", async () => {
    await sc.script({
      rules: [
        {
          when: { role: "user", includes: "LOAD" },
          respond: {
            // 400 deltas of 40 chars, 5 ms apart: ~2 s of streaming per reply.
            chunkDelayMs: 5,
            steps: [{ type: "text", text: "word ", repeat: REPLY_CHARS / 5, chunkSize: 40 }],
          },
        },
      ],
    });
    const w = sc.worker("load", { ENGINE_PROGRESS_INTERVAL_MS: "100" });
    const url = await w.serving();
    const ids: string[] = [];
    for (let i = 0; i < CONVERSATIONS; i++) ids.push(await createConversation(url));
    const baseline = (await call(url, "GET", "/v1/test/stats", { headers: AUTH })).body.commits
      .length as number;

    const started = Date.now();
    const subs = await Promise.all(
      ids.map((id, i) => submit(url, id, { text: `LOAD ${i}`, requestId: `load-${i}` })),
    );
    const ins = await sc.inspect("load");
    const settled = await Promise.all(subs.map((s) => ins.settled(s.body.submissionId, 60_000)));
    const elapsed = Date.now() - started;
    for (const s of settled) {
      expect(s.status).toBe("done");
      expect((s.result as { text?: string }).text?.length).toBe(REPLY_CHARS);
    }

    const all = (await call(url, "GET", "/v1/test/stats", { headers: AUTH })).body.commits as {
      ms: number;
      bytes: number;
    }[];
    const samples = all.slice(baseline);
    const latencies = samples.map((s) => s.ms).sort((a, b) => a - b);
    const bytes = samples.map((s) => s.bytes);
    const result = {
      conversations: CONVERSATIONS,
      replyChars: REPLY_CHARS,
      commits: samples.length,
      elapsedMs: elapsed,
      p50Ms: Number(percentile(latencies, 50).toFixed(2)),
      p99Ms: Number(percentile(latencies, 99).toFixed(2)),
      maxMs: Number((latencies[latencies.length - 1] ?? 0).toFixed(2)),
      meanBytesPerCommit: Math.round(bytes.reduce((a, b) => a + b, 0) / bytes.length),
      p50BytesPerCommit: percentile(
        [...bytes].sort((a, b) => a - b),
        50,
      ),
      maxBytesPerCommit: Math.max(...bytes),
      // Share of the run the serialized commit line spent inside storage commits.
      commitLineBusy: Number((latencies.reduce((a, b) => a + b, 0) / elapsed).toFixed(3)),
    };
    console.log(`LOAD RESULT ${JSON.stringify(result)}`);

    // Throttling bounds progress commits: ~one per 100 ms per conversation.
    expect(samples.length).toBeLessThan(CONVERSATIONS * (elapsed / 100 + 10));
    expect(result.p99Ms).toBeLessThan(P99_LIMIT_MS);
  });
});
