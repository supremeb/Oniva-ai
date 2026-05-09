from pydantic import BaseModel
from pydantic_ai import RunContext
from typing import List, Literal, Optional

from app.agents.base import BaseAgent, AgentDependencies
from app.agents.tool_results import ComplianceReportResult, InsiderTradingRiskResult


class ComplianceResult(BaseModel):
    """Result from compliance check agent"""
    is_compliant: bool
    violations: List[str] = []
    risk_level: Literal["low", "medium", "high", "critical"]
    recommendations: List[str] = []
    regulations_checked: List[str] = []
    artifact_uri: Optional[str] = None  # URI to saved compliance report


class ComplianceAgent(BaseAgent):
    """
    Agent for checking regulatory compliance and risk assessment.
    Evaluates financial activities against regulations.

    Storage Pattern:
    - Saves compliance reports as artifacts (7-year retention for SEC)
    - Logs all compliance checks to session history for audit trails
    - Can reference regulatory documents from mounted volumes
    """

    def __init__(self, name: str, description: str, system_prompt: str):
        super().__init__(
            name=name,
            description=description,
            output_type=ComplianceResult,
            system_prompt=system_prompt,
            model="anthropic/claude-3.5-sonnet",  # Best reasoning for compliance
            temperature=0.0,  # Deterministic for compliance
        )

        @self.agent.tool
        async def save_compliance_report(
            ctx: RunContext[AgentDependencies],
            report_content: str,
            report_type: str,
            subject_identifier: str
        ) -> ComplianceReportResult:
            """
            Save a compliance report as an artifact for audit trail.

            Args:
                report_content: Full text content of the compliance report
                report_type: Type of report (kyc, aml, insider_trading, etc.)
                subject_identifier: Entity being evaluated (user_id, trade_id, etc.)

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
                file_path = f"compliance/{report_type}_{subject_identifier}_{timestamp}.md"

                uri = await artifact_storage.write(
                    tenant_id=str(ctx.deps.tenant_id),
                    session_id=session_id,
                    file_path=file_path,
                    content=report_content.encode('utf-8'),
                    metadata={
                        "user_id": str(ctx.deps.user_id),
                        "agent_id": "compliance_agent",
                        "report_type": report_type,
                        "subject": subject_identifier,
                    },
                    content_type="text/markdown",
                )

                return {
                    "success": True,
                    "artifact_uri": uri,
                    "file_path": file_path,
                    "retention_years": 7,
                }
            except Exception as e:
                return {"error": str(e), "success": False}

        @self.agent.tool
        async def check_insider_trading_risk(
            ctx: RunContext[AgentDependencies],
            employee_role: str,
            stock_ticker: str,
            trade_date: str,
            company_events: List[str]
        ) -> InsiderTradingRiskResult:
            """
            Assess insider trading risk

            Args:
                employee_role: Employee's position in the company
                stock_ticker: Stock being traded
                trade_date: Date of trade
                company_events: Recent company events

            Returns:
                Risk assessment
            """
            # Simple rule-based check (real implementation would be more sophisticated)
            high_risk_roles = ["ceo", "cfo", "cto", "director", "vp"]
            risk_events = ["earnings", "merger", "acquisition", "lawsuit"]

            is_high_risk_role = any(role in employee_role.lower() for role in high_risk_roles)
            has_material_event = any(event in " ".join(company_events).lower() for event in risk_events)

            risk_level = "low"
            if is_high_risk_role and has_material_event:
                risk_level = "high"
            elif is_high_risk_role or has_material_event:
                risk_level = "medium"

            return {
                "risk_level": risk_level,
                "high_risk_role": is_high_risk_role,
                "material_events_present": has_material_event,
                "recommendation": "Review trade with compliance team" if risk_level != "low" else "Standard review"
            }


# 1. KYC/AML Agent
kyc_agent = ComplianceAgent(
    name="KYC/AML Agent",
    description="Validates Know Your Customer and Anti-Money Laundering requirements",
    system_prompt="""You are a KYC (Know Your Customer) and AML (Anti-Money Laundering) specialist.
Analyze customer data and identity documentation to identify:
1. Missing or expired identification.
2. Politically Exposed Persons (PEPs) or sanctioned entities.
3. Suspicious patterns of financial activity or high-risk jurisdictions.
4. Gaps in beneficial ownership information.

Output a structured compliance report with risk level and specific violations."""
)

# 2. Market Abuse/Insider Trading Agent
market_abuse_agent = ComplianceAgent(
    name="Market Abuse Agent",
    description="Detects potential insider trading, front-running, or market manipulation",
    system_prompt="""You are an expert in market abuse and trade surveillance.
Analyze trading activity, communication logs, and company material events to identify:
1. Potential insider trading (trading before material non-public information is disclosed).
2. Unusual trading volume or price movements correlated with internal communications.
3. Front-running or layering/spoofing patterns.
4. Conflicts of interest in employee personal trading.

Use the check_insider_trading_risk tool when analyzing trade-specific risks."""
)

# 3. Regulatory Report Generator Agent
report_generator_agent = ComplianceAgent(
    name="Regulatory Report Generator",
    description="Synthesizes analysis into formal regulatory reports",
    system_prompt="""You are a technical writer specializing in financial regulatory reporting.
Your goal is to take various analysis results (KYC, Market Abuse, or Research) and synthesize them into a formal report structure.
Ensure the report:
1. Uses professional, precise terminology.
2. Clearly identifies the specific regulations involved (e.g., Section 16 of the Exchange Act).
3. Provides a concise executive summary of the findings.
4. Categorizes violations by severity.
5. Lists required next steps for the compliance team."""
)

# Default instance for backward compatibility
compliance_agent = kyc_agent
