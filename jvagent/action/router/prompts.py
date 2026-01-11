"""Prompt templates for InteractRouter.

This module provides the prompt templates used by InteractRouter for:
- System prompt for routing analysis
- Routing prompt template with placeholders
- DSPy signature docstring (single source of truth)
"""

# DSPy Signature Docstring (single source of truth for RouterClassification)
# This docstring is used by the RouterClassification DSPy signature
# Can be overridden via action class attribute for runtime customization
ROUTER_CLASSIFICATION_SIGNATURE = """Classify user utterance intent and route to appropriate InteractActions.
    
    Generate concise interpretation about the user's intent, then determine which actions should handle the request.
    
    Analyze the user's utterance and conversation history to determine intent,
    then match against available action anchors to identify which actions should
    handle this request.
    
    CRITICAL: ACTION NAMES vs ANCHOR STATEMENTS
    - available_actions is a JSON object where KEYS are action names (e.g., "SignupInterviewInteractAction")
    - VALUES are lists of anchor statements (e.g., ["User wants to sign up", "User cancels SignupInterviewInteractAction"])
    - The actions output MUST contain ONLY the KEYS (action names), NEVER the anchor statements
    - CORRECT: ["SignupInterviewInteractAction"]
    - INCORRECT: ["User cancels SignupInterviewInteractAction", "User stops SignupInterviewInteractAction"]
    - Each action name in the output must exactly match a key from the available_actions JSON object
    
    ROUTING RULES:
    - Match when utterance intent aligns with anchor descriptions
    - If multiple actions match, prefer more specific anchors over general ones
    - When uncertain, include all reasonable matches (multi-action responses are allowed)
    - If no clear match, return empty actions array []
    - Consider conversation history for context (ongoing topics, prior questions, references)
    - Be precise but inclusive - missing a relevant action is worse than including an extra one
    
    INTERPRETATION GUIDELINES:
    - The interpretation should be concise (under 80 words) and serve as the intent interpretation
    - Capture what the user wants (information request, providing data, or both)
    - **CRITICAL: Always extract and include specific information from the current utterance and conversation history**
    - The interpretation must be rich enough for downstream actions to extract information without re-parsing the raw utterance
    - Examples:
      * "User provides name 'John Doe' and email 'john@example.com' for signup" (includes extracted values)
      * "User requests status update for ticket #789, mentions deadline of Friday" (includes ID and specific detail)
      * "User wants to change email from 'old@example.com' to 'new@example.com'" (includes both old and new values)
      * "User confirms order #12345 for $99.99" (includes order ID and amount)
    
    MATCHING GUIDELINES:
    - An action matches if its anchors align with the interpretation and describe handling this type of request
    - Prefer actions with more specific/detailed anchor matches
    - Include all actions that reasonably match (it's ok to route to multiple actions)
    - Consider conversation history and events - is this continuing a prior topic or answering a previous question?
    - Return ONLY the action name (key) from available_actions, not the anchor statements
    - Example: If available_actions contains {"SignupInterviewInteractAction": ["User wants to sign up"]}, return ["SignupInterviewInteractAction"], not ["User wants to sign up"]
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
1. Review conversation history for context (ongoing topics, prior questions, references)
2. Analyze current utterance intent
3. Match intent to action anchors - each anchor describes when that action should handle a request
4. Return matched action NAMES (dictionary keys) in JSON format

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
1. Interpret the user's intent in <50 words. Capture:
   - What they want (information request, providing data, or both)
   - Relevant context (IDs, references to prior conversation, ongoing events, user-provided details)
   - Example format: "User requests status update for ticket #789, mentions deadline"

2. Match interpretation to actions by comparing intent with each action's anchor statements:
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
