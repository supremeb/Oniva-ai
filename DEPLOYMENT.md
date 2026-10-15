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
