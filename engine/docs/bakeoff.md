# Engine bake-off results (U14)

Date: 2026-10-14 · Branch: `feat/oniva-durable-engine` · Machine: Apple M3 Pro,
Node 22.23, Postgres 14.18 (throwaway local cluster from `test/support/pg.ts`,
`fsync=off`, `synchronous_commit=off`).

The scenario suite (`npm run test:scenario`, `test/scenario/*.test.ts`) runs
real worker processes (`scripts/worker-test-harness.ts`) against Postgres,
drives them over HTTP, kills them with `SIGKILL` / `SIGSTOP` at marked points
(a gateway tool held on a gate, or an invoke logged as `invoke_pending`) and
starts replacements. Lease TTL in the suite is 2 s (heartbeat every 667 ms).
Waits are event- or state-driven (worker log lines, committed state polled
with timeouts); there are no fixed sleeps.

## Results

Three consecutive full runs, all green (12 tests in 7 files each):

| Run | Wall time | Files / tests |
| --- | --- | --- |
| 1 | 29.8 s | 7 / 12 passed |
| 2 | 30.1 s | 7 / 12 passed |
| 3 | 30.1 s | 7 / 12 passed |

| AE | Scenario (file) | Result | Typical duration |
| --- | --- | --- | --- |
| AE1 | Unsafe gateway write SIGKILLed after the gateway recorded its outcome → the stored outcome becomes the result; effects = 1, one invoke (`crash-resume`) | pass | 2.6 s |
| AE1 | Same, no recorded outcome → `interrupted` (warning, `isError`), not re-run; effects = 1, one invoke (`crash-resume`) | pass | 2.8 s |
| AE2 | Replay-safe (idempotent) write SIGKILLed → re-executed with the same `Idempotency-Key`, `attempts: 2`; effects = 2; run completes (`crash-resume`) | pass | 2.6 s |
| AE3 | Approval pending; shared clock +61 min; SIGKILL and replace; HTTP approve → tool runs once, run completes; decision ≥ 61 min after request (`approval-wait`) | pass | 2.8 s |
| AE4 | HTTP steer while the tool round is blocked → placed after the tool result, before round 1's model request, same `runId`; both submissions settle with that run's answer (`steer`) | pass | 0.7 s |
| AE5 | HTTP abort of a run whose subagent's tool call is in flight → every child task terminal `aborted` when the abort returns, and before the parent's run task by commit seq (`subagent`) | pass | 0.4 s |
| AE6 | Same `requestId` submitted twice, SIGKILL mid-reply, again on the replacement (and via `Idempotency-Key`) → one submission id, one user entry, run completes (`duplicate-submit`) | pass | 2.8 s |
| AE7 | Two workers on one session: one serves, the other answers `409 { error: "not_owner", ownerId }`; owner SIGKILLed → takeover (`lease`) | pass | 3.6 s |
| AE7 | Owner SIGSTOPped with a gateway invoke in flight; successor takes over, reconciles the call `interrupted` and raises the gateway high-water; resumed owner's stale invoke is rejected `409 fence_rejected`, effects = 0, it self-fences and exits 3; no commit with its token after the successor's first (`lease`) | pass | 2.6 s |
| — | Production `worker.ts` answers `404` for `/v1/test/clock` (GET and POST); the test harness answers `401` without and `200` with the token; `ENGINE_TEST_HARNESS` + `NODE_ENV=production` refuses to start (exit 2), the test harness refuses `NODE_ENV=production` (`approval-wait`) | pass | 1.0 s |

Takeover after SIGKILL of the owner (TTL 2000 ms, heartbeat 667 ms, bound
checked: TTL + heartbeat + 500 ms scheduling slack):

| Run | Lease acquired | Serving |
| --- | --- | --- |
| 1 | 1982 ms | 2015 ms |
| 2 | 2001 ms | 2014 ms |
| 3 | 1983 ms | 1996 ms |

Mutation check (to show the scenarios are not vacuous): with the
`X-Engine-Fence` header dropped and `reconcile` stubbed out of the gateway
client, the AE7 paused-owner scenario fails (the stale write executes) and the
AE1 recorded-outcome scenario fails (result is `interrupted`, not the stored
outcome). The client was restored afterwards.

## Load

10 conversations in one session, each streaming a 16 000-character (~4k-token)
fake reply in 400 deltas 5 ms apart, progress throttled to 100 ms. Latency is
the storage commit (the serialized part of the commit line: lease row lock,
fence check, writes, counters), measured in the worker around
`SessionStorage.commit`; bytes are the JSON size of each `CommitBatch`.

| Run | Commits | Streaming wall time | p50 | p99 | max | Mean bytes/commit | p50 bytes | Max bytes | Commit line busy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 261 | 2319 ms | 0.98 ms | 3.00 ms | 10.4 ms | 8717 | 7621 | 34 214 | 12.7 % |
| 2 | 261 | 2313 ms | 0.97 ms | 2.68 ms | 4.8 ms | 8662 | 7581 | 34 214 | 12.4 % |
| 3 | 261 | 2377 ms | 1.03 ms | 7.98 ms | 13.4 ms | 8451 | 7221 | 34 214 | 15.1 % |

About 26 commits per conversation for ~2.3 s of streaming: the 100 ms throttle
holds (≈ 23 progress commits plus submit and final commits). Bytes per commit
grow with the reply because each progress commit rewrites the whole
`agent.live` document; the largest batch (34 KB) is a reply's final commit,
which carries the complete reply.

## Kill criteria (restated from the plan)

An Oniva-owned engine is abandoned if any of these holds:

| # | Criterion | Verdict |
| --- | --- | --- |
| 1 | The storage conformance suite fails on Postgres | **Not triggered.** `test/storage/postgres.conformance.test.ts` passes in `npm test` (37 files, 388 tests green). |
| 2 | The AE7 fencing scenario fails | **Not triggered.** Both AE7 scenarios pass in three consecutive runs. |
| 3 | p99 commit latency under the load scenario exceeds 250 ms | **Not triggered.** p99 2.7–8.0 ms across three runs (local, see caveats). |
| 4 | The build exceeds 30 working days | **Not triggered.** U1–U14 were built on one day (about two hours of agent wall time, below). |

## Effort per unit

Approximate wall time between consecutive commits on 2026-10-14 (from `git
log`); units committed in the same second were developed in parallel and
landed together, so their time is shared.

| Unit | Committed | Approx. effort |
| --- | --- | --- |
| Plan | 08:42 | — |
| U1 scaffold & core primitives | 08:46 | ~4 min |
| U2 storage contract, memory backend, conformance | 08:52 | ~6 min |
| U6 model layer (parallel with U2/U3) | 08:55 | ~2 min after U2 |
| U3 session commit line & documents | 08:58 | ~6 min |
| U4 Postgres backend & fenced lease | 09:08 | ~10 min |
| U5 task runtime | 09:23 | ~15 min |
| U7 agent loop, hooks, HostPolicy | 09:36 | ~13 min |
| U13 gateway client, U8 submissions & inbox, U9 approvals (parallel) | 09:42 | ~7 min (shared) |
| U10 subagents, U11 compaction (parallel) | 09:57 | ~14 min (shared) |
| U12 watch stream & HTTP surface | 10:08 | ~11 min |
| U14 bake-off suite, worker entry points, docs | ~10:30 | ~20 min |

## Engine bugs found

None. Every scenario passed against the U1–U13 engine without changes to the
engine modules; U14 added only the worker wiring (`src/worker/`), the entry
points and test support.

## Known gaps and caveats

- **Latency is optimistic.** The local cluster runs with `fsync=off`; a
  production Postgres 16 with durable commits adds WAL flush time per commit.
  The measurement covers the storage commit only, not time queued on the
  session's commit line (the line was busy 12–15 % of the run, so queueing is
  small at this load). `.github/workflows/engine.yml` runs the same suite on
  Postgres 16 with default (durable) settings via `ENGINE_TEST_PG_URL`; that
  run has not been observed from this machine.
- **AE5 "idle" reading.** `abort` settles the parent submission
  `unanswered(aborted)` and clears the live document in its first commit, before
  tasks end (U8 design). The scenario reads "the parent reports idle" as the
  parent run's generation task ending and the abort call returning; both happen
  only after every child task is terminal `aborted`.
- **Gateway fencing needs a successor call.** The gateway rejects a stale token
  only once the successor has sent a higher one (its high-water mark). In AE7
  the successor's own tool call raises it; until then the stale worker's
  self-fence (watchdog) is the guard. The scenario models a request already on
  the wire by delivering it after the pause regardless of the worker's abort.
- **A paused worker inside a transaction** would hold the lease row lock and
  block takeover until its connection dies; production should set
  `idle_in_transaction_session_timeout` (and a `statement_timeout`) on the
  engine role.
- **Lease loss ends the process** (exit 3); a supervisor must restart it. A
  worker does not return to standby by itself.
- **Production entry runs through `tsx`** (`npm run worker`); `scripts/` is not
  part of `npm run build`. A deploy image should compile the entry point.
- **Real gateway.** The Python gateway does not yet honour `X-Engine-Fence` or
  outcome lookup by idempotency key; the contract is defined by the fake
  gateway (roadmap Phase 3).
- **Single-session workers.** Multi-session claim loops remain deferred (KTD4).
