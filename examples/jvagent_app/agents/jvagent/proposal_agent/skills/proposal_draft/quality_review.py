"""Quality gate for proposal drafts before pricing/authoring."""

from __future__ import annotations

import re
from typing import Any, Dict, List


REQUIRED_HEADINGS = [
    "Executive Summary",
    "Understanding of Your Needs",
    "Core Deliverables",
    "Technical Approach",
    "Client Responsibilities",
    "Value Summary",
    "Next Steps",
]


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "proposal_draft__quality_review",
        "description": (
            "Run a deterministic quality checklist against a proposal draft. "
            "Checks section presence, pricing placeholder, unresolved review markers, "
            "and baseline business sections required for high-quality client proposals."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Rendered proposal markdown text.",
                },
                "proposal_state": {
                    "type": "object",
                    "description": "Optional structured proposal state from draft tool.",
                },
            },
            "required": ["content"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    content = arguments.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return {
            "status": "failed",
            "error": "content is required for quality review",
            "quality_score": 0,
        }

    missing_headings: List[str] = []
    lowered = content.lower()
    for heading in REQUIRED_HEADINGS:
        if heading.lower() not in lowered:
            missing_headings.append(heading)

    has_pricing_placeholder = "[PRICING PLACEHOLDER]" in content
    has_deliverable_table = bool(re.search(r"\|\s*Deliverable\s*\|", content, flags=re.IGNORECASE))
    has_next_steps_ordered = bool(re.search(r"##\s+.*Next Steps[\s\S]*\n1\.", content, flags=re.IGNORECASE))
    unresolved_reviews = re.findall(r"\[REVIEW:[^\]]+\]", content)

    penalties = 0
    penalties += len(missing_headings) * 10
    penalties += 20 if not has_pricing_placeholder else 0
    penalties += 10 if not has_deliverable_table else 0
    penalties += 8 if not has_next_steps_ordered else 0
    penalties += min(len(unresolved_reviews), 5) * 2

    quality_score = max(0, 100 - penalties)
    status = "pass" if quality_score >= 70 and not missing_headings else "needs_revision"

    return {
        "status": status,
        "quality_score": quality_score,
        "missing_headings": missing_headings,
        "has_pricing_placeholder": has_pricing_placeholder,
        "has_deliverable_table": has_deliverable_table,
        "has_next_steps_ordered": has_next_steps_ordered,
        "unresolved_review_markers": unresolved_reviews,
        "recommendations": [
            "Add missing required sections before authoring.",
            "Keep [PRICING PLACEHOLDER] until pricing__build_investment_section runs.",
            "Reduce unresolved [REVIEW: ...] items where transcript evidence exists.",
        ],
    }
