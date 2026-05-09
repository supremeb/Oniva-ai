# Oniva Deployment Guide

## Quick Start (30 minutes)

### Prerequisites

- Docker 24+ and Docker Compose 2+
- OR kubectl 1.28+ with Kubernetes cluster
- Domain name with DNS configured
- TLS certificates (or Let's Encrypt)

### Option 1: Docker Compose (Simplest)

1. **Clone and configure**
   ```bash
   git clone https://github.com/yourorg/oniva.git
   cd oniva
   cp .env.example .env
   ```

2. **Edit `.env`**
   ```bash
   # Required
   SECRET_KEY=<generate-32-char-key>
   DB_PASSWORD=<strong-password>
   DB_USER=postgres
   DB_NAME=oniva

   # Optional: LLM API keys (or configure in app)
   OPENROUTER_API_KEY=sk-or-...
   ```

3. **Start services**
   ```bash
   docker-compose -f docker-compose.prod.yml up -d
   ```

4. **Run migrations**
   ```bash
   docker-compose exec app alembic upgrade head
   ```

5. **Access application**
   - Frontend: http://localhost:3000
   - API: http://localhost:8000/docs

### Option 2: Kubernetes

1. **Create namespace and secrets**
   ```bash
   kubectl apply -f k8s/namespace.yaml
   # Edit secrets.yaml first with your values!
   kubectl apply -f k8s/secrets.yaml
   ```

2. **Create persistent volumes**
   ```bash
   kubectl apply -f k8s/pvc.yaml
   ```

3. **Deploy configuration and services**
   ```bash
   kubectl apply -f k8s/configmap.yaml
   kubectl apply -f k8s/deployment.yaml
   kubectl apply -f k8s/service.yaml
   ```

4. **Configure ingress**
   ```bash
   # Edit ingress.yaml to set your domain
   kubectl apply -f k8s/ingress.yaml
   ```

5. **Run migrations**
   ```bash
   kubectl exec -n oniva deploy/oniva-api -- alembic upgrade head
   ```

6. **Verify deployment**
   ```bash
   kubectl get pods -n oniva
   kubectl logs -n oniva -l app=oniva-api
   ```

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
docker-compose -f docker-compose.prod.yml logs -f app

# Kubernetes
kubectl logs -n oniva -l app=oniva-api -f

# AWS CloudWatch
aws logs tail /ecs/oniva-production --follow
```

### Run Database Migrations

```bash
# Docker
docker-compose exec app alembic upgrade head

# Kubernetes
kubectl exec -n oniva deploy/oniva-api -- alembic upgrade head
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
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
docker-compose -f docker-compose.prod.yml up -d --scale app=4

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
docker-compose exec postgres pg_dump -U postgres oniva > backup.sql

# Kubernetes
kubectl exec -n oniva deploy/postgres -- pg_dump -U postgres oniva > backup.sql
```

### PostgreSQL Restore

```bash
# Restore from backup
docker-compose exec -T postgres psql -U postgres oniva < backup.sql
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
docker-compose -f docker-compose.prod.yml ps

# Restart a service
docker-compose -f docker-compose.prod.yml restart app

# View database connections
docker-compose exec postgres psql -U postgres -c "SELECT * FROM pg_stat_activity;"

# Clear Redis cache
docker-compose exec redis redis-cli FLUSHALL
```

## Monitoring

### Prometheus Metrics

The API exposes metrics at `/metrics` (when enabled):
- `http_requests_total`
- `http_request_duration_seconds`
- `database_connections_active`
- `workflow_executions_total`

### Health Endpoints

- `/health` - Overall health status
- `/health/ready` - Readiness probe
- `/health/live` - Liveness probe

## Support

- Documentation: https://docs.oniva.io
- Issues: https://github.com/yourorg/oniva/issues
- Email: support@oniva.io
