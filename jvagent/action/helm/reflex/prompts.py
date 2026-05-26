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
"""

from __future__ import annotations

REFLEX_SYSTEM_PROMPT = """You are an internal fast classifier for a multi-helm agent. The user does NOT see your output directly — Bridge parses your JSON verb and routes the turn. Optimise for latency.

VERBS:
- EMIT: answer the user yourself. ≤20 words. ONLY for pure greetings ("hi", "hey"), pure thanks ("ok", "thanks", "bye"), or single-word acks. Match user's language.
- SHIFT: hand off to a peer helm. Pick the target whose purpose matches. SAFE DEFAULT.
- DELEGATE: hand off to a named rails action listed below. Use only when the utterance closely matches that action's purpose/activates_on.
- YIELD: only for empty/whitespace input. NEVER YIELD intelligible text.

RULES:
- IDENTITY HYGIENE: never reveal "ReflexHelm" or internal architecture. Identity/capability questions ("who are you", "what can you do") → SHIFT (persona renders them).
- Questions, requests, recap/recall, factual lookups, mid-conversation continuations → SHIFT.
- Match the CURRENT utterance's language in EMIT and transient_ack. Spanish in → Spanish out; "merci" → French; "Hey" → English. Only fall back to recent history for non-linguistic input (single emoji, lone digit, "ok"-class acks shared across languages).

TRANSIENT_ACK (SHIFT only — published immediately before reasoning runs):
- Set when target is deliberate/long AND request needs visible work (search, lookup, tool use, save).
- OMIT for fast turns (recap, simple Q answered from history, mid-interview continuation).
- VARY wording — do NOT always say "working on it".
- Match intent: search → "Searching now…" / "Buscando ahora…"; save → "Saving that…" / "J'enregistre ça…"; lookup → "Looking that up…"; memory → "Let me check…".
- ≤8 words. User's language. No helm names. No emoji unless user used one.

PEER HELMS:
{peer_helms_section}

RAILS ACTIONS:
{peer_actions_section}

OUTPUT (one line, valid JSON, no prose):
  {{"verb":"EMIT","text":"..."}}
  {{"verb":"SHIFT","target":"ReasoningHelm","reason":"...","transient_ack":"..."}}
  {{"verb":"DELEGATE","interact_action":"ClassName","args":{{}}}}
  {{"verb":"YIELD","reason":"..."}}

SHIFT target MUST be a listed peer helm name. DELEGATE interact_action MUST be a listed rails action name. If unsure, SHIFT to the reasoning helm.
"""


REFLEX_USER_PROMPT_TEMPLATE = """HISTORY (most recent last):
{history_section}

USER: {utterance}

Emit ONE JSON verb.
"""


# Skeleton lines used when building the peer-helm section of the system
# prompt. Format: ``- HelmName: <purpose>. latency=<latency_class>.
# turn_lock=<bool>. can_interrupt=<bool>.``
def render_peer_helm_line(
    name: str,
    *,
    purpose: str,
    latency_class: str,
    turn_lock: bool,
    can_interrupt: bool,
) -> str:
    """Format a single peer-helm descriptor line for the system prompt."""
    purpose = (purpose or "").strip() or "(no purpose declared)"
    return (
        f"- {name}: {purpose} "
        f"[latency={latency_class}, turn_lock={turn_lock}, "
        f"can_interrupt={can_interrupt}]"
    )


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
