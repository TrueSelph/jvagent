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
REVIEW_CONFIRMATION_CONTENT = """Prompt: Here's what I have so far:

{summary}

{instructions}

{prompt}"""

# Unclear edit content template
# Used when user wants to update but hasn't specified which field
# Placeholders: {summary} (formatted summary of current information), {field_list} (comma-separated list of fields)
REVIEW_UNCLEAR_EDIT_CONTENT = """Got it, you'd like to make some changes. Here's what I have:

{summary}

Which one would you like to change? I can update: {field_list}"""

# Unclear general content (static, no placeholders)
REVIEW_UNCLEAR_GENERAL_CONTENT = """Prompt: I'm not quite sure what you meant there. Could you clarify what you'd like to do?"""

# Default values for review confirmation
REVIEW_CONFIRMATION_DEFAULT_INSTRUCTIONS = """Prompt: Just let me know if everything looks good, or tell me what you'd like to change. You can also let me know if you'd like to cancel altogether."""

REVIEW_CONFIRMATION_DEFAULT_PROMPT = """Ask: "Does everything look correct?" or similar phrasing to prompt their response."""

# Summary formatting templates (used to build summary for confirmation)
REVIEW_SUMMARY_HEADER_TEMPLATE = ""
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
COMPLETION_EVENT_MESSAGE_TEMPLATE = "Completed activity: {class_name}"

# Cancellation event message template (for CANCELLED state)
CANCELLATION_EVENT_MESSAGE_TEMPLATE = "interview process cancelled as part of {class_name}"

# Question directive template (for ACTIVE state - question prompting)
# Consolidated template that handles description, question, and optional instructions
# Instructions placeholder will be empty string if no instructions provided
QUESTION_DIRECTIVE_TEMPLATE = """Make a request to the user based on the following:
{question} ({description})

{instructions}
"""

# Required field decline template (for when user tries to decline a required field)
# Used to politely but firmly insist the user must provide an answer
# Placeholders: {field_display} (human-readable field name), {question} (original question text)
REQUIRED_FIELD_DECLINE_TEMPLATE = """Tell the user: I understand, but {field_display} is required to complete this process.

Ask: {question}"""

# DSPy Signature Docstring (single source of truth for InterviewClassification)
# This docstring is used by the InterviewClassification DSPy signature
# Can be overridden via action class attribute for runtime customization
INTERVIEW_CLASSIFICATION_SIGNATURE = """Classify user intent and extract field values from interview context.
    
    The user_input contains router interpretation with reasoning and extracted values.
    Focus on interview-specific state-aware logic and field mapping.
    
    CRITICAL RULE - CHECK THIS FIRST:
    - If ANY field mentioned by the user appears in entities_to_extract (unanswered questions), classify as SUBMISSION, NOT UPDATE
    - This rule takes precedence over all other classification logic
    - Example: If user says "yes" and "is_sensitive" is in entities_to_extract, it's SUBMISSION even if language suggests update
    
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
    - For yes/no questions with options ['yes', 'no']: Map "Yes"/"yes"/"Yeah"/"Yep"/"Sure"/"Ok" → "yes" and "No"/"no"/"Nope" → "no"
    - If the response is ambiguous, use conversation_history to match the response to the most recently asked question in entities_to_extract
    - Example: If AI asked "Would you like to keep the report private?" and user responds "Yes", extract as {"is_sensitive": "yes"} (assuming is_sensitive is in entities_to_extract)
    
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
      * Simple affirmative ("Yes" to "Would you like to keep it private?") → {"is_sensitive": "yes"} when is_sensitive is in entities_to_extract
    - Extract complete, contextually appropriate entities rather than just literal fragments
    - When conversation_history is available, prioritize resolving fragments to full values over extracting partial fragments
    
    Return JSON with intent, confidence, field (for UPDATE/DECLINE), value (for UPDATE),
    and extracted_data (for SUBMISSION) as field-value pairs.
    """

# Interview Prompt Template
# Simplified template that relies on router interpretation for reasoning and extraction.
# Focuses on interview-specific state-aware logic and field mapping.
INTERVIEW_PROMPT_TEMPLATE = """Classify intent and extract field values from user input.

USER INPUT (contains router interpretation with reasoning):
{user_input}

CONTEXT:
- Current state: {current_state}
- Answered fields: {answered_fields}
- Unanswered fields: {entities_to_extract}
- Required fields: {required_fields_info}

CRITICAL: Before classifying intent, check if ANY field mentioned by the user appears in "Unanswered fields" above. If it does, classify as SUBMISSION, NOT UPDATE, regardless of the user's language.

CRITICAL RULE - CHECK THIS FIRST:
- If ANY field mentioned by the user appears in entities_to_extract (unanswered questions), classify as SUBMISSION, NOT UPDATE
- This rule takes precedence over all other classification logic
- Example: If user says "yes" and "is_sensitive" is in entities_to_extract, it's SUBMISSION even if language suggests update

INTENT CLASSIFICATION (priority order):
1. CANCELLATION - User explicitly abandons entire process (e.g., "cancel", "abort", "stop the interview")
   - Only use CANCELLATION if language explicitly abandons the entire interview

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
   - Extract field values from user input (interpretation already contains values)
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

5. DECLINE - User explicitly refuses to answer an optional question (only for non-required fields)
   - Only use when user explicitly declines to answer (e.g., "I don't want to answer", "skip this", "I'd rather not provide that", "I'd prefer not to say")
   - Must specify field name (use active question from entities_to_extract)
   - CRITICAL: Invalid choices/values should be SUBMISSION, not DECLINE. If user provides a value that doesn't match constraints, selects an invalid option, or provides wrong type/format, classify as SUBMISSION (validation will handle them as INVALID)

6. NONE - No clear intent

EXTRACTION:
- For SUBMISSION: Extract field-value pairs from user input (interpretation already has values)
- For UPDATE: Use "field" and "value" keys
- Map extracted values to field names from entities_to_extract
- CRITICAL: When SUBMISSION intent is detected, ALWAYS extract at least one field-value pair if there's a question in entities_to_extract that matches the response context

HANDLING SIMPLE AFFIRMATIVE RESPONSES (CRITICAL):
- When user responds with simple affirmative responses like "Yes", "No", "Sure", "Ok", "Yeah", "Yep", "Nope" in ACTIVE state, extract it as a value for the question that was most recently asked
- Use conversation history to determine which field is being answered - check the most recent AI question/statement to identify the field
- For yes/no questions with options ['yes', 'no']: Map "Yes"/"yes"/"Yeah"/"Yep"/"Sure"/"Ok" → "yes" and "No"/"no"/"Nope" → "no"
- If the response is ambiguous, use conversation history to match the response to the most recently asked question in entities_to_extract
- Example: If AI asked "Would you like to keep the report private?" and user responds "Yes", extract as {"is_sensitive": "yes"} (assuming is_sensitive is in entities_to_extract)

CONTEXT-AWARE EXTRACTION (CRITICAL):
- Use conversation history to resolve fragmentary references to full values
- When user provides partial values, pronouns, or references (e.g., "the first one", "that option", "same as before", "9am"), match them to complete values from recent conversation history
- If the AI recently presented options, lists, or specific values, resolve user fragments to the full matching option/value from those recent mentions
- Examples of fragment resolution:
  * Partial value ("9am") → full matching option ("Monday 9:00 AM - 11:00 AM") when that option was recently presented
  * Ordinal reference ("the first one") → full value from first option/item in recent AI response
  * Demonstrative reference ("that option", "this one") → full value from recently mentioned options
  * Temporal reference ("same as before", "like last time") → previously mentioned value from conversation history
  * Partial match (e.g., "John" when "John Smith" was mentioned) → full matching value from context
  * Simple affirmative ("Yes" to "Would you like to keep it private?") → {"is_sensitive": "yes"} when is_sensitive is in entities_to_extract
- Extract complete, contextually appropriate entities rather than just literal fragments
- When conversation history is available, prioritize resolving fragments to full values over extracting partial fragments

Return JSON:
{{
  "intent": "CANCELLATION" | "CONFIRMATION" | "UPDATE" | "DECLINE" | "SUBMISSION" | "NONE",
  "confidence": 0.0-1.0,
  "field": "field_name" | null,
  "value": "value" | null,
  // For SUBMISSION: include field-value pairs
  // For DECLINE: field must be specified
}}"""
