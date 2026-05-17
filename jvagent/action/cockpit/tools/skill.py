"""Skill harness tools for cockpit (deduplicated SkillCatalog usage)."""

from typing import Any, Dict, List, Optional

from jvagent.action.cockpit.context import CockpitContext
from jvagent.tooling.tool import Tool


def _build_skill_tools(ctx: CockpitContext) -> List[Tool]:
    """Return harness tools that expose skill discovery and reading to the cockpit model."""

    def _get_catalog():
        """Resolve the shared SkillCatalog from visitor state (single source of truth)."""
        skill_state = getattr(ctx.visitor, "_skill_state", None) or {}
        catalog = skill_state.get("skill_catalog")
        if catalog is not None:
            return catalog
        discovered = skill_state.get("discovered_skills", {})
        if not discovered:
            return None
        from jvagent.action.cockpit.catalog.skill_catalog import SkillCatalog

        catalog = SkillCatalog(discovered)
        skill_state["skill_catalog"] = catalog
        return catalog

    async def _list_skills() -> str:
        catalog = _get_catalog()
        if catalog is None:
            return "No skills are currently loaded."
        if catalog.is_empty:
            return "No skills are available."
        return catalog.render_catalog()

    async def _search_skills(query: str) -> str:
        catalog = _get_catalog()
        if catalog is None:
            return "No skills are loaded to search."
        try:
            # ``SkillCatalog.search`` returns a pre-rendered string, not a
            # mapping. The previous implementation called ``.items()`` on
            # the result and threw on every invocation. AUDIT-interact
            # HIGH-03.
            rendered = catalog.search(query)
            if not rendered or not rendered.strip():
                return f'No skills found matching "{query}".'
            return rendered
        except Exception as exc:
            return f"Error searching skills: {exc}"

    async def _read_skill(skill_name: str) -> str:
        catalog = _get_catalog()
        if catalog is None:
            return "No skills are loaded."
        skills = catalog.skills
        data = skills.get(skill_name)
        if not data:
            available = list(skills.keys())
            return (
                f"Skill '{skill_name}' not found. "
                f"Available skills: {available}. "
                "Use skill_search or skill_list to find the right name."
            )

        content = data.get("content", "")
        description = data.get("description", "")

        out = f"# Skill: {skill_name}\n\n"
        if description:
            out += f"**Description:** {description}\n\n"
        if content:
            out += content

        allowed = data.get("allowed_tools", []) or []
        if allowed:
            out += f"\n\n**Available tools:** {', '.join(allowed)}"

        return out

    return [
        Tool(
            name="skill_list",
            description="List all locally installed skills with their descriptions and tags.",
            parameters_schema={"type": "object", "properties": {}},
            execute=_list_skills,
        ),
        Tool(
            name="skill_search",
            description=(
                "Search locally installed skills by description keyword. "
                "Use this to find the right skill before activating it."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword or short phrase describing the capability needed.",
                    },
                },
                "required": ["query"],
            },
            execute=_search_skills,
        ),
        Tool(
            name="skill_read",
            description=(
                "Read the full instructions and SOP for a specific skill. "
                "Always call this before activating or using a skill's tools."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "The exact name of the skill to read.",
                    },
                },
                "required": ["skill_name"],
            },
            execute=_read_skill,
        ),
    ]
