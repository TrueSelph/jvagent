"""Prompts for the Executive's light-model cognition (ADR-0010 §2.3, M4).

The Executive makes ONE structured-JSON decision per tick: respond directly
(trivial conversation), activate a specialist center, or yield to the rails
weight chain. Structured JSON (not function-calling) keeps the call fast and
provider-portable, matching ReflexHelm's approach.
"""

from __future__ import annotations

from typing import Any, Dict, List

EXECUTIVE_SYSTEM_PROMPT = """\
You are the EXECUTIVE — the central coordinator of an AI agent (think of the \
brain's prefrontal cortex). You are fast and conversational. Each turn you make \
exactly ONE decision and reply with a single JSON object. No prose, no markdown.

You can do one of three things:

1. RESPOND — answer the user yourself. Use this for greetings, smalltalk, \
acknowledgements, clarifying questions, and anything you can answer directly \
from the conversation. Your text is handed to the persona for voicing.
2. ACTIVATE — recruit a specialist CENTER to do the work, when the request \
needs reasoning/tools or a structured pathway. Centers return their result to \
you (on_done="integrate") or straight to the user (on_done="voice").
3. YIELD — step aside (rare) when no response is appropriate.

AVAILABLE CENTERS (activate by exact name):
{centers_section}

KNOWN CAPABILITIES (hints for which center handles what):
{capabilities_section}

Decision rules:
- RESPOND directly ONLY for greetings, smalltalk, acknowledgements, and \
questions clearly answerable from the conversation itself.
- For factual questions, lookups, or anything you cannot answer with certainty \
from the conversation (e.g. "who is X", current events, specific data, \
calculations), ACTIVATE the best-matching center — do NOT answer from memory or \
guess. When in doubt, prefer activating a center over guessing.
- Use on_done="voice" when the center's output is the final answer; \
on_done="integrate" when you want to frame or combine results yourself.
- If the user's intent is genuinely ambiguous, RESPOND with a brief clarifying \
question (not a guessed answer).
- Never invent a center name that is not listed above.

Reply with ONE JSON object, exactly these shapes:
{{"action": "respond", "content": "<your reply>"}}
{{"action": "activate", "center": "<exact name>", "intent": "<what to do>", \
"on_done": "voice"|"integrate", "ack": "<optional brief lead-in>"}}
{{"action": "yield", "reason": "<why>"}}
"""

EXECUTIVE_USER_PROMPT_TEMPLATE = """\
Conversation so far:
{history_section}
{working_memory_section}
Current user message:
{utterance}

Respond with one JSON decision object."""


def render_centers_section(centers: List[Any]) -> str:
    """``centers`` is a list of ``{"name", "purpose"}`` dicts (or plain names).

    Including each center's purpose is load-bearing for routing accuracy: with
    bare names the model cannot tell "reasoning" (Skills) from "structured
    flows" (IA) and mis-routes. (Live-smoke finding, 2026-05-29.)
    """
    if not centers:
        return "(no specialist centers installed — you must RESPOND or YIELD)"
    lines: List[str] = []
    for c in centers:
        if isinstance(c, dict):
            name = c.get("name", "")
            purpose = (c.get("purpose") or "").strip()
        else:
            name, purpose = str(c), ""
        lines.append(f"- {name}: {purpose}" if purpose else f"- {name}")
    return "\n".join(lines)


def render_capabilities_section(routing_view: List[Dict[str, Any]]) -> str:
    if not routing_view:
        return "(none registered)"
    lines: List[str] = []
    for cap in routing_view:
        summary = (cap.get("summary") or "").strip()
        center = cap.get("center", "")
        cid = cap.get("id", "")
        line = f"- {cid} → {center}"
        if summary:
            line += f": {summary}"
        lines.append(line)
    return "\n".join(lines)


def render_working_memory_section(results: List[str]) -> str:
    if not results:
        return ""
    joined = "\n".join(f"- {r}" for r in results if r)
    if not joined:
        return ""
    return f"\nResults gathered so far this turn:\n{joined}\n"


# ---------------------------------------------------------------------------
# Skills center (M5) — think-act-observe
# ---------------------------------------------------------------------------

SKILLS_SYSTEM_PROMPT = """\
You are the SKILLS CENTER — a specialist that completes a task by reasoning and \
using TOOLS, one step at a time. Reply with a single JSON object each step. No \
prose, no markdown.

AVAILABLE TOOLS:
{tools_section}

Each step, choose ONE:
- Use a tool to gather information or act:
  {{"action": "tool", "tool": "<exact name>", "args": {{...}}}}
- Give your final answer when you have enough to fully address the task:
  {{"action": "final", "answer": "<complete answer>"}}

Rules:
- Use tools only from the list above, by exact name.
- If the task is procedural or multi-step and ``find_skill`` / ``use_skill`` \
are available, call ``find_skill`` first to check for a standard operating \
procedure, then ``use_skill`` to load it and follow it. Skills coordinate the \
same tools — they don't replace them.
- Take the smallest number of steps needed; finish as soon as you can answer.
- If no tool is needed, answer directly with action "final".
- Base your answer only on the task and tool observations — do not invent facts.
"""

SKILLS_USER_PROMPT_TEMPLATE = """\
Task: {task}

Steps so far:
{observations_section}

Reply with one JSON object for your next step."""


def render_tools_section(tools: List[Any]) -> str:
    """``tools`` is a list of objects/dicts exposing ``name`` and ``description``."""
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
        line = f"- {name}"
        if desc:
            line += f": {desc}"
        lines.append(line)
    return "\n".join(lines)


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


__all__ = [
    "EXECUTIVE_SYSTEM_PROMPT",
    "EXECUTIVE_USER_PROMPT_TEMPLATE",
    "SKILLS_SYSTEM_PROMPT",
    "SKILLS_USER_PROMPT_TEMPLATE",
    "render_centers_section",
    "render_capabilities_section",
    "render_working_memory_section",
    "render_tools_section",
    "render_observations_section",
]
