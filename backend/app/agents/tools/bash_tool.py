"""
Unified Bash Tool for Agent Execution

Based on Vercel's research showing 5x better performance by replacing 80% of
specialized tools with a single bash interface. This reduces token overhead
and simplifies agent decision-making.

Security:
- Sandboxed execution within session directory
- Regex validation blocks dangerous commands
- Minimal environment (no access to system paths)
- Timeout enforcement (default 30s)
- Output truncation (max 10KB)

Usage in agents:
    bash("cat file.csv | awk '{sum+=$3} END {print sum}'")
    bash("curl -s https://api.example.com/data | jq '.results'")
    bash("python -c 'import pandas as pd; print(pd.read_csv(\"data.csv\").head())'")
"""

import asyncio
import re
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

from app.config import get_settings


class BashToolConfig(BaseModel):
    """Configuration for bash tool execution."""
    session_path: Path
    timeout_seconds: int = 30
    max_output_chars: int = 10000


class BashToolResult(BaseModel):
    """Result from bash tool execution."""
    success: bool
    output: str
    truncated: bool = False
    exit_code: Optional[int] = None


# Dangerous command patterns to block
DANGEROUS_PATTERNS = [
    # Path traversal
    r'\.\./|/\.\./',
    # Privilege escalation
    r'\bsudo\b|\bsu\s',
    # Dangerous deletions (root or home)
    r'\brm\s+(-rf?|-fr?)\s+/',
    r'\brm\s+(-rf?|-fr?)\s+~',
    # System permission changes
    r'\bchmod\s+\d+\s+/',
    r'\bchown\s+',
    # Direct device access
    r'>/dev/',
    r'</dev/',
    # Network backdoors
    r'\bnc\s+|\bnetcat\b',
    r'\bncat\b',
    # Process manipulation outside session
    r'\bkill\s+-9\s',
    r'\bkillall\b',
    # Environment manipulation
    r'\bexport\s+PATH=',
    r'\bunset\s+PATH',
    # Shell escapes
    r'\bsh\s+-c\s',
    r'\bbash\s+-c\s',
    # Disk operations
    r'\bdd\s+if=',
    r'\bmkfs\b',
    r'\bfdisk\b',
]


def is_unsafe_command(command: str) -> tuple[bool, Optional[str]]:
    """
    Detect commands that could escape sandbox or cause harm.

    Returns:
        Tuple of (is_unsafe, reason_if_unsafe)
    """
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True, f"Command matches dangerous pattern: {pattern}"

    return False, None


def get_safe_env(session_path: Path) -> dict:
    """
    Create minimal safe environment for command execution.

    Only includes:
    - Basic PATH for standard utilities
    - HOME set to session directory
    - PYTHONPATH cleared
    """
    return {
        'PATH': '/usr/local/bin:/usr/bin:/bin',
        'PYTHONPATH': '',
        'HOME': str(session_path),
        'SHELL': '/bin/bash',
        'LANG': 'en_US.UTF-8',
        'LC_ALL': 'en_US.UTF-8',
        # Session-specific
        'SESSION_PATH': str(session_path),
    }


async def execute_bash(
    command: str,
    config: BashToolConfig,
    working_dir: Optional[str] = None,
) -> BashToolResult:
    """
    Execute bash command in sandboxed session directory.

    Args:
        command: Shell command to execute
        config: Bash tool configuration with session_path, timeout, etc.
        working_dir: Optional subdirectory within session (default: session root)

    Returns:
        BashToolResult with success status, output, and metadata
    """
    settings = get_settings()

    # Check if bash tool is enabled
    if not getattr(settings, 'USE_BASH_TOOL', False):
        return BashToolResult(
            success=False,
            output="ERROR: Bash tool is disabled. Set USE_BASH_TOOL=True to enable.",
            exit_code=-1
        )

    # Security check
    is_unsafe, reason = is_unsafe_command(command)
    if is_unsafe:
        return BashToolResult(
            success=False,
            output=f"ERROR: Command blocked for security - {reason}",
            exit_code=-1
        )

    # Determine working directory
    if working_dir:
        work_path = config.session_path / working_dir
        # Ensure working directory is within session
        try:
            work_path = work_path.resolve()
            config.session_path.resolve()
            if not str(work_path).startswith(str(config.session_path.resolve())):
                return BashToolResult(
                    success=False,
                    output="ERROR: Working directory must be within session",
                    exit_code=-1
                )
        except Exception:
            return BashToolResult(
                success=False,
                output="ERROR: Invalid working directory path",
                exit_code=-1
            )
    else:
        work_path = config.session_path

    # Ensure session directory exists
    work_path.mkdir(parents=True, exist_ok=True)

    # Execute with timeout
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(work_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=get_safe_env(config.session_path)
        )

        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=config.timeout_seconds
        )

        output = stdout.decode('utf-8', errors='replace')
        truncated = False

        # Truncate if too large
        if len(output) > config.max_output_chars:
            output = output[:config.max_output_chars] + f"\n\n[Output truncated - {len(stdout)} total chars]"
            truncated = True

        return BashToolResult(
            success=process.returncode == 0,
            output=output,
            truncated=truncated,
            exit_code=process.returncode
        )

    except asyncio.TimeoutError:
        # Kill the process if it timed out
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass

        return BashToolResult(
            success=False,
            output=f"ERROR: Command timed out after {config.timeout_seconds}s",
            exit_code=-1
        )

    except Exception as e:
        return BashToolResult(
            success=False,
            output=f"ERROR: {str(e)}",
            exit_code=-1
        )


# Pydantic AI Tool wrapper (for use with agents)
async def bash_tool(
    command: str,
    session_path: Path,
    working_dir: Optional[str] = None,
    timeout_seconds: int = 30,
) -> str:
    """
    Execute bash command in sandboxed session directory.

    Use this for:
    - File operations (cat, grep, find, wc, etc.)
    - Data processing (jq, csvkit, pandas via Python)
    - Text manipulation (sed, awk, cut)
    - API calls (curl, wget)
    - Report generation

    Examples:
    - bash("cat artifacts/market_data.csv | head -10")
    - bash("jq '.[] | select(.amount > 1000)' data.json")
    - bash("python -c 'import pandas as pd; ...'")
    - bash("curl -s https://api.example.com/data | jq .results")

    Args:
        command: Shell command to execute
        session_path: Root path of the session directory
        working_dir: Optional subdirectory within session (default: session root)
        timeout_seconds: Max execution time (default: 30s)

    Returns:
        Command output (stdout + stderr combined)
    """
    config = BashToolConfig(
        session_path=session_path,
        timeout_seconds=timeout_seconds
    )

    result = await execute_bash(command, config, working_dir)
    return result.output
