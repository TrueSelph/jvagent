"""Tool primitives for the Orchestrator loop (ADR-0012).

A :class:`SkillTool` is the loop's uniform call surface: a name, a description,
and an async ``run(args) -> str`` that returns an observation string. Action
``get_tools()`` ``Tool`` objects, IA-as-tools, persona tools, core tools, and
the catalog meta-tools are all adapted to this shape by :func:`wrap_action_tool`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from jvagent.action.orchestrator.access import is_tool_allowed

logger = logging.getLogger(__name__)


@dataclass
class SkillTool:
    """A tool the Orchestrator loop can call: name, description, async runner.

    ``terminal`` marks IA-as-tools that own the turn's user-facing output; the
    loop ends after a terminal tool runs so the orchestrator won't double-voice.
    Plain tools leave it ``False``.
    """

    name: str
    description: str
    run: Callable[[Dict[str, Any]], Awaitable[str]]
    terminal: bool = False


def wrap_action_tool(
    tool: Any,
    *,
    visitor: Any = None,
    terminal: bool = False,
    agent: Any = None,
    user_id: Any = None,
    channel: str = "default",
    access_label: Optional[str] = None,
) -> SkillTool:
    """Adapt a ``jvagent.tooling.tool.Tool`` to a :class:`SkillTool`.

    ``Tool.call(**kwargs)`` returns a ``ToolResult``; we surface ``.content`` to
    the loop's observation log. Defensive — a raising tool yields an error
    observation rather than breaking the turn. This is the single binder for
    every tool family; the keyword bindings are all opt-in, so a plain capability
    tool wraps with no extra behavior:

    - ``visitor`` — injected into ``call`` for tools that publish through the
      turn's walker (persona ``reply``/``respond``, IA-as-tools). Omit it for
      plain tools whose ``call`` does not accept a ``visitor``.
    - ``access_label`` — when set, dispatch is gated by AccessControl
      (``is_tool_allowed``); a denied call returns ``"(access denied)"`` and the
      tool never runs. IA-as-tools pass ``tool:delegate:{name}``; this is the
      hook for per-user gating of any tool call.
    - ``terminal`` — marks tools that own the turn's user-facing output
      (IA-as-tools), so the loop ends after they run.

    ``agent`` / ``user_id`` / ``channel`` supply the AC context and are consulted
    only when ``access_label`` is set.
    """
    name = getattr(tool, "name", "tool")

    async def _run(args: Dict[str, Any], _tool: Any = tool) -> str:
        if access_label is not None and not await is_tool_allowed(
            agent, label=access_label, user_id=user_id, channel=channel
        ):
            return "(access denied)"
        call_kwargs = dict(args or {})
        if visitor is not None:
            call_kwargs["visitor"] = visitor
        try:
            result = await _tool.call(**call_kwargs)
        except Exception as exc:
            logger.warning("wrap_action_tool: tool %r raised: %s", name, exc)
            return f"(tool error: {exc})"
        return (getattr(result, "content", "") or "") if result is not None else ""

    return SkillTool(
        name=name,
        description=getattr(tool, "description", "") or "",
        run=_run,
        terminal=terminal,
    )


def render_tools_section(tools: List[Any], *, lean: bool = False) -> str:
    """Render ``[{name, description}]`` (or objects) as a bulleted list.

    ``lean=True`` appends a one-line hint that this is a *partial* surface and
    more tools are reachable via ``find_tool`` — used when lean surfacing keeps
    the long tail off the prompt (ADR-0018).
    """
    if not tools:
        return '(no tools available — answer directly with action "final")'
    lines: List[str] = []
    for t in tools:
        name = t["name"] if isinstance(t, dict) else getattr(t, "name", "")
        desc = (
            t.get("description", "")
            if isinstance(t, dict)
            else getattr(t, "description", "")
        )
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    body = "\n".join(lines)
    if lean:
        body += (
            "\n\n(This is a partial list of your most relevant tools. More are "
            "available — call find_tool(query) to discover them by capability, "
            "e.g. find_tool('file'), find_tool('email'), find_tool('calendar'), "
            "then call the tool it returns.)"
        )
    return body


def render_observations_section(observations: List[Dict[str, Any]]) -> str:
    if not observations:
        return "(none yet)"
    lines: List[str] = []
    for obs in observations:
        tool = obs.get("tool", "")
        args = obs.get("args", {})
        result = obs.get("observation", "")
        lines.append(f"TOOL {tool}({args}) → {result}")
    return "\n".join(lines)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Parse the first JSON object out of a model response (tolerant)."""
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


__all__ = [
    "SkillTool",
    "wrap_action_tool",
    "render_tools_section",
    "render_observations_section",
    "parse_json_object",
]
