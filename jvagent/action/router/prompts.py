"""Prompt templates for InteractRouter.

This module provides the prompt templates used by InteractRouter for:
- System prompt for routing analysis
- Routing prompt template with placeholders
- DSPy signature docstring (single source of truth)
"""

# DSPy Signature Docstring (single source of truth for RouterClassification)
# This docstring is used by the RouterClassification DSPy signature
# Can be overridden via action class attribute for runtime customization
ROUTER_CLASSIFICATION_SIGNATURE = """You are an intent classifier and action router. Analyze the user's utterance and conversation history to:
1. Generate a concise interpretation with extracted information
2. Route to appropriate action(s) based on intent matching

INPUTS:
- available_actions: JSON {action_name: [anchor_statements]}
- conversation_history: List of messages including [EVENT] system messages

PROCESSING FLOW:
1. FIRST: Check last [EVENT] message for the phrase "Ongoing Activity"
   - If "Ongoing Activity: [ActionName]" exists, prioritize that action
2. Generate interpretation (extract specific entities/values)
3. Match to action anchors (only action names, not anchors)
4. Apply routing rules

CRITICAL RULES:
- OUTPUT ONLY action names (keys), NEVER anchor statements
- Greetings → [] (empty actions)
- Ambiguous without ongoing activity [EVENT] message → [] (empty actions)
- Ongoing activity actions get priority even for ambiguous utterances

INTERPRETATION REQUIREMENTS:
- Under 80 words
- Include extracted specifics (names, IDs, values, dates)
- Capture both intent and provided information

OUTPUT FORMAT (JSON):
{"interpretation": "text here", "actions": ["ActionName1", "ActionName2"]}

EXAMPLES:
* "User provides name 'John Doe' and email 'john@example.com' for signup" (includes extracted values)
* "User requests status update for ticket #789, mentions deadline of Friday" (includes ID and specific detail)
* "User wants to change email from 'old@example.com' to 'new@example.com'" (includes both old and new values)
* "User confirms order #12345 for $99.99" (includes order ID and amount)

VALIDATE BEFORE OUTPUTTING:
1. Actions array contains only keys from available_actions
2. Interpretation includes specific extracted values
3. [EVENT] messages were checked and conversation history was reviewed
4. That if there is no [EVENT] tag with the words "Ongoing Activity" in the conversation history, the interpretation matches an anchor statement for the selected action
"""

# ============================================================================
# System Prompt Template
# ============================================================================

SYSTEM_PROMPT_TEMPLATE = """You route user utterances to appropriate actions based on intent analysis.

CRITICAL: The available_actions input is a JSON dictionary where:
- KEYS are action names (e.g., "SignupInterviewInteractAction")
- VALUES are lists of anchor statements (e.g., ["User wants to sign up", "User cancels SignupInterviewInteractAction"])
- You MUST return ONLY the action names (keys), NEVER the anchor statements (values)

Process:
1. **CRITICAL FIRST STEP**: Check [EVENT] messages in conversation history for ongoing activities
   - Look for last event entry like "[EVENT] Ongoing Activity: ..."
   - If an action has an ongoing activity in the last event, prioritize routing to it
2. Review conversation history for context (ongoing topics, prior questions, references)
3. Analyze current utterance intent
4. Match intent to action anchors - each anchor describes when that action should handle a request
5. Return matched action NAMES (dictionary keys) in JSON format

Matching rules:
- Match when utterance intent aligns with anchor descriptions
- If multiple actions match, prefer more specific anchors over general ones
- When uncertain, include all reasonable matches (multi-action responses are allowed)
- If no clear match, return empty actions array []

Output format:
- CORRECT: ["SignupInterviewInteractAction"] (action name/key)
- INCORRECT: ["User cancels SignupInterviewInteractAction"] (anchor statement/value)

Be precise but inclusive - missing a relevant action is worse than including an extra one."""

# ============================================================================
# Routing Prompt Template
# ============================================================================

ROUTING_PROMPT_TEMPLATE = """Current utterance:
{utterance}

Available actions and their anchors:
{anchors_json}

CRITICAL: The anchors JSON is a dictionary where:
- KEYS are action names (e.g., "SignupInterviewInteractAction")
- VALUES are lists of anchor statements (e.g., ["User wants to sign up", "User cancels SignupInterviewInteractAction"])

Instructions:
1. **CRITICAL FIRST STEP**: Check system messages with the tag [EVENT] in conversation history for ongoing activities
   - Look for recent events like "[EVENT] Ongoing Activity..."
   - If an action has an ongoing activity in recent events, prioritize routing to it

2. Interpret the user's intent in <80 words. Capture:
   - What they want (information request, providing data, or both)
   - Relevant context (IDs, references to prior conversation, ongoing events, user-provided details)
   - Example format: "User requests status update for ticket #789, mentions deadline"

3. Match interpretation to actions by comparing intent with each action's anchor statements:
   - **PRIORITY**: First check if any actions have ongoing activities in system messages - these should be routed to even with ambiguous utterances
   - If there is no [EVENT] tag in the conversation history, then only route to actions that have anchors that match the intent.
   - Check the conversation history to see if the user is continuing a prior topic or answering a previous question and determine the intent.
   - If intent is ambiguous i.e. it does not match any action anchors, do not route to any actions unless there is an ongoing activity in system messages with [EVENT] tag.
   - An action matches if its anchors align with the interpretation and describe handling this type of request
   - Prefer actions with more specific/detailed anchor matches
   - Include all actions that reasonably match (it's ok to route to multiple actions)
   - Consider conversation history and events - is this continuing a prior topic or answering a previous question?

3. Return ONLY this JSON structure:
{{
    "interpretation": "your intent interpretation",
    "actions": ["ActionName1", "ActionName2"]
}}

CRITICAL: The actions array must contain ONLY the action names (dictionary KEYS), NOT the anchor statements.
- CORRECT: ["SignupInterviewInteractAction"]
- INCORRECT: ["User cancels SignupInterviewInteractAction", "User stops SignupInterviewInteractAction"]
- Each action name must exactly match a key from the anchors JSON dictionary
- Return empty actions array [] if no match."""
