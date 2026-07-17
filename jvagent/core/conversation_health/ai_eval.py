"""Gated AI Evaluation for Conversation Health."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .constants import DEFAULT_SEVERITY_DEDUCTIONS, DIMENSIONS
from .scoring import (
    build_contribution,
    heuristic_health_score,
    is_flagged,
    score_dimensions,
)

if TYPE_CHECKING:
    from jvagent.action.model.language.base import LanguageModelAction

logger = logging.getLogger(__name__)

AI_EVAL_PROMPT = """You are evaluating a single agent conversation turn for quality.

Return ONLY a JSON object with this shape:
{{
  "issues": [
    {{
      "code": "answer_inadequate|contradiction|hallucination|negative_sentiment|idk_response|unanswered_question|other",
      "dimension": "quality|responsiveness|friction|integrity",
      "severity": "low|medium|high",
      "evidence_excerpt": "short quote",
      "rationale": "one sentence"
    }}
  ],
  "notes": "optional brief summary"
}}

Rules:
- Only report clear problems; empty issues array if the turn looks fine.
- Prefer codes from the list; use "other" only if needed.
- dimension must match the issue type.
- Be conservative: false positives are costly.

User utterance:
{utterance}

Agent response:
{response}

Recent conversation (oldest first):
{history}
"""


def _parse_json_response(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        # try to find a JSON object
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def merge_ai_issues(
    heuristic_issues: List[Dict[str, Any]],
    ai_payload: Dict[str, Any],
    *,
    excerpt_max: int = 120,
) -> List[Dict[str, Any]]:
    """Merge AI issues into heuristic list (by code, AI can add new)."""
    cap = max(1, int(excerpt_max) if excerpt_max else 120)
    by_code = {str(i.get("code")): dict(i) for i in heuristic_issues if i.get("code")}
    for raw in ai_payload.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "other")
        dim = str(raw.get("dimension") or "quality")
        if dim not in DIMENSIONS:
            dim = "quality"
        sev = str(raw.get("severity") or "medium").lower()
        if sev not in DEFAULT_SEVERITY_DEDUCTIONS:
            sev = "medium"
        ded = int(DEFAULT_SEVERITY_DEDUCTIONS[sev])
        raw_excerpt = str(raw.get("evidence_excerpt") or raw.get("rationale") or "")
        excerpt = raw_excerpt[:cap]
        by_code[code] = {
            "code": code,
            "dimension": dim,
            "severity": sev,
            "deduction": ded,
            "evidence": {"field": "response", "excerpt": excerpt},
            "source": "ai",
            "rationale": str(raw.get("rationale") or "")[:300],
        }
    return list(by_code.values())


async def run_ai_evaluation(
    *,
    model_action: "LanguageModelAction",
    utterance: str,
    response: str,
    history: Optional[List[Dict[str, str]]] = None,
    model: str = "gpt-4o-mini",
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> Dict[str, Any]:
    """Call LM and return parsed AI payload (issues list)."""
    hist_lines = []
    for msg in history or []:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        hist_lines.append(f"{role}: {content}")
    history_text = "\n".join(hist_lines) if hist_lines else "(none)"

    prompt = AI_EVAL_PROMPT.format(
        utterance=utterance or "",
        response=response or "",
        history=history_text,
    )
    raw = await model_action.generate(
        prompt=prompt,
        model=model,
        history=[],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return _parse_json_response(raw)


def apply_ai_to_health(
    health: Dict[str, Any],
    ai_payload: Dict[str, Any],
    *,
    day: str,
    flag_threshold: float = 70.0,
    excerpt_max: int = 120,
) -> Dict[str, Any]:
    """Return updated health dict after merging AI issues and recomputing scores.

    ``flag_threshold`` should match the action config so intermediate flagged
    state is consistent (caller may still re-flag).
    """
    issues = merge_ai_issues(
        list(health.get("issues") or []),
        ai_payload,
        excerpt_max=excerpt_max,
    )
    dimensions = score_dimensions(issues)
    flagged = is_flagged(dimensions, issues, flag_threshold=flag_threshold)
    hs = heuristic_health_score(dimensions)
    contribution = build_contribution(
        day=day,
        dimensions=dimensions,
        flagged=flagged,
        issues=issues,
    )
    updated = dict(health)
    updated.update(
        {
            "issues": issues,
            "dimensions": dimensions,
            "flagged": flagged,
            "heuristic_health_score": hs,
            "contribution": contribution,
            "evaluation_tier": "heuristic+ai",
            "ai_status": "completed",
            "ai_notes": str(ai_payload.get("notes") or "")[:500],
        }
    )
    return updated
