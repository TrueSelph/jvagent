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

    This function processes:
    - Dictionaries: Recursively resolves placeholders in all values
    - Lists: Recursively resolves placeholders in all items
    - Strings: Replaces ${VAR_NAME} patterns with env var values (or empty string)
    - Other types: Returned unchanged

    Args:
        value: Value to process (can be dict, list, str, or other types)

    Returns:
        Value with environment variables resolved. Missing env vars result in empty strings.

    Examples:
        >>> resolve_env_placeholders("${OPENAI_API_KEY}")
        "sk-..."  # or "" if OPENAI_API_KEY not set

        >>> resolve_env_placeholders({"api_key": "${API_KEY}"})
        {"api_key": "actual-key-value"}  # or {"api_key": ""} if API_KEY not set

        >>> resolve_env_placeholders(["${VAR1}", "${VAR2}"])
        ["value1", "value2"]  # or ["", ""] if vars not set

        >>> resolve_env_placeholders({"nested": {"key": "${VAR}"}})
        {"nested": {"key": "value"}}  # Recursively processes nested structures
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

    Supports ${VAR_NAME} syntax. If the environment variable is not found,
    the placeholder is replaced with an empty string.

    Handles multiple placeholders in a single string:
    - "${VAR1}_${VAR2}" resolves both placeholders
    - "prefix_${VAR}_suffix" resolves the placeholder while preserving surrounding text

    Args:
        text: String that may contain ${VAR_NAME} placeholders

    Returns:
        String with placeholders replaced by environment variable values (or empty string if not found)

    Examples:
        >>> _resolve_string_placeholders("${OPENAI_API_KEY}")
        "sk-..."  # or "" if OPENAI_API_KEY not set

        >>> _resolve_string_placeholders("prefix_${VAR}_suffix")
        "prefix_value_suffix"  # or "prefix__suffix" if VAR not set

        >>> _resolve_string_placeholders("${VAR1}_${VAR2}")
        "value1_value2"  # or "__" if both not set
    """

    def replace_placeholder(match: re.Match[str]) -> str:
        var_name = match.group(1)
        # Get environment variable value, defaulting to empty string if not found
        # This ensures that missing env vars result in empty string as prescribed
        value = os.getenv(var_name, "")
        if not value:
            logger.debug(
                f"Environment variable '{var_name}' not found in environment, "
                f"replacing placeholder with empty string"
            )
        return value

    # Replace all ${VAR_NAME} patterns in the string
    return ENV_PLACEHOLDER_PATTERN.sub(replace_placeholder, text)
