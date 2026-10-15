"""
E2B Sandbox Executor for secure code and shell execution.

Uses E2B's Firecracker microVMs for hypervisor-level isolation.
Manages multiple concurrent sandboxes keyed by (session_id, worker_id).

Two access patterns:
- SandboxHandle (new): Bound context with run_shell(), execute_code(), extend_timeout().
  Obtained via get_or_create(session_id, worker_id).
- Legacy API: create_sandbox() + execute_code(sandbox_id) for execute_with_lifecycle().

Key design decisions:
- In-memory dict + asyncio.Lock for sandbox tracking (single-process)
- Cost logged at teardown, no real-time tracking
- extend_timeout() in finally blocks on every execution
- Path traversal protection on all downloads
"""

import asyncio
import json
import logging
import os
import time
import uuid as _uuid
from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Any, Optional

from app.config import get_settings
from app.services.session_storage import get_session_storage
from app.services.redis_ha import get_redis_service

logger = logging.getLogger(__name__)

# Module constants (promote to config when deployments need to override)
_DEFAULT_TIMEOUT = 3600  # 1 hour max sandbox lifetime
_EXECUTION_TIMEOUT = 60  # 60 seconds per code execution
_MAX_COST_PER_EXECUTION = 0.50  # USD of real LLM spend per execution (enforced by sandbox_proxy)
_PROVIDER_KEY_ENV_NAMES = frozenset({
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY",
    "GEMINI_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY", "GROQ_API_KEY",
    "AZURE_OPENAI_API_KEY", "TOGETHER_API_KEY", "DEEPSEEK_API_KEY",
})
_MAX_DOWNLOAD_SIZE_MB = 100
_MAX_DOWNLOAD_FILES = 50
_COST_PER_SECOND = 0.000014  # E2B pricing: $0.000014/sec/vCPU

# Redis key pattern: sandbox:{session_key}
# Value: JSON {"sandbox_id": "...", "instance_id": "...", "created_at": ..., "status": "active"}
_SANDBOX_TTL = _DEFAULT_TIMEOUT + 300  # sandbox timeout + 5 min buffer

# Unique per process. Uses PID to avoid collisions under gunicorn --preload
# (module imported once in master, forked to workers — uuid4 would be shared).
# PID is unique per worker process after fork.
_INSTANCE_ID = f"{str(_uuid.uuid4())[:8]}-{os.getpid()}"


async def _redis_get_sandbox(key: str) -> dict | None:
    """Read sandbox metadata from Redis. Returns None on miss or Redis failure."""
    try:
        redis = await get_redis_service()
        raw = await redis.get(f"sandbox:{key}")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


async def _redis_set_sandbox(key: str, sandbox_id: str) -> bool | None:
    """Write sandbox metadata to Redis with TTL. Uses NX (set-if-not-exists) as distributed lock.

    Returns:
        True  — registered successfully (this instance won)
        False — NX race lost (another instance already registered)
        None  — Redis unavailable (operation failed)

    Callers must distinguish False (race lost) from None (Redis down) to log
    accurately. RedisHAService.set() returns False for both "NX key exists"
    and "circuit breaker open", so we catch exceptions to disambiguate.
    """
    try:
        redis = await get_redis_service()
        data = json.dumps({
            "sandbox_id": sandbox_id,
            "instance_id": _INSTANCE_ID,
            "created_at": time.time(),
            "status": "active",
        })
        result = await redis.set(f"sandbox:{key}", data, ttl=_SANDBOX_TTL, nx=True)
        return result  # True = won, False = NX race lost
    except Exception:
        logger.warning("Failed to write sandbox to Redis for key %s", key)
        return None  # Redis unavailable


async def _redis_delete_sandbox(key: str, owner_only: bool = True) -> None:
    """Remove sandbox metadata from Redis.

    Args:
        owner_only: If True (default), only delete if this instance owns the entry.
                    Prevents Instance B's teardown from deleting Instance A's
                    still-running sandbox from the registry.
    """
    try:
        redis = await get_redis_service()
        if owner_only:
            raw = await redis.get(f"sandbox:{key}")
            if raw:
                meta = json.loads(raw)
                if meta.get("instance_id") != _INSTANCE_ID:
                    logger.debug(
                        "Skipping Redis delete for sandbox:%s — owned by instance %s",
                        key, meta.get("instance_id"),
                    )
                    return
        await redis.delete(f"sandbox:{key}")
    except Exception:
        pass


@dataclass
class SandboxSession:
    """Metadata for an active sandbox session."""
    sandbox_id: str
    template: str
    timeout_seconds: int


@dataclass
class ExecutionResult:
    """Result of a sandbox code execution."""
    success: bool
    stdout: str
    stderr: str
    duration_seconds: float
    error: Optional[str] = None
    output_files: list[str] | None = None


@dataclass
class ShellResult:
    """Result of a shell command execution."""
    stdout: str
    stderr: str
    exit_code: int


class SandboxHandle:
    """Bound handle to an active sandbox. Call methods directly on the handle."""

    def __init__(self, sandbox: Any, key: str, executor: "SandboxExecutor"):
        self._sandbox = sandbox
        self._key = key
        self._executor = executor
        self._created_at = time.time()
        self.token: str | None = None  # Sandbox control plane token for revocation

    @property
    def sandbox_id(self) -> str:
        return self._sandbox.sandbox_id

    async def run_shell(self, command: str, timeout: int = _EXECUTION_TIMEOUT) -> ShellResult:
        """Execute a shell command. Extends timeout automatically."""
        try:
            result = await self._sandbox.commands.run(command, timeout=timeout)
            return ShellResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
            )
        except Exception as e:
            # E2B SDK raises CommandExitException on non-zero exit codes.
            # Extract stdout/stderr/exit_code from the exception.
            exit_code = getattr(e, "exit_code", 1)
            stdout = getattr(e, "stdout", "")
            stderr = getattr(e, "stderr", str(e))
            return ShellResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
        finally:
            await self.extend_timeout()

    async def execute_code(self, code: str, timeout: int = _EXECUTION_TIMEOUT) -> ExecutionResult:
        """Execute Python via code interpreter. Returns structured output with charts."""
        start_time = time.monotonic()
        try:
            execution = await self._sandbox.run_code(code, timeout=timeout)
            duration = time.monotonic() - start_time

            stdout = ""
            stderr = ""
            if execution.logs:
                stdout = _join_log_lines(execution.logs.stdout)
                stderr = _join_log_lines(execution.logs.stderr)

            output_files: list[str] = []
            if execution.results:
                for result in execution.results:
                    if hasattr(result, "png") and result.png:
                        output_files.append("output.png")
                    if hasattr(result, "text") and result.text:
                        if result.text not in stdout:
                            stdout += result.text + "\n"

            has_error = bool(execution.error)
            error_msg = None
            if has_error:
                error_msg = str(execution.error)
                stderr += f"\n{error_msg}" if stderr else error_msg

            return ExecutionResult(
                success=not has_error,
                stdout=stdout.strip(),
                stderr=stderr.strip(),
                duration_seconds=duration,
                error=error_msg,
                output_files=output_files if output_files else None,
            )
        except Exception as e:
            duration = time.monotonic() - start_time
            return ExecutionResult(
                success=False, stdout="", stderr=str(e),
                duration_seconds=duration, error=str(e),
            )
        finally:
            await self.extend_timeout()

    async def extend_timeout(self, seconds: int = _DEFAULT_TIMEOUT) -> None:
        """Reset sandbox TTL. Called in finally after every action."""
        try:
            await self._sandbox.set_timeout(seconds)
        except Exception:
            logger.warning("Failed to extend sandbox timeout for %s", self._key)
        # Keep token TTL synchronized with sandbox lifetime
        if self.token:
            try:
                from app.services.sandbox_tokens import extend_sandbox_token_ttl
                await extend_sandbox_token_ttl(self.token, seconds)
            except Exception:
                logger.warning("Failed to extend sandbox token TTL for %s", self._key)

    async def upload_files(self, files: dict[str, bytes | str]) -> None:
        """Upload files to /workspace/ in sandbox."""
        for path, content in files.items():
            await self._sandbox.files.write(f"/workspace/{path}", content)

    async def download_workspace(self) -> dict[str, bytes]:
        """Download files from /workspace/. Returns {filename: content}."""
        result: dict[str, bytes] = {}
        try:
            entries = await self._sandbox.files.list("/workspace")
            for entry in entries:
                if not hasattr(entry, "type") or entry.type == "file":
                    name = entry.name if hasattr(entry, "name") else str(entry)
                    # Path traversal protection
                    if ".." in name or name.startswith("/"):
                        continue
                    try:
                        content = await self._sandbox.files.read(f"/workspace/{name}")
                        result[name] = content if isinstance(content, bytes) else content.encode()
                    except Exception:
                        logger.debug("Failed to read /workspace/%s", name)
        except Exception:
            logger.warning("Failed to list /workspace for %s", self._key)
        return result

    async def kill(self) -> None:
        """Kill this sandbox and log estimated cost."""
        duration = time.time() - self._created_at
        cost_estimate = duration * _COST_PER_SECOND
        logger.info(
            "Sandbox %s killed after %.0fs (~$%.4f)",
            self._key, duration, cost_estimate,
        )
        # Revoke control plane token before killing sandbox
        if self.token:
            try:
                from app.services.sandbox_tokens import revoke_sandbox_token
                await revoke_sandbox_token(self.token)
            except Exception:
                logger.warning("Failed to revoke sandbox token for %s", self._key)
        try:
            await self._sandbox.kill()
        except Exception:
            logger.warning("Failed to kill sandbox %s", self._key)


class SandboxExecutor:
    """
    Manages E2B sandbox lifecycle for multiple concurrent sandboxes.

    New API (recommended):
        handle = await executor.get_or_create(session_id, worker_id)
        result = await handle.run_shell("python3 script.py")
        await executor.teardown(session_id, worker_id)

    Legacy API (backward compat for execute_with_lifecycle):
        session = await executor.create_sandbox()
        result = await executor.execute_code(session.sandbox_id, code)
        await executor.close_sandbox(session.sandbox_id)
    """

    def __init__(self):
        self._settings = get_settings()
        self._storage = get_session_storage()
        self._sandboxes: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _resolve_api_key(self, api_key: str | None = None) -> str | None:
        """Resolve E2B API key: explicit param → env var."""
        return api_key or self._settings.E2B_API_KEY or None

    def _check_enabled(self, api_key: str | None = None) -> tuple[bool, Optional[str]]:
        """Check if sandbox execution is enabled and configured."""
        if not self._settings.ENABLE_SANDBOX:
            return False, "Sandbox execution is disabled. Set ENABLE_SANDBOX=True."
        if not self._resolve_api_key(api_key):
            return False, "E2B API key not configured. Add it in Settings > API Keys or set E2B_API_KEY in environment."
        if not self._settings.SANDBOX_PROXY_URL:
            # Fail closed: without a proxy the sandbox would have no key-free LLM path.
            return False, (
                "SANDBOX_PROXY_URL is not set. Sandboxes only get a proxy token (never a "
                "provider key), so a backend URL reachable from the sandbox is required. "
                "Local dev: tunnel the backend (e.g. ngrok http 8000) and set SANDBOX_PROXY_URL."
            )
        return True, None

    @staticmethod
    def _guard_env(env_vars: dict[str, str] | None) -> None:
        """Refuse to hand a real LLM provider key to a sandbox."""
        for name, value in (env_vars or {}).items():
            if name in _PROVIDER_KEY_ENV_NAMES and not (
                name == "OPENAI_API_KEY" and str(value).startswith("sbx_tok_")
            ):
                raise RuntimeError(
                    f"Refusing to inject provider key {name} into a sandbox; "
                    "use a sandbox proxy token (OPENAI_API_KEY=sbx_tok_...) instead."
                )

    def _network_opts(self) -> dict[str, Any] | None:
        """Egress allow-list for E2B: deny all, allow proxy host + SANDBOX_EGRESS_ALLOW."""
        raw = getattr(self._settings, "SANDBOX_EGRESS_ALLOW", "")
        entries = [e.strip() for e in raw.split(",") if e.strip()] if isinstance(raw, str) else []
        if "*" in entries:
            return None  # explicit operator opt-out
        proxy = getattr(self._settings, "SANDBOX_PROXY_URL", "")
        host = urlparse(proxy).hostname if isinstance(proxy, str) else None
        allow = list(dict.fromkeys(([host] if host else []) + entries))
        return {"allow_out": allow, "deny_out": ["0.0.0.0/0"]}

    # ── New multi-sandbox API ──────────────────────────────────

    async def get_or_create(
        self, session_id: str, worker_id: str | None = None,
        api_key: str | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> SandboxHandle:
        """Get existing sandbox or create a new one. Thread-safe via asyncio.Lock.

        Args:
            session_id: Session identifier.
            worker_id: Optional worker identifier for per-worker isolation.
            api_key: Optional E2B API key override (from user's stored keys).
                     Falls back to E2B_API_KEY env var if not provided.
            env_vars: Optional env vars injected at sandbox creation time.
                      Used by SandboxConnector to inject credentials securely.
        """
        enabled, error = self._check_enabled(api_key)
        if not enabled:
            raise RuntimeError(error)
        self._guard_env(env_vars)

        resolved_key = self._resolve_api_key(api_key)
        key = f"{session_id}:{worker_id}" if worker_id else session_id

        # Fast path: already exists
        if key in self._sandboxes:
            return SandboxHandle(self._sandboxes[key], key, self)

        # Acquire per-key lock to prevent duplicate creation
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Double-check after acquiring lock
            if key in self._sandboxes:
                return SandboxHandle(self._sandboxes[key], key, self)

            # Check Redis for sandbox created by another instance
            redis_meta = await _redis_get_sandbox(key)
            if redis_meta and redis_meta.get("sandbox_id"):
                try:
                    from e2b_code_interpreter import AsyncSandbox
                    sandbox = await AsyncSandbox.connect(
                        redis_meta["sandbox_id"],
                        api_key=resolved_key,
                    )
                    self._sandboxes[key] = sandbox
                    logger.info(
                        "Reconnected to E2B sandbox %s for key %s (from instance %s)",
                        redis_meta["sandbox_id"], key, redis_meta.get("instance_id"),
                    )
                    return SandboxHandle(sandbox, key, self)
                except Exception as e:
                    logger.warning(
                        "Failed to reconnect to sandbox %s: %s. Creating new.",
                        redis_meta["sandbox_id"], e,
                    )
                    await _redis_delete_sandbox(key, owner_only=False)

            # Create new sandbox
            try:
                from e2b_code_interpreter import AsyncSandbox

                template = getattr(self._settings, "E2B_TEMPLATE_ID", None)
                create_kwargs: dict[str, Any] = {
                    "api_key": resolved_key,
                    "timeout": _DEFAULT_TIMEOUT,
                }
                if template:
                    create_kwargs["template"] = template
                if env_vars:
                    create_kwargs["envs"] = env_vars
                network = self._network_opts()
                if network:
                    create_kwargs["network"] = network

                sandbox = await AsyncSandbox.create(**create_kwargs)
                self._sandboxes[key] = sandbox

                # Register in Redis (NX = distributed lock to prevent duplicates)
                registered = await _redis_set_sandbox(key, sandbox.sandbox_id)
                if registered is None:
                    # Redis unavailable — sandbox works fine locally, just not discoverable
                    logger.info(
                        "Redis unavailable — sandbox %s for key %s is local-only",
                        sandbox.sandbox_id, key,
                    )
                elif not registered:
                    # NX race lost — another instance registered first
                    # Keep ours locally. The duplicate will be cleaned up by TTL expiry
                    # or cleanup_orphans(). Cost: one extra sandbox (~$0.05/hr).
                    logger.warning(
                        "NX race lost for key %s — duplicate sandbox %s. "
                        "Orphan will expire via TTL.",
                        key, sandbox.sandbox_id,
                    )

                logger.info("Created E2B sandbox %s for key %s", sandbox.sandbox_id, key)
                return SandboxHandle(sandbox, key, self)

            except ImportError:
                raise RuntimeError(
                    "e2b-code-interpreter package not installed. "
                    "Run: pip install e2b-code-interpreter"
                )
            except Exception as e:
                logger.error("Failed to create E2B sandbox for key %s: %s", key, e)
                raise RuntimeError(f"Sandbox creation failed: {e}")

    async def teardown(self, session_id: str, worker_id: str | None = None) -> None:
        """Kill sandbox and remove from tracking."""
        key = f"{session_id}:{worker_id}" if worker_id else session_id
        sandbox = self._sandboxes.pop(key, None)
        if sandbox:
            handle = SandboxHandle(sandbox, key, self)
            await handle.kill()
            # Only delete Redis if we owned this sandbox
            await _redis_delete_sandbox(key, owner_only=True)
        self._locks.pop(key, None)

    async def teardown_session(self, session_id: str) -> None:
        """Kill all sandboxes for a session (all workers + session sandbox)."""
        keys_to_remove = [k for k in self._sandboxes if k.startswith(f"{session_id}:") or k == session_id]
        for key in keys_to_remove:
            sandbox = self._sandboxes.pop(key, None)
            if sandbox:
                handle = SandboxHandle(sandbox, key, self)
                await handle.kill()
                await _redis_delete_sandbox(key, owner_only=True)
            self._locks.pop(key, None)

    async def cleanup_orphans(self) -> int:
        """Reconcile E2B sandbox list with local tracking + Redis. Kill true orphans only."""
        try:
            from e2b_code_interpreter import AsyncSandbox

            active = await AsyncSandbox.list()
            tracked_ids = {s.sandbox_id for s in self._sandboxes.values()}

            # Collect sandbox IDs registered in Redis by ANY instance
            redis_tracked_ids: set[str] = set()
            for sb in active:
                meta = await _redis_get_sandbox(sb.sandbox_id)
                if meta and meta.get("sandbox_id"):
                    redis_tracked_ids.add(meta["sandbox_id"])

            # Only kill sandboxes not tracked locally AND not in Redis
            orphans = [
                s for s in active
                if s.sandbox_id not in tracked_ids
                and s.sandbox_id not in redis_tracked_ids
            ]
            for orphan in orphans:
                try:
                    await orphan.kill()
                    logger.warning("Killed orphaned sandbox %s", orphan.sandbox_id)
                except Exception:
                    pass
            return len(orphans)
        except Exception:
            logger.exception("Failed to clean up orphan sandboxes")
            return 0

    # ── Legacy single-sandbox API (backward compat) ────────────

    async def create_sandbox(
        self,
        timeout_seconds: int = _DEFAULT_TIMEOUT,
        env_vars: dict[str, str] | None = None,
    ) -> SandboxSession:
        """Create a new E2B sandbox instance (legacy API)."""
        enabled, error = self._check_enabled()
        if not enabled:
            raise RuntimeError(error)
        self._guard_env(env_vars)

        try:
            from e2b_code_interpreter import AsyncSandbox

            create_kwargs: dict = {
                "api_key": self._settings.E2B_API_KEY,
                "timeout": timeout_seconds,
            }
            if env_vars:
                create_kwargs["envs"] = env_vars
            network = self._network_opts()
            if network:
                create_kwargs["network"] = network

            sandbox = await AsyncSandbox.create(**create_kwargs)

            sandbox_id = sandbox.sandbox_id
            logger.info("Created E2B sandbox: %s", sandbox_id)

            # Store in shared dict (keyed by sandbox_id for legacy lookup)
            self._sandboxes[sandbox_id] = sandbox

            return SandboxSession(
                sandbox_id=sandbox_id,
                template=getattr(self._settings, "E2B_TEMPLATE_ID", "default"),
                timeout_seconds=timeout_seconds,
            )

        except ImportError:
            raise RuntimeError(
                "e2b-code-interpreter package not installed. "
                "Run: pip install e2b-code-interpreter"
            )
        except Exception as e:
            logger.error("Failed to create E2B sandbox: %s", e)
            raise RuntimeError(f"Sandbox creation failed: {e}")

    async def execute_code(
        self,
        sandbox_id: str,
        code: str,
        working_dir: str = "/home/user",
        timeout: int = _EXECUTION_TIMEOUT,
    ) -> ExecutionResult:
        """Execute Python code in an E2B sandbox (legacy API)."""
        sandbox = self._get_sandbox(sandbox_id)
        if not sandbox:
            return ExecutionResult(
                success=False, stdout="", stderr="",
                duration_seconds=0,
                error=f"Sandbox {sandbox_id} not found or expired.",
            )

        start_time = time.monotonic()
        try:
            execution = await sandbox.run_code(code, timeout=timeout)
            duration = time.monotonic() - start_time

            stdout = ""
            stderr = ""
            if execution.logs:
                stdout = _join_log_lines(execution.logs.stdout)
                stderr = _join_log_lines(execution.logs.stderr)

            output_files: list[str] = []
            if execution.results:
                for result in execution.results:
                    if hasattr(result, "png") and result.png:
                        output_files.append("output.png")
                    if hasattr(result, "text") and result.text:
                        if result.text not in stdout:
                            stdout += result.text + "\n"

            has_error = bool(execution.error)
            error_msg = None
            if has_error:
                error_msg = str(execution.error)
                stderr += f"\n{error_msg}" if stderr else error_msg

            return ExecutionResult(
                success=not has_error,
                stdout=stdout.strip(),
                stderr=stderr.strip(),
                duration_seconds=duration,
                error=error_msg,
                output_files=output_files if output_files else None,
            )

        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error("Sandbox execution failed: %s", e)
            return ExecutionResult(
                success=False, stdout="", stderr=str(e),
                duration_seconds=duration, error=str(e),
            )

    async def upload_to_sandbox(
        self,
        sandbox_id: str,
        tenant_id: str,
        session_id: str,
        workspace_paths: list[str],
    ) -> None:
        """Upload workspace files to sandbox (legacy API)."""
        sandbox = self._get_sandbox(sandbox_id)
        if not sandbox:
            raise RuntimeError(f"Sandbox {sandbox_id} not found.")

        for path in workspace_paths:
            content = await self._storage.read(tenant_id, session_id, f"workspace/{path}")
            if content:
                await sandbox.files.write(f"/home/user/{path}", content)
                logger.debug("Uploaded %s to sandbox %s", path, sandbox_id)

    async def download_from_sandbox(
        self,
        sandbox_id: str,
        tenant_id: str,
        session_id: str,
        sandbox_paths: list[str],
        target_prefix: str = "workspace/code_outputs",
    ) -> list[str]:
        """Download files from sandbox to workspace (legacy API)."""
        sandbox = self._get_sandbox(sandbox_id)
        if not sandbox:
            raise RuntimeError(f"Sandbox {sandbox_id} not found.")

        if len(sandbox_paths) > _MAX_DOWNLOAD_FILES:
            raise ValueError(
                f"Too many files to download: {len(sandbox_paths)} "
                f"(max: {_MAX_DOWNLOAD_FILES})"
            )

        downloaded = []
        total_size = 0

        for sandbox_path in sandbox_paths:
            try:
                content = await sandbox.files.read(sandbox_path)

                if isinstance(content, bytes):
                    file_size = len(content)
                else:
                    file_size = len(content.encode("utf-8")) if content else 0

                total_size += file_size
                if total_size > _MAX_DOWNLOAD_SIZE_MB * 1024 * 1024:
                    raise ValueError(
                        f"Download size limit exceeded: {total_size / (1024*1024):.1f}MB "
                        f"(max: {_MAX_DOWNLOAD_SIZE_MB}MB)"
                    )

                filename = sandbox_path.split("/")[-1]
                target_path = f"{target_prefix}/{filename}"

                if isinstance(content, bytes):
                    await self._storage.write_binary(tenant_id, session_id, target_path, content)
                else:
                    await self._storage.write(tenant_id, session_id, target_path, content)

                downloaded.append(target_path)
                logger.debug("Downloaded %s -> %s", sandbox_path, target_path)

            except ValueError:
                raise
            except Exception as e:
                logger.warning("Failed to download %s: %s", sandbox_path, e)

        return downloaded

    async def close_sandbox(self, sandbox_id: str) -> None:
        """Close and clean up a sandbox (legacy API)."""
        sandbox = self._sandboxes.pop(sandbox_id, None)
        if sandbox:
            try:
                await sandbox.kill()
                logger.info("Closed E2B sandbox: %s", sandbox_id)
            except Exception as e:
                logger.warning("Failed to close sandbox %s: %s", sandbox_id, e)

    def _get_sandbox(self, sandbox_id: str) -> Any:
        """Get sandbox by its E2B sandbox_id."""
        # Direct dict lookup (new storage)
        if sandbox_id in self._sandboxes:
            return self._sandboxes[sandbox_id]
        # Scan values for matching sandbox_id (handles session-keyed entries)
        for sandbox in self._sandboxes.values():
            if hasattr(sandbox, "sandbox_id") and sandbox.sandbox_id == sandbox_id:
                return sandbox
        return None

    async def execute_with_lifecycle(
        self,
        code: str,
        tenant_id: str,
        session_id: str,
        input_files: list[str] | None = None,
        timeout: int = _EXECUTION_TIMEOUT,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """
        Full sandbox lifecycle: create -> upload -> execute -> download -> close.

        Used by the node executor after human review approval.
        """
        sandbox_session = None
        try:
            sandbox_session = await self.create_sandbox(env_vars=env_vars)

            if input_files:
                await self.upload_to_sandbox(
                    sandbox_session.sandbox_id, tenant_id, session_id, input_files,
                )

            result = await self.execute_code(sandbox_session.sandbox_id, code, timeout=timeout)

            if result.success and result.output_files:
                downloaded = await self.download_from_sandbox(
                    sandbox_session.sandbox_id, tenant_id, session_id,
                    [f"/home/user/{f}" for f in result.output_files],
                )
                result.output_files = downloaded

            return result

        except Exception as e:
            logger.error("Sandbox lifecycle failed: %s", e)
            return ExecutionResult(
                success=False, stdout="", stderr=str(e),
                duration_seconds=0, error=str(e),
            )
        finally:
            if sandbox_session:
                await self.close_sandbox(sandbox_session.sandbox_id)


# ── Module-level singleton ──────────────────────────────────────

def _join_log_lines(lines: Any) -> str:
    """Join E2B log output into a single string. Handles list[str], list[OutputMessage], or str."""
    if isinstance(lines, str):
        return lines
    if not lines:
        return ""
    parts = []
    for line in lines:
        if isinstance(line, str):
            parts.append(line)
        elif hasattr(line, "line"):
            parts.append(line.line)
        else:
            parts.append(str(line))
    return "".join(parts)


_executor_instance: SandboxExecutor | None = None


def get_sandbox_executor() -> SandboxExecutor:
    """Get or create the shared SandboxExecutor singleton."""
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = SandboxExecutor()
    return _executor_instance


async def resolve_e2b_key(tenant_id, user_id) -> str | None:
    """Resolve E2B API key from user's stored API keys in the database.

    Returns the decrypted key or None if not found.
    """
    from uuid import UUID
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal, set_tenant_context
    from app.models.api_key import APIKey
    from app.core.encryption import get_encryption_service

    tid = UUID(str(tenant_id)) if not isinstance(tenant_id, UUID) else tenant_id
    uid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id

    try:
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, str(tid))
            result = await db.execute(
                select(APIKey)
                .where(APIKey.tenant_id == tid)
                .where(APIKey.user_id == uid)
                .where(APIKey.provider == "e2b")
                .where(APIKey.is_active.is_(True))
                .limit(1)
            )
            key_row = result.scalar_one_or_none()
            if key_row and key_row.encrypted_key:
                encryption = get_encryption_service()
                return encryption.decrypt(key_row.encrypted_key)
    except Exception as exc:
        logger.warning("Failed to resolve E2B key from DB: %s", exc)
    return None
