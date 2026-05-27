"""Prompt templates for InteractRouter.

This module provides the prompt templates used by InteractRouter for
intelligent conversational state analysis and routing.
"""

# =============================================================================
# Anchor disambiguation clause (shared invariant with CockpitRouter)
# =============================================================================
#
# Reduces false-positive routing caused by keyword-only overlap between the
# user's utterance and an action's anchor list. Canonical failure mode (live
# smoke, May 2026): "Help me prepare for an interview" was routed to a
# ``signup_interview_interact_action`` whose anchors described training
# enrollment — the LLM latched onto the shared noun "interview" rather than
# the verb-object intent ("help me prepare for…" vs "sign up / enroll").
#
# The clause reframes anchor matching from "do any words overlap?" to "does
# the user's verb + object match what this action does?". A concrete contrast
# example is load-bearing — small router models (gpt-4o-mini) reliably
# internalise one example better than abstract instructions.
#
# Identical text MUST appear in CockpitRouter's prompts module; the cross-
# module invariant is pinned by ``tests/action/router/test_anchor_disambiguation.py``
# so a future edit cannot silently drift one copy without the other.
ANCHOR_DISAMBIGUATION_CLAUSE = """ANCHOR MATCHING — by INTENT, not by keywords:
- An action's anchors and description tell you what THAT ACTION does. They are NOT a keyword filter.
- Before routing to an action, ask: "In this turn, does the user's verb + object match the action the anchor describes?" Same noun, different verb-object = NO match.
- When a user word appears in an anchor but the user's request is about a different topic, do NOT route to that action. Prefer an empty actions list — let the engine or persona handle it — over a low-confidence match on a shared noun.
- Example: an action whose anchors describe "training signup interviews" must NOT match "help me prepare for a job interview". The noun "interview" overlaps but the user's request ("help me prepare for…") is unrelated to the action described ("sign up / enroll")."""


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

ROUTER_SYSTEM_PROMPT = (
    """You are a unified classification and routing intelligence for a conversational agent. First classify response posture (RESPOND/SUPPRESS/DEFER), then—only when posture is RESPOND—classify intent and route to actions.

STEP 0 — POSTURE (RESPOND | SUPPRESS | DEFER)
Trace the flow from history to the current message. What was the most recent assistant message? How does the current user message relate?

RESPOND — use when:
- Greeting, opener, first contact ("Hey", "Hi", "Hello") — ALWAYS RESPOND
- Question, request, substantive statement
- User sent media (images, documents) — ALWAYS RESPOND; treat as request to view/interpret
- Answer (affirmative OR negative) to assistant's direct question ("ok", "yes", "no", "no sorry", "nope", "sure" after "Would you like X?")
- Gratitude for directly preceding assistant help ("Thanks!" after answer) — allow "you're welcome"
- Short but contextually coherent message; when in doubt, use RESPOND

SUPPRESS — use ONLY when:
- Social closing (goodbye) AND exchange already concluded or same closing already exchanged
- Redundant gratitude after assistant already said "you're welcome"
- Hanging acknowledgment ("ok", "alright") with nothing to answer AND exchange at natural pause
- NEVER SUPPRESS: direct answer to question ("No sorry"), greetings, "thanks" before "you're welcome"

DEFER — use ONLY when:
- Utterance genuinely unintelligible/fragmentary ("Actually...", "wait no I") AND history lacks context
- When prior deferred fragments are provided: if combined (fragments + current) is intelligible, use RESPOND
- NEVER DEFER: User sent media (images, documents); use RESPOND

STEP 1 — ROUTING (only when posture=RESPOND)
Classify intent and route to actions. For SUPPRESS/DEFER, use actions=[], canned_response="", intent_type="UNCLEAR".

CORE PRINCIPLES:
- CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have empty actions []
- Lower confidence if ambiguous or uncertain
- canned_response: Brief, varied lead-in; never answer; tailor to request; avoid generic repetition

GATING CONTEXT (when present in history):
- "Agent did not respond to recent message (suppressed)": Prior turn was backchannel; route based on current message only.
- "Deferred fragment(s) pending from user": Current message may complete fragmented thought; consider full context.

"""
    + ANCHOR_DISAMBIGUATION_CLAUSE
)

# =============================================================================
# Routing Prompt
# =============================================================================

PRIOR_FRAGMENTS_SECTION = """
PRIOR DEFERRED FRAGMENTS (not yet responded to):
{fragments_list}

"""

ROUTING_PROMPT_TEMPLATE = """CONVERSATION STATE:
{active_tasks_section}{history_section}{prior_fragments_section}
CURRENT USER MESSAGE:
{utterance}

AVAILABLE ACTIONS:
{anchors_json}

TASK: 1) Classify posture (RESPOND/SUPPRESS/DEFER). 2) If posture=RESPOND, classify intent and route to actions; otherwise use actions=[], canned_response="", intent_type="UNCLEAR".

POSTURE RULES:
- RESPOND: Greeting (always), question, request, answer to question, gratitude for help, media (images/documents), contextually coherent message. When in doubt, RESPOND.
- SUPPRESS: Closing after exchange concluded; redundant thanks; hanging "ok" with nothing to answer. NEVER: direct answer to question, greetings.
- DEFER: Genuinely unintelligible fragment AND no context. NEVER: media attachment. With prior fragments: if combined is intelligible, use RESPOND.

INTENT TYPES (when posture=RESPOND):
- CONVERSATIONAL: Greeting, thanks, smalltalk only; no request
- INFORMATIONAL: Question, lookup, knowledge retrieval (including user profile/memory)
- INTERACTIVE: Multi-turn (interview) — starting or answering
- DIRECTIVE: Direct command
- UNCLEAR: Cannot determine

RULES:
1. The ">>> USER RESPONDS NOW <<<" marker in history indicates the transition to the current user message.
2. Output posture first; then interpretation (which explains posture and summarizes intent), intent_type, actions, confidence (and canned_response when posture=RESPOND)
3. CONVERSATIONAL intent MUST have empty actions []
4. Actions MUST be the exact JSON keys from AVAILABLE ACTIONS (the action class names), NEVER the anchor descriptions or values. Example: for {{"PageIndexRetrievalInteractAction": ["User asks...", ...]}} use actions: ["PageIndexRetrievalInteractAction"], NOT ["User asks..."]
5. If the assistant's most recent message was a question and user answers, use INTERACTIVE
6. Lower confidence if ambiguous {optional_instructions}

INTERPRETATION: Brief synopsis of user intent and why this posture applies. Include key topics/entities. RESPOND: user intent + key entities (e.g. "User greeted and asked about jvspatial; clear informational inquiry"). SUPPRESS: why no response (e.g. "Closing exchange; no further response needed"). DEFER: why deferred (e.g. "Fragmentary; lacks context to respond"). Target: one sentence, ~15-30 words.

OUTPUT (JSON only):
{{
  "posture": "RESPOND|SUPPRESS|DEFER",
  "interpretation": "Brief synopsis of user intent and why this posture applies. Include key topics/entities. For SUPPRESS/DEFER: explain why.",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|INTERACTIVE|DIRECTIVE|UNCLEAR",
  "actions": ["ActionName1"],
  "confidence": 0.0-1.0{entity_field}{canned_field}
}}"""

# =============================================================================
# Canned Response Instructions (lead-in only, never substantive)
# =============================================================================
# The canned_response is a brief lead-in shown before the full response.
# It must NEVER answer, explain, refuse, or provide any substantive content.

CANNED_RESPONSE_INSTRUCTIONS_TEMPLATE = """
6. Generate a VARIED, REQUEST-TAILORED lead-in for canned_response. Prefer 3–6 words; max {max_words} words.
   - NEVER answer, never comment on the request—only acknowledge that you are working on it.
   - Reference topic or request type when it fits (e.g. "Good question about X") without answering.
   - TAILOR to request type (question, command, lookup); match user's tone.
   - VARY phrasing across requests; avoid mechanistic repetition.
   - GOOD (varied, tailored): "Good question about jvspatial" | "On it" | "Checking that" | "Give me a sec" | "Looking into it" | "Got it, checking now" | "On the weather—checking" | "Doing that now"
   - BAD: Any answer, explanation, or substantive content; overusing the same phrase repeatedly
   - For {skip_intents} intents, use empty string ""
"""

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
