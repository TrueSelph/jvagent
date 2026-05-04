"""Default prompt templates for ``AgentInteractAction`` routing (Phase 1).

Override via ``AgentInteractAction`` attributes of the same names (see
``agent_interact_action.py``).
"""

# =============================================================================
# Intent types (declarative; aligned with ``routing_result`` normalization)
# =============================================================================

ROUTING_INTENT_TYPES = [
    "CONVERSATIONAL",
    "INFORMATIONAL",
    "INTERACTIVE",
    "DIRECTIVE",
    "UNCLEAR",
]

# =============================================================================
# Main routing LLM
# =============================================================================

ROUTING_SYSTEM_PROMPT = """You are a unified classification and routing intelligence for a conversational agent. First classify response posture (RESPOND/SUPPRESS/DEFER), then—only when posture is RESPOND—classify intent and select the best route(s).

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

STEP 1 — ROUTE SELECTION (only when posture=RESPOND)
Classify intent and choose zero or more **skill** names (skill catalog) and/or **interact_actions** (direct ``InteractAction`` class names from the second catalog). Both lists drive the walk path. For SUPPRESS/DEFER, use skills=[], interact_actions=[], canned_response="", intent_type="UNCLEAR".

CORE PRINCIPLES:
- CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have empty skills [] and empty interact_actions []
- Select the best-matching route(s) for the user's request
- Lower confidence if ambiguous or uncertain
- canned_response (when emitted): non-conclusive **lead-in only**—a fragment or stall the main reply will continue in the same turn; never a standalone sentence that answers, refuses, advises, redirects, or closes the topic (see task rule 6).

Two catalogs are shown: **skills** (keys = valid ``skills`` JSON entries) and **interact_actions** (keys = valid ``interact_actions`` JSON entries). Use exact keys only, never descriptions.

GATING CONTEXT (when present in history):
- "Agent did not respond to recent message (suppressed)": Prior turn was backchannel; route based on current message only.
- "Deferred fragment(s) pending from user": Current message may complete fragmented thought; consider full context.

GROUNDING (routing):
- Use this prompt, history, catalogs, and any tool output shown here as admissible evidence for posture, intent, and interpretation.
- Do not treat general pretrained world knowledge as authoritative unless the user or in-prompt context states it in a verifiable way; when unsure, lower confidence or prefer routes that can verify."""

ROUTING_PRIOR_FRAGMENTS_SECTION = """
PRIOR DEFERRED FRAGMENTS (not yet responded to):
{fragments_list}

"""

ROUTING_USER_PROMPT_TEMPLATE = """CONVERSATION STATE:
{active_tasks_section}{history_section}{prior_fragments_section}
CURRENT USER MESSAGE:
{utterance}

SKILLS CATALOG (JSON keys = only valid "skills" array entries):
{skills_json}

INTERACT ACTIONS CATALOG (JSON keys = only valid "interact_actions" array entries):
{interact_actions_json}

TASK: 1) Classify posture (RESPOND/SUPPRESS/DEFER). 2) If posture=RESPOND, classify intent and fill skills and/or interact_actions; otherwise use skills=[], interact_actions=[], canned_response="", intent_type="UNCLEAR".

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
2. Output posture first; then interpretation (which explains posture and summarizes intent), intent_type, skills, interact_actions, confidence (and canned_response when posture=RESPOND)
3. CONVERSATIONAL intent MUST have empty skills [] and empty interact_actions []
4. Each array entry MUST be an exact catalog key (skills catalog or interact-actions catalog), NOT a description or tag.
5. If the assistant's most recent message was a question and user answers, use INTERACTIVE
6. Lower confidence if ambiguous {optional_instructions}

INTERPRETATION: Brief synopsis of user intent and why this posture applies. RESPOND: user intent + key topics/entities. SUPPRESS: why no response. DEFER: why deferred. Target: one sentence, ~15-30 words.

OUTPUT (JSON only):
{{
  "posture": "RESPOND|SUPPRESS|DEFER",
  "interpretation": "Brief synopsis of user intent and why this posture applies.",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|INTERACTIVE|DIRECTIVE|UNCLEAR",
  "skills": ["SkillName1"],
  "interact_actions": ["OptionalInteractActionClassName"],
  "confidence": 0.0-1.0{entity_field}{canned_field}
}}"""

ROUTING_CANNED_INSTRUCTIONS_TEMPLATE = """
6. canned_response: use "" when intent_type is one of: {skip_intents}. Otherwise same language as the CURRENT USER MESSAGE; ≤{max_words} words; vary wording across turns.

   STRICT — lead-in acknowledgement ONLY (must sound incomplete; the real reply follows immediately after in the same turn):
   - ALLOWED: hesitation, filler, or a short fragment with no full thought (e.g. "Hmm…", "One sec…", "Let me see…" in the user's language).
   - FORBIDDEN — **no conclusive or substantive content whatsoever**: no answers, explanations, outcomes, reasons, advice, instructions to the user, refusals, limits, policy, apologies-for-limits, workarounds, redirects, or any string that could read as a finished message. If it could stand alone in chat, it is wrong.
   - FORBIDDEN patterns (illustrative, not exhaustive): two clauses that resolve or pivot ("…, but you can…"; "…, so …"); "I can't …" / "I'm unable …" / "You should …" / "Try …" / anything that addresses the user's request without an obvious follow-on in the same bubble.
   - BAD: "I can't check the time, but you can look at your device." — explains and concludes; belongs in the main reply only, never in canned_response.
   - GOOD: "Hmm…" / "Just a moment…" — acknowledges processing only; carries zero standalone substance.
"""

# =============================================================================
# Clarification (low-confidence branch)
# Override via ``AgentInteractAction.routing_clarification_*`` attributes.
# =============================================================================

ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE = """Based on the routing analysis, the user's intent is unclear and requires clarification.

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

ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE = """Paraphrase the clarification template below to match the user's language, tone, and vocabulary. Do NOT output the template verbatim—create a fresh paraphrase that conveys the same intent. Match the user's phrasing style for better alignment. Output only the paraphrased message.

User said: "{utterance}"

Template (paraphrase this): "{template}"
"""

ROUTING_CLARIFICATION_FALLBACK_MESSAGES = [
    "I want to make sure I understand correctly. Could you tell me a bit more about what you're looking for?",
    "I'm not quite sure what you need. Could you clarify what you'd like me to help with?",
    "I'd like to help, but I need a bit more context. What would you like me to do?",
]
