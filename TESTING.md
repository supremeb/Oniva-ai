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

## Conventions

- External services (E2B, LLM providers, Linear, OFAC, etc.) are mocked in
  tests; never exercise live credentials in CI.
- Keep integration tests deterministic — use `freezegun` or seeded UUIDs when a
  scenario depends on time or IDs.
- One scenario per test function; share setup through fixtures, not helpers
  with implicit state.
