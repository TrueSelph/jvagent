"""Constants for interview action module.

Centralized constants to replace magic strings throughout the codebase.
"""

# Cache keys for session.context
CACHE_KEY_QUESTION_NODES = "_question_node_cache"

# Context keys (moved from enums.ContextKey for better organization)
CONTEXT_KEY_DIRECTIVE_OVERRIDE_REPLACE_MODE = "_directive_override_replace_mode"
CONTEXT_KEY_DIRECTIVE_OVERRIDE_APPEND_MODE = "_directive_override_append_mode"
