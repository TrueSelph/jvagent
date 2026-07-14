"""Dimension scores, Flagged state, and Heuristic Health Score."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .constants import DIMENSIONS


def score_dimensions(
    issues: Sequence[Mapping[str, Any]],
) -> Dict[str, int]:
    """Penalty-from-100 per dimension from issue deductions."""
    totals = {d: 0 for d in DIMENSIONS}
    for issue in issues:
        dim = str(issue.get("dimension") or "")
        if dim not in totals:
            continue
        try:
            ded = int(issue.get("deduction") or 0)
        except (TypeError, ValueError):
            ded = 0
        totals[dim] += max(0, ded)
    return {d: max(0, 100 - totals[d]) for d in DIMENSIONS}


def heuristic_health_score(dimensions: Mapping[str, int]) -> float:
    """Equal-weight mean of the four dimensions."""
    vals = [float(dimensions.get(d, 100)) for d in DIMENSIONS]
    if not vals:
        return 100.0
    return round(sum(vals) / len(vals), 2)


def is_flagged(
    dimensions: Mapping[str, int],
    issues: Sequence[Mapping[str, Any]],
    flag_threshold: float = 70.0,
) -> bool:
    """Flag when any dimension < floor or any high-severity issue."""
    for d in DIMENSIONS:
        if float(dimensions.get(d, 100)) < flag_threshold:
            return True
    for issue in issues:
        if str(issue.get("severity") or "").lower() == "high":
            return True
    return False


def is_critical(
    *,
    dimensions: Mapping[str, int],
    issues: Sequence[Mapping[str, Any]],
    flagged: bool,
    health_score: float,
    flag_threshold: float = 70.0,
) -> bool:
    """Bucket C: score < threshold, flagged, or critical issue codes."""
    if flagged or health_score < flag_threshold:
        return True
    critical_codes = {
        "toxicity",
        "execution_failure",
        "prompt_injection_attempt",
        "human_request",
        "empty_or_trivial_response",
    }
    for issue in issues:
        if str(issue.get("code") or "") in critical_codes:
            return True
        if str(issue.get("severity") or "").lower() == "high" and float(
            dimensions.get(str(issue.get("dimension") or ""), 100)
        ) < flag_threshold:
            return True
    return False


def build_contribution(
    *,
    day: str,
    dimensions: Mapping[str, int],
    flagged: bool,
    issues: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    return {
        "day": day,
        "dimensions": {d: int(dimensions.get(d, 100)) for d in DIMENSIONS},
        "flagged": bool(flagged),
        "issue_codes": [str(i.get("code")) for i in issues if i.get("code")],
    }


def build_interaction_health(
    *,
    scored: bool,
    skip_reason: Optional[str] = None,
    dimensions: Optional[Mapping[str, int]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    flagged: bool = False,
    health_score: Optional[float] = None,
    ai_bucket: Optional[str] = None,
    ai_status: str = "none",
    ai_select_reason: Optional[str] = None,
    evaluation_tier: str = "heuristic",
    scored_at: str = "",
    contribution: Optional[Dict[str, Any]] = None,
    agent_id: str = "",
    ai_selected: bool = False,
) -> Dict[str, Any]:
    dims = dict(dimensions or {d: 100 for d in DIMENSIONS})
    iss = list(issues or [])
    hs = (
        health_score
        if health_score is not None
        else heuristic_health_score(dims)
    )
    return {
        "scored": scored,
        "skip_reason": skip_reason,
        "flagged": flagged if scored else False,
        "dimensions": dims,
        "issues": iss,
        "heuristic_health_score": hs,
        "ai_bucket": ai_bucket,
        "ai_status": ai_status,
        "ai_select_reason": ai_select_reason,
        "ai_selected": ai_selected,
        "evaluation_tier": evaluation_tier,
        "scored_at": scored_at,
        "contribution": contribution or {},
        "agent_id": agent_id,
    }


def recompute_conversation_rollup(
    turn_healths: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """avg + min per dimension across scored turns, plus composite scores.

    Composite scores (derived, equal-weight mean of the four dimensions unless a
    turn already has ``heuristic_health_score``):

    - ``avg_score``: mean of per-turn health scores (overall conversation health)
    - ``min_score``: worst turn health score (risk signal for list UIs)
    """
    scored = [h for h in turn_healths if h.get("scored")]
    if not scored:
        return {
            "flagged": False,
            "scored_turn_count": 0,
            "last_scored_at": None,
            "avg_score": None,
            "min_score": None,
            "avg": {d: None for d in DIMENSIONS},
            "min": {d: None for d in DIMENSIONS},
            "issue_counts": {},
        }

    sums = {d: 0.0 for d in DIMENSIONS}
    mins = {d: 100 for d in DIMENSIONS}
    issue_counts: Dict[str, int] = {}
    flagged = False
    last_scored_at = None
    turn_scores: List[float] = []

    for h in scored:
        dims = h.get("dimensions") or {}
        for d in DIMENSIONS:
            v = int(dims.get(d, 100))
            sums[d] += v
            mins[d] = min(mins[d], v)
        # Prefer stored turn score; else mean of dimensions
        if h.get("heuristic_health_score") is not None:
            try:
                turn_scores.append(float(h["heuristic_health_score"]))
            except (TypeError, ValueError):
                turn_scores.append(heuristic_health_score(dims))
        else:
            turn_scores.append(heuristic_health_score(dims))
        if h.get("flagged"):
            flagged = True
        for issue in h.get("issues") or []:
            code = str(issue.get("code") or "")
            if code:
                issue_counts[code] = issue_counts.get(code, 0) + 1
        ts = h.get("scored_at")
        if ts and (last_scored_at is None or str(ts) > str(last_scored_at)):
            last_scored_at = ts

    n = len(scored)
    avg = {d: round(sums[d] / n, 2) for d in DIMENSIONS}
    avg_score = round(sum(turn_scores) / len(turn_scores), 2) if turn_scores else None
    min_score = round(min(turn_scores), 2) if turn_scores else None
    return {
        "flagged": flagged,
        "scored_turn_count": n,
        "last_scored_at": last_scored_at,
        "avg_score": avg_score,
        "min_score": min_score,
        "avg": avg,
        "min": mins,
        "issue_counts": issue_counts,
    }
