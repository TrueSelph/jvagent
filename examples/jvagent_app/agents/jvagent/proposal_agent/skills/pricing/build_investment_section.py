"""Build and inject the Investment section from PricingAssessment."""

from __future__ import annotations

from typing import Any, Dict, List


def _money(value: Any, currency: str) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return f"{currency} 0.00"
    return f"{currency} {num:,.2f}"


def _render_investment(assessment: Dict[str, Any], currency: str) -> str:
    line_items: List[Dict[str, Any]] = assessment.get("line_items", []) or []
    assumptions: List[str] = assessment.get("assumptions", []) or []
    table = [
        "## X. Investment",
        "",
        "| Line Item | Hours | Rate | Total | Notes |",
        "| :---- | :---- | :---- | :---- | :---- |",
    ]
    for item in line_items:
        table.append(
            "| {activity} | {hours} | {rate} | {total} | {notes} |".format(
                activity=item.get("activity", "Item"),
                hours=item.get("hours", 0),
                rate=_money(item.get("rate", 0), currency),
                total=_money(item.get("total", 0), currency),
                notes=item.get("notes", ""),
            )
        )
    table.extend(
        [
            "",
            f"**Total estimated engineering hours:** {assessment.get('total_engineering_hours', 0)}",
            f"**Blended rate:** {_money(assessment.get('blended_rate', 0), currency)} / hr",
            f"**Total investment:** {_money(assessment.get('total', 0), currency)}",
            f"**Validity:** {assessment.get('valid_until', 'N/A')}",
            "",
            "### Assumptions",
        ]
    )
    for assumption in assumptions:
        table.append(f"- {assumption}")
    return "\n".join(table)


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "pricing__build_investment_section",
        "description": (
            "Build a deterministic Investment section from PricingAssessment and "
            "replace [PRICING PLACEHOLDER] in proposal markdown and state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "assessment": {
                    "type": "object",
                    "description": "PricingAssessment from pricing__apply_pricing.",
                },
                "proposal_markdown": {
                    "type": "string",
                    "description": "Current proposal markdown containing [PRICING PLACEHOLDER].",
                },
                "proposal_state": {
                    "type": "object",
                    "description": "Structured proposal state to update.",
                },
                "currency": {
                    "type": "string",
                    "description": "Display currency label (default USD).",
                },
            },
            "required": ["assessment", "proposal_markdown"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    assessment = arguments.get("assessment", {})
    proposal_markdown = arguments.get("proposal_markdown", "")
    proposal_state = arguments.get("proposal_state", {}) or {}
    currency = arguments.get("currency", "USD")

    investment_section = _render_investment(assessment, currency)
    if "[PRICING PLACEHOLDER]" in proposal_markdown:
        updated_markdown = proposal_markdown.replace("[PRICING PLACEHOLDER]", investment_section)
    else:
        updated_markdown = f"{proposal_markdown.rstrip()}\n\n{investment_section}\n"

    proposal_state["pricing_tables"] = assessment.get("line_items", [])
    proposal_state["investment"] = {
        "currency": currency,
        "total": assessment.get("total"),
        "valid_until": assessment.get("valid_until"),
        "assumptions": assessment.get("assumptions", []),
    }

    return {
        "updated_markdown": updated_markdown,
        "proposal_state": proposal_state,
        "investment_section": investment_section,
        "placeholder_replaced": "[PRICING PLACEHOLDER]" in proposal_markdown,
        "next_step": "Pass updated_markdown to authoring stage.",
    }
