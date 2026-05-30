"""Progressive-disclosure catalogs for the SkillExecutive (ADR-0012 §2.2).

The full tool surface can be large, so the prompt only lists a *visible* subset.
``find_tool`` searches the whole surface and ``load_tool`` promotes a tool into
the visible set (so it appears in subsequent steps). By default dispatch resolves
against the full surface, so a tool the model names is callable even before it
is loaded — the catalog is a discovery aid, not a gate. (The exception is
``block_raw_tool_invocation``: when that executive flag is on, dispatch is
restricted to the *visible* set, so hidden tools must first be loaded via
``find_tool`` or surfaced by a skill.)

``find_skill`` / ``use_skill`` mirror this for native SOP skills: only names +
descriptions are surfaced up front; ``use_skill`` returns the full procedure
body as an observation so it persists for the rest of the loop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from jvagent.action.skill_executive.skills import SkillDoc
from jvagent.action.skill_executive.tools import SkillTool


def build_catalog_tools(
    all_tools: Dict[str, SkillTool], visible: Set[str]
) -> Dict[str, SkillTool]:
    """``find_tool`` / ``load_tool`` over the full ``all_tools`` surface."""

    async def _find(args: Dict[str, Any]) -> str:
        q = ((args or {}).get("query") or "").strip().lower()
        hits = [
            t
            for name, t in all_tools.items()
            if not q or q in (name + " " + (t.description or "")).lower()
        ]
        if not hits:
            return "(no tools matched)"
        lines = [f"- {t.name}: {t.description}" for t in hits[:15]]
        return "Matching tools (call load_tool to surface one):\n" + "\n".join(lines)

    async def _load(args: Dict[str, Any]) -> str:
        name = ((args or {}).get("name") or "").strip()
        tool = all_tools.get(name)
        if tool is None:
            return f"(no such tool: {name})"
        visible.add(name)
        return f"Loaded tool '{name}': {tool.description}"

    return {
        "find_tool": SkillTool(
            name="find_tool",
            description="Search the full tool surface by query when the tool you need isn't listed.",
            run=_find,
        ),
        "load_tool": SkillTool(
            name="load_tool",
            description="Surface a tool by exact name so you can call it.",
            run=_load,
        ),
    }


def build_skill_meta_tools(
    docs: List[SkillDoc],
    available_tool_names: Set[str],
    activated: List[str],
    visible: Optional[Set[str]] = None,
) -> Dict[str, SkillTool]:
    """``find_skill`` / ``use_skill`` over native SOP skills (progressive).

    When ``visible`` is provided, activating a skill via ``use_skill`` surfaces
    the skill's declared ``allowed-tools`` (those present on the surface) into
    that set, so the model can call them immediately — a jvSkill *executes* by
    coordinating the tools it names.
    """
    if not docs:
        return {}
    index = {d.name: d for d in docs}

    async def _find(args: Dict[str, Any]) -> str:
        q = ((args or {}).get("query") or "").strip().lower()
        hits = [
            d for d in docs if not q or q in (d.name + " " + d.description).lower()
        ] or docs
        lines = [f"- {d.name}: {d.description}" for d in hits[:10]]
        return "Available skills (call use_skill to load one):\n" + "\n".join(lines)

    async def _use(args: Dict[str, Any]) -> str:
        name = ((args or {}).get("name") or "").strip()
        doc = index.get(name)
        if doc is None:
            return f"(no such skill: {name})"
        present = [t for t in doc.requires_tools if t in available_tool_names]
        missing = [t for t in doc.requires_tools if t not in available_tool_names]
        # Surface the skill's declared tools so the model can call them now.
        if visible is not None and present:
            visible.update(present)
        # Idempotent: re-activating a skill already loaded this turn returns a
        # short directive instead of re-dumping the SOP, so the model proceeds
        # with the procedure instead of looping on use_skill.
        if name in activated:
            hint = f" Its tools are available: {', '.join(present)}." if present else ""
            return (
                f"Skill '{doc.name}' is already active.{hint} Proceed with its "
                "steps now using the available tools; do not call use_skill for "
                "it again."
            )
        activated.append(name)
        surfaced = f" Tools now callable: {', '.join(present)}." if present else ""
        warn = ""
        if missing:
            warn = (
                "\n\n(Note: these referenced tools are not currently available: "
                + ", ".join(missing)
                + ". Adapt accordingly or report the gap.)"
            )
        return (
            f"Activated skill '{doc.name}'.{surfaced}\n\nPROCEDURE:\n{doc.body}{warn}"
        )

    return {
        "find_skill": SkillTool(
            name="find_skill",
            description="Search available skills (standard operating procedures) by query.",
            run=_find,
        ),
        "use_skill": SkillTool(
            name="use_skill",
            description=(
                "Activate a skill to load its procedure (SOP) and make its tools "
                'callable. Call as {"action":"tool","tool":"use_skill",'
                '"args":{"name":"<skill name>"}}.'
            ),
            run=_use,
        ),
    }


__all__ = ["build_catalog_tools", "build_skill_meta_tools"]
