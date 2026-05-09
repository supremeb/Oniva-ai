
import sys
import os
import unittest
from unittest.mock import MagicMock

# Ensure backend path is in pythonpath
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from app.agents.skills.compliance import (
    kyc_verification,
    aml_screening,
    sanctions_check,
    regulatory_filing,
    risk_assessment
)
from pydantic_ai import RunContext
from app.agents.base import AgentDependencies

class TestComplianceSkills(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = MagicMock(spec=RunContext)
        self.ctx.deps = MagicMock(spec=AgentDependencies)

    async def test_kyc_verification(self):
        # Test low risk
        result = await kyc_verification(self.ctx, full_name="John Doe", dob="1990-01-01", country="USA")
        self.assertEqual(result["risk_level"], "low")
        self.assertEqual(result["status"], "verified")
        
        # Test high risk
        result = await kyc_verification(self.ctx, full_name="Kim", dob="1980-01-01", country="North Korea")
        self.assertEqual(result["risk_level"], "high")
        self.assertEqual(result["status"], "flagged")

    async def test_aml_screening(self):
        # Safe transaction
        result = await aml_screening(self.ctx, amount=5000, sender_country="USA", receiver_country="USA")
        self.assertEqual(result["risk_score"], 0)
        self.assertEqual(result["action"], "approve")
        
        # Risky transaction
        result = await aml_screening(self.ctx, amount=15000, sender_country="USA", receiver_country="China")
        self.assertGreater(result["risk_score"], 0)
        
    async def test_sanctions_check(self):
        # Clean
        result = await sanctions_check(self.ctx, entity_name="Good Corp")
        self.assertFalse(result["is_sanctioned"])
        
        # Sanctioned
        result = await sanctions_check(self.ctx, entity_name="Evil Corp International")
        self.assertTrue(result["is_sanctioned"])
        
    async def test_regulatory_filing(self):
        result = await regulatory_filing(self.ctx, cik="0001067983")
        self.assertEqual(result["cik"], "0001067983")
        self.assertIn("url", result)
        
    async def test_risk_assessment(self):
        result = await risk_assessment(self.ctx, entity_name="Some Bank", entity_type="Bank")
        self.assertIn("composite_risk_score", result)
        self.assertIn("rating", result)

if __name__ == "__main__":
    unittest.main()
