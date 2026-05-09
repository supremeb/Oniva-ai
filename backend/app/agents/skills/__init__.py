# Skills Module
# Provides composable skill/ability system for agents
#
# Skills are loaded from SKILL.md directories in app/skills/<name>/.
# See app/skills/web_search/ for an example.

from app.agents.skills.registry import SkillRegistry, skill, Skill

__all__ = ["SkillRegistry", "skill", "Skill"]
