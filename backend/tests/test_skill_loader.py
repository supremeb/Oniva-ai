"""
Tests for SkillLoader and unified registry loading.
"""

import pytest
import tempfile
from pathlib import Path

from app.agents.skills.loader import SkillLoader, SkillMetadata, LoadedSkill


class TestSkillMetadata:
    """Tests for SkillMetadata dataclass."""

    def test_from_frontmatter_minimal(self):
        """Test parsing minimal frontmatter."""
        frontmatter = {
            "name": "test-skill",
            "description": "A test skill",
        }
        metadata = SkillMetadata.from_frontmatter(frontmatter)

        assert metadata.name == "test-skill"
        assert metadata.description == "A test skill"
        assert metadata.type == "tool"  # default
        assert metadata.category == "general"  # default

    def test_from_frontmatter_full(self):
        """Test parsing full frontmatter with all fields."""
        frontmatter = {
            "name": "sanctions-check",
            "description": "Check entity against sanctions lists",
            "type": "tool",
            "category": "compliance",
            "tool": {
                "module": "app.skills.sanctions_check.tool",
                "function": "sanctions_check",
                "async": True,
            },
            "metadata": {
                "author": "oniva",
                "version": "2.0.0",
            },
            "audit-level": "full",
            "tenant-scope": "organization",
            "retention-years": 10,
            "timeout-seconds": 30,
            "max-retries": 5,
        }
        metadata = SkillMetadata.from_frontmatter(frontmatter, "# Documentation")

        assert metadata.name == "sanctions-check"
        assert metadata.description == "Check entity against sanctions lists"
        assert metadata.type == "tool"
        assert metadata.category == "compliance"
        assert metadata.tool_module == "app.skills.sanctions_check.tool"
        assert metadata.tool_function == "sanctions_check"
        assert metadata.tool_async is True
        assert metadata.author == "oniva"
        assert metadata.version == "2.0.0"
        assert metadata.audit_level == "full"
        assert metadata.tenant_scope == "organization"
        assert metadata.retention_years == 10
        assert metadata.timeout_seconds == 30
        assert metadata.max_retries == 5
        assert metadata.documentation == "# Documentation"

    def test_from_frontmatter_mcp_type(self):
        """Test parsing MCP type skill."""
        frontmatter = {
            "name": "linear-issues",
            "description": "Manage Linear issues",
            "type": "mcp",
            "mcp": {
                "server": "linear",
                "tool": "list_issues",
                "tools": ["list_issues", "create_issue"],
            },
        }
        metadata = SkillMetadata.from_frontmatter(frontmatter)

        assert metadata.type == "mcp"
        assert metadata.mcp_server == "linear"
        assert metadata.mcp_tool == "list_issues"
        assert metadata.mcp_tools == ["list_issues", "create_issue"]


class TestSkillLoaderParsing:
    """Tests for SKILL.md parsing."""

    def test_parse_valid_skill_md(self):
        """Test parsing valid SKILL.md content."""
        content = """---
name: test-skill
description: A test skill for unit testing
type: tool
category: testing

tool:
  module: app.skills.test.tool
  function: test_function
  async: true

metadata:
  author: test-author
  version: "1.0.0"
---

# Test Skill

This is the documentation for the test skill.

## Usage

```python
result = await test_function(query="test")
```
"""
        metadata, errors = SkillLoader.parse_skill_md(content)

        assert len(errors) == 0
        assert metadata.name == "test-skill"
        assert metadata.description == "A test skill for unit testing"
        assert metadata.type == "tool"
        assert metadata.category == "testing"
        assert metadata.tool_module == "app.skills.test.tool"
        assert metadata.tool_function == "test_function"
        assert "# Test Skill" in metadata.documentation

    def test_parse_missing_frontmatter(self):
        """Test parsing content without frontmatter."""
        content = "# Just a heading\n\nSome content without frontmatter."
        metadata, errors = SkillLoader.parse_skill_md(content)

        assert len(errors) == 1
        assert "missing frontmatter" in errors[0].lower()
        assert metadata.name == ""

    def test_parse_invalid_yaml(self):
        """Test parsing invalid YAML frontmatter."""
        content = """---
name: test
description: [invalid yaml
---

# Content
"""
        metadata, errors = SkillLoader.parse_skill_md(content)

        assert len(errors) == 1
        assert "yaml" in errors[0].lower() or "parse" in errors[0].lower()


class TestSkillLoaderValidation:
    """Tests for skill metadata validation."""

    def test_validate_missing_name(self):
        """Test validation catches missing name."""
        metadata = SkillMetadata(name="", description="A skill")
        errors = SkillLoader.validate_metadata(metadata, Path("/tmp/test"))

        assert any("name" in e.lower() for e in errors)

    def test_validate_missing_description(self):
        """Test validation catches missing description."""
        metadata = SkillMetadata(name="test", description="")
        errors = SkillLoader.validate_metadata(metadata, Path("/tmp/test"))

        assert any("description" in e.lower() for e in errors)

    def test_validate_invalid_name_format(self):
        """Test validation catches invalid name format."""
        metadata = SkillMetadata(name="Test_Skill", description="A skill")
        errors = SkillLoader.validate_metadata(metadata, Path("/tmp/Test_Skill"))

        assert any("lowercase" in e.lower() or "name" in e.lower() for e in errors)

    def test_validate_name_too_long(self):
        """Test validation catches name that's too long."""
        metadata = SkillMetadata(name="a" * 65, description="A skill")
        errors = SkillLoader.validate_metadata(metadata, Path("/tmp/" + "a" * 65))

        assert any("64" in e for e in errors)

    def test_validate_tool_type_without_config(self):
        """Test validation catches tool type without module/function."""
        metadata = SkillMetadata(
            name="test-skill",
            description="A skill",
            type="tool",
            tool_module=None,
            tool_function=None,
        )
        errors = SkillLoader.validate_metadata(metadata, Path("/tmp/test-skill"))

        assert any("module" in e.lower() or "function" in e.lower() for e in errors)

    def test_validate_mcp_type_without_server(self):
        """Test validation catches MCP type without server."""
        metadata = SkillMetadata(
            name="test-skill",
            description="A skill",
            type="mcp",
            mcp_server=None,
        )
        errors = SkillLoader.validate_metadata(metadata, Path("/tmp/test-skill"))

        assert any("server" in e.lower() for e in errors)

    def test_validate_invalid_type(self):
        """Test validation catches invalid type."""
        metadata = SkillMetadata(
            name="test-skill",
            description="A skill",
            type="invalid_type",
        )
        errors = SkillLoader.validate_metadata(metadata, Path("/tmp/test-skill"))

        assert any("type" in e.lower() for e in errors)

    def test_validate_directory_name_mismatch(self):
        """Test validation catches name/directory mismatch."""
        metadata = SkillMetadata(
            name="test-skill",
            description="A skill",
            type="instruction",
        )
        errors = SkillLoader.validate_metadata(metadata, Path("/tmp/different-name"))

        assert any("directory" in e.lower() or "match" in e.lower() for e in errors)


class TestSkillLoaderLoading:
    """Tests for loading skills from files."""

    @pytest.mark.asyncio
    async def test_load_skill_missing_directory(self):
        """Test loading from non-existent directory."""
        skill = await SkillLoader.load_skill("/tmp/nonexistent-skill-12345")

        assert not skill.is_valid
        assert any("not found" in e.lower() for e in skill.errors)

    @pytest.mark.asyncio
    async def test_load_skill_missing_skill_md(self):
        """Test loading from directory without SKILL.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill = await SkillLoader.load_skill(tmpdir)

            assert not skill.is_valid
            assert any("skill.md" in e.lower() for e in skill.errors)

    @pytest.mark.asyncio
    async def test_load_valid_instruction_skill(self):
        """Test loading a valid instruction-type skill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-instruction"
            skill_dir.mkdir()

            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text("""---
name: test-instruction
description: An instruction skill that provides guidance
type: instruction
category: guidance
---

# Test Instruction

This skill provides instructions without executable code.

## When to Use

Use this skill when you need guidance on...
""")

            skill = await SkillLoader.load_skill(str(skill_dir))

            assert skill.is_valid
            assert skill.metadata.name == "test-instruction"
            assert skill.metadata.type == "instruction"
            assert skill.handler is None  # Instruction skills don't have handlers

    @pytest.mark.asyncio
    async def test_load_directory(self):
        """Test loading multiple skills from a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two skill directories
            for name in ["skill-one", "skill-two"]:
                skill_dir = Path(tmpdir) / name
                skill_dir.mkdir()
                skill_md = skill_dir / "SKILL.md"
                skill_md.write_text(f"""---
name: {name}
description: Test skill {name}
type: instruction
---

# {name}
Documentation here.
""")

            skills = await SkillLoader.load_directory(tmpdir)

            assert len(skills) == 2
            assert "skill-one" in skills
            assert "skill-two" in skills


class TestLoadedSkill:
    """Tests for LoadedSkill dataclass."""

    def test_is_valid_with_valid_skill(self):
        """Test is_valid returns True for valid skill."""
        skill = LoadedSkill(
            metadata=SkillMetadata(name="test", description="Test"),
            handler=lambda: None,
            errors=[],
        )
        assert skill.is_valid

    def test_is_valid_with_errors(self):
        """Test is_valid returns False when errors exist."""
        skill = LoadedSkill(
            metadata=SkillMetadata(name="test", description="Test"),
            handler=lambda: None,
            errors=["Some error"],
        )
        assert not skill.is_valid

    def test_is_valid_with_empty_name(self):
        """Test is_valid returns False when name is empty."""
        skill = LoadedSkill(
            metadata=SkillMetadata(name="", description="Test"),
            handler=lambda: None,
            errors=[],
        )
        assert not skill.is_valid


class TestSkillLoaderIntegration:
    """Integration tests for loading actual project skills."""

    @pytest.mark.asyncio
    async def test_load_web_search_skill(self):
        """Test loading the web_search pilot skill."""
        skill_path = Path(__file__).parent.parent / "app" / "skills" / "web_search"

        if not skill_path.exists():
            pytest.skip("web_search skill not found")

        skill = await SkillLoader.load_skill(str(skill_path))

        assert skill.is_valid, f"Errors: {skill.errors}"
        assert skill.metadata.name == "web_search"
        assert skill.metadata.type == "tool"
        assert skill.metadata.category == "web"
        assert skill.handler is not None

    @pytest.mark.asyncio
    async def test_load_sanctions_check_skill(self):
        """Test loading the sanctions_check pilot skill."""
        skill_path = Path(__file__).parent.parent / "app" / "skills" / "sanctions_check"

        if not skill_path.exists():
            pytest.skip("sanctions_check skill not found")

        skill = await SkillLoader.load_skill(str(skill_path))

        assert skill.is_valid, f"Errors: {skill.errors}"
        assert skill.metadata.name == "sanctions_check"
        assert skill.metadata.type == "tool"
        assert skill.metadata.category == "compliance"
        assert skill.metadata.audit_level == "full"
        assert skill.handler is not None

    @pytest.mark.asyncio
    async def test_load_skills_directory(self):
        """Test loading all skills from the skills directory."""
        skills_path = Path(__file__).parent.parent / "app" / "skills"

        if not skills_path.exists():
            pytest.skip("skills directory not found")

        skills = await SkillLoader.load_directory(str(skills_path))

        # Should load at least our two pilot skills
        assert len(skills) >= 2
        assert "web_search" in skills or "web-search" in skills
        assert "sanctions_check" in skills or "sanctions-check" in skills
