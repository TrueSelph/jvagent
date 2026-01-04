"""DSPy integration for router classification.

This module provides DSPy signatures and modules for routing user utterances
to appropriate InteractActions, enabling optimization via DSPy teleprompters.
"""

from jvagent.action.router.dspy.signatures import RouterClassification
from jvagent.action.router.dspy.modules import RouterModule

__all__ = ["RouterClassification", "RouterModule"]

