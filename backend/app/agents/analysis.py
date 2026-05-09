from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from typing import List, Dict, Optional, Literal


from app.agents.base import BaseAgent, AgentDependencies
from app.agents.tool_results import AnalysisReportResult


class AnalysisResult(BaseModel):
    """Result from financial analysis agent"""
    ticker: Optional[str] = None
    recommendation: Literal["buy", "sell", "hold", "research"]
    confidence: float = Field(ge=0, le=1, description="Confidence score 0-1")
    reasoning: List[str] = []
    key_metrics: Dict[str, float] = {}
    risks: List[str] = []
    artifact_uri: Optional[str] = None  # URI to saved analysis report


class AnalysisAgent(BaseAgent):
    """
    Agent for performing financial analysis.
    Analyzes stocks, portfolios, and market data.

    Storage Pattern:
    - Mounts market_data volume for historical prices
    - Saves analysis reports as artifacts (7-year retention)
    - Logs analysis actions to session history
    """

    def __init__(self):
        super().__init__(
            name="Analysis Agent",
            description="Performs financial analysis on stocks and portfolios",
            output_type=AnalysisResult,
            system_prompt="""You are an expert financial analyst specializing in fundamental and technical analysis.

Your analysis should be:
1. Data-driven and objective
2. Consider multiple factors (financial metrics, market conditions, sector trends)
3. Provide clear reasoning for recommendations
4. Identify and articulate risks

Use the available tools to calculate financial metrics and analyze data.
Always provide a confidence score based on the quality and completeness of available data.

When completing analysis, save the detailed report as an artifact for compliance records.""",
            model="openai/gpt-4o",  # Use smarter model for analysis
        )

        # Register tools
        @self.agent.tool
        async def save_analysis_report(
            ctx: RunContext[AgentDependencies],
            ticker: str,
            recommendation: str,
            report_content: str,
            key_metrics: Dict[str, float]
        ) -> AnalysisReportResult:
            """
            Save an analysis report as an artifact for compliance records.

            Args:
                ticker: Stock ticker analyzed
                recommendation: buy/sell/hold/research
                report_content: Full markdown analysis report
                key_metrics: Dictionary of calculated metrics

            Returns:
                Storage URI or error
            """
            from app.services.artifacts import get_artifact_storage
            from app.config import get_settings
            from datetime import datetime

            settings = get_settings()
            if not getattr(settings, 'USE_ARTIFACTS', False):
                return {"warning": "Artifacts feature not enabled, report not saved"}

            session_id = ctx.deps.session_id
            if not session_id:
                return {"error": "No session_id available to save artifact"}

            try:
                artifact_storage = get_artifact_storage()
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                file_path = f"analysis/{ticker}_{recommendation}_{timestamp}.md"

                # Format metrics section
                metrics_section = "\n## Key Metrics\n"
                for metric_name, value in key_metrics.items():
                    metrics_section += f"- **{metric_name}**: {value}\n"

                full_report = f"# Analysis Report: {ticker}\n\n**Recommendation**: {recommendation}\n\n{report_content}\n{metrics_section}"

                uri = await artifact_storage.write(
                    tenant_id=str(ctx.deps.tenant_id),
                    session_id=session_id,
                    file_path=file_path,
                    content=full_report.encode('utf-8'),
                    metadata={
                        "user_id": str(ctx.deps.user_id),
                        "agent_id": "analysis_agent",
                        "ticker": ticker,
                        "recommendation": recommendation,
                    },
                    content_type="text/markdown",
                )

                return {
                    "success": True,
                    "artifact_uri": uri,
                    "file_path": file_path,
                }
            except Exception as e:
                return {"error": str(e), "success": False}

        @self.agent.tool
        async def calculate_pe_ratio(
            ctx: RunContext[AgentDependencies],
            price: float,
            earnings_per_share: float
        ) -> float:
            """
            Calculate Price-to-Earnings ratio

            Args:
                price: Current stock price
                earnings_per_share: EPS

            Returns:
                P/E ratio
            """
            if earnings_per_share == 0:
                return 0
            return round(price / earnings_per_share, 2)

        @self.agent.tool
        async def calculate_debt_to_equity(
            ctx: RunContext[AgentDependencies],
            total_debt: float,
            total_equity: float
        ) -> float:
            """
            Calculate Debt-to-Equity ratio

            Args:
                total_debt: Total debt
                total_equity: Total shareholder equity

            Returns:
                D/E ratio
            """
            if total_equity == 0:
                return 0
            return round(total_debt / total_equity, 2)

        @self.agent.tool
        async def calculate_roe(
            ctx: RunContext[AgentDependencies],
            net_income: float,
            shareholder_equity: float
        ) -> float:
            """
            Calculate Return on Equity

            Args:
                net_income: Net income
                shareholder_equity: Shareholder equity

            Returns:
                ROE as percentage
            """
            if shareholder_equity == 0:
                return 0
            return round((net_income / shareholder_equity) * 100, 2)


# Create singleton instance
analysis_agent = AnalysisAgent()
