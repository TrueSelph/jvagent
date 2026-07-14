"""Health Day Buckets and contribution deltas for agent readings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, MutableMapping, Optional

from .constants import DIMENSIONS


def utc_day_str(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def window_day_list(n_days: int, end: Optional[datetime] = None) -> List[str]:
    end = end or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)
    days = []
    for i in range(n_days):
        d = end - timedelta(days=i)
        days.append(d.strftime("%Y-%m-%d"))
    return list(reversed(days))


def empty_day_bucket(day: str) -> Dict[str, Any]:
    return {
        "day": day,
        "interaction_count": 0,
        "flagged_count": 0,
        "dimension_sums": {d: 0 for d in DIMENSIONS},
        "issue_counts": {},
        "unflagged_eligible": 0,
        "unflagged_selected": 0,
        "a_eligible": 0,
        "a_selected": 0,
        "b_eligible": 0,
        "b_selected": 0,
        "critical_enqueued": 0,
    }


def ensure_day(
    day_buckets: MutableMapping[str, Any], day: str
) -> Dict[str, Any]:
    b = day_buckets.get(day)
    if not isinstance(b, dict):
        b = empty_day_bucket(day)
        day_buckets[day] = b
    # Backfill keys for older buckets
    for k, v in empty_day_bucket(day).items():
        if k not in b:
            b[k] = v if not isinstance(v, dict) else dict(v)
    if not isinstance(b.get("dimension_sums"), dict):
        b["dimension_sums"] = {d: 0 for d in DIMENSIONS}
    if not isinstance(b.get("issue_counts"), dict):
        b["issue_counts"] = {}
    return b


def apply_contribution(
    day_buckets: MutableMapping[str, Any],
    contribution: Optional[Mapping[str, Any]],
    sign: int = 1,
) -> None:
    """Add (sign=+1) or remove (sign=-1) a turn contribution from its day bucket."""
    if not contribution:
        return
    day = str(contribution.get("day") or "")
    if not day:
        return
    b = ensure_day(day_buckets, day)
    b["interaction_count"] = max(0, int(b.get("interaction_count") or 0) + sign)
    if contribution.get("flagged"):
        b["flagged_count"] = max(0, int(b.get("flagged_count") or 0) + sign)

    sums = b.setdefault("dimension_sums", {d: 0 for d in DIMENSIONS})
    dims = contribution.get("dimensions") or {}
    for d in DIMENSIONS:
        try:
            sums[d] = int(sums.get(d) or 0) + sign * int(dims.get(d) or 0)
        except (TypeError, ValueError):
            pass

    counts = b.setdefault("issue_counts", {})
    for code in contribution.get("issue_codes") or []:
        c = str(code)
        if not c:
            continue
        counts[c] = max(0, int(counts.get(c) or 0) + sign)


def bump_sampling_eligible(
    day_buckets: MutableMapping[str, Any], day: str, bucket: str
) -> None:
    b = ensure_day(day_buckets, day)
    if bucket == "C":
        return
    b["unflagged_eligible"] = int(b.get("unflagged_eligible") or 0) + 1
    if bucket == "A":
        b["a_eligible"] = int(b.get("a_eligible") or 0) + 1
    elif bucket == "B":
        b["b_eligible"] = int(b.get("b_eligible") or 0) + 1


def bump_sampling_selected(
    day_buckets: MutableMapping[str, Any], day: str, bucket: str
) -> None:
    b = ensure_day(day_buckets, day)
    if bucket == "C":
        b["critical_enqueued"] = int(b.get("critical_enqueued") or 0) + 1
        return
    b["unflagged_selected"] = int(b.get("unflagged_selected") or 0) + 1
    if bucket == "A":
        b["a_selected"] = int(b.get("a_selected") or 0) + 1
    elif bucket == "B":
        b["b_selected"] = int(b.get("b_selected") or 0) + 1


def prune_old_days(
    day_buckets: MutableMapping[str, Any], keep_days: int = 30
) -> None:
    """Drop buckets older than keep_days to bound document size."""
    if keep_days <= 0 or not day_buckets:
        return
    allowed = set(window_day_list(keep_days))
    stale = [k for k in list(day_buckets.keys()) if k not in allowed]
    for k in stale:
        del day_buckets[k]


def agent_reading_from_buckets(
    day_buckets: Mapping[str, Any],
    days: int = 7,
) -> Dict[str, Any]:
    day_list = window_day_list(days)
    interaction_count = 0
    flagged_count = 0
    dim_sums = {d: 0 for d in DIMENSIONS}
    issue_counts: Dict[str, int] = {}
    unflagged_eligible = 0
    unflagged_selected = 0
    trend = []

    for day in day_list:
        b = day_buckets.get(day) or empty_day_bucket(day)
        ic = int(b.get("interaction_count") or 0)
        fc = int(b.get("flagged_count") or 0)
        interaction_count += ic
        flagged_count += fc
        sums = b.get("dimension_sums") or {}
        for d in DIMENSIONS:
            dim_sums[d] += int(sums.get(d) or 0)
        for code, n in (b.get("issue_counts") or {}).items():
            issue_counts[str(code)] = issue_counts.get(str(code), 0) + int(n or 0)
        unflagged_eligible += int(b.get("unflagged_eligible") or 0)
        unflagged_selected += int(b.get("unflagged_selected") or 0)
        day_avg = None
        if ic > 0:
            day_avg = {
                d: round(int(sums.get(d) or 0) / ic, 2) for d in DIMENSIONS
            }
        trend.append(
            {
                "day": day,
                "interaction_count": ic,
                "flagged_count": fc,
                "avg_dimensions": day_avg,
            }
        )

    avg = None
    avg_score = None
    if interaction_count > 0:
        avg = {
            d: round(dim_sums[d] / interaction_count, 2) for d in DIMENSIONS
        }
        # Raw agent health score = equal-weight mean of dimension averages
        avg_score = round(sum(avg.values()) / len(DIMENSIONS), 2)

    ambient_rate = (
        round(unflagged_selected / unflagged_eligible, 4)
        if unflagged_eligible
        else 0.0
    )

    top_issues = sorted(
        issue_counts.items(), key=lambda x: (-x[1], x[0])
    )[:10]

    # Per-day composite on trend for sparklines
    for point in trend:
        day_avg = point.get("avg_dimensions")
        if day_avg:
            point["avg_score"] = round(
                sum(float(day_avg[d]) for d in DIMENSIONS) / len(DIMENSIONS), 2
            )
        else:
            point["avg_score"] = None

    return {
        "window_days": days,
        "interaction_count": interaction_count,
        "flagged_count": flagged_count,
        "flag_rate": (
            round(flagged_count / interaction_count, 4) if interaction_count else 0.0
        ),
        "avg_score": avg_score,
        "avg_dimensions": avg,
        "top_issues": [{"code": c, "count": n} for c, n in top_issues],
        "ambient": {
            "unflagged_eligible": unflagged_eligible,
            "unflagged_selected": unflagged_selected,
            "ambient_rate": ambient_rate,
        },
        "trend": trend,
    }
