"""
AML Screening Skill
Screen transactions for money laundering patterns.
"""

from typing import Any, Dict

from app.agents.skills.context import SkillContext


async def aml_screening(
    ctx: SkillContext,
    amount: float,
    sender_country: str,
    receiver_country: str,
) -> Dict[str, Any]:
    """
    Screen a transaction for AML risk.

    Args:
        ctx: Skill execution context
        amount: Transaction amount
        sender_country: Sender's country
        receiver_country: Receiver's country

    Returns:
        Risk score, flags, and recommended action
    """
    flags = []
    if amount > 10000:
        flags.append("large_transaction")
    if sender_country != receiver_country:
        flags.append("cross_border")

    risk_score = len(flags) * 25

    return {
        "risk_score": risk_score,  # 0-100
        "flags": flags,
        "action": "approve" if risk_score < 50 else "manual_review",
    }
