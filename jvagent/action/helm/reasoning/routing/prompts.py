"""Routing prompts for the Phase-1 router.

Lives next to :mod:`jvagent.action.helm.reasoning.routing.router`. Engine and
skill-catalog prompts are colocated with their respective implementations.

ReasoningHelm only runs INSIDE Bridge composition (it requires a
``BridgeState`` on the visitor and cannot be installed as a standalone
``InteractAction``). That has two prompt-level implications:

1. **Posture classification is upstream-of-router.** ReflexHelm gates the
   turn first: empty input → YIELD; trivial smalltalk → EMIT; substantive →
   SHIFT to Reasoning. By the time EngineRouter runs, posture is already
   RESPOND by construction. The full STEP 0 (RESPOND/SUPPRESS/DEFER) block
   from the standalone-Cockpit prompt costs ~400 tokens for behaviour
   Reflex already provides.

2. **canned_response is owned by Reflex.** Reflex's ``transient_ack`` on
   SHIFT is the user-facing immediate response in Bridge composition.
   The router's ``enable_canned_response`` is False by default in
   ``bridge_agent.yaml`` for exactly this reason. The principle bullet
   and schema field that describe canned_response are dead surface when
   the flag is off.

Both surfaces are now parameterized via :func:`build_routing_system_prompt`
and :func:`build_routing_user_prompt_template`. The module-level
``ROUTING_SYSTEM_PROMPT`` / ``ROUTING_USER_PROMPT_TEMPLATE`` constants
are rebuilt through those factories with the full (standalone-equivalent)
flags so importers see no diff. ``EngineRouter`` calls the factories with
Bridge-mode flags (posture stripped, canned stripped) to take the savings.

Single-source-of-truth invariant pinned by
``tests/action/helm/reasoning/test_routing_prompt_factories.py``.
"""

from __future__ import annotations

from typing import List

# ---------------------------------------------------------------------------
# Anchor disambiguation clause (shared invariant with rails InteractRouter)
# ---------------------------------------------------------------------------
#
# Reduces false-positive routing caused by keyword-only overlap between the
# user's utterance and an action's anchor list. Canonical failure mode (live
# smoke, May 2026): "Help me prepare for an interview" was routed to a
# ``signup_interview_interact_action`` whose anchors described training
# enrollment — the LLM latched onto the shared noun "interview" rather than
# the verb-object intent ("help me prepare for…" vs "sign up / enroll").
#
# Identical text MUST appear in jvagent/action/router/prompts.py; the
# cross-module invariant is pinned by
# ``tests/action/router/test_anchor_disambiguation.py`` so a future edit
# cannot silently drift one copy without the other.
ANCHOR_DISAMBIGUATION_CLAUSE = """ANCHOR MATCHING — by INTENT, not by keywords:
- An action's anchors and description tell you what THAT ACTION does. They are NOT a keyword filter.
- Before routing to an action, ask: "In this turn, does the user's verb + object match the action the anchor describes?" Same noun, different verb-object = NO match.
- When a user word appears in an anchor but the user's request is about a different topic, do NOT route to that action. Prefer an empty actions list — let the engine or persona handle it — over a low-confidence match on a shared noun.
- Example: an action whose anchors describe "training signup interviews" must NOT match "help me prepare for a job interview". The noun "interview" overlaps but the user's request ("help me prepare for…") is unrelated to the action described ("sign up / enroll")."""


# ---------------------------------------------------------------------------
# System-prompt blocks
# ---------------------------------------------------------------------------
#
# Each block is a self-contained fragment of the routing system prompt.
# ``build_routing_system_prompt`` assembles them based on caller flags.
# Editing a block here applies to BOTH the back-compat module constant
# (full prompt) and the Bridge-mode prompt that EngineRouter actually
# sends — no risk of drift between the two shapes.

_OPENING_FULL = """You are a unified classification and routing intelligence for a conversational reasoning agent. First classify response posture (RESPOND/SUPPRESS/DEFER), then — only when posture is RESPOND — classify intent, select skills, and (when appropriate) emit a brief canned lead-in."""

_OPENING_BRIDGE = """You are a routing intelligence for a conversational reasoning agent. Classify intent, select skills, and select interact_actions for the current turn."""

# STEP 0 — full posture block (used by standalone-Cockpit-equivalent prompt).
_POSTURE_BLOCK_FULL = """STEP 0 — POSTURE (RESPOND | SUPPRESS | DEFER)
Trace the flow from history to the current message. What was the most recent assistant message? How does the current user message relate?

RESPOND — use when:
- Greeting, opener, first contact ("Hey", "Hi", "Hello") — ALWAYS RESPOND
- Question, request, substantive statement
- User sent media (images, documents) — ALWAYS RESPOND; treat as request to view/interpret
- Answer (affirmative OR negative) to assistant's direct question ("ok", "yes", "no", "no sorry", "nope", "sure" after "Would you like X?")
- Gratitude for directly preceding assistant help ("Thanks!" after answer) — allow "you're welcome"
- Short but contextually coherent message; when in doubt, use RESPOND
- Personal-fact statements like "my name is..." or "remember that I..." — DIRECTIVE intent, RESPOND

SUPPRESS — use ONLY when:
- Social closing (goodbye) AND exchange already concluded or same closing already exchanged
- Redundant gratitude after assistant already said "you're welcome"
- Hanging acknowledgment ("ok", "alright") with nothing to answer AND exchange at natural pause
- NEVER SUPPRESS: direct answer to question ("No sorry"), greetings, "thanks" before "you're welcome", any new request, any personal-fact statement.

DEFER — use ONLY when:
- Utterance genuinely unintelligible/fragmentary ("Actually...", "wait no I") AND history lacks context
- NEVER DEFER: User sent media; use RESPOND."""

# STEP 0 — Bridge-mode defensive one-liner. Reflex has already gated
# SUPPRESS/DEFER upstream; the only case the router still needs to handle
# is a pathological pass-through where Reflex SHIFTed something genuinely
# unintelligible. Keeping a tiny escape hatch is cheaper than removing the
# posture surface entirely and rewriting the parser. ~25 tokens vs ~400.
_POSTURE_BLOCK_BRIDGE = """POSTURE — Always RESPOND. The upstream classifier (Reflex) has already gated SUPPRESS/DEFER posture before any turn reaches you. Only exception: if the utterance is truly unintelligible AND history provides no context, set posture=DEFER, skills=[], interact_actions=[]."""

_ROUTE_SELECTION_BLOCK = """STEP 1 — ROUTE SELECTION (only when posture=RESPOND)
Two route classes are available:

A. **skills** — capability bundles invoked through the reasoning engine (tool-driven research / synthesis / multi-step work). Pick from the SKILLS CATALOG. Use exact skill keys, never descriptions.
B. **interact_actions** — specialized response handlers that run AS InteractActions, without the reasoning engine. Pick from the INTERACT ACTIONS CATALOG. Use exact class names.

DECISION RULES:
- Choose **skills only** when the request needs tool-driven exploration / synthesis / data retrieval and no specialized handler matches.
- Choose **interact_actions only** when a listed handler is purpose-built for this request type (e.g., explicit handoff, structured form-fill, dedicated workflow) and no engine-level reasoning is needed.
- Choose **both** when the request needs research first AND a specialized handler afterward (engine produces output, then the interact_action runs).
- The reasoning engine has harness tools beyond skills (memory, artifacts, task planning, conversation search). A request that doesn't match any listed skill or interact_action can still be handled — emit ``skills: []`` and ``interact_actions: []`` and the engine will figure it out."""

# canned_response is permanently dropped from ReasoningHelm's prompt
# surface (Reflex owns the transient_ack lead-in in Bridge composition).
# The standalone-Cockpit copy at jvagent/action/cockpit/routing/prompts.py
# keeps the full canned principles for its own use.
_CORE_PRINCIPLES = """CORE PRINCIPLES:
- CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have empty skills [] AND empty interact_actions [].
- Recap / summarize / recall / "what did I say" requests are **always
  INFORMATIONAL**, never CONVERSATIONAL — even when phrased with a polite
  preamble ("Great can you recap…", "Thanks! Could you summarize…"). The
  engine path needs to run so it can read full conversation history; the
  conversational fast-path sees only a short window and produces
  truncated or fabricated recaps."""

_INTENT_TYPES_BLOCK = """INTENT TYPES (when posture=RESPOND):
- CONVERSATIONAL: greeting, thanks, smalltalk only; no request.
- INFORMATIONAL: question, lookup, knowledge retrieval. **Includes recap /
  summary / recall requests** — "what did I say", "what have we discussed",
  "summarize our chat", "recap our conversation", "what was the first
  thing I asked", "remind me what I told you" — these are NOT conversational
  and MUST be INFORMATIONAL so the engine path runs with full conversation
  history. A polite preamble like "Great," / "Thanks!" does not downgrade a
  recap request to CONVERSATIONAL.
- INTERACTIVE: multi-turn (interview / form-fill / back-and-forth).
- DIRECTIVE: direct command, imperative ("search for X", "remember that...", "save Z").
- UNCLEAR: cannot determine."""

_GROUNDING_BLOCK = """GROUNDING:
- Use this prompt, history, the skill catalog, and any tool output as admissible evidence for posture, intent, and interpretation.
- Do not treat general pretrained world knowledge as authoritative; when unsure, lower confidence."""


def build_routing_system_prompt(
    *,
    include_posture_block: bool = True,
) -> str:
    """Assemble the routing system prompt from blocks.

    Args:
        include_posture_block: When True, embeds the full STEP 0
            RESPOND/SUPPRESS/DEFER classification block (~400 tokens).
            When False, replaces it with a single defensive one-liner
            (~25 tokens) — the appropriate setting in Bridge composition
            where Reflex has already gated posture upstream.

    Returns:
        The fully assembled routing system prompt string, terminated
        with the anchor disambiguation clause (always included).

    Note:
        The ``canned_response`` principle bullet has been permanently
        removed from this prompt surface — Reflex owns the
        transient_ack lead-in in Bridge composition, so the Reasoning-
        side router never emits a canned response. The standalone-
        Cockpit copy at ``jvagent/action/cockpit/routing/prompts.py``
        retains its full canned guidance for its own use.
    """
    opening = _OPENING_FULL if include_posture_block else _OPENING_BRIDGE
    posture = _POSTURE_BLOCK_FULL if include_posture_block else _POSTURE_BLOCK_BRIDGE

    sections = [
        opening,
        posture,
        _ROUTE_SELECTION_BLOCK,
        _CORE_PRINCIPLES,
        _INTENT_TYPES_BLOCK,
        _GROUNDING_BLOCK,
        ANCHOR_DISAMBIGUATION_CLAUSE,
    ]
    return "\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# User-prompt template blocks
# ---------------------------------------------------------------------------

_USER_TEMPLATE_HEADER = """CONVERSATION STATE:
{active_tasks_section}{history_section}{prior_fragments_section}
CURRENT USER MESSAGE:
{utterance}

SKILLS CATALOG (JSON keys = only valid "skills" array entries):
{skills_json}

INTERACT ACTIONS CATALOG (JSON keys = only valid "interact_actions" array entries):
{interact_actions_json}"""

_USER_TEMPLATE_TASK_FULL = """TASK: 1) Classify posture (RESPOND/SUPPRESS/DEFER). 2) If posture=RESPOND, classify intent and fill skills + interact_actions; otherwise use skills=[], interact_actions=[], canned_response="", intent_type="UNCLEAR"."""

_USER_TEMPLATE_TASK_BRIDGE = (
    """TASK: Classify intent and fill skills + interact_actions for the current turn."""
)

# The recap is a compressed re-statement of the system prompt's STEP 0.
# Only emitted in standalone (non-Bridge) mode — when posture is already
# upstream-gated, the recap is pure duplication.
_USER_TEMPLATE_POSTURE_RECAP = """POSTURE RULES (recap):
- RESPOND: greeting (always), question, request, answer to question, gratitude for help, personal-fact statement, contextually coherent message. When in doubt, RESPOND.
- SUPPRESS: closing after exchange concluded; redundant thanks; hanging "ok" with nothing to answer. NEVER for direct answers, greetings, or new requests.
- DEFER: genuinely unintelligible fragment AND no context. NEVER for media attachments."""

_USER_TEMPLATE_RULES = """RULES:
1. The ">>> USER RESPONDS NOW <<<" marker in history indicates the transition to the current user message.
2. Output posture first; then interpretation, intent_type, skills, interact_actions, confidence (and canned_response when posture=RESPOND).
3. CONVERSATIONAL intent MUST have empty skills [] AND empty interact_actions [].
4. Each skills array entry MUST be an exact SKILLS CATALOG key, NOT a description or tag.
5. Each interact_actions array entry MUST be an exact INTERACT ACTIONS CATALOG key (class name), NOT a description.
6. Use interact_actions ONLY when a listed handler is purpose-built for this request and no tool-driven engine work is needed.
7. Use both skills AND interact_actions when engine work must precede a specialized handler.
8. If the assistant's most recent message was a question and the user answers, use INTERACTIVE.
9. Lower confidence if ambiguous{optional_instructions}"""

_USER_TEMPLATE_INTERPRETATION = """INTERPRETATION: Brief synopsis of user intent and why this posture applies. Target one sentence, ~15-30 words."""

_USER_TEMPLATE_OUTPUT_SCHEMA = """OUTPUT (JSON only):
{{
  "posture": "RESPOND|SUPPRESS|DEFER",
  "interpretation": "Brief synopsis of user intent and why this posture applies.",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|INTERACTIVE|DIRECTIVE|UNCLEAR",
  "skills": ["SkillName1"],
  "interact_actions": ["ClassName1"],
  "confidence": 0.0-1.0{entity_field}{canned_field}
}}"""


def build_routing_user_prompt_template(
    *,
    include_posture_recap: bool = True,
) -> str:
    """Assemble the routing user-prompt template from blocks.

    The returned string is a ``str.format()`` template — callers pass
    ``active_tasks_section``, ``history_section``, ``prior_fragments_section``,
    ``utterance``, ``skills_json``, ``interact_actions_json``,
    ``entity_field``, ``canned_field``, and ``optional_instructions`` at
    render time.

    Args:
        include_posture_recap: When True, includes the compressed
            POSTURE RULES recap block (~100 tokens). When False, drops
            both the recap and the task-line posture mention — the
            appropriate setting in Bridge composition where the system
            prompt's posture block has already been replaced with the
            one-liner.

    Returns:
        Format-string template ready for ``.format(**fields)``.
    """
    task = (
        _USER_TEMPLATE_TASK_FULL
        if include_posture_recap
        else _USER_TEMPLATE_TASK_BRIDGE
    )
    sections = [_USER_TEMPLATE_HEADER, task]
    if include_posture_recap:
        sections.append(_USER_TEMPLATE_POSTURE_RECAP)
    sections.extend(
        [
            _USER_TEMPLATE_RULES,
            _USER_TEMPLATE_INTERPRETATION,
            _USER_TEMPLATE_OUTPUT_SCHEMA,
        ]
    )
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Module-level constants — rebuilt through the factories for single source
# of truth. Anything that imported ROUTING_SYSTEM_PROMPT before still sees
# the full (standalone-equivalent) prompt; EngineRouter calls the factories
# directly with Bridge-mode flags to take the token savings.
# ---------------------------------------------------------------------------

ROUTING_SYSTEM_PROMPT = build_routing_system_prompt(
    include_posture_block=True,
)

ROUTING_USER_PROMPT_TEMPLATE = build_routing_user_prompt_template(
    include_posture_recap=True,
)


# ---------------------------------------------------------------------------
# Clarification fallbacks (used when router confidence is too low)
# ---------------------------------------------------------------------------

ROUTING_CLARIFICATION_FALLBACK_MESSAGES: List[str] = [
    "Could you tell me more about what you need?",
    "I'd like to help — could you rephrase that?",
    "Can you provide more details about your request?",
]


ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE = """\
The user said: "{utterance}"
Our initial interpretation: {interpretation}
Intent type: {intent_type}
Confidence: {confidence}
Issues: {issues}

Please provide a clarification question to ask the user.
"""


ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE = """\
Rephrase this clarification question naturally and concisely: "{template}"
"""


__all__ = [
    "ANCHOR_DISAMBIGUATION_CLAUSE",
    "ROUTING_SYSTEM_PROMPT",
    "ROUTING_USER_PROMPT_TEMPLATE",
    "ROUTING_CLARIFICATION_FALLBACK_MESSAGES",
    "ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE",
    "ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE",
    "build_routing_system_prompt",
    "build_routing_user_prompt_template",
]
