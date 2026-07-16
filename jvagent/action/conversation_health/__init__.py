"""Deprecated: Conversation Health is a core service, not an action.

Use ``jvagent.core.conversation_health`` and ``config.conversation_health``.
Listing ``jvagent/conversation_health`` in agent.yaml is no longer required
and should be removed.
"""

import logging
import warnings

logger = logging.getLogger(__name__)

warnings.warn(
    "jvagent.action.conversation_health is deprecated; Conversation Health is a "
    "core service (default on). Remove jvagent/conversation_health from agent.yaml "
    "and use config.conversation_health / agent.conversation_health_enabled.",
    DeprecationWarning,
    stacklevel=2,
)
logger.warning(
    "Deprecated package jvagent.action.conversation_health imported; "
    "use jvagent.core.conversation_health (service is on by default)."
)

from jvagent.core.conversation_health.heuristics import run_heuristics  # noqa: E402
from jvagent.core.conversation_health.scoring import (  # noqa: E402
    recompute_conversation_rollup,
    score_dimensions,
)
from jvagent.core.conversation_health.service import is_scorable  # noqa: E402

__all__ = [
    "is_scorable",
    "recompute_conversation_rollup",
    "run_heuristics",
    "score_dimensions",
]
