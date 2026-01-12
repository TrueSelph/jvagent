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
    
    INTENT TYPES (priority order):
    1. CANCELLATION - User explicitly abandons entire process
       - Only use if language explicitly abandons entire interview (e.g., "cancel", "abort", "stop the interview")
    
    2. CONFIRMATION - Only in REVIEW state, positive affirmation
       - "No" in REVIEW state = UPDATE (rejecting confirmation)
    
    3. UPDATE - Change specific answered field
       - In REVIEW state, "no" = UPDATE
       - Identify field name and optionally new value
    
    4. DECLINE - User explicitly refuses to answer an optional question (only non-required fields)
       - Only use when user explicitly declines to answer (e.g., "I don't want to answer", "skip this", "I'd rather not provide that", "I'd prefer not to say")
       - Must specify field name (use active question from entities_to_extract)
       - CRITICAL: Invalid choices/values should be SUBMISSION, not DECLINE. If user provides a value that doesn't match constraints, selects an invalid option, or provides wrong type/format, classify as SUBMISSION (validation will handle them as INVALID)
    
    5. SUBMISSION - Providing answers to unanswered questions
       - Extract field values from user_input (interpretation already contains values)
       - Includes invalid choices/values - validation system will mark them as INVALID and provide feedback
       - When user provides a value that doesn't match constraints → SUBMISSION
       - When user selects an option that doesn't exist → SUBMISSION
       - When user provides wrong type/format → SUBMISSION
    
    6. NONE - No clear intent
    
    EXTRACTION:
    - For SUBMISSION: Extract field-value pairs from user_input (interpretation has values)
    - For UPDATE: Use "field" and "value" keys
    - Map values to field names from entities_to_extract
    
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

INTENT CLASSIFICATION (priority order):
1. CANCELLATION - User explicitly abandons entire process (e.g., "cancel", "abort", "stop the interview")
   - Only use CANCELLATION if language explicitly abandons the entire interview

2. CONFIRMATION - Only in REVIEW state, positive affirmation (e.g., "yes", "correct", "looks good")
   - "No" in REVIEW state = UPDATE (rejecting confirmation)

3. UPDATE - Change specific answered field (e.g., "change email", "wrong", "not correct")
   - In REVIEW state, "no" = UPDATE
   - Identify field name and optionally new value

4. DECLINE - User explicitly refuses to answer an optional question (only for non-required fields)
   - Only use when user explicitly declines to answer (e.g., "I don't want to answer", "skip this", "I'd rather not provide that", "I'd prefer not to say")
   - Must specify field name (use active question from entities_to_extract)
   - CRITICAL: Invalid choices/values should be SUBMISSION, not DECLINE. If user provides a value that doesn't match constraints, selects an invalid option, or provides wrong type/format, classify as SUBMISSION (validation will handle them as INVALID)

5. SUBMISSION - Providing answers to unanswered questions
   - Extract field values from user input (interpretation already contains values)
   - Includes invalid choices/values - validation system will mark them as INVALID and provide feedback
   - When user provides a value that doesn't match constraints → SUBMISSION
   - When user selects an option that doesn't exist → SUBMISSION
   - When user provides wrong type/format → SUBMISSION

6. NONE - No clear intent

EXTRACTION:
- For SUBMISSION: Extract field-value pairs from user input (interpretation already has values)
- For UPDATE: Use "field" and "value" keys
- Map extracted values to field names from entities_to_extract

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
