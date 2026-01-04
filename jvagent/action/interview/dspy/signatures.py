"""DSPy signatures for interview classification and extraction.

This module defines typed DSPy signatures that match the ClassificationResult
structure used in the interview system.
"""

from typing import Literal, Optional

import dspy


class InterviewClassification(dspy.Signature):
    """Classify user intent and extract field values from interview context.
    
    Analyze the user's message to determine PRIMARY INTENT and extract field values.
    Use the interpretation field when available for additional structured context.
    
    INTENT TYPES (check in priority order):
    1. CANCELLATION - HIGHEST PRIORITY (overrides all others)
       Indicators: "cancel", "abort", "stop", "quit", "nevermind", "forget it", 
       "don't want to continue", "changed my mind", "no thanks", "not interested"
       Key: Abandons ENTIRE process, not just one question
       Distinguish: "Stop asking me that; I don't have an answer; I don't know" = skip question (NOT cancellation), 
       Distinguish: "No" = indicator of response to a question (NOT cancellation), 
       "Change my email" = UPDATE (specific field)
       Rule: If abandonment language present, prefer CANCELLATION over UPDATE
    
    2. CONFIRMATION - Only in REVIEW state
       Indicators: "yes", "correct", "looks good", "sounds good", "okay", "sure", 
       "confirm", "approve"
       CRITICAL: "No" is NOT a CONFIRMATION indicator. "No" in REVIEW state means 
       the user is rejecting the confirmation and wants to UPDATE information.
       CONFIRMATION only applies to positive affirmations.
    
    3. UPDATE - Change a SPECIFIC previously answered field
       Indicators: "change", "update", "actually", "instead", "wrong", "modify", 
       "edit", "fix", "not correct", "that's wrong"
       STATE-AWARE RULE: In REVIEW state, "no" (rejecting confirmation) = UPDATE
       - If current_state is REVIEW and user says "no" or interpretation indicates 
         "not correct" or "wrong", classify as UPDATE
       - If interpretation indicates user wants to change/correct information, 
         prefer UPDATE over other intents
       - UPDATE in REVIEW state means user wants to edit previously provided information
       Must identify: field name and optionally new value (field can be null if unclear)
    
    4. SUBMISSION - Providing answers to unanswered questions
       Extract field values from message and conversation history
    
    5. NONE - No clear intent
    
    CONTEXT AWARENESS:
    - Use the interpretation field when available - it provides structured context 
      beyond the raw utterance
    - If interpretation indicates "not correct", "wrong", "needs to be changed", 
      or similar rejection language, prefer UPDATE intent
    - Interpretation helps distinguish between "no" as rejection (UPDATE) vs 
      "no" as answer to a question (SUBMISSION)
    
    EXTRACTION RULES:
    - For SUBMISSION: Include all extracted field-value pairs as separate keys 
      (e.g., "user_name": "John", "user_email": "john@example.com")
    - For UPDATE: Use "field" and "value" keys (field is null if unclear, 
      value is null if not provided)
    - Only include fields with EXPLICITLY stated or clearly implied values
    - Do NOT invent or guess values
    - Information may be provided in fragments across multiple messages
    - Review conversation history to piece together complete field values
    - Extract what is provided even if incomplete; the system will ask for missing pieces
    
    EXAMPLES:
    - "no" in REVIEW state with interpretation "not correct" → UPDATE (field: null, value: null)
    - "no" in REVIEW state → UPDATE (rejecting confirmation, not CONFIRMATION)
    - "yes" in REVIEW state → CONFIRMATION
    - "no" in ACTIVE state as answer to question → SUBMISSION
    - "no thanks" → CANCELLATION
    
    Return valid JSON with intent, confidence, field (for UPDATE), value (for UPDATE), 
    and extracted_data (for SUBMISSION) as a dictionary of field-value pairs.
    """
    
    # Input fields - context for classification
    user_message: str = dspy.InputField(
        desc="User's utterance and interpretation combined"
    )
    current_state: str = dspy.InputField(
        desc="Current interview state (ACTIVE, REVIEW, COMPLETED, CANCELLED)"
    )
    progress_info: str = dspy.InputField(
        desc="Progress information showing questions answered (e.g., '3/5 questions answered')"
    )
    answered_fields: str = dspy.InputField(
        desc="Previously answered fields with their values, formatted as a list"
    )
    entities_to_extract: str = dspy.InputField(
        desc="Unanswered fields to extract, formatted as a list with descriptions and constraints"
    )
    conversation_history: Optional[str] = dspy.InputField(
        desc="Formatted conversation history from previous interactions. Use this to piece together field values that span multiple messages. Format: chronological list of user utterances and system responses."
    )
    
    # Output fields - matching ClassificationResult structure
    intent: Literal["CANCELLATION", "CONFIRMATION", "UPDATE", "SUBMISSION", "NONE"] = dspy.OutputField(
        desc="Primary intent: CANCELLATION (abandon process), CONFIRMATION (confirm in REVIEW state), UPDATE (change specific field), SUBMISSION (provide answers), or NONE"
    )
    confidence: float = dspy.OutputField(
        desc="Confidence score between 0.0 and 1.0 for the classification"
    )
    field: Optional[str] = dspy.OutputField(
        desc="Field name for UPDATE intent (null if not UPDATE or unclear)"
    )
    value: Optional[str] = dspy.OutputField(
        desc="Field value for UPDATE intent (null if not provided or not UPDATE)"
    )
    extracted_data: Optional[dict] = dspy.OutputField(
        desc="Extracted field values for SUBMISSION intent as a dictionary (null if not SUBMISSION)"
    )

