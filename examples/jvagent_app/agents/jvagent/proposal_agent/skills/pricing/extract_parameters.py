"""Extract scope parameters from a draft proposal for pricing assessment."""

from __future__ import annotations

from typing import Any, Dict


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "pricing__extract_parameters",
        "description": (
            "Extract structured scope parameters from a draft proposal "
            "and source transcript/context. The LLM should call this with "
            "the draft analysis, then populate the fields based on its reading."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "estimated_engineering_hours": {
                    "type": "number",
                    "description": "Total estimated engineering hours for the project",
                },
                "team_composition": {
                    "type": "object",
                    "description": "Team composition counts by role",
                    "properties": {
                        "senior": {"type": "integer", "description": "Number of senior engineers"},
                        "engineer": {"type": "integer", "description": "Number of engineers"},
                        "junior": {"type": "integer", "description": "Number of junior engineers"},
                        "pm": {"type": "integer", "description": "Number of project managers"},
                        "designer": {"type": "number", "description": "Number of designers (can be fractional for part-time)"},
                    },
                },
                "timeline_months": {
                    "type": "number",
                    "description": "Estimated timeline in months",
                },
                "strategic_value": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Strategic value level: low=commodity, medium=differentiating, high=transformational",
                },
                "competitive_pressure": {
                    "type": "number",
                    "description": "Competitive pressure discount factor (0.0-0.5). 0=none, 0.5=high",
                },
                "relationship_stage": {
                    "type": "string",
                    "enum": ["new", "existing", "strategic"],
                    "description": "Relationship stage with the client",
                },
                "scope_notes": {
                    "type": "string",
                    "description": "Free-text notes about scope assumptions or special considerations",
                },
            },
            "required": [
                "estimated_engineering_hours",
                "timeline_months",
                "strategic_value",
            ],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any) -> Dict[str, Any]:
    """Return the extracted parameters as-is (LLM-calculated)."""
    return {
        "scope_parameters": {
            "estimated_engineering_hours": arguments.get("estimated_engineering_hours", 0),
            "team_composition": arguments.get("team_composition", {}),
            "timeline_months": arguments.get("timeline_months", 0),
            "strategic_value": arguments.get("strategic_value", "medium"),
            "competitive_pressure": arguments.get("competitive_pressure", 0.0),
            "relationship_stage": arguments.get("relationship_stage", "new"),
            "relationship_discount": {
                "new": 0.0,
                "existing": 0.05,
                "strategic": 0.10,
            }.get(arguments.get("relationship_stage", "new"), 0.0),
            "scope_notes": arguments.get("scope_notes", ""),
        }
    }
