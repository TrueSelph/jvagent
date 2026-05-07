"""Local pre-classifier for the cockpit router.

Cheap heuristic that fires BEFORE any LLM call. When the utterance is an
unambiguous greeting / thanks / goodbye and no active task is in flight,
the router returns a synthetic ``RoutingResult`` directing the cockpit to
the ``converse`` skill — saving a router LLM round-trip on smalltalk.

Conservative by design:

- Length capped tightly. Longer utterances probably carry intent.
- Whitelist of exact tokens / short phrases only. Substring / prefix
  matching is intentionally avoided ("hi" should not match "highway").
- Does NOT fire when the conversation has any active task — fragments
  ("yes", "no", short answers) might be answers to an in-flight interview.
- Ambiguous acknowledgments ("ok", "got it", "alright") are excluded
  since they could be answers to an assistant question. They cost a
  router LLM call; that's the cheaper failure mode.
- No matching against substrings, prefixes, or contains — exact-equal
  on the cleaned token only.

When the heuristic fires, the synthesised ``RoutingResult`` carries:

- ``posture=RESPOND``
- ``intent_type=CONVERSATIONAL``
- ``actions=["converse"]`` (so the cockpit gate dispatches structurally)
- ``confidence=0.95``
- ``interpretation`` describing the heuristic match
- ``raw_response="<preclassifier:bucket>"`` for trace introspection
"""

from __future__ import annotations

import re
from typing import Any, Optional

from jvagent.action.cockpit.delivery.gates import CONVERSE_SKILL_NAMES
from jvagent.action.cockpit.routing.types import POSTURE_RESPOND, RoutingResult

# Longest pre-classifier match is "you are very welcome" (20 chars). Anything
# beyond ~30 chars is likely substantive content; bail out fast.
MAX_UTTERANCE_LENGTH = 30


# ---------------------------------------------------------------------------
# Phrase buckets
# ---------------------------------------------------------------------------
#
# Each bucket maps a category label (used for trace introspection only) to
# the set of canonical phrases. Phrases are stored in lowercase with
# punctuation stripped — `_normalize_utterance` produces the same shape so
# membership is a single set lookup.

_GREETINGS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "yo",
        "sup",
        "howdy",
        "greetings",
        "good morning",
        "good afternoon",
        "good evening",
        "good day",
        "hey there",
        "hi there",
        "hello there",
    }
)

_THANKS = frozenset(
    {
        "thanks",
        "thank you",
        "thx",
        "ty",
        "tysm",
        "thanks a lot",
        "thanks so much",
        "thank you so much",
        "thank you very much",
        "many thanks",
        "thanks again",
    }
)

_GOODBYES = frozenset(
    {
        "bye",
        "goodbye",
        "see you",
        "see ya",
        "later",
        "ttyl",
        "cya",
        "good night",
        "goodnight",
        "take care",
        "farewell",
    }
)

_PLEASANTRIES = frozenset(
    {
        "youre welcome",
        "you are welcome",
        "no problem",
        "np",
        "no worries",
    }
)


_BUCKETS = {
    "greeting": _GREETINGS,
    "thanks": _THANKS,
    "goodbye": _GOODBYES,
    "pleasantry": _PLEASANTRIES,
}


# Apostrophe variants that show up in utterances ("you're", "you’re"). Strip
# all forms so the canonical bucket entries (which use no apostrophe) match.
_APOSTROPHES = "’ʼ'`"
_APOSTROPHE_RE = re.compile(f"[{re.escape(_APOSTROPHES)}]")
_NON_LETTER_RE = re.compile(r"[^a-z\s]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_utterance(utterance: str) -> str:
    """Lowercase, strip apostrophes / non-letter chars, collapse whitespace."""
    text = (utterance or "").strip().lower()
    if not text:
        return ""
    text = _APOSTROPHE_RE.sub("", text)
    text = _NON_LETTER_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def classify_smalltalk(utterance: str) -> Optional[str]:
    """Return the bucket label (``"greeting"``, ``"thanks"``, ...) for a match, or None.

    Pure function — does not look at active tasks or conversation state.
    Caller must gate by active-task presence before applying the result.
    """
    if not utterance or len(utterance) > MAX_UTTERANCE_LENGTH:
        return None
    cleaned = _normalize_utterance(utterance)
    if not cleaned:
        return None
    for label, bucket in _BUCKETS.items():
        if cleaned in bucket:
            return label
    return None


def has_active_tasks(visitor: Any) -> bool:
    """Return True if ``visitor.tasks`` exposes any active task.

    Defensive: missing visitor / conversation / task store all return False
    so the pre-classifier proceeds in test setups that don't wire a store.
    """
    if visitor is None:
        return False
    conv = getattr(visitor, "conversation", None)
    if conv is None:
        return False
    try:
        store = getattr(visitor, "tasks", None)
    except Exception:
        return False
    if store is None:
        return False
    try:
        active = store.list(status="active")
    except Exception:
        return False
    return bool(active)


def _converse_skill_name() -> str:
    return CONVERSE_SKILL_NAMES[0]


def synthesize_smalltalk_routing(bucket: str, utterance: str) -> RoutingResult:
    """Build a synthetic RoutingResult that dispatches to the converse skill.

    ``bucket`` is the label returned by ``classify_smalltalk`` and is
    surfaced in the result's ``interpretation`` and ``raw_response`` fields
    for trace introspection.
    """
    interp_for_bucket = {
        "greeting": "User greeted the agent — fast-path to the converse skill.",
        "thanks": "User thanked the agent — fast-path to the converse skill.",
        "goodbye": "User signing off — fast-path to the converse skill.",
        "pleasantry": "Smalltalk pleasantry — fast-path to the converse skill.",
    }.get(bucket, "Smalltalk fast-path to the converse skill.")

    return RoutingResult(
        posture=POSTURE_RESPOND,
        interpretation=interp_for_bucket,
        intent_type="CONVERSATIONAL",
        actions=[_converse_skill_name()],
        interact_actions=[],
        confidence=0.95,
        canned_response="",
        needs_clarification=False,
        raw_response=f"<preclassifier:{bucket}>",
    )


def maybe_preclassify(
    visitor: Any,
    utterance: str,
    *,
    enabled: bool = True,
) -> Optional[RoutingResult]:
    """Top-level entrypoint used by ``CockpitRouter.route``.

    Returns a synthetic ``RoutingResult`` when:

    - ``enabled`` is True (operator can disable via cockpit config)
    - the utterance matches a smalltalk bucket
    - no active task is in flight on the conversation

    Returns None otherwise — caller proceeds with the normal LLM route.
    """
    if not enabled:
        return None
    bucket = classify_smalltalk(utterance)
    if bucket is None:
        return None
    if has_active_tasks(visitor):
        return None
    return synthesize_smalltalk_routing(bucket, utterance)


__all__ = [
    "MAX_UTTERANCE_LENGTH",
    "classify_smalltalk",
    "has_active_tasks",
    "synthesize_smalltalk_routing",
    "maybe_preclassify",
]
