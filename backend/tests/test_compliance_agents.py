"""
Tests for specialized compliance agents.
"""

import pytest
from unittest.mock import AsyncMock, patch
from app.agents.compliance import kyc_agent, market_abuse_agent, report_generator_agent, ComplianceResult

@pytest.mark.asyncio
async def test_kyc_agent_definition():
    """Verify KYC agent is defined with correct prompt"""
    assert kyc_agent.name == "KYC/AML Agent"
    assert "KYC" in kyc_agent.system_prompt
    # result_type was removed from specialized agents

@pytest.mark.asyncio
async def test_market_abuse_agent_definition():
    """Verify Market Abuse agent is defined with correct prompt"""
    assert market_abuse_agent.name == "Market Abuse Agent"
    assert "insider trading" in market_abuse_agent.system_prompt

@pytest.mark.asyncio
async def test_report_generator_agent_definition():
    """Verify Report Generator agent is defined with correct prompt"""
    assert report_generator_agent.name == "Regulatory Report Generator"
    assert "technical writer" in report_generator_agent.system_prompt

@pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: needs an LLM API key (env-dependent)")
@pytest.mark.asyncio
async def test_kyc_agent_execution_mock(test_db, test_user_id):
    """Verify KYC agent can be executed (mocked LLM)"""
    with patch.object(kyc_agent.agent, 'run', new_callable=AsyncMock) as mock_run:
        mock_run.return_value.data = ComplianceResult(
            is_compliant=False,
            violations=["Missing passport scan"],
            risk_level="high",
            recommendations=["Request identity verification document"]
        )
        
        result = await kyc_agent.run("Client ID: 123, Name: John Doe", db=test_db, user_id=test_user_id)
        
        assert result.data.is_compliant is False
        assert len(result.data.violations) == 1
        assert result.data.risk_level == "high"
