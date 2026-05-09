#!/usr/bin/env python
"""
Seed demo workflows and agents for the test tenant.

Creates 6 realistic financial services workflows and 3 demo agents.
Idempotent: deletes existing demo data before re-creating.

Set SEED_TENANT_ID and SEED_USER_ID env vars to target an existing tenant/user;
otherwise random UUIDs are generated each run.

Usage:
    cd backend && source venv/bin/activate && python scripts/seed_demo_data.py
"""
import asyncio
import os
import sys
import uuid

# Ensure backend package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.models.workflow import Workflow
from app.models.custom_agent import CustomAgent
from app.models.connector import Connector

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_ID = uuid.UUID(os.environ["SEED_TENANT_ID"]) if os.environ.get("SEED_TENANT_ID") else uuid.uuid4()
USER_ID = uuid.UUID(os.environ["SEED_USER_ID"]) if os.environ.get("SEED_USER_ID") else uuid.uuid4()

# Stable UUIDs so re-runs produce the same IDs (idempotent)
WORKFLOW_IDS = {
    "kyc_screening_pipeline": uuid.UUID("d0000001-0001-4000-a000-000000000001"),
    "sanctions_check_report": uuid.UUID("d0000001-0001-4000-a000-000000000002"),
    "company_due_diligence": uuid.UUID("d0000001-0001-4000-a000-000000000003"),
    "portfolio_risk_assessment": uuid.UUID("d0000001-0001-4000-a000-000000000004"),
    "client_onboarding_checklist": uuid.UUID("d0000001-0001-4000-a000-000000000005"),
    "incident_escalation": uuid.UUID("d0000001-0001-4000-a000-000000000006"),
}

AGENT_IDS = {
    "compliance_analyst": uuid.UUID("a0000001-0001-4000-b000-000000000001"),
    "research_analyst": uuid.UUID("a0000001-0001-4000-b000-000000000002"),
    "operations_assistant": uuid.UUID("a0000001-0001-4000-b000-000000000003"),
}

CONNECTOR_IDS = {
    "synthetic_bank": uuid.UUID("c0000001-0001-4000-c000-000000000001"),
}


# ---------------------------------------------------------------------------
# Workflow Specs
# ---------------------------------------------------------------------------

def _kyc_screening_pipeline() -> dict:
    """KYC Screening Pipeline: start -> http_request (x2) -> llm -> llm -> ifelse -> output (x2 branches)."""
    return {
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "position": {"x": 50, "y": 200},
                "data": {
                    "label": "Start",
                    "variables": [
                        {"name": "entity_name", "type": "string"},
                        {"name": "entity_type", "type": "string"},
                    ],
                },
            },
            {
                "id": "http_search_customer",
                "type": "http",
                "position": {"x": 300, "y": 200},
                "data": {
                    "label": "Search Customer",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/customers",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {"search": "{{entity_name}}"},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "customer_search_data",
                    },
                },
            },
            {
                "id": "http_fetch_kyc",
                "type": "http",
                "position": {"x": 550, "y": 200},
                "data": {
                    "label": "Fetch KYC Record",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/kyc/{{customer_search_data.results[0].id}}",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "kyc_data",
                    },
                },
            },
            {
                "id": "llm_extract",
                "type": "llm",
                "position": {"x": 800, "y": 200},
                "data": {
                    "label": "Extract Entity Info",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a KYC data extraction specialist. "
                        "Extract and normalize entity information for compliance screening."
                    ),
                    "userPrompt": (
                        "Extract and normalize the following entity for KYC screening:\n\n"
                        "Entity Name: {{entity_name}}\n"
                        "Entity Type: {{entity_type}}\n\n"
                        "Customer Data from Bank API:\n{{customer_search_data}}\n\n"
                        "KYC Record from Bank API:\n{{kyc_data}}\n\n"
                        "Return a structured summary with:\n"
                        "- Normalized name (remove titles, suffixes)\n"
                        "- Entity classification (individual / corporation / trust / government)\n"
                        "- Jurisdiction indicators (if any)\n"
                        "- Known aliases or alternate spellings"
                    ),
                    "temperature": 0.3,
                    "maxTokens": 1024,
                },
            },
            {
                "id": "llm_risk",
                "type": "llm",
                "position": {"x": 1050, "y": 200},
                "data": {
                    "label": "KYC Risk Scoring",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a KYC risk scoring engine. Evaluate entities against "
                        "compliance risk factors and assign a risk score between 0.0 and 1.0."
                    ),
                    "userPrompt": (
                        "Based on the following entity profile and live KYC data, "
                        "assign a risk score.\n\n"
                        "Entity Profile:\n{{llm_extract.text}}\n\n"
                        "KYC Record:\n{{kyc_data}}\n\n"
                        "Consider these risk factors:\n"
                        "- PEP (Politically Exposed Person) indicators\n"
                        "- High-risk jurisdiction associations\n"
                        "- Adverse media signals\n"
                        "- Complex ownership structures\n\n"
                        "Return ONLY a JSON object: {\"risk_score\": <float 0-1>, \"risk_level\": \"high|medium|low\", \"findings\": [\"...\"]}"
                    ),
                    "temperature": 0.2,
                    "maxTokens": 1024,
                },
            },
            {
                "id": "ifelse_risk",
                "type": "ifelse",
                "position": {"x": 1300, "y": 200},
                "data": {
                    "label": "Risk Level Check",
                    "conditions": [
                        {
                            "variable": "llm_risk.text",
                            "operator": "contains",
                            "value": "high",
                        }
                    ],
                    "logicalOperator": "and",
                },
            },
            {
                "id": "output_high",
                "type": "output",
                "position": {"x": 1550, "y": 100},
                "data": {
                    "label": "High Risk Result",
                    "outputName": "kyc_result",
                    "outputValue": (
                        "## KYC Screening Result: HIGH RISK\n\n"
                        "**Entity:** {{entity_name}} ({{entity_type}})\n\n"
                        "### Entity Profile\n{{llm_extract.text}}\n\n"
                        "### Risk Assessment\n{{llm_risk.text}}\n\n"
                        "**Recommendation:** ENHANCED DUE DILIGENCE REQUIRED. "
                        "Escalate to senior compliance officer for manual review."
                    ),
                },
            },
            {
                "id": "output_low",
                "type": "output",
                "position": {"x": 1550, "y": 350},
                "data": {
                    "label": "Low/Medium Risk Result",
                    "outputName": "kyc_result",
                    "outputValue": (
                        "## KYC Screening Result: PASS\n\n"
                        "**Entity:** {{entity_name}} ({{entity_type}})\n\n"
                        "### Entity Profile\n{{llm_extract.text}}\n\n"
                        "### Risk Assessment\n{{llm_risk.text}}\n\n"
                        "**Recommendation:** Standard onboarding procedures may proceed."
                    ),
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "http_search_customer"},
            {"id": "e2", "source": "http_search_customer", "target": "http_fetch_kyc"},
            {"id": "e3", "source": "http_fetch_kyc", "target": "llm_extract"},
            {"id": "e4", "source": "llm_extract", "target": "llm_risk"},
            {"id": "e5", "source": "llm_risk", "target": "ifelse_risk"},
            {"id": "e6", "source": "ifelse_risk", "target": "output_high", "sourceHandle": "true"},
            {"id": "e7", "source": "ifelse_risk", "target": "output_low", "sourceHandle": "false"},
        ],
    }


def _sanctions_check_report() -> dict:
    """Sanctions Check & Report: start -> http_request -> llm -> llm -> llm -> output."""
    return {
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "position": {"x": 50, "y": 200},
                "data": {
                    "label": "Start",
                    "variables": [
                        {"name": "entity_name", "type": "string"},
                        {"name": "country", "type": "string"},
                    ],
                },
            },
            {
                "id": "http_watchlist",
                "type": "http",
                "position": {"x": 300, "y": 200},
                "data": {
                    "label": "Watchlist Screen",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/watchlist/screen",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {
                            "entity_name": "{{entity_name}}",
                            "country": "{{country}}",
                        },
                        "body": "",
                        "timeout": 30,
                        "variable_name": "watchlist_results",
                    },
                },
            },
            {
                "id": "llm_ofac",
                "type": "llm",
                "position": {"x": 550, "y": 200},
                "data": {
                    "label": "OFAC/EU Sanctions Check",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a sanctions screening system. Analyze watchlist screening results "
                        "against OFAC SDN, EU consolidated sanctions, and UN Security Council lists."
                    ),
                    "userPrompt": (
                        "Analyze the following watchlist screening results for:\n\n"
                        "Entity: {{entity_name}}\n"
                        "Country: {{country}}\n\n"
                        "Watchlist Screening Data from Bank API:\n{{watchlist_results}}\n\n"
                        "For each list (OFAC SDN, EU Consolidated, UN Security Council):\n"
                        "- Match status: MATCH / POSSIBLE MATCH / NO MATCH\n"
                        "- If matched, list the program/designation\n"
                        "- Confidence score (0-100%)\n\n"
                        "Return results in a structured format."
                    ),
                    "temperature": 0.2,
                    "maxTokens": 1024,
                },
            },
            {
                "id": "llm_pep",
                "type": "llm",
                "position": {"x": 800, "y": 200},
                "data": {
                    "label": "PEP Screening",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a PEP (Politically Exposed Person) screening specialist. "
                        "Simulate PEP database lookup and adverse media screening."
                    ),
                    "userPrompt": (
                        "Screen the following entity for PEP status and adverse media:\n\n"
                        "Entity: {{entity_name}}\n"
                        "Country: {{country}}\n\n"
                        "Watchlist Data:\n{{watchlist_results}}\n\n"
                        "Sanctions findings so far:\n{{llm_ofac.text}}\n\n"
                        "Check for:\n"
                        "- PEP status (current or former)\n"
                        "- PEP relatives and close associates (RCA)\n"
                        "- Adverse media mentions\n"
                        "- Regulatory enforcement actions\n\n"
                        "Return structured PEP screening results."
                    ),
                    "temperature": 0.2,
                    "maxTokens": 1024,
                },
            },
            {
                "id": "llm_report",
                "type": "llm",
                "position": {"x": 1050, "y": 200},
                "data": {
                    "label": "Generate Compliance Report",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a compliance report generator. Compile sanctions and PEP "
                        "screening results into a formal compliance report."
                    ),
                    "userPrompt": (
                        "Generate a formal compliance screening report for:\n\n"
                        "Entity: {{entity_name}}\n"
                        "Country: {{country}}\n\n"
                        "### Sanctions Screening Results\n{{llm_ofac.text}}\n\n"
                        "### PEP Screening Results\n{{llm_pep.text}}\n\n"
                        "Format as a professional compliance report with:\n"
                        "1. Executive Summary\n"
                        "2. Screening Methodology\n"
                        "3. Findings Detail\n"
                        "4. Overall Risk Rating (HIGH / MEDIUM / LOW)\n"
                        "5. Recommended Actions\n"
                        "6. Analyst Notes"
                    ),
                    "temperature": 0.3,
                    "maxTokens": 2048,
                },
            },
            {
                "id": "output_1",
                "type": "output",
                "position": {"x": 1300, "y": 200},
                "data": {
                    "label": "Compliance Report",
                    "outputName": "sanctions_report",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "http_watchlist"},
            {"id": "e2", "source": "http_watchlist", "target": "llm_ofac"},
            {"id": "e3", "source": "llm_ofac", "target": "llm_pep"},
            {"id": "e4", "source": "llm_pep", "target": "llm_report"},
            {"id": "e5", "source": "llm_report", "target": "output_1"},
        ],
    }


def _company_due_diligence() -> dict:
    """Company Due Diligence: start -> http_request (x2) -> llm -> llm -> llm -> template -> output."""
    return {
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "position": {"x": 50, "y": 200},
                "data": {
                    "label": "Start",
                    "variables": [
                        {"name": "company_name", "type": "string"},
                        {"name": "analysis_focus", "type": "string"},
                    ],
                },
            },
            {
                "id": "http_search_company",
                "type": "http",
                "position": {"x": 300, "y": 200},
                "data": {
                    "label": "Search Company",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/customers",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {"search": "{{company_name}}"},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "company_data",
                    },
                },
            },
            {
                "id": "http_fetch_accounts",
                "type": "http",
                "position": {"x": 550, "y": 200},
                "data": {
                    "label": "Fetch Accounts",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/accounts",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {"customer_id": "{{company_data.results[0].id}}"},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "accounts_data",
                    },
                },
            },
            {
                "id": "llm_overview",
                "type": "llm",
                "position": {"x": 800, "y": 200},
                "data": {
                    "label": "Company Overview",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a corporate research analyst. Provide detailed company overviews "
                        "based on publicly available information and live bank data."
                    ),
                    "userPrompt": (
                        "Provide a comprehensive overview of {{company_name}}.\n"
                        "Analysis focus: {{analysis_focus}}\n\n"
                        "Customer Record from Bank API:\n{{company_data}}\n\n"
                        "Accounts Data from Bank API:\n{{accounts_data}}\n\n"
                        "Include:\n"
                        "- Industry and sector classification\n"
                        "- Company size (employees, revenue range)\n"
                        "- Key products/services\n"
                        "- Geographic presence\n"
                        "- Key leadership team\n"
                        "- Recent significant events or news"
                    ),
                    "temperature": 0.5,
                    "maxTokens": 1536,
                },
            },
            {
                "id": "llm_financial",
                "type": "llm",
                "position": {"x": 1050, "y": 200},
                "data": {
                    "label": "Financial Health Analysis",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a financial analyst specializing in corporate health assessment. "
                        "Analyze financial indicators and provide health ratings."
                    ),
                    "userPrompt": (
                        "Analyze the financial health of {{company_name}}.\n\n"
                        "Company overview:\n{{llm_overview.text}}\n\n"
                        "Assess:\n"
                        "- Revenue trends and growth trajectory\n"
                        "- Profitability metrics (margins, ROE, ROA)\n"
                        "- Debt levels and leverage ratios\n"
                        "- Cash flow health\n"
                        "- Working capital position\n"
                        "- Credit rating indicators\n\n"
                        "Provide a financial health score (A/B/C/D/F) with justification."
                    ),
                    "temperature": 0.4,
                    "maxTokens": 1536,
                },
            },
            {
                "id": "llm_competitive",
                "type": "llm",
                "position": {"x": 1300, "y": 200},
                "data": {
                    "label": "Competitive Position",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a strategy consultant analyzing competitive positioning "
                        "and market dynamics."
                    ),
                    "userPrompt": (
                        "Assess the competitive position of {{company_name}}.\n\n"
                        "Company overview:\n{{llm_overview.text}}\n\n"
                        "Financial health:\n{{llm_financial.text}}\n\n"
                        "Analyze:\n"
                        "- Market position and share\n"
                        "- Key competitors and relative strengths\n"
                        "- Competitive moats (brand, IP, network effects, switching costs)\n"
                        "- Threats and disruption risks\n"
                        "- Growth opportunities\n\n"
                        "Provide a competitive strength rating (Strong / Moderate / Weak)."
                    ),
                    "temperature": 0.5,
                    "maxTokens": 1536,
                },
            },
            {
                "id": "template_report",
                "type": "template",
                "position": {"x": 1550, "y": 200},
                "data": {
                    "label": "Compile Report",
                    "template": (
                        "# Due Diligence Report: {{company_name}}\n\n"
                        "**Analysis Focus:** {{analysis_focus}}\n"
                        "**Date:** {{sys.timestamp}}\n\n"
                        "---\n\n"
                        "## 1. Company Overview\n{{llm_overview.text}}\n\n"
                        "## 2. Financial Health\n{{llm_financial.text}}\n\n"
                        "## 3. Competitive Position\n{{llm_competitive.text}}\n\n"
                        "---\n\n"
                        "*This report was generated by an automated due diligence workflow. "
                        "Findings should be verified by a qualified analyst before use in "
                        "investment or partnership decisions.*"
                    ),
                },
            },
            {
                "id": "output_1",
                "type": "output",
                "position": {"x": 1800, "y": 200},
                "data": {
                    "label": "Due Diligence Report",
                    "outputName": "due_diligence_report",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "http_search_company"},
            {"id": "e2", "source": "http_search_company", "target": "http_fetch_accounts"},
            {"id": "e3", "source": "http_fetch_accounts", "target": "llm_overview"},
            {"id": "e4", "source": "llm_overview", "target": "llm_financial"},
            {"id": "e5", "source": "llm_financial", "target": "llm_competitive"},
            {"id": "e6", "source": "llm_competitive", "target": "template_report"},
            {"id": "e7", "source": "template_report", "target": "output_1"},
        ],
    }


def _portfolio_risk_assessment() -> dict:
    """Portfolio Risk Assessment: start -> http_request (x2) -> llm -> llm -> llm -> output."""
    return {
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "position": {"x": 50, "y": 200},
                "data": {
                    "label": "Start",
                    "variables": [
                        {"name": "account_id", "type": "string"},
                        {"name": "customer_id", "type": "string"},
                        {"name": "risk_tolerance", "type": "string"},
                    ],
                },
            },
            {
                "id": "http_fetch_transactions",
                "type": "http",
                "position": {"x": 300, "y": 200},
                "data": {
                    "label": "Fetch Transactions",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/transactions",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {"account_id": "{{account_id}}"},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "transactions_data",
                    },
                },
            },
            {
                "id": "http_fetch_risk_scores",
                "type": "http",
                "position": {"x": 550, "y": 200},
                "data": {
                    "label": "Fetch Risk Scores",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/risk-scores/{{customer_id}}",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "risk_scores_data",
                    },
                },
            },
            {
                "id": "llm_parse",
                "type": "llm",
                "position": {"x": 800, "y": 200},
                "data": {
                    "label": "Parse Holdings",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a portfolio data parser. Parse transaction and risk score data "
                        "from the bank API into structured holdings data."
                    ),
                    "userPrompt": (
                        "Parse the following portfolio data into a structured holdings list:\n\n"
                        "Transaction History from Bank API:\n{{transactions_data}}\n\n"
                        "Risk Scores from Bank API:\n{{risk_scores_data}}\n\n"
                        "Risk Tolerance: {{risk_tolerance}}\n\n"
                        "For each holding, extract:\n"
                        "- Asset name / ticker\n"
                        "- Asset class (equity, fixed income, commodity, crypto, real estate, cash)\n"
                        "- Approximate allocation percentage\n"
                        "- Sector classification\n\n"
                        "Return as a structured list."
                    ),
                    "temperature": 0.3,
                    "maxTokens": 1024,
                },
            },
            {
                "id": "llm_concentration",
                "type": "llm",
                "position": {"x": 1050, "y": 200},
                "data": {
                    "label": "Concentration & Exposure Analysis",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a portfolio risk analyst. Identify concentration risks, "
                        "sector overweight/underweight, and correlation risks."
                    ),
                    "userPrompt": (
                        "Analyze portfolio concentration and exposure risks:\n\n"
                        "Parsed Holdings:\n{{llm_parse.text}}\n\n"
                        "Risk Tolerance: {{risk_tolerance}}\n\n"
                        "Assess:\n"
                        "- Single-name concentration risk\n"
                        "- Sector concentration (top-heavy sectors)\n"
                        "- Geographic concentration\n"
                        "- Asset class diversification\n"
                        "- Correlation risk between holdings\n"
                        "- Liquidity risk\n\n"
                        "For each risk, rate as: HIGH / MEDIUM / LOW relative to "
                        "the stated risk tolerance."
                    ),
                    "temperature": 0.4,
                    "maxTokens": 1536,
                },
            },
            {
                "id": "llm_recommendations",
                "type": "llm",
                "position": {"x": 1300, "y": 200},
                "data": {
                    "label": "Risk-Adjusted Recommendations",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a portfolio strategy advisor. Provide actionable "
                        "rebalancing recommendations based on risk analysis."
                    ),
                    "userPrompt": (
                        "Generate risk-adjusted portfolio recommendations:\n\n"
                        "Holdings:\n{{llm_parse.text}}\n\n"
                        "Risk Analysis:\n{{llm_concentration.text}}\n\n"
                        "Risk Tolerance: {{risk_tolerance}}\n\n"
                        "Provide:\n"
                        "1. Overall portfolio risk score (1-10, where 10 = highest risk)\n"
                        "2. Top 3 risk warnings with severity\n"
                        "3. Specific rebalancing suggestions\n"
                        "4. Suggested additions for better diversification\n"
                        "5. Positions to consider reducing\n"
                        "6. Summary recommendation (REBALANCE URGENTLY / REVIEW RECOMMENDED / ACCEPTABLE)"
                    ),
                    "temperature": 0.5,
                    "maxTokens": 2048,
                },
            },
            {
                "id": "output_1",
                "type": "output",
                "position": {"x": 1550, "y": 200},
                "data": {
                    "label": "Risk Dashboard",
                    "outputName": "risk_assessment",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "http_fetch_transactions"},
            {"id": "e2", "source": "http_fetch_transactions", "target": "http_fetch_risk_scores"},
            {"id": "e3", "source": "http_fetch_risk_scores", "target": "llm_parse"},
            {"id": "e4", "source": "llm_parse", "target": "llm_concentration"},
            {"id": "e5", "source": "llm_concentration", "target": "llm_recommendations"},
            {"id": "e6", "source": "llm_recommendations", "target": "output_1"},
        ],
    }


def _client_onboarding_checklist() -> dict:
    """Client Onboarding Checklist: start -> http_request (x2) -> llm -> llm -> ifelse -> human_in_loop -> output."""
    return {
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "position": {"x": 50, "y": 200},
                "data": {
                    "label": "Start",
                    "variables": [
                        {"name": "client_name", "type": "string"},
                        {"name": "client_type", "type": "string"},
                        {"name": "jurisdiction", "type": "string"},
                    ],
                },
            },
            {
                "id": "http_search_client",
                "type": "http",
                "position": {"x": 300, "y": 200},
                "data": {
                    "label": "Search Client",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/customers",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {"search": "{{client_name}}"},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "client_search_data",
                    },
                },
            },
            {
                "id": "http_fetch_client_kyc",
                "type": "http",
                "position": {"x": 550, "y": 200},
                "data": {
                    "label": "Fetch Client KYC",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/kyc/{{client_search_data.results[0].id}}",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "client_kyc_data",
                    },
                },
            },
            {
                "id": "llm_requirements",
                "type": "llm",
                "position": {"x": 800, "y": 200},
                "data": {
                    "label": "Determine Required Documents",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a compliance onboarding specialist. Determine the required "
                        "documents and verification steps based on client type, jurisdiction, "
                        "and existing KYC data from the bank."
                    ),
                    "userPrompt": (
                        "Determine the onboarding requirements for:\n\n"
                        "Client: {{client_name}}\n"
                        "Type: {{client_type}}\n"
                        "Jurisdiction: {{jurisdiction}}\n\n"
                        "Existing Customer Record from Bank API:\n{{client_search_data}}\n\n"
                        "Existing KYC Record from Bank API:\n{{client_kyc_data}}\n\n"
                        "List all required:\n"
                        "1. Identity documents (passport, articles of incorporation, etc.)\n"
                        "2. Address verification documents\n"
                        "3. Financial documents (tax returns, bank statements)\n"
                        "4. Regulatory forms (W-8BEN, FATCA, CRS)\n"
                        "5. Beneficial ownership declarations\n"
                        "6. Source of funds/wealth documentation\n\n"
                        "For each, indicate: REQUIRED / CONDITIONAL / OPTIONAL and the reason."
                    ),
                    "temperature": 0.3,
                    "maxTokens": 1536,
                },
            },
            {
                "id": "llm_verification",
                "type": "llm",
                "position": {"x": 1050, "y": 200},
                "data": {
                    "label": "Simulate Document Verification",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are a document verification system. Simulate verifying submitted "
                        "documents against requirements and report completion status."
                    ),
                    "userPrompt": (
                        "Simulate document verification for {{client_name}} ({{client_type}}):\n\n"
                        "Required Documents:\n{{llm_requirements.text}}\n\n"
                        "Simulate that the client has submitted most but not all documents. "
                        "For each requirement:\n"
                        "- Mark as VERIFIED, PENDING, or MISSING\n"
                        "- Add a note for any issues found\n\n"
                        "At the end, provide:\n"
                        "- Completion percentage\n"
                        "- documents_complete: true/false\n"
                        "- List of outstanding items"
                    ),
                    "temperature": 0.4,
                    "maxTokens": 1536,
                },
            },
            {
                "id": "ifelse_complete",
                "type": "ifelse",
                "position": {"x": 1300, "y": 200},
                "data": {
                    "label": "Documents Complete?",
                    "conditions": [
                        {
                            "variable": "llm_verification.text",
                            "operator": "contains",
                            "value": "documents_complete: true",
                        }
                    ],
                    "logicalOperator": "and",
                },
            },
            {
                "id": "hitl_approval",
                "type": "human_in_loop",
                "position": {"x": 1550, "y": 100},
                "data": {
                    "label": "Compliance Officer Approval",
                    "title": "Client Onboarding Approval: {{client_name}}",
                    "description": (
                        "Review onboarding documentation for {{client_name}} ({{client_type}}) "
                        "in {{jurisdiction}}. All required documents have been verified."
                    ),
                    "approverType": "role",
                    "approverConfig": {"role": "compliance_officer"},
                    "options": [
                        {"id": "approve", "label": "Approve Onboarding"},
                        {"id": "reject", "label": "Reject - Request More Info"},
                    ],
                    "requireComment": True,
                    "slaHours": 24,
                },
            },
            {
                "id": "output_complete",
                "type": "output",
                "position": {"x": 1800, "y": 100},
                "data": {
                    "label": "Onboarding Approved",
                    "outputName": "onboarding_status",
                    "outputValue": (
                        "## Client Onboarding: APPROVED\n\n"
                        "**Client:** {{client_name}} ({{client_type}})\n"
                        "**Jurisdiction:** {{jurisdiction}}\n\n"
                        "### Document Requirements\n{{llm_requirements.text}}\n\n"
                        "### Verification Status\n{{llm_verification.text}}\n\n"
                        "**Status:** Approved by compliance officer. Account may be opened."
                    ),
                },
            },
            {
                "id": "output_incomplete",
                "type": "output",
                "position": {"x": 1550, "y": 350},
                "data": {
                    "label": "Documents Incomplete",
                    "outputName": "onboarding_status",
                    "outputValue": (
                        "## Client Onboarding: PENDING - Documents Incomplete\n\n"
                        "**Client:** {{client_name}} ({{client_type}})\n"
                        "**Jurisdiction:** {{jurisdiction}}\n\n"
                        "### Document Requirements\n{{llm_requirements.text}}\n\n"
                        "### Verification Status\n{{llm_verification.text}}\n\n"
                        "**Status:** Cannot proceed to compliance approval until all "
                        "required documents are submitted and verified."
                    ),
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "http_search_client"},
            {"id": "e2", "source": "http_search_client", "target": "http_fetch_client_kyc"},
            {"id": "e3", "source": "http_fetch_client_kyc", "target": "llm_requirements"},
            {"id": "e4", "source": "llm_requirements", "target": "llm_verification"},
            {"id": "e5", "source": "llm_verification", "target": "ifelse_complete"},
            {"id": "e6", "source": "ifelse_complete", "target": "hitl_approval", "sourceHandle": "true"},
            {"id": "e7", "source": "ifelse_complete", "target": "output_incomplete", "sourceHandle": "false"},
            {"id": "e8", "source": "hitl_approval", "target": "output_complete"},
        ],
    }


def _incident_escalation() -> dict:
    """Incident Escalation: start -> http_request (x2) -> llm -> ifelse -> llm (x2 branches) -> output."""
    return {
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "position": {"x": 50, "y": 200},
                "data": {
                    "label": "Start",
                    "variables": [
                        {"name": "customer_id", "type": "string"},
                        {"name": "incident_description", "type": "string"},
                        {"name": "reported_by", "type": "string"},
                    ],
                },
            },
            {
                "id": "http_fetch_alerts",
                "type": "http",
                "position": {"x": 300, "y": 200},
                "data": {
                    "label": "Fetch Compliance Alerts",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/compliance-alerts",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {"customer_id": "{{customer_id}}"},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "compliance_alerts",
                    },
                },
            },
            {
                "id": "http_fetch_incidents",
                "type": "http",
                "position": {"x": 550, "y": 200},
                "data": {
                    "label": "Fetch Prior Incidents",
                    "node_type": "http_request",
                    "config": {
                        "url": "http://localhost:8001/incidents",
                        "method": "GET",
                        "headers": {"Content-Type": "application/json"},
                        "params": {"customer_id": "{{customer_id}}"},
                        "body": "",
                        "timeout": 30,
                        "variable_name": "prior_incidents",
                    },
                },
            },
            {
                "id": "llm_classify",
                "type": "llm",
                "position": {"x": 800, "y": 200},
                "data": {
                    "label": "Classify Incident",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are an incident classification engine for a financial services firm. "
                        "Classify operational incidents by severity and category."
                    ),
                    "userPrompt": (
                        "Classify the following incident:\n\n"
                        "Description: {{incident_description}}\n"
                        "Reported by: {{reported_by}}\n\n"
                        "Compliance Alerts from Bank API:\n{{compliance_alerts}}\n\n"
                        "Prior Incidents from Bank API:\n{{prior_incidents}}\n\n"
                        "Determine:\n"
                        "1. Severity: CRITICAL / HIGH / MEDIUM / LOW\n"
                        "2. Category: data_breach / system_outage / fraud / compliance_violation / "
                        "operational_error / vendor_issue / other\n"
                        "3. Affected systems/departments\n"
                        "4. Estimated impact scope (number of affected clients/transactions)\n"
                        "5. Regulatory notification required: YES / NO\n\n"
                        "Return results with severity clearly stated. "
                        "Include 'CRITICAL' in your response if the severity is critical."
                    ),
                    "temperature": 0.2,
                    "maxTokens": 1024,
                },
            },
            {
                "id": "ifelse_severity",
                "type": "ifelse",
                "position": {"x": 1050, "y": 200},
                "data": {
                    "label": "Critical Severity?",
                    "conditions": [
                        {
                            "variable": "llm_classify.text",
                            "operator": "contains",
                            "value": "CRITICAL",
                        }
                    ],
                    "logicalOperator": "and",
                },
            },
            {
                "id": "llm_urgent",
                "type": "llm",
                "position": {"x": 1300, "y": 100},
                "data": {
                    "label": "Urgent Escalation Report",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are an incident response coordinator handling CRITICAL incidents. "
                        "Generate urgent escalation reports with immediate action items."
                    ),
                    "userPrompt": (
                        "Generate an URGENT incident escalation report:\n\n"
                        "Incident: {{incident_description}}\n"
                        "Reported by: {{reported_by}}\n"
                        "Classification:\n{{llm_classify.text}}\n\n"
                        "Generate:\n"
                        "1. IMMEDIATE ACTIONS (within 15 minutes)\n"
                        "2. Escalation chain (who to notify NOW)\n"
                        "3. Client communication template\n"
                        "4. Regulatory notification draft (if required)\n"
                        "5. War room setup instructions\n"
                        "6. Preliminary root cause hypotheses\n\n"
                        "Mark report as PRIORITY: CRITICAL"
                    ),
                    "temperature": 0.3,
                    "maxTokens": 2048,
                },
            },
            {
                "id": "llm_standard",
                "type": "llm",
                "position": {"x": 1300, "y": 350},
                "data": {
                    "label": "Standard Incident Report",
                    "model": "openai/gpt-4o-mini",
                    "systemPrompt": (
                        "You are an incident response coordinator. Generate standard incident "
                        "reports with appropriate next steps."
                    ),
                    "userPrompt": (
                        "Generate a standard incident report:\n\n"
                        "Incident: {{incident_description}}\n"
                        "Reported by: {{reported_by}}\n"
                        "Classification:\n{{llm_classify.text}}\n\n"
                        "Generate:\n"
                        "1. Incident summary\n"
                        "2. Assigned team and owner\n"
                        "3. Investigation steps\n"
                        "4. Expected resolution timeline\n"
                        "5. Affected party communication (if needed)\n"
                        "6. Follow-up actions and review date"
                    ),
                    "temperature": 0.4,
                    "maxTokens": 1536,
                },
            },
            {
                "id": "output_1",
                "type": "output",
                "position": {"x": 1550, "y": 200},
                "data": {
                    "label": "Incident Report",
                    "outputName": "incident_report",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "start_1", "target": "http_fetch_alerts"},
            {"id": "e2", "source": "http_fetch_alerts", "target": "http_fetch_incidents"},
            {"id": "e3", "source": "http_fetch_incidents", "target": "llm_classify"},
            {"id": "e4", "source": "llm_classify", "target": "ifelse_severity"},
            {"id": "e5", "source": "ifelse_severity", "target": "llm_urgent", "sourceHandle": "true"},
            {"id": "e6", "source": "ifelse_severity", "target": "llm_standard", "sourceHandle": "false"},
            {"id": "e7", "source": "llm_urgent", "target": "output_1"},
            {"id": "e8", "source": "llm_standard", "target": "output_1"},
        ],
    }


# ---------------------------------------------------------------------------
# Workflow Definitions
# ---------------------------------------------------------------------------

WORKFLOWS = [
    {
        "id": WORKFLOW_IDS["kyc_screening_pipeline"],
        "name": "KYC Screening Pipeline",
        "description": (
            "Screen a person or entity for Know Your Customer compliance. "
            "Extracts entity info, runs risk scoring, and produces a structured "
            "KYC report with pass/fail/review recommendation."
        ),
        "spec": _kyc_screening_pipeline(),
    },
    {
        "id": WORKFLOW_IDS["sanctions_check_report"],
        "name": "Sanctions Check & Report",
        "description": (
            "Check an entity against OFAC, EU, and UN sanctions lists plus PEP screening. "
            "Generates a formal compliance report with risk rating and recommended actions."
        ),
        "spec": _sanctions_check_report(),
    },
    {
        "id": WORKFLOW_IDS["company_due_diligence"],
        "name": "Company Due Diligence",
        "description": (
            "Research a company for investment or partnership evaluation. "
            "Covers company overview, financial health, and competitive position, "
            "compiled into a structured due diligence report."
        ),
        "spec": _company_due_diligence(),
    },
    {
        "id": WORKFLOW_IDS["portfolio_risk_assessment"],
        "name": "Portfolio Risk Assessment",
        "description": (
            "Analyze risk exposure across a portfolio of holdings. "
            "Parses holdings, calculates concentration and sector risks, "
            "and generates rebalancing recommendations."
        ),
        "spec": _portfolio_risk_assessment(),
    },
    {
        "id": WORKFLOW_IDS["client_onboarding_checklist"],
        "name": "Client Onboarding Checklist",
        "description": (
            "Step through new client onboarding with compliance gates. "
            "Determines required documents, simulates verification, and routes "
            "to compliance officer approval. Contains a human-in-the-loop node."
        ),
        "spec": _client_onboarding_checklist(),
    },
    {
        "id": WORKFLOW_IDS["incident_escalation"],
        "name": "Incident Escalation",
        "description": (
            "Classify and route operational incidents by severity. "
            "Critical incidents get urgent escalation with immediate actions; "
            "others receive standard incident reports."
        ),
        "spec": _incident_escalation(),
    },
]


# ---------------------------------------------------------------------------
# Agent Definitions
# ---------------------------------------------------------------------------

AGENTS = [
    {
        "id": AGENT_IDS["compliance_analyst"],
        "name": "Compliance Analyst",
        "description": (
            "Specializes in KYC/AML screening, sanctions checks, and regulatory compliance. "
            "Proposes structured compliance workflows when users need thorough entity screening."
        ),
        "icon": "Shield",
        "model": "gpt-4o",
        "system_prompt": (
            "You are a Senior Compliance Analyst at a regulated financial institution. "
            "You have direct access to the bank's core systems through connected tools.\n\n"
            "## Your Data Sources\n"
            "When connectors are enabled for your session, use them proactively:\n"
            "- **search_customers / get_customer**: Look up customer profiles, entity types, PEP status\n"
            "- **screen_watchlist**: Screen names against OFAC SDN, EU, and UN sanctions lists\n"
            "- **get_kyc**: Pull KYC records, verification status, and identity documents\n"
            "- **get_compliance_alerts**: Check for open compliance alerts (suspicious activity, sanctions matches)\n"
            "- **get_risk_score**: Get the customer's risk score breakdown\n"
            "- **get_transactions**: Review transaction history for patterns\n\n"
            "## How to Work\n"
            "1. When asked about a customer, ALWAYS start by searching for them using your tools\n"
            "2. Pull real data before making assessments — never simulate or guess\n"
            "3. Cross-reference findings: customer profile + watchlist + KYC + alerts\n"
            "4. Cite specific data points from the bank's records in your analysis\n"
            "5. Flag risks with severity levels and recommend concrete next steps\n"
            "6. For thorough screening, propose the appropriate compliance workflow\n\n"
            "Be precise, cite regulatory frameworks (BSA/AML, FATF, FinCEN), and flag risks clearly."
        ),
        "temperature": 0.3,
        "max_tokens": 4000,
        "skills": [
            "kyc_verification",
            "aml_screening",
            "sanctions_check",
            "risk_assessment",
            "search_knowledge",
            "list_available_connectors",
            "invoke_connector_tool",
            "get_connector_schema",
        ],
        "category": "Compliance",
        "visibility": "tenant",
    },
    {
        "id": AGENT_IDS["research_analyst"],
        "name": "Research Analyst",
        "description": (
            "Financial research analyst for company research, market analysis, "
            "portfolio risk assessment, and investment due diligence."
        ),
        "icon": "ChartLine",
        "model": "gpt-4o",
        "system_prompt": (
            "You are a Financial Research Analyst at a regulated financial institution. "
            "You have direct access to the bank's core systems through connected tools.\n\n"
            "## Your Data Sources\n"
            "When connectors are enabled for your session, use them proactively:\n"
            "- **search_customers / get_customer**: Look up client profiles and entity information\n"
            "- **get_accounts**: Review client account structures and balances\n"
            "- **get_transactions**: Analyze transaction patterns, volumes, and counterparties\n"
            "- **get_holdings**: Review portfolio positions, sectors, and market values\n"
            "- **get_loans**: Check loan portfolios, payment status, and credit exposure\n"
            "- **get_risk_score**: Pull risk score breakdowns for due diligence\n\n"
            "## How to Work\n"
            "1. When asked about a client or company, search the bank's records first\n"
            "2. Pull real financial data before making assessments — never simulate\n"
            "3. Analyze patterns: transaction flows, portfolio concentration, credit exposure\n"
            "4. Support insights with specific data points from the bank's records\n"
            "5. For comprehensive analysis, propose the appropriate research workflow\n\n"
            "Be analytical, data-driven, and support all conclusions with evidence."
        ),
        "temperature": 0.5,
        "max_tokens": 4000,
        "skills": [
            "web_search",
            "fetch_market_data",
            "analyze_risk_metrics",
            "get_financial_statements",
            "search_knowledge",
            "list_available_connectors",
            "invoke_connector_tool",
            "get_connector_schema",
        ],
        "category": "Research",
        "visibility": "tenant",
    },
    {
        "id": AGENT_IDS["operations_assistant"],
        "name": "Operations Assistant",
        "description": (
            "Handles client onboarding, incident management, and day-to-day "
            "operational tasks with compliance awareness."
        ),
        "icon": "Gear",
        "model": "gpt-4o",
        "system_prompt": (
            "You are an Operations Specialist at a regulated financial institution. "
            "You handle client onboarding, incident management, and operational tasks. "
            "You have direct access to the bank's core systems through connected tools.\n\n"
            "## Your Data Sources\n"
            "When connectors are enabled for your session, use them proactively:\n"
            "- **search_customers / get_customer**: Look up clients for onboarding status\n"
            "- **get_kyc**: Check KYC verification status and outstanding documents\n"
            "- **get_compliance_alerts**: Monitor open compliance alerts and their severity\n"
            "- **get_incidents**: Review security/operational incidents and their status\n"
            "- **get_accounts**: Check account setup and status during onboarding\n"
            "- **get_scenarios**: View available test scenarios for QA validation\n\n"
            "## How to Work\n"
            "1. When asked about a client or incident, pull their records from the bank first\n"
            "2. Check KYC status and outstanding documents before onboarding assessments\n"
            "3. Review open incidents and alerts — prioritize by severity\n"
            "4. For client onboarding, note the workflow includes a compliance approval step\n"
            "5. Propose the appropriate workflow for structured operational processes\n\n"
            "Be organized, proactive, and detail-oriented. Flag blockers immediately."
        ),
        "temperature": 0.5,
        "max_tokens": 4000,
        "skills": [
            "parse_document",
            "summarize_document",
            "web_search",
            "create_task",
            "list_tasks",
            "search_knowledge",
            "list_available_connectors",
            "invoke_connector_tool",
            "get_connector_schema",
        ],
        "category": "Operations",
        "visibility": "tenant",
    },
]


# ---------------------------------------------------------------------------
# Database Operations
# ---------------------------------------------------------------------------

async def get_engine():
    """Create async engine from environment or .env file."""
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        # Try loading from .env
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DATABASE_URL="):
                        database_url = line.split("=", 1)[1].strip()
                        break

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL not found. Set it as an env var or in backend/.env"
        )

    return create_async_engine(database_url, echo=False)


async def seed():
    """Main seed function: delete stale data, create workflows + agents."""
    engine = await get_engine()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        # Set tenant context for RLS
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(TENANT_ID)},
        )

        # ------------------------------------------------------------------
        # 1. Delete existing custom agents on this tenant
        # ------------------------------------------------------------------
        result = await session.execute(
            delete(CustomAgent).where(CustomAgent.tenant_id == TENANT_ID)
        )
        print(f"Deleted {result.rowcount} existing custom agents")

        # Delete existing demo workflows (by known IDs) to avoid duplicates
        demo_wf_ids = list(WORKFLOW_IDS.values())
        result = await session.execute(
            delete(Workflow).where(Workflow.id.in_(demo_wf_ids))
        )
        print(f"Deleted {result.rowcount} existing demo workflows")

        # ------------------------------------------------------------------
        # 2. Create 6 demo workflows
        # ------------------------------------------------------------------
        for wf_def in WORKFLOWS:
            wf = Workflow(
                id=wf_def["id"],
                user_id=USER_ID,
                tenant_id=TENANT_ID,
                name=wf_def["name"],
                description=wf_def["description"],
                spec=wf_def["spec"],
            )
            session.add(wf)
            print(f"  + Workflow: {wf_def['name']} ({wf_def['id']})")

        # ------------------------------------------------------------------
        # 3. Create 3 demo agents
        # ------------------------------------------------------------------
        for agent_def in AGENTS:
            agent = CustomAgent(
                id=agent_def["id"],
                user_id=USER_ID,
                tenant_id=TENANT_ID,
                name=agent_def["name"],
                description=agent_def["description"],
                icon=agent_def["icon"],
                model=agent_def["model"],
                system_prompt=agent_def["system_prompt"],
                temperature=agent_def["temperature"],
                max_tokens=agent_def["max_tokens"],
                skills=agent_def["skills"],
                category=agent_def["category"],
                visibility=agent_def["visibility"],
                is_active=True,
            )
            session.add(agent)
            print(f"  + Agent: {agent_def['name']} ({agent_def['id']})")

        # ------------------------------------------------------------------
        # 4. Create Synthetic Bank connector
        # ------------------------------------------------------------------
        bank_conn_id = CONNECTOR_IDS["synthetic_bank"]
        await session.execute(
            delete(Connector).where(Connector.id == bank_conn_id)
        )
        bank_connector = Connector(
            id=bank_conn_id,
            user_id=USER_ID,
            tenant_id=TENANT_ID,
            connector_type="rest_api",
            provider="synthetic_bank",
            name="Synthetic Bank",
            description="Synthetic banking data for E2E QA testing — customers, accounts, transactions, KYC, compliance, risk scores",
            config={"base_url": "http://localhost:8001", "auth_type": "none"},
            is_active=True,
            is_verified=True,
            source_type="curated",
        )
        session.add(bank_connector)
        print(f"  + Connector: Synthetic Bank ({bank_conn_id})")

        # Flush and commit
        await session.flush()
        await session.commit()
        print("\nSeed data committed successfully.")

        # ------------------------------------------------------------------
        # 4. Verification
        # ------------------------------------------------------------------
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(TENANT_ID)},
        )

        wf_count = await session.execute(
            select(Workflow).where(Workflow.tenant_id == TENANT_ID)
        )
        workflows = wf_count.scalars().all()

        agent_count = await session.execute(
            select(CustomAgent).where(CustomAgent.tenant_id == TENANT_ID)
        )
        agents = agent_count.scalars().all()

        print(f"\nVerification:")
        print(f"  Workflows on tenant: {len(workflows)}")
        for wf in workflows:
            node_count = len(wf.spec.get("nodes", []))
            edge_count = len(wf.spec.get("edges", []))
            has_hitl = any(n["type"] == "human_in_loop" for n in wf.spec.get("nodes", []))
            hitl_tag = " [HITL]" if has_hitl else ""
            print(f"    - {wf.name}: {node_count} nodes, {edge_count} edges{hitl_tag}")

        print(f"  Agents on tenant: {len(agents)}")
        for a in agents:
            print(f"    - {a.name} ({a.icon}): {len(a.skills or [])} skills")

    await engine.dispose()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(seed())
