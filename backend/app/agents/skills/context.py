"""
Skill Context
Unified context for skills in both agent and workflow execution.

This replaces the previous pattern where skills expected RunContext[AgentDependencies]
and provides a consistent interface regardless of how the skill is invoked.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from uuid import UUID


@dataclass
class SkillContext:
    """
    Unified context for skills in both agent and workflow execution.

    Skills receive this context as their first parameter, providing access to:
    - User and tenant identification
    - API keys for external services
    - Connector configurations
    - Database session for persistence
    - Session/workflow tracking for audit trails

    Example:
        async def web_search(ctx: SkillContext, query: str) -> Dict:
            api_key = ctx.api_keys.get("tavily")
            if not api_key:
                return {"error": "Tavily API key not configured"}
            # ... use api_key
    """

    # Required fields
    user_id: Union[str, UUID]

    # Optional identification
    tenant_id: Optional[Union[str, UUID]] = None
    session_id: Optional[str] = None
    workflow_id: Optional[Union[str, UUID]] = None
    workflow_run_id: Optional[Union[str, UUID]] = None

    # Resources
    db: Optional[Any] = None  # AsyncSession - can't use type hint due to dataclass limitation
    api_keys: Dict[str, str] = field(default_factory=dict)  # provider -> decrypted API key
    connectors: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # connector_id -> config

    # Execution tracking
    skill_call_stack: List[str] = field(default_factory=list)  # For cycle detection
    mounted_volumes: List[str] = field(default_factory=list)  # Volume names mounted to session

    # Search scoping
    specific_sites: Optional[List[str]] = None  # Domain restrictions for web search

    # Additional context
    metadata: Dict[str, Any] = field(default_factory=dict)  # Arbitrary metadata

    @classmethod
    def from_run_context(cls, ctx: Any) -> "SkillContext":
        """
        Create SkillContext from Pydantic AI RunContext (agent execution).

        Args:
            ctx: RunContext[AgentDependencies] from Pydantic AI

        Returns:
            SkillContext with fields populated from RunContext.deps
        """
        deps = ctx.deps

        return cls(
            user_id=deps.user_id,
            tenant_id=deps.tenant_id,
            session_id=deps.session_id,
            db=deps.db,
            api_keys=deps.api_keys or {},
            connectors=deps.connectors or {},
            skill_call_stack=deps.skill_call_stack or [],
            mounted_volumes=deps.mounted_volumes or [],
            specific_sites=getattr(deps, 'specific_sites', None),
        )

    @classmethod
    def from_workflow_context(
        cls,
        context: Any,  # NodeExecutionContext
        db: Any,
        api_keys: Dict[str, str],
        connectors: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> "SkillContext":
        """
        Create SkillContext from NodeExecutionContext (workflow execution).

        Args:
            context: NodeExecutionContext with node_id, user_id, etc.
            db: AsyncSession for database operations
            api_keys: Dict of provider -> decrypted API key
            connectors: Optional dict of connector configurations

        Returns:
            SkillContext with fields populated from workflow context
        """
        return cls(
            user_id=context.user_id,
            tenant_id=getattr(context, 'tenant_id', None),
            session_id=getattr(context, 'session_id', None),
            workflow_id=getattr(context, 'workflow_id', None),
            workflow_run_id=getattr(context, 'workflow_run_id', None),
            db=db,
            api_keys=api_keys,
            connectors=connectors or {},
            metadata={
                "node_id": context.node_id,
                "node_type": context.node_type,
            }
        )

    @classmethod
    def create_minimal(
        cls,
        user_id: Union[str, UUID],
        tenant_id: Optional[Union[str, UUID]] = None,
        api_keys: Optional[Dict[str, str]] = None,
    ) -> "SkillContext":
        """
        Create minimal SkillContext for testing or simple use cases.

        Args:
            user_id: User identifier
            tenant_id: Optional tenant identifier
            api_keys: Optional API keys dict

        Returns:
            SkillContext with minimal required fields
        """
        return cls(
            user_id=user_id,
            tenant_id=tenant_id,
            api_keys=api_keys or {},
        )

    def get_api_key(self, provider: str) -> Optional[str]:
        """
        Get API key for a provider.

        Args:
            provider: Provider name (e.g., "tavily", "openai", "anthropic")

        Returns:
            Decrypted API key or None if not configured
        """
        return self.api_keys.get(provider)

    def get_connector(self, connector_id: str) -> Optional[Dict[str, Any]]:
        """
        Get connector configuration.

        Args:
            connector_id: Connector identifier

        Returns:
            Connector configuration dict or None if not configured
        """
        return self.connectors.get(connector_id)

    def has_connector(self, connector_id: str) -> bool:
        """
        Check if a connector is available.

        Args:
            connector_id: Connector identifier

        Returns:
            True if connector is configured
        """
        return connector_id in self.connectors

    def push_skill(self, skill_name: str) -> None:
        """
        Push skill onto call stack for cycle detection.

        Args:
            skill_name: Name of skill being called
        """
        self.skill_call_stack.append(skill_name)

    def pop_skill(self) -> Optional[str]:
        """
        Pop skill from call stack.

        Returns:
            Skill name that was popped, or None if stack empty
        """
        if self.skill_call_stack:
            return self.skill_call_stack.pop()
        return None

    def is_in_stack(self, skill_name: str) -> bool:
        """
        Check if skill is already in call stack (cycle detection).

        Args:
            skill_name: Skill name to check

        Returns:
            True if skill is in stack (would cause cycle)
        """
        return skill_name in self.skill_call_stack

    @property
    def stack_depth(self) -> int:
        """Current depth of skill call stack."""
        return len(self.skill_call_stack)
