# Testing Oniva

This project uses three layers of automated tests. End-to-end coverage lives
in the backend integration suite — that is the canonical place to add new
scenarios when wiring up agent skills, node executors, or workflow flows.

## Layout

| Layer | Location | Tooling |
|-------|----------|---------|
| Backend unit | `backend/tests/test_*.py` | pytest |
| Backend integration / E2E | `backend/tests/integration/` | pytest, in-memory SQLite, mocked LLMs |
| Frontend unit | `frontend/{components,hooks,lib}/**/__tests__/` | Jest + React Testing Library |

The integration suite covers the realistic agent paths (Deep Work, structured
elicitation, chat pipeline, state machine) by composing the
real services against an in-memory database and stubbed LLM/E2B clients.

## Running

```bash
# Backend — all tests
cd backend && pytest

# Backend — integration / E2E only
cd backend && pytest tests/integration -v

# Backend — single scenario
cd backend && pytest tests/integration/test_deep_work_e2e.py::test_workspace_save -v

# Frontend
cd frontend && npm test
cd frontend && npm test -- --watch     # interactive
```

## Adding tests

**For agent skills, node executors, and workflow features**, add a scenario in
`backend/tests/integration/`. Reuse fixtures from
`backend/tests/integration/conftest.py` (`integration_db`, `session_storage`,
`skill_context`, `state_machine`) rather than reconstructing the wiring.

**For UI components**, colocate Jest tests next to the component under
`__tests__/`. Prefer testing-library queries (`getByRole`, `getByText`) over
test IDs.

**For pure utilities**, prefer `frontend/lib/__tests__/` or
`backend/tests/test_*.py` and avoid pulling in the full app context.

## CI smoke check

A minimal integration smoke run is:

```bash
cd backend && pytest tests/integration -q --maxfail=1
cd frontend && npm test -- --ci
```

Both should be green before opening a PR.

## Evals

The eval harness lives in `backend/evals/` (tasks in `evals/tasks/`, reports in `evals/results/` which is not
committed, promoted baselines in `evals/baselines/`). Run from `backend/`:

```bash
# fake mode: scripted model, replayed web, no judges, no key, free. Needs the local stack (engine deps in engine/node_modules, Postgres tools on PATH)
ONIVA_E2E=1 uv run python -m evals run --tag regression -k 3 --baseline regression-fake --strict
# after a deliberate change, re-baseline (refuses failed trials and existing files unless --force)
ONIVA_E2E=1 uv run python -m evals run --tag regression -k 3 --out evals/results/new.json
uv run python -m evals promote-baseline evals/results/new.json --force
# harness tests: offline ones always, stack ones with ONIVA_E2E=1
ONIVA_E2E=1 uv run pytest -q -p no:cacheprovider -m "e2e or eval" tests/evals
```

- **CI** (`.github/workflows/evals.yml`, path-filtered): the `regression` tag at k=3 in fake mode against
  `evals/baselines/regression-fake.json`, then the mutation meta-eval. About 3 minutes (the stack boot dominates; the
  27 trials take about 25 seconds). It fails on a pass-rate drop beyond max(0.05, 2 standard errors), a cost per
  pass rise over 15%, a gating grader that newly fails, or a baseline task that is missing (`--strict`).
- **Mutation meta-eval** (`evals/mutate.py`, `tests/evals/test_mutations.py`): injects known faults (dropped
  citations, limit exceeded, wrong tool order, run error, cost blow-up, ...) into recorded traces and asserts the
  grader meant to catch each one fails. A grader that misses its mutation fails the test.
- **Live mode** (`--live`) spends money: it needs `EVAL_OPENROUTER_API_KEY` and a spend cap (`EVAL_MAX_USD` or
  `--max-usd`), and refuses to start without both. CI never uses it. Keep the key in `.env` and load it with
  `uv run --env-file .env ...`; never print it.

## Conventions

- External services (E2B, LLM providers, Linear, OFAC, etc.) are mocked in
  tests; never exercise live credentials in CI.
- Keep integration tests deterministic — use `freezegun` or seeded UUIDs when a
  scenario depends on time or IDs.
- One scenario per test function; share setup through fixtures, not helpers
  with implicit state.
