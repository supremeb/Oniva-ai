"""
Compliance Workflow Templates
Pre-built templates for KYC, AML, Sanctions, and other compliance use cases
"""

from typing import Dict, Any, List
from app.templates.compliance.kyc_edd import KYC_EDD_TEMPLATE
from app.templates.compliance.sanctions_screening import SANCTIONS_SCREENING_TEMPLATE
from app.templates.compliance.aml_transaction import AML_TRANSACTION_TEMPLATE


def get_compliance_templates() -> List[Dict[str, Any]]:
    """Get all available compliance workflow templates"""
    return [
        KYC_EDD_TEMPLATE,
        SANCTIONS_SCREENING_TEMPLATE,
        AML_TRANSACTION_TEMPLATE,
    ]


def get_template_by_id(template_id: str) -> Dict[str, Any] | None:
    """Get a specific template by ID"""
    templates = {t["id"]: t for t in get_compliance_templates()}
    return templates.get(template_id)


__all__ = [
    "get_compliance_templates",
    "get_template_by_id",
    "KYC_EDD_TEMPLATE",
    "SANCTIONS_SCREENING_TEMPLATE",
    "AML_TRANSACTION_TEMPLATE",
]
