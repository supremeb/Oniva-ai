"""Mock skills must be flagged so the UI can label them (plan U7 / R7)."""
from pathlib import Path

import pytest

from app.agents.skills.loader import SkillLoader

MOCK_SKILLS = [
    "sanctions_check", "kyc_verification", "aml_screening", "regulatory_filing",
    "risk_assessment", "fetch_market_data", "get_financial_statements",
    "fetch_economic_indicators", "extract_entities",
]
SKILLS_DIR = Path(__file__).resolve().parents[1] / "app" / "skills"


@pytest.mark.parametrize("name", MOCK_SKILLS)
def test_mock_skill_manifest_flagged(name):
    meta, errors = SkillLoader.parse_skill_md((SKILLS_DIR / name / "SKILL.md").read_text())
    assert not errors
    assert meta.mock is True


def test_real_skill_not_flagged():
    meta, _ = SkillLoader.parse_skill_md((SKILLS_DIR / "web_search" / "SKILL.md").read_text())
    assert meta.mock is False
