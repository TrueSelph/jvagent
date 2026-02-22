"""Token estimation utilities for streaming model calls.

Provides token counting using tiktoken when available, with word-based fallback.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Try to import tiktoken, but make it optional
try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logger.debug("tiktoken not available, using word-based token estimation")


def _normalize_model_name(model: str, provider: str) -> str:
    """Normalize model name for tiktoken compatibility.

    Maps model names to tiktoken encoding names.
    Handles OpenRouter format (provider/model) by extracting the model part.

    Args:
        model: Model identifier (e.g., "gpt-4o", "gpt-4o-mini", "openai/gpt-4o", "anthropic/claude-3.5-sonnet")
        provider: Provider name (e.g., "openai", "openrouter")

    Returns:
        Normalized model name for tiktoken
    """
    model_lower = model.lower()

    # Extract model name from OpenRouter format (provider/model)
    if "/" in model_lower:
        # OpenRouter format: "openai/gpt-4o" or "anthropic/claude-3.5-sonnet"
        model_lower = model_lower.split("/", 1)[1]

    # OpenAI/OpenRouter GPT models
    if model_lower.startswith("gpt-4o"):
        return "gpt-4o"
    elif model_lower.startswith("gpt-4-turbo") or model_lower.startswith("gpt-4-1106"):
        return "gpt-4-turbo"
    elif model_lower.startswith("gpt-4-32k"):
        return "gpt-4-32k"
    elif model_lower.startswith("gpt-4"):
        return "gpt-4"
    elif model_lower.startswith("gpt-3.5-turbo") or model_lower.startswith(
        "gpt-35-turbo"
    ):
        return "gpt-3.5-turbo"
    elif model_lower.startswith("gpt-3.5") or model_lower.startswith("gpt-35"):
        return "gpt-3.5-turbo"

    # Claude models (Anthropic via OpenRouter)
    if "claude" in model_lower:
        # Claude models use cl100k_base encoding (same as GPT-4)
        return "gpt-4"

    # Default to gpt-3.5-turbo encoding for unknown models
    return "gpt-3.5-turbo"


def _get_tiktoken_encoding(model: str, provider: str) -> Optional[object]:
    """Get tiktoken encoding for a model.

    Args:
        model: Model identifier
        provider: Provider name

    Returns:
        tiktoken encoding object, or None if unavailable
    """
    if not TIKTOKEN_AVAILABLE:
        return None

    try:
        normalized = _normalize_model_name(model, provider)
        encoding = tiktoken.encoding_for_model(normalized)
        return encoding
    except (KeyError, ValueError) as e:
        logger.debug(f"Could not get tiktoken encoding for model {model}: {e}")
        return None


def estimate_tokens(text: str, model: str = "", provider: str = "") -> int:
    """Estimate token count for text.

    Uses tiktoken when available for supported models, otherwise falls back
    to word-based estimation.

    Args:
        text: Text to estimate tokens for
        model: Model identifier (for tiktoken selection)
        provider: Provider name (for tiktoken selection)

    Returns:
        Estimated token count
    """
    if not text:
        return 0

    # Try tiktoken first
    encoding = _get_tiktoken_encoding(model, provider)
    if encoding:
        try:
            tokens = encoding.encode(text)
            return len(tokens)
        except Exception as e:
            logger.debug(f"tiktoken encoding failed, using fallback: {e}")

    # Fallback to word-based estimation
    # Average English word is ~1.3 tokens (accounts for subword tokenization)
    word_count = len(text.split())
    estimated = int(word_count * 1.3)
    return max(estimated, 1)  # At least 1 token


def estimate_prompt_tokens(
    messages: List[Dict[str, Any]], model: str = "", provider: str = ""
) -> int:
    """Estimate token count for prompt messages.

    Formats messages as they would be sent to the API and estimates tokens.
    Accounts for message formatting overhead (role, content structure).

    Args:
        messages: List of message dicts with 'role' and 'content' keys
        model: Model identifier (for tiktoken selection)
        provider: Provider name (for tiktoken selection)

    Returns:
        Estimated prompt token count
    """
    if not messages:
        return 0

    # Format messages as they would be sent to API
    # Each message has overhead: role name + formatting
    encoding = _get_tiktoken_encoding(model, provider)

    total_tokens = 0

    # Account for message formatting overhead (approximately 4 tokens per message)
    message_overhead = 4

    for message in messages:
        role = message.get("role", "")
        content = message.get("content", "")

        if isinstance(content, str):
            content_text = content
        elif isinstance(content, list):
            # Multimodal content - extract text parts
            content_text = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            content_text = str(content)

        # Estimate tokens for role + content
        if encoding:
            try:
                role_tokens = len(encoding.encode(role))
                content_tokens = len(encoding.encode(content_text))
                total_tokens += role_tokens + content_tokens + message_overhead
            except Exception:
                # Fallback if encoding fails
                word_count = len(content_text.split())
                total_tokens += int(word_count * 1.3) + message_overhead
        else:
            # Word-based fallback
            word_count = len(content_text.split())
            total_tokens += int(word_count * 1.3) + message_overhead

    # Add system message overhead if present
    # Most APIs add a few tokens for system message formatting
    if any(msg.get("role") == "system" for msg in messages):
        total_tokens += 2

    return max(total_tokens, 1)


def estimate_completion_tokens(text: str, model: str = "", provider: str = "") -> int:
    """Estimate token count for completion text.

    Convenience wrapper around estimate_tokens for completion text.

    Args:
        text: Completion text to estimate tokens for
        model: Model identifier (for tiktoken selection)
        provider: Provider name (for tiktoken selection)

    Returns:
        Estimated completion token count
    """
    return estimate_tokens(text, model, provider)
