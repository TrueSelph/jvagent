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

import re

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

# Aliases for test compatibility
CANCELLATION_EVENT_MESSAGE_TEMPLATE = STATE_EVENT_MESSAGES["CANCELLED"]
CANCELLATION_MESSAGE_TEMPLATE = CANCELLATION_MESSAGE


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
# Classification Prompts - Composed Sections for Structured Reasoning
# =============================================================================

CLASSIFICATION_REASONING_INSTRUCTIONS = """REASONING PROCESS (required structured output):
You must follow this step-by-step reasoning process before classifying and extracting. Output your reasoning in the "reasoning" object.

1. USER SAID: Identify exactly what the user said. Note current state first; CONFIRMATION is only valid when state=review.
2. REFERENCES: Check if the user's response contains references (e.g., "the second option", "Wednesday afternoon", "that one") that need resolution against conversation history or context data.
3. COMPOSITION: Check if the user's current response is a partial value that should be composed with a previous fragment from conversation history (e.g., "Smith" following "John" for full_name).
4. INTENT: Determine the user's intent based on state and response content.
5. EXTRACTION: Extract values using the appropriate mode (verbatim, normalized, or select) based on field type.
6. VERIFICATION: Verify the extracted value genuinely satisfies the field's expected content description."""

CLASSIFICATION_DECISION_ORDER = """DECISION ORDER:

1. CANCELLATION (any state)
   - If the utterance contains cancellation intent, return CANCELLATION.
   - This takes absolute priority, even if extractable content is present.
2. CONFIRMATION (only if state = REVIEW)
   - If the utterance is a pure affirmation (e.g., "yes", "confirm", "looks good"), return CONFIRMATION.
3. SUBMISSION
   - If the utterance provides a valid answer to an UNANSWERED field, return SUBMISSION.
   - This takes priority over UPDATE to prevent overwriting when multiple values are being collected.
4. UPDATE
   - If the utterance provides a value that matches or closely resembles an ALREADY-ANSWERED field, return UPDATE.
     • Applies even without explicit change language
     • Includes corrected, extended, or reformatted values
   - If explicit change language is used and the field exists in Answered, return UPDATE.
5. DECLINE
   - If the utterance expresses refusal and the field is OPTIONAL, return DECLINE.
6. NONE
   - If none of the above conditions are met, return NONE."""

CLASSIFICATION_INTENT_RULES = """INTENT CLASSIFICATION (choose exactly one):

1. CANCELLATION
   - User abandons the interview process
   - Patterns: "cancel", "abort", "stop", "never mind", "forget it"
   - Valid in any state

2. CONFIRMATION (REVIEW STATE ONLY)
   - Pure affirmation with NO new field values
   - User confirms reviewed responses are correct
   - Expanded patterns: "yes", "correct", "looks good", "that's right", "yep", "yeah", "all correct",
     "everything is correct", "looks fine", "looks fine to me", "that's fine", "confirmed",
     "all good", "perfect", "exactly", "that's all correct"
   - CRITICAL: In active state, "yes"/"no" are SUBMISSION (answers to question), NOT CONFIRMATION
   - CRITICAL: If user provides ANY new value, it's UPDATE or SUBMISSION, not CONFIRMATION

3. SUBMISSION
   - User answers an UNANSWERED question
   - Field MUST be in Unanswered fields list
   - CRITICAL: "yes"/"no" responses in active state
     * If field description expects "yes or no" answers ? SUBMISSION
     * If field is [OPTIONAL] and no yes/no expectation ? DECLINE (user declining, not answering)
   - CRITICAL: Before classifying as SUBMISSION, ALWAYS check if the value better matches an Answered field (→ UPDATE)
   - Invalid format is still SUBMISSION (validation layer handles it)
   - When the utterance is a bare value matching an unanswered field's type (email format for email field, digits for phone, etc.), treat as SUBMISSION and extract the value. Do not ask for clarification.

4. UPDATE
   - User changes, corrects, or re-provides a value for an **ALREADY-ANSWERED field within 'Answered fields' only**
   - Field must be in Answered fields
   - DOES NOT require explicit change language
   - Trigger UPDATE when:
     - Value closely matches or extends an existing answered field
     - User provides a more complete version (e.g., phone number now includes country code)
     - User corrects spelling, formatting, or adds missing components
   - Examples:
     - Old: "6235678" → New: "592-623-5678" → UPDATE
     - Old: "John" → New: "John Smith" → UPDATE
   - Bare values CAN be UPDATE if they align with an Answered field

5. DECLINE
   - User explicitly refuses to answer an optional or required field
   - CRITICAL: Check if field is marked [OPTIONAL] in Unanswered fields
   - When field is [OPTIONAL] and user says "no", "nope", "n/a", "none", "skip" → DECLINE (unless field expects yes/no answers)
   - Explicit refusal patterns: "I won't answer", "skip", "I'd rather not", "no thanks", "I prefer not to"
   - "No" to optional content (photos, attachments, phone numbers, addresses) → DECLINE
   - EXCEPTION: "No" as answer to yes/no question (field description contains "yes or no", "yes/no", or similar) → SUBMISSION

6. NONE
   - Use when (a) meta-request or greeting with no extractable content, (b) off-topic, or (c) ambiguous with no viable extraction
   - Meta-requests (e.g., "I want to make a report"), greetings, or off-topic content
   - Do NOT use for "no" (→ SUBMISSION or DECLINE) or explicit refusal (→ DECLINE)"""

CLASSIFICATION_EXTRACTION_RULES = """EXTRACTION MODES (field-type aware):

The Unanswered fields list includes extraction mode hints in brackets. Use these modes:

1. [verbatim] MODE
   - For fields with "description", "narrative", "details", "incident", "explain", "describe", "story", "account", "report" in their description
   - Preserve the user's FULL response exactly as stated
   - Do NOT summarize, truncate, normalize, or paraphrase
   - Capture multi-sentence responses completely
   - Example: incident_description expects full narrative → extract entire user statement verbatim

2. [normalized] MODE
   - For structured fields: email, phone, name, address, date
   - Normalize formatting (trim whitespace, fix casing) but preserve semantic content
   - Example: "  john@EXAMPLE.com  " → "john@example.com"

3. [select] MODE
   - For fields with "Options:" in their Unanswered fields entry
   - Match user's response to closest valid option from the list
   - Handle partial matches and references (see Reference Resolution)

FIELD NAME CONSTRAINT: You may ONLY extract for fields that appear in the Unanswered fields list. The field_name in each extracted entry MUST exactly match one of the field names listed (e.g., incident_description, incident_location). Do NOT invent, hallucinate, or use field names not in the list.

A single utterance may satisfy multiple Unanswered fields; extract each when the mapping is clear."""

CLASSIFICATION_META_EXTRACTION = """META EXTRACTION FROM VERBATIM:

When a [verbatim] extraction captures a full description or narrative, the content may contain enough information to satisfy OTHER unanswered fields. You may extract additional fields from within that verbatim content:

1. SOURCE: The meta value must be a direct extraction from the verbatim content — a verbatim substring or clearly stated information. Do NOT infer, speculate, or hallucinate.
2. TARGET: Only meta-extract for fields that are in Unanswered fields.
3. EXAMPLE: User says "There's a pothole on Water Street near Oak. It's been there for weeks."
   - incident_description [verbatim]: full text (primary extraction)
   - incident_location [normalized]: "Water Street near Oak" (meta from verbatim content)
4. WHEN TO META-EXTRACT: Only when the verbatim content unambiguously contains information satisfying another unanswered field's expected content."""

CLASSIFICATION_REFERENCE_RESOLUTION = """REFERENCE RESOLUTION:

When the user's response contains a reference rather than a literal value, resolve it BEFORE extracting:

1. ORDINAL REFERENCES
   - "the first one", "the second option", "option 3", "number 2"
   - Resolve against the most recent list of options in conversation history or Options: in Unanswered fields
   - Example: If Options: "Monday 9AM, Monday 2PM, Wednesday 9AM" and user says "the second one" → extract "Monday 2PM"

2. TEMPORAL REFERENCES
   - "Wednesday afternoon", "the morning one", "the 2pm slot"
   - Resolve against available times/slots in context or Options
   - Example: If Options include time slots and user says "Wednesday afternoon" → match to specific Wednesday PM slot

3. ANAPHORIC REFERENCES
   - "that one", "same as before", "the one I mentioned", "it"
   - Resolve from conversation history
   - Look back at what the user or assistant most recently mentioned

4. COMPOSITION WITH HISTORY: See MULTI-TURN VALUE COMPOSITION for composition rules."""

CLASSIFICATION_COMPOSITION_RULES = """MULTI-TURN VALUE COMPOSITION:

When a user provides partial values across multiple turns:

1. CHECK CONVERSATION HISTORY
   - Look at the numbered conversation history turns
   - Identify if the user previously provided a partial value for the current unanswered field

2. COMPOSITION CONDITIONS
   - Field must be in Unanswered fields (still not satisfied)
   - Previous turn shows assistant re-asking the same question
   - Previous turn shows user provided partial response
   - Current response is also partial but complementary

3. COMPOSE THE VALUE
   - Combine the fragments into a complete value
   - Example: "John" (turn 2) + "Smith" (turn 4) → "John Smith"
   - Example: "123 Main" (turn 2) + "Street, Boston" (turn 4) → "123 Main Street, Boston"

4. DO NOT COMPOSE IF
   - Field is already answered (in Answered fields)
   - Previous value was for a different field
   - Current response is complete by itself"""

CLASSIFICATION_VERIFICATION = """VERIFICATION CHECKLIST (Chain of Verification):

Before finalizing your classification and extraction, verify:

1. VALUE MATCH: Does the extracted value genuinely match the field's expected content description?
   - Not just mentions the topic, but provides actual informational content
   - Example: "I want to report an incident" does NOT satisfy "incident_description" (it's meta-request)
   - Example: "There was a pothole on Main Street" DOES satisfy "incident_description"

2. INTENT CONSISTENCY: Is the intent consistent with the current state?
   - CONFIRMATION only valid in review state
   - CONFIRMATION requires NO new field values
   - SUBMISSION only for unanswered fields
   - UPDATE only for answered fields with explicit change language

3. REFERENCES RESOLVED: Did I resolve all references?
   - Ordinal, temporal, anaphoric references converted to literal values

4. CORRECT FIELD: Am I extracting for the right field?
   - SUBMISSION: field in Unanswered fields
   - UPDATE: field in Answered fields
   - field_name must exactly match a listed field (Unanswered for SUBMISSION/DECLINE, Answered for UPDATE). No invented or hallucinated field names.

5. NO DISAMBIGUATION: For "no" responses, follow this decision tree:
   STEP 1: Check field description for yes/no question indicators
      - Does description say "yes or no", "yes/no", "true or false"? → SUBMISSION with value "no"
   STEP 2: Check if field is [OPTIONAL]
      - Is field marked [OPTIONAL] in Unanswered fields? → DECLINE (user declining to provide optional info)
   STEP 3: Check if field is [REQUIRED] with explicit refusal context
      - "no thanks", "I'd rather not", "skip" → DECLINE
   STEP 4: Default for [REQUIRED] fields with bare "no"
      - If unclear and field is [REQUIRED] → SUBMISSION (benefit of doubt for data capture)"""

CLASSIFICATION_OUTPUT_FORMAT = """OUTPUT FORMAT:

Return a single JSON object with structured reasoning:

{
  "reasoning": {
    "user_said": "Brief summary of user's response and current state",
    "references_resolved": "Any references resolved (or 'none')",
    "composition_applied": "Any multi-turn composition applied (or 'none')",
    "intent_rationale": "Why this intent was chosen",
    "extraction_mode": "verbatim/normalized/select (or 'none' if no extraction)",
    "verification": "Verification check results"
  },
  "intent": "CANCELLATION|CONFIRMATION|UPDATE|DECLINE|SUBMISSION|NONE",
  "confidence": 0.0-1.0,
  "extracted": [{"field_name": "value"}] or []
}

Confidence: Use lower (0.5-0.7) when intent or extraction is ambiguous; use high (0.9+) when clear.

CRITICAL:
- ALL field data goes in "extracted" as list of one-key objects
- Do NOT include "field" or "value" keys at top level
- DECLINE with identified field: [{"field_name": "N/A"}]
- No extractions: extracted: []
- reasoning object is REQUIRED (not optional)
- field_name in each extracted entry MUST exactly match a field from Unanswered fields (SUBMISSION, DECLINE) or Answered fields (UPDATE). Entries with unknown field names are invalid and will be discarded."""

CLASSIFICATION_EXAMPLES = """EXAMPLES:

Example 1 - Long-form verbatim extraction:
User: "There's a huge pothole on Water Street near the intersection with Oak. It's been there for weeks and caused two flat tires that I know of. The hole is about 2 feet wide and 8 inches deep."
Unanswered fields: incident_description [verbatim] — Expected: "Detailed description..."
{
  "reasoning": {
    "user_said": "User provided detailed incident description in active state",
    "references_resolved": "none",
    "composition_applied": "none",
    "intent_rationale": "Answering unanswered question (incident_description)",
    "extraction_mode": "verbatim",
    "verification": "Full narrative provided, matches field description exactly"
  },
  "intent": "SUBMISSION",
  "confidence": 0.95,
  "extracted": [{"incident_description": "There's a huge pothole on Water Street near the intersection with Oak. It's been there for weeks and caused two flat tires that I know of. The hole is about 2 feet wide and 8 inches deep."}]
}

Example 1b - Verbatim + meta extraction (multiple fields from same utterance):
User: "There's a pothole on Water Street near Oak. It's been there for weeks."
Unanswered fields: incident_description [verbatim], incident_location [normalized]
{
  "reasoning": {
    "user_said": "User provided incident description containing location",
    "references_resolved": "none",
    "composition_applied": "none",
    "intent_rationale": "Answering unanswered questions; verbatim content also satisfies incident_location",
    "extraction_mode": "verbatim + meta",
    "verification": "Full narrative for incident_description; 'Water Street near Oak' directly extracted for incident_location"
  },
  "intent": "SUBMISSION",
  "confidence": 0.9,
  "extracted": [
    {"incident_description": "There's a pothole on Water Street near Oak. It's been there for weeks."},
    {"incident_location": "Water Street near Oak"}
  ]
}

Example 2 - Multi-turn composition:
Conversation: [1] assistant: What's your full name? [2] user: John [3] assistant: Please provide both first and last name. [4] user: Smith
Unanswered fields: user_name [normalized] — Expected: "Full name"
{
  "reasoning": {
    "user_said": "User provided last name only ('Smith') in active state",
    "references_resolved": "none",
    "composition_applied": "Composed 'John' (turn 2) with 'Smith' (turn 4) for user_name",
    "intent_rationale": "Completing partial answer for unanswered field user_name",
    "extraction_mode": "normalized",
    "verification": "Combined fragments form valid full name"
  },
  "intent": "SUBMISSION",
  "confidence": 0.9,
  "extracted": [{"user_name": "John Smith"}]
}

Example 3 - Reference resolution:
Conversation: [1] assistant: Available times: Monday 9AM-11AM, Monday 2PM-4PM, Wednesday 9AM-11AM [2] user: the second option
Unanswered fields: available_times [select] — Options: Monday 9AM-11AM, Monday 2PM-4PM, Wednesday 9AM-11AM
{
  "reasoning": {
    "user_said": "User selected using ordinal reference ('the second option')",
    "references_resolved": "Resolved 'the second option' to 'Monday 2PM-4PM' from Options list",
    "composition_applied": "none",
    "intent_rationale": "Answering unanswered question with resolved reference",
    "extraction_mode": "select",
    "verification": "Resolved reference matches valid option"
  },
  "intent": "SUBMISSION",
  "confidence": 0.95,
  "extracted": [{"available_times": "Monday 2PM-4PM"}]
}

Example 4 - CONFIRMATION in review:
State: review, User: "yep that all looks good"
Answered fields: user_name, email, phone
{
  "reasoning": {
    "user_said": "User affirmed review with 'yep that all looks good'",
    "references_resolved": "none",
    "composition_applied": "none",
    "intent_rationale": "Pure affirmation in review state with no new values provided",
    "extraction_mode": "none",
    "verification": "No new field values, state is review, affirmation pattern matched"
  },
  "intent": "CONFIRMATION",
  "confidence": 0.95,
  "extracted": []
}

Example 5 - 'no' disambiguation:
Conversation: [1] assistant: Are you submitting on behalf of someone else? [2] user: no
Unanswered fields: reporting_on_behalf [REQUIRED] [normalized] — Expected: "yes or no"
{
  "reasoning": {
    "user_said": "User answered 'no' to yes/no question in active state",
    "references_resolved": "none",
    "composition_applied": "none",
    "intent_rationale": "Answering yes/no question with 'no' value, not declining",
    "extraction_mode": "normalized",
    "verification": "Direct answer to yes/no question, satisfies field description"
  },
  "intent": "SUBMISSION",
  "confidence": 0.95,
  "extracted": [{"reporting_on_behalf": "no"}]
}

Example 6 - CANCELLATION:
State: active, User: "never mind, cancel"
Unanswered fields: user_name, user_email
{
  "reasoning": {
    "user_said": "User requested cancellation with 'never mind, cancel'",
    "references_resolved": "none",
    "composition_applied": "none",
    "intent_rationale": "Explicit cancellation language, valid in any state",
    "extraction_mode": "none",
    "verification": "Cancellation pattern matched, no extraction"
  },
  "intent": "CANCELLATION",
  "confidence": 0.95,
  "extracted": []
}

Example 7 - UPDATE in review:
State: review, User: "actually change my email to jane@example.com"
Answered fields: user_name: Jane Doe, user_email: jane.doe@old.com
{
  "reasoning": {
    "user_said": "User requested change with explicit 'change my email to' language",
    "references_resolved": "none",
    "composition_applied": "none",
    "intent_rationale": "Explicit change language, field in Answered fields",
    "extraction_mode": "normalized",
    "verification": "Change pattern matched, user_email is in Answered fields"
  },
  "intent": "UPDATE",
  "confidence": 0.95,
  "extracted": [{"user_email": "jane@example.com"}]
}"""

# Composed classification rules (built dynamically via build_classification_rules())
# Legacy constant kept for backward compatibility - use build_classification_rules() instead
CLASSIFICATION_RULES_CORE_LEGACY = """CLASSIFICATION AND EXTRACTION INSTRUCTIONS

{reasoning_instructions}

{intent_rules}

{extraction_rules}

{reference_resolution}

{composition_rules}

{verification}

{output_format}

{examples}"""

# =============================================================================
# Classification Prompts - Template Variants
# =============================================================================

# Interview Prompt - Full template with context formatting (use .format(classification_rules_core=..., ...))
INTERVIEW_PROMPT = """You are a classification and extraction module. Your ONLY output is a single JSON object. NEVER output natural language, questions, prompts, or dialogue.

USER INPUT:
- Interpretation (if present): Router's description of what the user is doing. Does NOT contain the field value.
- User's utterance: The ACTUAL user message. This is where you extract values from.

{user_input}

EXTRACT FROM THE UTTERANCE ONLY. Extract values ONLY from the User's utterance, never from Interpretation. When the utterance is a bare value (e.g. "john@gmail.com", "555-1234") that matches an unanswered field's expected format, classify SUBMISSION and extract it directly.

CONTEXT:
- Current state: {current_state}
- Current question (first unanswered): {current_question}
- Answered fields (with current values): {answered_fields}
- Unanswered fields: {entities_to_extract}
- Use the conversation history in the preceding messages to identify the current question, resolve "yes"/"no" and references, and for multi-turn composition.

{classification_rules_core}

OUTPUT: Valid JSON only. No markdown, no explanation, no conversational text. Output must match the structure in OUTPUT FORMAT below exactly."""


def build_classification_rules(
    include_reasoning: bool = True,
    include_examples: bool = True,
    include_reference_resolution: bool = True,
    include_composition: bool = True,
    max_examples: int = 5,
) -> str:
    """Build classification rules prompt from composed sections.

    This builder allows conditional inclusion of sections to manage token budget
    and customize behavior based on configuration.

    Args:
        include_reasoning: Include reasoning instructions section
        include_examples: Include few-shot examples section
        include_reference_resolution: Include reference resolution section
        include_composition: Include multi-turn composition section
        max_examples: Maximum number of examples to include (if include_examples=True)

    Returns:
        Composed classification rules string ready for use in INTERVIEW_PROMPT
    """
    sections = []

    # Always include decision order and intent rules (core functionality)
    sections.append(CLASSIFICATION_DECISION_ORDER)
    sections.append(CLASSIFICATION_INTENT_RULES)
    sections.append(CLASSIFICATION_EXTRACTION_RULES)
    sections.append(CLASSIFICATION_META_EXTRACTION)

    # Optional: Reasoning instructions
    if include_reasoning:
        sections.insert(0, CLASSIFICATION_REASONING_INSTRUCTIONS)

    # Optional: Reference resolution
    if include_reference_resolution:
        sections.append(CLASSIFICATION_REFERENCE_RESOLUTION)

    # Optional: Multi-turn composition
    if include_composition:
        sections.append(CLASSIFICATION_COMPOSITION_RULES)

    # Always include verification (best practice)
    sections.append(CLASSIFICATION_VERIFICATION)

    # Always include output format
    sections.append(CLASSIFICATION_OUTPUT_FORMAT)

    # Optional: Examples (token-expensive but improves accuracy)
    if include_examples:
        examples_text = _get_classification_examples(max_examples)
        sections.append(examples_text)

    return "\n\n".join(sections)


def _get_classification_examples(max_examples: int) -> str:
    """Return classification examples, limited to first max_examples blocks.

    Splits CLASSIFICATION_EXAMPLES by 'Example N - ' pattern and returns
    the header plus the first max_examples example blocks.

    Args:
        max_examples: Maximum number of examples to include

    Returns:
        Examples string with at most max_examples blocks
    """
    parts = re.split(r"(?=Example \d+b? - )", CLASSIFICATION_EXAMPLES)
    # parts[0] = "EXAMPLES:\n\n", parts[1:] = "Example 1 - ...", "Example 1b - ...", etc.
    header = parts[0] if parts else ""
    example_blocks = [p for p in parts[1:] if p.strip()]
    selected = example_blocks[:max_examples]
    return header + "\n\n".join(selected) if selected else header
