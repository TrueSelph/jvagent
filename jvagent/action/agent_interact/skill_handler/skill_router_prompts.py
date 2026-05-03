"""Prompt templates for AgentInteractAction skill-based routing.

Adapted from InteractRouter prompts (jvagent/action/router/prompts.py) but
using skill descriptors instead of raw InteractAction anchors. The router
now classifies posture, selects skills by name from a catalog of skill
descriptors, and generates canned responses.
"""

# =============================================================================
# Intent Types - Declarative Categories
# =============================================================================

INTENT_TYPES = [
    "CONVERSATIONAL",
    "INFORMATIONAL",
    "INTERACTIVE",
    "DIRECTIVE",
    "UNCLEAR",
]

# =============================================================================
# System Prompt
# =============================================================================

SKILL_ROUTER_SYSTEM_PROMPT = """You are a unified classification and routing intelligence for a conversational agent. First classify response posture (RESPOND/SUPPRESS/DEFER), then—only when posture is RESPOND—classify intent and select the most appropriate skill(s).

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

STEP 1 — SKILL SELECTION (only when posture=RESPOND)
Classify intent and select skills from AVAILABLE SKILLS. For SUPPRESS/DEFER, use skills=[], canned_response="", intent_type="UNCLEAR".

CORE PRINCIPLES:
- CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have empty skills []
- Select the best-matching skill(s) for the user's request
- Lower confidence if ambiguous or uncertain
- canned_response: Brief, varied lead-in; never answer; tailor to request; avoid generic repetition

AVAILABLE SKILLS are presented as skill_name with their description, tags, and plan steps. Select skills by name only (the exact keys).

GATING CONTEXT (when present in history):
- "Agent did not respond to recent message (suppressed)": Prior turn was backchannel; route based on current message only.
- "Deferred fragment(s) pending from user": Current message may complete fragmented thought; consider full context."""

# =============================================================================
# Routing Prompt
# =============================================================================

PRIOR_FRAGMENTS_SECTION_SKILL = """
PRIOR DEFERRED FRAGMENTS (not yet responded to):
{fragments_list}

"""

SKILL_ROUTING_PROMPT_TEMPLATE = """CONVERSATION STATE:
{active_tasks_section}{history_section}{prior_fragments_section}
CURRENT USER MESSAGE:
{utterance}

AVAILABLE SKILLS:
{skills_json}

TASK: 1) Classify posture (RESPOND/SUPPRESS/DEFER). 2) If posture=RESPOND, classify intent and select skills; otherwise use skills=[], canned_response="", intent_type="UNCLEAR".

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
2. Output posture first; then interpretation (which explains posture and summarizes intent), intent_type, skills, confidence (and canned_response when posture=RESPOND)
3. CONVERSATIONAL intent MUST have empty skills []
4. skills MUST be the exact skill names from AVAILABLE SKILLS keys, NOT descriptions or tags.
5. If the assistant's most recent message was a question and user answers, use INTERACTIVE
6. Lower confidence if ambiguous {optional_instructions}

INTERPRETATION: Brief synopsis of user intent and why this posture applies. RESPOND: user intent + key topics/entities. SUPPRESS: why no response. DEFER: why deferred. Target: one sentence, ~15-30 words.

OUTPUT (JSON only):
{{
  "posture": "RESPOND|SUPPRESS|DEFER",
  "interpretation": "Brief synopsis of user intent and why this posture applies.",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|INTERACTIVE|DIRECTIVE|UNCLEAR",
  "skills": ["SkillName1"],
  "confidence": 0.0-1.0{entity_field}{canned_field}
}}"""

# =============================================================================
# Canned Response Instructions
# =============================================================================

CANNED_RESPONSE_INSTRUCTIONS_TEMPLATE_SKILL = """
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
# Clarification Prompt
# =============================================================================

CLARIFICATION_PROMPT_TEMPLATE_SKILL = """Based on the routing analysis, the user's intent is unclear and requires clarification.

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

CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE_SKILL = """Paraphrase the clarification template below to match the user's language, tone, and vocabulary. Do NOT output the template verbatim—create a fresh paraphrase that conveys the same intent. Match the user's phrasing style for better alignment. Output only the paraphrased message.

User said: "{utterance}"

Template (paraphrase this): "{template}"
"""

DEFAULT_CLARIFICATION_MESSAGES = [
    "I want to make sure I understand correctly. Could you tell me a bit more about what you're looking for?",
    "I'm not quite sure what you need. Could you clarify what you'd like me to help with?",
    "I'd like to help, but I need a bit more context. What would you like me to do?",
]
