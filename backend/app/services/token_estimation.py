"""Token Estimation and Cost Budget Integration.

Implements:
- L-007: Pre-call token estimation
- L-008: Cost budget check integration into LLM call chain
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


# ===== L-007: Token Estimation =====

# Rough token ratios for estimation (conservative estimates)
TOKEN_RATIOS = {
    "english": 4,        # ~4 chars per token for English
    "chinese": 1.5,      # ~1.5 chars per token for Chinese
    "mixed": 2.5,        # ~2.5 chars per token for mixed content
    "code": 3.5,         # ~3.5 chars per token for code
}

# Model-specific token limits
MODEL_TOKEN_LIMITS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-3.5-turbo": 16385,
    "claude-3-5-sonnet": 200000,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "gemini-1.5-pro": 1048576,
    "gemini-1.5-flash": 1048576,
    "deepseek-chat": 128000,
    "deepseek-coder": 128000,
}

# Default token limit for unknown models
DEFAULT_TOKEN_LIMIT = 8192


@dataclass
class TokenEstimate:
    """Estimated token count for a message list."""
    estimated_prompt_tokens: int
    model_token_limit: int
    available_for_completion: int
    exceeds_limit: bool
    estimated_cost_usd: float = 0.0


def estimate_text_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses a heuristic based on character count and content type:
    - Chinese text: ~1.5 chars/token
    - English text: ~4 chars/token
    - Mixed/code: ~2.5-3.5 chars/token

    This is a rough estimate; actual tokenization depends on the model.
    """
    if not text:
        return 0

    # Count Chinese characters
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    total_chars = len(text)

    if total_chars == 0:
        return 0

    # Determine content type ratio
    chinese_ratio = chinese_chars / total_chars
    if chinese_ratio > 0.5:
        ratio = TOKEN_RATIOS["chinese"]
    elif chinese_ratio > 0.1:
        ratio = TOKEN_RATIOS["mixed"]
    else:
        # Check for code-like content
        code_indicators = sum(1 for c in text if c in '{}[]()=;<>')
        if code_indicators / max(total_chars, 1) > 0.1:
            ratio = TOKEN_RATIOS["code"]
        else:
            ratio = TOKEN_RATIOS["english"]

    return max(1, int(total_chars / ratio))


def estimate_message_tokens(role: str, content: str) -> int:
    """Estimate tokens for a single message, including role overhead.

    Each message has ~4 tokens overhead for role markers and formatting.
    """
    content_tokens = estimate_text_tokens(content)
    role_overhead = 4  # Approximate overhead for <|role|> markers
    return content_tokens + role_overhead


def estimate_prompt_tokens(messages: list) -> int:
    """Estimate total prompt tokens for a list of messages.

    Args:
        messages: List of LLMMessage objects or dicts with 'role' and 'content'.

    Returns:
        Estimated total prompt token count.
    """
    total = 0
    for msg in messages:
        if hasattr(msg, 'role') and hasattr(msg, 'content'):
            total += estimate_message_tokens(msg.role, msg.content)
        elif isinstance(msg, dict):
            total += estimate_message_tokens(msg.get('role', ''), msg.get('content', ''))
    # Add a small buffer for system formatting
    return total + 10


def get_model_token_limit(model: str) -> int:
    """Get the token limit for a model."""
    model_lower = model.lower()
    for key, limit in MODEL_TOKEN_LIMITS.items():
        if key in model_lower:
            return limit
    return DEFAULT_TOKEN_LIMIT


def estimate_call(
    messages: list,
    model: str,
    max_tokens: int = 4096,
) -> TokenEstimate:
    """Estimate token usage for an LLM call before making it.

    Per L-007: Pre-call token estimation to avoid unnecessary API calls
    that would exceed the model's context window.

    Args:
        messages: List of messages for the call
        model: Model name
        max_tokens: Requested max completion tokens

    Returns:
        TokenEstimate with estimated usage info
    """
    estimated_prompt = estimate_prompt_tokens(messages)
    model_limit = get_model_token_limit(model)
    available = model_limit - estimated_prompt
    exceeds = available < max_tokens

    return TokenEstimate(
        estimated_prompt_tokens=estimated_prompt,
        model_token_limit=model_limit,
        available_for_completion=max(0, available),
        exceeds_limit=exceeds,
    )


# ===== L-008: Cost Budget Integration =====

# Approximate cost per 1K tokens (USD) - update as prices change
MODEL_COSTS = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-coder": {"input": 0.00014, "output": 0.00028},
}

DEFAULT_COST = {"input": 0.002, "output": 0.008}  # Default fallback


def get_model_cost(model: str) -> dict:
    """Get cost per 1K tokens for a model."""
    model_lower = model.lower()
    for key, cost in MODEL_COSTS.items():
        if key in model_lower:
            return cost
    return DEFAULT_COST


def estimate_call_cost(
    model: str,
    estimated_prompt_tokens: int,
    max_completion_tokens: int = 4096,
) -> float:
    """Estimate the cost of an LLM call in USD.

    Args:
        model: Model name
        estimated_prompt_tokens: Estimated prompt token count
        max_completion_tokens: Maximum completion tokens

    Returns:
        Estimated cost in USD
    """
    costs = get_model_cost(model)
    input_cost = (estimated_prompt_tokens / 1000) * costs["input"]
    output_cost = (max_completion_tokens / 1000) * costs["output"]
    return round(input_cost + output_cost, 6)


async def check_session_budget(
    session_id: str,
    model: str,
    estimated_prompt_tokens: int,
    max_completion_tokens: int = 4096,
) -> tuple:
    """Check if a call would exceed the session budget.

    Per L-008: Integrated budget check before every LLM call.

    Args:
        session_id: The session ID
        model: Model name
        estimated_prompt_tokens: Estimated prompt tokens
        max_completion_tokens: Max completion tokens

    Returns:
        (allowed, reason) tuple. If not allowed, reason explains why.
    """
    from app.core.database import async_session as db_session
    from sqlalchemy import select, func
    from app.models.models import LLMCall, ModelSettings

    # Get session budget
    async with db_session() as db:
        settings_result = await db.execute(select(ModelSettings))
        model_settings = settings_result.scalars().first()
        if not model_settings:
            return True, ""  # No budget configured = unlimited

        budget = model_settings.session_budget_usd
        if budget <= 0:
            return True, ""  # Budget of 0 or less = unlimited

        # Get current spending
        result = await db.execute(
            select(func.sum(LLMCall.cost)).where(LLMCall.session_id == session_id)
        )
        total_spent = result.scalar() or 0.0

    # Estimate this call's cost
    estimated_cost = estimate_call_cost(model, estimated_prompt_tokens, max_completion_tokens)

    if total_spent + estimated_cost > budget:
        return False, f"Session budget exceeded: spent ${total_spent:.4f}, estimated ${estimated_cost:.4f}, budget ${budget:.2f}"

    return True, ""


# ===== Integration with LLM Router =====

def should_compress_context(
    messages: list,
    model: str,
    max_tokens: int = 4096,
    compression_threshold: float = 0.8,
) -> bool:
    """Check if context should be compressed before calling LLM.

    Per L-007: If estimated tokens exceed threshold of model limit,
    trigger context compression before making the call.

    Args:
        messages: Message list
        model: Model name
        max_tokens: Max completion tokens
        compression_threshold: Fraction of model limit to trigger compression

    Returns:
        True if context should be compressed
    """
    estimate = estimate_call(messages, model, max_tokens)
    usage_ratio = estimate.estimated_prompt_tokens / max(estimate.model_token_limit, 1)
    return usage_ratio > compression_threshold
