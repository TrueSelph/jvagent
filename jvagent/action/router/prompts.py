"""Prompt templates for InteractRouter.

This module provides the prompt templates used by InteractRouter for
intelligent conversational state analysis and routing.
"""

# =============================================================================
# Intent Types - Declarative Categories
# =============================================================================

INTENT_TYPES = [
    "CONVERSATIONAL",  # Simple exchanges - greetings, smalltalk, social pleasantries
    "INFORMATIONAL",  # Knowledge/data retrieval - questions, lookups, RAG queries
    "INTERACTIVE",  # Multi-turn, stateful interactions - interviews, forms, workflows (starting or continuing)
    "DIRECTIVE",  # Direct action commands - "do X", "send Y", "create Z"
    "UNCLEAR",  # Cannot determine intent with confidence
]

# =============================================================================
# System Prompt
# =============================================================================

ROUTER_SYSTEM_PROMPT = """You are a routing intelligence for a conversational agent. Analyze user messages and route them to appropriate actions with high accuracy.

CORE PRINCIPLES:
1. Understand what the user actually needs right now
2. Route to actions whose anchors genuinely match the user's need
3. Calibrate confidence based on certainty and ambiguity

KEY RULES:
- CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have empty actions []
- Lower confidence if ambiguous or uncertain

GATING CONTEXT (when present in history):
- "Agent did not respond to recent message (suppressed)": Prior user message was a backchannel/filler; agent correctly stayed silent. Do not over-explain.
- "Deferred fragment(s) pending from user": User sent incomplete fragments; current message may complete the thought. Consider full context when routing."""

# =============================================================================
# Routing Prompt
# =============================================================================

ROUTING_PROMPT_TEMPLATE = """CONVERSATION STATE:
{active_tasks_section}{history_section}

CURRENT USER MESSAGE:
{utterance}

AVAILABLE ACTIONS:
{anchors_json}

TASK: Analyze the current user message in context of the conversation history and route to appropriate action(s).

INTENT TYPES:
- CONVERSATIONAL: Greeting, thanks, smalltalk only; no request, no information given
- INFORMATIONAL: Question, lookup, knowledge retrieval
- INTERACTIVE: Multi-turn process (interview) — starting or answering/continuing
- DIRECTIVE: Direct command to perform action
- UNCLEAR: Cannot determine

RULES:
1. The conversation history is shown FIRST, with the current user message shown AFTER the ">>> USER RESPONDS NOW <<<" marker
2. Match the current user's message to action anchors based on their actual need
3. CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have empty actions []
4. Actions must be exact keys from Available actions (e.g., "SignupInterviewInteractAction"), NOT anchor descriptions
5. If the most recent assistant message in the history was a question, and the current user message appears to answer it, use INTERACTIVE (not CONVERSATIONAL)
6. If the user asks a question about the agent's role, capabilities, or purpose, use CONVERSATIONAL
7. If context shows "Agent did not respond to recent message (suppressed)", the prior turn was correctly gated; route based on current message only
8. If context shows "Deferred fragment(s) pending from user", the current message may complete a fragmented thought; consider prior fragments when interpreting
9. Lower confidence if ambiguous or uncertain {optional_instructions}

OUTPUT (JSON only):
{{
  "interpretation": "Brief synopsis of user's request/need",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|INTERACTIVE|DIRECTIVE|UNCLEAR",
  "actions": ["ActionName1"],
  "confidence": 0.0-1.0{entity_field}{canned_field}
}}"""

# =============================================================================
# Clarification Prompt Template
# =============================================================================

CLARIFICATION_PROMPT_TEMPLATE = """Based on the routing analysis, the user's intent is unclear and requires clarification.

Routing context:
- User message: {utterance}
- Interpretation: {interpretation}
- Intent type: {intent_type}
- Confidence: {confidence}
- Issues found: {issues}

Generate a brief, friendly clarification request that:
1. Acknowledges what you understood
2. Asks a specific question to disambiguate
3. Keeps it conversational and helpful

Output only the clarification message text, nothing else."""

# =============================================================================
# Default Clarification Messages (templates for paraphrasing)
# =============================================================================
# These are used as inspiration. The language model MUST paraphrase when using them—
# never output verbatim. Paraphrase to match the user's language, tone, and phrasing
# for variation and better alignment with the request.

DEFAULT_CLARIFICATION_MESSAGES = [
    "I want to make sure I understand correctly. Could you tell me a bit more about what you're looking for?",
    "I'm not quite sure what you need. Could you clarify what you'd like me to help with?",
    "I'd like to help, but I need a bit more context. What would you like me to do?",
]

CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE = """Paraphrase the clarification template below to match the user's language, tone, and vocabulary. Do NOT output the template verbatim—create a fresh paraphrase that conveys the same intent. Match the user's phrasing style for better alignment. Output only the paraphrased message.

User said: "{utterance}"

Template (paraphrase this): "{template}"
"""
