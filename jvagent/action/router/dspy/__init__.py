"""DSPy integration for router classification.

This module provides DSPy signatures and modules for routing user utterances
to appropriate InteractActions, enabling optimization via DSPy teleprompters.
"""

from jvagent.action.router.dspy.signatures import create_router_classification_signature
from jvagent.action.router.dspy.modules import RouterModule

__all__ = ["create_router_classification_signature", "RouterModule"]

