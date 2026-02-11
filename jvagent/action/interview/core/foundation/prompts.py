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

REVIEW_CONFIRMATION_DIRECTIVE = """Prompt: Here's what I have so far:

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

CLASSIFICATION_RULES_CORE = """Chain of verification: (1) Apply checks below in order. (2) Then extract. (3) Output only the JSON object.

PRIORITY (check first):
- If any field the user is referring to is in Unanswered fields → intent is SUBMISSION (not UPDATE), regardless of wording.

STATE:
- current_state "review": CONFIRMATION = pure affirmation, no new values; "no" = UPDATE. Affirmatives with a new value = UPDATE.
- current_state "active": Answering the current question = SUBMISSION (including "yes"/"no"). CONFIRMATION is invalid in active.

INTENTS (choose one):
1. CANCELLATION – user abandons entire process ("cancel", "abort", "stop").
2. CONFIRMATION – only in review; "yes"/"correct"/"looks good" with no new values.
3. SUBMISSION – answering an unanswered question. Field in Unanswered fields → SUBMISSION. Invalid/wrong-format answers still SUBMISSION (validation handles them).
4. UPDATE – changing an already answered field. Field must be in Answered fields and not in Unanswered fields. In review, "no" or giving a new value = UPDATE.
5. DECLINE – user declines optional question or optional content. Use when: explicit refusal ("skip", "I'd rather not"); or "No" to optional uploads/content (photos, attachments); or router says "declines to..." / "refuses to...". Only [OPTIONAL] in Unanswered fields; [REQUIRED] refusal = NONE. Field: from Unanswered fields, or from Recent conversation (current question), or "unknown_field".
6. NONE – no clear intent or refusal of a required field.

"NO" DISAMBIGUATION (use Recent conversation and router): "No" to optional content (photos, attachments) → DECLINE. "No" as answer to a yes/no question (e.g. "Is this sensitive?") → SUBMISSION with that field = "no". "No" in review → UPDATE.

EXTRACTION:
- SUBMISSION: Map response to Unanswered fields. Use Recent conversation to identify which question was just asked. "Yes"/"Yeah"/"Sure"/"Ok" → "yes"; "No"/"Nope" → "no". Always output at least one field–value pair when the response clearly answers a listed question. For partials ("the first one", "9am") use Recent conversation to resolve to full values.
- UPDATE: Set "field" and "value".
- DECLINE: Set "field" (name from Unanswered fields or Recent conversation, or "unknown_field").

OUTPUT: Single JSON object. Include "reasoning" (optional one-line) only if helpful. For SUBMISSION put each extracted field as a top-level key with its value (same level as intent, confidence, field, value)."""

# =============================================================================
# Classification Prompts - Template Variants
# =============================================================================

# Interview Prompt - Full template with context formatting
INTERVIEW_PROMPT = f"""Classify intent and extract field values from user input.

USER INPUT (router interpretation or raw utterance):
{{user_input}}

CONTEXT:
- Current state: {{current_state}}
- Answered fields (with current values): {{answered_fields}}
- Unanswered fields: {{entities_to_extract}}
- Recent conversation (use to identify current question and resolve "yes"/"no" and references):
{{conversation_history}}

{CLASSIFICATION_RULES_CORE}

Return a single JSON object only. No markdown or explanation. Required keys: intent, confidence, field (or null), value (or null). For SUBMISSION add one or more top-level keys with field names from Unanswered fields and their extracted values. Optional key: reasoning (one line).
{{{{
  "intent": "CANCELLATION" | "CONFIRMATION" | "UPDATE" | "DECLINE" | "SUBMISSION" | "NONE",
  "confidence": 0.0-1.0,
  "field": "field_name" | null,
  "value": "value" | null,
  "<field_name>": "<extracted_value>"
}}}}"""
