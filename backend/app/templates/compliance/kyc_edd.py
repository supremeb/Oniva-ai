"""
KYC Enhanced Due Diligence (EDD) Workflow Template
For high-risk customer onboarding and periodic reviews
"""

KYC_EDD_TEMPLATE = {
    "id": "kyc-edd-v1",
    "name": "KYC Enhanced Due Diligence",
    "description": "Automated KYC review workflow for high-risk customer onboarding. Includes sanctions screening, PEP checks, adverse media search, and risk scoring.",
    "version": "1.0.0",
    "category": "compliance",
    "regulatory_regimes": ["SEC", "FINRA", "FinCEN", "FATF"],
    "estimated_duration_seconds": 30,
    "tags": ["kyc", "edd", "onboarding", "high-risk"],

    # React Flow compatible spec
    "spec": {
        "nodes": [
            {
                "id": "start-1",
                "type": "start",
                "position": {"x": 100, "y": 200},
                "data": {
                    "label": "Start KYC Review",
                    "variables": [
                        {"name": "customer_name", "type": "string", "required": True},
                        {"name": "customer_id", "type": "string", "required": True},
                        {"name": "date_of_birth", "type": "string", "required": False},
                        {"name": "nationality", "type": "string", "required": False},
                        {"name": "country_of_residence", "type": "string", "required": False},
                        {"name": "source_of_funds", "type": "string", "required": False},
                        {"name": "occupation", "type": "string", "required": False},
                    ]
                }
            },
            {
                "id": "sanctions-check-1",
                "type": "compliance",
                "position": {"x": 400, "y": 100},
                "data": {
                    "label": "Sanctions Screening",
                    "compliance_type": "sanctions_screening",
                    "config": {
                        "databases": ["OFAC_SDN", "UN_SANCTIONS", "EU_SANCTIONS", "UK_SANCTIONS"],
                        "match_threshold": 0.85,
                        "fields_to_check": ["customer_name", "date_of_birth", "nationality"]
                    }
                }
            },
            {
                "id": "pep-check-1",
                "type": "compliance",
                "position": {"x": 400, "y": 300},
                "data": {
                    "label": "PEP Screening",
                    "compliance_type": "pep_screening",
                    "config": {
                        "databases": ["WORLD_CHECK", "DOW_JONES_PEP"],
                        "pep_categories": [
                            "foreign_official",
                            "domestic_official",
                            "international_org",
                            "family_member",
                            "close_associate"
                        ],
                        "match_threshold": 0.80
                    }
                }
            },
            {
                "id": "adverse-media-1",
                "type": "llm",
                "position": {"x": 700, "y": 200},
                "data": {
                    "label": "Adverse Media Search",
                    "model": "anthropic/claude-3.5-sonnet",
                    "system_prompt": """You are a compliance analyst conducting adverse media screening.

Search for and analyze any negative news, regulatory actions, or legal issues related to the subject.

Focus on:
1. Financial crimes (fraud, money laundering, embezzlement)
2. Regulatory violations or sanctions
3. Criminal proceedings or convictions
4. Corruption or bribery allegations
5. Association with sanctioned entities

Return a structured assessment with:
- findings: List of relevant articles/issues found
- risk_indicators: Specific red flags identified
- severity: low/medium/high/critical
- summary: Brief explanation of findings""",
                    "prompt": """Conduct adverse media screening for:

Name: {{customer_name}}
Date of Birth: {{date_of_birth}}
Nationality: {{nationality}}
Country of Residence: {{country_of_residence}}

Previous screening results:
- Sanctions: {{sanctions-check-1.output}}
- PEP Status: {{pep-check-1.output}}

Search for any negative news, regulatory actions, court records, or other adverse information from the past 10 years.

Provide your findings in JSON format.""",
                    "temperature": 0.1
                }
            },
            {
                "id": "risk-assessment-1",
                "type": "code",
                "position": {"x": 1000, "y": 200},
                "data": {
                    "label": "Calculate Risk Score",
                    "language": "python",
                    "code": """
# Risk scoring based on all screening results
import json

def calculate_risk_score(sanctions_result, pep_result, adverse_media, customer_data):
    score = 0.0
    factors = []

    # High-risk jurisdictions (25% weight)
    high_risk_countries = ["IR", "KP", "SY", "RU", "BY", "MM", "VE", "CU"]
    country = customer_data.get("country_of_residence", "").upper()[:2]
    nationality = customer_data.get("nationality", "").upper()[:2]

    if country in high_risk_countries or nationality in high_risk_countries:
        score += 0.25
        factors.append({"factor": "high_risk_jurisdiction", "weight": 0.25, "detail": f"Country: {country}, Nationality: {nationality}"})

    # Sanctions hit (40% weight - critical)
    if sanctions_result.get("match_found", False):
        score += 0.40
        factors.append({"factor": "sanctions_match", "weight": 0.40, "detail": sanctions_result.get("matches", [])})

    # PEP status (20% weight)
    if pep_result.get("is_pep", False):
        pep_category = pep_result.get("category", "unknown")
        if pep_category in ["foreign_official", "close_associate"]:
            score += 0.20
        else:
            score += 0.15
        factors.append({"factor": "pep_status", "weight": 0.20, "detail": pep_category})

    # Adverse media (15% weight)
    media_severity = adverse_media.get("severity", "low")
    if media_severity == "critical":
        score += 0.15
    elif media_severity == "high":
        score += 0.10
    elif media_severity == "medium":
        score += 0.05

    if media_severity != "low":
        factors.append({"factor": "adverse_media", "weight": 0.15, "detail": media_severity})

    # Determine risk level
    if score >= 0.80:
        risk_level = "critical"
        recommendation = "REJECT - Multiple critical risk factors"
    elif score >= 0.60:
        risk_level = "high"
        recommendation = "ESCALATE - Senior compliance approval required"
    elif score >= 0.40:
        risk_level = "medium"
        recommendation = "REVIEW - Enhanced monitoring recommended"
    elif score >= 0.20:
        risk_level = "low-medium"
        recommendation = "CONDITIONAL - Document risk acceptance"
    else:
        risk_level = "low"
        recommendation = "APPROVE - Standard monitoring"

    return {
        "risk_score": round(min(score, 1.0), 4),
        "risk_level": risk_level,
        "recommendation": recommendation,
        "risk_factors": factors,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat()
    }

# Parse inputs
sanctions = inputs.get("sanctions-check-1", {})
pep = inputs.get("pep-check-1", {})
adverse = inputs.get("adverse-media-1", {})

# Try to parse LLM response if it's a string
if isinstance(adverse, str):
    try:
        adverse = json.loads(adverse)
    except:
        adverse = {"severity": "low", "findings": []}

customer = {
    "country_of_residence": variables.get("country_of_residence", ""),
    "nationality": variables.get("nationality", ""),
    "source_of_funds": variables.get("source_of_funds", ""),
}

result = calculate_risk_score(sanctions, pep, adverse, customer)
output = result
"""
                }
            },
            {
                "id": "output-1",
                "type": "output",
                "position": {"x": 1300, "y": 200},
                "data": {
                    "label": "KYC Result",
                    "output_schema": {
                        "customer_id": "string",
                        "customer_name": "string",
                        "risk_score": "number",
                        "risk_level": "string",
                        "recommendation": "string",
                        "sanctions_result": "object",
                        "pep_result": "object",
                        "adverse_media": "object",
                        "risk_factors": "array",
                        "review_date": "string"
                    }
                }
            }
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "sanctions-check-1"},
            {"id": "e2", "source": "start-1", "target": "pep-check-1"},
            {"id": "e3", "source": "sanctions-check-1", "target": "adverse-media-1"},
            {"id": "e4", "source": "pep-check-1", "target": "adverse-media-1"},
            {"id": "e5", "source": "adverse-media-1", "target": "risk-assessment-1"},
            {"id": "e6", "source": "risk-assessment-1", "target": "output-1"}
        ]
    },

    # Sample input for testing
    "sample_input": {
        "customer_name": "John Smith",
        "customer_id": "CUST-12345",
        "date_of_birth": "1975-03-15",
        "nationality": "United States",
        "country_of_residence": "United States",
        "source_of_funds": "Employment - Software Engineer",
        "occupation": "Software Engineer"
    }
}
