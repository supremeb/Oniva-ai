"""Configuration module for Oniva backend.

This package re-exports the Settings class and provides access to all configuration.
Note: This __init__.py includes the full Settings class because this package
shadows the original app/config.py module.
"""

import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Optional
from pathlib import Path

# Get absolute path to .env file (relative to this config package)
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


def _resolve_env_file() -> Optional[str]:
    """The dotenv file Settings reads: backend/.env by default.

    ``ONIVA_ENV_FILE`` overrides the path; set it to an empty string to read no file at all (the test
    suite does this in tests/conftest.py so a developer's local .env cannot leak into unit tests).
    """
    override = os.environ.get("ONIVA_ENV_FILE")
    if override is None:
        return str(_ENV_FILE)
    return override or None

# Exact-match weak passwords (lower-cased). Placeholder prefixes are checked separately.
_WEAK_DB_PASSWORDS = frozenset({
    "", "postgres", "password", "passw0rd", "changeme", "change_me", "change-me",
    "default", "test", "example", "admin", "root", "secret", "oniva", "oniva_app",
    "oniva_system", "123456", "12345678", "qwerty", "letmein", "pass", "user",
})
_PLACEHOLDER_PREFIXES = ("change_me", "change-me", "changeme", "replace_with", "replace-with")
_SUPERUSER_NAMES = frozenset({"postgres", "root", "admin", "superuser"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    # Application
    APP_NAME: str = "Oniva"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Database
    # Request-path URL: MUST connect as the NOSUPERUSER NOBYPASSRLS role `oniva_app`.
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/oniva"
    # BYPASSRLS role `oniva_system` (scheduler, webhook ingestion, startup recovery, pre-tenant
    # auth lookups). Falls back to SCHEDULER_DATABASE_URL, then DATABASE_URL (dev/test only).
    SYSTEM_DATABASE_URL: str = ""
    # Schema-owner URL used by Alembic (init_db auto-migrate and the CLI). Falls back to DATABASE_URL.
    MIGRATION_DATABASE_URL: str = ""
    # Run `alembic upgrade head` at startup. None => True in development, False in production.
    AUTO_MIGRATE: Optional[bool] = None
    # Startup recovery only fails jobs whose last activity is older than this.
    STALE_JOB_THRESHOLD_SECONDS: int = 3600

    # Redis
    REDIS_URL: str = "redis://redis:6379"

    # Default API Keys (optional - users can configure their own)
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None

    # OpenRouter Configuration
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    DEFAULT_LLM_PROVIDER: str = "openrouter"  # 'openrouter', 'openai', 'anthropic'
    DEFAULT_LLM_MODEL: str = "openai/gpt-4o-mini"  # Model path for OpenRouter

    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production-must-be-at-least-32-chars-long"
    ENCRYPTION_SALT: str = "default-salt-change-in-production"  # Must be hex-encoded 32 bytes in production
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7  # Refresh tokens valid for 7 days
    ALGORITHM: str = "HS256"
    AUTH_ALGORITHM: str = "HS256"  # Alias for backward compatibility
    CORS_ORIGINS: str = ""  # SECURITY: Empty default - must be explicitly configured

    # DBOS Configuration
    DBOS_DATABASE_URL: str = "postgresql://postgres:postgres@postgres:5432/oniva"

    # Monitoring
    LOGFIRE_TOKEN: Optional[str] = None
    ENABLE_METRICS: bool = True

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    DEFAULT_RATE_LIMIT: int = 100  # requests per minute

    # Skills API Keys
    ALPHA_VANTAGE_API_KEY: Optional[str] = None  # For market data
    TAVILY_API_KEY: Optional[str] = None  # For web search
    SERPAPI_API_KEY: Optional[str] = None  # Alternative web search
    POLYGON_API_KEY: Optional[str] = None  # For premium market data

    # External API Keys (for workflows)
    EXA_API_KEY: Optional[str] = None  # For Exa.ai web search / DeepWork
    PERPLEXITY_API_KEY: Optional[str] = None  # For Perplexity AI deep work
    PARALLEL_AI_API_KEY: Optional[str] = None  # For Parallel AI web search

    # Skills Configuration
    ENABLE_SANDBOX: bool = False  # Enable sandboxing for code execution (E2B Firecracker)
    E2B_API_KEY: str = ""  # E2B API key for Firecracker sandbox execution
    E2B_TEMPLATE_ID: str = ""  # Custom E2B template (e.g. "oniva-agent" with data science stack)

    # Sandbox Control Plane: proxy-based LLM access (no real keys in sandbox)
    # Sandboxes ALWAYS get a proxy token, never a real LLM key. This flag is retained only for
    # backward compatibility with feature_flags; it no longer gates a real-key fallback (removed).
    ENABLE_SANDBOX_CONTROL_PLANE: bool = True
    # URL the *sandbox* can reach this backend at. REQUIRED when ENABLE_SANDBOX is on (fail closed).
    # Local dev: E2B VMs cannot reach localhost, so expose the backend with a tunnel, e.g.
    #   ngrok http 8000  ->  SANDBOX_PROXY_URL=https://<id>.ngrok-free.app
    SANDBOX_PROXY_URL: str = ""
    # Comma-separated hosts/CIDRs the sandbox may reach (the proxy host is always added).
    # Everything else is denied. "*" disables egress restriction (explicit opt-out).
    SANDBOX_EGRESS_ALLOW: str = "pypi.org,files.pythonhosted.org"
    # Comma-separated model allow-list enforced by the sandbox LLM proxy. Empty = DEFAULT_LLM_MODEL only.
    SANDBOX_ALLOWED_MODELS: str = ""

    # Connectors via E2B sandbox (Code Mode: 2 tools per connector instead of N)
    USE_SANDBOX_CONNECTORS: bool = False  # Requires ENABLE_SANDBOX=True

    # Browser automation in sandbox (Chromium + Playwright + Browser Use)
    ENABLE_BROWSER_SANDBOX: bool = False  # Requires ENABLE_SANDBOX=True

    # App Database User (for RLS enforcement)
    APP_DATABASE_USER: str = "oniva_app"
    # No default: role passwords come from env/secrets (set via ALTER ROLE out-of-band).
    APP_DATABASE_PASSWORD: str = ""

    # ========================================
    # Feature Flags
    # Phases 1-3 enabled by default (Jan 16, 2026)
    # ========================================
    USE_DURABLE_WORKFLOWS: bool = True            # Phase 1: DBOS checkpointing - ENABLED
    USE_STATE_MACHINE: bool = True                # Phase 3: Tool masking - ENABLED
    USE_ADVANCED_MEMORY: bool = True              # Phase 2: Context ranking - ENABLED
    USE_PROVIDER_CACHING: bool = True             # P2: Provider-native prompt caching (Anthropic/OpenAI) - ENABLED
    USE_KVFLOW_CACHING: bool = False              # Phase 4: Week 7-8 - Custom KVFlow caching (not implemented)
    ENABLE_A2A: bool = False                      # Phase 5: Week 9-10 - Agent-to-Agent (not implemented)

    # ========================================
    # Workflow Scheduling
    # ========================================
    USE_WORKFLOW_SCHEDULER: bool = True
    SCHEDULER_POLL_INTERVAL_SECONDS: int = 30
    SCHEDULER_BATCH_SIZE: int = 10
    SCHEDULER_MAX_CONCURRENT_GLOBAL: int = 20
    SCHEDULER_DATABASE_URL: str = ""  # If empty, uses main DATABASE_URL

    # ========================================
    # Workflow Quality & Complexity Limits (Enterprise)
    # Prevent runaway workflows and ensure performance
    # ========================================
    ENABLE_WORKFLOW_VALIDATION: bool = True       # Cycle detection & complexity limits
    ENABLE_VERSION_DIFF: bool = False             # Version comparison (nice-to-have)
    MAX_WORKFLOW_NODES: int = 95                  # Maximum nodes per workflow
    MAX_WORKFLOW_DEPTH: int = 20                  # Maximum depth (longest path)
    MAX_WORKFLOW_EDGES: int = 200                 # Maximum edges per workflow
    DEFAULT_EXECUTION_TIMEOUT_SECONDS: int = 7200 # Default timeout: 2 hours

    # ========================================
    # Security Foundation (Phase 1 Security)
    # Data classification and export control
    # ========================================
    USE_DATA_CLASSIFICATION: bool = True          # Enable data classification and export control - ENABLED

    # ========================================
    # Phase 2A Enhanced Features
    # Based on Vercel research (bash consolidation) and Manus AI (recitation)
    # ========================================
    USE_BASH_TOOL: bool = False                   # Phase 2A: Unified bash interface (40-50% cost reduction)
    USE_PLAN_TODO_RECITATION: bool = True         # Phase 2A: Plan/todo recitation (42% error reduction) - ENABLED (legacy, replaced by USE_HARNESS_PIPELINE)
    USE_HARNESS_PIPELINE: bool = True             # Agent harness: loop detection + credential scanning + recitation - ENABLED

    # ========================================
    # Enhanced Agent Memory System
    # Cross-session learning and user personalization
    # ========================================
    USE_EPISODIC_MEMORY: bool = True              # Store/retrieve agent decisions with vector similarity - ENABLED
    USE_USER_PREFERENCES: bool = True             # Per-user agent customization (risk tolerance, metrics, etc.) - ENABLED
    USE_AGENT_LEARNING: bool = False              # Error-driven learning capture and retrieval - DISABLED (opt-in)
    USE_ORG_CONTEXT: bool = False                 # Organization Context Layer: shared memory + org profile injection (requires USE_EPISODIC_MEMORY)
    # ========================================
    # Deep Work Mode (unified autonomous execution)
    # Replaces separate Deep Research toggle with unified plan-first UX
    # Deep Work Engine (orchestrator-worker) configuration
    DEEP_WORK_MAX_WORKERS: int = 10               # Max research workers per round (matches the legacy plan limit)
    DEEP_WORK_MAX_REFLECTION_ROUNDS: int = 3      # Max reflection rounds (0 = no reflection)
    # ========================================
    ENABLE_DEEP_WORK: bool = True                 # Master toggle for autonomous execution
    DEEP_WORK_MAX_DURATION_SEC: int = 1800        # Budget: max time (30 min)
    DEEP_WORK_MAX_LEARNINGS_PER_WORKER: int = 30  # Max learnings before worker stops

    # ========================================
    # Intelligent Routing (Phase B1)
    # Agent auto-suggests deep work for complex queries
    # ========================================
    ENABLE_INTELLIGENT_ROUTING: bool = False      # Agent suggests deep work via banner (off by default)

    # ========================================
    # RAG & Embedding Configuration
    # ========================================
    EMBEDDING_MODEL: str = "text-embedding-3-small"  # Only 1536-dim models supported. Phase 2 for variable dimensions.
    QUERY_AUGMENTATION_MODEL: str = "openai/gpt-4o-mini"  # Model for HyDE / multi-query augmentation
    USE_QUERY_AUGMENTATION: bool = False           # Enable query augmentation (HyDE + multi-query) - DISABLED by default

    # RAG Chunking Upgrade (Stream B)
    USE_SEMANTIC_CHUNKING: bool = False            # Gates semantic chunking + contextual retrieval
    CONTEXTUAL_RETRIEVAL_MODEL: str = "claude-3-haiku-20240307"  # Via LLM Gateway for contextual retrieval

    # ========================================
    # Simplified Memory Architecture (Phase 2 Refactor)
    # Replaces complex 3-tier memory with simpler Volumes + Session + Artifacts
    # Enabled by default (Jan 16, 2026)
    # ========================================
    USE_VOLUMES: bool = True                      # Pre-warmed input data (read-only) - ENABLED
    USE_SESSION_FILESYSTEM: bool = True           # Filesystem-backed session storage - ENABLED
    USE_ARTIFACTS: bool = True                    # Persistent outputs with 7-year retention - ENABLED

    # Volume Configuration
    VOLUMES_BASE_PATH: str = "./volumes"          # Local volumes base path
    VOLUMES_USE_S3: bool = False                  # Use S3 for volumes in production
    VOLUMES_S3_BUCKET: Optional[str] = None       # S3 bucket for volumes
    VOLUMES_S3_PREFIX: str = "volumes/"           # S3 prefix for volumes

    # Artifact Configuration
    ARTIFACTS_BASE_PATH: str = "./artifacts"      # Local artifacts base path
    ARTIFACTS_USE_S3: bool = False                # Use S3 for artifacts in production
    ARTIFACTS_S3_BUCKET: Optional[str] = None     # S3 bucket for artifacts
    ARTIFACTS_S3_PREFIX: str = "artifacts/"       # S3 prefix for artifacts
    ARTIFACTS_RETENTION_DAYS: int = 2555          # 7 years default for compliance

    # Phase 2: Session Configuration
    SESSION_BASE_PATH: str = "./sessions"
    SESSION_MAX_SIZE_GB: int = 10
    SESSION_RETENTION_DAYS: int = 30              # Delete stale sessions after N days

    # SSE Tool Result Display
    TOOL_RESULT_SSE_LIMIT: int = 5000             # Max chars for tool results in SSE events (LLM sees full output)

    # Workflow Execution
    WORKFLOW_RETRY_BUDGET: int = 10               # Max total retries across all tools per workflow run

    # Phase 4: Cache Configuration
    CACHE_STRATEGY: str = "provider"              # "provider" or "vllm"
    CACHE_TTL_SECONDS: int = 3600

    # Phase 5: Ecosystem
    KNOWLEDGE_GRAPH_BACKEND: str = "postgres"     # "postgres" or "neo4j"

    # Phase 1: Observability
    LANGCHAIN_API_KEY: Optional[str] = None       # LangSmith API key
    LANGCHAIN_PROJECT: str = "oniva"          # LangSmith project name
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None  # OpenTelemetry collector

    # Langfuse Integration (Feature-Flagged)
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str = "http://localhost:3001"

    # Phase 2: Braintrust Observability (P2.4)
    # Enterprise AI observability with built-in evaluation and alerting
    BRAINTRUST_API_KEY: Optional[str] = None      # Braintrust API key (from braintrust.dev)
    BRAINTRUST_PROJECT: str = "oniva"         # Braintrust project name
    BRAINTRUST_ENABLED: bool = False              # Enable Braintrust integration
    BRAINTRUST_TRACE_AI_ONLY: bool = True         # Only trace AI/LLM spans (vs all spans)

    # ========================================
    # Feature Toggles
    # ========================================
    ENABLE_ACCURACY: bool = True                  # Enable Accuracy evaluation features
    ENABLE_COMPLIANCE: bool = False               # Enable Compliance audit features

    # ========================================
    # Per-Tenant Feature Modules (Phase 1 Platform Foundation)
    # These flags can be overridden per-tenant via tenant.settings.features
    # ========================================
    ENABLE_FINANCE_AGENTS: bool = True            # Finance-specific agents (Compliance, Analysis, etc.)
    ENABLE_FINANCE_SKILLS: bool = True            # Finance-specific skills (kyc, aml, sanctions, market data)
    ENABLE_STRICT_TOOL_VALIDATION: bool = True    # Skip tools without API keys at agent activation
    ENABLE_BACKGROUND_AGENTS: bool = True         # Background agent execution mode
    ENABLE_FLEET_DASHBOARD: bool = True            # Fleet run history dashboard
    ENABLE_EVALUATOR_AGENT: bool = False           # Evaluator gates background agent outputs

    # ========================================
    # Email Configuration (User Management)
    # Stubbed for future Resend integration
    # ========================================
    EMAIL_ENABLED: bool = False                   # Enable email sending (Resend)
    RESEND_API_KEY: Optional[str] = None          # Resend API key
    EMAIL_FROM_ADDRESS: str = "noreply@oniva.ai"  # From email address
    EMAIL_FROM_NAME: str = "Oniva"            # From display name

    # ========================================
    # Phase 2: Enterprise SSO (SAML 2.0)
    # Configure your Identity Provider (IdP) settings
    # ========================================
    SAML_ENABLED: bool = False                    # Enable SAML authentication
    SAML_CONFIG_PATH: Optional[str] = None        # Path to SAML settings JSON file

    # Service Provider (SP) Configuration - Oniva
    APP_BASE_URL: str = "http://localhost:8000"   # Base URL for this application
    SAML_SP_ENTITY_ID: Optional[str] = None       # SP Entity ID (usually app URL)
    SAML_SP_ACS_URL: Optional[str] = None         # Assertion Consumer Service URL
    SAML_SP_SLO_URL: Optional[str] = None         # Single Logout Service URL
    SAML_SP_CERTIFICATE: Optional[str] = None     # SP X.509 certificate (for signing)
    SAML_SP_PRIVATE_KEY: Optional[str] = None     # SP private key (for signing)

    # Identity Provider (IdP) Configuration - Okta, Azure AD, etc.
    SAML_IDP_ENTITY_ID: Optional[str] = None      # IdP Entity ID
    SAML_IDP_SSO_URL: Optional[str] = None        # IdP Single Sign-On URL
    SAML_IDP_SLO_URL: Optional[str] = None        # IdP Single Logout URL
    SAML_IDP_CERTIFICATE: Optional[str] = None    # IdP X.509 certificate

    # SAML Security Settings
    SAML_SIGN_REQUESTS: bool = False              # Sign AuthnRequests and LogoutRequests
    SAML_WANT_ASSERTIONS_SIGNED: bool = True      # Require signed assertions from IdP
    SAML_WANT_MESSAGES_SIGNED: bool = True        # Require signed SAML messages

    # SAML User Provisioning
    SAML_AUTO_PROVISION: bool = False             # Auto-create users on first SAML login
    SAML_DEFAULT_TENANT: Optional[str] = None     # Default tenant slug for new SAML users
    SAML_DEFAULT_ROLE: str = "member"             # Default role for new SAML users
    SAML_GROUP_MAPPING: str = "{}"                # JSON: IdP group -> role mapping

    # ========================================
    # Redis High Availability (P2.2)
    # Sentinel mode for automatic failover
    # ========================================
    REDIS_SENTINEL_ENABLED: bool = False                  # Enable Sentinel mode
    REDIS_SENTINEL_HOSTS: Optional[str] = None            # Comma-separated: "host1:26379,host2:26379"
    REDIS_SENTINEL_MASTER_NAME: str = "mymaster"          # Sentinel master name
    REDIS_SENTINEL_PASSWORD: Optional[str] = None         # Sentinel password (if required)

    # Redis Connection Settings
    REDIS_SOCKET_TIMEOUT: float = 5.0                     # Socket timeout in seconds
    REDIS_CONNECT_TIMEOUT: float = 5.0                    # Connection timeout in seconds
    REDIS_MAX_CONNECTIONS: int = 50                       # Maximum connection pool size

    # Engine interfaces (@oniva/engine). Two secrets, one per direction:
    # ENGINE_GATEWAY_TOKEN guards /internal (engine -> backend); the router is mounted only when set.
    # ENGINE_SERVICE_TOKEN is what the backend presents to the engine (backend -> engine).
    ENGINE_URL: str = ""
    ENGINE_SESSION_ID: str = ""
    ENGINE_SERVICE_TOKEN: Optional[str] = None
    ENGINE_GATEWAY_TOKEN: Optional[str] = None
    ENGINE_GATEWAY_TOKEN_PREVIOUS: Optional[str] = None  # accepted during rotation
    ENABLE_ENGINE_API: bool = False
    ENABLE_ENGINE_CHAT: bool = False  # global gate for engine-backed chat; tenants opt in via settings.features
    ENABLE_ENGINE_DEEP_WORK: bool = False  # global gate for engine-backed Deep Work; tenants opt in via settings.features
    ENGINE_REQUEST_TIMEOUT_S: float = 30.0
    # Legacy POST /api/v1/chat: when a stored session is supplied, rebuild model history from the
    # stored messages and ignore client-supplied assistant/tool parts (anti-forgery). Set to
    # false (CHAT_SERVER_HISTORY=false) to restore the old client-trusted history.
    CHAT_SERVER_HISTORY: bool = True
    INTERNAL_ALLOW_PLAINTEXT: bool = False  # allow a non-https ENGINE_URL in production

    # Redis Circuit Breaker Settings
    REDIS_CIRCUIT_BREAKER_ENABLED: bool = True            # Enable circuit breaker
    REDIS_CIRCUIT_FAILURE_THRESHOLD: int = 5              # Failures before opening circuit
    REDIS_CIRCUIT_TIMEOUT_SECONDS: float = 30.0           # Timeout before half-open
    REDIS_FAIL_FAST_ON_CIRCUIT_OPEN: bool = False         # Raise error vs return None

    @model_validator(mode="after")
    def _validate_engine_tokens(self) -> "Settings":
        tokens = {
            "ENGINE_GATEWAY_TOKEN": self.ENGINE_GATEWAY_TOKEN,
            "ENGINE_GATEWAY_TOKEN_PREVIOUS": self.ENGINE_GATEWAY_TOKEN_PREVIOUS,
            "ENGINE_SERVICE_TOKEN": self.ENGINE_SERVICE_TOKEN,
        }
        present = {k: v for k, v in tokens.items() if v}
        for name, value in present.items():
            if len(value) < 32:
                raise ValueError(f"{name} must be at least 32 characters")
            if value == self.SECRET_KEY:
                raise ValueError(f"{name} must differ from SECRET_KEY")
        values = list(present.values())
        if len(set(values)) != len(values):
            raise ValueError("ENGINE_GATEWAY_TOKEN, ENGINE_GATEWAY_TOKEN_PREVIOUS and ENGINE_SERVICE_TOKEN must differ from each other")
        return self

    @property
    def system_database_url(self) -> str:
        """URL of the BYPASSRLS `oniva_system` role (see SYSTEM_DATABASE_URL)."""
        return self.SYSTEM_DATABASE_URL or self.SCHEDULER_DATABASE_URL or self.DATABASE_URL

    @property
    def migration_database_url(self) -> str:
        """Schema-owner URL for Alembic (never the request role)."""
        return self.MIGRATION_DATABASE_URL or self.DATABASE_URL

    @property
    def auto_migrate(self) -> bool:
        if self.AUTO_MIGRATE is not None:
            return self.AUTO_MIGRATE
        return self.ENVIRONMENT not in ("production", "prod")

    @staticmethod
    def _is_weak_db_password(password: Optional[str]) -> bool:
        if password is None:
            return True
        lowered = password.strip().lower()
        return lowered in _WEAK_DB_PASSWORDS or lowered.startswith(_PLACEHOLDER_PREFIXES)

    def _validate_database_credentials(self) -> list[str]:
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import ArgumentError

        errors: list[str] = []
        parsed = {}
        for name, raw in (
            ("DATABASE_URL", self.DATABASE_URL),
            ("SYSTEM_DATABASE_URL", self.SYSTEM_DATABASE_URL),
            ("MIGRATION_DATABASE_URL", self.MIGRATION_DATABASE_URL),
        ):
            if not raw:
                if name == "SYSTEM_DATABASE_URL":
                    errors.append(
                        "SYSTEM_DATABASE_URL must be set in production "
                        "(BYPASSRLS oniva_system role for scheduler/webhooks/startup recovery)."
                    )
                continue
            try:
                url = make_url(raw)
            except (ArgumentError, ValueError):
                # Never echo the raw value: it may contain a password.
                errors.append(f"{name} is not a valid SQLAlchemy database URL.")
                continue
            parsed[name] = url
            if self._is_weak_db_password(url.password):
                errors.append(
                    f"{name} has a missing or weak/default password. "
                    "Generate secure credentials using: python scripts/generate_secrets.py"
                )

        if self.APP_DATABASE_PASSWORD and self._is_weak_db_password(self.APP_DATABASE_PASSWORD):
            errors.append(
                "APP_DATABASE_PASSWORD is a weak/default value. "
                "Generate a secure password using: python scripts/generate_secrets.py"
            )

        app_url = parsed.get("DATABASE_URL")
        if app_url is not None:
            app_user = (app_url.username or "").lower()
            if app_user in _SUPERUSER_NAMES:
                errors.append(
                    f"DATABASE_URL must connect as the non-superuser '{self.APP_DATABASE_USER}' role, "
                    f"not '{app_url.username}'."
                )
            owner = parsed.get("MIGRATION_DATABASE_URL")
            if owner is not None and owner.username and app_user == owner.username.lower():
                errors.append("DATABASE_URL user must differ from the schema owner (MIGRATION_DATABASE_URL user).")
            system = parsed.get("SYSTEM_DATABASE_URL")
            if system is not None and system.username and app_user == system.username.lower():
                errors.append("DATABASE_URL user must differ from the SYSTEM_DATABASE_URL user (RLS separation).")
        return errors

    def validate_production_security(self) -> None:
        """Validate that production deployments use secure credentials.

        This method checks for common security misconfigurations and raises
        ValueError if any insecure defaults are detected in production mode.

        Raises:
            ValueError: If any security validation fails in production
        """
        if self.ENVIRONMENT not in ["production", "prod"]:
            # Only enforce in production
            return

        errors = []

        # 1. Validate SECRET_KEY
        insecure_secret_patterns = [
            "your-secret-key",
            "change",
            "changeme",
            "change-me",
            "change_me",
            "default",
            "secret",
            "test",
            "example",
        ]
        secret_lower = self.SECRET_KEY.lower()
        if any(pattern in secret_lower for pattern in insecure_secret_patterns):
            errors.append(
                "SECRET_KEY contains insecure default value. "
                "Generate a secure key using: python scripts/generate_secrets.py"
            )
        elif len(self.SECRET_KEY) < 32:
            errors.append(
                f"SECRET_KEY must be at least 32 characters (current: {len(self.SECRET_KEY)})"
            )

        # 2. Validate ENCRYPTION_SALT
        insecure_salt_patterns = [
            "default",
            "change",
            "changeme",
            "salt",
            "test",
            "example",
        ]
        salt_lower = self.ENCRYPTION_SALT.lower()
        if any(pattern in salt_lower for pattern in insecure_salt_patterns):
            errors.append(
                "ENCRYPTION_SALT contains insecure default value. "
                "Generate a secure salt using: python scripts/generate_secrets.py"
            )

        # 3. Validate database credentials (parse URLs; never substring-match the whole URL,
        #    a valid URL legitimately contains "postgres" in its scheme/user/host).
        errors.extend(self._validate_database_credentials())

        # 4. Validate CORS configuration
        if not self.CORS_ORIGINS or self.CORS_ORIGINS == "*":
            errors.append(
                "CORS_ORIGINS must be set to explicit origins in production. "
                "Example: 'https://app.oniva.ai,https://admin.oniva.ai'. "
                "Wildcard '*' is not allowed."
            )

        # 5. Validate DEBUG is disabled
        if self.DEBUG:
            errors.append(
                "DEBUG must be False in production. "
                "Set DEBUG=false in your environment."
            )

        # 6. Engine URL must be https unless explicitly overridden
        if (self.ENGINE_URL and not self.INTERNAL_ALLOW_PLAINTEXT
                and not self.ENGINE_URL.lower().startswith("https://")):
            errors.append(
                "ENGINE_URL must use https in production (or set INTERNAL_ALLOW_PLAINTEXT=true "
                "for a trusted private network)."
            )

        # If any errors, crash the application with clear instructions
        if errors:
            error_message = "\n".join([
                "",
                "=" * 70,
                "PRODUCTION SECURITY VALIDATION FAILED",
                "=" * 70,
                "",
                "The following security issues must be fixed before deploying:",
                "",
            ] + [f"  {error}" for error in errors] + [
                "",
                "TO FIX:",
                "  1. Run: python scripts/generate_secrets.py",
                "  2. Store output in secrets manager (AWS Secrets Manager, Vault, etc.)",
                "  3. Update environment variables with generated secrets",
                "  4. NEVER commit secrets to version control",
                "",
                "See Docs/DEPLOYMENT.md for detailed instructions",
                "=" * 70,
                "",
            ])
            raise ValueError(error_message)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# LLM Pricing exports
from app.config.llm_pricing import (
    LLM_PRICING,
    DEFAULT_PRICING,
    ModelPricing,
    get_model_pricing,
    calculate_cost,
)

__all__ = [
    # Settings
    "Settings",
    "get_settings",
    # LLM Pricing
    "LLM_PRICING",
    "DEFAULT_PRICING",
    "ModelPricing",
    "get_model_pricing",
    "calculate_cost",
]
