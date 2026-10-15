# Oniva Quick Start Guide

Get the Oniva platform running in under 5 minutes!

---

## Prerequisites

Before starting, ensure you have:

| Requirement | Version | Check Command |
|-------------|---------|---------------|
| **Docker Desktop** | 27.x+ | `docker --version` |
| **Docker Compose** | 2.x+ | `docker compose version` |
| **Git** | 2.x+ | `git --version` |

**LLM API Key** (at least one):
- [OpenAI API Key](https://platform.openai.com/api-keys) - Recommended
- [Anthropic API Key](https://console.anthropic.com/) - Optional
- [OpenRouter API Key](https://openrouter.ai/keys) - Optional (100+ models)

---

## Quick Start (3 Steps)

### Step 1: Clone and Configure

```bash
# Clone the repository
git clone <your-repo-url>
cd Oniva

# Copy environment template
cp .env.example .env
```

Edit `.env` and add your API key(s):

```bash
# Required: At least one LLM provider
OPENAI_API_KEY=sk-your-openai-key-here

# Optional: Additional providers
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key-here
OPENROUTER_API_KEY=sk-or-your-openrouter-key-here
```

### Step 2: Start Services

```bash
# Start all services with Docker Compose
docker compose up
```

Wait for services to be ready (about 30-60 seconds). You'll see:
```
backend-1   | INFO:     Application startup complete.
frontend-1  | Ready in 2.3s
```

### Step 3: Access the Application

| Service | URL | Description |
|---------|-----|-------------|
| **Frontend** | http://localhost:3000 | Main application |
| **API Docs** | http://localhost:8000/docs | Interactive Swagger UI |
| **Health Check** | http://localhost:8000/health | Service status |

---

## Verify Installation

### Quick Health Check

```bash
# Check all services are running
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "healthy", "database": "connected", "redis": "connected"}
```

### Test the Frontend

1. Open http://localhost:3000
2. You should see the Oniva dashboard
3. Navigate to **Agents** to see available AI agents
4. Navigate to **Workflows** to see the visual workflow builder

### Test the API

```bash
# List available agents
curl http://localhost:8000/api/v1/agents

# Check skills registry
curl http://localhost:8000/api/v1/skills
```

---

## Test Each Feature

### 1. Test the Chat Interface

1. Go to http://localhost:3000/chat
2. Type a message like: "What can you help me with?"
3. Watch the streaming response

**Example queries to try:**
- "Analyze Apple stock performance"
- "What are the key financial metrics I should track?"
- "Help me understand P/E ratios"

### 2. Test the Workflow Builder

1. Go to http://localhost:3000/workflows
2. Click **New Workflow**
3. Drag nodes from the sidebar:
   - **Trigger** → **Input** → **Agent** → **Output**
4. Connect nodes by dragging between handles
5. Click **Execute** to run the workflow

### 3. Test Agent Creation

1. Go to http://localhost:3000/agents
2. Click **Create Agent** or clone an existing one
3. Configure:
   - Name: "My Analysis Agent"
   - Model: GPT-4o
   - Skills: Select financial, web, document skills
4. Save and test in the chat panel

### 4. Test Compliance Features

```bash
# Test OFAC Sanctions Screening
curl -X POST http://localhost:8000/api/v1/compliance/screen/sanctions \
  -H "Content-Type: application/json" \
  -d '{"name": "John Smith", "entity_type": "individual"}'

# List compliance templates
curl http://localhost:8000/api/v1/compliance/templates
```

### 5. Test the API Directly

Use the interactive API docs at http://localhost:8000/docs to:
- Try out any endpoint
- See request/response schemas
- Test with different parameters

---

## Local Development (Recommended)

Efficient commands for running the app locally without full Docker stack.

### Quick Start / Stop Commands

```bash
# === START THE APP ===
# 1. Start databases (required)
docker-compose up -d postgres redis

# 2. Wait for healthy status
docker-compose ps  # Both should show "healthy"

# 3. Start backend (Terminal 1)
cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000

# 4. Start frontend (Terminal 2)
cd frontend && npm run dev

# === STOP THE APP ===
# Press Ctrl+C in backend/frontend terminals, then:
docker-compose stop postgres redis
```

### One-Liner Commands (Copy-Paste Ready)

```bash
# Start everything (run from project root)
docker-compose up -d postgres redis && sleep 5 && \
  (cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000 &) && \
  (cd frontend && npm run dev &)

# Stop everything
docker-compose stop && pkill -f "uvicorn app.main:app" && pkill -f "next dev"

# Check status
docker-compose ps && curl -s http://localhost:8000/health | jq
```

### Fix: PostgreSQL Won't Start (postmaster.pid Error)

If you see `FATAL: bogus data in lock file "postmaster.pid"`:

```bash
# Remove stale lock file from Docker volume
docker run --rm -v oniva_postgres_data:/data alpine rm -f /data/postmaster.pid

# Restart PostgreSQL
docker-compose up -d postgres
```

### Fix: Connection Refused on Backend Start

```bash
# Ensure databases are running and healthy
docker-compose ps

# If postgres shows "Exited", check logs and restart
docker-compose logs postgres
docker-compose up -d postgres redis

# Wait for healthy status before starting backend
watch docker-compose ps  # Wait until both show "healthy"
```

### Service URLs

| Service | URL | Health Check |
|---------|-----|--------------|
| Frontend | http://localhost:3000 | Open in browser |
| Backend | http://localhost:8000 | `curl localhost:8000/health` |
| API Docs | http://localhost:8000/docs | Swagger UI |
| PostgreSQL | localhost:5432 | `docker-compose ps postgres` |
| Redis | localhost:6379 | `docker-compose ps redis` |

---

## Development Mode (Full Details)

For local development without Docker:

### Terminal 1: Start Database & Cache

```bash
# Start only PostgreSQL and Redis
docker compose up postgres redis
```

### Terminal 2: Start Backend

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Point the backend at the three DB roles (passwords from your root .env; see below).
# DATABASE_URL = oniva_app (request path), SYSTEM_DATABASE_URL = oniva_system (BYPASSRLS),
# MIGRATION_DATABASE_URL = schema owner (POSTGRES_USER). Put these in backend/.env or export them.
export DATABASE_URL=postgresql+asyncpg://oniva_app:$APP_DATABASE_PASSWORD@localhost:5432/oniva
export SYSTEM_DATABASE_URL=postgresql+asyncpg://oniva_system:$SYSTEM_DATABASE_PASSWORD@localhost:5432/oniva
export MIGRATION_DATABASE_URL=postgresql+asyncpg://oniva:$POSTGRES_PASSWORD@localhost:5432/oniva

# Migrations run automatically at startup in development (AUTO_MIGRATE defaults to true).
# To run them by hand: alembic upgrade head

# Start the server
uvicorn app.main:app --reload --port 8000
```

### Terminal 3: Start Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

---

## Running Tests

### Backend Tests

```bash
cd backend
source venv/bin/activate

# Run all tests
pytest

# Run with coverage
pytest --cov=app

# Run specific test file
pytest tests/test_agents.py -v
```

### Frontend Tests

```bash
cd frontend

# Unit tests (Jest)
npm test

# E2E tests (Playwright)
npm run test:e2e

# Watch mode for development
npm run test:watch
```

---

## Common Commands

### Docker Operations

```bash
# Start all services
docker compose up

# Start in background
docker compose up -d

# View logs
docker compose logs -f

# View specific service logs
docker compose logs -f backend
docker compose logs -f frontend

# Stop services
docker compose down

# Stop and remove volumes (clean slate)
docker compose down -v

# Rebuild containers
docker compose build
docker compose up --build
```

### Database Operations

```bash
# Connect to PostgreSQL
docker compose exec postgres psql -U oniva -d oniva

# Run migrations
docker compose exec backend alembic upgrade head

# Create new migration
docker compose exec backend alembic revision --autogenerate -m "description"

# Check migration status
docker compose exec backend alembic current
```

### Useful Shortcuts

```bash
# Quick restart backend
docker compose restart backend

# Quick restart frontend
docker compose restart frontend

# View real-time backend logs
docker compose logs -f backend | grep -v "INFO"

# Check service health
docker compose ps
```

---

## Test Users (Stub Auth Mode)

The platform includes test users for development:

| Email | Tenant | Role |
|-------|--------|------|
| `alice@acme.com` | Acme Corp | Admin |
| `bob@acme.com` | Acme Corp | User |
| `charlie@globex.com` | Globex Inc | Admin |

Select a user from the tenant dropdown in the sidebar.

---

## Troubleshooting

### Port Already in Use

```bash
# Check what's using the port
lsof -i :3000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or use different ports in docker-compose.yml
```

### Database Connection Failed

```bash
# Check PostgreSQL is running
docker compose ps postgres

# View PostgreSQL logs
docker compose logs postgres

# Restart PostgreSQL
docker compose restart postgres
```

### Backend Not Starting

```bash
# Check backend logs
docker compose logs backend

# Common fixes:
# 1. Missing API keys in .env
# 2. Database not ready - wait 10 seconds
# 3. Redis not connected

# Restart backend
docker compose restart backend
```

### Frontend Not Loading

```bash
# Check frontend logs
docker compose logs frontend

# Rebuild frontend
docker compose build frontend
docker compose up frontend
```

### Clear Everything and Start Fresh

```bash
# Nuclear option - removes all data
docker compose down -v
docker system prune -f
docker compose up --build
```

---

## Environment Variables Reference

### Required

```bash
# Database: three roles (docker compose builds these URLs for you from the passwords in .env)
POSTGRES_PASSWORD=...            # schema owner "oniva" (migrations)
APP_DATABASE_PASSWORD=...        # oniva_app    -> DATABASE_URL        (NOSUPERUSER NOBYPASSRLS)
SYSTEM_DATABASE_PASSWORD=...     # oniva_system -> SYSTEM_DATABASE_URL (BYPASSRLS)
# The role passwords are applied on the FIRST boot of an empty postgres volume. If you already
# have a volume from before this change, either `docker compose down -v` (destroys data) or run
# ALTER ROLE oniva_app PASSWORD '...' / ALTER ROLE oniva_system PASSWORD '...' (see DEPLOYMENT.md).

# Redis (auto-configured in Docker)
REDIS_URL=redis://redis:6379

# Security (generate a random key)
SECRET_KEY=your-secret-key-at-least-32-characters-long

# LLM Provider (at least one required)
OPENAI_API_KEY=sk-...
```

### Optional

```bash
# Additional LLM Providers
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...

# Financial Data APIs
ALPHA_VANTAGE_API_KEY=your-key
TAVILY_API_KEY=your-key

# Connectors
LINEAR_API_KEY=lin_api_...

# Development Mode
STUB_AUTH_ENABLED=true
RLS_ENABLED=true
DEBUG=true
```

---

## What's Running

When you run `docker compose up`, these services start:

| Service | Port | Description |
|---------|------|-------------|
| **frontend** | 3000 | Next.js 15 application |
| **backend** | 8000 | FastAPI server |
| **postgres** | 5432 | PostgreSQL 16 + pgvector |
| **redis** | 6379 | Redis 7 cache |

All services have health checks configured for reliability.

---

## Next Steps

After getting the platform running:

1. **Explore the Workflow Builder** - Create visual AI workflows
2. **Create Custom Agents** - Configure agents with specific skills
3. **Try Compliance Templates** - Run KYC/AML screening workflows
4. **Read the README** - architecture overview at the repo root
5. **Check API Documentation** - http://localhost:8000/docs

---

## Getting Help

- **API Reference**: http://localhost:8000/docs
- **In-App Docs**: http://localhost:3000/docs

### Quick Diagnostic

```bash
# Run this to check system status
echo "=== Docker Status ===" && docker compose ps
echo "=== Backend Health ===" && curl -s http://localhost:8000/health | jq
echo "=== Frontend ===" && curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
```

---

**Ready to build AI agent workflows!**
