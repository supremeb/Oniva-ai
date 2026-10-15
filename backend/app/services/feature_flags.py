"""Per-tenant feature flag service.

Enables per-tenant feature control so the platform operator can configure
what each tenant sees and can use. Resolution order:

  1. tenant.settings["features"][flag_name] — explicit per-tenant override
  2. get_settings().FLAG_NAME — global env var default
  3. False — not found

Uses in-memory TTLCache matching the existing _tenant_provider_cache pattern
in llm_gateway.py.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from cachetools import TTLCache

from app.config import get_settings
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

# In-memory cache: 500 tenants, 5-minute TTL
# Matches the existing _tenant_provider_cache pattern in llm_gateway.py
_feature_cache: TTLCache = TTLCache(maxsize=500, ttl=300)


# ========================================
# Flag Metadata — groups + operator-only classification
# ========================================

@dataclass(frozen=True)
class FlagMeta:
    """Metadata for a feature flag."""
    group: str                    # UI grouping: "Core", "Finance", "Infrastructure", etc.
    description: str              # Human-readable description
    operator_only: bool = False   # If True, only platform operators can toggle


# All known feature flags with metadata.
# Unknown flags are rejected by the admin API.
FLAG_METADATA: dict[str, FlagMeta] = {
    # Core (always available, but can be toggled)
    "ENABLE_ACCURACY": FlagMeta(
        group="Core",
        description="Accuracy evaluation features",
    ),
    "ENABLE_DEEP_WORK": FlagMeta(
        group="Core",
        description="Autonomous orchestrator-worker execution",
    ),
    "USE_EPISODIC_MEMORY": FlagMeta(
        group="Core",
        description="Vector similarity memory for cross-session learning",
    ),
    "USE_DATA_CLASSIFICATION": FlagMeta(
        group="Core",
        description="Data classification and export control",
    ),

    # Finance
    "ENABLE_FINANCE_AGENTS": FlagMeta(
        group="Finance",
        description="Finance-specific agents (Compliance, Analysis, etc.)",
    ),
    "ENABLE_FINANCE_SKILLS": FlagMeta(
        group="Finance",
        description="Finance-specific skills (KYC, AML, sanctions, market data, etc.)",
    ),
    "ENABLE_COMPLIANCE": FlagMeta(
        group="Finance",
        description="Compliance audit and reporting features",
    ),

    # Agent Reliability
    "ENABLE_STRICT_TOOL_VALIDATION": FlagMeta(
        group="Core",
        description="Skip tools without API keys at agent activation (prevents mock data)",
    ),

    # Background Agents
    "ENABLE_BACKGROUND_AGENTS": FlagMeta(
        group="Core",
        description="Background agent execution mode (cron schedules, webhook triggers)",
    ),
    "ENABLE_FLEET_DASHBOARD": FlagMeta(
        group="Core",
        description="Fleet run history dashboard for background agents",
    ),
    "ENABLE_EVALUATOR_AGENT": FlagMeta(
        group="Core",
        description="Evaluator agent gates background agent outputs before delivery",
    ),

    # Engine chat (operator-only: rollout is controlled per tenant)
    "ENABLE_ENGINE_CHAT": FlagMeta(
        group="Core",
        description="Run chat on the new engine (needs global ENABLE_ENGINE_CHAT + ENABLE_ENGINE_API)",
        operator_only=True,
    ),

    # Infrastructure (operator-only — cost/security implications)
    "ENABLE_SANDBOX": FlagMeta(
        group="Infrastructure",
        description="E2B Firecracker sandbox for code execution",
        operator_only=True,
    ),
    "ENABLE_BROWSER_SANDBOX": FlagMeta(
        group="Infrastructure",
        description="Full Chromium browser in sandbox",
        operator_only=True,
    ),
    "USE_SANDBOX_CONNECTORS": FlagMeta(
        group="Infrastructure",
        description="Connectors via E2B sandbox (Code Mode)",
        operator_only=True,
    ),
}

# Flags that are known but not yet added to FLAG_METADATA
# (will be added by their respective phases)
_FUTURE_FLAGS = {
    "ENABLE_RESTAURANT", "ENABLE_RESTAURANT_SKILLS",  # Phase 3
    "ENABLE_OUTPUT_CHANNELS",  # Phase 4+
    "ENABLE_CONTEXT_BUDGETING", "ENABLE_ERROR_RECOVERY",  # Phase 2
    "ENABLE_TOOL_CHECKPOINTING", "ENABLE_BUDGET_TRANSPARENCY",  # Phase 2
    # ENABLE_EVALUATOR_AGENT moved to FLAG_METADATA
    "ENABLE_VOICE",  # Phase 6
}


# ========================================
# Feature Resolution
# ========================================

def get_feature(tenant: Tenant, flag_name: str) -> bool:
    """Resolve a feature flag with per-tenant override.

    Resolution order (most specific wins):
      1. tenant.settings["features"][flag_name] — if key EXISTS, use value (even False)
      2. get_settings().FLAG_NAME — global env var default
      3. False — not found

    Critical: absent key = fall through. Present key with False = return False.
    """
    if flag_name == ENGINE_CHAT_FLAG:
        return engine_chat_enabled(tenant)

    tenant_features = (tenant.settings or {}).get("features", {})
    tenant_val = tenant_features.get(flag_name)
    if tenant_val is not None:
        return bool(tenant_val)

    global_settings = get_settings()
    if hasattr(global_settings, flag_name):
        return bool(getattr(global_settings, flag_name))

    return False


ENGINE_CHAT_FLAG = "ENABLE_ENGINE_CHAT"


def engine_chat_enabled(tenant: Tenant, settings=None) -> bool:
    """Engine chat for a tenant: every global condition AND an explicit tenant opt-in.

    Unlike other flags the global value is a ceiling, not a default: a tenant setting of true
    never enables engine chat while the global flag, ENABLE_ENGINE_API or the engine config is off.
    """
    s = settings if settings is not None else get_settings()
    if not (s.ENABLE_ENGINE_CHAT and s.ENABLE_ENGINE_API and s.ENGINE_URL and s.ENGINE_SERVICE_TOKEN):
        return False
    return (((tenant.settings or {}).get("features") or {}).get(ENGINE_CHAT_FLAG)) is True


def get_tenant_features(tenant: Tenant) -> dict[str, bool]:
    """Return the full resolved feature map for a tenant.

    Used by the frontend to know what to show/hide.
    Cached in-memory with 5-minute TTL.
    """
    cache_key = str(tenant.id)
    if cache_key in _feature_cache:
        return _feature_cache[cache_key]

    resolved = _resolve_all_features(tenant)
    _feature_cache[cache_key] = resolved
    return resolved


def invalidate_tenant_cache(tenant_id: str):
    """Invalidate the cached features for a tenant. Call after toggling a flag."""
    _feature_cache.pop(tenant_id, None)


def _resolve_all_features(tenant: Tenant) -> dict[str, bool]:
    """Resolve all boolean feature flags for a tenant."""
    global_settings = get_settings()
    tenant_overrides = (tenant.settings or {}).get("features", {})

    # Start with all global boolean flags
    all_flags: dict[str, bool] = {}
    for attr in dir(global_settings):
        if attr.isupper() and not attr.startswith("_"):
            val = getattr(global_settings, attr)
            if isinstance(val, bool):
                all_flags[attr] = val

    # Layer tenant overrides (operator-set, most specific)
    for flag_name, flag_val in tenant_overrides.items():
        if isinstance(flag_val, bool):
            all_flags[flag_name] = flag_val

    all_flags[ENGINE_CHAT_FLAG] = engine_chat_enabled(tenant, global_settings)
    return all_flags


# ========================================
# Validation Helpers
# ========================================

def is_known_flag(flag_name: str) -> bool:
    """Check if a flag name is recognized."""
    if flag_name in FLAG_METADATA:
        return True
    if flag_name in _FUTURE_FLAGS:
        return True
    # Also accept any flag defined in global Settings
    return hasattr(get_settings(), flag_name) and isinstance(
        getattr(get_settings(), flag_name), bool
    )


def is_operator_only(flag_name: str) -> bool:
    """Check if a flag requires platform operator access to toggle."""
    meta = FLAG_METADATA.get(flag_name)
    return meta.operator_only if meta else False


def get_flag_metadata() -> dict[str, dict]:
    """Return all flag metadata for the admin UI."""
    result = {}
    for flag_name, meta in FLAG_METADATA.items():
        result[flag_name] = {
            "group": meta.group,
            "description": meta.description,
            "operator_only": meta.operator_only,
        }
    return result


def validate_flag_dependencies(flag_name: str, enabled: bool, tenant: Tenant) -> Optional[str]:
    """Validate that enabling/disabling a flag doesn't break dependencies.

    Returns error message if validation fails, None if OK.
    """
    if not enabled:
        # Disabling — check if anything depends on this
        dependents = _FLAG_DEPENDENCIES.get(flag_name, [])
        for dep in dependents:
            if get_feature(tenant, dep):
                return f"Cannot disable {flag_name}: {dep} depends on it. Disable {dep} first."
    else:
        # Enabling — check prerequisites
        prerequisites = {v: k for k, deps in _FLAG_DEPENDENCIES.items() for v in deps}
        prereq = prerequisites.get(flag_name)
        if prereq and not get_feature(tenant, prereq):
            return f"Cannot enable {flag_name}: requires {prereq} to be enabled first."

    return None


# Flag dependency graph: parent -> [children that require it]
_FLAG_DEPENDENCIES: dict[str, list[str]] = {
    "ENABLE_SANDBOX": [
        "USE_SANDBOX_CONNECTORS",
        "ENABLE_BROWSER_SANDBOX",
    ],
    "ENABLE_BACKGROUND_AGENTS": [
        "ENABLE_FLEET_DASHBOARD",
    ],
}
