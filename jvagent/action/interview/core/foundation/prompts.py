"""Prompt templates for interview action module.

This module centralizes all prompt templates used throughout the interview
action system for consistency and maintainability.

Template Categories:
    User-Facing Messages: Shown directly to users
        - UPDATE_PROMPT_FOR_VALUE: Prompt for new field value
        - REQUIRED_FIELD_DECLINE: Insist on required field
        - COMPLETION_MESSAGE: Interview completed confirmation
        - CANCELLATION_MESSAGE: Interview cancelled acknowledgment
    
    System Directives: Guide LLM response generation
        - QUESTION_DIRECTIVE: Format question prompts
        - REVIEW_CONFIRMATION_DIRECTIVE: Confirmation with summary
        - REVIEW_UNCLEAR_EDIT_DIRECTIVE: Prompt for which field to edit
        - REVIEW_UNCLEAR_GENERAL_DIRECTIVE: General unclear response
    
    Classification Prompts: Intent detection and extraction
        - CLASSIFICATION_RULES_CORE: Core classification logic (DRY)
        - INTERVIEW_PROMPT: Full prompt with context formatting

    State Messages: Event tracking
        - STATE_EVENT_MESSAGES: State-specific event messages (dict)
        - get_state_event_message(): Helper to format state messages

Placeholder Conventions:
    {field_display}, {current_value}: Field-related values
    {summary}, {instructions}, {prompt}: Review content sections
    {class_name}: Interview action class name
    {question}, {description}: Question-related values
"""

# =============================================================================
# Summary Formatting Templates
# =============================================================================

REVIEW_SUMMARY_HEADER = ""
REVIEW_SUMMARY_ITEM = "{display_name}: {value}"

# =============================================================================
# User-Facing Message Templates
# =============================================================================

UPDATE_PROMPT_FOR_VALUE = """Tell the user: The current value for {field_display} is: {current_value}

Ask: What would you like to change it to?"""

REQUIRED_FIELD_DECLINE = """Tell the user: I understand, but {field_display} is required to complete this process.

Ask: {question}"""

COMPLETION_MESSAGE = "Tell the user: Thank you! Your responses have been recorded."

CANCELLATION_MESSAGE = "Tell the user: I've cancelled the interview. Ask: Let me know if you'd like to start over."

# =============================================================================
# System Directive Templates
# =============================================================================

QUESTION_DIRECTIVE = """Make a request to the user based on the following:
{question} ({description}){context_section}
{instructions}
"""

REVIEW_CONFIRMATION_DIRECTIVE = """Prompt: Here's what I have so far: (let the user know what you have, keeping the same structure and order of the items below. Format the label in bold. Eg. *label:* value)

{summary}

{instructions}

{prompt}"""

REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS = """Prompt: Just let me know if everything looks good, or tell me what you'd like to change. You can also let me know if you'd like to cancel altogether."""

REVIEW_CONFIRMATION_DEFAULT_PROMPT = """Ask: "Does everything look correct?" or similar phrasing to prompt their response."""

REVIEW_UNCLEAR_EDIT_DIRECTIVE = """Got it, you'd like to make some changes. Here's what I have:

{summary}

Which one would you like to change? I can update: {field_list}"""

REVIEW_UNCLEAR_GENERAL_DIRECTIVE = """Prompt: I'm not quite sure what you meant there. Could you clarify what you'd like to do?"""

# =============================================================================
# State Event Messages
# =============================================================================

STATE_EVENT_MESSAGES = {
    "ACTIVE": "Ongoing Activity: interviewing user as part of {class_name}",
    "REVIEW": "Ongoing Activity: reviewing interview responses as part of {class_name}",
    "COMPLETED": "Completed activity: {class_name}",
    "CANCELLED": "interview process cancelled as part of {class_name}",
}


def get_state_event_message(state: str, class_name: str) -> str:
    """Get formatted state event message.
    
    Args:
        state: Interview state (ACTIVE, REVIEW, COMPLETED, CANCELLED)
        class_name: Interview action class name
        
    Returns:
        Formatted event message string
    """
    template = STATE_EVENT_MESSAGES.get(state, "")
    return template.format(class_name=class_name) if template else ""


# =============================================================================
# Classification Prompts - Core Rules (Single Source of Truth)
# =============================================================================

CLASSIFICATION_RULES_CORE = """Apply checks in order, then extract, then output a single JSON object only.

STATE: review → CONFIRMATION = pure affirmation, no new values. active → answering current question = SUBMISSION (including "yes"/"no"); CONFIRMATION invalid.

INTENTS (one only):
1. CANCELLATION - abandons process ("cancel", "abort", "stop").
2. CONFIRMATION - only in review; "yes"/"correct"/"looks good" with no new values.
3. SUBMISSION - answering an unanswered question only. Field must be in Unanswered fields. If the field is in Answered fields or extraction would assign a new value to a field in Answered fields → UPDATE instead. Invalid/wrong-format → SUBMISSION (validation handles). In review, if Unanswered fields exist and the response answers one (including "yes"/"no"), this is SUBMISSION, not UPDATE.
4. UPDATE - changing an already-answered field. REQUIRES explicit change/update language: phrases like "change … to …", "update …", "actually I prefer …", "make it …", "switch … to …", "no, change …", "I meant …", "let me correct …", or clearly referencing an answered field with a replacement value. An isolated word or bare "yes"/"no" is NEVER an UPDATE — classify as SUBMISSION or DECLINE instead. Field in Answered fields (not in Unanswered); user provides a replacement value alongside update language.
5. DECLINE - declines to answer (optional or required). Use when: explicit refusal ("I won't answer", "skip", "I'd rather not"); "No" to optional content (photos, attachments); or router "declines to..." / "refuses to...". Not when user answers "no" to a yes/no question (→ SUBMISSION). Field from Unanswered / Recent conversation / "unknown_field".
6. NONE - last resort only: no clear intent and no viable extractions or deductions. Not for yes/no "no" (→ SUBMISSION) or explicit refusal (→ DECLINE).

"NO": optional content (photos, attachments) → DECLINE. yes/no question (Unanswered fields or Recent conversation) → SUBMISSION value "no". review with Unanswered fields → SUBMISSION if answering a pending question. If current question (Recent conversation / Unanswered fields) is answerable by yes or no, negatory ("no", "nope", "I don't", etc.) = SUBMISSION value "no", not UPDATE, DECLINE, or NONE.

EXTRACTION: All field-related output goes only in "extracted" as a list of one-key objects. SUBMISSION/UPDATE: e.g. [{{"incident_location": "Water Street"}}]. DECLINE: or No extractions: extracted: [].

VALIDATION: Before accepting a SUBMISSION, verify the extracted value genuinely satisfies the field's expected content description. The description after each field name defines what a valid answer looks like. If the user's response is a meta-request (e.g., "I want to make a report"), a greeting, an expression of intent, or otherwise does not contain substantive content matching the field description, do NOT extract it. Classify as NONE instead. A response must provide actual informational content relevant to the field — not just mention the topic.

OUTPUT: JSON only. reasoning optional. Output only intent, confidence, and extracted. Do not include "field" or "value" keys."""

# =============================================================================
# Classification Prompts - Template Variants
# =============================================================================

# Interview Prompt - Full template with context formatting (use .format(classification_rules_core=..., ...))
INTERVIEW_PROMPT = """Classify intent and extract field values from user input.

USER INPUT (router interpretation or raw utterance):
{user_input}

CONTEXT:
- Current state: {current_state}
- Answered fields (with current values): {answered_fields}
- Unanswered fields: {entities_to_extract}
- Recent conversation (use to identify current question and resolve "yes"/"no" and references):
{conversation_history}

{classification_rules_core}

Return a single JSON object only. No markdown or explanation. Required keys: intent, confidence, extracted (array). Do not include "field" or "value". SUBMISSION/UPDATE: put actual values in extracted as list of one-key objects, e.g. [{{"incident_location": "Water Street"}}]. DECLINE: put [{{"field_name": "N/A"}}]. When no extractions: extracted: []. Optional key: reasoning (one line).
{{
  "intent": "CANCELLATION" | "CONFIRMATION" | "UPDATE" | "DECLINE" | "SUBMISSION" | "NONE",
  "confidence": 0.0-1.0,
  "extracted": [{{"<field_name>": "<value>"}}] or []
}}"""
