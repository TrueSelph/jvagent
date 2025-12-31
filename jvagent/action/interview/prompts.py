"""Prompt templates for interview action module.

This module centralizes all prompt templates used throughout the interview
action system for consistency and maintainability.
"""

# Review state directive templates
REVIEW_SUMMARY_HEADER_TEMPLATE = "Here's what I have:\n"

REVIEW_SUMMARY_ITEM_TEMPLATE = "{display_name}: {value}"

REVIEW_CONFIRMATION_HEADER_TEMPLATE = """Present the following collected information to the user for review and confirmation.

{summary}

Instruct the user to:"""

REVIEW_CONFIRMATION_INSTRUCTIONS_TEMPLATE = """- Confirm if all information is correct by saying "yes", "correct", "looks good", or similar
- Request changes to any specific field if needed (e.g., "change my email to...", "update my name")
- Say "cancel" if they wish to abandon the process"""

REVIEW_CONFIRMATION_PROMPT_TEMPLATE = """Ask: "Does everything look correct?" or similar phrasing to prompt their response."""

REVIEW_UNCLEAR_EDIT_DIRECTIVE_TEMPLATE = """Which field would you like to change? Available fields: {field_list}"""

REVIEW_UNCLEAR_GENERAL_DIRECTIVE_TEMPLATE = """I didn't understand. Please say 'yes' to confirm, 'no' to edit, or specify which field you'd like to change."""

# Update prompt template (for prompting user for new value when updating)
UPDATE_PROMPT_FOR_VALUE_TEMPLATE = """The current value for {field_display} is: {current_value}

What would you like to change it to?"""

# Completion message template (for COMPLETED state)
COMPLETION_MESSAGE_TEMPLATE = "Thank you! Your responses have been recorded."

# Cancellation message template (for CANCELLED state)
CANCELLATION_MESSAGE_TEMPLATE = "I've cancelled the interview. Let me know if you'd like to start over."

# Interview Prompt Template
# This prompt combines intent detection (CANCELLATION, CONFIRMATION, UPDATE, SUBMISSION) 
# with response extraction in a single LLM call for efficiency and consistency.
INTERVIEW_PROMPT_TEMPLATE = """Analyze the user's message to determine PRIMARY INTENT and extract field values.

USER MESSAGE:
{user_message}

CONTEXT:
- Current state: {current_state}
- Progress: {progress_info}
- Answered fields: {answered_fields_with_values}
- Unanswered fields to extract (if SUBMISSION): {entities_to_extract}

INTENT TYPES (check in priority order):
1. CANCELLATION - HIGHEST PRIORITY (overrides all others)
   Indicators: "cancel", "abort", "stop", "quit", "nevermind", "forget it", "don't want to continue", "changed my mind", "no thanks", "not interested"
   Key: Abandons ENTIRE process, not just one question
   Distinguish: "Stop asking me that" = skip question (NOT cancellation), "Change my email" = UPDATE (specific field)
   Rule: If abandonment language present, prefer CANCELLATION over UPDATE

2. CONFIRMATION - Only in REVIEW state
   Indicators: "yes", "correct", "looks good", "sounds good", "okay", "sure", "confirm", "approve"

3. UPDATE - Change a SPECIFIC previously answered field
   Indicators: "change", "update", "actually", "instead", "wrong", "modify", "edit", "fix"
   Must identify: field name and optionally new value

4. SUBMISSION - Providing answers to unanswered questions
   Extract field values from message

5. NONE - No clear intent

EXTRACTION RULES:
- Only extract values if intent is SUBMISSION
- Only include fields with EXPLICITLY stated or clearly implied values
- Do NOT invent or guess values
- For UPDATE: identify field from answered_fields, extract new_value if provided

Return ONLY valid JSON (no markdown):
{{
  "intent": "CANCELLATION" | "CONFIRMATION" | "UPDATE" | "SUBMISSION" | "NONE",
  "confidence": 0.0-1.0,
  "update_field": "field_name" | null,
  "update_value": "new_value" | null,
  "needs_value_prompt": true | false,
  "needs_field_clarification": true | false,
  // For SUBMISSION: include extracted field values only
}}

EXAMPLES:
"Cancel this" → {{"intent": "CANCELLATION", "confidence": 1.0}}
"Nevermind, forget it" → {{"intent": "CANCELLATION", "confidence": 1.0}}
"I don't want to continue" → {{"intent": "CANCELLATION", "confidence": 1.0}}
"Stop asking me that" → {{"intent": "SUBMISSION"}} (skip question, not cancellation)
"Change my email to john@example.com" → {{"intent": "UPDATE", "update_field": "user_email", "update_value": "john@example.com"}}
"Actually, my email is wrong" → {{"intent": "UPDATE", "update_field": null, "needs_field_clarification": true}}
"Yes, that looks correct" → {{"intent": "CONFIRMATION", "confidence": 0.95}}
"My name is John Doe" → {{"intent": "SUBMISSION", "user_name": "John Doe"}}"""

