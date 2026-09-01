"""Tool primitives for the Orchestrator loop (ADR-0012).

A :class:`SkillTool` is the loop's uniform call surface: a name, a description,
and an async ``run(args) -> str`` that returns an observation string. Action
``get_tools()`` ``Tool`` objects, IA-as-tools, persona tools, core tools, and
the catalog meta-tools are all adapted to this shape by :func:`wrap_action_tool`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from jvagent.action.model.utils.json_utils import strip_json_fences
from jvagent.action.orchestrator.access import is_tool_allowed

logger = logging.getLogger(__name__)


@dataclass
class SkillTool:
    """A tool the Orchestrator loop can call: name, description, async runner.

    ``terminal`` marks tools that own the turn's user-facing output; the
    loop ends after a terminal tool runs so the orchestrator won't double-reply.
    IA-as-tools and ``@tool(terminal=True)`` capability tools set this;
    plain tools leave it ``False``.
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
      (IA-as-tools), so the loop ends after they run. Also inherited from
      ``tool.terminal`` when the wrapped Tool was decorated
      ``@tool(terminal=True)``.

    ``agent`` / ``user_id`` / ``channel`` supply the AC context and are consulted
    only when ``access_label`` is set.
    """
    name = getattr(tool, "name", "tool")
    effective_access_label = (
        access_label
        if access_label is not None
        else getattr(tool, "access_label", None)
    )

    async def _run(args: Dict[str, Any], _tool: Any = tool) -> str:
        if effective_access_label is not None and not await is_tool_allowed(
            agent, label=effective_access_label, user_id=user_id, channel=channel
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
        terminal=terminal or bool(getattr(tool, "terminal", False)),
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
            "\n\n(This is a PARTIAL list of your most relevant tools. If the "
            "exact tool a step needs isn't here, call find_tool(query) to "
            "discover it — e.g. find_tool('add to knowledge base'), "
            "find_tool('fetch url'), find_tool('send email') — then call the "
            "tool it returns. Do NOT substitute a similar-looking tool from "
            "this list (a read/search tool when you need to write/save will "
            "fail). Prefer passing gathered text in tool args over inventing "
            "a filesystem write unless the user asked for a file.)"
        )
    return body


# Cap replay into the model prompt — unbounded observations blow context.
MAX_OBSERVATIONS_IN_PROMPT = 12

# Size caps for the observation replay. The count cap above bounds HOW MANY
# results are replayed; these bound how BIG each one is. Without them the
# per-turn input cost is quadratic in tick count: every tick re-sends every
# prior result in full, so an 8-tick research turn over 8 KB page fetches bills
# ~70k input tokens and a 20-tick one ~325k (measured on the example agent).
#
# The most recent results are kept near-verbatim (the model is usually acting on
# what it just fetched); older ones are elided hard, since by then their value is
# "what happened", not the payload. Elision is middle-out and always marked, so
# the model can see it was trimmed and re-run the tool if it truly needs the body.
DEFAULT_OBSERVATION_MAX_CHARS = 4000
DEFAULT_STALE_OBSERVATION_MAX_CHARS = 600
DEFAULT_OBSERVATION_FULL_RECENT = 3
DEFAULT_OBSERVATION_ARGS_MAX_CHARS = 400


def elide_middle(text: str, limit: int) -> str:
    """Middle-out elide *text* to ``limit`` characters, marking what was cut.

    Head-and-tail is kept (not a prefix) because a tool result's tail often
    carries the part that matters — a closing status line, the last rows of a
    listing, a trailing error. ``limit <= 0`` disables elision.
    """
    if limit <= 0 or not text or len(text) <= limit:
        return text
    dropped = len(text) - limit
    # Single-line marker: the renderer emits one line per tool result, and a
    # marker carrying newlines would fabricate structure inside a payload.
    marker = f" …[{dropped} chars elided — re-run the tool if you need the rest]… "
    keep = max(0, limit - len(marker))
    head = (keep * 3) // 4
    tail = keep - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def render_observations_section(
    observations: List[Dict[str, Any]],
    *,
    max_chars: int = DEFAULT_OBSERVATION_MAX_CHARS,
    stale_max_chars: int = DEFAULT_STALE_OBSERVATION_MAX_CHARS,
    full_recent: int = DEFAULT_OBSERVATION_FULL_RECENT,
    args_max_chars: int = DEFAULT_OBSERVATION_ARGS_MAX_CHARS,
    max_observations: int = MAX_OBSERVATIONS_IN_PROMPT,
) -> str:
    """Render this turn's tool results for the loop prompt, size-bounded.

    ``max_observations`` bounds how many results replay; the last
    ``full_recent`` of those are elided at ``max_chars`` and everything older at
    ``stale_max_chars``. Arguments are elided at ``args_max_chars`` — a
    write-file call carries its whole payload in ``args``, which would otherwise
    be replayed verbatim on every remaining tick. Any cap set to 0 disables that
    particular limit.
    """
    if not observations:
        return "(none yet)"
    lines: List[str] = []
    view = observations
    if max_observations > 0 and len(view) > max_observations:
        truncated = len(view) - max_observations
        view = view[-max_observations:]
        lines.append(f"(…{truncated} earlier tool results omitted)")
    recent_from = len(view) - full_recent if full_recent > 0 else 0
    for index, obs in enumerate(view):
        tool = obs.get("tool", "")
        args = elide_middle(str(obs.get("args", {})), args_max_chars)
        limit = max_chars if index >= recent_from else stale_max_chars
        result = elide_middle(str(obs.get("observation", "")), limit)
        lines.append(f"TOOL {tool}({args}) → {result}")
    return "\n".join(lines)


def parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    r"""Parse the first JSON object out of a model response.

    Strips markdown `````json`` code fences before parsing so responses from
    providers that don't enforce JSON mode (e.g. ollama) still parse. If parsing
    fails after fence-striipping, logs a warning so malformed responses are
    diagnosable rather than silently dropped.
    """
    candidate = strip_json_fences((raw or "").strip())
    if not candidate:
        return None
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError as exc:
        logger.warning(
            "parse_json_object: failed to parse model response as JSON "
            "(len=%d, err=%s). First 120 chars: %r",
            len(candidate),
            exc,
            candidate[:120],
        )
        return None


__all__ = [
    "SkillTool",
    "wrap_action_tool",
    "render_tools_section",
    "render_observations_section",
    "elide_middle",
    "parse_json_object",
    "MAX_OBSERVATIONS_IN_PROMPT",
    "DEFAULT_OBSERVATION_MAX_CHARS",
    "DEFAULT_STALE_OBSERVATION_MAX_CHARS",
    "DEFAULT_OBSERVATION_FULL_RECENT",
    "DEFAULT_OBSERVATION_ARGS_MAX_CHARS",
]
