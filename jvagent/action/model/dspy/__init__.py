"""DSPy integration for jvagent language models.

This module provides integration between DSPy and jvagent's LanguageModelAction,
enabling DSPy modules to use jvagent's existing model infrastructure with full
support for caching, usage tracking, and optimization.
"""

from jvagent.action.model.dspy.lm_adapter import DSPyLM
from jvagent.action.model.dspy.utils import format_conversation_history_for_dspy

__all__ = ["DSPyLM", "format_conversation_history_for_dspy"]

