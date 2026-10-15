# Oniva — Enterprise AI Agent Workflow Platform

**Website: [oniva.ai/agents](https://oniva.ai/agents) · Built by [Oniva](https://oniva.ai) · UI on [oniva/ui](https://design.oniva.ai)**

[![License: BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-blue.svg)](./LICENSE)
[![Commercial license](https://img.shields.io/badge/commercial-contact-success.svg)](mailto:balachander.ral@gmail.com)

> **Source-available under the [Business Source License 1.1](./LICENSE).**
> Free for personal, educational, research, and non-production evaluation.
> Production or commercial use (internal tooling, hosted services, redistribution
> as a product) requires a separate commercial license — converts to Apache 2.0
> on **2030-05-09**. Commercial inquiries: **balachander.ral@gmail.com**.

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#whats-technically-interesting">What's interesting</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#documentation">Documentation</a>
</p>

---

## Overview

Oniva is an enterprise-grade platform for building, deploying, and managing AI agent workflows through a visual drag-and-drop interface. Think **Zapier for AI agents** — with crash-safe durable execution, cryptographic audit trails, and a state-machine-enforced safety layer that survives both LLM hallucinations and infrastructure failures.

## Demo

[![Watch the Oniva demo on YouTube](https://img.youtube.com/vi/89XbO40I7iM/maxresdefault.jpg)](https://www.youtube.com/watch?v=89XbO40I7iM "Watch the Oniva demo")

▶ **[Watch the demo](https://www.youtube.com/watch?v=89XbO40I7iM)** — visual workflow builder, durable execution, signed audit log, connectors.

**Core value proposition:**
- **Visual workflow builder** that non-engineers can drive end-to-end
- **Postgres-checkpointed workflows** — per-node checkpoints with manual recovery via API (DBOS is declared as a dependency but is not integrated; no automatic resume on boot)
- **Tamper-proof audit trail** — RSA-2048 signed, hash-chained, 7-year retention
- **Tenant-isolated** at the database level (PostgreSQL Row Level Security)

---

## What's Technically Interesting

The Oniva codebase is more than a CRUD frontend over an LLM call. A few things worth a look if you're evaluating engineering depth:

- **State machine + tool masking** (`backend/app/agents/state_machine.py`, `stateful_agent.py`) — a library implementing an 8-state machine with per-state tool allow-lists (list filtering, not logit masking). It is covered by tests but is not yet wired into the production agent runtime.
- **Postgres-checkpointed workflows** (`backend/app/services/durable_workflow_engine.py`) — each node writes a checkpoint row; a failed run can be resumed manually through the `/recover` endpoint. Steps are not idempotent (a node that crashed mid-side-effect re-runs), nothing auto-recovers on boot, and DBOS is not used.
- **Hash-chained, RSA-2048 signed audit log** (`backend/app/services/audit_logger.py`) — every decision the system makes is provably linked to the previous one. Designed for SEC / MiFID II / SOX-style replay.
- **OpenAPI-spec-driven connector factory** (`backend/app/connectors/openapi/`) — drop in any OpenAPI spec, get an in-process connector tool (MCP-shaped, not the MCP protocol) with SSRF protection, private-IP blocking, and per-tenant credential resolution.
- **PostgreSQL RLS with transaction-scoped tenant context** (`backend/app/database.py`, migrations 011/043) — `current_setting('app.current_tenant', true)::uuid` gates every query; `db.flush()` + `db.refresh()` keeps the LOCAL setting alive across nested operations.
- **Session storage, volumes and artifacts** — per-session plan/todo/state files, read-only pre-warmed volumes, and artifact storage (local disk, optional S3), plus pgvector HNSW indexes for semantic recall. There is no Redis/PostgreSQL/S3 three-tier memory.

---

## Features

### Visual workflow builder
- 30+ node types — agents, logic, transforms, integrations
- React Flow canvas with real-time streaming execution
- Per-node Postgres checkpointing with manual recovery (no DBOS)
- Human Approval nodes are simulated: they auto-approve (no real pause/resume yet)
- Node types without an executor (Data Retrieval, Analysis, Research, Schedule Trigger) are hidden from the palette

### AI agents
- Pre-built agents — Data Retrieval, Analysis, Compliance, Research
- Custom agents with personalized prompts and tool sets
- State-machine-enforced execution — per-state tool allow-lists exist as a library but are not yet enforced at runtime
- Test/deploy gates with automated evaluation before production

### Connectors
- **4 native in-process connectors** (MCP-shaped, not the MCP protocol) — ServiceNow, Microsoft Teams, OFAC, OpenAPI factory
- **Mock skills** — sanctions_check, kyc_verification, aml_screening, regulatory_filing, risk_assessment, fetch_market_data, get_financial_statements, fetch_economic_indicators and extract_entities return simulated data and are flagged `mock: true` (labelled "Mock" in the UI)
- **31 OpenAPI-derived connectors** — Stripe, Slack, GitHub, HubSpot, Jira, Notion, and more
- **Custom imports** — point at any OpenAPI spec, get a sandboxed tool

### Enterprise compliance
- Cryptographic audit trails with hash chaining
- 7-year artifact retention (S3-compatible)
- Workflow replay for regulator audits
- Human-in-the-loop approval queues for high-risk decisions

---

## Quick Start

### Prerequisites
- Docker and Docker Compose
- Node.js 20+ and Python 3.12+ (for local development)
- An LLM provider key (OpenAI, Anthropic, or OpenRouter)

### Docker (recommended)

```bash
git clone https://github.com/supremeb/Oniva-ai.git
cd Oniva-ai

cp .env.example .env
# Edit .env: set OPENAI_API_KEY (or ANTHROPIC_API_KEY / OPENROUTER_API_KEY)
# and replace any CHANGE_ME_* placeholders.

docker compose up
```

### Local development

```bash
docker compose up -d postgres redis

# Backend (Terminal 1)
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (Terminal 2)
cd frontend
npm install && npm run dev
```

### Access points
| Service     | URL                          |
|-------------|------------------------------|
| Frontend    | http://localhost:3000        |
| API docs    | http://localhost:8000/docs   |
| PostgreSQL  | localhost:5432               |
| Redis       | localhost:6379               |

---

## Architecture

### Built with

<p align="center">
  <a href="https://nextjs.org"><img alt="Next.js" height="40" src="https://img.shields.io/badge/Next.js-000000?style=for-the-badge&logo=nextdotjs&logoColor=white" /></a>
  <a href="https://react.dev"><img alt="React" height="40" src="https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB" /></a>
  <a href="https://www.typescriptlang.org"><img alt="TypeScript" height="40" src="https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white" /></a>
  <a href="https://tailwindcss.com"><img alt="Tailwind CSS" height="40" src="https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white" /></a>
  <a href="https://www.radix-ui.com"><img alt="Radix UI" height="40" src="https://img.shields.io/badge/Radix_UI-161618?style=for-the-badge&logo=radixui&logoColor=white" /></a>
  <a href="https://reactflow.dev"><img alt="React Flow" height="40" src="https://img.shields.io/badge/React_Flow-FF0072?style=for-the-badge&logo=reactflow&logoColor=white" /></a>
</p>

<p align="center">
  <a href="https://www.python.org"><img alt="Python" height="40" src="https://img.shields.io/badge/Python_3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" /></a>
  <a href="https://fastapi.tiangolo.com"><img alt="FastAPI" height="40" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" /></a>
  <a href="https://ai.pydantic.dev"><img alt="Pydantic AI" height="40" src="assets/logos/pydantic-ai.png" /></a>
  <a href="https://www.sqlalchemy.org"><img alt="SQLAlchemy" height="40" src="https://img.shields.io/badge/SQLAlchemy-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white" /></a>
  <a href="https://e2b.dev"><img alt="E2B" height="40" src="assets/logos/e2b.svg" /></a>
</p>

<p align="center">
  <a href="https://www.postgresql.org"><img alt="PostgreSQL" height="40" src="https://img.shields.io/badge/PostgreSQL_16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" /></a>
  <a href="https://github.com/pgvector/pgvector"><img alt="pgvector" height="40" src="https://img.shields.io/badge/pgvector-336791?style=for-the-badge&logo=postgresql&logoColor=white" /></a>
  <a href="https://redis.io"><img alt="Redis" height="40" src="https://img.shields.io/badge/Redis_7-DC382D?style=for-the-badge&logo=redis&logoColor=white" /></a>
  <a href="https://openai.com"><img alt="OpenAI" height="40" src="https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white" /></a>
  <a href="https://www.anthropic.com"><img alt="Anthropic" height="40" src="https://img.shields.io/badge/Anthropic-191919?style=for-the-badge&logo=anthropic&logoColor=white" /></a>
  <a href="https://openrouter.ai"><img alt="OpenRouter" height="40" src="https://img.shields.io/badge/OpenRouter-3F3F46?style=for-the-badge&logoColor=white" /></a>
  <a href="https://www.docker.com"><img alt="Docker" height="40" src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" /></a>
</p>

| Layer       | Technologies                                                                |
|-------------|-----------------------------------------------------------------------------|
| Frontend    | Next.js 15, React, TypeScript, @xyflow/react 12, TanStack Query, Tailwind CSS, Radix UI |
| Backend     | FastAPI 0.115, Pydantic AI, SQLAlchemy 2.0 async, LiteLLM                  |
| Sandbox     | E2B (per-tenant code-execution sandboxes)                                   |
| Database    | PostgreSQL 16 + pgvector, Redis 7                                           |
| LLM         | OpenAI, Anthropic, OpenRouter (via LiteLLM)                                 |
| Container   | Docker, Docker Compose, Kubernetes                                          |

### Data flow

```
┌─────────────┐      ┌─────────────┐      ┌──────────────┐
│  Frontend   │─────▶│   FastAPI   │─────▶│ PostgreSQL   │
│  (Next.js)  │      │   Backend   │      │ + pgvector   │
└─────────────┘      └─────┬───────┘      └──────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ LLM Providers│
                    └──────────────┘
```

### Repository layout

```
Oniva-ai/
├── frontend/                       # Next.js 15 app
│   ├── app/(dashboard)/            # Authenticated dashboard routes
│   ├── components/                 # UI + workflow nodes
│   └── lib/                        # API clients, Zustand stores
│
├── backend/                        # FastAPI app
│   ├── app/
│   │   ├── api/v1/                 # ~399 endpoints across 55 modules
│   │   ├── agents/                 # Agent + state machine code
│   │   ├── connectors/             # In-process connectors + OpenAPI factory
│   │   ├── services/               # Durable workflows, sessions, audit
│   │   └── models/                 # SQLAlchemy 2.0 models
│   └── migrations/                 # Alembic schema migrations
│
└── docker-compose.yml              # Local development
```

---

## Feature flags

Architecture features are env-controlled. See `backend/app/config/__init__.py` for the source of truth.

```python
USE_DURABLE_WORKFLOWS   = True    # Postgres checkpointing (not DBOS)
USE_FILESYSTEM_SESSIONS = True    # Plan / todo recitation
USE_VOLUMES             = True    # Pre-warmed input data
USE_ARTIFACTS           = True    # 7-year compliance storage
USE_STATE_MACHINE       = True    # Tool masking
USE_EPISODIC_MEMORY     = True
USE_USER_PREFERENCES    = True
```

---

## API surface

The backend exposes ~399 REST endpoints across 55 modules. A few highlights:

| Endpoint                                    | Description                          |
|---------------------------------------------|--------------------------------------|
| `GET /health`                               | Health check                         |
| `CRUD /api/v1/agents`                       | Agent management                     |
| `CRUD /api/v1/workflows`                    | Workflow management                  |
| `POST /api/v1/workflows/execute-durable`    | Postgres-checkpointed execution      |
| `POST /api/v1/chat`                         | Streaming chat                       |
| `GET /api/v1/workflows/{id}/checkpoints`    | List durable checkpoints             |
| `POST /api/v1/workflows/{id}/recover`       | Resume from last checkpoint          |
| `GET /api/v1/workflows/{id}/replay`         | Audit-grade replay                   |
| `CRUD /api/v1/volumes`                      | Volume management                    |
| `CRUD /api/v1/artifacts`                    | Long-term artifact storage           |
| `GET/POST /api/v1/audit/reviews`            | Human-in-the-loop review queue       |

Full interactive docs at `http://localhost:8000/docs` once running.

---

## Environment variables

| Variable                | Description                       | Required |
|-------------------------|-----------------------------------|----------|
| `OPENAI_API_KEY`        | OpenAI API key                    | One LLM key required |
| `ANTHROPIC_API_KEY`     | Anthropic API key                 | One LLM key required |
| `OPENROUTER_API_KEY`    | OpenRouter API key                | One LLM key required |
| `DATABASE_URL`          | PostgreSQL connection             | Yes      |
| `REDIS_URL`             | Redis connection                  | Yes      |
| `SECRET_KEY`            | JWT signing key (≥32 chars)       | Yes      |
| `USE_DURABLE_WORKFLOWS` | Enable Postgres checkpointing    | No (default `True`)  |
| `USE_STATE_MACHINE`     | Enable state-machine safety layer | No (default `True`)  |

See `.env.example` and `backend/.env.example` for the full list.

---

## Testing

```bash
# Backend
cd backend && pytest

# Frontend
cd frontend && npm test
```

Coverage targets: 70% overall, 90% on critical paths, 95% on agent evaluation suites.

---

## Documentation

- [QUICKSTART.md](./QUICKSTART.md) — local development setup
- [TESTING.md](./TESTING.md) — testing strategy
- [CONTRIBUTING.md](./CONTRIBUTING.md) — contribution guidelines
- [SECURITY.md](./SECURITY.md) — vulnerability disclosure
- In-app docs at `http://localhost:3000/docs` once running

---

## Security

- Zero-trust architecture, ephemeral credentials, 15-minute JWT TTL
- Multi-tenant isolation via PostgreSQL Row Level Security
- RSA-2048-signed audit log with hash chaining
- State-machine tool allow-lists (library; not yet enforced at runtime)
- SSRF protection in the OpenAPI connector (private-IP blocking, scheme validation)
- Pydantic schemas validate every API boundary

To report a vulnerability, see [SECURITY.md](./SECURITY.md).

---

## Contributing

Fork, branch, change with tests, open a PR. See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full process.

Guidelines:
- TypeScript strict mode on the frontend
- Async/await for all I/O in the backend
- 70%+ coverage for new features
- Schema changes via `alembic revision --autogenerate`

---

## License

**Business Source License 1.1.** Source-available — free for non-production, personal, educational, and research use. Production or commercial use requires a separate license. Auto-converts to Apache 2.0 on 2030-05-09. See [LICENSE](./LICENSE).

For commercial licensing inquiries: **balachander.ral@gmail.com**

---

<p align="center">
  Built with Next.js · React Flow · FastAPI · Pydantic AI · E2B · PostgreSQL · Redis
</p>
