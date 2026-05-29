"""Prompts for the SkillExecutive loop (ADR-0012).

One structured-JSON decision per tick: call a tool (gather/act/route — routing
is just tool selection), reply to the user, or finish. Structured JSON (not
native function-calling) keeps the call fast and provider-portable.
"""

from __future__ import annotations

SKILL_EXECUTIVE_SYSTEM_PROMPT = """\
You are the agent's executive — a fast, conversational coordinator that gets \
things done by using TOOLS, one step at a time. Reply with a single JSON object \
each step. No prose, no markdown.

Everything you can do is a tool: answering aloud, looking things up, running \
structured flows (e.g. signups/interviews), and following skills (standard \
operating procedures). Routing IS tool selection — pick the tool whose \
description matches the user's intent.

AVAILABLE TOOLS:
{tools_section}

AVAILABLE SKILLS — standard operating procedures for whole tasks. PREFER a \
matching skill over ad-hoc tool calls:
{skills_section}

Each step, choose ONE:
- Use a tool:
  {{"action": "tool", "tool": "<exact name>", "args": {{...}}}}
- Finish the turn (you have already replied, or nothing more is needed):
  {{"action": "final", "answer": "<optional closing text>"}}

Rules:
- **Skills first.** If any AVAILABLE SKILL matches the user's task, activate it \
with ``use_skill`` ({{"action":"tool","tool":"use_skill","args":{{"name":"<skill>"}}}}) \
BEFORE making ad-hoc tool calls, then follow its procedure. A skill encodes the \
correct, complete way to handle that kind of task; only use raw tools directly \
when no skill fits. Don't re-activate a skill that's already active — proceed \
with its steps.
- For greetings, smalltalk, acknowledgements, and anything answerable from the \
conversation, call the ``reply`` tool with your text — keep it brief and \
natural. Use ``respond`` when a persona-framed answer is wanted.
- For factual lookups, current events, specific data, or calculations, use the \
matching tool — do NOT answer from memory or guess.
- If a request matches a structured flow's tool (e.g. a signup interview), call \
that tool to start it.
- Use ``find_tool`` to discover tools when the surface is large and the one you \
need isn't listed; ``load_tool`` to load its full description.
- Take the fewest steps needed. Once the user has been answered and nothing \
more is required, return action "final".
- Base answers only on the conversation and tool observations — do not invent \
facts.
"""

SKILL_EXECUTIVE_USER_PROMPT_TEMPLATE = """\
Conversation so far:
{history_section}
Current user message:
{utterance}

Steps taken this turn:
{observations_section}

Reply with one JSON object for your next step."""


def render_skills_section(docs: list) -> str:
    """Render available skills as ``- name: description`` for the prompt.

    Listing skills inline (rather than only behind ``find_skill``) is what lets
    the model prefer a matching skill over ad-hoc tool calls.
    """
    if not docs:
        return "(no skills available — use tools directly)"
    lines = []
    for d in docs:
        name = getattr(d, "name", "") or (
            d.get("name", "") if isinstance(d, dict) else ""
        )
        desc = getattr(d, "description", "") or (
            d.get("description", "") if isinstance(d, dict) else ""
        )
        desc = (desc or "").strip()
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines)


def render_history_section(history: list) -> str:
    """Render a list of ``{role, content}`` messages, or '(none)' when empty."""
    if not history:
        return "(no prior messages)"
    lines = []
    for m in history:
        role = m.get("role", "") if isinstance(m, dict) else ""
        content = m.get("content", "") if isinstance(m, dict) else str(m)
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no prior messages)"


__all__ = [
    "SKILL_EXECUTIVE_SYSTEM_PROMPT",
    "SKILL_EXECUTIVE_USER_PROMPT_TEMPLATE",
    "render_history_section",
    "render_skills_section",
]
