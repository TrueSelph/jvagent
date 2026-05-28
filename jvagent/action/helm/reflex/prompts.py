"""ReflexHelm classifier prompts (BRIDGE-ROADMAP §E, ADR-0007, ADR-0009).

Structured-JSON output — NOT function-calling — to minimise round-trip
overhead. The model picks ONE verb per call and the helm parses the
result into a :class:`HelmStepResult`.

Surfaces:

- :data:`REFLEX_SYSTEM_PROMPT` — the system message. References the
  current peer-helm list, the conditional ``HELMS AVAILABLE`` block
  (multi-deliberate-helm agents only), and the anchor-routable IA
  catalog (built per-call from each IA's manifest + ``anchors``).
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

**Anchor disambiguation clause (ADR-0009).** The
:data:`ANCHOR_DISAMBIGUATION_CLAUSE` is a cross-module invariant pinned
identically to ``jvagent/action/router/prompts.py``. The clause MUST
remain byte-identical between the two locations — see
``tests/action/router/test_anchor_disambiguation.py``.
"""

from __future__ import annotations

from typing import Iterable, List

# ---------------------------------------------------------------------------
# Anchor disambiguation clause (cross-module invariant)
# ---------------------------------------------------------------------------
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


REFLEX_SYSTEM_PROMPT = """You are an internal fast classifier for a multi-helm agent. The user does NOT see your JSON directly — Bridge parses your verb and routes the turn. Optimise for latency.

OUTPUT FIELD ORDER (fill in this order; ``detected_language`` ALWAYS comes first):
1. detected_language: the language of the USER's CURRENT utterance ONLY. One of: English, Spanish, French, German, Italian, Portuguese, Japanese, Chinese, or any other language name. Ignore the language pattern of prior turns — only the CURRENT utterance counts. If the utterance is purely symbolic (single emoji / digit), use the language of the most recent user turn that had words. Default to English when truly unclear.
2. verb: EMIT | SHIFT | DELEGATE | YIELD
3. Remaining fields depend on the verb.

LANGUAGE RULE (load-bearing — small models often mix languages without it):
- ALL user-facing text you produce (EMIT ``text``, SHIFT ``transient_ack``) MUST be in ``detected_language``.
- Do NOT carry over the language of recent turns. If the user spoke Spanish three turns ago but writes "Perfect, thanks!" now, ``detected_language`` is English and your reply is English.
- Mixed-language utterances: pick the dominant language. Loanwords don't switch it ("Hey amigo" is still English).

VERBS (priority order — try in this order):
1. DELEGATE: hand off to a named anchor-routable flow listed below. **MANDATORY when the utterance matches any anchor by verb+object intent.** The flow owns the next response; you do NOT generate text. Pick this BEFORE considering SHIFT.
2. SHIFT: hand off to a peer helm. Use when the turn is substantive but no flow anchor matches. SAFE DEFAULT for substantive turns without anchor hits.
3. EMIT: answer the user yourself in ``detected_language``. ≤20 words. ONLY for pure greetings, pure thanks, single-word acks, or identity-hygiene refusals. NEVER EMIT a substantive answer — that's the engine's job via SHIFT or DELEGATE.
4. YIELD: only for empty/whitespace input. NEVER YIELD intelligible text.

DECISION ORDER (apply top-down — first match wins):
1. Does the utterance match an anchor in ANCHOR-ROUTABLE FLOWS below? → DELEGATE to that flow.
2. Is the utterance a pure greeting / pure thanks / single-word ack? → EMIT.
3. Is the utterance empty / whitespace? → YIELD.
4. Otherwise → SHIFT to the default reasoning helm.

RULES:
- IDENTITY HYGIENE: never reveal "ReflexHelm" or internal architecture. Identity/capability questions ("who are you", "what can you do") → SHIFT (persona renders them).
- Questions, requests, recap/recall, factual lookups, mid-conversation continuations → SHIFT (after the DELEGATE check above fails).

TRANSIENT_ACK (SHIFT only — published immediately before reasoning runs):
- Set when target is deliberate/long AND request needs visible work (search, lookup, tool use, save).
- OMIT for fast turns (recap, simple Q answered from history, mid-interview continuation).
- VARY wording — do NOT always say "working on it".
- Intent palette (translate into ``detected_language``): search → "Searching now…"; save → "Saving that…"; lookup → "Looking that up…"; memory → "Let me check…"; think → "Thinking…".
- ≤8 words. In ``detected_language``. No helm names. No emoji unless user used one.

PEER HELMS:
{peer_helms_section}
{helms_available_section}
ANCHOR-ROUTABLE FLOWS:
{peer_actions_section}

{anchor_disambiguation_clause}

OUTPUT (one line, valid JSON, no prose, ``detected_language`` ALWAYS first):
  {{"detected_language":"English","verb":"EMIT","text":"..."}}
  {{"detected_language":"Spanish","verb":"SHIFT","target":"ReasoningHelm","reason":"...","transient_ack":"..."}}
  {{"detected_language":"English","verb":"DELEGATE","interact_action":"ClassName","args":{{}}}}
  {{"detected_language":"English","verb":"YIELD","reason":"..."}}

SHIFT target MUST be a listed peer helm name. DELEGATE interact_action MUST be a listed anchor-routable flow. **Anchor match → DELEGATE is non-negotiable.** Only fall through to SHIFT when NO anchor matches.
"""


REFLEX_USER_PROMPT_TEMPLATE = """HISTORY (most recent last):
{history_section}

USER: {utterance}

Emit ONE JSON verb. Remember: ``detected_language`` reflects THIS USER message only, never history.
"""


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


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


def render_peer_action_block(
    name: str,
    *,
    description: str,
    anchors: Iterable[str],
) -> str:
    """Format an anchor-routable IA as a multi-line block (ADR-0009).

    Block shape::

        - {name}
            description: {description}
            anchors:
              - {anchor 1}
              - {anchor 2}

    When ``anchors`` is empty (operator misconfiguration), the ``anchors:``
    sub-block is omitted — the bootstrap warning catches that case.
    """
    description = (description or "").strip() or "(no description declared)"
    lines: List[str] = [f"- {name}", f"    description: {description}"]
    anchor_list = [a.strip() for a in anchors if isinstance(a, str) and a.strip()]
    if anchor_list:
        lines.append("    anchors:")
        for a in anchor_list:
            lines.append(f"      - {a}")
    return "\n".join(lines)


def render_helms_available_block(
    deliberate_helms: List[dict],
) -> str:
    """Render the conditional ``HELMS AVAILABLE`` block (ADR-0009 §3).

    Emits an empty string when ≤1 deliberate helm is installed so
    single-Reasoning agents pay nothing in prompt tokens.

    Each entry must carry ``{"name", "purpose"}``. Helms with empty
    purpose render as ``(no purpose declared)``.
    """
    if len(deliberate_helms) <= 1:
        return ""
    lines: List[str] = ["", "HELMS AVAILABLE for substantive turns:"]
    for h in deliberate_helms:
        name = h.get("name", "")
        if not name:
            continue
        purpose = (h.get("purpose") or "").strip() or "(no purpose declared)"
        lines.append(f"  {name}: {purpose}")
    lines.append("")
    lines.append(
        "If the user's intent strongly matches a specialist helm's purpose, "
        "SHIFT to that helm. Otherwise SHIFT to ReasoningHelm."
    )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ANCHOR_DISAMBIGUATION_CLAUSE",
    "REFLEX_SYSTEM_PROMPT",
    "REFLEX_USER_PROMPT_TEMPLATE",
    "render_helms_available_block",
    "render_peer_action_block",
    "render_peer_helm_line",
]
