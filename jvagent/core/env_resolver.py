"""Environment variable resolution for YAML descriptors.

Provides functionality to resolve environment variable placeholders
like ${VAR_NAME} in YAML configuration files.
"""

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Pattern to match ${VAR_NAME} placeholders
ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_env_placeholders(value: Any) -> Any:
    """Recursively resolve environment variable placeholders in a value.

    Supports ${VAR_NAME} syntax. If the environment variable is not found,
    the placeholder is replaced with an empty string.

    Args:
        value: Value to process (can be dict, list, str, or other types)

    Returns:
        Value with environment variables resolved

    Examples:
        >>> resolve_env_placeholders("${OPENAI_API_KEY}")
        "sk-..."  # or "" if not set

        >>> resolve_env_placeholders({"api_key": "${API_KEY}"})
        {"api_key": "actual-key-value"}

        >>> resolve_env_placeholders(["${VAR1}", "${VAR2}"])
        ["value1", "value2"]
    """
    if isinstance(value, dict):
        return {k: resolve_env_placeholders(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_env_placeholders(item) for item in value]
    elif isinstance(value, str):
        return _resolve_string_placeholders(value)
    else:
        return value


def _resolve_string_placeholders(text: str) -> str:
    """Resolve environment variable placeholders in a string.

    Args:
        text: String that may contain ${VAR_NAME} placeholders

    Returns:
        String with placeholders replaced by environment variable values
    """

    def replace_placeholder(match: re.Match[str]) -> str:
        var_name = match.group(1)
        value = os.getenv(var_name, "")
        if not value:
            logger.debug(f"Environment variable '{var_name}' not found, using empty string")
        return value

    return ENV_PLACEHOLDER_PATTERN.sub(replace_placeholder, text)
