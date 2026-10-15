"""
Compliance Skills
Skills for KYC, AML, sanctions, and regulatory compliance

NOTE: These functions are kept for backwards compatibility.
New skills should use the SKILL.md format in app/skills/<skill_name>/
"""

from typing import Dict, Any, List
from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill


async def kyc_verification(ctx: SkillContext, full_name: str, dob: str, country: str) -> Dict[str, Any]:
    """
    Perform KYC verification.
    """
    # Mock logic
    risk_level = "low"
    if country.lower() in ["north korea", "iran"]:
        risk_level = "high"
        
    return {
        "status": "verified" if risk_level != "high" else "flagged",
        "risk_level": risk_level,
        "checks_passed": ["identity_match", "dob_match"] if risk_level != "high" else ["identity_match"]
    }

async def aml_screening(ctx: SkillContext, amount: float, sender_country: str, receiver_country: str) -> Dict[str, Any]:
    """
    Screen a transaction for AML risk.
    """
    flags = []
    if amount > 10000:
        flags.append("large_transaction")
    if sender_country != receiver_country:
        flags.append("cross_border")
        
    risk_score = len(flags) * 25
    
    return {
        "risk_score": risk_score, # 0-100
        "flags": flags,
        "action": "approve" if risk_score < 50 else "manual_review"
    }

async def sanctions_check(ctx: SkillContext, entity_name: str) -> Dict[str, Any]:
    """
    Check sanctions list.
    """
    # Mock list
    sanctioned_entities = ["Evil Corp", "Bad Guys LLC", "Smugglers Inc"]
    
    is_sanctioned = False
    for s in sanctioned_entities:
        if s.lower() in entity_name.lower():
            is_sanctioned = True
            break
            
    return {
        "entity": entity_name,
        "is_sanctioned": is_sanctioned,
        "source": "Mock OFAC List" if is_sanctioned else None
    }

@skill(
    name="batch_sanctions_check",
    description="Check multiple entities against OFAC/EU sanctions lists in one call. Preferred over individual sanctions_check for bulk screening.",
    category="compliance",
)
async def batch_sanctions_check(ctx: SkillContext, entity_names: list) -> Dict[str, Any]:
    """
    Check multiple entities against OFAC/EU sanctions lists in a single call.
    Accepts a list of entity names and returns results for all of them.
    Use this instead of calling sanctions_check repeatedly for each entity.
    """
    sanctioned_entities = ["Evil Corp", "Bad Guys LLC", "Smugglers Inc"]
    results = []
    flagged_count = 0
    for entity_name in entity_names:
        is_sanctioned = any(s.lower() in entity_name.lower() for s in sanctioned_entities)
        if is_sanctioned:
            flagged_count += 1
        results.append({
            "entity": entity_name,
            "is_sanctioned": is_sanctioned,
            "source": "Mock OFAC List" if is_sanctioned else None,
        })
    return {
        "total_checked": len(entity_names),
        "flagged_count": flagged_count,
        "results": results,
    }


@skill(
    name="batch_kyc_verification",
    description="Perform KYC verification for multiple entities in one call. Preferred over individual kyc_verification for bulk checks.",
    category="compliance",
)
async def batch_kyc_verification(ctx: SkillContext, entities: list) -> Dict[str, Any]:
    """
    Perform KYC verification for multiple entities in a single call.
    Each entity in the list should be a dict with: full_name, dob, country.
    Use this instead of calling kyc_verification repeatedly for each entity.
    """
    results = []
    flagged_count = 0
    for entity in entities:
        name = entity.get("full_name", "") if isinstance(entity, dict) else str(entity)
        country = entity.get("country", "") if isinstance(entity, dict) else ""
        risk_level = "high" if country.lower() in ["north korea", "iran"] else "low"
        if risk_level == "high":
            flagged_count += 1
        results.append({
            "full_name": name,
            "country": country,
            "status": "verified" if risk_level != "high" else "flagged",
            "risk_level": risk_level,
            "checks_passed": ["identity_match", "dob_match"] if risk_level != "high" else ["identity_match"],
        })
    return {
        "total_checked": len(entities),
        "flagged_count": flagged_count,
        "results": results,
    }


async def regulatory_filing(ctx: SkillContext, cik: str, filing_type: str = "10-K") -> Dict[str, Any]:
    """
    Get regulatory filings.
    """
    return {
        "cik": cik,
        "filing_type": filing_type,
        "latest_filing_date": "2023-03-15",
        "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/0000000000-23-000000-index.html",
        "summary": "Mock summary of the filing..."
    }

async def risk_assessment(ctx: SkillContext, entity_name: str, entity_type: str) -> Dict[str, Any]:
    """
    Assess overall risk.
    """
    # This mock would ideally call other skills, but for simplicity we just return a score
    # In a real scenario, we use ctx.invoke_skill()
    
    return {
        "entity": entity_name,
        "type": entity_type,
        "composite_risk_score": 15, # Low
        "rating": "Low Risk",
        "factors": {
            "geo_risk": "low",
            "industry_risk": "medium",
            "sanctions_risk": "none"
        }
    }
