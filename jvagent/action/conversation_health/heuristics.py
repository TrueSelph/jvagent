"""Deterministic issue detectors for Conversation Health (no LLM)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence

from .constants import DEFAULT_LATENCY_BANDS, DEFAULT_SEVERITY_DEDUCTIONS

# ── Patterns ──────────────────────────────────────────────────────────────────

_IDK_RE = re.compile(
    r"\b("
    r"i\s+don'?t\s+know|i\s+do\s+not\s+know|i'?m\s+not\s+sure|"
    r"i\s+cannot\s+help\s+with\s+that|i\s+can'?t\s+help\s+with\s+that|"
    r"i\s+don'?t\s+have\s+(enough\s+)?information|"
    r"as\s+an\s+ai(,?\s+i)?\s+(don'?t|cannot|can'?t)"
    r")\b",
    re.I,
)

_QUESTION_RE = re.compile(
    r"(\?|^\s*(who|what|when|where|why|how|which|can|could|would|is|are|do|does|did)\b)",
    re.I | re.M,
)

_HUMAN_REQUEST_RE = re.compile(
    r"\b("
    r"speak\s+to\s+(a\s+)?(human|person|agent|manager|representative|someone)|"
    r"talk\s+to\s+(a\s+)?(human|person|agent|manager|representative|someone)|"
    r"real\s+(person|human|agent)|"
    r"customer\s+service|human\s+support|live\s+agent|"
    r"escalate|transfer\s+me"
    r")\b",
    re.I,
)

_INJECTION_RE = re.compile(
    r"("
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions|"
    r"disregard\s+(all\s+)?(previous|prior)\s+|"
    r"you\s+are\s+now\s+|"
    r"system\s*:\s*|"
    r"<\s*/?\s*system\s*>|"
    r"jailbreak|"
    r"dan\s+mode|"
    r"override\s+(your\s+)?(safety|guidelines|rules)"
    r")",
    re.I,
)

_REFUSAL_RE = re.compile(
    r"\b("
    r"i\s+(can'?t|cannot|won'?t|will\s+not)\s+|"
    r"i'?m\s+not\s+able\s+to|"
    r"against\s+my\s+(guidelines|policies)|"
    r"i\s+must\s+decline|"
    r"i\s+won'?t\s+ignore"
    r")\b",
    re.I,
)

_TOXICITY_RE = re.compile(
    r"\b("
    r"fuck\s+you|go\s+to\s+hell|you\s+suck|"
    r"piece\s+of\s+shit|stupid\s+bot|idiot\s+bot"
    r")\b",
    re.I,
)

_TRIVIAL_REPLIES = frozenset(
    {
        "ok",
        "okay",
        "yes",
        "no",
        "sure",
        "thanks",
        "thank you",
        "hi",
        "hello",
        "hey",
        "...",
        "…",
    }
)

_EXCERPT_MAX = 120


def _excerpt(text: str, max_len: int = _EXCERPT_MAX) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _issue(
    code: str,
    dimension: str,
    severity: str,
    deduction: int,
    field: str,
    text: str,
    **extra: Any,
) -> Dict[str, Any]:
    return {
        "code": code,
        "dimension": dimension,
        "severity": severity,
        "deduction": deduction,
        "evidence": {"field": field, "excerpt": _excerpt(text)},
        **extra,
    }


def response_duration_seconds(interaction: Any) -> Optional[float]:
    """Duration from started_at/completed_at or usage.total_duration_seconds."""
    usage = getattr(interaction, "usage", None) or {}
    if isinstance(usage, dict):
        d = usage.get("total_duration_seconds")
        if isinstance(d, (int, float)) and d > 0:
            return float(d)

    started = getattr(interaction, "started_at", None)
    completed = getattr(interaction, "completed_at", None)
    if started is not None and completed is not None:
        try:
            return max(0.0, (completed - started).total_seconds())
        except Exception:
            return None
    return None


def detect_slow_response(
    duration: Optional[float],
    latency_bands: Optional[Sequence[tuple]] = None,
    deductions: Optional[Dict[str, int]] = None,
) -> Optional[Dict[str, Any]]:
    if duration is None:
        return None
    bands = list(latency_bands or DEFAULT_LATENCY_BANDS)
    ded = deductions or DEFAULT_SEVERITY_DEDUCTIONS
    severity: Optional[str] = None
    for threshold, sev in bands:
        if duration >= float(threshold):
            severity = sev
    if not severity:
        return None
    return _issue(
        "slow_response",
        "responsiveness",
        severity,
        int(ded.get(severity, 20)),
        "response",
        f"duration={duration:.2f}s",
        duration_seconds=duration,
    )


def detect_empty_or_trivial(response: str) -> Optional[Dict[str, Any]]:
    r = (response or "").strip()
    if not r:
        return _issue(
            "empty_or_trivial_response",
            "quality",
            "high",
            DEFAULT_SEVERITY_DEDUCTIONS["high"],
            "response",
            "(empty)",
        )
    if len(r) < 3 or r.lower() in _TRIVIAL_REPLIES:
        return _issue(
            "empty_or_trivial_response",
            "quality",
            "high",
            DEFAULT_SEVERITY_DEDUCTIONS["high"],
            "response",
            r,
        )
    return None


def detect_idk(utterance: str, response: str) -> Optional[Dict[str, Any]]:
    if not _IDK_RE.search(response or ""):
        return None
    # Only penalize when user asked something substantive
    u = (utterance or "").strip()
    if len(u) < 4:
        return None
    if not (_QUESTION_RE.search(u) or len(u) > 12):
        return None
    return _issue(
        "idk_response",
        "quality",
        "high",
        DEFAULT_SEVERITY_DEDUCTIONS["high"],
        "response",
        response or "",
    )


def detect_unanswered_question(
    utterance: str, response: str
) -> Optional[Dict[str, Any]]:
    u = (utterance or "").strip()
    r = (response or "").strip()
    if not u or not _QUESTION_RE.search(u):
        return None
    if not r:
        return _issue(
            "unanswered_question",
            "quality",
            "high",
            DEFAULT_SEVERITY_DEDUCTIONS["high"],
            "utterance",
            u,
        )
    # Weak answer: very short relative to question, or pure deflection without content
    if len(r) < 15 and not re.search(r"\b(yes|no|here|see|link|http)\b", r, re.I):
        return _issue(
            "unanswered_question",
            "quality",
            "high",
            DEFAULT_SEVERITY_DEDUCTIONS["high"],
            "response",
            r,
        )
    return None


def detect_human_request(utterance: str) -> Optional[Dict[str, Any]]:
    if not _HUMAN_REQUEST_RE.search(utterance or ""):
        return None
    return _issue(
        "human_request",
        "friction",
        "high",
        DEFAULT_SEVERITY_DEDUCTIONS["high"],
        "utterance",
        utterance or "",
    )


def detect_repetition_loop(
    response: str,
    prior_agent_responses: Sequence[str],
    threshold: float = 0.92,
) -> Optional[Dict[str, Any]]:
    r = (response or "").strip()
    if len(r) < 20:
        return None
    for prev in prior_agent_responses:
        p = (prev or "").strip()
        if len(p) < 20:
            continue
        ratio = SequenceMatcher(None, r.lower(), p.lower()).ratio()
        if ratio >= threshold:
            return _issue(
                "repetition_loop",
                "integrity",
                "high",
                50,
                "response",
                r,
                similarity=round(ratio, 3),
            )
    return None


def detect_prompt_injection(utterance: str, response: str) -> Optional[Dict[str, Any]]:
    if not _INJECTION_RE.search(utterance or ""):
        return None
    if _REFUSAL_RE.search(response or ""):
        return None  # handled correctly
    return _issue(
        "prompt_injection_attempt",
        "integrity",
        "high",
        40,
        "utterance",
        utterance or "",
    )


def detect_toxicity(utterance: str) -> Optional[Dict[str, Any]]:
    if not _TOXICITY_RE.search(utterance or ""):
        return None
    return _issue(
        "toxicity",
        "friction",
        "high",
        DEFAULT_SEVERITY_DEDUCTIONS["high"],
        "utterance",
        utterance or "",
    )


def detect_execution_failure(interaction: Any) -> Optional[Dict[str, Any]]:
    """Heuristic: events/parameters marked failed without a useful response."""
    events = getattr(interaction, "events", None) or []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        content = str(ev.get("content") or "").lower()
        if "error" in content or "failed" in content or "exception" in content:
            resp = (getattr(interaction, "response", None) or "").strip()
            if len(resp) < 20:
                return _issue(
                    "execution_failure",
                    "quality",
                    "high",
                    DEFAULT_SEVERITY_DEDUCTIONS["high"],
                    "response",
                    resp or content[:80],
                )
    return None


def run_heuristics(
    *,
    utterance: str,
    response: str,
    duration: Optional[float] = None,
    prior_agent_responses: Optional[Sequence[str]] = None,
    interaction: Any = None,
    latency_bands: Optional[Sequence[tuple]] = None,
) -> List[Dict[str, Any]]:
    """Run all v1 heuristic detectors; return list of Issue dicts."""
    issues: List[Dict[str, Any]] = []
    for detector_result in (
        detect_slow_response(duration, latency_bands=latency_bands),
        detect_empty_or_trivial(response),
        detect_idk(utterance, response),
        detect_unanswered_question(utterance, response),
        detect_human_request(utterance),
        detect_repetition_loop(response, prior_agent_responses or ()),
        detect_prompt_injection(utterance, response),
        detect_toxicity(utterance),
        detect_execution_failure(interaction) if interaction is not None else None,
    ):
        if detector_result:
            issues.append(detector_result)
    return issues
