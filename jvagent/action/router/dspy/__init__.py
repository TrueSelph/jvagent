"""DSPy integration for intent-first router classification.

This module provides DSPy signatures and modules for classifying user intent
and routing utterances to appropriate InteractActions.
"""

from jvagent.action.router.dspy.signatures import (
    create_router_classification_signature,
    INTENT_TYPES,
)
from jvagent.action.router.dspy.modules import RouterModule

__all__ = [
    "create_router_classification_signature",
    "RouterModule",
    "INTENT_TYPES",
]

