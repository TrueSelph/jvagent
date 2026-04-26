"""Apply a pricing rubric to scope parameters via PricingAction."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "pricing__apply_pricing",
        "description": (
            "Apply a pricing rubric to extracted scope parameters. "
            "Resolves the PricingAction, looks up the named rubric, "
            "and computes a full PricingAssessment with line items, "
            "markups, adjustments, and assumptions. Returns the "
            "assessment dict for inclusion in the draft proposal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "rubric_name": {
                    "type": "string",
                    "description": "Name of the pricing rubric to use. Defaults to the PricingAction's active rubric.",
                },
                "scope_parameters": {
                    "type": "object",
                    "description": "Scope parameters extracted by the extract_parameters tool",
                    "properties": {
                        "estimated_engineering_hours": {"type": "number"},
                        "team_composition": {"type": "object"},
                        "timeline_months": {"type": "number"},
                        "strategic_value": {"type": "string"},
                        "competitive_pressure": {"type": "number"},
                        "relationship_stage": {"type": "string"},
                        "relationship_discount": {"type": "number"},
                        "scope_notes": {"type": "string"},
                    },
                },
                "client_name": {
                    "type": "string",
                    "description": "Client name for narrative generation",
                },
            },
            "required": ["scope_parameters"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Apply the rubric via PricingAction.assess(), then generate a narrative."""
    resolver = getattr(visitor, "action_resolver", None)
    if resolver is None:
        return {"error": "ActionResolver not available"}

    action = await resolver.resolve("PricingAction")
    if action is None:
        return {"error": "PricingAction not found on this agent"}

    rubric_name = arguments.get("rubric_name") or getattr(action, "active_rubric", "standard")
    scope_params = arguments.get("scope_parameters", {})

    try:
        assessment = await action.assess(rubric_name, scope_params)
    except ValueError as e:
        return {"error": str(e)}

    # The LLM should generate the narrative in a subsequent step.
    # Return the computed assessment for the LLM to review and enhance.
    return {
        "rubric_applied": rubric_name,
        "assessment": assessment,
        "next_step": (
            "Review the assessment above, then write the pricing narrative "
            "and attach the assessment to the draft proposal's Investment section."
        ),
    }
