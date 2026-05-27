"""Skill harness tools for the engine (deduplicated SkillCatalog usage)."""

from typing import Any, Dict, List, Optional

from jvagent.action.helm.reasoning.context import EngineContext
from jvagent.tooling.tool import Tool


def _build_skill_tools(ctx: EngineContext) -> List[Tool]:
    """Return harness tools that expose skill discovery and reading to the engine model."""

    def _get_catalog():
        """Resolve the shared SkillCatalog from visitor state (single source of truth)."""
        skill_state = getattr(ctx.visitor, "_skill_state", None) or {}
        catalog = skill_state.get("skill_catalog")
        if catalog is not None:
            return catalog
        discovered = skill_state.get("discovered_skills", {})
        if not discovered:
            return None
        from jvagent.action.helm.reasoning.catalog.skill_catalog import SkillCatalog

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

    async def _activate_skill(skill_name: str) -> str:
        """Hot-register a skill's tools into the live registry.

        Use when ``skill_read`` shows a skill has tools you need but they
        are not yet callable (i.e. the router did not pre-select that skill
        and no ``coactivate-with`` declaration loaded it). Activation runs
        the same loader the engine uses at start-up; on the next model
        call the new tools appear in the engine's tool list.

        Idempotent: re-activating an already-active skill is a no-op.
        Bounded: per engine run, no more than
        ``EngineConfig.max_dynamic_activations`` skills may be activated
        this way (default 10).
        """
        skill_name = (skill_name or "").strip()
        if not skill_name:
            return "skill_activate: skill_name is required."

        catalog = _get_catalog()
        if catalog is None:
            return "skill_activate: no skill catalog available."

        data = catalog.skills.get(skill_name)
        if not data:
            available = list(catalog.skills.keys())
            return (
                f"skill_activate: skill '{skill_name}' not in catalog. "
                f"Available: {available}. Use skill_search to discover."
            )

        # Already loaded? Treat as success — engine already has the tools.
        if skill_name in (ctx.preloaded_skills or []):
            return f"skill_activate: '{skill_name}' is already active."

        # Refuse if a required action is not enabled on this agent.
        required_actions = data.get("requires_actions") or []
        if required_actions and ctx.action_resolver is not None:
            missing: List[str] = []
            for action_label in required_actions:
                try:
                    resolved, _err = await ctx.action_resolver.resolve(
                        ctx.visitor, action_label
                    )
                except Exception:
                    resolved = None
                if resolved is None:
                    missing.append(action_label)
            if missing:
                return (
                    f"skill_activate: cannot activate '{skill_name}' — "
                    f"required action(s) not available: {missing}."
                )

        # Enforce per-run activation cap.
        cap = getattr(ctx.config, "max_dynamic_activations", 10)
        if cap <= 0:
            return (
                "skill_activate: dynamic activation disabled in this agent's "
                "configuration (max_dynamic_activations=0)."
            )
        if ctx.dynamic_activations >= cap:
            return (
                f"skill_activate: per-run activation cap reached ({cap}). "
                "Continue with already-loaded skills or finish the turn."
            )

        registry = ctx.registry
        if registry is None:
            return (
                "skill_activate: tool registry is not exposed on ctx; this "
                "engine run cannot accept dynamic activations."
            )

        # Load the bundle. Import locally to avoid a circular import at
        # module load time (assembler imports tools/skill.py via
        # ``_build_skill_tools``).
        from jvagent.action.helm.reasoning.registry.assembler import (
            SKILL_LOAD_REPORT_KEY,
            SkillLoadReport,
            load_one_skill,
        )

        report = SkillLoadReport()
        try:
            await load_one_skill(
                registry,
                skill_name,
                data,
                catalog,
                ctx.action_resolver,
                ctx,
                report,
            )
        except Exception as exc:
            return f"skill_activate: load failed: {type(exc).__name__}: {exc}"

        loaded_names = [e.tool_name for e in report.loaded() if e.tool_name]
        failed_entries = report.failed()

        # Append to the cumulative skill-load report so observability
        # surfaces dynamic activations alongside startup loads.
        skill_state = getattr(ctx.visitor, "_skill_state", None)
        if isinstance(skill_state, dict):
            cumulative = skill_state.get(SKILL_LOAD_REPORT_KEY)
            if cumulative is not None and hasattr(cumulative, "entries"):
                cumulative.entries.extend(report.entries)

        if not loaded_names and failed_entries:
            reasons = "; ".join(
                f"{e.file or '<file>'}: {e.reason}" for e in failed_entries
            )
            return (
                f"skill_activate: no tools loaded for '{skill_name}'. "
                f"Errors: {reasons}"
            )

        if skill_name not in (ctx.preloaded_skills or []):
            ctx.preloaded_skills.append(skill_name)
        ctx.dynamic_activations += 1
        ctx.registry_dirty = True

        return (
            f"skill_activate: activated '{skill_name}'. "
            f"Newly callable tools: {loaded_names}. "
            "These appear in your tool list on the next step."
        )

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
        Tool(
            name="skill_activate",
            description=(
                "Hot-register a skill's tools into the engine so you can "
                "call them on the next step. Use after skill_read when the "
                "skill's tools are listed as available but not yet callable "
                "(i.e. the skill was not pre-selected by the router and is "
                "not always-active). Idempotent. Bounded by "
                "max_dynamic_activations per engine run."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": (
                            "Exact catalog name of the skill to activate. "
                            "Use skill_search or skill_list to discover "
                            "names."
                        ),
                    },
                },
                "required": ["skill_name"],
            },
            execute=_activate_skill,
        ),
    ]
