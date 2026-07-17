"""Progressive-disclosure catalogs for the Orchestrator (ADR-0012 §2.2).

The full tool surface can be large, so the prompt only lists a *visible* subset.
``find_tool`` searches the whole surface and ``load_tool`` promotes a tool into
the visible set (so it appears in subsequent steps). By default dispatch resolves
against the full surface, so a tool the model names is callable even before it
is loaded — the catalog is a discovery aid, not a gate. (The exception is
``block_raw_tool_invocation``: when that executive flag is on, dispatch is
restricted to the *visible* set, so hidden tools must first be loaded via
``find_tool`` or surfaced by a skill.)

``find_skill`` / ``use_skill`` mirror this for native SOP skills: only names +
descriptions are surfaced up front. ``use_skill`` returns a short activation
note (plus any activate-hook payload) as an observation — tool steps stay
contiguous under "Steps taken this turn". The skill PROCEDURE body is surfaced
via the system ``skills_section`` (task-lock or post-activation), not inside
the observation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.action.orchestrator.tools import SkillTool


# Per-agent assembled tool surface cache. Keyed by agent_id; invalidated on
# action reload when the orchestrator's config hash changes.
@dataclass
class _ToolSurfaceCacheEntry:
    config_hash: str
    action_tools: Dict[str, Tuple[Any, bool, Tuple[str, ...], bool]] = field(
        default_factory=dict
    )
    # action_tool_name -> (raw Tool, is_flow, triggers, binds_to_visitor)
    mcp_tools: Dict[str, Any] = field(default_factory=dict)
    skill_docs: Tuple[SkillDoc, ...] = ()
    longtail: frozenset[str] = frozenset()


_TOOL_SURFACE_CACHE: Dict[str, _ToolSurfaceCacheEntry] = {}


def compute_tool_surface_config_hash(orch: Any, enabled_action_ids: List[str]) -> str:
    """Stable hash of orchestrator surfacing config + enabled action set."""
    parts = [
        str(getattr(orch, "tool_tier", "")),
        str(getattr(orch, "lean_tool_threshold", "")),
        str(getattr(orch, "lean_presurface_k", "")),
        str(getattr(orch, "planning", "")),
        str(getattr(orch, "vision", "")),
        str(getattr(orch, "pinned_tools", "") or ""),
        ",".join(sorted(enabled_action_ids)),
    ]
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return digest[:16]


def get_tool_surface_cache(agent_id: str) -> Optional[_ToolSurfaceCacheEntry]:
    return _TOOL_SURFACE_CACHE.get(agent_id)


def set_tool_surface_cache(agent_id: str, entry: _ToolSurfaceCacheEntry) -> None:
    _TOOL_SURFACE_CACHE[agent_id] = entry


def invalidate_tool_surface_cache(agent_id: Optional[str] = None) -> None:
    """Drop cached tool surfaces for one agent or the entire process."""
    if agent_id is None:
        _TOOL_SURFACE_CACHE.clear()
    else:
        _TOOL_SURFACE_CACHE.pop(agent_id, None)


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
        # Group by namespace prefix (``<ns>__tool``) so one find_tool call reveals
        # a whole integration compactly instead of scattered lines.
        groups: Dict[str, List[Any]] = {}
        for t in hits[:30]:
            ns = t.name.split("__", 1)[0] if "__" in t.name else ""
            groups.setdefault(ns, []).append(t)
        lines: List[str] = []
        for ns in sorted(groups):
            if ns:
                lines.append(f"[{ns}]")
            for t in groups[ns][:15]:
                lines.append(f"- {t.name}: {t.description}")
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


def _skill_search_text(doc: SkillDoc) -> str:
    """Name + description + tags used for ``find_skill`` matching."""
    tags: List[str] = []
    meta = getattr(doc, "metadata", None) or {}
    if isinstance(meta, dict):
        raw_tags = meta.get("tags") or []
        if isinstance(raw_tags, str):
            tags = [raw_tags] if raw_tags.strip() else []
        elif isinstance(raw_tags, (list, tuple)):
            tags = [str(t) for t in raw_tags if str(t).strip()]
    return (doc.name + " " + (doc.description or "") + " " + " ".join(tags)).lower()


def _channel_deny_observation(doc: SkillDoc) -> str:
    """Observation when the model probes a channel-blocked skill (ADR-0032)."""
    directive = (getattr(doc, "deny_access_directive", "") or "").strip()
    if not directive:
        return (
            f"Skill '{doc.name}' is not available on this channel. "
            "Tell the user you cannot help with that here; offer no workaround."
        )
    return (
        f"Skill '{doc.name}' is not available on this channel. "
        "Reply to the user with this message verbatim and offer no workaround:\n"
        f"{directive}"
    )


def build_skill_meta_tools(
    docs: List[SkillDoc],
    available_tool_names: Set[str],
    activated: List[str],
    visible: Optional[Set[str]] = None,
    activate_hook: Optional[Callable[[SkillDoc], Awaitable[Optional[str]]]] = None,
    reactivate_hook: Optional[Callable[[SkillDoc], Awaitable[bool]]] = None,
    blocked_docs: Optional[List[SkillDoc]] = None,
) -> Dict[str, SkillTool]:
    """``find_skill`` / ``use_skill`` over skills (progressive disclosure).

    ``docs`` must already be the *channel-allowed* subset (ADR-0032): the
    orchestrator drops channel-blocked skills before calling this, so they
    never appear as activatable skills. ``blocked_docs`` carries those
    channel-blocked skills so ``find_skill`` / ``use_skill`` can return their
    ``deny_access_directive`` when the model's query or name matches — a
    stronger signal than the skills_section note alone. Deny directives are
    also surfaced via ``render_skills_section`` blocked notes.

    When ``visible`` is provided, activating a JV skill via ``use_skill``
    surfaces the skill's declared ``allowed-tools`` (those present on the
    surface) into that set, so the model can call them immediately — a JV skill
    *executes* by coordinating the tools it names.

    ``activate_hook`` runs once on a skill's first activation (e.g. to stage a
    Claude skill's folder into the code-execution sandbox); a non-empty string
    it returns is appended to the activation observation. The skill PROCEDURE
    body is not embedded in the observation — the orchestrator surfaces it via
    system ``skills_section`` so Steps taken this turn stays TOOL-only.
    """
    blocked = list(blocked_docs or [])
    if not docs and not blocked:
        return {}
    index = {d.name: d for d in docs}
    blocked_index = {d.name: d for d in blocked}

    async def _find(args: Dict[str, Any]) -> str:
        q = ((args or {}).get("query") or "").strip().lower()
        # Channel-blocked match wins: relay deny instead of listing unrelated
        # allowed skills (stops the model improvising a gated flow).
        if q:
            blocked_hits = [
                d
                for d in blocked
                if q in _skill_search_text(d)
                and (getattr(d, "deny_access_directive", "") or "").strip()
            ]
            if blocked_hits:
                return "\n\n".join(
                    _channel_deny_observation(d) for d in blocked_hits[:3]
                )
        hits = [d for d in docs if not q or q in _skill_search_text(d)] or list(docs)
        if not hits:
            return "(no skills matched)"
        lines = [f"- {d.name}: {d.description}" for d in hits[:10]]
        return "Available skills (call use_skill to load one):\n" + "\n".join(lines)

    async def _use(args: Dict[str, Any]) -> str:
        name = ((args or {}).get("name") or "").strip()
        blocked_doc = blocked_index.get(name)
        if blocked_doc is not None:
            return _channel_deny_observation(blocked_doc)
        doc = index.get(name)
        if doc is None:
            return f"(no such skill: {name})"
        present = [t for t in doc.requires_tools if t in available_tool_names]
        missing = [t for t in doc.requires_tools if t not in available_tool_names]
        # Idempotent: re-activating a skill already loaded this turn returns a
        # short directive instead of re-dumping activation, so the model proceeds
        # with the procedure (in skills_section) instead of looping on use_skill.
        if name in activated:
            if visible is not None and present:
                visible.update(present)
            staged = ""
            if reactivate_hook is not None and activate_hook is not None:
                try:
                    if await reactivate_hook(doc):
                        note = await activate_hook(doc)
                        if note:
                            staged = f"\n\n{note}"
                except Exception as exc:
                    staged = f"\n\n(activation hook error: {exc})"
            hint = f" Its tools are available: {', '.join(present)}." if present else ""
            return (
                f"Skill '{doc.name}' is already active.{hint}{staged} Proceed with its "
                "steps now using the available tools; do not call use_skill for "
                "it again."
            )
        activated.append(name)
        warn = ""
        if missing:
            warn = (
                "\n\n(Note: these referenced tools are not currently available: "
                + ", ".join(missing)
                + ". Adapt accordingly or report the gap.)"
            )
        staged = ""
        if activate_hook is not None:
            try:
                note = await activate_hook(doc)
            except Exception as exc:  # never break activation on a hook failure
                note = f"(activation hook error: {exc})"
            if note:
                staged = f"\n\n{note}"
        # Surface declared tools only after activation hooks run (e.g. skill
        # session bootstrap) so the model cannot call skill tools with
        # NO_SESSION on the same turn.
        if visible is not None and present:
            visible.update(present)
        surfaced = f" Tools now callable: {', '.join(present)}." if present else ""
        # PROCEDURE lives in skills_section (system prompt), not here — keeps
        # "Steps taken this turn" a contiguous list of TOOL lines.
        return f"Activated skill '{doc.name}'.{surfaced}{staged}{warn}"

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


__all__ = [
    "build_catalog_tools",
    "build_skill_meta_tools",
    "compute_tool_surface_config_hash",
    "get_tool_surface_cache",
    "set_tool_surface_cache",
    "invalidate_tool_surface_cache",
]
