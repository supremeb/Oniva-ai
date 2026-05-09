
import sys
import os
import unittest
from unittest.mock import MagicMock

# Ensure backend path is in pythonpath
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from app.agents.skills.registry import SkillRegistry, skill
from app.agents.base import AgentDependencies
from pydantic_ai import RunContext

# Define dummy skills for testing
@skill("skill_a", "Calls skill B", requires=["skill_b"])
async def skill_a(ctx, val: int):
    return await SkillRegistry.invoke_skill(ctx, "skill_b", val=val)

@skill("skill_b", "Returns val * 2")
async def skill_b(ctx, val: int):
    return val * 2

@skill("skill_c", "Calls skill d", requires=["skill_d"])
async def skill_c(ctx, val: int):
    return await SkillRegistry.invoke_skill(ctx, "skill_d", val=val)

@skill("skill_d", "Calls skill c (Cycle)", requires=["skill_c"])
async def skill_d(ctx, val: int):
    return await SkillRegistry.invoke_skill(ctx, "skill_c", val=val)

class TestSkillComposition(unittest.IsolatedAsyncioTestCase):
    async def test_composition(self):
        # Setup context — don't use spec=AgentDependencies because Pydantic
        # BaseModel fields are instance attrs, invisible to MagicMock spec.
        deps = MagicMock()
        deps.user_id = "test-user"
        deps.tenant_id = "test-tenant"
        deps.session_id = None
        deps.db = None
        deps.api_keys = {}
        deps.connectors = {}
        deps.skill_call_stack = []
        deps.mounted_volumes = []
        ctx = MagicMock(spec=RunContext)
        ctx.deps = deps
        
        # Enable skills
        registry = SkillRegistry()
        # Force load dummy skills into registry (they are decorated above, so they are in _registry)
        # But we need them to be findable. Since they are in this file, they are already registered in _registry
        # because the decorator ran on import.
        
        # Test A -> B
        result = await skill_a(ctx, val=10)
        self.assertEqual(result, 20)
        print("✅ Composition Test Passed (A->B)")
        
    async def test_cycle_detection(self):
        # Setup context
        deps = MagicMock()
        deps.user_id = "test-user"
        deps.tenant_id = "test-tenant"
        deps.session_id = None
        deps.db = None
        deps.api_keys = {}
        deps.connectors = {}
        deps.skill_call_stack = []
        deps.mounted_volumes = []
        ctx = MagicMock(spec=RunContext)
        ctx.deps = deps
        
        try:
            await skill_c(ctx, val=5)
            self.fail("Should have raised ValueError for cycle")
        except ValueError as e:
            print(f"✅ Cycle Detection Passed: {e}")
            self.assertIn("Circular dependency detected", str(e))

if __name__ == "__main__":
    unittest.main()
