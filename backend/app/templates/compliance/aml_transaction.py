"""
AML Transaction Monitoring Workflow Template
Analyze transactions for suspicious patterns
"""

AML_TRANSACTION_TEMPLATE = {
    "id": "aml-transaction-v1",
    "name": "AML Transaction Monitoring",
    "description": "Analyze transactions for money laundering indicators. Detects structuring, rapid movement, unusual patterns, and high-risk counterparties.",
    "version": "1.0.0",
    "category": "compliance",
    "regulatory_regimes": ["BSA", "FinCEN", "FATF", "AMLD"],
    "estimated_duration_seconds": 15,
    "tags": ["aml", "transaction", "monitoring", "suspicious-activity"],

    "spec": {
        "nodes": [
            {
                "id": "start-1",
                "type": "start",
                "position": {"x": 100, "y": 200},
                "data": {
                    "label": "Start AML Analysis",
                    "variables": [
                        {"name": "transaction_id", "type": "string", "required": True},
                        {"name": "customer_id", "type": "string", "required": True},
                        {"name": "amount", "type": "number", "required": True},
                        {"name": "currency", "type": "string", "required": True},
                        {"name": "transaction_type", "type": "string", "required": True},
                        {"name": "counterparty", "type": "string", "required": False},
                        {"name": "counterparty_country", "type": "string", "required": False},
                        {"name": "transaction_date", "type": "string", "required": True},
                    ]
                }
            },
            {
                "id": "pattern-analysis-1",
                "type": "code",
                "position": {"x": 400, "y": 100},
                "data": {
                    "label": "Pattern Analysis",
                    "language": "python",
                    "code": """
# AML pattern detection rules
import json
from datetime import datetime

amount = float(variables.get("amount", 0))
currency = variables.get("currency", "USD")
tx_type = variables.get("transaction_type", "")
counterparty_country = variables.get("counterparty_country", "")

alerts = []
risk_score = 0.0

# Rule 1: Large cash transaction (CTR threshold)
CTR_THRESHOLD = 10000
if tx_type.lower() in ["cash", "cash_deposit", "cash_withdrawal"]:
    if amount >= CTR_THRESHOLD:
        alerts.append({
            "rule": "LARGE_CASH",
            "severity": "medium",
            "description": f"Cash transaction >= ${CTR_THRESHOLD:,}",
            "regulatory_reference": "BSA CTR requirement"
        })
        risk_score += 0.2

# Rule 2: Structuring detection (just under threshold)
if tx_type.lower() in ["cash", "cash_deposit"] and 8000 <= amount < CTR_THRESHOLD:
    alerts.append({
        "rule": "POTENTIAL_STRUCTURING",
        "severity": "high",
        "description": f"Cash transaction just under CTR threshold: ${amount:,}",
        "regulatory_reference": "31 USC 5324 - Structuring"
    })
    risk_score += 0.35

# Rule 3: High-risk jurisdiction
HIGH_RISK_COUNTRIES = ["IR", "KP", "SY", "MM", "YE", "AF", "VE"]
if counterparty_country.upper()[:2] in HIGH_RISK_COUNTRIES:
    alerts.append({
        "rule": "HIGH_RISK_JURISDICTION",
        "severity": "high",
        "description": f"Transaction involves high-risk country: {counterparty_country}",
        "regulatory_reference": "FATF High-Risk Jurisdictions"
    })
    risk_score += 0.3

# Rule 4: Round amount (potential structuring indicator)
if amount >= 1000 and amount % 1000 == 0:
    alerts.append({
        "rule": "ROUND_AMOUNT",
        "severity": "low",
        "description": f"Suspiciously round amount: ${amount:,}",
        "regulatory_reference": "AML typology indicator"
    })
    risk_score += 0.1

# Rule 5: Wire transfer threshold
WIRE_THRESHOLD = 3000
if tx_type.lower() in ["wire", "international_wire"] and amount >= WIRE_THRESHOLD:
    alerts.append({
        "rule": "REPORTABLE_WIRE",
        "severity": "medium",
        "description": f"Wire transfer >= ${WIRE_THRESHOLD:,}",
        "regulatory_reference": "Travel Rule / Funds Transfer Rule"
    })
    risk_score += 0.15

output = {
    "alerts": alerts,
    "alert_count": len(alerts),
    "preliminary_risk_score": round(min(risk_score, 1.0), 4),
    "requires_sar_review": risk_score >= 0.5
}
"""
                }
            },
            {
                "id": "counterparty-check-1",
                "type": "compliance",
                "position": {"x": 400, "y": 300},
                "data": {
                    "label": "Counterparty Screening",
                    "compliance_type": "sanctions_screening",
                    "config": {
                        "databases": ["OFAC_SDN", "UN_SANCTIONS"],
                        "match_threshold": 0.85,
                        "fields_to_check": ["counterparty"]
                    }
                }
            },
            {
                "id": "risk-aggregation-1",
                "type": "code",
                "position": {"x": 700, "y": 200},
                "data": {
                    "label": "Aggregate Risk",
                    "language": "python",
                    "code": """
# Aggregate all risk signals
import json

pattern_result = inputs.get("pattern-analysis-1", {})
counterparty_result = inputs.get("counterparty-check-1", {})

# Base risk from pattern analysis
base_risk = pattern_result.get("preliminary_risk_score", 0)
alerts = pattern_result.get("alerts", [])

# Add counterparty sanctions risk
if counterparty_result.get("match_found", False):
    base_risk += 0.4
    alerts.append({
        "rule": "COUNTERPARTY_SANCTIONS",
        "severity": "critical",
        "description": "Counterparty matches sanctions list",
        "matches": counterparty_result.get("matches", [])
    })

final_risk = min(base_risk, 1.0)

# Determine action
if final_risk >= 0.7:
    action = "BLOCK_AND_SAR"
    recommendation = "Block transaction and file SAR immediately"
elif final_risk >= 0.5:
    action = "HOLD_FOR_REVIEW"
    recommendation = "Hold transaction pending compliance review"
elif final_risk >= 0.3:
    action = "FLAG_AND_MONITOR"
    recommendation = "Allow with enhanced monitoring"
else:
    action = "ALLOW"
    recommendation = "Transaction approved - standard monitoring"

output = {
    "transaction_id": variables.get("transaction_id"),
    "customer_id": variables.get("customer_id"),
    "amount": variables.get("amount"),
    "currency": variables.get("currency"),
    "final_risk_score": round(final_risk, 4),
    "risk_level": "critical" if final_risk >= 0.7 else "high" if final_risk >= 0.5 else "medium" if final_risk >= 0.3 else "low",
    "action": action,
    "recommendation": recommendation,
    "alerts": alerts,
    "alert_count": len(alerts),
    "requires_sar": final_risk >= 0.5,
    "analysis_timestamp": __import__("datetime").datetime.utcnow().isoformat()
}
"""
                }
            },
            {
                "id": "output-1",
                "type": "output",
                "position": {"x": 1000, "y": 200},
                "data": {
                    "label": "AML Result",
                    "output_type": "aml_analysis"
                }
            }
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "pattern-analysis-1"},
            {"id": "e2", "source": "start-1", "target": "counterparty-check-1"},
            {"id": "e3", "source": "pattern-analysis-1", "target": "risk-aggregation-1"},
            {"id": "e4", "source": "counterparty-check-1", "target": "risk-aggregation-1"},
            {"id": "e5", "source": "risk-aggregation-1", "target": "output-1"}
        ]
    },

    "sample_input": {
        "transaction_id": "TXN-2026-001234",
        "customer_id": "CUST-12345",
        "amount": 9500,
        "currency": "USD",
        "transaction_type": "cash_deposit",
        "counterparty": "Cash",
        "counterparty_country": "US",
        "transaction_date": "2026-01-08"
    }
}
