"""Prior-turn history helpers for Conversation Health scoring / AI eval."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from jvagent.memory.interaction import interaction_sort_key

# Cap each history line in the AI prompt to bound tokens (product cards, long NCR dumps).
AI_HISTORY_MAX_CHARS = 300


def prior_interactions(
    ordered: Sequence[Any],
    target_id: str,
    *,
    limit: int,
) -> List[Any]:
    """Return up to ``limit`` interactions strictly before the target.

    ``ordered`` must be chronological (oldest first), sorted by
    ``interaction_sort_key``. If ``target_id`` is missing, returns empty
    (prefer empty over guessing future/wrong context).
    """
    if limit <= 0 or not ordered:
        return []
    target_key = None
    for ix in ordered:
        if str(getattr(ix, "id", "")) == str(target_id):
            target_key = interaction_sort_key(ix)
            break
    if target_key is None:
        return []
    priors = [ix for ix in ordered if interaction_sort_key(ix) < target_key]
    if not priors:
        return []
    return list(priors[-limit:])


def prior_responses_for_heuristics(priors: Sequence[Any]) -> List[str]:
    """Full prior agent responses for heuristic repetition detection."""
    out: List[str] = []
    for ix in priors:
        r = getattr(ix, "response", None)
        if r is not None and str(r).strip():
            out.append(str(r))
    return out


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def history_for_ai(
    priors: Sequence[Any],
    *,
    max_chars: int = AI_HISTORY_MAX_CHARS,
) -> List[Dict[str, str]]:
    """Format prior turns as role/content pairs for the AI eval prompt."""
    history: List[Dict[str, str]] = []
    for ix in priors:
        utterance = getattr(ix, "utterance", None)
        if utterance is not None and str(utterance).strip():
            history.append(
                {
                    "role": "user",
                    "content": _truncate(str(utterance), max_chars),
                }
            )
        response = getattr(ix, "response", None)
        if response is not None and str(response).strip():
            history.append(
                {
                    "role": "assistant",
                    "content": _truncate(str(response), max_chars),
                }
            )
    return history
