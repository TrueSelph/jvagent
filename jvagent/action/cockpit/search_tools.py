"""Unified discovery tool for cockpit (skills + interact_actions + tools).

Single entry point for finding the right capability for a job. The same
implementation is used in two surfaces:

- **Router** (Phase 1): permitted_kinds = {skills, interact_actions, tools}.
  Optional, gated by ``router_use_cockpit_search`` to protect routing latency.
- **Engine** (Phase 2 think-act-observe): permitted_kinds = {skills, tools}.
  ``interact_actions`` is intentionally hidden — interact-action discovery is
  a router concern; the engine has no way to invoke another InteractAction
  from inside its loop.

The tool's JSON Schema enum for the ``kind`` parameter is dynamically built
from ``permitted_kinds`` so the model literally cannot ask for a kind that
isn't allowed in its context.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from jvagent.action.cockpit.context import CockpitContext
from jvagent.action.cockpit.skill_catalog import SkillCatalog
from jvagent.tooling.tool import Tool

logger = logging.getLogger(__name__)


# Allowed kinds. "all" is a meta-kind that fans out to every other allowed kind.
KIND_SKILLS = "skills"
KIND_ACTIONS = "interact_actions"
KIND_TOOLS = "tools"
KIND_ALL = "all"

_KIND_ORDER = (KIND_SKILLS, KIND_ACTIONS, KIND_TOOLS)


def _normalize_tokens(text: str) -> List[str]:
    """Same tokenizer as SkillCatalog for consistent ranking across kinds."""
    return SkillCatalog._normalize_tokens(text or "")


def _score_text(query_tokens: List[str], target: str, weight: float) -> float:
    if not query_tokens or not target:
        return 0.0
    target_tokens = set(_normalize_tokens(target))
    if not target_tokens:
        return 0.0
    overlap = sum(1 for t in query_tokens if t in target_tokens)
    return overlap * weight


async def _search_skills(
    catalog: Optional[SkillCatalog],
    query: str,
    query_tokens: List[str],
    top_k: int,
) -> List[Tuple[float, str, str]]:
    """Return [(score, label, summary)] for matching skills."""
    if catalog is None or catalog.is_empty:
        return []
    results: List[Tuple[float, str, str]] = []
    query_lower = (query or "").lower()
    for name, data in catalog.skills.items():
        score = SkillCatalog._compute_relevance(name, data, query_tokens, query_lower)
        if score <= 0:
            continue
        desc = str(data.get("description") or "").strip()
        results.append((score, f"skill:{name}", desc or "(no description)"))
    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]


async def _search_tools(
    ctx: CockpitContext,
    query: str,
    query_tokens: List[str],
    top_k: int,
) -> List[Tuple[float, str, str]]:
    """Score tools registered on the agent's actions plus harness tools."""
    results: List[Tuple[float, str, str]] = []
    seen_names: Set[str] = set()

    # Action tools (plus any harness tools the action layer exposes).
    if ctx.agent is not None:
        try:
            actions_mgr = await ctx.agent.get_actions_manager()
            if actions_mgr is not None:
                for tool in await actions_mgr.get_all_tools():
                    name = getattr(tool, "name", None)
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)
                    desc = getattr(tool, "description", "") or ""
                    score = _score_text(query_tokens, name, 4.0) + _score_text(
                        query_tokens, desc, 2.0
                    )
                    if score > 0:
                        results.append(
                            (score, f"tool:{name}", desc.strip() or "(no description)")
                        )
        except Exception as exc:
            logger.debug("cockpit_search: action tool collection failed: %s", exc)

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]


async def _search_interact_actions(
    ctx: CockpitContext,
    query: str,
    query_tokens: List[str],
    top_k: int,
) -> List[Tuple[float, str, str]]:
    """Score the agent's enabled InteractAction subclasses (router-only surface)."""
    if ctx.agent is None:
        return []
    try:
        from jvagent.action.interact.base import InteractAction

        actions_mgr = await ctx.agent.get_actions_manager()
        if actions_mgr is None:
            return []
        all_actions = await actions_mgr.get_all_actions(enabled_only=True)
    except Exception as exc:
        logger.debug("cockpit_search: interact action enumeration failed: %s", exc)
        return []

    results: List[Tuple[float, str, str]] = []
    for action in all_actions:
        try:
            if not isinstance(action, InteractAction):
                continue
            name = action.__class__.__name__
            desc = (
                getattr(action, "description", None) or action.__class__.__doc__ or ""
            ).strip()
            score = _score_text(query_tokens, name, 4.0) + _score_text(
                query_tokens, desc, 2.0
            )
            if score > 0:
                results.append(
                    (score, f"interact_action:{name}", desc or "(no description)")
                )
        except Exception:
            continue

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]


def _resolve_catalog(ctx: CockpitContext) -> Optional[SkillCatalog]:
    skill_state = getattr(ctx.visitor, "_skill_state", None) or {}
    catalog = skill_state.get("skill_catalog")
    if catalog is not None:
        return catalog
    discovered = skill_state.get("discovered_skills") or {}
    if discovered:
        catalog = SkillCatalog(discovered)
        skill_state["skill_catalog"] = catalog
        return catalog
    return None


def _format_results(
    sections: Dict[str, List[Tuple[float, str, str]]],
    query: str,
) -> str:
    has_any = any(len(v) > 0 for v in sections.values())
    if not has_any:
        return f"No matches for '{query}'." if query else "No capabilities to list."
    lines: List[str] = []
    for kind in _KIND_ORDER:
        items = sections.get(kind) or []
        if not items:
            continue
        header = {
            KIND_SKILLS: "Skills",
            KIND_ACTIONS: "Interact actions",
            KIND_TOOLS: "Tools",
        }[kind]
        lines.append(f"## {header}")
        for _, label, summary in items:
            lines.append(f"- {label}: {summary}")
    return "\n".join(lines)


def _build_search_tools(
    ctx: CockpitContext,
    *,
    permitted_kinds: Iterable[str],
    name: str = "cockpit_search",
) -> List[Tool]:
    """Return the unified ``cockpit_search`` tool, scoped to ``permitted_kinds``.

    Args:
        ctx: Cockpit context.
        permitted_kinds: Subset of {skills, interact_actions, tools} the caller
            allows. The model sees only these kinds (plus the meta-kind ``all``)
            in the schema enum.
        name: Tool name (defaults to ``cockpit_search``).
    """
    permitted: Set[str] = {k for k in permitted_kinds if k in _KIND_ORDER}
    if not permitted:
        return []

    enum_values = [KIND_ALL] + [k for k in _KIND_ORDER if k in permitted]
    permitted_summary = ", ".join(enum_values)

    async def _execute(query: str, kind: str = KIND_ALL, limit: int = 5) -> str:
        q = (query or "").strip()
        if not q:
            return "Error: 'query' is required."
        target_kinds: Set[str]
        kind_lc = (kind or KIND_ALL).strip().lower()
        if kind_lc == KIND_ALL or not kind_lc:
            target_kinds = set(permitted)
        elif kind_lc in permitted:
            target_kinds = {kind_lc}
        else:
            return (
                f"Error: kind '{kind}' is not permitted in this context. "
                f"Allowed: {permitted_summary}"
            )

        top_k = max(1, int(limit or 5))
        query_tokens = _normalize_tokens(q)
        catalog = _resolve_catalog(ctx)

        sections: Dict[str, List[Tuple[float, str, str]]] = {}
        if KIND_SKILLS in target_kinds:
            sections[KIND_SKILLS] = await _search_skills(
                catalog, q, query_tokens, top_k
            )
        if KIND_ACTIONS in target_kinds:
            sections[KIND_ACTIONS] = await _search_interact_actions(
                ctx, q, query_tokens, top_k
            )
        if KIND_TOOLS in target_kinds:
            sections[KIND_TOOLS] = await _search_tools(ctx, q, query_tokens, top_k)

        return _format_results(sections, q)

    return [
        Tool(
            name=name,
            description=(
                "Unified capability search. Find the most appropriate "
                f"{permitted_summary.replace(KIND_ALL + ', ', '')} for a given task. "
                "Use a short, intent-focused query (e.g. 'search the web', 'send email', "
                "'summarize a pdf'). Returns ranked, grouped results."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Short phrase describing the capability needed.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": enum_values,
                        "description": (
                            "Restrict to a kind. 'all' (default) fans out to every "
                            "permitted kind."
                        ),
                        "default": KIND_ALL,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results per kind (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            execute=_execute,
        ),
    ]


# Public helper used by router (no CockpitContext available pre-engine).
async def search_for_router(
    *,
    agent: Any,
    visitor_shim: Any,
    catalog: Optional[SkillCatalog],
    query: str,
    limit: int = 5,
) -> str:
    """Run a unified search from the router (skills + interact_actions + tools).

    Returns a human-readable string that the router can splice into its prompt
    or use to enrich route descriptors.
    """

    class _RouterCtx:
        def __init__(self) -> None:
            self.agent = agent
            self.visitor = visitor_shim

    ctx = _RouterCtx()  # type: ignore[assignment]
    if catalog is not None and getattr(visitor_shim, "_skill_state", None) is None:
        # Provide skill_catalog where the search tool will look for it.
        try:
            visitor_shim._skill_state = {"skill_catalog": catalog}
        except Exception:
            pass

    tools = _build_search_tools(
        ctx,  # type: ignore[arg-type]
        permitted_kinds={KIND_SKILLS, KIND_ACTIONS, KIND_TOOLS},
        name="cockpit_search",
    )
    if not tools:
        return ""
    result = await tools[0].call(query=query, kind=KIND_ALL, limit=limit)
    return result.content if hasattr(result, "content") else str(result)
