"""
Skills Registry
Manages available skills that can be attached to agents dynamically
Based on patterns from LangGraph, CrewAI, and Anthropic Agent Skills
"""

from typing import Dict, List, Callable, Optional, Any, Type
from dataclasses import dataclass, field
from functools import wraps
import importlib
from pydantic import BaseModel
from contextlib import AsyncExitStack
# Try importing MCP, gracefully handle if missing (optional for now)
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

@dataclass
class Skill:
    """
    Represents a skill/ability that can be attached to an agent
    """
    name: str
    description: str
    category: str  # 'web', 'code', 'file', 'memory', 'mcp'
    handler: Callable  # The actual tool function
    enabled: bool = True
    requires: List[str] = field(default_factory=list)  # Dependencies
    config_schema: Optional[Type[BaseModel]] = None  # Optional config
    author: str = "system"
    version: str = "1.0.0"
    mcp_dependencies: List[str] = field(default_factory=list)
    mcp_server: Optional[str] = None
    mcp_tool: Optional[str] = None
    retryable: bool = False  # If True, retry with exponential backoff on transient errors

@dataclass
class SkillManifest:
    """
    Metadata for a skill to allow discovery without importing the implementation
    """
    name: str
    description: str
    category: str
    module_path: str
    requires: List[str] = field(default_factory=list)
    author: str = "system"
    version: str = "1.0.0"
    mcp_dependencies: List[str] = field(default_factory=list)
    mcp_server: Optional[str] = None
    mcp_tool: Optional[str] = None
    required_feature: Optional[str] = None  # Feature flag required for this skill (e.g., "ENABLE_FINANCE_SKILLS")
    mock: bool = False  # Returns simulated data (not a real provider)

# Global skills registry (loaded skills)
_registry: Dict[str, Skill] = {}

# Global manifests registry (all available skills)
_manifests: Dict[str, SkillManifest] = {}

# MCP Client Sessions (server_name -> session)
_mcp_sessions: Dict[str, Any] = {} # Typed as Any to avoid checking ClientSession if not available
_exit_stack = AsyncExitStack()

# Mock Configuration for MCP Servers (In real app, load from env or config file)
# Example: "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}
MCP_SERVERS_CONFIG = {} 


# Define built-in skills manifests
# This allows us to know about skills without importing their modules
BUILTIN_SKILLS = [
    # Web Skills
    SkillManifest(
        name="web_search",
        description="Search the web for information using a search query. Requires at least one of: tavily, parallelai, exa, or serpapi API key.",
        category="web",
        module_path="app.agents.skills.web",
        requires=["tavily", "parallelai", "exa", "serpapi"],  # Any one suffices
    ),
    SkillManifest(
        name="browse_url",
        description="Fetch and parse a web page using HTTP GET + BeautifulSoup. Use for static HTML pages. For JavaScript-heavy sites, use browser_navigate instead.",
        category="web",
        module_path="app.agents.skills.web"
    ),
    SkillManifest(
        name="extract_structured_data",
        description="Extract structured data (tables, lists, etc.) from HTML content",
        category="web",
        module_path="app.agents.skills.web"
    ),
    # Browser Skills (gated by ENABLE_BROWSER_SANDBOX)
    SkillManifest(
        name="browser_navigate",
        description="Navigate to a URL using a full Chromium browser inside E2B sandbox. Requires ENABLE_BROWSER_SANDBOX=True. For simple static pages, browse_url is faster and free.",
        category="web",
        module_path="app.skills.browser_navigate.tool"
    ),
    SkillManifest(
        name="browser_screenshot",
        description="Take a screenshot of a URL in the sandbox browser",
        category="web",
        module_path="app.skills.browser_screenshot.tool"
    ),
    # Code Skills
    SkillManifest(
        name="analyze_code",
        description="Analyze Python code for quality, complexity, and potential issues",
        category="code",
        module_path="app.agents.skills.code"
    ),
    SkillManifest(
        name="generate_chart",
        description="Generate a chart/visualization from data",
        category="code",
        module_path="app.agents.skills.code"
    ),
    # File Skills (Workspace)
    SkillManifest(
        name="read_file",
        description="Read text content from the agent's session workspace",
        category="file",
        module_path="app.skills.read_file.tool"
    ),
    SkillManifest(
        name="write_file",
        description="Write text content to the agent's session workspace",
        category="file",
        module_path="app.skills.write_file.tool"
    ),
    SkillManifest(
        name="delete_file",
        description="Delete a file from the agent's session workspace",
        category="file",
        module_path="app.skills.delete_file.tool"
    ),
    SkillManifest(
        name="list_workspace",
        description="List files and directories in the agent's session workspace with metadata",
        category="file",
        module_path="app.skills.list_workspace.tool"
    ),
    SkillManifest(
        name="parse_document",
        description="Parse content from documents (CSV, JSON, etc.)",
        category="file",
        module_path="app.agents.skills.file"
    ),
    # Financial Skills (gated by ENABLE_FINANCE_SKILLS)
    SkillManifest(
        name="fetch_market_data",
        description="Fetch real-time market data (price, volume, change) for a ticker",
        category="financial",
        module_path="app.agents.skills.financial",
        required_feature="ENABLE_FINANCE_SKILLS",
    ),
    SkillManifest(
        name="get_financial_statements",
        description="Get financial statements (income, balance, cash flow) for a company",
        category="financial",
        module_path="app.agents.skills.financial",
        required_feature="ENABLE_FINANCE_SKILLS",
    ),
    SkillManifest(
        name="analyze_risk_metrics",
        description="Calculate risk metrics (VaR, Sharpe, Beta) given a set of returns",
        category="financial",
        module_path="app.agents.skills.financial",
        required_feature="ENABLE_FINANCE_SKILLS",
    ),
    SkillManifest(
        name="fetch_economic_indicators",
        description="Fetch economic indicators like GDP, CPI, Unemployment",
        category="financial",
        module_path="app.agents.skills.financial",
        required_feature="ENABLE_FINANCE_SKILLS",
    ),
    SkillManifest(
        name="get_trading_volume",
        description="Analyze trading volume trends for a ticker",
        category="financial",
        module_path="app.agents.skills.financial",
        required_feature="ENABLE_FINANCE_SKILLS",
    ),
    # Compliance Skills (gated by ENABLE_COMPLIANCE)
    SkillManifest(
        name="kyc_verification",
        description="Verify identity against mock datasets/watchlists",
        category="compliance",
        module_path="app.agents.skills.compliance",
        required_feature="ENABLE_COMPLIANCE",
    ),
    SkillManifest(
        name="aml_screening",
        description="Screen transactions for money laundering patterns",
        category="compliance",
        module_path="app.agents.skills.compliance",
        required_feature="ENABLE_COMPLIANCE",
    ),
    SkillManifest(
        name="sanctions_check",
        description="Check entity against OFAC/EU sanctions lists",
        category="compliance",
        module_path="app.agents.skills.compliance",
        required_feature="ENABLE_COMPLIANCE",
    ),
    SkillManifest(
        name="batch_sanctions_check",
        description="Check multiple entities against OFAC/EU sanctions lists in one call. Preferred over individual sanctions_check for bulk screening.",
        category="compliance",
        module_path="app.agents.skills.compliance",
        mock=True,
        required_feature="ENABLE_COMPLIANCE",
    ),
    SkillManifest(
        name="batch_kyc_verification",
        description="Perform KYC verification for multiple entities in one call. Preferred over individual kyc_verification for bulk checks.",
        category="compliance",
        module_path="app.agents.skills.compliance",
        mock=True,
        required_feature="ENABLE_COMPLIANCE",
    ),
    SkillManifest(
        name="regulatory_filing",
        description="Parse/Retrieve SEC/FINRA filings for an entity",
        category="compliance",
        module_path="app.agents.skills.compliance",
        required_feature="ENABLE_COMPLIANCE",
    ),
    SkillManifest(
        name="risk_assessment",
        description="Calculate composite risk score for an entity",
        category="compliance",
        module_path="app.agents.skills.compliance",
        required_feature="ENABLE_COMPLIANCE",
    ),
    # Document Skills
    SkillManifest(
        name="parse_pdf",
        description="Extract text and metadata from a PDF file",
        category="document",
        module_path="app.agents.skills.document"
    ),
    SkillManifest(
        name="extract_entities",
        description="Extract financial entities (Dates, Amounts, Orgs) from text",
        category="document",
        module_path="app.agents.skills.document"
    ),
    SkillManifest(
        name="summarize_document",
        description="Summarize a text document",
        category="document",
        module_path="app.agents.skills.document"
    ),
    # Memory Skills - Enhanced Memory System
    SkillManifest(
        name="store_decision",
        description="Store a decision to episodic memory for future recall",
        category="memory",
        module_path="app.agents.skills.memory"
    ),
    SkillManifest(
        name="search_past_decisions",
        description="Search for similar past decisions using hybrid search",
        category="memory",
        module_path="app.agents.skills.memory"
    ),
    SkillManifest(
        name="search_entities",
        description="Search for entities (companies, people, regulations) across workflows",
        category="memory",
        module_path="app.agents.skills.memory"
    ),
    SkillManifest(
        name="get_workflow_entities",
        description="Get entities extracted from a specific workflow",
        category="memory",
        module_path="app.agents.skills.memory"
    ),
    SkillManifest(
        name="search_knowledge",
        description="Search the knowledge base using RAG (pgvector)",
        category="memory",
        module_path="app.agents.skills.memory"
    ),
    # Planning Skills (Deep Work / Autonomous Execution)
    SkillManifest(
        name="present_plan",
        description="Present a work plan to the user for approval before starting autonomous execution",
        category="planning",
        module_path="app.skills.present_plan.tool"
    ),
    SkillManifest(
        name="update_todo",
        description="Update the todo list during autonomous execution (mark items done, add new items)",
        category="planning",
        module_path="app.skills.update_todo.tool"
    ),
    SkillManifest(
        name="ask_user",
        description="Pause execution to ask the user a clarifying question. Provide options for structured multiple-choice when possible.",
        category="planning",
        module_path="app.skills.ask_user.tool"
    ),
    SkillManifest(
        name="complete_task",
        description="Signal that the current task is complete with a summary of accomplishments",
        category="planning",
        module_path="app.skills.complete_task.tool"
    ),
    # Task Management Skills (replaces update_todo)
    SkillManifest(
        name="create_task",
        description="Create a new task to track work.",
        category="planning",
        module_path="app.skills.task_manager.tool"
    ),
    SkillManifest(
        name="update_task",
        description="Update a task's status.",
        category="planning",
        module_path="app.skills.task_manager.tool"
    ),
    SkillManifest(
        name="list_tasks",
        description="List all tasks in the current session.",
        category="planning",
        module_path="app.skills.task_manager.tool"
    ),
    # Connector Skills - Generic tools for token-efficient connector access
    # Instead of expanding N tool definitions per connector into the LLM context,
    # agents use these 3 generic skills to discover and invoke connectors on demand.
    SkillManifest(
        name="list_available_connectors",
        description="List all connectors configured by the user and their available tools",
        category="connector",
        module_path="app.agents.skills.connectors"
    ),
    SkillManifest(
        name="invoke_connector_tool",
        description="Execute a tool from a configured connector (e.g., create Linear issue, search ServiceNow)",
        category="connector",
        module_path="app.agents.skills.connectors"
    ),
    SkillManifest(
        name="get_connector_schema",
        description="Get the input schema for a specific connector tool before invoking it",
        category="connector",
        module_path="app.agents.skills.connectors"
    ),
]

# Initialize manifests
for manifest in BUILTIN_SKILLS:
    _manifests[manifest.name] = manifest


def skill(
    name: str,
    description: str,
    category: str = "general",
    requires: Optional[List[str]] = None,
    config_schema: Optional[Type[BaseModel]] = None,
    author: str = "system",
    version: str = "1.0.0",
    mcp_dependencies: Optional[List[str]] = None,
    retryable: bool = False,
):
    """
    Decorator to register a function as an agent skill

    The decorated function should accept SkillContext as its first argument.
    When called from Pydantic AI (agent execution), the wrapper will automatically
    convert RunContext[AgentDependencies] to SkillContext.

    Args:
        retryable: If True, wraps execution with tenacity retry (3 attempts,
            exponential backoff 1-4s). Only for idempotent read-only operations
            like web_search, browse_url, fetch_market_data. Never use for
            write operations (send_email, execute_python, write_file).

    Usage:
        @skill("web_search", "Search the web", category="web", retryable=True)
        async def web_search(ctx: SkillContext, query: str) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        from app.agents.skills.context import SkillContext

        @wraps(func)
        async def wrapper(ctx, *args, **kwargs):
            # Convert RunContext to SkillContext if needed
            if isinstance(ctx, SkillContext):
                skill_ctx = ctx
            elif hasattr(ctx, 'deps'):
                # This is a Pydantic AI RunContext[AgentDependencies]
                skill_ctx = SkillContext.from_run_context(ctx)
            else:
                # Fallback: create minimal context
                skill_ctx = SkillContext.create_minimal(user_id="unknown")

            # Inner execution: observability + actual function call
            async def _execute(attempt: int = 1):
                from app.services.observability import get_observability_service
                obs = get_observability_service()
                async with obs.trace_tool_execution(
                    tool_name=name,
                    tool_category=category,
                    agent_type=getattr(skill_ctx, 'agent_type', 'unknown'),
                    session_id=getattr(skill_ctx, 'session_id', None),
                    tenant_id=str(skill_ctx.tenant_id) if getattr(skill_ctx, 'tenant_id', None) else None,
                    attempt_number=attempt,
                ) as trace:
                    try:
                        import inspect
                        if inspect.iscoroutinefunction(func):
                            result = await func(skill_ctx, *args, **kwargs)
                        else:
                            result = func(skill_ctx, *args, **kwargs)
                        trace.set_success(output_size=len(str(result)) if result is not None else 0)
                        return result
                    except Exception as e:
                        trace.set_error(str(e))
                        raise

            if retryable:
                from tenacity import (
                    AsyncRetrying, stop_after_attempt, wait_exponential,
                    retry_if_exception_type,
                )
                attempt = 0
                async for attempt_obj in AsyncRetrying(
                    stop=stop_after_attempt(3),
                    wait=wait_exponential(multiplier=1, min=1, max=4),
                    retry=retry_if_exception_type((
                        ConnectionError, TimeoutError, OSError,
                    )),
                    reraise=True,
                ):
                    with attempt_obj:
                        attempt += 1
                        return await _execute(attempt=attempt)
            else:
                return await _execute()

        # Remove the ctx type annotation so Pydantic AI doesn't reject it.
        # @wraps copies __annotations__ from the original function, which may
        # annotate ctx as SkillContext.  Pydantic AI requires either bare ctx
        # or RunContext[...].  We convert RunContext → SkillContext at runtime
        # inside the wrapper, so strip the annotation here.
        if 'ctx' in getattr(wrapper, '__annotations__', {}):
            wrapper.__annotations__.pop('ctx')

        # Register the skill with the wrapper as handler
        _registry[name] = Skill(
            name=name,
            description=description,
            category=category,
            handler=wrapper,  # Use wrapper instead of raw func
            requires=requires or [],
            config_schema=config_schema,
            author=author,
            version=version,
            mcp_dependencies=mcp_dependencies or [],
            retryable=retryable,
        )

        # Attach metadata for Pydantic AI
        wrapper.__skill_name__ = name
        wrapper.__skill_description__ = description
        return wrapper

    return decorator


async def _execute_mcp_tool(ctx: Any, server_name: str, tool_name: str, **kwargs) -> Any:
    """Helper to execute an MCP tool"""
    if not MCP_AVAILABLE:
        raise ImportError("MCP library not installed.")

    session = _mcp_sessions.get(server_name)
    if not session:
        raise RuntimeError(f"MCP Server '{server_name}' not connected. Call initialize_mcp_clients() first.")

    from app.services.observability import get_observability_service
    obs = get_observability_service()
    async with obs.trace_tool_execution(
        tool_name=tool_name,
        tool_category="mcp",
        agent_type=f"mcp_{server_name}",
    ) as trace:
        try:
            result = await session.call_tool(tool_name, arguments=kwargs)
            trace.set_success(output_size=len(str(result)) if result is not None else 0)
            return result
        except Exception as e:
            trace.set_error(str(e))
            raise


class SkillRegistry:
    """
    Manages skills for an agent
    Provides methods to enable/disable and query available skills

    Supports loading from both:
    - Legacy @skill decorator format
    - New SKILL.md format (agentskills.io specification)
    """

    _initialized: bool = False
    _skill_md_skills: Dict[str, Skill] = {}  # Skills loaded from SKILL.md

    def __init__(self):
        self._enabled_skills: Dict[str, Skill] = {}

    @classmethod
    async def initialize(cls, skills_directory: str = "app/skills/") -> None:
        """
        Initialize the registry by loading skills from both sources.

        Should be called at application startup.

        Args:
            skills_directory: Path to SKILL.md skills directory
        """
        if cls._initialized:
            return

        # Load SKILL.md format skills
        try:
            from app.agents.skills.loader import SkillLoader
            loaded_skills = await SkillLoader.load_directory(skills_directory)

            for name, loaded_skill in loaded_skills.items():
                if loaded_skill.is_valid:
                    # Convert to Skill dataclass
                    skill = Skill(
                        name=loaded_skill.metadata.name,
                        description=loaded_skill.metadata.description,
                        category=loaded_skill.metadata.category,
                        handler=loaded_skill.handler,
                        requires=loaded_skill.metadata.requires,
                        author=loaded_skill.metadata.author,
                        version=loaded_skill.metadata.version,
                        mcp_server=loaded_skill.metadata.mcp_server,
                        mcp_tool=loaded_skill.metadata.mcp_tool,
                    )
                    skill_is_mock = loaded_skill.metadata.mock

                    # Check for name collision with legacy skills
                    if name in _registry:
                        # SKILL.md format takes precedence
                        import logging
                        logging.warning(
                            f"Skill '{name}' exists in both legacy and SKILL.md format. "
                            f"Using SKILL.md version."
                        )

                    cls._skill_md_skills[name] = skill
                    # Also add to global registry for get_skill()
                    _registry[name] = skill

                    # Add to manifests
                    _manifests[name] = SkillManifest(
                        name=loaded_skill.metadata.name,
                        description=loaded_skill.metadata.description,
                        category=loaded_skill.metadata.category,
                        module_path=loaded_skill.metadata.tool_module or "skill_md",
                        requires=loaded_skill.metadata.requires,
                        author=loaded_skill.metadata.author,
                        version=loaded_skill.metadata.version,
                        mcp_server=loaded_skill.metadata.mcp_server,
                        mcp_tool=loaded_skill.metadata.mcp_tool,
                        mock=skill_is_mock,
                    )

        except Exception as e:
            import logging
            logging.warning(f"Failed to load SKILL.md skills: {e}")

        cls._initialized = True

    @classmethod
    def load_skill(cls, name: str) -> bool:
        """
        Dynamically load a skill by name if not already loaded.
        Returns True if skill is available (loaded or already present),
        False if not found.
        """
        if name in _registry:
            return True
        
        if name in _manifests:
            manifest = _manifests[name]
            try:
                # Import the module - this triggers the @skill decorator
                importlib.import_module(manifest.module_path)
                
                # Check if registration happened
                if name in _registry:
                    return True
                else:
                    # Warning: Manifest exists but module didn't register the skill?
                    print(f"Warning: Module {manifest.module_path} imported but skill {name} not registered. Check spellings.")
                    return False
            except ImportError as e:
                print(f"Error loading skill {name}: {e}")
                return False
                
        return False

    @classmethod
    def get_all_skills(cls) -> Dict[str, SkillManifest]:
        """
        Get all known skills (manifests)
        Returns a dictionary of SkillManifests to be lightweight.
        """
        return _manifests.copy()

    @classmethod
    def get_skills_for_tenant(cls, tenant) -> Dict[str, SkillManifest]:
        """Get skills filtered by tenant feature flags.

        Skills with a required_feature that is disabled for the tenant are excluded.
        Skills with no required_feature are always included.
        """
        from app.services.feature_flags import get_feature

        result = {}
        for name, manifest in _manifests.items():
            if manifest.required_feature:
                if not get_feature(tenant, manifest.required_feature):
                    continue
            result[name] = manifest
        return result
    
    @classmethod
    def get_skill(cls, name: str) -> Optional[Skill]:
        """
        Get a skill implementation by name, loading it if necessary.
        """
        if cls.load_skill(name):
            return _registry.get(name)
        return None
    
    @classmethod
    def get_skills_by_category(cls, category: str) -> List[SkillManifest]:
        """Get all skill manifests in a category"""
        return [s for s in _manifests.values() if s.category == category]
    
    @classmethod
    def list_skills(cls) -> List[Dict[str, Any]]:
        """List all skills as dictionaries for API response (using manifests)"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "category": s.category,
                "requires": s.requires,
                "enabled": True, # Available to be enabled
            }
            for s in _manifests.values()
        ]
    
    def enable_skill(self, name: str) -> bool:
        """Enable a skill for this registry instance, loading it if needed"""
        if self.load_skill(name):
            if name in _registry:
                self._enabled_skills[name] = _registry[name]
                return True
        return False
    
    def disable_skill(self, name: str) -> bool:
        """Disable a skill for this registry instance"""
        if name in self._enabled_skills:
            del self._enabled_skills[name]
            return True
        return False
    
    def enable_skills(self, names: List[str]) -> None:
        """Enable multiple skills"""
        for name in names:
            self.enable_skill(name)
    
    def get_enabled_skills(self) -> Dict[str, Skill]:
        """Get all enabled skills for this instance"""
        return self._enabled_skills.copy()
    
    def get_enabled_skill_names(self) -> List[str]:
        """Get names of enabled skills"""
        return list(self._enabled_skills.keys())
    
    def get_tool_functions(self) -> List[Callable]:
        """Get handler functions for all enabled skills (for Pydantic AI)"""
        return [s.handler for s in self._enabled_skills.values()]

    @classmethod
    def get_tools_as_openai_functions(cls, skill_names: list[str]) -> list[dict]:
        """Convert skills to OpenAI function-calling format for LLMGateway.

        Returns a list of tool definitions compatible with the OpenAI API's
        tools parameter: {"type": "function", "function": {...}}.
        """
        import inspect
        tools = []
        for name in skill_names:
            skill = cls.get_skill(name)
            if not skill:
                continue

            # Build parameters from function signature (skip 'ctx')
            sig = inspect.signature(skill.handler)
            properties: dict = {}
            required: list[str] = []
            for param_name, param in sig.parameters.items():
                if param_name in ("ctx", "self", "args", "kwargs"):
                    continue
                prop: dict[str, Any] = {"type": "string"}
                if param.annotation != inspect.Parameter.empty:
                    ann = param.annotation
                    if ann in (int, float):
                        prop["type"] = "number"
                    elif ann is bool:
                        prop["type"] = "boolean"
                    elif ann is list or (hasattr(ann, "__origin__") and ann.__origin__ is list):
                        prop["type"] = "array"
                        prop["items"] = {"type": "string"}
                    elif ann is dict:
                        prop["type"] = "object"
                properties[param_name] = prop
                if param.default is inspect.Parameter.empty:
                    required.append(param_name)

            tools.append({
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return tools

    @classmethod
    async def initialize_mcp_clients(cls) -> None:
        """
        Connect to configured MCP servers and register their tools as skills.
        Should be called at application startup.
        """
        if not MCP_AVAILABLE:
            print("MCP library not found. Skipping MCP initialization.")
            return

        global _mcp_sessions

        for server_name, config in MCP_SERVERS_CONFIG.items():
            try:
                command = config.get("command")
                args = config.get("args", [])
                env = config.get("env")

                server_params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env
                )

                # Connect
                # We use the global exit stack to keep connections open
                stdio_ctx = stdio_client(server_params)
                read, write = await _exit_stack.enter_async_context(stdio_ctx)
                
                session = ClientSession(read, write)
                await _exit_stack.enter_async_context(session)
                
                await session.initialize()
                
                # Store session
                _mcp_sessions[server_name] = session
                
                # List tools and register as manifests
                tools_response = await session.list_tools()
                # tools_response is ListToolsResult, we need tools attribute? 
                # Depends on MCP version. Assuming tools_response.tools is a list of Tool objects
                
                # Check structure of response (it might be pydantic model)
                tools = getattr(tools_response, 'tools', [])
                
                for tool in tools:
                    # Create unique skill name: mcp__<server>__<tool> or just <tool> if unique?
                    # Let's use mcp_<server>_<tool> for safety
                    skill_name = f"mcp_{server_name}_{tool.name}"
                    
                    manifest = SkillManifest(
                        name=skill_name,
                        description=tool.description or f"MCP Tool: {tool.name}",
                        category="mcp",
                        module_path="mcp_dynamic", # Placeholder
                        mcp_server=server_name,
                        mcp_tool=tool.name,
                        author=server_name,
                        version="1.0.0"
                    )
                    
                    _manifests[skill_name] = manifest
                    # Pre-register the skill implementation since it doesn't have a real module
                    cls._register_mcp_skill(manifest)
                    
                print(f"Initialized MCP server '{server_name}' with {len(tools)} tools.")

            except Exception as e:
                print(f"Failed to initialize MCP server '{server_name}': {e}")

    @classmethod
    def _register_mcp_skill(cls, manifest: SkillManifest) -> None:
        """Register the actual Skill object for an MCP tool"""
        
        async def handler(ctx, **kwargs):
            return await _execute_mcp_tool(ctx, manifest.mcp_server, manifest.mcp_tool, **kwargs)
        
        # Set metadata for Pydantic AI
        handler.__skill_name__ = manifest.name
        handler.__skill_description__ = manifest.description
        
        # Need to dynamically generate function signature (inspect.Signature) based on tool.inputSchema?
        # For now, we use **kwargs and let Pydantic AI/LLM figure it out? 
        # Ideally we should construct a signature so Pydantic AI can introspect it.
        # But for MVP, we rely on Pydantic AI's ability to handle general callables if schema is provided?
        # Currently Skill class has 'config_schema'. We might need 'parameters_schema' for tools?
        # Base Skill class definition (above) implies `handler` is the tool function. 
        # Pydantic AI inspects definition. 
        # If we use **kwargs, we miss validation info.
        # Attempt to attach schema if possible.
        
        skill = Skill(
            name=manifest.name,
            description=manifest.description,
            category="mcp",
            handler=handler,
            requires=[],
            author=manifest.author,
            version=manifest.version,
            mcp_server=manifest.mcp_server,
            mcp_tool=manifest.mcp_tool
        )
        
        _registry[manifest.name] = skill

    @classmethod
    async def shutdown_mcp_clients(cls) -> None:
        """Close all MCP connections"""
        await _exit_stack.aclose()
        _mcp_sessions.clear()


    @classmethod
    async def invoke_skill(cls, ctx: Any, skill_name: str, **kwargs) -> Any:
        """
        Invoke another skill from within a skill

        Args:
            ctx: The SkillContext or RunContext from the calling skill
            skill_name: Name of the skill to invoke
            **kwargs: Arguments for the skill

        Returns:
            Result of the skill execution

        Raises:
            ValueError: If skill not found, dependency missing, or cycle detected
        """
        from app.agents.skills.context import SkillContext

        # 1. Load skill
        skill = cls.get_skill(skill_name)
        if not skill:
            raise ValueError(f"Skill '{skill_name}' not found")

        # 2. Get or create SkillContext
        # ctx can be SkillContext directly or RunContext[AgentDependencies]
        if isinstance(ctx, SkillContext):
            skill_ctx = ctx
        elif hasattr(ctx, 'deps'):
            # Legacy RunContext - convert to SkillContext
            skill_ctx = SkillContext.from_run_context(ctx)
        else:
            raise ValueError("Context must be SkillContext or RunContext with deps")

        # 3. Cycle and Depth Detection
        if skill_ctx.is_in_stack(skill_name):
            raise ValueError(f"Circular dependency detected: {' -> '.join(skill_ctx.skill_call_stack)} -> {skill_name}")

        MAX_DEPTH = 5
        if skill_ctx.stack_depth >= MAX_DEPTH:
            raise ValueError(f"Max skill recursion depth ({MAX_DEPTH}) exceeded")

        # 4. Dependency Validation
        if skill_ctx.skill_call_stack:
            caller_name = skill_ctx.skill_call_stack[-1]
            caller_skill = cls.get_skill(caller_name)
            if caller_skill and skill_name not in caller_skill.requires:
                # Log warning in real app using correct logger
                pass

        # 5. Execute
        try:
            # Push to stack
            skill_ctx.push_skill(skill_name)

            from app.services.observability import get_observability_service
            obs = get_observability_service()
            async with obs.trace_tool_execution(
                tool_name=skill_name,
                tool_category=skill.category,
                agent_type=getattr(skill_ctx, 'agent_type', 'unknown'),
                session_id=getattr(skill_ctx, 'session_id', None),
                tenant_id=str(skill_ctx.tenant_id) if getattr(skill_ctx, 'tenant_id', None) else None,
            ) as trace:
                import inspect
                if inspect.iscoroutinefunction(skill.handler):
                    result = await skill.handler(skill_ctx, **kwargs)
                else:
                    result = skill.handler(skill_ctx, **kwargs)
                trace.set_success(output_size=len(str(result)) if result is not None else 0)
                return result
        finally:
            # Pop from stack
            skill_ctx.pop_skill()


# Default skill sets per agent type
# Phase 3: Added memory skills (store_decision, search_past_decisions, search_entities)
DEFAULT_SKILLS: Dict[str, List[str]] = {
    "data-retrieval": ["web_search", "browse_url", "extract_structured_data", "search_entities", "read_file", "write_file", "list_workspace", "delete_file", "list_available_connectors", "invoke_connector_tool", "get_connector_schema", "create_task", "update_task", "list_tasks"],
    "analysis": ["generate_chart", "analyze_code", "store_decision", "search_past_decisions", "search_entities", "read_file", "write_file", "list_workspace", "delete_file", "list_available_connectors", "invoke_connector_tool", "get_connector_schema", "create_task", "update_task", "list_tasks"],
    "research": ["search_knowledge", "read_file", "write_file", "list_workspace", "delete_file", "parse_document", "web_search", "store_decision", "search_past_decisions", "search_entities", "list_available_connectors", "invoke_connector_tool", "get_connector_schema", "create_task", "update_task", "list_tasks"],
    "compliance": ["read_file", "write_file", "list_workspace", "parse_document", "search_knowledge", "store_decision", "search_past_decisions", "search_entities", "list_available_connectors", "invoke_connector_tool", "get_connector_schema", "batch_sanctions_check", "batch_kyc_verification", "create_task", "update_task", "list_tasks"],
    "kyc-aml": ["read_file", "write_file", "list_workspace", "parse_document", "search_knowledge", "store_decision", "search_past_decisions", "search_entities", "list_available_connectors", "invoke_connector_tool", "get_connector_schema", "batch_sanctions_check", "batch_kyc_verification", "create_task", "update_task", "list_tasks"],
    "market-abuse": ["search_knowledge", "analyze_code", "store_decision", "search_past_decisions", "search_entities", "create_task", "update_task", "list_tasks"],
    "report-generator": ["generate_chart", "read_file", "write_file", "list_workspace", "search_knowledge", "search_entities", "get_workflow_entities", "create_task", "update_task", "list_tasks"],
    "general": ["web_search", "browse_url", "read_file", "write_file", "search_knowledge", "list_workspace", "store_decision", "search_past_decisions", "search_entities", "create_task", "update_task", "list_tasks"],
}


def get_default_skills(agent_id: str, tenant=None) -> List[str]:
    """Get default skills for an agent type, optionally filtered by tenant features.

    If tenant is provided, skills whose required_feature is disabled are excluded.
    """
    skills = DEFAULT_SKILLS.get(agent_id, [])
    if tenant is None:
        return skills

    from app.services.feature_flags import get_feature
    filtered = []
    for skill_name in skills:
        manifest = _manifests.get(skill_name)
        if manifest and manifest.required_feature:
            if not get_feature(tenant, manifest.required_feature):
                continue
        filtered.append(skill_name)
    return filtered
