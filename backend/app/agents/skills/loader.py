"""
Skill Loader
Loads skills from SKILL.md directories following the agentskills.io specification.

Supports:
- tool: Python function with SKILL.md documentation
- instruction: Pure guidance without executable code
- mcp: Wraps MCP server tools
- composite: Combines multiple tools and instructions
"""

import importlib
import inspect
import logging
import re
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SkillMetadata:
    """Parsed metadata from SKILL.md frontmatter"""

    # Required (agentskills.io standard)
    name: str
    description: str

    # Oniva extensions
    type: str = "tool"  # instruction | tool | mcp | composite
    category: str = "general"

    # Tool configuration
    tool_module: Optional[str] = None
    tool_function: Optional[str] = None
    tool_async: bool = True

    # MCP configuration
    mcp_server: Optional[str] = None
    mcp_tool: Optional[str] = None
    mcp_tools: List[str] = field(default_factory=list)

    # Dependencies
    requires: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)

    # Enterprise fields
    tenant_scope: str = "global"  # global | organization | user
    audit_level: str = "basic"  # none | basic | full
    retention_years: int = 7

    # Execution constraints
    timeout_seconds: int = 60
    max_retries: int = 3
    rate_limit: Optional[str] = None

    # Metadata
    author: str = "oniva"
    version: str = "1.0.0"
    license: Optional[str] = None
    compatibility: Optional[str] = None
    allowed_tools: Optional[str] = None

    # Content
    documentation: str = ""  # Markdown content after frontmatter

    @classmethod
    def from_frontmatter(cls, frontmatter: Dict[str, Any], content: str = "") -> "SkillMetadata":
        """Create SkillMetadata from parsed frontmatter dict"""
        # Extract tool configuration
        tool_config = frontmatter.get("tool", {})
        if isinstance(tool_config, dict):
            tool_module = tool_config.get("module")
            tool_function = tool_config.get("function")
            tool_async = tool_config.get("async", True)
        else:
            tool_module = tool_function = None
            tool_async = True

        # Extract MCP configuration
        mcp_config = frontmatter.get("mcp", {})
        if isinstance(mcp_config, dict):
            mcp_server = mcp_config.get("server")
            mcp_tool = mcp_config.get("tool")
            mcp_tools = mcp_config.get("tools", [])
        else:
            mcp_server = mcp_tool = None
            mcp_tools = []

        # Extract metadata
        metadata = frontmatter.get("metadata", {})
        if isinstance(metadata, dict):
            author = metadata.get("author", "oniva")
            version = metadata.get("version", "1.0.0")
        else:
            author = "oniva"
            version = "1.0.0"

        return cls(
            name=frontmatter.get("name", ""),
            description=frontmatter.get("description", ""),
            type=frontmatter.get("type", "tool"),
            category=frontmatter.get("category", "general"),
            tool_module=tool_module,
            tool_function=tool_function,
            tool_async=tool_async,
            mcp_server=mcp_server,
            mcp_tool=mcp_tool,
            mcp_tools=mcp_tools,
            requires=frontmatter.get("requires", []) or [],
            mcp_servers=frontmatter.get("mcp-servers", []) or [],
            tenant_scope=frontmatter.get("tenant-scope", "global"),
            audit_level=frontmatter.get("audit-level", "basic"),
            retention_years=frontmatter.get("retention-years", 7),
            timeout_seconds=frontmatter.get("timeout-seconds", 60),
            max_retries=frontmatter.get("max-retries", 3),
            rate_limit=frontmatter.get("rate-limit"),
            author=author,
            version=version,
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            allowed_tools=frontmatter.get("allowed-tools"),
            documentation=content,
        )


@dataclass
class LoadedSkill:
    """A skill loaded from SKILL.md"""

    metadata: SkillMetadata
    handler: Optional[Callable] = None  # None for instruction skills
    source_path: Optional[Path] = None
    errors: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Check if skill loaded successfully"""
        return len(self.errors) == 0 and self.metadata.name != ""


class SkillLoader:
    """
    Loads skills from SKILL.md directories.

    Usage:
        loader = SkillLoader()
        skills = await loader.load_directory("backend/app/skills/")

        # Or load single skill
        skill = await loader.load_skill("backend/app/skills/sanctions-check/")
    """

    # Regex pattern to extract YAML frontmatter
    FRONTMATTER_PATTERN = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n?(.*)",
        re.DOTALL | re.MULTILINE
    )

    @classmethod
    async def load_directory(cls, base_path: str) -> Dict[str, LoadedSkill]:
        """
        Scan directory for SKILL.md files and load all skills.

        Args:
            base_path: Path to skills directory

        Returns:
            Dict of skill_name -> LoadedSkill
        """
        skills: Dict[str, LoadedSkill] = {}
        path = Path(base_path)

        if not path.exists():
            logger.warning(f"Skills directory not found: {base_path}")
            return skills

        # Find all SKILL.md files
        for skill_md in path.glob("*/SKILL.md"):
            skill_dir = skill_md.parent
            try:
                skill = await cls.load_skill(str(skill_dir))
                if skill.is_valid:
                    skills[skill.metadata.name] = skill
                    logger.info(f"Loaded skill: {skill.metadata.name} ({skill.metadata.type})")
                else:
                    logger.warning(
                        f"Skill {skill_dir.name} has errors: {skill.errors}"
                    )
            except Exception as e:
                logger.error(f"Failed to load skill from {skill_dir}: {e}")

        return skills

    @classmethod
    async def load_skill(cls, skill_path: str) -> LoadedSkill:
        """
        Load single skill from directory.

        Args:
            skill_path: Path to skill directory containing SKILL.md

        Returns:
            LoadedSkill with metadata and optional handler
        """
        path = Path(skill_path)
        skill_md_path = path / "SKILL.md"
        errors: List[str] = []

        # Check SKILL.md exists
        if not skill_md_path.exists():
            return LoadedSkill(
                metadata=SkillMetadata(name="", description=""),
                errors=[f"SKILL.md not found in {skill_path}"],
                source_path=path,
            )

        # Parse SKILL.md
        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return LoadedSkill(
                metadata=SkillMetadata(name="", description=""),
                errors=[f"Failed to read SKILL.md: {e}"],
                source_path=path,
            )

        # Extract frontmatter
        metadata, parse_errors = cls.parse_skill_md(content)
        errors.extend(parse_errors)

        # Validate required fields
        validation_errors = cls.validate_metadata(metadata, path)
        errors.extend(validation_errors)

        if errors:
            return LoadedSkill(
                metadata=metadata,
                errors=errors,
                source_path=path,
            )

        # Load handler based on type
        handler = None
        if metadata.type == "tool":
            handler, handler_errors = await cls._load_tool_handler(metadata, path)
            errors.extend(handler_errors)
        elif metadata.type == "mcp":
            handler, handler_errors = cls._create_mcp_handler(metadata)
            errors.extend(handler_errors)
        # instruction and composite types don't have handlers

        return LoadedSkill(
            metadata=metadata,
            handler=handler,
            source_path=path,
            errors=errors,
        )

    @classmethod
    def parse_skill_md(cls, content: str) -> tuple[SkillMetadata, List[str]]:
        """
        Parse YAML frontmatter from SKILL.md content.

        Args:
            content: Full SKILL.md file content

        Returns:
            Tuple of (SkillMetadata, list of errors)
        """
        errors: List[str] = []

        # Match frontmatter
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            return (
                SkillMetadata(name="", description=""),
                ["Invalid SKILL.md format: missing frontmatter"],
            )

        frontmatter_str = match.group(1)
        documentation = match.group(2).strip()

        # Parse YAML
        try:
            frontmatter = yaml.safe_load(frontmatter_str)
            if not isinstance(frontmatter, dict):
                return (
                    SkillMetadata(name="", description=""),
                    ["Invalid frontmatter: expected YAML dict"],
                )
        except yaml.YAMLError as e:
            return (
                SkillMetadata(name="", description=""),
                [f"YAML parse error: {e}"],
            )

        # Create metadata
        metadata = SkillMetadata.from_frontmatter(frontmatter, documentation)

        return metadata, errors

    @classmethod
    def validate_metadata(cls, metadata: SkillMetadata, skill_path: Path) -> List[str]:
        """
        Validate skill metadata format.

        Args:
            metadata: Parsed SkillMetadata
            skill_path: Path to skill directory

        Returns:
            List of validation errors
        """
        errors: List[str] = []

        # Required fields
        if not metadata.name:
            errors.append("Missing required field: name")
        if not metadata.description:
            errors.append("Missing required field: description")

        # Name format (lowercase, alphanumeric + hyphens, 1-64 chars)
        if metadata.name:
            if len(metadata.name) > 64:
                errors.append("name must be 64 characters or less")
            if not re.match(r"^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$", metadata.name):
                errors.append(
                    "name must be lowercase alphanumeric with hyphens/underscores, "
                    "not starting or ending with hyphen/underscore"
                )

        # Name should match directory name
        if metadata.name and skill_path.name != metadata.name:
            errors.append(
                f"name '{metadata.name}' doesn't match directory name '{skill_path.name}'"
            )

        # Type validation
        valid_types = {"instruction", "tool", "mcp", "composite"}
        if metadata.type not in valid_types:
            errors.append(f"type must be one of: {valid_types}")

        # Tool type requires tool configuration
        if metadata.type == "tool":
            if not metadata.tool_module or not metadata.tool_function:
                errors.append("tool type requires tool.module and tool.function")

        # MCP type requires mcp configuration
        if metadata.type == "mcp":
            if not metadata.mcp_server:
                errors.append("mcp type requires mcp.server")

        # Description length
        if metadata.description and len(metadata.description) > 1024:
            errors.append("description must be 1024 characters or less")

        return errors

    @classmethod
    async def _load_tool_handler(
        cls, metadata: SkillMetadata, skill_path: Path
    ) -> tuple[Optional[Callable], List[str]]:
        """
        Load Python handler for tool-type skill.

        Args:
            metadata: Skill metadata with tool configuration
            skill_path: Path to skill directory

        Returns:
            Tuple of (handler function, list of errors)
        """
        errors: List[str] = []

        if not metadata.tool_module or not metadata.tool_function:
            return None, ["Missing tool.module or tool.function"]

        try:
            # Import module
            module = importlib.import_module(metadata.tool_module)

            # Get function
            if not hasattr(module, metadata.tool_function):
                errors.append(
                    f"Function '{metadata.tool_function}' not found in {metadata.tool_module}"
                )
                return None, errors

            handler = getattr(module, metadata.tool_function)

            if not callable(handler):
                errors.append(f"'{metadata.tool_function}' is not callable")
                return None, errors

            # Wrap handler to convert RunContext → SkillContext at runtime,
            # matching what the @skill decorator does.  This lets Pydantic AI
            # accept the tool (it rejects SkillContext annotations).
            handler = cls._wrap_skill_handler(handler, metadata)

            return handler, errors

        except ImportError as e:
            errors.append(f"Failed to import {metadata.tool_module}: {e}")
            return None, errors
        except Exception as e:
            errors.append(f"Failed to load handler: {e}")
            return None, errors

    @classmethod
    def _wrap_skill_handler(cls, func: Callable, metadata: "SkillMetadata") -> Callable:
        """
        Wrap a SKILL.md handler so Pydantic AI can use it as a tool.

        The raw handler has `ctx: SkillContext` as first param. Pydantic AI
        requires either no context param or `RunContext[...]`. This wrapper:
        1. Accepts RunContext from Pydantic AI and converts to SkillContext
        2. Strips the SkillContext annotation so Pydantic AI schema gen works
        """
        from app.agents.skills.context import SkillContext

        @wraps(func)
        async def wrapper(ctx, *args, **kwargs):
            # Convert RunContext → SkillContext if needed
            if isinstance(ctx, SkillContext):
                skill_ctx = ctx
            elif hasattr(ctx, 'deps'):
                skill_ctx = SkillContext.from_run_context(ctx)
            else:
                skill_ctx = SkillContext.create_minimal(user_id="unknown")

            if inspect.iscoroutinefunction(func):
                return await func(skill_ctx, *args, **kwargs)
            else:
                return func(skill_ctx, *args, **kwargs)

        # Strip SkillContext annotation so Pydantic AI doesn't reject the tool
        if 'ctx' in getattr(wrapper, '__annotations__', {}):
            wrapper.__annotations__.pop('ctx')

        return wrapper

    @classmethod
    def _create_mcp_handler(
        cls, metadata: SkillMetadata
    ) -> tuple[Optional[Callable], List[str]]:
        """
        Create handler that wraps MCP server tool.

        Args:
            metadata: Skill metadata with MCP configuration

        Returns:
            Tuple of (handler function, list of errors)
        """
        errors: List[str] = []

        if not metadata.mcp_server:
            return None, ["Missing mcp.server"]

        server_name = metadata.mcp_server
        tool_name = metadata.mcp_tool

        async def mcp_handler(ctx, **kwargs):
            """MCP tool wrapper handler"""
            from app.agents.skills.registry import _execute_mcp_tool

            return await _execute_mcp_tool(ctx, server_name, tool_name, **kwargs)

        # Set metadata on handler
        mcp_handler.__skill_name__ = metadata.name
        mcp_handler.__skill_description__ = metadata.description

        return mcp_handler, errors

    @classmethod
    def validate(cls, skill_path: str) -> List[str]:
        """
        Validate skill format without loading.

        Args:
            skill_path: Path to skill directory

        Returns:
            List of validation errors (empty if valid)
        """
        import asyncio

        async def _validate():
            skill = await cls.load_skill(skill_path)
            return skill.errors

        return asyncio.run(_validate())
