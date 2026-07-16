"""Conversation Health core service (default-on, not an InteractAction)."""

# Register REST endpoints + deferred AI handler on import
from . import deferred as _deferred  # noqa: F401
from . import endpoints as _endpoints  # noqa: F401
from .config import (
    ConversationHealthConfig,
    is_enabled_for_agent,
    load_conversation_health_config,
)
from .service import (
    get_agent_reading,
    is_scorable,
    maybe_score_after_interaction,
    run_ai_for_interaction,
    score_interaction,
)
from .state import ConversationHealthState

__all__ = [
    "ConversationHealthConfig",
    "ConversationHealthState",
    "get_agent_reading",
    "is_enabled_for_agent",
    "is_scorable",
    "load_conversation_health_config",
    "maybe_score_after_interaction",
    "run_ai_for_interaction",
    "score_interaction",
]
