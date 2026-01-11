"""DSPy signatures for interview classification and extraction.

This module defines typed DSPy signatures that match the ClassificationResult
structure used in the interview system.
"""

from typing import Literal, Optional, Type

import dspy


def create_interview_classification_signature(docstring: str) -> Type[dspy.Signature]:
    """Factory function to create InterviewClassification signature with custom docstring.
    
    Args:
        docstring: The docstring to use for the signature class
        
    Returns:
        A dynamically created signature class with the provided docstring
    """
    class InterviewClassification(dspy.Signature):
        __doc__ = docstring
        
        # Input fields - context for classification
        user_input: str = dspy.InputField(
            desc="User's input (typically with reasoning) - router interpretation when available which contains structured context with embedded field values, or raw utterance as fallback."
        )
        current_state: str = dspy.InputField(
            desc="Current interview state (ACTIVE, REVIEW, COMPLETED, CANCELLED)"
        )
        answered_fields: str = dspy.InputField(
            desc="Comma-separated list of previously answered field names (minimal context for UPDATE intent)"
        )
        entities_to_extract: str = dspy.InputField(
            desc="Unanswered fields to extract, formatted as a list with descriptions, constraints, and [REQUIRED] or [OPTIONAL] markers"
        )
        required_fields_info: str = dspy.InputField(
            desc="List of required field names. Use this to determine if a field can be declined (only non-required fields can be declined)"
        )
        conversation_history: Optional[str] = dspy.InputField(
            desc="Formatted conversation history from previous interactions. CRITICAL: Use this to resolve fragmentary user references (e.g., '9am', 'the first one') to complete values from recent AI responses. When user provides partial values, match them to full options/values mentioned in recent conversation history. Format: chronological list of user utterances and system responses."
        )
        
        # Output fields - matching ClassificationResult structure
        intent: Literal["CANCELLATION", "CONFIRMATION", "UPDATE", "DECLINE", "SUBMISSION", "NONE"] = dspy.OutputField(
            desc="Primary intent: CANCELLATION (abandon process), CONFIRMATION (confirm in REVIEW state), UPDATE (change specific field), DECLINE (decline non-required field), SUBMISSION (provide answers), or NONE"
        )
        confidence: float = dspy.OutputField(
            desc="Confidence score between 0.0 and 1.0 for the classification"
        )
        field: Optional[str] = dspy.OutputField(
            desc="Field name for UPDATE or DECLINE intent (null if not UPDATE/DECLINE or unclear). For DECLINE, field must be specified."
        )
        value: Optional[str] = dspy.OutputField(
            desc="Field value for UPDATE intent (null if not provided or not UPDATE)"
        )
        extracted_data: Optional[dict] = dspy.OutputField(
            desc="Extracted field values for SUBMISSION intent as a dictionary (null if not SUBMISSION). Values should be complete and contextually resolved - use conversation_history to resolve fragments to full values from recent conversation context."
        )
    
    return InterviewClassification

