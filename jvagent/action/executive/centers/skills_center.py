"""SkillsCenter — skill-based reasoning (ADR-0010 §2.1, M5).

A specialist leaf that completes a task via a bounded **think-act-observe**
loop over a pluggable tool surface. One model call per tick: the model picks a
tool (the center runs it and re-enters via ``STEP``) or gives a final answer
(``RETURN``). Bounded by ``max_iterations``.

Deliberately self-contained — it does NOT import the reasoning helm. The tool
surface is resolved from the agent's enabled actions via their ``get_tools()``
(web_search, pageindex, calendar, …), each wrapped as a :class:`SkillTool`;
tests/bespoke wiring may inject a fixed surface via :meth:`set_tools`. An empty
surface is valid — the center then answers directly.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.executive.base import BaseCenter
from jvagent.action.executive.contracts import RETURN, STEP, CenterDirective, Result
from jvagent.action.executive.prompts import (
    SKILLS_SYSTEM_PROMPT,
    SKILLS_USER_PROMPT_TEMPLATE,
    render_observations_section,
    render_tools_section,
)
from jvagent.action.executive.skills_catalog import SkillDoc, discover_skill_docs

if TYPE_CHECKING:
    from jvagent.action.executive.context import TurnContext
    from jvagent.action.executive.state import Frame

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 8


@dataclass
class SkillTool:
    """A tool the Skills center can call: name, description, async runner."""

    name: str
    description: str
    run: Callable[[Dict[str, Any]], Awaitable[str]]


class SkillsCenter(BaseCenter):
    """Skill-based reasoning center (think-act-observe)."""

    description: str = attribute(
        default="Skills center — completes tasks via tool-using step-by-step reasoning.",
    )
    latency_class: str = attribute(default="deliberate")

    model: str = attribute(default="gpt-4o-mini")
    model_action_type: str = attribute(default="OpenAILanguageModelAction")
    model_temperature: float = attribute(default=0.2)
    model_max_tokens: int = attribute(default=1024)
    enforce_json_mode: bool = attribute(default=True)
    max_iterations: int = attribute(default=DEFAULT_MAX_ITERATIONS)
    exhausted_text: str = attribute(
        default="I wasn't able to complete that within the step budget.",
    )

    # -- Skill overlay (native SOP skills; ADR-0011) ------------------------
    skills_source: str = attribute(
        default="both",
        description="Skill discovery source: both | local | app | registry | builtin.",
    )
    skills: Any = attribute(
        default="-all",
        description="Skill selector: '-all' to keep all, or a list of name globs.",
    )
    denied_skills: List[str] = attribute(default_factory=list)

    def center_name(self) -> str:
        return "SkillsCenter"

    # -- Skill discovery (injectable for tests) -----------------------------

    def set_skills(self, docs: List[SkillDoc]) -> None:
        """Inject a fixed set of SOP skills (tests / explicit wiring)."""
        object.__setattr__(self, "_injected_skills", list(docs))

    async def _discover_skills(self, ctx: "TurnContext") -> List[SkillDoc]:
        injected = self.__dict__.get("_injected_skills")
        if injected is not None:
            return list(injected)
        try:
            return discover_skill_docs(
                getattr(ctx, "agent", None),
                skills_source=self.skills_source,
                selector=self.skills,
                denied=list(self.denied_skills or []),
            )
        except Exception as exc:
            logger.debug("SkillsCenter: skill discovery failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Tool surface (overridable in tests / future registry adapter)
    # ------------------------------------------------------------------

    def set_tools(self, tools: List[SkillTool]) -> None:
        """Inject a fixed tool surface (tests / explicit wiring)."""
        object.__setattr__(self, "_injected_tools", list(tools))

    async def _resolve_tools(
        self, ctx: "TurnContext", frame: "Frame"
    ) -> Dict[str, SkillTool]:
        """Resolve the tool surface for this activation.

        Precedence: explicitly injected tools (tests / bespoke wiring) win;
        otherwise build from the agent's enabled actions' ``get_tools()``
        (web_search, pageindex, calendar, …). Cached on the frame so a
        multi-``STEP`` loop enumerates once.
        """
        injected = self.__dict__.get("_injected_tools")
        if injected:
            return {t.name: t for t in injected}
        cached = frame.scratch.get("_resolved_tools")
        if cached is not None:
            return cached
        action_tools = await self._build_agent_tools(ctx.agent)
        skill_tools = await self._build_skill_meta_tools(ctx, frame, action_tools)
        tools = {**action_tools, **skill_tools}
        frame.scratch["_resolved_tools"] = tools
        return tools

    async def _build_skill_meta_tools(
        self,
        ctx: "TurnContext",
        frame: "Frame",
        action_tools: Dict[str, SkillTool],
    ) -> Dict[str, SkillTool]:
        """Expose native SOP skills via ``find_skill`` / ``use_skill`` meta-tools.

        Progressive disclosure: only skill names + descriptions are surfaced
        up front; ``use_skill`` loads the full SOP body (returned as an
        observation, so it persists for the rest of the loop). Skills are
        SOP-only — they reference action tools by name, they don't execute.
        """
        docs = await self._discover_skills(ctx)
        if not docs:
            return {}
        index = {d.name: d for d in docs}
        frame.scratch["_skill_index"] = index
        available = set(action_tools.keys())

        async def _find(args: Dict[str, Any], _docs: List[SkillDoc] = docs) -> str:
            q = ((args or {}).get("query") or "").strip().lower()
            hits = [
                d for d in _docs if not q or q in (d.name + " " + d.description).lower()
            ] or _docs
            lines = [f"- {d.name}: {d.description}" for d in hits[:10]]
            return "Available skills (call use_skill to load one):\n" + "\n".join(lines)

        async def _use(
            args: Dict[str, Any], _frame: "Frame" = frame, _avail: set = available
        ) -> str:
            name = ((args or {}).get("name") or "").strip()
            idx = _frame.scratch.get("_skill_index", {})
            doc = idx.get(name)
            if doc is None:
                return f"(no such skill: {name})"
            activated = _frame.scratch.setdefault("activated_skills", [])
            if name not in activated:
                activated.append(name)
            missing = [t for t in doc.requires_tools if t not in _avail]
            warn = ""
            if missing:
                warn = (
                    "\n\n(Note: these referenced tools are not currently available: "
                    + ", ".join(missing)
                    + ". Adapt accordingly or report the gap.)"
                )
            return f"Activated skill '{doc.name}'.\n\nPROCEDURE:\n{doc.body}{warn}"

        return {
            "find_skill": SkillTool(
                name="find_skill",
                description="Search available skills (standard operating procedures) by query.",
                run=_find,
            ),
            "use_skill": SkillTool(
                name="use_skill",
                description="Activate a skill by exact name to load its procedure (SOP).",
                run=_use,
            ),
        }

    async def _build_agent_tools(self, agent: Any) -> Dict[str, SkillTool]:
        """Wrap every enabled action's ``get_tools()`` Tool as a :class:`SkillTool`.

        Best-effort and defensive — a single misbehaving action never breaks
        the surface. Returns an empty dict when no agent / no tool-providing
        actions are present (a valid state: the center answers directly).
        """
        if agent is None:
            return {}
        try:
            mgr = await agent.get_actions_manager()
            actions = await mgr.get_all_actions(enabled_only=True) if mgr else []
        except Exception as exc:
            logger.debug("SkillsCenter: action enumeration failed: %s", exc)
            return {}
        resolved: Dict[str, SkillTool] = {}
        for action in actions or []:
            get_tools = getattr(action, "get_tools", None)
            if not callable(get_tools):
                continue
            try:
                action_tools = await get_tools()
            except Exception as exc:
                logger.debug(
                    "SkillsCenter: get_tools() failed on %s: %s",
                    type(action).__name__,
                    exc,
                )
                continue
            for tool in action_tools or []:
                name = getattr(tool, "name", None)
                if not name:
                    continue
                resolved[name] = self._wrap_action_tool(tool)
        return resolved

    @staticmethod
    def _wrap_action_tool(tool: Any) -> SkillTool:
        """Adapt a ``jvagent.tooling.tool.Tool`` to a :class:`SkillTool`.

        The underlying ``Tool.call(**kwargs)`` returns a ``ToolResult``; we
        surface its ``.content`` string to the skill loop's observation log.
        """

        async def _run(args: Dict[str, Any], _tool: Any = tool) -> str:
            try:
                result = await _tool.call(**(args or {}))
            except Exception as exc:
                return f"(tool error: {exc})"
            return (getattr(result, "content", "") or "") if result is not None else ""

        return SkillTool(
            name=getattr(tool, "name", "tool"),
            description=getattr(tool, "description", "") or "",
            run=_run,
        )

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    async def tick(
        self,
        ctx: "TurnContext",
        frame: "Frame",
    ) -> CenterDirective:
        s = frame.scratch
        s.setdefault("observations", [])
        iterations = int(s.get("iterations", 0))
        if iterations >= max(1, int(self.max_iterations)):
            logger.info("SkillsCenter: iteration cap reached → returning best-effort")
            return RETURN(Result(content=s.get("last_answer") or self.exhausted_text))
        s["iterations"] = iterations + 1

        tools = await self._resolve_tools(ctx, frame)
        task = frame.brief.intent if frame.brief else (ctx.utterance or "")
        decision = await self._call_skill_model(
            ctx, task, list(tools.values()), s["observations"]
        )
        if decision is None:
            return RETURN(Result(content="I ran into an error working on that."))

        action = (decision.get("action") or "").strip().lower()
        tool_field = (decision.get("tool") or "").strip()
        # Robustness: models frequently deviate from the {"action":"tool",
        # "tool":"name"} shape — e.g. {"action":"web_search__search", ...}
        # (tool name in `action`) or {"tool":"...","args":{...}} with no
        # `action`. Normalize all of these to a tool call so a near-miss
        # JSON shape doesn't waste the whole step budget.
        if action not in ("tool", "final"):
            if tool_field:
                action = "tool"
            elif action in tools:  # the `action` value *is* a tool name
                tool_field = decision.get("action") or ""
                action = "tool"
            elif decision.get("answer"):
                action = "final"

        if action == "final":
            answer = (decision.get("answer") or "").strip()
            return RETURN(Result(content=answer or self.exhausted_text))

        if action == "tool":
            tool_name = tool_field
            args = (
                decision.get("args") if isinstance(decision.get("args"), dict) else {}
            )
            tool = tools.get(tool_name)
            if tool is None:
                observation = f"(no such tool: {tool_name})"
            else:
                try:
                    observation = await tool.run(args)
                except Exception as exc:
                    logger.warning("SkillsCenter: tool %r raised: %s", tool_name, exc)
                    observation = f"(tool error: {exc})"
            s["observations"].append(
                {"tool": tool_name, "args": args, "observation": observation}
            )
            return STEP()

        # Unknown action — finish defensively rather than loop.
        logger.warning("SkillsCenter: unknown action %r → returning", action)
        return RETURN(Result(content=self.exhausted_text))

    # ------------------------------------------------------------------
    # Model call (mocked in tests)
    # ------------------------------------------------------------------

    async def _call_skill_model(
        self,
        ctx: "TurnContext",
        task: str,
        tools: List[SkillTool],
        observations: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """One model call → parsed JSON decision. Acquires the per-tick budget."""
        ctx.use_model()
        model_action = await self.get_model_action(required=False)
        if model_action is None:
            logger.warning(
                "SkillsCenter: no model action (model_action_type=%r)",
                self.model_action_type,
            )
            return None
        system_prompt = SKILLS_SYSTEM_PROMPT.format(
            tools_section=render_tools_section(tools)
        )
        user_prompt = SKILLS_USER_PROMPT_TEMPLATE.format(
            task=task or "(no task given)",
            observations_section=render_observations_section(observations),
        )
        kwargs: Dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "system": system_prompt,
            "prompt_for_observability": user_prompt,
            "tools": None,
            "model": self.model or None,
            "temperature": self.model_temperature,
            "max_tokens": self.model_max_tokens,
            "calling_action_name": self.center_name(),
        }
        if self.enforce_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            result = await model_action.query_messages(**kwargs)
        except Exception as exc:
            logger.warning("SkillsCenter: model call raised: %s", exc)
            return None
        raw = (getattr(result, "response", None) or "").strip()
        return _parse_json_object(raw) if raw else None


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    candidate = (raw or "").strip()
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(candidate)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


__all__ = ["SkillsCenter", "SkillTool"]
