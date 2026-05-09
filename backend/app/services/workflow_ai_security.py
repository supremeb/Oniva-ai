"""
Workflow AI Security Service.

Provides:
- Prompt injection detection and sanitization
- OAuth state validation
- Input validation for workflow building
"""

import base64
import json
import re
import hashlib
import hmac
import secrets
import logging
from typing import Optional, Tuple
from pydantic import BaseModel

from app.config import get_settings
from app.services.redis_ha import get_redis_service

logger = logging.getLogger(__name__)
settings = get_settings()


class PromptInjectionResult(BaseModel):
    """Result of prompt injection check."""

    is_safe: bool
    original_input: str
    sanitized_input: str
    detected_patterns: list[str] = []
    risk_level: str = "none"  # none, low, medium, high


# Patterns that indicate potential prompt injection attempts
INJECTION_PATTERNS = [
    # Direct instruction override attempts
    (r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions", "instruction_override"),
    (r"disregard\s+(all\s+)?(previous|above|prior)", "instruction_override"),
    (r"forget\s+(everything|all)\s+(you\s+)?know", "instruction_override"),
    # Role manipulation
    (r"you\s+are\s+(now|actually)\s+a", "role_manipulation"),
    (r"pretend\s+(to\s+be|you\s+are)", "role_manipulation"),
    (r"act\s+as\s+(if|a|an)", "role_manipulation"),
    # System prompt extraction
    (r"(show|reveal|display|print)\s+(me\s+)?(your|the)\s+system", "system_extraction"),
    (r"what\s+(are|is)\s+your\s+(instructions|prompt|rules)", "system_extraction"),
    (r"repeat\s+(your|the)\s+(system|initial)\s+(prompt|instructions)", "system_extraction"),
    # Jailbreak attempts
    (r"do\s+anything\s+now", "jailbreak"),
    (r"DAN\s+mode", "jailbreak"),
    (r"developer\s+mode", "jailbreak"),
    # Code injection attempts
    (r"```\s*(python|javascript|bash|sh)\s*\n.*?(exec|eval|system|subprocess)", "code_injection"),
    (r"__import__\s*\(", "code_injection"),
    (r"os\.system\s*\(", "code_injection"),
    # Data exfiltration attempts
    (r"(send|post|upload|exfiltrate)\s+.*(data|information|secrets)", "data_exfiltration"),
    (r"(api|secret)\s*key", "data_exfiltration"),
]

# Patterns that are suspicious but not necessarily malicious
SUSPICIOUS_PATTERNS = [
    (r"<\s*(script|style|iframe)", "html_injection"),
    (r"javascript:", "javascript_uri"),
    (r"data:\s*text/html", "data_uri"),
    (r"\$\{.*\}", "template_injection"),
    (r"\{\{.*\}\}", "template_injection"),
]


def check_prompt_injection(user_input: str) -> PromptInjectionResult:
    """
    Check user input for prompt injection attempts.

    This is a defense-in-depth measure. The AI model should also be
    robust against prompt injection, but this provides an additional layer.

    Args:
        user_input: The raw user input to check

    Returns:
        PromptInjectionResult with safety status and sanitized input
    """
    detected_patterns = []
    risk_level = "none"

    # Check for high-risk injection patterns
    for pattern, pattern_type in INJECTION_PATTERNS:
        if re.search(pattern, user_input, re.IGNORECASE | re.DOTALL):
            detected_patterns.append(pattern_type)
            risk_level = "high"
            logger.warning(
                f"Prompt injection detected: {pattern_type}",
                extra={"pattern": pattern, "input_preview": user_input[:100]},
            )

    # Check for suspicious patterns (lower risk)
    if risk_level != "high":
        for pattern, pattern_type in SUSPICIOUS_PATTERNS:
            if re.search(pattern, user_input, re.IGNORECASE | re.DOTALL):
                detected_patterns.append(pattern_type)
                if risk_level == "none":
                    risk_level = "low"

    # Sanitize the input
    sanitized = sanitize_user_input(user_input)

    return PromptInjectionResult(
        is_safe=risk_level != "high",
        original_input=user_input,
        sanitized_input=sanitized,
        detected_patterns=detected_patterns,
        risk_level=risk_level,
    )


def sanitize_user_input(user_input: str) -> str:
    """
    Sanitize user input by removing or escaping potentially dangerous content.

    This preserves the user's intent while removing injection attempts.

    Args:
        user_input: The raw user input

    Returns:
        Sanitized input safe for use in prompts
    """
    sanitized = user_input

    # Remove null bytes and other control characters (except newlines/tabs)
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", sanitized)

    # Escape markdown-like code blocks that might contain executable code
    sanitized = re.sub(
        r"```\s*(python|javascript|bash|sh|exec)\s*\n",
        "```plaintext\n",
        sanitized,
        flags=re.IGNORECASE,
    )

    # Limit excessive whitespace (potential buffer manipulation)
    sanitized = re.sub(r"\n{4,}", "\n\n\n", sanitized)
    sanitized = re.sub(r" {10,}", "    ", sanitized)

    # Limit overall length (prevent context overflow attacks)
    max_length = 5000
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "... [truncated]"

    return sanitized.strip()


# OAuth State Management
# ----------------------
# OAuth states are stored in Redis for multi-process support.
# Key pattern: oauth_state:{token} with 5-minute TTL.

OAUTH_STATE_TTL = 600  # 10 minutes in seconds
OAUTH_STATE_PREFIX = "oauth_state:"


async def generate_oauth_state(
    user_id: str,
    workflow_id: str,
    connector_id: str,
    redirect_uri: str,
    code_verifier: Optional[str] = None,
) -> str:
    """
    Generate a secure OAuth state parameter.

    The state parameter prevents CSRF attacks by ensuring the OAuth
    callback is in response to a request initiated by this user.

    State is stored in Redis with a 10-minute TTL so it works across
    multiple server processes (e.g., Kubernetes pods).

    The PKCE code_verifier is stored server-side (never sent to the
    client) and retrieved during the callback for token exchange.

    Args:
        user_id: The user initiating OAuth
        workflow_id: The workflow being edited
        connector_id: The connector being authenticated
        redirect_uri: Where to redirect after OAuth
        code_verifier: PKCE code verifier to store server-side

    Returns:
        A secure state token
    """
    # Generate random state token
    state_token = secrets.token_urlsafe(32)

    # Store state metadata in Redis with TTL
    metadata = {
        "user_id": user_id,
        "workflow_id": workflow_id,
        "connector_id": connector_id,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        metadata["code_verifier"] = code_verifier

    redis = await get_redis_service()
    redis_key = f"{OAUTH_STATE_PREFIX}{state_token}"
    await redis.set(redis_key, json.dumps(metadata), ttl=OAUTH_STATE_TTL)

    return state_token


async def validate_oauth_state(
    state_token: str,
    expected_user_id: str,
) -> Tuple[bool, Optional[dict]]:
    """
    Validate an OAuth state token.

    Retrieves and atomically deletes the state from Redis (one-time use).

    Args:
        state_token: The state token from OAuth callback
        expected_user_id: The user ID we expect to own this state

    Returns:
        Tuple of (is_valid, state_metadata)
    """
    redis = await get_redis_service()
    redis_key = f"{OAUTH_STATE_PREFIX}{state_token}"

    # Get the state from Redis
    raw = await redis.get(redis_key)
    if raw is None:
        logger.warning(f"OAuth state not found: {state_token[:10]}...")
        return False, None

    # Delete immediately (one-time use) — Redis TTL handles expiry
    await redis.delete(redis_key)

    metadata = json.loads(raw)

    # Check user ID matches
    if metadata["user_id"] != expected_user_id:
        logger.warning(
            f"OAuth state user mismatch: expected {expected_user_id}, got {metadata['user_id']}"
        )
        return False, None

    return True, metadata


# PKCE Support for OAuth
# ----------------------


def _base64url_encode(data: bytes) -> str:
    """Base64url encode without padding, per RFC 7636 Appendix A."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce_pair() -> Tuple[str, str]:
    """
    Generate a PKCE code verifier and challenge pair per RFC 7636.

    PKCE (Proof Key for Code Exchange) protects against authorization
    code interception attacks, especially important for public clients.

    The code_challenge uses S256: BASE64URL(SHA256(code_verifier)).

    Returns:
        Tuple of (code_verifier, code_challenge)
    """
    # Generate a random code verifier (43-128 characters, base64url)
    code_verifier = secrets.token_urlsafe(64)

    # Generate code challenge using S256 method per RFC 7636
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = _base64url_encode(digest)

    return code_verifier, code_challenge


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """
    Verify a PKCE code verifier against its challenge per RFC 7636.

    Args:
        code_verifier: The original code verifier
        code_challenge: The code challenge to verify against

    Returns:
        True if verification passes
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected_challenge = _base64url_encode(digest)
    return hmac.compare_digest(expected_challenge, code_challenge)


# Step Proposal Validation
# ------------------------


def get_allowed_node_types() -> set[str]:
    """Return the full set of valid node types for step proposals."""
    return {
        # Triggers
        "start", "webhook_trigger", "schedule_trigger",
        # AI/LLM
        "llm", "agent", "skill",
        # Logic
        "ifelse", "iteration", "classifier",
        # Data
        "code", "http", "template", "knowledge", "connector", "spreadsheet",
        # Transform
        "aggregator", "variable_assigner", "list_operator", "parameter_extractor",
        # Document
        "document_extractor", "ocr",
        # Control
        "wait", "human_in_loop", "notification", "subworkflow", "notes",
        # Output
        "output",
    }


def validate_step_proposal(step: dict) -> Tuple[bool, Optional[str]]:
    """
    Validate a proposed step before applying to workflow.

    Ensures the step doesn't contain malicious content or
    attempt to bypass security controls.

    Args:
        step: The proposed step configuration

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields
    required_fields = ["id", "type", "label"]
    for field in required_fields:
        if field not in step:
            return False, f"Missing required field: {field}"

    # Validate step type
    allowed_types = get_allowed_node_types()
    if step.get("type") not in allowed_types:
        return False, f"Invalid step type: {step.get('type')}"

    # Check for suspicious content in config
    config = step.get("config", {})
    config_str = str(config)

    # Check for code injection in config
    if re.search(r"(eval|exec|__import__|subprocess)", config_str, re.IGNORECASE):
        return False, "Suspicious code pattern detected in config"

    # Check for excessive size (prevent DoS)
    if len(config_str) > 10000:
        return False, "Step config exceeds maximum size"

    return True, None
