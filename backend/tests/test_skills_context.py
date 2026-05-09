"""
Tests for SkillContext and SkillExecutor

Phase 1 Skills: Verify that skills receive proper context in both
agent (via RunContext) and workflow (via NodeExecutionContext) execution.
"""

import pytest
from dataclasses import dataclass
from typing import Dict, Any

from app.agents.skills.context import SkillContext
from app.node_executors import NodeExecutionContext
from app.node_executors.skill_executor import SkillExecutor


class TestSkillContext:
    """Tests for SkillContext class"""

    def test_create_minimal(self):
        """Test creating minimal SkillContext"""
        ctx = SkillContext.create_minimal(
            user_id="user-123",
            tenant_id="tenant-456"
        )

        assert str(ctx.user_id) == "user-123"
        assert str(ctx.tenant_id) == "tenant-456"
        assert ctx.api_keys == {}
        assert ctx.connectors == {}

    def test_create_with_api_keys(self):
        """Test creating SkillContext with API keys"""
        ctx = SkillContext.create_minimal(
            user_id="user-123",
            api_keys={"tavily": "key-123", "openai": "key-456"}
        )

        assert ctx.get_api_key("tavily") == "key-123"
        assert ctx.get_api_key("openai") == "key-456"
        assert ctx.get_api_key("nonexistent") is None

    def test_create_with_connectors(self):
        """Test SkillContext with connectors"""
        ctx = SkillContext(
            user_id="user-123",
            connectors={
                "ofac": {"id": "conn-1", "config": {"api_url": "https://api.ofac.gov"}}
            }
        )

        assert ctx.has_connector("ofac")
        assert not ctx.has_connector("nonexistent")
        assert ctx.get_connector("ofac")["config"]["api_url"] == "https://api.ofac.gov"

    def test_skill_call_stack(self):
        """Test skill call stack operations"""
        ctx = SkillContext.create_minimal(user_id="user-123")

        assert ctx.stack_depth == 0
        assert not ctx.is_in_stack("skill-1")

        ctx.push_skill("skill-1")
        assert ctx.stack_depth == 1
        assert ctx.is_in_stack("skill-1")

        ctx.push_skill("skill-2")
        assert ctx.stack_depth == 2
        assert ctx.is_in_stack("skill-1")
        assert ctx.is_in_stack("skill-2")

        popped = ctx.pop_skill()
        assert popped == "skill-2"
        assert ctx.stack_depth == 1
        assert not ctx.is_in_stack("skill-2")

    def test_from_workflow_context(self):
        """Test creating SkillContext from NodeExecutionContext"""
        node_ctx = NodeExecutionContext(
            node_id="node-123",
            node_type="skill",
            node_data={"skillName": "web_search"},
            inputs={},
            variables={},
            user_id="user-456",
            tenant_id="tenant-789",
            session_id="session-abc",
            workflow_id="workflow-def",
        )

        api_keys = {"tavily": "key-123"}
        connectors = {"ofac": {"id": "conn-1"}}

        ctx = SkillContext.from_workflow_context(
            context=node_ctx,
            db=None,
            api_keys=api_keys,
            connectors=connectors,
        )

        assert ctx.user_id == "user-456"
        assert ctx.tenant_id == "tenant-789"
        assert ctx.session_id == "session-abc"
        assert ctx.workflow_id == "workflow-def"
        assert ctx.get_api_key("tavily") == "key-123"
        assert ctx.has_connector("ofac")
        assert ctx.metadata["node_id"] == "node-123"

    def test_from_run_context(self):
        """Test creating SkillContext from RunContext (Pydantic AI)"""
        # Mock RunContext with deps attribute
        @dataclass
        class MockDeps:
            user_id: str = "user-123"
            tenant_id: str = "tenant-456"
            session_id: str = None
            db: Any = None
            api_keys: Dict[str, str] = None
            connectors: Dict[str, Any] = None
            skill_call_stack: list = None
            mounted_volumes: list = None

            def __post_init__(self):
                self.api_keys = self.api_keys or {"openai": "key-openai"}
                self.connectors = self.connectors or {}
                self.skill_call_stack = self.skill_call_stack or []
                self.mounted_volumes = self.mounted_volumes or []

        @dataclass
        class MockRunContext:
            deps: MockDeps = None

            def __post_init__(self):
                self.deps = self.deps or MockDeps()

        mock_ctx = MockRunContext()
        ctx = SkillContext.from_run_context(mock_ctx)

        assert ctx.user_id == "user-123"
        assert ctx.tenant_id == "tenant-456"
        assert ctx.get_api_key("openai") == "key-openai"


class TestSkillExecutor:
    """Tests for SkillExecutor"""

    @pytest.fixture
    def executor(self):
        """Create SkillExecutor instance"""
        return SkillExecutor()

    @pytest.fixture
    def node_context(self):
        """Create basic NodeExecutionContext"""
        return NodeExecutionContext(
            node_id="node-123",
            node_type="skill",
            node_data={
                "skillName": "web_search",
                "inputs": {"query": "test query"}
            },
            inputs={},
            variables={},
            user_id="user-456",
            tenant_id="tenant-789",
        )

    @pytest.mark.asyncio
    async def test_execute_missing_skill_name(self, executor, test_db):
        """Test execution fails when skill name is missing"""
        context = NodeExecutionContext(
            node_id="node-123",
            node_type="skill",
            node_data={},  # No skillName
            inputs={},
            variables={},
            user_id="user-456",
        )

        result = await executor.execute(context, test_db)

        assert result.status == "error"
        assert "Skill name not specified" in result.error

    @pytest.mark.asyncio
    async def test_execute_skill_not_found(self, executor, node_context, test_db):
        """Test execution fails when skill doesn't exist"""
        node_context.node_data["skillName"] = "nonexistent_skill"

        result = await executor.execute(node_context, test_db)

        assert result.status == "error"
        assert "not found in registry" in result.error

    @pytest.mark.asyncio
    async def test_execute_skill_receives_context(self, executor, node_context, test_db):
        """Test that skill handler receives SkillContext"""
        # Create a mock skill that captures its context
        captured_ctx = None

        async def mock_handler(ctx, **kwargs):
            nonlocal captured_ctx
            captured_ctx = ctx
            return {"result": "success"}

        # Register mock skill
        from app.agents.skills.registry import _registry, Skill
        _registry["test_skill"] = Skill(
            name="test_skill",
            description="Test skill",
            category="test",
            handler=mock_handler,
        )

        try:
            node_context.node_data["skillName"] = "test_skill"
            node_context.node_data["inputs"] = {}

            result = await executor.execute(node_context, test_db)

            assert result.status == "success"
            # The wrapper should have converted to SkillContext
            assert captured_ctx is not None
            # Check it's a SkillContext or has the expected attributes
            assert hasattr(captured_ctx, 'user_id') or hasattr(captured_ctx, 'api_keys')
        finally:
            # Clean up
            del _registry["test_skill"]

    @pytest.mark.asyncio
    async def test_execute_with_variable_substitution(self, executor, test_db):
        """Test that variables are substituted in skill inputs"""
        captured_inputs = None

        async def mock_handler(ctx, query=None, **kwargs):
            nonlocal captured_inputs
            captured_inputs = {"query": query}
            return {"result": query}

        from app.agents.skills.registry import _registry, Skill
        _registry["test_skill"] = Skill(
            name="test_skill",
            description="Test skill",
            category="test",
            handler=mock_handler,
        )

        try:
            context = NodeExecutionContext(
                node_id="node-123",
                node_type="skill",
                node_data={
                    "skillName": "test_skill",
                    "inputs": {"query": "{{user_query}}"}
                },
                inputs={},
                variables={"user_query": "substituted value"},
                user_id="user-456",
            )

            result = await executor.execute(context, test_db)

            assert result.status == "success"
            assert captured_inputs["query"] == "substituted value"
        finally:
            del _registry["test_skill"]


class TestSkillDecoratorWrapper:
    """Test that the @skill decorator properly wraps handlers"""

    @pytest.mark.asyncio
    async def test_decorator_converts_run_context(self):
        """Test that decorator converts RunContext to SkillContext"""
        from app.agents.skills.registry import skill, _registry

        captured_ctx_type = None

        @skill(name="test_decorated_skill", description="Test", category="test")
        async def test_decorated_skill(ctx: SkillContext, value: str) -> Dict[str, Any]:
            nonlocal captured_ctx_type
            captured_ctx_type = type(ctx).__name__
            return {"value": value, "user_id": str(ctx.user_id)}

        try:
            # Simulate calling from Pydantic AI with RunContext-like object
            @dataclass
            class MockDeps:
                user_id: str = "user-from-agent"
                tenant_id: str = None
                session_id: str = None
                db: Any = None
                api_keys: Dict[str, str] = None
                connectors: Dict[str, Any] = None
                skill_call_stack: list = None
                mounted_volumes: list = None

                def __post_init__(self):
                    self.api_keys = self.api_keys or {}
                    self.connectors = self.connectors or {}
                    self.skill_call_stack = self.skill_call_stack or []
                    self.mounted_volumes = self.mounted_volumes or []

            @dataclass
            class MockRunContext:
                deps: MockDeps = None

                def __post_init__(self):
                    self.deps = self.deps or MockDeps()

            mock_run_ctx = MockRunContext()

            # Get the registered handler (the wrapper)
            handler = _registry["test_decorated_skill"].handler
            result = await handler(mock_run_ctx, value="test-value")

            assert captured_ctx_type == "SkillContext"
            assert result["user_id"] == "user-from-agent"
            assert result["value"] == "test-value"
        finally:
            if "test_decorated_skill" in _registry:
                del _registry["test_decorated_skill"]

    @pytest.mark.asyncio
    async def test_decorator_passes_skill_context_through(self):
        """Test that decorator passes SkillContext through unchanged"""
        from app.agents.skills.registry import skill, _registry

        captured_ctx = None

        @skill(name="test_passthrough_skill", description="Test", category="test")
        async def test_passthrough_skill(ctx: SkillContext, value: str) -> Dict[str, Any]:
            nonlocal captured_ctx
            captured_ctx = ctx
            return {"value": value}

        try:
            # Call directly with SkillContext
            skill_ctx = SkillContext.create_minimal(
                user_id="direct-user",
                api_keys={"test": "key"}
            )

            handler = _registry["test_passthrough_skill"].handler
            result = await handler(skill_ctx, value="test-value")

            assert captured_ctx.user_id == "direct-user"
            assert captured_ctx.get_api_key("test") == "key"
        finally:
            if "test_passthrough_skill" in _registry:
                del _registry["test_passthrough_skill"]
