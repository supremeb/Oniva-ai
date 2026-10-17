"""Scoped execution context and tool guards (U4 / R5, R9, AE8).

A tool receives exactly what its ToolSpec declares: only the named provider keys,
a database session only when `needs_db`, connector metadata only when
`needs_connectors`. Its result is redacted, size-capped and returned as an
outcome; errors never carry stack traces or secrets.
"""
import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterable, Optional, Tuple

import yaml
from fastapi import HTTPException
from sqlalchemy import select

from app.agents.harness import _scan_credentials
from app.agents.skills.context import SkillContext
from app.agents.skills.registry import SkillRegistry
from app.internal_api.fence import GatewayError
from app.internal_api.tool_specs import ToolSpec, spec_for

logger = logging.getLogger(__name__)

MAX_OUTCOME_BYTES = 512 * 1024
TRUNCATION_MARKER = "\n[Truncated: result exceeded 512 KiB]"
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

_ENV_FALLBACK = {
    "tavily": "TAVILY_API_KEY",
    "parallelai": "PARALLEL_API_KEY",
    "exa": "EXA_API_KEY",
    "serpapi": "SERP_API_KEY",
    "e2b": "E2B_API_KEY",
}


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


def _session_factory():
    from app.core.database import AsyncSessionLocal

    return AsyncSessionLocal


async def resolve_tool_keys(session: Any, tenant_id: Any, providers: Iterable[str]) -> Dict[str, str]:
    """Decrypt only the named providers' keys for this tenant (env fallback for search providers)."""
    wanted = [p for p in providers if p]
    if not wanted:
        return {}
    from uuid import UUID

    from app.core.encryption import get_encryption_service
    from app.models.api_key import APIKey

    keys: Dict[str, str] = {}
    try:
        rows = await session.execute(
            select(APIKey).where(
                APIKey.tenant_id == UUID(str(tenant_id)),
                APIKey.is_active == True,  # noqa: E712
                APIKey.provider.in_(wanted),
            )
        )
        encryption = get_encryption_service()
        for row in rows.scalars():
            if row.provider not in keys:
                try:
                    keys[row.provider] = encryption.decrypt(row.encrypted_key)
                except Exception:
                    logger.warning("Could not decrypt a tenant key for provider %s", row.provider)
    except Exception:
        logger.warning("Tool key lookup failed", exc_info=True)
    for provider in wanted:
        env_var = _ENV_FALLBACK.get(provider)
        if provider not in keys and env_var and os.environ.get(env_var):
            keys[provider] = os.environ[env_var]
    return {p: keys[p] for p in wanted if p in keys}


async def resolve_tool_connectors(session: Any, tenant_id: Any) -> Dict[str, Dict[str, Any]]:
    """Metadata (never credentials) of connectors this tenant has connected, plus auth-free ones."""
    from app.connectors.registry import ConnectorRegistry, load_tenant_credentials

    out: Dict[str, Dict[str, Any]] = {}
    for meta in ConnectorRegistry.list_available():
        if getattr(meta, "auth_type", "") != "none":
            try:
                if await load_tenant_credentials(meta.id, tenant_id, db=session) is None:
                    continue
            except Exception:
                continue
        out[meta.id] = {"provider": meta.id, "name": meta.name, "type": "native", "is_verified": True}
    return out


@asynccontextmanager
async def build_skill_context(
    spec: ToolSpec,
    tenant_id: Any,
    project_id: Any,
    user_id: Any,
    conversation_id: Any,
    *,
    run_id: Any = None,
    tool_call_id: Optional[str] = None,
    bindings: Optional[Dict[str, Any]] = None,
    key_resolver=resolve_tool_keys,
    connector_resolver=resolve_tool_connectors,
) -> AsyncIterator[SkillContext]:
    """Yield a SkillContext holding only what `spec` declares; any DB session is closed on exit."""
    from app.core.database import set_tenant_context

    needs_session = bool(spec.needs_keys or spec.needs_db or spec.needs_connectors)
    session = None
    try:
        api_keys: Dict[str, str] = {}
        connectors: Dict[str, Dict[str, Any]] = {}
        if needs_session:
            session = _session_factory()()
            await set_tenant_context(session, str(tenant_id))
            if spec.needs_keys:
                api_keys = await key_resolver(session, tenant_id, spec.needs_keys)
            if spec.needs_connectors:
                connectors = await connector_resolver(session, tenant_id)
            if not spec.needs_db:
                await session.close()
                session = None
        ctx = SkillContext.for_gateway(
            tenant_id=tenant_id,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            db=session if spec.needs_db else None,
            api_keys=api_keys,
            connectors=connectors,
        )
        if bindings:
            ctx.metadata.update(bindings)  # server-side scope the model cannot override
        yield ctx
        if session is not None:
            await session.commit()
    except BaseException:
        if session is not None:
            try:
                await session.rollback()
            except Exception:
                logger.warning("Session rollback failed", exc_info=True)
        raise
    finally:
        if session is not None:
            await session.close()


# --- rate limiting -----------------------------------------------------------

_WINDOWS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hour": 3600}


def parse_rate_limit(value: Any) -> Optional[Tuple[int, int]]:
    """'30/min' -> (30, 60). A bare number means per minute. Unparseable means no limit."""
    if value is None:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*(?:/\s*([a-zA-Z]+))?\s*", str(value))
    if not m or int(m.group(1)) <= 0:
        return None
    unit = (m.group(2) or "min").lower()
    window = _WINDOWS.get(unit)
    return (int(m.group(1)), window) if window else None


_skill_rate_limits: Optional[Dict[str, str]] = None


def skill_rate_limit(name: str) -> Optional[str]:
    """`rate-limit` frontmatter of the tool's SKILL.md, if any."""
    global _skill_rate_limits
    if _skill_rate_limits is None:
        found: Dict[str, str] = {}
        for md in SKILLS_DIR.glob("*/SKILL.md"):
            try:
                text = md.read_text()
                if text.startswith("---"):
                    front = yaml.safe_load(text.split("---", 2)[1]) or {}
                    if front.get("rate-limit") is not None:
                        found[str(front.get("name", md.parent.name))] = str(front["rate-limit"])
            except Exception:
                logger.warning("Could not read rate limit from %s", md, exc_info=True)
        _skill_rate_limits = found
    return _skill_rate_limits.get(name)


class ToolRateLimited(HTTPException):
    def __init__(self, retry_after: int):
        super().__init__(
            status_code=429,
            detail="Tool rate limit exceeded",
            headers={"Retry-After": str(max(1, retry_after))},
        )


class ToolRateLimiter:
    """Fixed-window per-tenant, per-tool counter in Redis. No client means no limit."""

    def __init__(self, client: Any = None):
        self._client = client
        self._warned = False

    def _warn(self, why: str) -> None:
        if not self._warned:
            self._warned = True
            logger.warning("Tool rate limiting disabled: %s", why)

    async def check(self, tenant_id: Any, tool: str, limit: Optional[Tuple[int, int]]) -> None:
        if limit is None:
            return
        if self._client is None:
            self._warn("no Redis configured")
            return
        count, window = limit
        bucket = int(time.time() // window)
        key = f"toolrl:{tenant_id}:{tool}:{bucket}"
        try:
            used = await self._client.incr(key)
            if used == 1:
                await self._client.expire(key, window + 1)
        except Exception:
            self._warn("Redis unavailable")
            return
        if used > count:
            raise ToolRateLimited(window - int(time.time() % window))


_default_limiter: Optional[ToolRateLimiter] = None


def get_rate_limiter() -> ToolRateLimiter:
    global _default_limiter
    if _default_limiter is None:
        client = None
        try:
            from app.config import get_settings

            settings = get_settings()
            if settings.REDIS_URL:
                import redis.asyncio as redis

                client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        except Exception:
            logger.warning("Tool rate limiter could not create a Redis client", exc_info=True)
        _default_limiter = ToolRateLimiter(client)
        if client is None:
            _default_limiter._warn("no Redis configured")
    return _default_limiter


# --- result guards -----------------------------------------------------------


def guard_result(result: Any, tool: str, secrets: Iterable[str] = ()) -> Tuple[str, Dict[str, Any]]:
    """Serialize, redact (pattern scan plus the exact keys the tool held) and cap a tool result."""
    text = result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
    text, redactions = _scan_credentials(text, tool, None)
    for secret in secrets:
        if secret and len(secret) >= 8 and secret in text:
            text = text.replace(secret, "[REDACTED: tenant key]")
            redactions += 1
    details: Dict[str, Any] = {}
    if redactions:
        details["redactions"] = redactions
    raw = text.encode("utf-8")
    if len(raw) > MAX_OUTCOME_BYTES:
        text = raw[:MAX_OUTCOME_BYTES].decode("utf-8", errors="ignore") + TRUNCATION_MARKER
        details.update({"truncated": True, "originalBytes": len(raw)})
    return text, details


def _error(tool: str, error: str, message: str) -> ToolOutcome:
    return ToolOutcome(content=message, is_error=True, details={"error": error, "tool": tool})


async def _run_tool(
    name: str,
    args: Dict[str, Any],
    *,
    tenant_id: Any,
    project_id: Any,
    user_id: Any,
    conversation_id: Any,
    run_id: Any = None,
    tool_call_id: Optional[str] = None,
    bindings: Optional[Dict[str, Any]] = None,
    spec: Optional[ToolSpec] = None,
    timeout_ms: Optional[int] = None,
    key_resolver=resolve_tool_keys,
    connector_resolver=resolve_tool_connectors,
) -> ToolOutcome:
    """Run one skill with a scoped context (no approval or rate-limit checks)."""
    spec = spec or spec_for(name)
    timeout_s = (timeout_ms if timeout_ms is not None else spec.timeout_ms) / 1000
    held: Dict[str, str] = {}
    try:
        async with build_skill_context(
            spec, tenant_id, project_id, user_id, conversation_id,
            run_id=run_id, tool_call_id=tool_call_id, bindings=bindings,
            key_resolver=key_resolver, connector_resolver=connector_resolver,
        ) as ctx:
            held = dict(ctx.api_keys)
            result = await asyncio.wait_for(
                SkillRegistry.invoke_skill(ctx, name, **(args or {})), timeout=timeout_s
            )
    except asyncio.TimeoutError:
        return _error(name, "timeout", f"Tool '{name}' timed out after {timeout_s:g}s")
    except Exception as exc:
        logger.warning("Tool %s failed: %s", name, type(exc).__name__)
        return _error(name, type(exc).__name__, f"Tool '{name}' failed: {type(exc).__name__}")

    if isinstance(result, ToolOutcome):  # a tool that reports its own error flag and details (Deep Work tools)
        content, guard_details = guard_result(result.content, name, held.values())
        return ToolOutcome(content=content, is_error=result.is_error, details={**result.details, **guard_details})
    content, details = guard_result(result, name, held.values())
    return ToolOutcome(content=content, is_error=False, details=details)


class ToolApprovalRefused(GatewayError):
    """403 with ``approval_required`` / ``approval_arguments_mismatch`` / ``approval_consumed``; nothing ran."""

    def __init__(self, code: str):
        super().__init__(403, {"error": code})
        self.code = code


# One in-process gate per approval: the row lock serialises across processes, this makes the
# common single-process case (and SQLite tests, which ignore FOR UPDATE) exactly-once too.
_approval_gates: Dict[Tuple[str, str, str], asyncio.Lock] = {}


async def _guarded_run(name: str, args: Dict[str, Any], *, tenant_id: Any, run_id: Any,
                       tool_call_id: Optional[str], **kwargs: Any) -> ToolOutcome:
    """Run a ``requires_approval`` tool only against an unused, matching, approved ledger row (KTD7)."""
    from app.core.database import set_tenant_context
    from app.services.approval_service import ApprovalError, ApprovalService
    from app.services.tool_policy import canonical_args_hash

    if run_id is None or not tool_call_id:
        raise ToolApprovalRefused("approval_required")
    gate_key = (str(tenant_id), str(run_id), str(tool_call_id))
    gate = _approval_gates.setdefault(gate_key, asyncio.Lock())
    session = _session_factory()()
    try:
        async with gate:
            if session.get_bind().dialect.name == "postgresql":
                await set_tenant_context(session, str(tenant_id))
            service = ApprovalService(session)
            row = await service.find_for_execution(tenant_id, str(run_id), str(tool_call_id), lock=True)
            try:
                service.check_executable(row, canonical_args_hash(args or {}))
            except ApprovalError as e:
                raise ToolApprovalRefused(e.code) from None
            outcome = await _run_tool(
                name, args, tenant_id=tenant_id, run_id=run_id, tool_call_id=tool_call_id, **kwargs)
            if not outcome.is_error:
                await service.consume(row)
                await session.commit()
            return outcome
    except BaseException:
        try:
            await session.rollback()
        except Exception:
            logger.warning("Approval guard rollback failed", exc_info=True)
        raise
    finally:
        await session.close()
        if not gate.locked() and not getattr(gate, "_waiters", None):
            _approval_gates.pop(gate_key, None)


async def execute_tool(
    name: str,
    args: Dict[str, Any],
    *,
    rate_limit: Any = None,
    limiter: Optional[ToolRateLimiter] = None,
    spec: Optional[ToolSpec] = None,
    **kwargs: Any,
) -> ToolOutcome:
    """Run one skill with a scoped context.

    Raises ToolRateLimited (429) and ToolApprovalRefused (403); everything else is an outcome. A
    ``requires_approval`` tool never reaches its handler without an approved, unused ledger row whose
    argument hash matches (KTD7); other tools are untouched.
    """
    spec = spec or spec_for(name)
    limit = parse_rate_limit(rate_limit if rate_limit is not None else skill_rate_limit(name))
    await (limiter or get_rate_limiter()).check(kwargs.get("tenant_id"), name, limit)
    if spec.requires_approval:
        return await _guarded_run(name, args, spec=spec, **kwargs)
    return await _run_tool(name, args, spec=spec, **kwargs)
