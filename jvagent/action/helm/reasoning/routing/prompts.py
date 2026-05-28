"""Routing prompts for the ReasoningHelm capability router.

Lives next to :mod:`jvagent.action.helm.reasoning.routing.router`. Engine
and skill-catalog prompts are colocated with their respective implementations.

ADR-0008 shape (Wave 6):

- **Unified catalog.** Skills and InteractActions appear in one
  ``CAPABILITIES AVAILABLE`` section. The model picks by name; the
  capability registry decodes ``kind`` after the LLM call.
- **No posture surface.** ReflexHelm gates SUPPRESS/DEFER upstream; the
  posture-classification block is removed entirely. The model produces
  clarifying questions naturally from context when warranted.
- **No canned_response surface.** ReflexHelm's ``transient_ack`` on SHIFT
  owns the user-facing immediate response.

The ``ANCHOR_DISAMBIGUATION_CLAUSE`` is preserved verbatim across both
``jvagent/action/helm/reasoning/routing/prompts.py`` and
``jvagent/action/router/prompts.py`` (the legacy rails InteractRouter);
the cross-module invariant is pinned by
``tests/action/router/test_anchor_disambiguation.py``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Anchor disambiguation clause (shared invariant with rails InteractRouter)
# ---------------------------------------------------------------------------
#
# Reduces false-positive routing caused by keyword-only overlap between the
# user's utterance and a capability's anchor list. Canonical failure mode
# (live smoke, May 2026): "Help me prepare for an interview" was routed to a
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

_OPENING = (
    "You are a routing intelligence for a conversational reasoning agent. "
    "Select the capabilities (if any) that should run for the current turn."
)

_CAPABILITY_SELECTION_BLOCK = """CAPABILITY SELECTION
A single catalog of CAPABILITIES is presented to you. Each capability has a name and a description; the dispatch layer resolves whether each capability is a skill (engine-loop tool bundle) or an interact action (specialized handler) — you do not classify the kind, you only pick relevant names.

DECISION RULES:
- Pick the capabilities whose descriptions match the user's intent for THIS turn. Multiple capabilities are allowed when more than one applies.
- Prefer an empty list over a low-confidence match. Downstream the engine can still handle a turn that selects no catalog capabilities.
- Recap / summarize / recall / "what did I say" requests should be classified INFORMATIONAL — even when phrased with a polite preamble ("Great can you recap…", "Thanks! Could you summarize…").
- CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have an empty selection list."""

_INTENT_TYPES_BLOCK = """INTENT TYPES:
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
- Use this prompt, history, the capability catalog, and any tool output as admissible evidence for intent and interpretation.
- Do not treat general pretrained world knowledge as authoritative; when unsure, lower confidence."""


def build_routing_system_prompt() -> str:
    """Assemble the routing system prompt.

    No parameters: the ADR-0008 prompt has a single shape (no posture
    block, no canned_response surface, no skill/IA-kind classification).
    The returned string terminates with the anchor disambiguation clause.
    """
    sections = [
        _OPENING,
        _CAPABILITY_SELECTION_BLOCK,
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

CAPABILITIES AVAILABLE (JSON keys = only valid "selected" entries — pick by name):
{capabilities_json}"""

_USER_TEMPLATE_TASK = """TASK: Classify intent_type and select zero or more capabilities by exact name for the current turn."""

_USER_TEMPLATE_RULES = """RULES:
1. The ">>> USER RESPONDS NOW <<<" marker in history indicates the transition to the current user message.
2. Output interpretation, intent_type, selected (list of names), confidence.
3. CONVERSATIONAL intent MUST have an empty selected list.
4. Each entry in "selected" MUST be an exact CAPABILITIES key from the catalog, NOT a description or tag.
5. If the assistant's most recent message was a question and the user answers, classify INTERACTIVE.
6. Lower confidence if ambiguous{optional_instructions}"""

_USER_TEMPLATE_INTERPRETATION = """INTERPRETATION: Brief synopsis of user intent. Target one sentence, ~15-30 words."""

_USER_TEMPLATE_OUTPUT_SCHEMA = """OUTPUT (JSON only):
{{
  "interpretation": "Brief synopsis of user intent.",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|INTERACTIVE|DIRECTIVE|UNCLEAR",
  "selected": ["CapabilityName1", "CapabilityName2"],
  "confidence": 0.0-1.0
}}"""


def build_routing_user_prompt_template() -> str:
    """Assemble the routing user-prompt template.

    The returned string is a ``str.format()`` template — callers pass
    ``active_tasks_section``, ``history_section``, ``prior_fragments_section``,
    ``utterance``, ``capabilities_json``, and ``optional_instructions`` at
    render time.
    """
    sections = [
        _USER_TEMPLATE_HEADER,
        _USER_TEMPLATE_TASK,
        _USER_TEMPLATE_RULES,
        _USER_TEMPLATE_INTERPRETATION,
        _USER_TEMPLATE_OUTPUT_SCHEMA,
    ]
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Module-level constants — rebuilt through the factories for single source
# of truth.
# ---------------------------------------------------------------------------

ROUTING_SYSTEM_PROMPT = build_routing_system_prompt()
ROUTING_USER_PROMPT_TEMPLATE = build_routing_user_prompt_template()


__all__ = [
    "ANCHOR_DISAMBIGUATION_CLAUSE",
    "ROUTING_SYSTEM_PROMPT",
    "ROUTING_USER_PROMPT_TEMPLATE",
    "build_routing_system_prompt",
    "build_routing_user_prompt_template",
]
