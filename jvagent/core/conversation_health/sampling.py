"""AI sampling buckets and rolling ambient cap decisions."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping, Tuple

from .constants import (
    AI_BUCKET_BLIND_SPOT,
    AI_BUCKET_CRITICAL,
    AI_BUCKET_OPTIMIZATION,
)
from .scoring import is_critical


def assign_bucket(
    *,
    health_score: float,
    dimensions: Mapping[str, int],
    issues: List[Dict[str, Any]],
    flagged: bool,
    flag_threshold: float = 70.0,
    optimization_ceiling: float = 90.0,
) -> str:
    if is_critical(
        dimensions=dimensions,
        issues=issues,
        flagged=flagged,
        health_score=health_score,
        flag_threshold=flag_threshold,
    ):
        return AI_BUCKET_CRITICAL
    if health_score < optimization_ceiling:
        return AI_BUCKET_OPTIMIZATION
    return AI_BUCKET_BLIND_SPOT


def _stable_unit_interval(key: str) -> float:
    """Deterministic [0, 1) from a string key."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    # Use first 8 hex chars → 32-bit int
    return (int(digest[:8], 16) % 10_000_000) / 10_000_000.0


def soft_rate_selected(interaction_id: str, bucket: str, target_rate: float) -> bool:
    if target_rate <= 0:
        return False
    if target_rate >= 1:
        return True
    return _stable_unit_interval(f"{bucket}:{interaction_id}") < float(target_rate)


def sum_window_counters(
    day_buckets: Mapping[str, Mapping[str, Any]],
    days: List[str],
) -> Dict[str, int]:
    keys = (
        "unflagged_eligible",
        "unflagged_selected",
        "a_eligible",
        "a_selected",
        "b_eligible",
        "b_selected",
    )
    out = dict.fromkeys(keys, 0)
    for day in days:
        b = day_buckets.get(day) or {}
        for k in keys:
            try:
                out[k] += int(b.get(k) or 0)
            except (TypeError, ValueError):
                pass
    return out


def ambient_cap_allows(
    *,
    window: Mapping[str, int],
    bucket: str,
    unflagged_ambient_max_rate: float = 0.05,
    ambient_b_share: float = 0.5,
    ambient_a_share: float = 0.5,
    ambient_spillover: bool = True,
) -> bool:
    """Return True if enqueueing one more ambient AI for bucket is under cap."""
    eligible = int(window.get("unflagged_eligible") or 0)
    selected = int(window.get("unflagged_selected") or 0)
    if eligible <= 0:
        # First unflagged turn: allow if rate > 0 (1/1 will be checked after bump)
        # We check after hypothetical +1 eligible on caller side.
        pass

    # After caller has already incremented eligible for this turn:
    if eligible > 0 and (selected + 1) / eligible > unflagged_ambient_max_rate + 1e-12:
        return False

    share = ambient_b_share if bucket == AI_BUCKET_OPTIMIZATION else ambient_a_share
    max_for_bucket = unflagged_ambient_max_rate * share
    if bucket == AI_BUCKET_OPTIMIZATION:
        b_el = int(window.get("b_eligible") or 0)
        b_sel = int(window.get("b_selected") or 0)
        # Prefer share of global unflagged eligible
        global_budget = eligible * max_for_bucket
        if b_el > 0 and (b_sel + 1) > global_budget + 1e-9:
            if not ambient_spillover:
                return False
            # Spillover: allow if global ambient still has room (already checked)
            a_sel = int(window.get("a_selected") or 0)
            if (selected + 1) > eligible * unflagged_ambient_max_rate + 1e-9:
                return False
            # If A under-used, allow B to take spillover
            a_budget = eligible * unflagged_ambient_max_rate * ambient_a_share
            if a_sel >= a_budget - 1e-9 and (b_sel + 1) > global_budget + 1e-9:
                return False
        return True

    if bucket == AI_BUCKET_BLIND_SPOT:
        a_el = int(window.get("a_eligible") or 0)
        a_sel = int(window.get("a_selected") or 0)
        global_budget = eligible * max_for_bucket
        if a_el > 0 and (a_sel + 1) > global_budget + 1e-9:
            if not ambient_spillover:
                return False
            b_sel = int(window.get("b_selected") or 0)
            b_budget = eligible * unflagged_ambient_max_rate * ambient_b_share
            if b_sel >= b_budget - 1e-9 and (a_sel + 1) > global_budget + 1e-9:
                return False
        return True

    return False


def decide_ai_schedule(
    *,
    interaction_id: str,
    bucket: str,
    day_buckets: Mapping[str, Mapping[str, Any]],
    window_days: List[str],
    ambient_b_target_rate: float = 0.18,
    ambient_a_target_rate: float = 0.02,
    unflagged_ambient_max_rate: float = 0.05,
    ambient_b_share: float = 0.5,
    ambient_a_share: float = 0.5,
    ambient_spillover: bool = True,
    already_selected: bool = False,
) -> Tuple[bool, str, str]:
    """
    Decide whether to schedule deferred AI.

    Returns:
        (should_schedule, ai_status, ai_select_reason)
    """
    if already_selected:
        return True, "queued", "already_selected"

    if bucket == AI_BUCKET_CRITICAL:
        return True, "queued", "critical"

    target = (
        ambient_b_target_rate
        if bucket == AI_BUCKET_OPTIMIZATION
        else ambient_a_target_rate
    )
    if not soft_rate_selected(interaction_id, bucket, target):
        return False, "not_selected", "soft_rate"

    # Window includes today's eligible already bumped by caller
    window = sum_window_counters(day_buckets, window_days)
    if not ambient_cap_allows(
        window=window,
        bucket=bucket,
        unflagged_ambient_max_rate=unflagged_ambient_max_rate,
        ambient_b_share=ambient_b_share,
        ambient_a_share=ambient_a_share,
        ambient_spillover=ambient_spillover,
    ):
        return False, "skipped_budget", "ambient_cap"

    reason = "ambient_b" if bucket == AI_BUCKET_OPTIMIZATION else "ambient_a"
    return True, "queued", reason
