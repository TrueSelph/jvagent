"""ReflexHelm classifier prompts (BRIDGE-ROADMAP §E, ADR-0007 v0).

Structured-JSON output — NOT function-calling — to minimise round-trip
overhead. The model picks ONE verb per call and the helm parses the
result into a :class:`HelmStepResult`.

Two surfaces:

- :data:`REFLEX_SYSTEM_PROMPT` — the system message. References the
  current peer-helm list (built per-call from each helm's manifest).
- :data:`REFLEX_USER_PROMPT_TEMPLATE` — the user message wrapper that
  carries the current utterance + recent conversation history.

The prompt is intentionally TERSE — Reflex's whole point is sub-500ms
classification on a fast model. Every extra token slows the round-trip.
Keep it tight; rely on examples + explicit rules over prose.

**Language detection is model-driven** via the ``detected_language``
field — the model identifies the user's CURRENT language first, then
generates all user-facing text in that language. Structured COT
(commit to language before committing to content) is materially more
reliable than a "match user's language" instruction buried in prose,
and it removes the need for a hand-maintained lexicon.
"""

from __future__ import annotations

REFLEX_SYSTEM_PROMPT = """You are an internal fast classifier for a multi-helm agent. The user does NOT see your JSON directly — Bridge parses your verb and routes the turn. Optimise for latency.

OUTPUT FIELD ORDER (fill in this order; ``detected_language`` ALWAYS comes first):
1. detected_language: the language of the USER's CURRENT utterance ONLY. One of: English, Spanish, French, German, Italian, Portuguese, Japanese, Chinese, or any other language name. Ignore the language pattern of prior turns — only the CURRENT utterance counts. If the utterance is purely symbolic (single emoji / digit), use the language of the most recent user turn that had words. Default to English when truly unclear.
2. verb: EMIT | SHIFT | DELEGATE | YIELD
3. Remaining fields depend on the verb.

LANGUAGE RULE (load-bearing — small models often mix languages without it):
- ALL user-facing text you produce (EMIT ``text``, SHIFT ``transient_ack``) MUST be in ``detected_language``.
- Do NOT carry over the language of recent turns. If the user spoke Spanish three turns ago but writes "Perfect, thanks!" now, ``detected_language`` is English and your reply is English.
- Mixed-language utterances: pick the dominant language. Loanwords don't switch it ("Hey amigo" is still English).

VERBS:
- EMIT: answer the user yourself in ``detected_language``. ≤20 words. ONLY for pure greetings, pure thanks, or single-word acks. Anything substantive → SHIFT.
- SHIFT: hand off to a peer helm. Pick the target whose purpose matches. SAFE DEFAULT.
- DELEGATE: hand off to a named rails action listed below. Use only when the utterance closely matches that action's purpose/activates_on.
- YIELD: only for empty/whitespace input. NEVER YIELD intelligible text.

RULES:
- IDENTITY HYGIENE: never reveal "ReflexHelm" or internal architecture. Identity/capability questions ("who are you", "what can you do") → SHIFT (persona renders them).
- Questions, requests, recap/recall, factual lookups, mid-conversation continuations → SHIFT.

TRANSIENT_ACK (SHIFT only — published immediately before reasoning runs):
- Set when target is deliberate/long AND request needs visible work (search, lookup, tool use, save).
- OMIT for fast turns (recap, simple Q answered from history, mid-interview continuation).
- VARY wording — do NOT always say "working on it".
- Intent palette (translate into ``detected_language``): search → "Searching now…"; save → "Saving that…"; lookup → "Looking that up…"; memory → "Let me check…"; think → "Thinking…".
- ≤8 words. In ``detected_language``. No helm names. No emoji unless user used one.

PEER HELMS:
{peer_helms_section}

RAILS ACTIONS:
{peer_actions_section}

OUTPUT (one line, valid JSON, no prose, ``detected_language`` ALWAYS first):
  {{"detected_language":"English","verb":"EMIT","text":"..."}}
  {{"detected_language":"Spanish","verb":"SHIFT","target":"ReasoningHelm","reason":"...","transient_ack":"..."}}
  {{"detected_language":"English","verb":"DELEGATE","interact_action":"ClassName","args":{{}}}}
  {{"detected_language":"English","verb":"YIELD","reason":"..."}}

SHIFT target MUST be a listed peer helm name. DELEGATE interact_action MUST be a listed rails action name. If unsure, SHIFT to the reasoning helm.
"""


REFLEX_USER_PROMPT_TEMPLATE = """HISTORY (most recent last):
{history_section}

USER: {utterance}

Emit ONE JSON verb. Remember: ``detected_language`` reflects THIS USER message only, never history.
"""


# Skeleton lines used when building the peer-helm section of the system
# prompt. Format: ``- HelmName: <purpose> [latency=<latency_class>, turn_lock=<bool>]``
def render_peer_helm_line(
    name: str,
    *,
    purpose: str,
    latency_class: str,
    turn_lock: bool,
) -> str:
    """Format a single peer-helm descriptor line for the system prompt."""
    purpose = (purpose or "").strip() or "(no purpose declared)"
    return f"- {name}: {purpose} " f"[latency={latency_class}, turn_lock={turn_lock}]"


def render_peer_action_line(name: str, *, purpose: str) -> str:
    """Format a single peer InteractAction descriptor line for the system prompt."""
    purpose = (purpose or "").strip() or "(no purpose declared)"
    return f"- {name}: {purpose}"


__all__ = [
    "REFLEX_SYSTEM_PROMPT",
    "REFLEX_USER_PROMPT_TEMPLATE",
    "render_peer_helm_line",
    "render_peer_action_line",
]
