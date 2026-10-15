"""LLM Pricing Configuration.

Centralized pricing information for different LLM models.
Pricing is per 1M tokens (as of January 2025).

This file is separated from the main config to make it easy to update
pricing without modifying core application logic.
"""

from typing import Dict, List, NotRequired, TypedDict


class PricingTier(TypedDict):
    """Rate card that applies when a request's prompt exceeds a token threshold.

    `above_input_tokens` counts the whole prompt of one request: uncached input plus
    cache-read plus cache-write tokens. A tier without explicit cache prices derives them
    from its own input rate with the global multipliers.
    """
    above_input_tokens: int
    input: float
    output: float
    cache_read: NotRequired[float]
    cache_write: NotRequired[float]


class ModelPricing(TypedDict):
    """Pricing for a single model."""
    input: float  # Cost per 1M input tokens
    output: float  # Cost per 1M output tokens
    cache_read: NotRequired[float]  # Cost per 1M cache-read tokens; default input * CACHE_READ_MULTIPLIER
    cache_write: NotRequired[float]  # Cost per 1M 5-minute cache-write tokens; default input * CACHE_WRITE_MULTIPLIER
    tiers: NotRequired[List[PricingTier]]  # Long-context rate cards, highest matching threshold wins


# Pricing per 1M tokens (as of January 2025)
LLM_PRICING: Dict[str, ModelPricing] = {
    # OpenAI Models
    "gpt-4": {"input": 30.0, "output": 60.0},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/gpt-4o": {"input": 2.5, "output": 10.0},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},

    # Anthropic Models
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-opus": {"input": 15.0, "output": 75.0},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "anthropic/claude-3.5-sonnet": {"input": 3.0, "output": 15.0},
    "anthropic/claude-3-opus": {"input": 15.0, "output": 75.0},
    "anthropic/claude-3-haiku": {"input": 0.25, "output": 1.25},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-opus-4-5": {"input": 5.0, "output": 25.0},
    # Claude 5.x (claude-api skill reference, models.md / model-migration.md / prompt-caching.md,
    # cached 2026-10-06). 5-minute cache writes are 1.25x input; cache reads differ per model.
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.50},
    # Haiku 5.5: $0.10/$0.50 for prompts <=100K tokens, $0.50/$2.50 above. The reference documents
    # cache reads 0.1x and 5-minute writes 1.25x of the applicable input rate (the global multipliers),
    # so no explicit cache prices are set; the tier derives them from its own input rate.
    "claude-haiku-5-5": {
        "input": 0.10,
        "output": 0.50,
        "tiers": [{"above_input_tokens": 100_000, "input": 0.50, "output": 2.50}],
    },
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write": 12.5},

    # DeepSeek Models (via OpenRouter)
    "nex-agi/deepseek-v3.1-nex-n1:free": {"input": 0.0, "output": 0.0},
    "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek/deepseek-coder": {"input": 0.14, "output": 0.28},
}

# Default pricing for unknown models (conservative estimate)
DEFAULT_PRICING: ModelPricing = {"input": 0.15, "output": 0.60}


def get_model_pricing(model: str) -> ModelPricing:
    """
    Get pricing for a model.

    Args:
        model: Model identifier (can be with or without provider prefix)

    Returns:
        ModelPricing dict with input and output costs per 1M tokens
    """
    # Try exact match first
    if model in LLM_PRICING:
        return LLM_PRICING[model]

    # Try without provider prefix (e.g., "openai/gpt-4o" -> "gpt-4o")
    if "/" in model:
        base_model = model.split("/", 1)[1]
        if base_model in LLM_PRICING:
            return LLM_PRICING[base_model]

    # Return default pricing
    return DEFAULT_PRICING


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """
    Calculate the cost for an LLM call.

    Args:
        model: Model identifier
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        Cost in USD
    """
    pricing = get_model_pricing(model)
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


# Cache rates as multiples of the input rate (Anthropic: read 0.1x, 5-minute write 1.25x).
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


def find_model_pricing(model: str) -> ModelPricing | None:
    """Pricing for a model, or None when the model is not in the table (no default fallback)."""
    if model in LLM_PRICING:
        return LLM_PRICING[model]
    if "/" in model:
        return LLM_PRICING.get(model.split("/", 1)[1])
    return None


def calculate_cost_with_cache(
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Cost in USD for ONE request, where input_tokens excludes cache tokens.

    Cache tokens bill at the model's explicit cache_read/cache_write prices when set, else at the
    global multiples of the input rate. When the whole prompt (input + cache read + cache write)
    exceeds a tier's threshold, that tier's rate card replaces the base one.
    """
    rates: ModelPricing | PricingTier = pricing
    prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens
    for tier in sorted(pricing.get("tiers", []), key=lambda t: t["above_input_tokens"]):
        if prompt_tokens > tier["above_input_tokens"]:
            rates = tier
    inp = rates["input"]
    cache_read = rates.get("cache_read", inp * CACHE_READ_MULTIPLIER)
    cache_write = rates.get("cache_write", inp * CACHE_WRITE_MULTIPLIER)
    return (
        input_tokens * inp
        + output_tokens * rates["output"]
        + cache_read_tokens * cache_read
        + cache_write_tokens * cache_write
    ) / 1_000_000
