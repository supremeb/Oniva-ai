import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

from app.api.v1 import workflows, chat, agents, health, settings, skills, connectors, custom_agents, auth, audit, chat_sessions, agent_versions, background_jobs, compliance
from app.api.v1 import agent_chat, agent_test_cases, agent_evaluation, agent_deploy, agent_observability, agent_aliases
from app.api.v1 import volumes, artifacts, workflow_logs
from app.api.v1 import memory, preferences, entities
from app.api.v1 import agent_learnings  # Agent Learning System
from app.api.v1 import tenant_settings  # Per-tenant feature flags
from app.api.v1 import fleet  # Fleet dashboard (background agents)
from app.api.v1 import saml  # SAML authentication
from app.api.v1 import users, pending_users, onboarding  # User management & RBAC
from app.api.v1 import schedules  # Workflow scheduling (Workflow Nodes Expansion Phase 2)
from app.api.v1 import workflow_versions  # Workflow versioning (Workflow Nodes Expansion Phase 4)
from app.api.v1 import workflow_dsl  # Workflow DSL export/import (Workflow Nodes Expansion Phase 4)
from app.api.v1 import conversation_variables  # Conversation variables (Workflow Nodes Expansion Phase 4)
from app.api.v1 import llm_providers, llm_usage  # LLM Reliability
from app.api.v1 import knowledge_bases  # Context Engineering
from app.api.v1 import marketplace  # OpenAPI Connector Marketplace
from app.api.v1 import workflow_ai  # AI Workflow Builder
from app.api.v1 import oauth_clients  # OAuth 2.0 Client Credentials (Enterprise)
from app.api.v1 import workflow_permissions  # Workflow Sharing (Enterprise)
from app.api.v1 import analytics  # Cost Attribution & Analytics (Enterprise)
from app.api.v1 import process_logs  # Process Log CSV Export (Phase 0)
from app.api.v1 import observability_api  # Observability Dashboard
from app.api.v1 import alert_rules  # Alert Rules & Notification Channels (Enterprise)
from app.api.v1 import webhooks  # Inbound Webhook Triggers (Enterprise)
from app.api.v1 import agent_generation  # Chat-First Agent Creation (Frontier Phase A)
from app.api.v1 import mentions  # Mention resolution for Hybrid Document Editor (Frontier Phase B)
from app.api.v1 import knowledge_management  # Knowledge Management (Frontier Phase C)
from app.api.v1 import admin  # Org Admin Dashboard (Frontier Phase D)
from app.api.v1 import feedback  # Inline Feedback (Frontier Phase E)
from app.api.v1 import sandbox, workspace  # Deep Work Phase 2: Sandbox + Workspace
from app.api.v1 import wide_research  # Deep Work Phase 3: Wide Research Orchestrator
from app.api.v1 import deep_work  # Deep Work: Autonomous Execution
from app.api.v1 import projects  # Project Workspaces
from app.api.v1 import sandbox_proxy  # Sandbox Control Plane (zero-secrets LLM proxy)
from app.api.v1 import routing_policies  # Model Routing Policies (Deep Work Phase B5)
from app.api.v1 import workflow_execution  # Workflow Execution SSE (Workflows as Agent Resources)
from app.core.database import init_db, close_db, SystemSessionLocal
from app.config import get_settings
from app.core.logging import setup_logging
from app.auth.middleware import AuthMiddleware
from app.middleware.csrf import CSRFMiddleware
from app.middleware.tenant import TenantMiddleware
from app.api.exception_handlers import unhandled_exception_handler, entity_not_found_handler, permission_denied_handler, circuit_breaker_handler, quota_exceeded_handler
from app.exceptions import EntityNotFoundError, PermissionDeniedError, CircuitBreakerOpenError
from app.services.quota_service import QuotaExceededError

app_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: root handler for app loggers (uvicorn keeps its own); it was never configured before.
    # Done at startup, not import, so importing the app (tests, tooling) leaves logging alone.
    setup_logging()
    print("🚀 Starting Oniva Backend")

    # Validate production security settings
    print("🔒 Validating security configuration...")
    try:
        app_settings.validate_production_security()
        print("✅ Security validation passed")
    except ValueError as e:
        print(f"\n❌ Security validation failed:\n{e}")
        raise  # Crash the application - security validation must pass

    print("📦 Initializing database...")
    await init_db()
    print("✅ Database initialized")

    # Initialize skills registry (SKILL.md format + legacy manifests)
    print("🛠️ Loading skills...")
    from app.agents.skills.registry import SkillRegistry
    await SkillRegistry.initialize()
    print(f"✅ Loaded {len(SkillRegistry.get_all_skills())} skills")

    # Initialize semantic chunker (only if USE_SEMANTIC_CHUNKING=True)
    from app.services.chunking import init_chunker
    init_chunker()

    # Initialize connector registry
    print("🔌 Loading connectors...")
    from app.connectors.registry import ConnectorRegistry
    ConnectorRegistry.auto_discover()
    print(f"✅ Loaded {len(ConnectorRegistry.list_available())} connectors")

    # Recover orphaned jobs/documents (server crashed mid-run). Runs on the system role
    # (all tenants in one statement) and only touches rows with STALE activity, so jobs that
    # another replica started recently are never failed.
    try:
        from app.services.startup_recovery import (
            fail_stale_deep_work_jobs, fail_stale_knowledge_documents,
        )
        stale = app_settings.STALE_JOB_THRESHOLD_SECONDS
        n = await fail_stale_deep_work_jobs(
            SystemSessionLocal, stale, "Server restarted during execution")
        if n:
            print(f"✅ Cleaned up {n} stale Deep Work jobs")
        n = await fail_stale_knowledge_documents(SystemSessionLocal, stale)
        if n:
            print(f"✅ Cleaned up {n} stale knowledge documents")
        if app_settings.ENGINE_GATEWAY_TOKEN:
            from app.services.startup_recovery import interrupt_stale_tool_invocations
            n = await interrupt_stale_tool_invocations(SystemSessionLocal)
            if n:
                print(f"✅ Marked {n} stale engine tool invocations as interrupted")
    except Exception as e:
        print(f"⚠️ Failed startup recovery sweep: {e}")

    # Start Workflow Scheduler Daemon
    if app_settings.USE_WORKFLOW_SCHEDULER:
        try:
            from app.services.workflow_scheduler import get_scheduler_daemon
            daemon = get_scheduler_daemon()
            await daemon.recover_orphaned_executions()
            await daemon.start()
            print("✅ Workflow scheduler daemon started")
        except Exception as e:
            print(f"⚠️ Failed to start workflow scheduler daemon: {e}")

    yield
    # Shutdown
    print("👋 Shutting down Oniva Backend")

    # Stop Workflow Scheduler Daemon
    if app_settings.USE_WORKFLOW_SCHEDULER:
        try:
            from app.services.workflow_scheduler import get_scheduler_daemon
            daemon = get_scheduler_daemon()
            await daemon.stop()
            print("✅ Workflow scheduler daemon stopped")
        except Exception as e:
            print(f"⚠️ Failed to stop workflow scheduler daemon: {e}")

    # Graceful shutdown: only fail stale jobs (we do not track which replica owns a job, so
    # failing every running job would kill other replicas' work). Fresh jobs of this process
    # are picked up by the next startup sweep once they go stale.
    try:
        from app.services.startup_recovery import fail_stale_deep_work_jobs
        total = await fail_stale_deep_work_jobs(
            SystemSessionLocal, app_settings.STALE_JOB_THRESHOLD_SECONDS, "Server shutting down")
        if total > 0:
            print(f"⚠️ Marked {total} stale Deep Work jobs as FAILED on shutdown")
    except Exception as e:
        print(f"⚠️ Failed to clean Deep Work jobs on shutdown: {e}")

    from app.services.langfuse_integration import shutdown_langfuse
    shutdown_langfuse()
    await close_db()

app = FastAPI(
    title="Oniva API",
    version="1.0.0",
    description="Financial Intelligence Agents Platform",
    lifespan=lifespan,
    redirect_slashes=False,  # Disable automatic trailing slash redirects to avoid CORS issues
)

# Security headers middleware — runs on every response
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if app_settings.ENVIRONMENT in ("production", "prod"):
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains"
        )
    response.headers["Content-Security-Policy-Report-Only"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data:; "
        "font-src 'self' https://fonts.gstatic.com; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "worker-src 'self' blob:; "
        "report-uri /api/v1/csp-report"
    )
    return response

# Global exception handlers for RBAC, Circuit Breaker, Entity Not Found, and Quota enforcement
app.add_exception_handler(EntityNotFoundError, entity_not_found_handler)
app.add_exception_handler(PermissionDeniedError, permission_denied_handler)
app.add_exception_handler(CircuitBreakerOpenError, circuit_breaker_handler)
app.add_exception_handler(QuotaExceededError, quota_exceeded_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)  # logs, then a generic 500

# Middleware - order matters (last added = first executed)
# CORS origins: Use config value, fall back to localhost in development
if app_settings.CORS_ORIGINS:
    cors_origins = [origin.strip() for origin in app_settings.CORS_ORIGINS.split(",") if origin.strip()]
elif app_settings.DEBUG:
    # Development fallback - localhost only
    cors_origins = ["http://localhost:3000", "http://localhost:3001", "http://localhost:3002"]
    print("⚠️ CORS: Using localhost defaults (set CORS_ORIGINS for other origins)")
else:
    # Production with no CORS_ORIGINS - this should fail at validation
    cors_origins = []

app.add_middleware(GZipMiddleware, minimum_size=500)

# Rate limiting middleware - ALWAYS enabled for security
# Higher limits in development, strict in production
try:
    from app.middleware.rate_limit import RateLimitMiddleware

    rate_limit = 1000 if app_settings.DEBUG else 100

    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=rate_limit,
        fail_open=app_settings.DEBUG  # Allow requests if Redis down in dev only
    )
    print(f"✅ Rate limiting enabled ({rate_limit} req/min)")
except ImportError as e:
    print(f"⚠️ Rate limiting middleware not available: {e}")
    if not app_settings.DEBUG:
        # SECURITY: Rate limiting is required in production
        raise RuntimeError(
            "Rate limiting middleware required in production. "
            "Install dependencies or set DEBUG=true for development."
        )

# Authentication & security middleware
# Note: Middleware executes in reverse order of registration (last added = first executed)
# Execution order: Auth → CSRF → Tenant → Route handler
app.add_middleware(TenantMiddleware, fail_open=app_settings.DEBUG)
app.add_middleware(CSRFMiddleware)
app.add_middleware(AuthMiddleware)

# CORS must be registered LAST so it is the OUTERMOST middleware: auth/CSRF/tenant/
# rate-limit short-circuit responses (401/403/429) then still carry CORS headers,
# and OPTIONS preflights are answered before auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,  # Cache preflight requests for 1 hour
)

# Routes
app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(saml.router, prefix="/api/v1/auth", tags=["saml"])  # SAML SSO endpoints
app.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])
# Workflow Permissions must be registered BEFORE workflows to prevent /{workflow_id} from
# catching named routes like /shared-with-me
app.include_router(workflow_permissions.router, prefix="/api/v1/workflows", tags=["workflow-permissions"])
app.include_router(workflows.router, prefix="/api/v1/workflows", tags=["workflows"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
app.include_router(agents.router, prefix="/api/v1/agents", tags=["agents"])
app.include_router(custom_agents.router, prefix="/api/v1/agents/custom", tags=["custom-agents"])
app.include_router(skills.router, prefix="/api/v1/skills", tags=["skills"])
app.include_router(connectors.router, prefix="/api/v1/connectors", tags=["connectors"])
app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
app.include_router(chat_sessions.router, prefix="/api/v1/chat-sessions", tags=["chat-sessions"])
app.include_router(agent_versions.router, prefix="/api/v1/agent-versions", tags=["agent-versions"])
app.include_router(agent_aliases.router, prefix="/api/v1/agents", tags=["agent-aliases"])
app.include_router(background_jobs.router, prefix="/api/v1/background-jobs", tags=["background-jobs"])
app.include_router(compliance.router, prefix="/api/v1/compliance", tags=["compliance"])

# Agent Studio routes
app.include_router(agent_chat.router, prefix="/api/v1/agent-studio", tags=["agent-studio"])
app.include_router(agent_test_cases.router, prefix="/api/v1/agent-studio", tags=["agent-studio"])
app.include_router(agent_evaluation.router, prefix="/api/v1/agent-studio", tags=["agent-studio"])
app.include_router(agent_deploy.router, prefix="/api/v1/agent-studio", tags=["agent-studio"])
app.include_router(agent_observability.router, prefix="/api/v1/agent-studio/agents", tags=["agent-studio-observability"])

# Workflow Execution SSE (Workflows as Agent Resources)
app.include_router(workflow_execution.router, prefix="/api/v1/agent-studio", tags=["agent-studio"])

# Simplified Memory Architecture routes
app.include_router(volumes.router, prefix="/api/v1", tags=["volumes"])
app.include_router(artifacts.router, prefix="/api/v1", tags=["artifacts"])

# Enhanced Agent Memory System routes
app.include_router(memory.router, prefix="/api/v1", tags=["memory"])
app.include_router(preferences.router, prefix="/api/v1", tags=["preferences"])
app.include_router(entities.router, prefix="/api/v1", tags=["entities"])
app.include_router(agent_learnings.router, prefix="/api/v1", tags=["agent-learnings"])

# Workflow Logs & Monitoring routes
app.include_router(workflow_logs.router, prefix="/api/v1/workflows", tags=["workflow-logs"])

# User Management & RBAC routes
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(pending_users.router, prefix="/api/v1/pending-users", tags=["pending-users"])
app.include_router(onboarding.router, prefix="/api/v1/onboarding", tags=["onboarding"])

# Workflow Scheduling routes (Workflow Nodes Expansion Phase 2)
app.include_router(schedules.router, prefix="/api/v1/schedules", tags=["schedules"])

# Workflow Versioning routes (Workflow Nodes Expansion Phase 4)
app.include_router(workflow_versions.router, prefix="/api/v1", tags=["workflow-versions"])

# Workflow DSL Export/Import routes (Workflow Nodes Expansion Phase 4)
app.include_router(workflow_dsl.router, prefix="/api/v1", tags=["workflow-dsl"])

# Conversation Variables routes (Workflow Nodes Expansion Phase 4)
app.include_router(conversation_variables.router, prefix="/api/v1", tags=["conversation-variables"])

# LLM Reliability routes
app.include_router(llm_providers.router, prefix="/api/v1/settings", tags=["llm-providers"])
app.include_router(llm_usage.router, prefix="/api/v1", tags=["llm-usage"])

# Context Engineering routes
app.include_router(knowledge_bases.router, prefix="/api/v1", tags=["knowledge-bases"])

# OpenAPI Connector Marketplace routes
app.include_router(marketplace.router, prefix="/api/v1", tags=["marketplace"])

# AI Workflow Builder routes
app.include_router(workflow_ai.router, prefix="/api/v1/workflow-ai", tags=["workflow-ai"])

# OAuth 2.0 Client Credentials (Enterprise)
app.include_router(oauth_clients.router, prefix="/api/v1/oauth", tags=["oauth"])

# Cost Attribution & Analytics (Enterprise)
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"])

# Process Log CSV Export (Phase 0)
app.include_router(process_logs.router, prefix="/api/v1/process-logs", tags=["process-logs"])

# Observability Dashboard
app.include_router(observability_api.router, prefix="/api/v1/observability", tags=["observability"])

# Alert Rules & Notification Channels (Enterprise)
app.include_router(alert_rules.router, prefix="/api/v1", tags=["alerting"])

# Inbound Webhook Triggers (Enterprise)
app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])

# Chat-First Agent Creation (Frontier Phase A)
app.include_router(agent_generation.router, prefix="/api/v1/agents", tags=["agent-generation"])

# Hybrid Document Editor - Mention Resolution (Frontier Phase B)
app.include_router(mentions.router, prefix="/api/v1", tags=["mentions"])

# Knowledge Management (Frontier Phase C)
app.include_router(knowledge_management.router, prefix="/api/v1", tags=["knowledge-management"])

# Org Admin Dashboard (Frontier Phase D)
app.include_router(admin.router, prefix="/api/v1", tags=["admin"])
app.include_router(tenant_settings.router, prefix="/api/v1", tags=["tenant-settings"])
app.include_router(fleet.router, prefix="/api/v1", tags=["fleet"])

# Inline Feedback (Frontier Phase E)
app.include_router(feedback.router, prefix="/api/v1", tags=["feedback"])

# Deep Work Phase 2: Sandbox + Workspace
app.include_router(sandbox.router, prefix="/api/v1", tags=["sandbox"])
app.include_router(workspace.router, prefix="/api/v1", tags=["workspace"])

# Deep Work Phase 3: Wide Research Orchestrator
app.include_router(wide_research.router, prefix="/api/v1", tags=["wide-research"])

# Deep Work: Autonomous Execution
app.include_router(deep_work.router, prefix="/api/v1", tags=["deep-work"])
app.include_router(routing_policies.router, prefix="/api/v1/routing-policies", tags=["routing-policies"])

# Project Workspaces
app.include_router(projects.router, prefix="/api/v1/projects", tags=["projects"])

# Sandbox Control Plane: zero-secrets LLM proxy
app.include_router(sandbox_proxy.router, prefix="/api/v1", tags=["sandbox-proxy"])

# Engine-facing /internal namespace: mounted only when ENGINE_GATEWAY_TOKEN is set.
from app.internal_api.router import mount_internal_router
mount_internal_router(app, app_settings)

# Browser-facing engine conversations: only when ENABLE_ENGINE_API and the engine is configured.
from app.api.v1.engine_conversations import mount_engine_conversations
mount_engine_conversations(app, app_settings)

@app.get("/")
async def root():
    return {
        "message": "Oniva API",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.post("/api/v1/csp-report")
async def csp_report(request: Request):
    body = await request.json()
    logger.warning("CSP violation: %s", body)
    return {"status": "ok"}

