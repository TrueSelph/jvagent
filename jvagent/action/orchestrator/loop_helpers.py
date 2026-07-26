"""Pure helpers used by the orchestrator think-act-observe loop."""

from __future__ import annotations

import re
from typing import Any, Dict

from jvagent.action.orchestrator.constants import TEXT_KEYS


def text_candidate(decision: Dict[str, Any]) -> str:
    """Extract user-facing text from a model decision dict."""
    for key in TEXT_KEYS:
        val = decision.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


# Sentence-leading markers that make a mention of a source an OFFER or a PLAN
# ("I can check the knowledge base", "shall I search?") rather than a claim to
# have already used it. Only assertions are guarded.
_HYPOTHETICAL_RE = re.compile(
    r"\b(?:can|could|would|will|shall|may|might|should|let me|want me|happy to|"
    r"I'?ll|going to|able to|if you)\b",
    re.I,
)

# Assertions that the answer came from a source the agent consulted. Deliberately
# narrow and past/assertive: each says the lookup HAS happened.
_SOURCE_CLAIM_RES = (
    re.compile(
        r"\b(?:retrieved|sourced|pulled|taken|obtained)\s+(?:\w+\s+){0,2}from\b", re.I
    ),
    re.compile(
        r"\bI\s+(?:searched|looked\s+it\s+up|looked\s+up|checked|consulted)\b", re.I
    ),
    re.compile(
        r"\baccording\s+to\s+(?:the|our|my|your)\s+"
        r"(?:knowledge\s?base|document|documents|source|sources|search)\b",
        re.I,
    ),
    re.compile(
        r"\bbased\s+on\s+(?:the|our|my|your)\s+"
        r"(?:knowledge\s?base|document|documents|search\s+result|search\s+results)\b",
        re.I,
    ),
    re.compile(r"\b(?:from|in)\s+(?:the|our|my|your)\s+knowledge\s?base\b", re.I),
    re.compile(
        r"\b(?:the\s+)?search\s+results?\s+(?:show|showed|indicate|say)\b", re.I
    ),
)

_SENTENCE_SPLIT_RE = re.compile(r"[^.!?]+[.!?]*")


def unsupported_source_claim(text: str) -> str:
    """The first sentence claiming a source was consulted, or "".

    Used only when the turn made ZERO substantive tool calls, where such a claim
    is false by construction — the orchestrator knows nothing ran. Kept
    name-free (it never looks for a specific tool) so it stays true for any
    agent's tool surface.

    Sentences that merely offer or plan a lookup are ignored; the target is the
    assertion, e.g. "Yes, that was retrieved from the knowledge base" after a
    turn that called no tool at all.
    """
    for sentence in _SENTENCE_SPLIT_RE.findall(text or ""):
        if not sentence.strip() or _HYPOTHETICAL_RE.search(sentence):
            continue
        for pattern in _SOURCE_CLAIM_RES:
            if pattern.search(sentence):
                return sentence.strip()
    return ""


# A specific worth checking: a multi-word proper noun ("University of Toronto",
# "New York") or a year. Single capitalised words are deliberately excluded —
# sentence starts, the agent's own name and ordinary title-case make them far too
# noisy to gate on.
_PROPER_NOUN_RE = re.compile(
    r"\b[A-Z][a-z]{2,}(?:\s+(?:of|the|for|and|de|van|von|da)\s+[A-Z][a-z]{2,}"
    r"|\s+[A-Z][a-z]{2,})+\b"
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower()


def unsupported_specifics(text: str, corpus: str) -> str:
    """The first concrete specific in *text* that appears nowhere in *corpus*.

    Used only when the turn made ZERO substantive tool calls. In that case every
    factual specific must already be present in what the agent can actually see —
    the user's message, the conversation, this turn's tool results. Anything else
    was invented.

    A live turn answered "where did he teach?" with "Eldon Marks taught at the
    University of Toronto" after retrieving nothing; the knowledge base never
    mentioned Toronto. That reply makes no source claim, so the claim-detector
    could not see it, but the fabricated specific is plainly checkable.

    Deliberately narrow — multi-word proper nouns and years only. Paraphrase,
    reasoning and summary from context stay untouched, which is what makes it
    safe to gate on.
    """
    haystack = _normalise(corpus)
    if not haystack:
        return ""
    for match in _PROPER_NOUN_RE.findall(text or ""):
        if _normalise(match) not in haystack:
            return match
    for year in _YEAR_RE.findall(text or ""):
        if year not in haystack:
            return year
    return ""
