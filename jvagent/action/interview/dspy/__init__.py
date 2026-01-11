"""DSPy integration for interview classification and extraction."""

from jvagent.action.model.dspy import DSPyLM
from jvagent.action.interview.dspy.modules import InterviewClassifier
from jvagent.action.interview.dspy.signatures import create_interview_classification_signature

__all__ = ["DSPyLM", "InterviewClassifier", "create_interview_classification_signature"]

