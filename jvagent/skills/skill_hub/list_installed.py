"""Tool: list_installed — list skill bundles currently installed for this agent."""

from __future__ import annotations

from typing import Any, Dict, List

from jvagent.core.app_context import get_app_root
from jvagent.scaffold.skill_resolve import resolve_merged_skill_bundles


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "list_installed",
        "description": (
            "List skill bundles currently installed for this agent. "
            "Shows both built-in and app-local skills with their names, "
            "descriptions, and whether they contain tool modules."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any = None) -> Any:
    app_root = get_app_root()
    namespace, agent_name = _resolve_agent_identity(visitor)
    if not namespace or not agent_name:
        return {"error": "Cannot determine agent identity."}

    try:
        bundles = resolve_merged_skill_bundles(
            app_root, namespace, agent_name, include_builtin=True
        )
    except Exception as exc:
        return {"error": f"Failed to resolve installed skills: {exc}"}

    if not bundles:
        return "No skills are currently installed for this agent."

    results = []
    for name, data in sorted(bundles.items()):
        tool_files = data.get("tool_files", [])
        results.append(
            {
                "name": name,
                "description": data.get("description", ""),
                "source": data.get("dir", ""),
                "has_tools": len(tool_files) > 0,
                "tool_count": len(tool_files),
            }
        )

    # Format as readable output
    lines = [f"Installed skills ({len(results)}):"]
    for skill in results:
        name = skill["name"]
        desc = skill["description"]
        tools = "with tools" if skill["has_tools"] else "SOP only"
        lines.append(f"  {name} — {desc[:80]} ({tools})")

    return "\n".join(lines)


def _resolve_agent_identity(visitor: Any) -> tuple:
    """Extract namespace and agent_name from the visitor's agent."""
    agent = getattr(visitor, "_agent", None)
    if agent is None:
        return "", ""

    namespace = getattr(agent, "namespace", "") or ""
    agent_name = getattr(agent, "name", "") or getattr(agent, "agent_name", "") or ""

    return namespace, agent_name
