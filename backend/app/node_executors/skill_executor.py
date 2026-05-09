"""
Skill Node Executor
Executes individual skills from the skill registry

Phase 1 Skills: Now builds and passes SkillContext to handlers,
enabling skills to access API keys, connectors, and tenant context.

Phase 3 Audit: Logs all skill executions for compliance and debugging.
"""

import logging
import time
import inspect
from typing import Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from app.agents.skills.registry import SkillRegistry
from app.agents.skills.context import SkillContext

logger = logging.getLogger(__name__)


class SkillOutput(BaseModel):
    """Output schema for Skill node"""
    result: Any
    skill_name: str
    category: str
    execution_time: float


class SkillExecutor(BaseNodeExecutor):
    """Executor for Skill node - executes individual skills"""

    node_type = "skill"

    # Skills may call external APIs (web search, market data, etc.)
    makes_external_calls = True

    async def _load_api_keys(
        self,
        db: AsyncSession,
        user_id: str
    ) -> Dict[str, str]:
        """
        Load decrypted API keys for the user.

        Args:
            db: Database session
            user_id: User ID to load keys for

        Returns:
            Dict of provider -> decrypted API key
        """
        try:
            from app.models.api_key import APIKey
            from app.core.encryption import get_encryption_service

            stmt = (
                select(APIKey)
                .where(APIKey.user_id == user_id)
                .where(APIKey.is_active == True)
            )

            result = await db.execute(stmt)
            api_keys_records = result.scalars().all()

            encryption = get_encryption_service()
            api_keys: Dict[str, str] = {}

            for record in api_keys_records:
                try:
                    decrypted = encryption.decrypt(record.encrypted_key)
                    api_keys[record.provider] = decrypted
                except Exception:
                    # Skip keys that fail to decrypt
                    pass

            return api_keys
        except Exception:
            # Return empty dict if loading fails
            return {}

    async def _load_connectors(
        self,
        db: AsyncSession,
        user_id: str,
        tenant_id: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Load connector configurations for the user/tenant.

        Args:
            db: Database session
            user_id: User ID
            tenant_id: Optional tenant ID

        Returns:
            Dict of connector_id -> configuration
        """
        try:
            from app.models.connector import ConnectorInstance

            stmt = select(ConnectorInstance).where(
                ConnectorInstance.user_id == user_id,
                ConnectorInstance.is_active == True
            )

            if tenant_id:
                stmt = stmt.where(ConnectorInstance.tenant_id == tenant_id)

            result = await db.execute(stmt)
            connector_records = result.scalars().all()

            connectors: Dict[str, Dict[str, Any]] = {}
            for record in connector_records:
                connectors[record.connector_id] = {
                    "id": str(record.id),
                    "connector_id": record.connector_id,
                    "config": record.config or {},
                    "credentials": record.credentials or {},
                }

            return connectors
        except Exception:
            # Return empty dict if loading fails
            return {}

    async def _log_audit(
        self,
        db: AsyncSession,
        *,
        skill_name: str,
        context: NodeExecutionContext,
        inputs: Optional[Dict[str, Any]],
        outputs: Optional[Dict[str, Any]],
        status: str,
        duration_ms: int,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        """Log skill execution for audit trail."""
        try:
            from app.services.skill_audit import SkillAuditService

            # Only log if tenant_id is available
            if not context.tenant_id:
                logger.debug(f"Skipping audit log for {skill_name}: no tenant_id")
                return

            audit_service = SkillAuditService(db)
            await audit_service.log_execution(
                skill_name=skill_name,
                tenant_id=context.tenant_id,
                status=status,
                inputs=inputs,
                outputs=outputs,
                user_id=context.user_id if context.user_id else None,
                session_id=context.session_id,
                workflow_id=context.workflow_id,
                workflow_run_id=context.workflow_run_id,
                node_id=context.node_id,
                duration_ms=duration_ms,
                error_message=error_message,
                error_type=error_type,
            )
        except Exception as e:
            # Don't fail skill execution if audit logging fails
            logger.warning(f"Failed to log audit for skill {skill_name}: {e}")

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()
        skill_name = None
        processed_inputs = None

        try:
            node_data = context.node_data

            # Get skill configuration
            skill_name = node_data.get("skillName")
            if not skill_name:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error="Skill name not specified",
                    execution_time=time.time() - start_time
                )

            # Get skill from registry
            skill = SkillRegistry.get_skill(skill_name)
            if not skill:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Skill '{skill_name}' not found in registry",
                    execution_time=time.time() - start_time
                )

            # Check if skill is enabled
            if not skill.enabled:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Skill '{skill_name}' is disabled",
                    execution_time=time.time() - start_time
                )

            # Prepare skill inputs with variable substitution
            skill_inputs = node_data.get("inputs", {})

            # Substitute variables in inputs
            processed_inputs: Dict[str, Any] = {}
            for key, value in skill_inputs.items():
                if isinstance(value, str):
                    processed_inputs[key] = self.substitute_variables(
                        value,
                        context.variables,
                        context.inputs
                    )
                else:
                    processed_inputs[key] = value

            # Build SkillContext with API keys and connectors
            api_keys = await self._load_api_keys(db, context.user_id)
            connectors = await self._load_connectors(
                db, context.user_id, context.tenant_id
            )

            skill_ctx = SkillContext.from_workflow_context(
                context=context,
                db=db,
                api_keys=api_keys,
                connectors=connectors,
            )

            # Execute the skill with context as first argument
            try:
                if inspect.iscoroutinefunction(skill.handler):
                    result = await skill.handler(skill_ctx, **processed_inputs)
                else:
                    result = skill.handler(skill_ctx, **processed_inputs)
            except TypeError as e:
                # If handler doesn't accept ctx, try without it (backwards compatibility)
                error_msg = str(e)
                if "skill_ctx" in error_msg or "positional argument" in error_msg:
                    try:
                        if inspect.iscoroutinefunction(skill.handler):
                            result = await skill.handler(**processed_inputs)
                        else:
                            result = skill.handler(**processed_inputs)
                    except Exception as fallback_error:
                        return NodeExecutionResult(
                            node_id=context.node_id,
                            status="error",
                            error=f"Skill execution failed: {str(fallback_error)}",
                            execution_time=time.time() - start_time
                        )
                else:
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="error",
                        error=f"Skill execution failed: {str(e)}",
                        execution_time=time.time() - start_time
                    )

            execution_time = time.time() - start_time
            duration_ms = int(execution_time * 1000)

            # Prepare output
            output = SkillOutput(
                result=result,
                skill_name=skill_name,
                category=skill.category,
                execution_time=execution_time
            )

            # Log successful execution
            await self._log_audit(
                db,
                skill_name=skill_name,
                context=context,
                inputs=processed_inputs,
                outputs=output.model_dump(),
                status="success",
                duration_ms=duration_ms,
            )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output.model_dump(),
                execution_time=execution_time
            )

        except Exception as e:
            execution_time = time.time() - start_time
            duration_ms = int(execution_time * 1000)
            error_message = f"Unexpected error in skill execution: {str(e)}"

            # Log failed execution
            if skill_name:
                await self._log_audit(
                    db,
                    skill_name=skill_name,
                    context=context,
                    inputs=processed_inputs,
                    outputs=None,
                    status="failed",
                    duration_ms=duration_ms,
                    error_message=error_message,
                    error_type=type(e).__name__,
                )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="error",
                error=error_message,
                execution_time=execution_time
            )
