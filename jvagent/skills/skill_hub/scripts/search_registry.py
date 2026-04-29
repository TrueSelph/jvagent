"""Tool: search_registry — search the skills.sh ecosystem for skill bundles."""

from __future__ import annotations

from typing import Any, Dict, List

from jvagent.skills.skill_hub._skills_cli import run_skills_find


def get_tool_definition() -> Dict[str, Any]:
    return {
        "name": "search_registry",
        "description": (
            "Search the skills.sh ecosystem for available skill bundles. "
            "Returns matching skills with name, source, and install count. "
            "Use this when the user asks for new capabilities or says "
            '"find a skill for X".'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query — keywords, capability name, or domain "
                        "(e.g. 'react testing', 'deployment', 'calendar')"
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 5, max: 20)",
                },
            },
            "required": ["query"],
        },
    }


async def execute(arguments: Dict[str, Any], *, visitor: Any = None) -> Any:
    query = str(arguments.get("query", "")).strip()
    if not query:
        return {"error": "query is required"}

    top_k = arguments.get("top_k", 5)
    try:
        top_k = max(1, min(20, int(top_k)))
    except (TypeError, ValueError):
        top_k = 5

    result = await run_skills_find(query, top_k=top_k)

    if "error" in result:
        return result

    skills = result.get("skills", [])
    if not skills:
        return f"No skills found matching '{query}'."

    # Format as a readable summary for the LLM
    lines = []
    for skill in skills:
        name = skill.get("name", "unknown")
        source = skill.get("source", "unknown")
        installs = skill.get("install_count", "?")
        url = skill.get("url", "")
        line = f"{source}@{name} ({installs} installs)"
        if url:
            line += f"\n  {url}"
        lines.append(line)

    header = f"Found {len(skills)} skill(s) matching '{query}':\n\n"
    return header + "\n\n".join(lines)
