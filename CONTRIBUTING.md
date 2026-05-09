# Contributing to Oniva

Thanks for your interest in contributing! This document covers the basics for
getting a development environment running, the project conventions, and how
changes flow through review.

## Development setup

Prerequisites: Docker, Docker Compose, Node.js 20+, Python 3.12+.

```bash
git clone <your-fork-url>
cd oniva
cp .env.example .env   # fill in API keys; never commit .env
docker compose up      # starts postgres, redis, backend, frontend
```

For local (non-Docker) backend/frontend dev, see [QUICKSTART.md](./QUICKSTART.md).

## Project layout

- `backend/` — FastAPI service, Pydantic AI agents, workflow engine
- `frontend/` — Next.js 15 + React Flow visual builder
- `synthetic-bank/` — sample dataset / API used in demos
- `docker-compose.yml` — all services for local dev

## Branching and commits

- Branch from `master`. Use a descriptive prefix: `feat/`, `fix/`, `refactor/`, `docs/`.
- Keep commits focused. Conventional commit prefixes are encouraged
  (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`).
- Open a PR against `master` once green locally.

## Tests

| Layer | Command | Notes |
|-------|---------|-------|
| Backend unit + integration | `cd backend && pytest` | SQLite-backed; mocks LLM/E2B |
| Backend integration only | `cd backend && pytest tests/integration -v` | E2E scenarios across the stack |
| Frontend unit | `cd frontend && npm test` | Jest + React Testing Library |
| Lint (frontend) | `cd frontend && npm run lint` | Next ESLint config |
| Build (frontend) | `cd frontend && npm run build` | Type-check + production build |

A change should keep existing tests green and add coverage for non-trivial
new behavior. The backend integration suite under `backend/tests/integration/`
is the canonical end-to-end check — extend it when adding new agent skills,
node executors, or workflow features.

## Style

- **Backend**: async/await for I/O, Pydantic models at API boundaries,
  Alembic migrations for schema changes (`alembic revision --autogenerate`).
- **Frontend**: TypeScript strict mode, server components by default,
  Zustand for client state, Tailwind for styling.
- Don't add backwards-compat shims or speculative abstractions; keep changes
  scoped to the task.
- No emojis in code. Avoid comments that restate what the code does.

## Security

- Never commit secrets. `.env` is gitignored; rotate any key you accidentally
  paste.
- Report vulnerabilities privately via a maintainer rather than a public issue.

## Code of conduct

Be kind, assume good faith, and keep discussions technical. Disagreements get
resolved through evidence and small, reviewable PRs.
