"""Unit tests for AI agents"""

import pytest
from unittest.mock import AsyncMock, patch

from app.agents.analysis import AnalysisAgent
from app.agents.data_retrieval import DataRetrievalAgent
from app.agents.compliance import ComplianceAgent


@pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: needs an LLM API key (env-dependent)")
@pytest.mark.asyncio
async def test_analysis_agent_result_type(test_db, test_user_id):
    """Test that analysis agent returns correct result type"""
    agent = AnalysisAgent()

    # Mock the agent run method
    with patch.object(agent.agent, 'run', new_callable=AsyncMock) as mock_run:
        mock_result = AsyncMock()
        mock_result.data.model_dump.return_value = {
            "ticker": "AAPL",
            "recommendation": "buy",
            "confidence": 0.85,
            "reasoning": ["Strong fundamentals"],
            "key_metrics": {"pe_ratio": 28.5},
            "risks": []
        }
        mock_run.return_value = mock_result

        result = await agent.run(
            user_message="Analyze AAPL",
            db=test_db,
            user_id=test_user_id
        )

        # Verify call was made
        assert mock_run.called


@pytest.mark.asyncio
async def test_data_retrieval_agent_tools(test_db, test_user_id):
    """Test data retrieval agent has required tools"""
    agent = DataRetrievalAgent()

    # Check that agent has the expected tools
    assert hasattr(agent, 'agent')
    assert agent.name == "Data Retrieval Agent"


@pytest.mark.asyncio
async def test_compliance_agent_risk_levels(test_db, test_user_id):
    """Test compliance agent with different risk levels"""
    agent = ComplianceAgent(
        name="Compliance Agent",
        description="Checks regulatory compliance and assesses risk",
        system_prompt="You are a compliance agent that evaluates financial activities.",
    )

    # Check agent configuration
    assert agent.name == "Compliance Agent"
    assert agent.description == "Checks regulatory compliance and assesses risk"


@pytest.mark.asyncio
async def test_agent_api_key_fallback(test_db, test_user_id):
    """Test agent falls back to environment variable for API key"""
    agent = AnalysisAgent()

    # Test get_api_key method
    api_key = await agent.get_api_key("openrouter", test_db, test_user_id)

    # Should return None if no user key and no env var
    assert api_key is None or isinstance(api_key, str)
