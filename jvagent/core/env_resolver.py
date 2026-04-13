"""Environment variable resolution for YAML descriptors.

Resolves ``${VAR_NAME}`` in YAML strings (missing vars become ``""``; debug log by default).
Use ``${VAR_NAME:?}`` to emit a warning when the variable is unset or empty.
Set ``JVAGENT_WARN_EMPTY_PLACEHOLDERS=true`` to warn on every empty ``${VAR}`` (not ``:?``).
"""

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ${VAR} optional; ${VAR:?} logs WARNING when unset or empty (required placeholder)
ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:\?)?\}")


def _warn_all_empty_placeholders() -> bool:
    raw = os.getenv("JVAGENT_WARN_EMPTY_PLACEHOLDERS", "")
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


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
        "sk-..."  # or "" if OPENAI_API_KEY not set  # pragma: allowlist secret

        >>> resolve_env_placeholders({"api_key": "${API_KEY}"})
        {"api_key": "actual-key-value"}  # or {"api_key": ""} if API_KEY not set  # pragma: allowlist secret

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

    Supports ``${VAR_NAME}`` and ``${VAR_NAME:?}`` (required; warns if empty).

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
        required = match.group(2) == ":?"
        value = os.getenv(var_name, "")
        if not value:
            if required:
                logger.warning(
                    "Required env placeholder ${%s:?} is unset or empty; "
                    "replacing with empty string",
                    var_name,
                )
            elif _warn_all_empty_placeholders():
                logger.warning(
                    "Environment variable '%s' not set; ${%s} replaced with empty string",
                    var_name,
                    var_name,
                )
            else:
                logger.debug(
                    "Environment variable '%s' not found in environment, "
                    "replacing placeholder with empty string",
                    var_name,
                )
        return value

    return ENV_PLACEHOLDER_PATTERN.sub(replace_placeholder, text)
