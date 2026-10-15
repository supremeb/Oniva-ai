# Golden event-contract fixtures (U12 / R13)

Fixtures in `fixtures/*.json` record what the backend emits **today**, quirks
included (KTD5: record, don't fix). Phase 4 maps these quirks deliberately.

```
cd backend
uv run pytest tests/contracts                          # compare live output to fixtures
ONIVA_RECORD_CONTRACTS=1 uv run pytest tests/contracts # re-record (review the git diff!)
```

How it works: `streams.py` drives each real code path with the LLM faked (no app
code is modified; fakes are `unittest.mock.patch`es), `recorder.py` normalizes
the output (uuids and `tc-xxxx` ids become `<id-N>` in order of appearance;
`timestamp`/`*_at` keys and ISO strings become `<timestamp>`; `duration_ms`,
`durationMs`, `duration_seconds`, `elapsedMs`, `total_time` become `<duration>`),
and `test_contracts.py` compares. SSE `id` counters are deterministic and kept.
A deliberate event rename in `engine.py` was verified to fail the test.

| Stream | Driven through | Fake |
|---|---|---|
| Chat SSE | `generate_enhanced_sse_stream` (`api/v1/chat.py`) | real pydantic-ai `Agent` + `FunctionModel` (so real PartStart/PartDelta/FunctionToolCall/Result events); `AsyncSessionLocal`, `emit_run_event`, `store_memory_async` patched; `persist_session` replaced by a capture, so `persisted_messages[].message_parts` = the `metadata.parts` shape |
| Deep Work engine v2 | `DeepWorkEngine.run` with the real `SSEQueueSink`, plus `_build_full_state` (`full-state` recovery) | `LLMGateway` replaced by a tier/prompt-dispatching fake; `DeepWorkWorker._execute_tool` returns a canned `web_search` result; real `SessionStorage` on a temp dir |
| Fleet | `run_background_agent` then `stream_job_progress` (`api/v1/background_jobs.py`) | `LLMGateway` fake (tool call, then text); fake skill handler; an in-memory `BackgroundJob` instead of a DB |
| Workflow | `WorkflowOrchestrator.execute_workflow_stream` (raw events) and `execute_workflow_stream` endpoint (`workflow_progress`/`workflow_complete`) | in-memory spec of real executors (`start`, `template`, `output`); `AsyncMock` DB. `USE_STATE_MACHINE` and `USE_DATA_CLASSIFICATION` pinned to their code defaults (True) |

Workflow-correctness changes to the orchestrator / node executors / durable
engine were in flight when this was recorded. Re-record
(`ONIVA_RECORD_CONTRACTS=1`) and review the diff if that work changes events.

## Fixture notes and quirks

### chat_text / chat_tool_call / chat_error
- Each fixture is `{events, persisted_messages}`. `events` are the sse-starlette dicts (`event`, `id`, `data` decoded from JSON).
- `done.messageId` is a fresh uuid (normalized); `done.sessionId` is whatever `persist_session` returns; `done.model` is `agent.model`; `usage` comes from pydantic-ai (`input_tokens`/`output_tokens`).
- `text_delta` always carries `partIndex: 0` for deltas but the first chunk of a part uses the pydantic-ai part index (`PartStartEvent`), so they can differ in multi-part runs.
- `tool_call_complete` has **no `toolName`** (frontend falls back to the id) and `durationMs` is wall-clock.
- `tool_call_start.producesFile` is omitted when false. `save_report` is not in `_FILE_PRODUCING_TOOLS`, so no `producesFile`; the file part is emitted anyway via the `filename` heuristic in `_extract_file_info`, **without `content`** (args are only stashed for file-producing tools).
- Ordering: `tool_call_complete` is followed by derived events (`source`, `part`) for the same tool call.
- SSE `source` uses `snippet`; the persisted source part uses `description`. Persisted tool_call parts carry `status`, `result`, `error: null`, `durationMs` and are updated in place; the final persisted part is `{type:"text", isStreaming:false}`.
- Reasoning becomes `reasoning` SSE events and one accumulated persisted `thinking` part (`variant: "accordion"`).
- **Error path (`chat_error`)**: partial `text_delta`s are emitted first, then a single `error` event (`title: "Chat Error"`, `details: null`, message rewritten by `format_error`, `retryable` is a substring check on "rate limit"). No `done`, and **nothing is persisted** (`persisted_messages: []`), so the partial text is lost on reload. Non-rate-limit exceptions collapse to a generic message (not recorded separately).
- Not driven: XML tool-call rescue, KB/memory source events, `deep_work_suggested`, `metric`/`memory_saved` parts, the legacy non-enhanced stream, and agent-studio chat (`agent_chat.py`, a second SSE translator).

### deep_work_success / deep_work_failure
- `events` is what `GET /deep-work/engine/{id}/stream` sends on the wire: `{event, data}` (envelope `id`/`timestamp` is unwrapped and not recorded; SSE ids there are uuid4).
- Scenario: one planned worker (`w1`), one reflection round that adds a gap task (`gap-1`), a second reflection that stops, then synthesis with two sections. Only one worker per round is planned on purpose: parallel worker interleaving depends on aiofiles thread timing and is not deterministic, so concurrent ordering is **not** recorded.
- `deep_work_engine_worker_complete.tool_call_count` equals `iterations_used` (2 here) although only one tool call ran (it counts LLM iterations, not tool calls).
- `source_update` is emitted before `worker_complete`.
- `deep_work_engine_phase` events: `researching`, `reflecting`, `researching`, `reflecting`, `synthesizing`, then `workspace_update`, then `phase: completed`, then terminal `deep_work_engine_complete`.
- `deep_work_engine_reflection.round` is 0-based; `gap_tasks.round` is the round that produced them. `planned.sub_queries` are plain strings while `gap_tasks.sub_queries` are `{worker_id, sub_query}` objects.
- Synthesis streams the heading and body of each section as separate `synthesis_chunk`s, then the executive summary/takeaways stream, then the deterministic `## Sources` block. The assembled `report` has stray blank lines around the executive summary. `report_length` (characters) differs from `workspace_update.size` (bytes).
- `full_state` (recovery contract, `_build_full_state`) is derived from files, not events: `summary` duplicates `engine_report`, `engine_phase` is `null` once completed, `steps` is `[]`, `todos` are derived from the plan (gap tasks included because the plan is re-saved), `workers` is `engine_state.completed_workers`, `sources[]` gain `domain: null, query: null`. `persisted_engine_state` and `files` record the on-disk state.
- **Failure (`deep_work_failure`)**: orchestrator returns non-JSON. The engine emits **zero events** and raises `ValidationError` (no retry). The `deep_work_engine_error {message}` event is produced by the API layer (`deep_work.py` `_run_engine`), which is not driven here. The `run_failed` run event is patched out.
- Not driven: the removed v1 `AutonomousExecutor` events (`deep_work_start`, `step_activity`, `step_complete`, ...), the output phase (`deep_work_engine_output_*`, `sandbox_screenshot`, needs a sandbox), cancellation, and the dropped-on-full-queue behaviour.

### fleet_completed / fleet_failed / fleet_cancelled
- Each fixture: `{events, final_job}`. `events` is the output of `stream_job_progress` after the run has finished.
- Because the job is already terminal on the first poll, the whole run arrives in one burst: `progress` (with the final status/progress), one `step` per trace entry, then the terminal event. The live, incremental poll-every-0.5s behaviour is **not** recorded.
- `step` payloads are the raw trace entry (`step`, `type`, `tool_name`, `tool_args`, `tool_result_preview`, `duration_ms`, `timestamp`, `token_usage{input,output,total}`) with **no `step_id`**; text steps use `content_preview` instead of `tool_*`. This is what makes the frontend's `step_id` de-dup collapse steps.
- `progress.timestamp` is a naive `datetime.utcnow()` stringified with a space; trace timestamps are ISO with `+00:00`. `progress.current_step` is always `null`.
- `completed` repeats the entire `execution_trace`. `failed` carries `error: "LLM call failed: <detail>"`, `result: null`, an empty trace, and progress 0.
- `fleet_cancelled` is synthetic: the job runs to completion and then `job.cancel()` is called, mirroring what the cancel endpoint does. Progress stays 100, and the cancelled event has `error: null`. The audited race (the executor later calling `job.complete()` over `CANCELLED`) is not reproduced.
- `execution_trace` is only written once per iteration, so a failure on iteration 1 leaves it empty.

### workflow_success / workflow_unknown_node
- Each fixture: `{orchestrator_events, sse_events}`. `orchestrator_events` are the raw `progress` / `node_input_received` / `node_started` / `node_complete` / `complete` events; `sse_events` are the `workflow_progress` / `workflow_complete` events the frontend consumes.
- The endpoint drops `node_input_received` and `node_started`; each node yields two `workflow_progress` events (`active`/"Executing...", then `complete`/"Done"). `elapsedMs` is cumulative wall-clock from stream start.
- `workflow_complete.results` is the unfiltered orchestrator results map. The start node's output includes the full variable bag, including internal `sys.workflow_state` and `sys.session_id`.
- A node type with no executor is reported as `status: skipped` by the orchestrator but as `workflow_progress.status: "complete"`, message "Done" by the endpoint, and its label falls back to the raw node type (`does_not_exist`).
- `failed` / `halted` / `node_error` branches and LLM/agent/connector nodes are not driven (they need keys, a sandbox, or approval state, and those executors are being changed in parallel work).

### chat_engine_text / chat_engine_tool_call / chat_engine_error (engine path, U12)
- Each fixture is `{legacy, view}`: the conversation view the real engine produced for the same scripted conversation as the legacy fixture named in `legacy` (normalized by the recorder). The source is `frontend/lib/engine/__fixtures__/chat_views.json`; `tests/contracts/test_engine_chat_fixtures.py` keeps the copies in sync (re-record with `ONIVA_RECORD_CONTRACTS=1`), checks each view still holds the legacy user message, response and tool names, and fails when a legacy `chat_*` fixture has no `chat_engine_*` counterpart.
- The adapter output (`viewToMessages`) is compared to the legacy `message_parts` in `frontend/lib/engine/__tests__/viewToMessages.test.ts`. Deliberate differences from legacy (KTD6), each asserted there:
  - `tool_call` parts carry `toolName` (legacy omitted it) and no `label` or `error: null`; `durationMs` is measured from entry timestamps.
  - The error path keeps the partial assistant text and a `run_error` entry (legacy persisted nothing).
  - `source` parts use `description` consistently; the legacy `partIndex` quirks disappear.
