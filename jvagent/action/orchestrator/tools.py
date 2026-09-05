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
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from jvagent.action.model.utils.json_utils import strip_json_fences
from jvagent.action.orchestrator.access import is_tool_allowed

logger = logging.getLogger(__name__)


def _empty_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {}}


@dataclass
class SkillTool:
    """A tool the Orchestrator loop can call: name, description, async runner.

    ``terminal`` marks tools that own the turn's user-facing output; the
    loop ends after a terminal tool runs so the orchestrator won't double-reply.
    IA-as-tools and ``@tool(terminal=True)`` capability tools set this;
    plain tools leave it ``False``.

    ``parameters_schema`` is the tool's JSON Schema for its arguments. Under
    the native tool protocol (ADR-0044) it is what the provider validates and
    what the model reads to name arguments correctly; under the JSON-text
    protocol it is unused (arguments are described in prose).
    """

    name: str
    description: str
    run: Callable[[Dict[str, Any]], Awaitable[str]]
    terminal: bool = False
    parameters_schema: Dict[str, Any] = field(default_factory=_empty_schema)


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

    schema = getattr(tool, "parameters_schema", None)
    return SkillTool(
        name=name,
        description=getattr(tool, "description", "") or "",
        run=_run,
        terminal=terminal or bool(getattr(tool, "terminal", False)),
        parameters_schema=(
            dict(schema) if isinstance(schema, dict) and schema else _empty_schema()
        ),
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


# --------------------------------------------------------------------------- #
# Native tool protocol (ADR-0044)
# --------------------------------------------------------------------------- #
#
# Under the native protocol the provider's function-calling API carries the
# decision: the tools go up as JSON-Schema'd definitions, the model's
# ``tool_calls`` come back as the step, and this turn's prior steps replay as
# assistant ``tool_calls`` + ``tool`` result messages instead of a text digest.

# Provider limits on tool names (OpenAI: ``^[a-zA-Z0-9_-]{1,64}$``; Anthropic is
# the same shape). A name outside them is aliased for the wire and mapped back.
_NATIVE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_NATIVE_NAME_BAD_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
# OpenAI caps a function description at 1024 characters.
NATIVE_DESCRIPTION_MAX_CHARS = 1024
# Marker prefix for harness-authored notes replayed as user-role messages.
HARNESS_NOTE_PREFIX = "[harness note] "


def native_tool_name(name: str) -> str:
    """The wire-safe name for *name* (unchanged when already valid)."""
    raw = str(name or "")
    if _NATIVE_NAME_RE.match(raw):
        return raw
    safe = _NATIVE_NAME_BAD_CHARS.sub("_", raw).strip("_") or "tool"
    return safe[:64]


def native_tool_definitions(
    tools: List[SkillTool],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Serialise *tools* to OpenAI-style function definitions.

    Returns ``(definitions, alias_map)`` where ``alias_map`` maps each wire
    name back to the loop's tool name (identity for names that needed no
    aliasing). Collisions after sanitising are disambiguated with a numeric
    suffix so two loop tools can never share a wire name.
    """
    definitions: List[Dict[str, Any]] = []
    alias_map: Dict[str, str] = {}
    for tool in tools:
        wire = native_tool_name(tool.name)
        if wire in alias_map and alias_map[wire] != tool.name:
            base = wire[:60]
            n = 2
            while f"{base}_{n}" in alias_map:
                n += 1
            wire = f"{base}_{n}"
        alias_map[wire] = tool.name
        schema = tool.parameters_schema
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = _empty_schema()
        description = (tool.description or "").strip()
        if len(description) > NATIVE_DESCRIPTION_MAX_CHARS:
            description = description[: NATIVE_DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": wire,
                    "description": description,
                    "parameters": schema,
                },
            }
        )
    return definitions, alias_map


def _elide_args_json(args: Any, limit: int) -> Any:
    """Shrink long string leaves in *args* while keeping it valid JSON.

    A replayed ``tool_calls`` entry must stay parseable, so instead of eliding
    the serialised text (as the JSON-text protocol does) the string values
    themselves are elided. ``limit <= 0`` disables.
    """
    if limit <= 0:
        return args
    if isinstance(args, str):
        return elide_middle(args, limit)
    if isinstance(args, dict):
        return {k: _elide_args_json(v, limit) for k, v in args.items()}
    if isinstance(args, list):
        return [_elide_args_json(v, limit) for v in args]
    return args


def _is_model_call(obs: Dict[str, Any]) -> bool:
    """True for an observation produced by a model-issued tool call.

    The loop stamps ``call_id`` (and ``call_tool`` / ``call_args``, the call as
    the model made it) onto the first observation a decision produced — the
    dispatched result, or the guard note that stood in for it.
    """
    return bool(obs.get("call_id"))


def render_observation_messages(
    observations: List[Dict[str, Any]],
    *,
    max_chars: int = DEFAULT_OBSERVATION_MAX_CHARS,
    stale_max_chars: int = DEFAULT_STALE_OBSERVATION_MAX_CHARS,
    full_recent: int = DEFAULT_OBSERVATION_FULL_RECENT,
    args_max_chars: int = DEFAULT_OBSERVATION_ARGS_MAX_CHARS,
    max_observations: int = MAX_OBSERVATIONS_IN_PROMPT,
    alias_for: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Replay this turn's steps as chat messages for the native protocol.

    - A step the model took (``call_id`` set) becomes an assistant message
      carrying the ``tool_calls`` entry, followed by a ``tool`` message with
      the (size-bounded) result. Steps sharing a ``group_id`` (parallel calls
      the provider returned together) fold into one assistant message.
    - Any ``assistant_text`` recorded on a step is the prose the model emitted
      with it and rides as the assistant message's ``content``.
    - Every other observation (server-generated framing: guards, prep notes,
      seeds) becomes a user-role harness note. Consecutive notes merge.

    The same count/size caps as :func:`render_observations_section` apply, so
    the two protocols bill comparably. ``alias_for`` maps loop tool names to
    the wire names used in the definitions sent alongside.
    """
    if not observations:
        return []
    view = list(observations)
    messages: List[Dict[str, Any]] = []
    if max_observations > 0 and len(view) > max_observations:
        truncated = len(view) - max_observations
        view = view[-max_observations:]
        messages.append(
            {
                "role": "user",
                "content": f"{HARNESS_NOTE_PREFIX}(…{truncated} earlier tool results omitted)",
            }
        )
    recent_from = len(view) - full_recent if full_recent > 0 else 0
    aliases = alias_for or {}

    i = 0
    while i < len(view):
        obs = view[i]
        if not _is_model_call(obs):
            prose = str(obs.get("assistant_text") or "").strip()
            if prose:
                # The model answered in prose and the harness deflected it (a
                # guard): keep the transcript honest — its text, then the note.
                messages.append({"role": "assistant", "content": prose})
            note = str(obs.get("observation", "")).strip()
            if note:
                text = HARNESS_NOTE_PREFIX + note
                if (
                    messages
                    and messages[-1].get("role") == "user"
                    and str(messages[-1].get("content", "")).startswith(
                        HARNESS_NOTE_PREFIX
                    )
                ):
                    messages[-1]["content"] = f"{messages[-1]['content']}\n{text}"
                else:
                    messages.append({"role": "user", "content": text})
            i += 1
            continue
        # A group of model-issued calls returned together.
        group = obs.get("group_id") or obs.get("call_id")
        j = i
        while (
            j < len(view)
            and _is_model_call(view[j])
            and (view[j].get("group_id") or view[j].get("call_id")) == group
        ):
            j += 1
        members = view[i:j]
        assistant: Dict[str, Any] = {
            "role": "assistant",
            "content": "",
            "tool_calls": [],
        }
        text = str(members[0].get("assistant_text") or "").strip()
        if text:
            assistant["content"] = text
        results: List[Dict[str, Any]] = []
        for k, member in enumerate(members):
            index = i + k
            limit = max_chars if index >= recent_from else stale_max_chars
            name = str(member.get("call_tool") or member.get("tool", ""))
            wire = aliases.get(name, name)
            raw_args = member.get("call_args", member.get("args"))
            args = raw_args if isinstance(raw_args, dict) else {}
            assistant["tool_calls"].append(
                {
                    "id": str(member["call_id"]),
                    "type": "function",
                    "function": {
                        "name": wire,
                        "arguments": json.dumps(
                            _elide_args_json(args, args_max_chars), ensure_ascii=False
                        ),
                    },
                }
            )
            results.append(
                {
                    "role": "tool",
                    "tool_call_id": str(member["call_id"]),
                    "name": wire,
                    "content": elide_middle(str(member.get("observation", "")), limit),
                }
            )
        messages.append(assistant)
        messages.extend(results)
        i = j
    return messages


def _parse_call_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = parse_json_object(text)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def decisions_from_native_result(
    tool_calls: Optional[List[Dict[str, Any]]],
    text: str,
    *,
    alias_map: Optional[Dict[str, str]] = None,
    text_as_reply: bool = False,
) -> List[Dict[str, Any]]:
    """Map a provider result to loop decisions (native protocol).

    Each tool call becomes ``{"action": "tool", "tool": <loop name>, "args":
    {...}, "_call_id": ..., "_group_id": ...}``; the model's prose (if any)
    rides on the first as ``_assistant_text``. With no tool call, non-empty
    text is the reply: a ``reply`` tool call when ``text_as_reply`` (the
    egress tool is on the surface), else ``{"action": "final", "answer": text}``
    (the finalize tick). Returns ``[]`` when the result carried neither.
    """
    aliases = alias_map or {}
    text = (text or "").strip()
    decisions: List[Dict[str, Any]] = []
    calls = [c for c in (tool_calls or []) if isinstance(c, dict)]
    group_id = ""
    for call in calls:
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        wire = str(fn.get("name") or call.get("name") or "").strip()
        if not wire:
            continue
        call_id = str(call.get("id") or "") or f"call_{uuid.uuid4().hex[:12]}"
        if not group_id:
            group_id = call_id
        decisions.append(
            {
                "action": "tool",
                "tool": aliases.get(wire, wire),
                "args": _parse_call_arguments(
                    fn.get("arguments") if fn else call.get("arguments")
                ),
                "_call_id": call_id,
                "_group_id": group_id,
                "_assistant_text": text if not decisions else "",
            }
        )
    if decisions:
        return decisions
    if text and text_as_reply:
        return [
            {
                "action": "tool",
                "tool": "reply",
                "args": {"text": text},
                "_assistant_text": text,
            }
        ]
    if text:
        return [{"action": "final", "answer": text, "_assistant_text": text}]
    return []


__all__ = [
    "SkillTool",
    "wrap_action_tool",
    "render_tools_section",
    "render_observations_section",
    "render_observation_messages",
    "native_tool_definitions",
    "native_tool_name",
    "decisions_from_native_result",
    "HARNESS_NOTE_PREFIX",
    "NATIVE_DESCRIPTION_MAX_CHARS",
    "elide_middle",
    "parse_json_object",
    "MAX_OBSERVATIONS_IN_PROMPT",
    "DEFAULT_OBSERVATION_MAX_CHARS",
    "DEFAULT_STALE_OBSERVATION_MAX_CHARS",
    "DEFAULT_OBSERVATION_FULL_RECENT",
    "DEFAULT_OBSERVATION_ARGS_MAX_CHARS",
]
