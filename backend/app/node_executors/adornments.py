"""
Node Adornments System
Vellum-inspired decorators that modify node behavior without changing core logic

Adornments:
- RetryAdornment: Retry node execution on failure with configurable backoff
- TryAdornment: Catch errors and return fallback/error output instead of failing
- TimeoutAdornment: Add custom timeout to node execution
- CacheAdornment: Cache node results based on input hash

Usage:
Node configurations can include an "adornments" field:
{
    "adornments": [
        {"type": "retry", "maxRetries": 3, "backoffSeconds": 2, "backoffMultiplier": 2},
        {"type": "try", "fallbackValue": null, "catchAll": true}
    ]
}
"""

import asyncio
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

from sqlalchemy.ext.asyncio import AsyncSession

from app.node_executors import (
    BaseNodeExecutor,
    NodeExecutionContext,
    NodeExecutionResult
)

logger = logging.getLogger(__name__)


class BaseAdornment(ABC):
    """Base class for all node adornments"""

    adornment_type: str = "base"

    def __init__(self, config: Dict[str, Any]):
        """Initialize with configuration from node data"""
        self.config = config

    @abstractmethod
    async def wrap_execution(
        self,
        executor: BaseNodeExecutor,
        context: NodeExecutionContext,
        db: AsyncSession,
        next_handler
    ) -> NodeExecutionResult:
        """
        Wrap the node execution with additional behavior

        Args:
            executor: The node executor being wrapped
            context: Node execution context
            db: Database session
            next_handler: Callable to invoke the next adornment or actual execution

        Returns:
            NodeExecutionResult
        """
        pass


class RetryAdornment(BaseAdornment):
    """
    Retry node execution on failure with exponential backoff

    Config options:
    - maxRetries: Maximum number of retry attempts (default: 3)
    - backoffSeconds: Initial backoff delay in seconds (default: 1)
    - backoffMultiplier: Multiplier for exponential backoff (default: 2)
    - retryOnErrors: List of error types to retry on (default: all)
    - retryOnStatuses: List of status codes to retry on (default: ["failed"])
    """

    adornment_type = "retry"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.max_retries = config.get("maxRetries", 3)
        self.backoff_seconds = config.get("backoffSeconds", 1)
        self.backoff_multiplier = config.get("backoffMultiplier", 2)
        self.retry_on_errors = config.get("retryOnErrors", [])  # Empty = all errors
        self.retry_on_statuses = config.get("retryOnStatuses", ["failed"])

    def _should_retry(self, result: NodeExecutionResult) -> bool:
        """Determine if we should retry based on result"""
        if result.status not in self.retry_on_statuses:
            return False

        if self.retry_on_errors and result.error:
            # Only retry if error matches one of the specified types
            return any(err_type.lower() in result.error.lower()
                       for err_type in self.retry_on_errors)

        return True

    async def wrap_execution(
        self,
        executor: BaseNodeExecutor,
        context: NodeExecutionContext,
        db: AsyncSession,
        next_handler
    ) -> NodeExecutionResult:
        """Execute with retry logic"""
        last_result = None
        current_backoff = self.backoff_seconds

        for attempt in range(self.max_retries + 1):  # +1 for initial attempt
            result = await next_handler()
            last_result = result

            if result.status == "success" or not self._should_retry(result):
                return result

            if attempt < self.max_retries:
                logger.warning(
                    f"Node {context.node_id} failed (attempt {attempt + 1}/{self.max_retries + 1}), "
                    f"retrying in {current_backoff}s: {result.error}"
                )
                await asyncio.sleep(current_backoff)
                current_backoff *= self.backoff_multiplier

        # All retries exhausted
        logger.error(
            f"Node {context.node_id} failed after {self.max_retries + 1} attempts: "
            f"{last_result.error if last_result else 'Unknown error'}"
        )

        return NodeExecutionResult(
            node_id=context.node_id,
            status="failed",
            error=f"Failed after {self.max_retries + 1} attempts: {last_result.error if last_result else 'Unknown'}",
            elapsed_ms=last_result.elapsed_ms if last_result else 0
        )


class TryAdornment(BaseAdornment):
    """
    Catch errors and return fallback value instead of failing workflow

    Config options:
    - fallbackValue: Value to return on error (default: null)
    - catchAll: Whether to catch all errors or specific ones (default: true)
    - catchErrors: List of error types to catch (used when catchAll is false)
    - outputOnError: Structure to use for error output (default: {"error": "<message>"})
    """

    adornment_type = "try"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.fallback_value = config.get("fallbackValue")
        self.catch_all = config.get("catchAll", True)
        self.catch_errors = config.get("catchErrors", [])
        self.output_on_error = config.get("outputOnError", {"error": None})

    def _should_catch(self, error: str) -> bool:
        """Determine if we should catch this error"""
        if self.catch_all:
            return True

        return any(err_type.lower() in error.lower()
                   for err_type in self.catch_errors)

    async def wrap_execution(
        self,
        executor: BaseNodeExecutor,
        context: NodeExecutionContext,
        db: AsyncSession,
        next_handler
    ) -> NodeExecutionResult:
        """Execute with try-catch behavior"""
        start_time = time.time()

        try:
            result = await next_handler()

            if result.status == "failed" and result.error:
                if self._should_catch(result.error):
                    logger.info(
                        f"TryAdornment caught error in node {context.node_id}: {result.error}"
                    )

                    # Build error output
                    error_output = dict(self.output_on_error)
                    if "error" in error_output:
                        error_output["error"] = result.error

                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="success",  # Mark as success since we caught the error
                        output={
                            "output": self.fallback_value,
                            "caught_error": result.error,
                            **error_output
                        },
                        elapsed_ms=result.elapsed_ms
                    )

            return result

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            error_msg = str(e)

            if self._should_catch(error_msg):
                logger.info(
                    f"TryAdornment caught exception in node {context.node_id}: {error_msg}"
                )

                error_output = dict(self.output_on_error)
                if "error" in error_output:
                    error_output["error"] = error_msg

                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output={
                        "output": self.fallback_value,
                        "caught_error": error_msg,
                        **error_output
                    },
                    elapsed_ms=elapsed_ms
                )

            # Re-raise if we shouldn't catch this error
            raise


class TimeoutAdornment(BaseAdornment):
    """
    Add custom timeout to node execution

    Config options:
    - timeoutSeconds: Maximum execution time in seconds (default: 60)
    - fallbackOnTimeout: Whether to return fallback value instead of failing (default: false)
    - fallbackValue: Value to return on timeout (if fallbackOnTimeout is true)
    """

    adornment_type = "timeout"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.timeout_seconds = config.get("timeoutSeconds", 60)
        self.fallback_on_timeout = config.get("fallbackOnTimeout", False)
        self.fallback_value = config.get("fallbackValue")

    async def wrap_execution(
        self,
        executor: BaseNodeExecutor,
        context: NodeExecutionContext,
        db: AsyncSession,
        next_handler
    ) -> NodeExecutionResult:
        """Execute with timeout"""
        start_time = time.time()

        try:
            result = await asyncio.wait_for(
                next_handler(),
                timeout=self.timeout_seconds
            )
            return result

        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start_time) * 1000)
            error_msg = f"Node execution timed out after {self.timeout_seconds} seconds"

            logger.warning(f"TimeoutAdornment: {error_msg} for node {context.node_id}")

            if self.fallback_on_timeout:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="success",
                    output={
                        "output": self.fallback_value,
                        "timed_out": True,
                        "timeout_seconds": self.timeout_seconds
                    },
                    elapsed_ms=elapsed_ms
                )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=error_msg,
                elapsed_ms=elapsed_ms
            )


class CacheAdornment(BaseAdornment):
    """
    Cache node results based on input hash

    Config options:
    - ttlSeconds: Cache time-to-live in seconds (default: 3600)
    - cacheKey: Custom cache key prefix (default: node_id)
    - cacheInputs: Whether to include inputs in cache key (default: true)
    - cacheVariables: List of variables to include in cache key (default: all)

    Note: This is an in-memory cache per worker. For production,
    consider using Redis for distributed caching.
    """

    adornment_type = "cache"

    # Simple in-memory cache (per worker)
    _cache: Dict[str, tuple] = {}  # key -> (result, expiry_time)

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.ttl_seconds = config.get("ttlSeconds", 3600)
        self.cache_key_prefix = config.get("cacheKey")
        self.cache_inputs = config.get("cacheInputs", True)
        self.cache_variables = config.get("cacheVariables")  # None = all

    def _compute_cache_key(self, context: NodeExecutionContext) -> str:
        """Compute a cache key based on context"""
        key_parts = [
            self.cache_key_prefix or context.node_id,
            context.node_type,
        ]

        # Include relevant inputs
        if self.cache_inputs:
            inputs_str = json.dumps(context.inputs, sort_keys=True, default=str)
            key_parts.append(inputs_str)

        # Include relevant variables
        if self.cache_variables:
            vars_to_cache = {k: context.variables.get(k)
                            for k in self.cache_variables
                            if k in context.variables}
        else:
            vars_to_cache = context.variables

        vars_str = json.dumps(vars_to_cache, sort_keys=True, default=str)
        key_parts.append(vars_str)

        # Include node_data (configuration)
        data_str = json.dumps(context.node_data, sort_keys=True, default=str)
        key_parts.append(data_str)

        # Hash the combined key
        combined = "|".join(key_parts)
        return hashlib.sha256(combined.encode()).hexdigest()[:32]

    def _get_cached(self, key: str) -> Optional[NodeExecutionResult]:
        """Get cached result if valid"""
        if key not in self._cache:
            return None

        result, expiry_time = self._cache[key]
        if time.time() > expiry_time:
            del self._cache[key]
            return None

        return result

    def _set_cached(self, key: str, result: NodeExecutionResult):
        """Store result in cache"""
        expiry_time = time.time() + self.ttl_seconds
        self._cache[key] = (result, expiry_time)

        # Basic cache cleanup (remove ~10% of expired entries)
        current_time = time.time()
        expired_keys = [k for k, (_, exp) in list(self._cache.items())[:100]
                        if exp < current_time]
        for k in expired_keys:
            del self._cache[k]

    async def wrap_execution(
        self,
        executor: BaseNodeExecutor,
        context: NodeExecutionContext,
        db: AsyncSession,
        next_handler
    ) -> NodeExecutionResult:
        """Execute with caching"""
        cache_key = self._compute_cache_key(context)

        # Check cache
        cached_result = self._get_cached(cache_key)
        if cached_result:
            logger.info(f"CacheAdornment: Cache hit for node {context.node_id}")
            # Update output to indicate cache hit
            if cached_result.output and isinstance(cached_result.output, dict):
                cached_result.output["_cached"] = True
            return cached_result

        # Execute and cache
        result = await next_handler()

        # Only cache successful results
        if result.status == "success":
            self._set_cached(cache_key, result)
            logger.info(f"CacheAdornment: Cached result for node {context.node_id}")

        return result


# Registry of adornment types
ADORNMENT_REGISTRY: Dict[str, Type[BaseAdornment]] = {
    "retry": RetryAdornment,
    "try": TryAdornment,
    "timeout": TimeoutAdornment,
    "cache": CacheAdornment,
}


def parse_adornments(adornments_config: List[Dict[str, Any]]) -> List[BaseAdornment]:
    """Parse adornment configuration into adornment instances"""
    adornments = []

    for config in adornments_config:
        adornment_type = config.get("type", "").lower()

        if adornment_type not in ADORNMENT_REGISTRY:
            logger.warning(f"Unknown adornment type: {adornment_type}")
            continue

        adornment_class = ADORNMENT_REGISTRY[adornment_type]
        adornments.append(adornment_class(config))

    return adornments


async def execute_with_adornments(
    executor: BaseNodeExecutor,
    context: NodeExecutionContext,
    db: AsyncSession,
    adornments: List[BaseAdornment]
) -> NodeExecutionResult:
    """
    Execute a node with all configured adornments applied

    Adornments are applied in order, with the first adornment being
    the outermost wrapper.
    """

    # Build the execution chain from inside out
    async def base_execution():
        return await executor.execute(context, db)

    # Wrap each adornment around the chain
    current_handler = base_execution

    for adornment in reversed(adornments):
        # Capture the current handler for this closure
        handler = current_handler

        async def wrapped_handler(
            adornment=adornment,
            handler=handler
        ):
            return await adornment.wrap_execution(
                executor, context, db, handler
            )

        current_handler = wrapped_handler

    # Execute the full chain
    return await current_handler()
