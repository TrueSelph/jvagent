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
        - INTERVIEW_CLASSIFICATION_SIGNATURE: DSPy signature wrapper
    
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

CLASSIFICATION_RULES_CORE = """CRITICAL RULE - CHECK THIS FIRST:
    - If ANY field mentioned by the user appears in entities_to_extract (unanswered questions), classify as SUBMISSION, NOT UPDATE
    - This rule takes precedence over all other classification logic
    - Example: If user says "yes" and the target field is in entities_to_extract, it's SUBMISSION even if language suggests update

    INTENT TYPES (priority order):
    1. CANCELLATION - User explicitly abandons entire process
       - Only use if language explicitly abandons entire interview (e.g., "cancel", "abort", "stop the interview")

    2. CONFIRMATION - Only in REVIEW state, positive affirmation WITHOUT providing new values
       - CRITICAL STATE CHECK: CONFIRMATION can ONLY be used when current_state is "review"
       - If current_state is NOT "review", then affirmative responses (like "yes", "sure", "ok") to questions should be classified as SUBMISSION, NOT CONFIRMATION
       - When answering a question in ACTIVE state, it is always SUBMISSION, even if the answer is "yes"
       - "No" in REVIEW state = UPDATE (rejecting confirmation)
       - CRITICAL: Do NOT classify as CONFIRMATION if user provides a specific value that differs from current stored values
       - CONFIRMATION is ONLY for pure affirmations like "yes", "correct", "looks good", "that's right" WITHOUT any new values, AND only when current_state is "review"

    3. SUBMISSION - Providing answers to unanswered questions
       - CRITICAL PRIORITY: If a field appears in entities_to_extract (unanswered questions), classify as SUBMISSION, NOT UPDATE
       - This applies even if user language suggests "change" or "update" - if the field is unanswered, it's SUBMISSION
       - CRITICAL STATE RULE: When current_state is "active" and user is answering a question, it is always SUBMISSION, even if the answer is "yes", "sure", "ok", or other affirmative words
       - Affirmative responses to questions in ACTIVE state are SUBMISSION, not CONFIRMATION (CONFIRMATION is only for REVIEW state)
       - Extract field values from user_input (interpretation already contains values)
       - Includes invalid choices/values - validation system will mark them as INVALID and provide feedback
       - When user provides a value that doesn't match constraints → SUBMISSION
       - When user selects an option that doesn't exist → SUBMISSION
       - When user provides wrong type/format → SUBMISSION

    4. UPDATE - Change specific answered field (e.g., "change email", "wrong", "not correct", providing new value)
       - ONLY use UPDATE if the field is in answered_fields (already answered) AND NOT in entities_to_extract
       - In REVIEW state, "no" = UPDATE
       - Identify field name and optionally new value
       - AFFIRMATIVE WORDS: Words like "Ok", "yes", "sure" before a new value do NOT make it CONFIRMATION - they're just conversational fillers
       - CRITICAL: In REVIEW state, if user provides a specific value, classify as UPDATE (even if prefixed with "Ok", "yes", etc.)
       - INTERPRETATION AWARENESS: Even if router interpretation says "confirms", if user provides a value, classify as UPDATE

    5. DECLINE - User explicitly refuses to answer an optional question (only non-required fields)
       - Only use when user explicitly declines to answer (e.g., "I don't want to answer", "skip this", "I'd rather not provide that", "I'd prefer not to say")
       - Must specify field name (use active question from entities_to_extract)
       - CRITICAL: Invalid choices/values should be SUBMISSION, not DECLINE. If user provides a value that doesn't match constraints, selects an invalid option, or provides wrong type/format, classify as SUBMISSION (validation will handle them as INVALID)

    6. NONE - No clear intent

    EXTRACTION:
    - For SUBMISSION: Extract field-value pairs from user_input (interpretation has values)
    - For UPDATE: Use "field" and "value" keys
    - Map values to field names from entities_to_extract
    - CRITICAL: When SUBMISSION intent is detected, ALWAYS extract at least one field-value pair if there's a question in entities_to_extract that matches the response context

    HANDLING SIMPLE AFFIRMATIVE RESPONSES (CRITICAL):
    - When user responds with simple affirmative responses like "Yes", "No", "Sure", "Ok", "Yeah", "Yep", "Nope" in ACTIVE state, extract it as a value for the question that was most recently asked
    - Use conversation_history to determine which field is being answered - check the most recent AI question/statement to identify the field
    - For yes/no questions: Map "Yes"/"yes"/"Yeah"/"Yep"/"Sure"/"Ok" → "yes" and "No"/"no"/"Nope" → "no"
    - If the question has specific options, map the response to the appropriate option value
    - If the response is ambiguous, use conversation_history to match the response to the most recently asked question in entities_to_extract
    - Example: If AI asked "Would you like to keep the report private?" and user responds "Yes", extract as the appropriate field-value pair for the privacy-related field in entities_to_extract

    CONTEXT-AWARE EXTRACTION (CRITICAL):
    - Use conversation_history to resolve fragmentary references to full values
    - When user provides partial values, pronouns, or references (e.g., "the first one", "that option", "same as before", "9am"), match them to complete values from recent conversation history
    - If the AI recently presented options, lists, or specific values, resolve user fragments to the full matching option/value from those recent mentions
    - Examples of fragment resolution:
      * Partial value ("9am") → full matching option ("Monday 9:00 AM - 11:00 AM") when that option was recently presented
      * Ordinal reference ("the first one") → full value from first option/item in recent AI response
      * Demonstrative reference ("that option", "this one") → full value from recently mentioned options
      * Temporal reference ("same as before", "like last time") → previously mentioned value from conversation history
      * Partial match (e.g., "John" when "John Smith" was mentioned) → full matching value from context
      * Simple affirmative ("Yes" to a yes/no question) → appropriate yes/no value for the target field in entities_to_extract
    - Extract complete, contextually appropriate entities rather than just literal fragments
    - When conversation_history is available, prioritize resolving fragments to full values over extracting partial fragments"""

# =============================================================================
# Classification Prompts - Template Variants
# =============================================================================

# DSPy Signature - Lightweight wrapper around core rules
INTERVIEW_CLASSIFICATION_SIGNATURE = f"""Classify user intent and extract field values from interview context.

    The user_input contains router interpretation with reasoning and extracted values.
    Focus on interview-specific state-aware logic and field mapping.

{CLASSIFICATION_RULES_CORE}

    Return JSON with intent, confidence, field (for UPDATE/DECLINE), value (for UPDATE),
    and extracted_data (for SUBMISSION) as field-value pairs.
    """

# Interview Prompt - Full template with context formatting
INTERVIEW_PROMPT = f"""Classify intent and extract field values from user input.

USER INPUT (contains router interpretation with reasoning):
{{user_input}}

CONTEXT:
- Current state: {{current_state}}
- Answered fields: {{answered_fields}}
- Unanswered fields: {{entities_to_extract}}
- Required fields: {{required_fields_info}}

CRITICAL: Before classifying intent, check if ANY field mentioned by the user appears in "Unanswered fields" above. If it does, classify as SUBMISSION, NOT UPDATE, regardless of the user's language.

{CLASSIFICATION_RULES_CORE}

Return JSON:
{{{{
  "intent": "CANCELLATION" | "CONFIRMATION" | "UPDATE" | "DECLINE" | "SUBMISSION" | "NONE",
  "confidence": 0.0-1.0,
  "field": "field_name" | null,
  "value": "value" | null,
  // For SUBMISSION: include field-value pairs
  // For DECLINE: field must be specified
}}}}"""
