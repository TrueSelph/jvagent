"""DSPy integration for interview classification and extraction."""

from jvagent.action.model.dspy import DSPyLM
from jvagent.action.interview.dspy.modules import InterviewClassifier
from jvagent.action.interview.dspy.signatures import InterviewClassification

__all__ = ["DSPyLM", "InterviewClassifier", "InterviewClassification"]

