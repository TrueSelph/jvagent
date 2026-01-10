"""Prompt templates for interview action module.

This module centralizes all prompt templates used throughout the interview
action system for consistency and maintainability.
"""

# Consolidated review directive template
# Single template handling all review scenarios: confirmation, unclear edit, unclear general
# Placeholders (populate one section, leave others empty):
#   - {summary_items}: Formatted list of field-value pairs (one per line with "- " prefix)
#   - {instructions}: Instructions for user actions
#   - {prompt}: Confirmation prompt
#   - {field_list}: Comma-separated list of available fields
#   - {confirmation_section}: Confirmation content (use REVIEW_CONFIRMATION_CONTENT.format(...))
#   - {unclear_edit_section}: Unclear edit content (use REVIEW_UNCLEAR_EDIT_CONTENT.format(...))
#   - {unclear_general_section}: Unclear general content (use REVIEW_UNCLEAR_GENERAL_CONTENT)
REVIEW_DIRECTIVE_TEMPLATE = """{confirmation_section}{unclear_edit_section}{unclear_general_section}"""

# Confirmation content template
REVIEW_CONFIRMATION_CONTENT = """Present the following collected information for review and confirmation.

{summary}

Tell the user: You can:
{instructions}

{prompt}"""

# Unclear edit content template
# Used when user wants to update but hasn't specified which field
# Placeholders: {summary} (formatted summary of current information), {field_list} (comma-separated list of fields)
REVIEW_UNCLEAR_EDIT_CONTENT = """Tell the user: I understand you'd like to make changes to the information above.

{summary}

Tell the user: Which field would you like to change? Available fields: {field_list}"""

# Unclear general content (static, no placeholders)
REVIEW_UNCLEAR_GENERAL_CONTENT = """Tell the user: I didn't understand. Ask: Please say 'yes' to confirm, 'no' to edit, or specify which field you'd like to change."""

# Default values for review confirmation
REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS = """Tell the user:
- Say "yes" or "correct" to confirm, or specify which field to change
- Say "cancel" to abandon the process"""

REVIEW_CONFIRMATION_DEFAULT_PROMPT = """Ask: "Does everything look correct?" or similar phrasing to prompt their response."""

# Summary formatting templates (used to build summary for confirmation)
REVIEW_SUMMARY_HEADER_TEMPLATE = "Tell the user: Here's what I have:\n"
REVIEW_SUMMARY_ITEM_TEMPLATE = "{display_name}: {value}"

# Update prompt template (for prompting user for new value when updating)
UPDATE_PROMPT_FOR_VALUE_TEMPLATE = """Tell the user: The current value for {field_display} is: {current_value}

Ask: What would you like to change it to?"""

# Completion message template (for COMPLETED state)
COMPLETION_MESSAGE_TEMPLATE = "Tell the user: Thank you! Your responses have been recorded."

# Cancellation message template (for CANCELLED state)
CANCELLATION_MESSAGE_TEMPLATE = "Tell the user: I've cancelled the interview. Ask: Let me know if you'd like to start over."

# Active event message template (for ACTIVE state)
ACTIVE_EVENT_MESSAGE_TEMPLATE = "Ongoing Activity: interviewing user as part of {class_name}"

# Review event message template (for REVIEW state)
REVIEW_EVENT_MESSAGE_TEMPLATE = "Ongoing Activity: reviewing interview responses as part of {class_name}"

# Completion event message template (for COMPLETED state)
COMPLETION_EVENT_MESSAGE_TEMPLATE = "interview process completed as part of {class_name}"

# Cancellation event message template (for CANCELLED state)
CANCELLATION_EVENT_MESSAGE_TEMPLATE = "interview process cancelled as part of {class_name}"

# Question directive template (for ACTIVE state - question prompting)
# Consolidated template that handles description, question, and optional instructions
# Instructions placeholder will be empty string if no instructions provided
QUESTION_DIRECTIVE_TEMPLATE = """Make a request to the user based on the following description:
{question} ({description})

{instructions}
"""

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

IMPORTANT - CONVERSATION HISTORY:
- Conversation history is available in the message history
- Information may be provided in fragments across multiple messages
- Review previous messages to piece together complete field values
- Consider context from earlier turns when extracting current message content
- If a field value is incomplete in the current message, check history for missing pieces
- **PARTIAL ANSWERS**: Users may provide partial answers to multi-part questions (e.g., a follow-up last name when asked for it)
- **CONTEXT MATCHING**: Match user responses to previously asked questions in the conversation history
- **INCREMENTAL EXTRACTION**: Extract what is provided even if incomplete; the system will ask for missing pieces

INTENT TYPES (check in priority order):
1. CANCELLATION - HIGHEST PRIORITY (overrides all others)
   Indicators: "cancel", "abort", "stop", "quit", "nevermind", "forget it", "don't want to continue", "changed my mind", "no thanks", "not interested"
   Key: Abandons ENTIRE process, not just one question
   Distinguish: "Stop asking me that; I don't have an answer; I don't know" = skip question (NOT cancellation), 
   Distinguish: "No" = indicator of response to a question (NOT cancellation), 
   Rule: If abandonment language present, prefer CANCELLATION over UPDATE

2. CONFIRMATION - Only in REVIEW state
   Indicators: "yes", "correct", "looks good", "sounds good", "okay", "sure", "confirm", "approve"
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
- For SUBMISSION: Include all extracted field-value pairs as separate keys (e.g., "user_name": "John", "user_email": "john@example.com")
- For UPDATE: Use "field" and "value" keys (field is null if unclear, value is null if not provided)
- Only include fields with EXPLICITLY stated or clearly implied values
- Do NOT invent or guess values
- **FRAGMENTED INFORMATION**: If information spans multiple messages, combine fragments from current message and conversation history to form complete values
- **CONTEXT AWARENESS**: Use conversation history to understand references (e.g., "my email" refers to email mentioned earlier, "that value" refers to previously discussed field)

Return ONLY valid JSON (no markdown):
{{
  "intent": "CANCELLATION" | "CONFIRMATION" | "UPDATE" | "SUBMISSION" | "NONE",
  "confidence": 0.0-1.0,
  "field": "field_name" | null,
  "value": "extracted_value | new_value" | null,
  // For SUBMISSION: include additional field-value pairs here
}}

EXAMPLES:
"Cancel this" → {{"intent": "CANCELLATION", "confidence": 1.0}}
"Nevermind, forget it" → {{"intent": "CANCELLATION", "confidence": 1.0}}
"I don't want to continue" → {{"intent": "CANCELLATION", "confidence": 1.0}}
"Stop asking me that" → {{"intent": "SUBMISSION"}} (skip question, not cancellation)
"Change my email to john@example.com" → {{"intent": "UPDATE", "field": "user_email", "value": "john@example.com"}}
"Actually, my email is wrong" → {{"intent": "UPDATE", "field": null, "value": null}}
"My email is john@example.com" → {{"intent": "UPDATE", "field": "user_email", "value": null}}
"Yes, that looks correct" → {{"intent": "CONFIRMATION", "confidence": 0.95}}
"no" in REVIEW state with interpretation "not correct" → {{"intent": "UPDATE", "field": null, "value": null}}
"no" in REVIEW state → {{"intent": "UPDATE", "field": null, "value": null}} (rejecting confirmation, not CONFIRMATION)
"no" in ACTIVE state as answer to question → {{"intent": "SUBMISSION"}}
"no thanks" → {{"intent": "CANCELLATION", "confidence": 1.0}}
"My name is John Doe" → {{"intent": "SUBMISSION", "user_name": "John Doe"}}
"My name is John and email is john@example.com" → {{"intent": "SUBMISSION", "user_name": "John", "user_email": "john@example.com"}}"""
