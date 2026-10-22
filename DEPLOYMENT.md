# Oniva Deployment Guide

## Quick Start (30 minutes)

### Prerequisites

- Docker 24+ and Docker Compose 2+
- OR kubectl 1.28+ with Kubernetes cluster
- Domain name with DNS configured
- TLS certificates (or Let's Encrypt)

### Database roles (read this first)

Oniva enforces row-level security, so it runs with three Postgres roles. The app refuses to
start if `DATABASE_URL` is a superuser or BYPASSRLS role.

| Role | Used by | Attributes | Env var |
|------|---------|------------|---------|
| schema owner (`oniva` / `POSTGRES_USER`) | Alembic migrations only | owns the tables | `MIGRATION_DATABASE_URL` |
| `oniva_app` | the request path | `NOSUPERUSER NOBYPASSRLS` | `DATABASE_URL` |
| `oniva_system` | scheduler, webhooks, startup recovery, pre-tenant auth | `BYPASSRLS` | `SYSTEM_DATABASE_URL` |

Migrations `100_baseline` / `101_system_role` create `oniva_app` and `oniva_system` only if they
do not exist and never set passwords. Passwords are provisioned out-of-band:

- Fresh database via the bundled compose files / k8s manifests: `postgres/init/01-roles.sh`
  creates both roles with passwords on first boot (from `APP_DATABASE_PASSWORD` and
  `SYSTEM_DATABASE_PASSWORD`).
- Existing database or managed Postgres (RDS, Cloud SQL...), as a superuser/owner:
  ```sql
  -- run AFTER the first `alembic upgrade head` (or create the roles yourself beforehand)
  ALTER ROLE oniva_app    WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '<APP_DATABASE_PASSWORD>';
  ALTER ROLE oniva_system WITH LOGIN NOSUPERUSER BYPASSRLS   PASSWORD '<SYSTEM_DATABASE_PASSWORD>';
  ```
  On managed Postgres where you cannot grant BYPASSRLS, create `oniva_system` as the
  provider's closest equivalent and verify `SELECT rolbypassrls FROM pg_roles WHERE rolname='oniva_system'`.

Use URL-safe passwords (hex or `secrets.token_urlsafe`): they are embedded in connection URLs.
With `ENVIRONMENT=production` the app validates these at boot: weak/default passwords
(`postgres`, `password`, `changeme`, `CHANGE_ME...`, ...), a missing `SYSTEM_DATABASE_URL`, or
`DATABASE_URL` using a superuser/owner/system user all abort startup. Production runs with
`AUTO_MIGRATE=false`; the schema is migrated by a separate step as the owner.

### Option 1: Docker Compose (Simplest)

1. **Clone and configure**
   ```bash
   git clone https://github.com/yourorg/oniva.git
   cd oniva
   cp .env.example .env
   ```

2. **Edit `.env`** (generate secrets with `python backend/scripts/generate_secrets.py`)
   ```bash
   VERSION=1.0.0                      # image tag, required (no :latest)
   POSTGRES_USER=oniva                # schema owner
   POSTGRES_DB=oniva
   POSTGRES_PASSWORD=<owner-password>
   APP_DATABASE_PASSWORD=<oniva_app-password>
   SYSTEM_DATABASE_PASSWORD=<oniva_system-password>
   REDIS_PASSWORD=<redis-password>
   SECRET_KEY=<min-32-char-key>
   ENCRYPTION_SALT=<64-hex-chars>
   CORS_ORIGINS=https://app.example.com
   OPENROUTER_API_KEY=sk-or-...       # optional
   ```
   The compose file assembles `DATABASE_URL`, `SYSTEM_DATABASE_URL` and
   `MIGRATION_DATABASE_URL` from these; do not set them in `.env` for compose.

3. **Start services**
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```
   Startup order is enforced: postgres (creates roles on first boot) -> `migrate` (one-shot
   `alembic upgrade head` as the owner) -> `app` (`AUTO_MIGRATE=false`). `volume-init` makes the
   `/data` volume writable for uid 1000 because the app container has a read-only root
   filesystem; sessions, volumes and artifacts live under `/data` (`SESSION_BASE_PATH`,
   `VOLUMES_BASE_PATH`, `ARTIFACTS_BASE_PATH`).

4. **Access**: the `app` and `frontend` services publish no host ports (so `deploy.replicas`
   works); traffic enters through the `nginx` service on 80/443 (configure `nginx/nginx.conf`
   and put certs in `nginx/certs`). To debug a single container use
   `docker compose -f docker-compose.prod.yml exec app curl -s localhost:8000/health/ready`.

5. **Upgrades**: bump `VERSION`, `docker compose -f docker-compose.prod.yml up -d --build`;
   `migrate` re-runs automatically and is a no-op when already at head.

#### Existing databases (created before the Alembic baseline)

Databases that were built by the old `create_all` / ad-hoc DDL already have the schema, so
do not run the baseline against them. Stamp, then upgrade:

```bash
# one-off, as the schema owner (MIGRATION_DATABASE_URL must point at the owner)
docker compose -f docker-compose.prod.yml run --rm migrate alembic stamp 100_baseline
docker compose -f docker-compose.prod.yml run --rm migrate alembic upgrade head   # applies 101_system_role
```

Then set the role passwords with the `ALTER ROLE` statements above (the init script does not
re-run on an existing volume) and make sure the tables are owned by the owner role (not
`postgres` or `oniva_app`) so grants and RLS policies apply. Take a backup first. Only stamp a
database whose schema matches the baseline; if unsure, restore into a fresh database instead.

### Option 2: Kubernetes

Manifests are a kustomize bundle (`kubectl apply -k k8s/`).

1. **Fill in secrets** in `k8s/secrets.yaml` (or provide a `oniva-secrets` Secret through your
   secret manager with the same keys): `SECRET_KEY`, `ENCRYPTION_SALT`, `DB_PASSWORD` (owner),
   `APP_DATABASE_PASSWORD`, `SYSTEM_DATABASE_PASSWORD`, `REDIS_PASSWORD`, optional LLM keys.
   Set `CORS_ORIGINS` in `k8s/configmap.yaml`, the host in `k8s/ingress.yaml`, and the
   `storageClassName` of `oniva-data-pvc` in `k8s/pvc.yaml` (it must support ReadWriteMany
   because all API replicas share sessions on `/data`).

2. **Pin images**: edit `images:` in `k8s/kustomization.yaml` (or
   `cd k8s && kustomize edit set image oniva=registry/oniva:1.0.0 oniva-frontend=registry/oniva-frontend:1.0.0`).
   Tags are never `latest`.

3. **Apply**
   ```bash
   kubectl -n oniva delete job oniva-migrate --ignore-not-found   # Jobs are immutable
   kubectl apply -k k8s/
   ```
   The `oniva-migrate` Job runs `alembic upgrade head` as the owner; API pods have an init
   container that waits until the database is at head, and run with `AUTO_MIGRATE=false`.
   The postgres pod creates `oniva_app` / `oniva_system` with their passwords on first boot
   (init script from the `postgres-init` ConfigMap, a copy of `postgres/init/01-roles.sh`).
   `$(VAR)` expansion in env values only sees variables declared earlier in `env` (not
   `envFrom`), which is why secrets are listed explicitly before the URLs that use them.

4. **Probes**: liveness `/health/live`, readiness `/health/ready` (checks the database).

5. **Existing database**: stamp before the Job runs
   ```bash
   kubectl -n oniva run alembic-stamp --rm -it --restart=Never --image=<your oniva image> \
     --env="MIGRATION_DATABASE_URL=postgresql+asyncpg://oniva:<owner-pw>@postgres:5432/oniva" \
     -- alembic stamp 100_baseline
   ```
   then `kubectl apply -k k8s/`, and set the role passwords with `ALTER ROLE` (see above).

6. **Verify**
   ```bash
   kubectl get pods,jobs -n oniva
   kubectl logs -n oniva job/oniva-migrate
   kubectl logs -n oniva -l app=oniva-api
   ```

Redis is a single instance with `--requirepass`. For HA see `k8s/redis-sentinel.yaml` (not part of
the kustomization; it needs its own password wiring).

### Option 3: AWS with Terraform

1. **Configure AWS CLI**
   ```bash
   aws configure
   ```

2. **Initialize Terraform**
   ```bash
   cd terraform/aws
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars with your values
   terraform init
   ```

3. **Review and apply infrastructure**
   ```bash
   terraform plan
   terraform apply
   ```

4. **Push images to ECR**
   ```bash
   # Get ECR URLs from terraform output
   ECR_API=$(terraform output -raw ecr_api_url)
   ECR_FRONTEND=$(terraform output -raw ecr_frontend_url)

   # Login to ECR
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $ECR_API

   # Build and push images
   docker build -t $ECR_API:latest -f backend/Dockerfile.prod backend/
   docker push $ECR_API:latest

   docker build -t $ECR_FRONTEND:latest -f frontend/Dockerfile.prod frontend/
   docker push $ECR_FRONTEND:latest
   ```

5. **Deploy ECS service** (manual or CI/CD)

## Post-Deployment

### Health Check

```bash
curl https://your-domain.com/health
# Expected: {"status": "healthy", "version": "1.0.0", ...}
```

### Test Authentication

```bash
# Get stub users (dev only)
curl https://your-domain.com/api/v1/auth/stub-users

# Login
curl -X POST https://your-domain.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@acme.local", "password": "demo123"}'
```

### View Logs

```bash
# Docker
docker compose -f docker-compose.prod.yml logs -f app

# Kubernetes
kubectl logs -n oniva -l app=oniva-api -f

# AWS CloudWatch
aws logs tail /ecs/oniva-production --follow
```

### Run Database Migrations

```bash
# Docker (re-run the one-shot migrate service as the schema owner)
docker compose -f docker-compose.prod.yml run --rm migrate

# Kubernetes (re-create the Job)
kubectl -n oniva delete job oniva-migrate --ignore-not-found && kubectl apply -k k8s/
```

#### Webhook secrets: expand now, contract later

`121_encrypt_webhook_secrets` is the expand step of an expand/contract rollout. It adds `secret_enc` /
`key_enc` (Fernet) to every webhook trigger's `auth_config` and **keeps** the plaintext `secret` / `key`, so
pods still running the previous release, or a code rollback, keep verifying signatures (previous code reads
an absent plaintext as an empty key, which fails open). The migration decrypts what it wrote and compares it
to the original; it fails if they differ, so run the migrate job with the same `SECRET_KEY` and
`ENCRYPTION_SALT` as the API.

- Runtime reads `*_enc` first and falls back to the plaintext.
- New triggers write both `*_enc` and the plaintext until the contract step, so a rollback to the previous
  release still works for triggers created in between.
- `alembic downgrade 120_engine_runs` removes the `*_enc` keys again.
- A later **cutover migration** drops the plaintext `secret` / `key` and stops writing them. Ship it only
  once no old pods or rollback targets remain.

Until that cutover, plaintext webhook secrets are still at rest in `webhook_triggers.auth_config`.

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | `oniva_app` URL (non-superuser, RLS enforced) |
| `SYSTEM_DATABASE_URL` | Yes (prod) | `oniva_system` URL (BYPASSRLS) |
| `MIGRATION_DATABASE_URL` | For migrations | Schema-owner URL used by Alembic |
| `AUTO_MIGRATE` | No | `true` in dev, `false` in prod (migrate out-of-band) |
| `APP_DATABASE_PASSWORD` | Yes (prod) | `oniva_app` password (weak values rejected) |
| `ENCRYPTION_SALT` | Yes (prod) | 64 hex chars |
| `CORS_ORIGINS` | Yes (prod) | Explicit origins, never `*` |
| `SESSION_BASE_PATH` | No | Writable session dir (`/data/sessions` with read-only rootfs) |
| `REDIS_URL` | Yes | Redis connection string |
| `SECRET_KEY` | Yes | JWT signing key (min 32 chars) |
| `DEBUG` | No | Enable debug mode (default: false) |
| `ENVIRONMENT` | No | Environment name (default: production) |
| `OPENAI_API_KEY` | No | OpenAI API key |
| `ANTHROPIC_API_KEY` | No | Anthropic API key |
| `OPENROUTER_API_KEY` | No | OpenRouter API key |

### Resource Requirements

| Service | CPU | Memory | Storage |
|---------|-----|--------|---------|
| API | 2 cores | 4GB | - |
| Frontend | 1 core | 2GB | - |
| PostgreSQL | 2 cores | 4GB | 100GB+ |
| Redis | 0.5 cores | 1GB | 10GB |

### Scaling

```bash
# Docker Compose (replicas)
docker compose -f docker-compose.prod.yml up -d --scale app=4

# Kubernetes
kubectl scale -n oniva deployment/oniva-api --replicas=4
```

## Engine (durable agent runtime)

The engine is a separate Node worker (`engine/`, image built from `engine/Dockerfile.prod`) with its
own Postgres database. One worker serves one `ENGINE_SESSION_ID`, so run exactly one replica
(`Recreate` rollout in k8s). It is optional: the backend routes stay off until `ENABLE_ENGINE_API=true`.

| Variable | Where | Meaning |
|----------|-------|---------|
| `ENGINE_GATEWAY_TOKEN` | backend + engine | Bearer for `/internal` (engine -> backend). `/internal` is mounted only when set |
| `ENGINE_SERVICE_TOKEN` | backend + engine | Bearer the backend presents to the engine |
| `ENGINE_GATEWAY_TOKEN_PREVIOUS` | backend | Old gateway token, accepted only during rotation |
| `ENGINE_URL` / `ENGINE_SESSION_ID` | backend | Where the worker is and which session it serves |
| `ENABLE_ENGINE_API` | backend | Exposes `/api/v1/engine/conversations` (default `false`) |
| `ENABLE_ENGINE_CHAT` | backend | Global ceiling / kill switch for engine chat (default `false`). On: engine chat is on for every tenant unless it opts out |
| `ENABLE_ENGINE_DEEP_WORK` | backend | Global ceiling / kill switch for engine Deep Work (default `false`). On: Deep Work on the engine for every tenant unless it opts out |
| `INTERNAL_ALLOW_PLAINTEXT` | backend | Allows an `http://` `ENGINE_URL` in production (in-cluster / compose network only) |
| `DATABASE_URL` | engine | The engine's own Postgres (`ENGINE_DATABASE_URL` secret in k8s, `engine-db` in compose) |
| `ENGINE_GATEWAY_URL`, `ENGINE_POLICY=backend`, `ENGINE_POLICY_URL` | engine | Point at the backend; the worker holds no model key |

The three tokens must be distinct, at least 32 characters, and different from `SECRET_KEY`; production
validation rejects equal tokens, a missing token with `ENABLE_ENGINE_API` on, and a plaintext
`ENGINE_URL` without the opt-in.

### Engine rollout: opt-out and spend control

Engine chat and engine Deep Work are **on by default** once the globals are set. The globals
(`ENABLE_ENGINE_API`, `ENABLE_ENGINE_CHAT`, `ENABLE_ENGINE_DEEP_WORK`) plus a reachable `ENGINE_URL`
and `ENGINE_SERVICE_TOKEN` are the ceiling and the kill switch: turning any of them off disables the
engine paths for every tenant at once, whatever a tenant says.

A tenant's setting is an **opt-out**, not an opt-in. A tenant with no `features` entry for a flag
gets the engine path; set `features.ENABLE_ENGINE_CHAT=false` or
`features.ENABLE_ENGINE_DEEP_WORK=false` to exclude one tenant. `get_tenant_features` returns the
same resolved value the routes use, so the frontend hides the engine UI exactly when a tenant is
opted out.

Spend is bounded by the per-tenant LLM token quota, which runs in the model policy for every engine
request (chat and Deep Work workers alike). Set one directly in the system database:

```sql
INSERT INTO usage_quotas
  (id, tenant_id, scope_type, scope_id, quota_type, limit_value, current_usage,
   period, period_start, period_end, warning_threshold, is_active)
VALUES
  (gen_random_uuid(), '<tenant-uuid>', 'tenant', '<tenant-uuid>', 'llm_tokens', 5000000, 0,
   'monthly', now(), now() + interval '30 days', 0.8, true)
ON CONFLICT DO NOTHING;
```

A request over the quota is denied `quota_exceeded` and the run fails fast with a readable reason;
no model key is ever returned for a denied request.

### Load gate (U36): required before the U29 flip

Fleet and workflows stay default-off until one full-profile run of the automation load gate has passed on a
proper runner and its result is recorded here. The gate answers one question: can automation runs and chat share
one engine session without hurting chat?

**Trigger it**: GitHub, Actions, "load-gate", "Run workflow" (`.github/workflows/load-gate.yml`, manual only, not in
CI). It boots the same stack as the e2e `engine-smoke` job (Postgres 16 with pgvector, one engine worker), with the
backend run production-like by default: schema from `alembic upgrade head`, the app connecting as the non-bypass
`oniva_app` role (system sessions as `oniva_system`, RLS on), and `backend_workers` uvicorn worker processes on one port
(production: gunicorn `-w 4`, `Dockerfile.prod`). `backend_db` = `sqlite` restores the old single-process SQLite backend.
Inputs: `engine_shards` (`1` = one engine worker, the default; `2` = sharded, see "Engine sharding" below), `profile` (`full` = 200 workflow runs of 10 nodes + 20 Fleet runs, 60 baseline chat samples, at least 60 loaded
chat samples; `smoke` = 5 + 1), `runner` (a GitHub-hosted label, default `ubuntu-latest`; use a larger-runner label
for a realistic number), and optional overrides `workflow_runs`, `fleet_runs`, `baseline_samples`,
`min_loaded_samples`, `chat_interval_s`, `start_threads`, `drain_timeout_s` (blank keeps the profile default).
For U29 evidence leave every override blank: an overridden run is named `<profile>+custom` and does not count.
Locally: `cd backend && ONIVA_E2E=1 uv run python -m tests.load.automation_load --profile full --json-out r.json`
(SQLite backend). Production-like locally: add `ONIVA_E2E_BACKEND_DB=postgres ONIVA_E2E_BACKEND_WORKERS=4` and a Postgres
with pgvector (`ONIVA_E2E_PG_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres`, e.g. a throwaway
`pgvector/pgvector:pg16` container; the harness creates and drops its own databases but sets passwords on the
cluster-wide `oniva_app` / `oniva_system` roles, so never point it at a shared dev cluster).
Both env vars are opt-in for any e2e test; unset, the stack is unchanged.

**Passing** (the job is green and the summary says PASS), all of:

- chat first-token p95 under load regressed by no more than 20% against the no-load baseline;
- no run left non-terminal after the drain, and no run failed or cancelled;
- no chat turn errored or timed out, and at least `min_loaded_samples` chat turns ran while automation was active;
- the counting HTTP target saw exactly one started and one finished call per workflow run;
- usage ledger rows equal the model requests made.

Any failed condition fails the job and is listed in the summary. A failed gate means: stop, open the sharding
follow-up (per-tenant or per-kind sessions through `session_for`) and ship it before U29.

**Outputs**: the markdown summary on the run page (`$GITHUB_STEP_SUMMARY`) and an artifact `load-gate-<profile>-<run id>`
with `result.json` (full numbers: first-token p50/p95, commit-gap p50/p95/p99, worker CPU and memory, outbox depth,
drain lag, ledger), `summary.md` and `console.log`. Commit-gap and drain lag are proxies (the stack cannot observe
per-commit latency or reconciler lag directly).

**For U29**: paste the summary (numbers, hardware, date, PASS) into this section, with the run URL, in the U29 PR.

**What the numbers mean** (first red run, 2026-10-22, 4-CPU runner): worker CPU alone does not show saturation, because the
backend, Postgres and the driver share the runner. Chat is protected in two places: the engine schedules chat ahead of
automation (task lanes: `agent.lane` / task kind `lane`, `ENGINE_INTERACTIVE_RESERVE` slots that background work cannot
take, `ENGINE_TASK_CONCURRENCY`; Fleet and workflow runs are started `background`), and the backend no longer rebuilds the
tool manifest per chat submission. The driver lifts the Fleet agent's `timeout_seconds` because it advances the engine
clock 31 s per drain iteration, which would otherwise exhaust a 600 s Fleet deadline (`the run deadline has passed`).
A failed run's reason is listed under the summary.

Multi-worker notes (Postgres backend): the run reconciler is leader-only through `pg_try_advisory_lock`, tool and
workflow-node idempotency, approvals and trace recording rely on row locks, and the per-process caches (`_manifest_cache`,
`_decisions`, `_applied`, `_approval_gates`) are only caches. Nothing else in the e2e path is process-local.

**Engine sharding** (the follow-up this gate asked for). Measured on the failing gate (12-CPU Mac, Postgres, 2 backend
workers, single engine session): the session's serialized commit line was 90-100% occupied under load (about 130
commits/s, 5 ms each), so a chat turn's roughly six sequential commits each waited a median 30-50 ms (p95 80-160 ms) for
its turn behind automation commits. Event-loop lag (p99 about 8 ms, utilization 50%) and the Postgres pool (no wait) were
not the cause. One worker serves one session, so the fix is a second session: set `ENGINE_SESSION_ROLE=interactive` on
the worker for the chat session and `ENGINE_SESSION_ROLE=background` on a second worker with its own
`ENGINE_SESSION_ID`, then give the backend `ENGINE_BACKGROUND_URL` and `ENGINE_BACKGROUND_SESSION_ID` (both or neither;
the id must differ from `ENGINE_SESSION_ID`). Workflow, Fleet and batch runs are then created on the background worker
(`EngineDirectory.session_for`), everything else stays on the first. Nothing changes unless configured. A conversation is
bound to one session when it is created (`engine_conversations.session_id`) and every later call uses that binding, so
turning sharding on or off never moves an existing conversation; roles refuse a lane they do not serve (`409
wrong_session_role`) and ids are unique across sessions in storage, so no conversation is owned by two workers. Each
session keeps its own lease, fence token and crash recovery. Locally: `ONIVA_E2E_ENGINE_SHARDS=2`.

**Recorded result**: none yet.

**`/internal` is never public.** nginx answers 404 for `/internal` (`nginx/nginx.conf`), the k8s ingress
carries the same rule as a server snippet and has no route for it, the engine Service is a ClusterIP,
and a NetworkPolicy admits only API pods to the engine. The engine reaches `oniva-api:8000` /
`app:8000` directly inside the cluster or compose network. If your ingress controller disables
snippets, enforce the block in your edge proxy instead.

**Rotating `ENGINE_GATEWAY_TOKEN`**: set the old value as `ENGINE_GATEWAY_TOKEN_PREVIOUS` and the new
one as `ENGINE_GATEWAY_TOKEN` on the backend, roll the backend, update the engine's
`ENGINE_GATEWAY_TOKEN` and roll it, then remove `ENGINE_GATEWAY_TOKEN_PREVIOUS`. Rotate
`ENGINE_SERVICE_TOKEN` by rolling backend and engine together (a short window of 502s on the
engine routes).

**Smoke test**: `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e/test_engine_smoke.py` (needs Node 22, `initdb`/`pg_ctl`).

### Deep Work subagent contract v2 (`ENABLE_ENGINE_SUBAGENT_V2`)

Off by default (flags and rollout plan: `docs/plans/2026-10-21-001-...`, Phase C, U22). With the flag off, the
Deep Work execute config and every prompt are byte-identical to before. With it on, the execute config:

- gives the research worker and the reflector an `outputSchema` (the worker's is the same
  `summary` / `learning_count` / `source_count` / `top_findings` / `sources` object the Deep Work card has always
  parsed) so they end by calling `complete_task`;
- passes each worker a `contextPreamble` listing the other workers' sub-queries, and tells the orchestrator to pass
  `label = worker_id` on every `research_worker` call and a `context.brief` on gap rounds;
- adds the `verifier` subagent (tools `dw_get_report`, `dw_get_learnings` only), run once before `dw_finalize`,
  and the `steer_worker` tool for the orchestrator;
- with `fork` on, runs the reflector in `mode: "fork"` (it sees the parent's recent conversation, bodies elided).

The flag resolves like the others: tenant `features.ENABLE_ENGINE_SUBAGENT_V2` first, then the global setting.
`ENGINE_SUBAGENT_V2_SWITCHES` (default `typed_results,verifier,steer_worker,fork`) narrows which pieces a tenant
with the flag on gets. The config is read when a run starts, so running conversations keep their config.

**The engine worker must be started to match** (the engine only runs tools its worker registered, so a tool the
config names but the worker lacks cannot be used):

```bash
ENGINE_SUBAGENT_TOOLS=research_worker,reflector,output_agent,verifier \
ENGINE_COMPLETE_TASK=1 \
ENGINE_STEER_WORKER=1 \
npm run worker
```

Roll the engine workers first (the new names are inert while the flag is off), then turn the flag on. To roll back,
turn the flag off; new runs use the legacy config and in-flight runs finish with theirs. The default flips only
after Gate C (U24).

### Context tiers: rollout and kill switch

Tiered context reduction keeps long conversations small: Tier 0 saves a big tool result to the conversation
workspace and leaves a preview and a pointer, Tier 1 shows old tool results as one-line stubs (view only; stored
entries never change), Tier 2 writes structured summaries when a conversation does compact. **It ships off.** Nothing
changes until `ENGINE_CONTEXT_TIERS` is raised, and it is raised only after Gate B passes (below).

| Variable | Default | Meaning |
|----------|---------|---------|
| `ENGINE_CONTEXT_TIERS` | `off` | `off`, `offload`, `offload+clear` or `full` (each includes the one before). An unknown value means `off` |
| `features.ENABLE_ENGINE_CONTEXT_TIERS` | absent | Per-tenant override in the tenant's `features`. `true` gives that tenant `full`, `false` gives it `off`, in both cases over the global value |
| `ENGINE_CONTEXT_OFFLOAD_TOKENS` | `8000` | A tool result above this is saved out of context |
| `ENGINE_CONTEXT_CLEAR_FRACTION` | `0.5` | Clearing starts at this fraction of the model's context window |
| `ENGINE_CONTEXT_KEEP_TOOL_RESULTS` | `5` | The newest tool results are never cleared |
| `ENGINE_CONTEXT_CLEAR_AT_LEAST_TOKENS` | `8000` | A clearing pass must free at least this much |

The tier is read when a conversation's agent config is built (creation, or `PUT .../agent`). **Existing
conversations keep their behaviour until they are reconfigured**, in both directions.

**Kill switch.** To turn the tiers off for new conversations:

1. Find tenants that opted in and set the flag to `false` (or remove the key so they follow the global value). A
   tenant flag wins over the global, so `ENGINE_CONTEXT_TIERS=off` alone does not switch off a tenant with the flag
   set to `true`:

   ```sql
   SELECT id, name FROM tenants WHERE settings->'features'->>'ENABLE_ENGINE_CONTEXT_TIERS' = 'true';
   ```

2. Set `ENGINE_CONTEXT_TIERS=off` on the backend and roll it.
3. Reconfigure running conversations if they must stop at once. Turning clearing off restores verbatim tool results
   in the next request; results already offloaded stay in the workspace and the model can still read them with
   `read_file`. Nothing is deleted.

**Rollout order.** Gate B decides which tiers ship; a tier that fails its criteria stays off while the others go on.

1. Run the long-session suite: `cd backend && uv run --env-file .env python -m evals.cli run --suite long_session --mode live -k 3 --max-usd 30`
   (three arms: `off`, `offload+clear`, `full`; see `TESTING.md` for the eval setup). Then run the regression suite
   once per arm under test with `--tiers <arm>` and read everything with `python -m evals.cli gate-b <report>`.
   The live run spends real credit; the key is the separate eval key, never a deployed one.
2. On a pass the gate records `evals/baselines/phaseB-<date>.json`.
3. Raise `ENGINE_CONTEXT_TIERS` one step at a time (`offload`, then `offload+clear`, then `full`) on an internal
   environment first. The global value applies to every tenant of that environment; only `full` can be given to a
   single tenant (through the flag), so the two intermediate steps are an environment-wide change, not an opt-in.
4. Watch, after each step: engine run failures (`context_overflow`, `compaction_failed`), calls to
   `/internal/workspace/offload` that fail (the engine keeps the full result when the store fails, so a failing
   store looks like no effect, not an error), and cost per conversation. Roll back with the kill switch above.

### Project memory: rollout order and kill switches

Project memory lets a project remember across conversations: `core.md` and a file index are shown at the start of
every chat, the model has five `memory_*` tools, Deep Work gets a playbook of short lessons, and a curator proposes
lessons after a run. **It ships off at three independent levels, and all three must be on for anything to happen.**

| Switch | Default | Where | Effect when off |
|--------|---------|-------|-----------------|
| `ENABLE_PROJECT_MEMORY` (global) and `features.ENABLE_PROJECT_MEMORY` (per tenant, wins over the global) | `false` | backend env / tenant `features` | no memory tools, no injected block, no playbook, API answers 404, a tool call is refused |
| project `settings.memory.enabled` | `false` | each project | the same, for that project only |
| `MEMORY_CURATOR_MODE` | `off` | backend env | no curation: the Deep Work trigger, the chat sweep and `prepare` all do nothing |

The curator also needs the engine worker to register its task: set `ENGINE_MEMORY_CURATE=1` (with `ENGINE_GATEWAY_URL`
set) or `ENGINE_MEMORY_BACKEND_URL` on the worker. Without it the backend sees no `memory_curate` task kind and does not
start one. Related limits: `MEMORY_CURATOR_DAILY_BUDGET_USD` (per tenant per UTC day, default 1.0),
`MEMORY_CURATE_MAX_RUNS_PER_TENANT_DAY`, `MEMORY_CURATE_MIN_TURNS`, `MEMORY_CURATE_IDLE_S`,
`MEMORY_RETENTION_DAYS` (pending rows nobody approved are swept) and `MEMORY_BULLET_IDLE_RUNS`.

**What the curator may do.** Curator modes: `propose` makes every result `pending` (invisible to the model until a person
approves it in the project's Memory tab); `auto` lets results from an untainted source be `active`. A tainted source
(its conversation read web pages, files, connector output or knowledge-base text) can only add `pending` rows in either
mode, never revise, retire or bump existing ones. Every Deep Work run is tainted (the reflector reads web-derived
learnings), so for Deep Work `auto` behaves like `propose`: a playbook reaches a Deep Work prompt only after a person
approves its bullets. A model's own `memory_*` write needs an approval when its conversation is tainted and is refused
when nobody can approve it.

**Rollout order.** Deploy order is engine first, then backend, then the flags (as for the other engine features).

1. Run the memory suite: `cd backend && uv run --env-file .env python -m evals.cli run --suite memory --mode live -k 3 --max-usd 40 --out memory.json`,
   then the regression suite with the flag on, `... run --suite regression --mode live -k 3 --memory manual --out regression-memory.json`,
   and read everything with `python -m evals.gate_d memory.json --regression-baseline <Gate A regression report> --regression-memory regression-memory.json`.
   The live run spends real credit (see `TESTING.md` for the eval key). Any poisoning, deletion or isolation failure keeps
   `ENABLE_PROJECT_MEMORY` off. Fake mode (`--mode fake`, no key, no cost) checks the mechanics only and is never a decision.
2. Manual tools and injection for an internal tenant: set `features.ENABLE_PROJECT_MEMORY=true` on that tenant and
   `settings.memory.enabled=true` on its projects. `MEMORY_CURATOR_MODE` stays `off`.
3. Curator in `propose` for about a week: `MEMORY_CURATOR_MODE=propose` and `ENGINE_MEMORY_CURATE=1` on the worker. Review the
   pending queue; look at what gets flagged (URLs, e-mail addresses, tool names, imperatives) and at the curator's daily spend.
4. Curator in `auto` (only untainted chats can produce active rows), then tenant opt-in. The default stays off until
   the decision is made on Gate D data.

**Kill switches, fastest first.**

- `MEMORY_CURATOR_MODE=off` on the backend: curation stops at once (an in-flight `memory_curate` run applies nothing: `apply`
  re-checks the mode). Nothing is deleted.
- Per tenant: set `features.ENABLE_PROJECT_MEMORY` to `false`. The tenant flag wins over the global, so
  `ENABLE_PROJECT_MEMORY=false` alone does not switch off a tenant that has it set to `true`:

  ```sql
  SELECT id, name FROM tenants WHERE settings->'features'->>'ENABLE_PROJECT_MEMORY' = 'true';
  ```

- Per project: set `settings.memory.enabled` to `false`.
- The memory block, the playbook and the tool grants are decided when a conversation's agent config is built, so a
  running conversation keeps its block until it is reconfigured (`PUT .../agent`) or a new conversation is opened. Tool
  calls themselves are refused once the tenant flag is off (tenant flags are cached for up to five minutes).
- Data: `DELETE /api/v1/projects/{id}/memory` (admins) erases every memory file and bullet of the project, pending ones
  included; deleting the project does the same. `POST .../memory/export` gives the owner a copy first.

**Known gap (U31).** `deep_work_run_sync.enqueue_curation` runs only from the run reconciler's `on_finished` hook, which looks
at Deep Work runs still `running`. `dw_finalize` marks a run `completed` inside the tool call, so a run that finishes
normally is never offered to that hook and the Deep Work curator does not start; the chat sweep is unaffected. The eval
harness fires the hook itself (`/e2e/memory/deep-work-completed`); `backend/tests/e2e/test_memory_curation_e2e.py` carries
an expected-failure test that turns green when the trigger is fixed. Until then, the `propose` step above covers chats only.

## Approvals and audit

A tool call that needs a human is decided by the Python control plane under RBAC; the engine only parks the
call and waits. Nothing here needs a new service, but a few settings and one runbook step matter. The three tenant
settings are read and changed with `GET`/`PUT /api/v1/tenant/approvals`.

| Setting | Where | Meaning |
|---------|-------|---------|
| `approvals.ttl_hours` | tenant settings | How long an approval waits (default `168`, 7 days; `0` never expires). The backend's answer sets the engine item's expiry, so `ENGINE_APPROVAL_TTL_MS` is ignored under `ENGINE_POLICY=backend` |
| `approvals.require_separate_approver` | tenant settings | `true` forbids deciding your own request everywhere. Admin-level tools (`execute_python`, connector writes) already need a second admin or owner whenever the tenant has two or more |
| `approvals.rule_of_two` | tenant settings (owner only) | Default `true`: once a run has read untrusted content, an outside-world write needs approval (or is blocked when no user owns the run) |
| `AUDIT_KMS_BACKEND` | backend | Signer for the audit chain. `local` (RSA keys under `SESSION_BASE_PATH/.audit_keys`) is refused when `ENVIRONMENT=production`; configure a real KMS backend there |
| `APPROVALS_LEGACY_CUTOFF` | backend | ISO timestamp. Approvals the engine created before the ledger existed have no ledger row; the owner or a tenant admin may still decide them if they were requested before this time. Unset disables that fallback |

**Runbook, upgrading the engine to the approvals release.**

1. Deploy the backend first. Migrations `110` to `113` run as the schema owner (see "Database roles").
2. Note the time the new engine image will start (the U5 deploy time) and set `APPROVALS_LEGACY_CUTOFF` to it on the backend.
   Approvals requested before then stay decidable; anything newer without a ledger row is refused with `409 approval_not_indexed`.
3. Roll the engine. One release later, remove `APPROVALS_LEGACY_CUTOFF` (the fallback is deleted in code at the same time).

**The audit log is append-only at the database.** `compliance_audit_logs` has `UPDATE`, `DELETE` and `TRUNCATE`
revoked from `oniva_app`, and a trigger rejects them for every role except two guarded purges, which run only as
`oniva_system` inside a transaction that sets a local GUC:

- `SET LOCAL oniva.audit_purge = 'retention'` deletes rows past `retention_until`;
- `SET LOCAL oniva.audit_purge = 'tenant_delete'` plus `oniva.audit_purge_tenant = '<tenant uuid>'` deletes one tenant's rows
  (the `ON DELETE CASCADE` from `tenants`).

Run retention jobs and tenant deletion as `oniva_system`; as `oniva_app` they fail by design. Deleting a user is allowed
(`user_id` is set to `NULL`); the entry keeps the `actor_email` and `actor_name` snapshot. Check a tenant's chain with
`GET /api/v1/audit/chain/verify`.

**Proof.** `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e/test_approval_restart.py` kills the worker and the backend with an
approval pending, shows only an authorised admin can decide it, and verifies the signed chain afterwards.
`cd frontend && ONIVA_E2E_BROWSER=1 npx playwright test approval-recovery` does the same through the browser.

## Local development

Run the full stack (Postgres, Redis, backend, engine worker, frontend) on one machine, with engine
chat and engine Deep Work on. Needs Docker, Python 3.12 with `uv`, and Node 22. Every value in angle
brackets is a placeholder you generate (`python backend/scripts/generate_secrets.py`, or any
URL-safe random string). Do not commit the `.env` files.

### 1. Postgres and Redis

Compose reads a root `.env` (not `backend/.env`). Create `.env` in the repo root:

```bash
POSTGRES_DB=oniva
POSTGRES_USER=oniva                 # schema owner
POSTGRES_PASSWORD=<owner-password>
APP_DATABASE_PASSWORD=<oniva_app-password>
SYSTEM_DATABASE_PASSWORD=<oniva_system-password>
REDIS_PASSWORD=<redis-password>
```

Start only the two infrastructure services. Compose also interpolates the `backend` service's
required variables even when that service is not started, so pass throwaway values for them:

```bash
SECRET_KEY=x REDIS_URL=x docker compose up -d postgres redis
```

On first boot `postgres/init/01-roles.sh` creates `oniva_app` and `oniva_system` with the passwords
above (see "Database roles"). An existing `postgres_data` volume is not re-initialised: run
`docker compose down -v` first if the passwords changed.

### 2. Engine database

The engine keeps its own database, owned by the schema owner. Use the container name from
`docker compose ps` (usually `oniva-ai-postgres-1`):

```bash
docker exec <postgres-container> psql -U oniva -d postgres -c "CREATE DATABASE oniva_engine OWNER oniva"
```

The worker migrates its own tables at start-up, so there is nothing else to run for it.

### 3. Backend

Create `backend/.env` (start from `backend/.env.example`; set the values below, the rest can stay):

```bash
ENVIRONMENT=development
SECRET_KEY=<min-32-char-key>
ENCRYPTION_SALT=<64-hex-chars>
REDIS_URL=redis://:<redis-password>@localhost:6379
CORS_ORIGINS=http://localhost:3000,http://localhost:3001   # comma-separated, not JSON

# Three roles (see "Database roles"); same passwords as the root .env
DATABASE_URL=postgresql+asyncpg://oniva_app:<oniva_app-password>@localhost:5432/oniva
SYSTEM_DATABASE_URL=postgresql+asyncpg://oniva_system:<oniva_system-password>@localhost:5432/oniva
MIGRATION_DATABASE_URL=postgresql+asyncpg://oniva:<owner-password>@localhost:5432/oniva

# Engine (tokens: distinct, 32+ characters, different from SECRET_KEY)
ENABLE_ENGINE_API=true
ENABLE_ENGINE_CHAT=true
ENABLE_ENGINE_DEEP_WORK=true
ENGINE_URL=http://127.0.0.1:8787
ENGINE_SESSION_ID=<any-stable-id, e.g. local-dev>
ENGINE_SERVICE_TOKEN=<service-token>
ENGINE_GATEWAY_TOKEN=<gateway-token>
```

`.env.example` also lists `DBOS_DATABASE_URL` and a postgres-superuser `DATABASE_URL`; the values
above replace them. Run Alembic as the schema owner (the app roles cannot create tables), then start
the API:

```bash
cd backend
DATABASE_URL=$MIGRATION_DATABASE_URL uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --port 8000
```

`$MIGRATION_DATABASE_URL` must be exported in your shell for the first command (or paste the owner
URL in its place). Check `curl localhost:8000/health`.

### 4. Engine worker

One worker serves the one `ENGINE_SESSION_ID` the backend is configured with. It holds no model key:
it asks the backend for the provider, model and key on every request. `ENGINE_SERVICE_TOKEN` and
`ENGINE_GATEWAY_TOKEN` must equal the backend's values. In a second terminal:

```bash
cd engine
npm install
DATABASE_URL=postgresql://oniva:<owner-password>@localhost:5432/oniva_engine \
ENGINE_HOST=127.0.0.1 \
ENGINE_PORT=8787 \
ENGINE_SESSION_ID=<same-session-id-as-backend> \
ENGINE_SERVICE_TOKEN=<service-token> \
ENGINE_GATEWAY_TOKEN=<gateway-token> \
ENGINE_GATEWAY_URL=http://127.0.0.1:8000 \
ENGINE_POLICY=backend \
ENGINE_POLICY_URL=http://127.0.0.1:8000 \
ENGINE_SUBAGENT_TOOLS=research_worker,reflector,output_agent \
npm run worker
```

The worker migrates, takes the session lease, then serves; `ENGINE_SUBAGENT_TOOLS` registers the
Deep Work workers. Variables are listed in `engine/README.md` ("Running a worker"). Exit code `2`
means bad configuration, `3` a lost lease (restart it).

### 5. Frontend

```bash
cd frontend
cp .env.local.example .env.local     # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open http://localhost:3000.

### 6. First tenant and owner

A fresh database has no tenants and sign-up is invite-only: `/api/v1/auth/signup` needs a row in
`pending_users`, which an existing admin creates. The platform-admin API only toggles features for
tenants that already exist. So create the first tenant and an owner directly with the app's models,
once. This runs as `oniva_system` (BYPASSRLS), from `backend/`:

```bash
uv run --env-file .env python - <<'PY'
import asyncio, os
from datetime import datetime
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models.tenant import Tenant
from app.models.user import User
from app.services.user_management import UserManagementService

async def main():
    engine = create_async_engine(os.environ["SYSTEM_DATABASE_URL"])
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        tenant = Tenant(name="Local Dev", slug="local-dev", settings={"signup_mode": "invite_only"})
        db.add(tenant)
        await db.flush()
        db.add(User(
            tenant_id=tenant.id,
            email="owner@example.com",
            name="Local Owner",
            hashed_password=UserManagementService.hash_password("<Password-1!>"),  # upper, lower, digit, special; 8+ chars
            role="owner",
            auth_provider="local",
            is_active=True,
            is_superuser=True,          # platform operator: can use /api/v1/admin/tenants/... 
            password_changed_at=datetime.utcnow(),
        ))
        await db.commit()
        print("tenant", tenant.id)
    await engine.dispose()

asyncio.run(main())
PY
```

Use any email that does not end in `.local` (those addresses are routed to the development stub
users). Sign in at http://localhost:3000/login with that email and password. Invite further users
from the app's user management; they finish through `/signup`.

**Engine features.** Nothing to set per tenant: with the three `ENABLE_ENGINE_*` globals on, engine
chat and engine Deep Work are on for the new tenant (see "Engine rollout: opt-out and spend
control"). To exclude it, set `settings.features.ENABLE_ENGINE_CHAT=false` and/or
`ENABLE_ENGINE_DEEP_WORK=false` for the tenant (as the platform operator,
`PUT /api/v1/admin/tenants/<tenant-uuid>/features/<flag>`).

### 7. Add a model provider key

Keys are never put in env files for the engine: add one for the tenant in the UI. Go to
**Settings -> LLM Providers** (`/settings/llm-providers`), choose **New**, pick the provider (for
example OpenRouter), paste your own API key into **API Key**, set the **Default Model**, and leave
**Enable Provider** on. The backend stores it encrypted (with `ENCRYPTION_SALT`) and hands it to the
engine per request through `/internal/models/policy`. Then open a chat and send a message to confirm
the worker answers; start a Deep Work run to confirm the subagent tools. If a request fails with
`policy_unavailable` or `quota_exceeded`, check the backend log and the tenant's quota (see
"Engine rollout").

### Troubleshooting

- `docker compose` complains about `SECRET_KEY` or `REDIS_URL`: pass the placeholders shown in step 1.
- Backend refuses to start with a superuser/BYPASSRLS `DATABASE_URL`: it must use `oniva_app`.
- Engine routes return 502: the worker is not up, or `ENGINE_URL` / the tokens do not match.
- `relation ... does not exist` from the backend: step 3 migrations were not run as the owner.

## Evals

The eval harness (`backend/evals/`, see `TESTING.md`) is tooling only. `EVAL_OPENROUTER_API_KEY`, `EVAL_MAX_USD` and
the other `EVAL_*` variables are never set in a deployed environment (not in `docker-compose.prod.yml`, `k8s/`,
`terraform/` or the secrets of the running services): the eval key is a separate, credit-limited OpenRouter key kept
in a developer's local `.env`. The CI eval job runs in fake mode and has no key.

## Security Checklist

- [ ] Change all default passwords
- [ ] Configure TLS/HTTPS
- [ ] Set up firewall rules
- [ ] Enable audit logging
- [ ] Configure backup schedule
- [ ] Review RLS policies
- [ ] Set up monitoring/alerting

## Backup & Restore

### PostgreSQL Backup

```bash
# Create backup
docker compose exec postgres pg_dump -U oniva oniva > backup.sql

# Kubernetes
kubectl exec -n oniva deploy/postgres -- pg_dump -U oniva oniva > backup.sql
```

### PostgreSQL Restore

```bash
# Restore from backup
docker compose exec -T postgres psql -U oniva oniva < backup.sql
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Database connection error | Check DATABASE_URL, verify PostgreSQL is running |
| Redis connection error | Check REDIS_URL, verify Redis is running |
| Auth errors | Verify SECRET_KEY is consistent across all instances |
| LLM errors | Configure API keys in Settings page |
| Slow queries | Check database indexes, review pg_stat_statements |
| Memory issues | Increase container limits, check for memory leaks |

### Common Commands

```bash
# Check service status
docker compose -f docker-compose.prod.yml ps

# Restart a service
docker compose -f docker-compose.prod.yml restart app

# View database connections
docker compose exec postgres psql -U oniva -c "SELECT * FROM pg_stat_activity;"

# Clear Redis cache
docker compose exec redis redis-cli FLUSHALL
```

## Monitoring

### Prometheus Metrics

The API exposes metrics at `/metrics` (when enabled):
- `http_requests_total`
- `http_request_duration_seconds`
- `database_connections_active`
- `workflow_executions_total`

### Health Endpoints

- `/health` - Basic status
- `/health/ready` - Readiness probe (checks the database)
- `/health/live` - Liveness probe

## Support

- Documentation: https://docs.oniva.io
- Issues: https://github.com/yourorg/oniva/issues
- Email: support@oniva.io
