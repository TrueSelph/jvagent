"""Memory harness tools for the engine.

Two layers:

1. **Legacy reads** — ``memory_get_history``, ``memory_get_user_info``,
   ``memory_set_preference``. Stable surface for conversation history and
   per-conversation preferences.

2. **General-purpose memory (Phase B)** — flat key→markdown store with two
   scopes: ``user`` (cross-session) and ``conversation`` (session-scoped).
   New tools: ``memory_set``, ``memory_get``, ``memory_append``,
   ``memory_search``, ``memory_list``, ``memory_delete``. Markdown values are
   directly ingestible into prompts; tags enable filtered retrieval.

The legacy ``memory_update_user_model`` is soft-deprecated: it still works
and persists to ``user_model`` for back-compat, but also writes into the new
``memory`` map under a synthesised key. New code should use ``memory_set``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.helm.reasoning.context import EngineContext
from jvagent.tooling.tool import Tool

logger = logging.getLogger(__name__)


SCOPE_USER = "user"
SCOPE_CONVERSATION = "conversation"
SCOPE_AUTO = "auto"

_PREVIEW_CHARS = 200
_SCOPE_ENUM = [SCOPE_AUTO, SCOPE_USER, SCOPE_CONVERSATION]


# ----------------------------------------------------------------------
# Scope helpers — resolve memory dicts on the right node
# ----------------------------------------------------------------------


async def _user_node(ctx: EngineContext) -> Optional[Any]:
    if not ctx.user_id or not ctx.agent:
        return None
    try:
        memory = await ctx.agent.get_memory()
        if not memory:
            return None
        return await memory.get_user(ctx.user_id)
    except Exception as exc:
        logger.debug("memory_tools: user node resolve failed: %s", exc)
        return None


def _ensure_dict(node: Any, attr: str) -> Optional[Dict[str, Any]]:
    if node is None:
        return None
    cur = getattr(node, attr, None)
    if not isinstance(cur, dict):
        cur = {}
        try:
            setattr(node, attr, cur)
        except Exception:
            return None
    return cur


async def _scope_view(
    ctx: EngineContext, scope: str
) -> List[Tuple[str, Any, Dict[str, str], Dict[str, List[str]]]]:
    """Return scoped memory views: [(scope_label, node, memory, memory_tags), ...].

    Order is "user first, then conversation" — used for ``auto`` resolution
    where user-scoped wins on key collision.
    """
    out: List[Tuple[str, Any, Dict[str, str], Dict[str, List[str]]]] = []
    s = (scope or SCOPE_AUTO).strip().lower()
    if s not in _SCOPE_ENUM:
        return out
    if s in (SCOPE_USER, SCOPE_AUTO):
        u = await _user_node(ctx)
        if u is not None:
            mem = _ensure_dict(u, "memory")
            tags = _ensure_dict(u, "memory_tags")
            if mem is not None and tags is not None:
                out.append((SCOPE_USER, u, mem, tags))
    if s in (SCOPE_CONVERSATION, SCOPE_AUTO):
        c = ctx.conversation
        if c is not None:
            mem = _ensure_dict(c, "memory")
            tags = _ensure_dict(c, "memory_tags")
            if mem is not None and tags is not None:
                out.append((SCOPE_CONVERSATION, c, mem, tags))
    return out


def _normalize_tags(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def _ensure_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        import json as _json

        return _json.dumps(value, indent=2, default=str)
    except Exception:
        return str(value)


def _summary(scope_label: str, key: str, body: str, tags: List[str]) -> str:
    preview = body if len(body) <= _PREVIEW_CHARS else body[:_PREVIEW_CHARS] + "..."
    tag_suffix = f" [tags: {', '.join(tags)}]" if tags else ""
    return f"- ({scope_label}) {key}{tag_suffix}: {preview}"


# ----------------------------------------------------------------------
# Tool factory
# ----------------------------------------------------------------------


def _build_memory_tools(ctx: EngineContext) -> List[Tool]:
    """Return harness tools that expose the memory subsystem to the engine model."""

    # ------------------------------------------------------------------
    # Legacy reads (kept verbatim for stability)
    # ------------------------------------------------------------------

    async def _get_history(limit: int = 5, include_responses: bool = True) -> str:
        if not ctx.conversation:
            return "No conversation available."
        try:
            # formatted=True returns {role, content} pairs we can read directly.
            history = await ctx.conversation.get_interaction_history(
                limit=limit,
                with_utterance=True,
                with_response=include_responses,
                with_interpretation=False,
                with_event=False,
                formatted=True,
                max_statement_length=ctx.config.max_statement_length,
            )
        except Exception as exc:
            return f"Error retrieving history: {exc}"
        if not history:
            return "No prior interactions in this conversation."
        lines = []
        for entry in history:
            role = entry.get("role", "")
            content = entry.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            if content:
                lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    async def _get_user_info() -> str:
        if not ctx.user_id or not ctx.agent:
            return "No authenticated user."
        try:
            memory = await ctx.agent.get_memory()
            if not memory:
                return "Memory subsystem unavailable."
            user_node = await memory.get_user(ctx.user_id)
            if not user_node:
                return f"User '{ctx.user_id}' not found."
            name = getattr(user_node, "name", "Unknown")
            usage = getattr(user_node, "usage", {}) or {}
            interaction_count = getattr(user_node, "interaction_count", 0)
            return (
                f"User: {name} (id: {ctx.user_id})\n"
                f"Interactions: {interaction_count}\n"
                f"Usage: {usage}"
            )
        except Exception as exc:
            return f"Error: {exc}"

    async def _set_preference(key: str, value: str) -> str:
        """Store or update a user preference for this conversation."""
        if not ctx.conversation:
            return "Error: no conversation available."
        try:
            context = getattr(ctx.conversation, "context", None)
            if context is None:
                return "Error: conversation has no context."
            prefs = context.get("preferences", {})
            prefs[key] = value
            context["preferences"] = prefs
            await ctx.conversation.save()
            return f"Preference set: {key} = {value}"
        except Exception as exc:
            return f"Error setting preference: {exc}"

    # ------------------------------------------------------------------
    # General-purpose memory (Phase B)
    # ------------------------------------------------------------------

    async def _set(
        key: str = "",
        content: Optional[str] = None,
        scope: str = SCOPE_USER,
        tags: Any = None,
        # ``value`` is accepted as an alias for ``content`` because models
        # frequently mirror ``memory_set_preference``'s parameter name when
        # writing to ``memory_set``. Either is accepted; ``content`` wins
        # when both are provided.
        value: Optional[str] = None,
        **_extra: Any,
    ) -> str:
        if not key or not str(key).strip():
            return "Error: 'key' is required."
        body_source = content if content is not None else value
        if body_source is None:
            return "Error: 'content' (or 'value') is required."
        s = (scope or SCOPE_USER).strip().lower()
        if s not in (SCOPE_USER, SCOPE_CONVERSATION):
            return f"Error: scope must be 'user' or 'conversation'; got '{scope}'."
        views = await _scope_view(ctx, s)
        if not views:
            return f"Error: scope '{s}' is unavailable in this run."
        scope_label, node, mem, tag_map = views[0]
        body = _ensure_str(body_source)
        mem[str(key).strip()] = body
        tag_list = _normalize_tags(tags)
        if tag_list:
            tag_map[str(key).strip()] = tag_list
        else:
            tag_map.pop(str(key).strip(), None)
        try:
            await node.save()
        except Exception as exc:
            return f"Error saving memory: {exc}"
        return f"Memory set ({scope_label}): {key} ({len(body)} chars)."

    async def _get(key: str, scope: str = SCOPE_AUTO) -> str:
        if not key or not str(key).strip():
            return "Error: 'key' is required."
        target = str(key).strip()
        for scope_label, _node, mem, _tag_map in await _scope_view(ctx, scope):
            if target in mem:
                return mem[target]
        # Back-compat read-through into legacy user_model
        if scope in (SCOPE_AUTO, SCOPE_USER):
            u = await _user_node(ctx)
            legacy = getattr(u, "user_model", None) if u else None
            if isinstance(legacy, dict):
                facts = legacy.get("facts") or []
                prefs = legacy.get("preferences") or {}
                if isinstance(prefs, dict) and target in prefs:
                    return f"(legacy preference) {prefs[target]}"
                if isinstance(facts, list):
                    for fact in facts:
                        if isinstance(fact, str) and fact.startswith(f"{target}:"):
                            return f"(legacy fact) {fact}"
        return f"Memory '{key}' not found in scope '{scope}'."

    async def _append(
        key: str = "",
        content: Optional[str] = None,
        scope: str = SCOPE_USER,
        separator: str = "\n\n",
        # ``value`` accepted as alias for ``content`` (see ``_set`` for rationale).
        value: Optional[str] = None,
        **_extra: Any,
    ) -> str:
        if not key or not str(key).strip():
            return "Error: 'key' is required."
        addition_source = content if content is not None else value
        if addition_source is None:
            return "Error: 'content' (or 'value') is required."
        s = (scope or SCOPE_USER).strip().lower()
        if s not in (SCOPE_USER, SCOPE_CONVERSATION):
            return f"Error: scope must be 'user' or 'conversation'; got '{scope}'."
        views = await _scope_view(ctx, s)
        if not views:
            return f"Error: scope '{s}' is unavailable."
        scope_label, node, mem, _tags = views[0]
        target = str(key).strip()
        existing = mem.get(target, "")
        addition = _ensure_str(addition_source)
        new_body = (existing + separator + addition) if existing else addition
        mem[target] = new_body
        try:
            await node.save()
        except Exception as exc:
            return f"Error saving memory: {exc}"
        return f"Memory appended ({scope_label}): {key} (now {len(new_body)} chars)."

    async def _delete(key: str, scope: str) -> str:
        if not key or not str(key).strip():
            return "Error: 'key' is required."
        s = (scope or "").strip().lower()
        if s not in (SCOPE_USER, SCOPE_CONVERSATION):
            return f"Error: scope must be 'user' or 'conversation'; got '{scope}'."
        views = await _scope_view(ctx, s)
        if not views:
            return f"Error: scope '{s}' is unavailable."
        scope_label, node, mem, tag_map = views[0]
        target = str(key).strip()
        if target not in mem:
            return f"Memory '{key}' not found in scope '{scope_label}'."
        del mem[target]
        tag_map.pop(target, None)
        try:
            await node.save()
        except Exception as exc:
            return f"Error saving after delete: {exc}"
        return f"Memory deleted ({scope_label}): {key}."

    async def _list(scope: str = SCOPE_AUTO) -> str:
        lines: List[str] = []
        any_found = False
        for scope_label, _node, mem, tag_map in await _scope_view(ctx, scope):
            if not mem:
                continue
            any_found = True
            for k in sorted(mem.keys()):
                lines.append(_summary(scope_label, k, mem[k], tag_map.get(k, [])))
        if not any_found:
            return f"No memory entries in scope '{scope}'."
        return "\n".join(lines)

    async def _search(
        query: str = "",
        tag: str = "",
        scope: str = SCOPE_AUTO,
        limit: int = 10,
    ) -> str:
        q = (query or "").strip().lower()
        t = (tag or "").strip().lower()
        matches: List[Tuple[float, str, str, str, List[str]]] = []
        seen_keys: Dict[str, str] = (
            {}
        )  # key → scope_label (user-scoped wins on collision)
        for scope_label, _node, mem, tag_map in await _scope_view(ctx, scope):
            for k, body in mem.items():
                if k in seen_keys:
                    continue  # earlier scope (user) takes precedence
                seen_keys[k] = scope_label
                tags = [str(x).lower() for x in (tag_map.get(k) or [])]
                score = 0.0
                body_lower = body.lower()
                if q:
                    if q in k.lower():
                        score += 3
                    if q in body_lower:
                        score += 1
                    if any(q in tg for tg in tags):
                        score += 2
                if t:
                    if t in tags:
                        score += 5
                    else:
                        continue  # tag filter is exclusive
                if not q and not t:
                    score = 1
                if score > 0:
                    matches.append((score, scope_label, k, body, tag_map.get(k) or []))

        if not matches:
            filt = []
            if q:
                filt.append(f"query='{query}'")
            if t:
                filt.append(f"tag='{tag}'")
            if scope != SCOPE_AUTO:
                filt.append(f"scope='{scope}'")
            return f"No memory matches ({', '.join(filt) or 'no filter'})."

        matches.sort(key=lambda x: x[0], reverse=True)
        matches = matches[: max(1, int(limit))]
        out = [
            f"Found {len(matches)} memory entr{'y' if len(matches) == 1 else 'ies'}:"
        ]
        for _, scope_label, k, body, tags in matches:
            out.append(_summary(scope_label, k, body, tags))
        return "\n".join(out)

    # ------------------------------------------------------------------
    # Soft-deprecated legacy writer — routes to memory_set(scope=user)
    # ------------------------------------------------------------------

    async def _update_user_model(key: str, value: str) -> str:
        """[Deprecated] Store a fact/preference. Routes to ``memory_set`` (scope=user).

        Existing callers continue to work; new code should call ``memory_set``
        directly with a clear scope.
        """
        return await _set(key=key, content=value, scope=SCOPE_USER, tags=["legacy"])

    return [
        # ── Legacy stable reads ─────────────────────────────────────────
        Tool(
            name="memory_get_history",
            description=(
                "Retrieve recent interactions from this conversation. "
                "Use limit to control how many past exchanges to fetch."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of past interactions to retrieve (default 5).",
                        "default": 5,
                    },
                    "include_responses": {
                        "type": "boolean",
                        "description": "Whether to include assistant responses (default true).",
                        "default": True,
                    },
                },
            },
            execute=_get_history,
        ),
        Tool(
            name="memory_get_user_info",
            description=(
                "Get information about the current user, including name, "
                "interaction count, and usage statistics."
            ),
            parameters_schema={"type": "object", "properties": {}},
            execute=_get_user_info,
        ),
        Tool(
            name="memory_set_preference",
            description=(
                "Store or update a user preference for the current conversation. "
                "Preferences persist across interactions within this conversation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Preference key (e.g., 'language', 'tone', 'format').",
                    },
                    "value": {
                        "type": "string",
                        "description": "Preference value.",
                    },
                },
                "required": ["key", "value"],
            },
            execute=_set_preference,
        ),
        # ── Phase B: general-purpose memory ─────────────────────────────
        Tool(
            name="memory_set",
            description=(
                "Create or overwrite a memory entry under a key. The body is "
                "markdown — write natural prose. Choose scope deliberately: "
                "'user' for stable facts about the human (cross-session); "
                "'conversation' for working notes that persist only within "
                "this session. Pass the body as ``content`` (canonical) — "
                "``value`` is also accepted as an alias for compatibility "
                "with memory_set_preference."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Stable identifier for this entry (e.g. 'name', 'profile', 'project_alpha_notes').",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown body for this memory entry. (Canonical name; ``value`` is accepted as alias.)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": [SCOPE_USER, SCOPE_CONVERSATION],
                        "description": "'user' = cross-session; 'conversation' = session-scoped.",
                        "default": SCOPE_USER,
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags.",
                    },
                },
                "required": ["key", "content"],
            },
            execute=_set,
        ),
        Tool(
            name="memory_get",
            description=(
                "Retrieve a memory entry's full markdown body. Default scope "
                "'auto' searches user-scope first, then conversation-scope; "
                "user wins on key collision."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": _SCOPE_ENUM,
                        "default": SCOPE_AUTO,
                    },
                },
                "required": ["key"],
            },
            execute=_get,
        ),
        Tool(
            name="memory_append",
            description=(
                "Append text to an existing memory entry (or create one if it "
                "doesn't exist). Useful for journals, evolving notes, or "
                "incremental fact accumulation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "content": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": [SCOPE_USER, SCOPE_CONVERSATION],
                        "default": SCOPE_USER,
                    },
                    "separator": {
                        "type": "string",
                        "description": "Text inserted between existing body and new content (default double newline).",
                        "default": "\n\n",
                    },
                },
                "required": ["key", "content"],
            },
            execute=_append,
        ),
        Tool(
            name="memory_search",
            description=(
                "Search memory entries by keyword and/or tag across the chosen "
                "scope. Returns ranked summaries (scope, key, tags, preview). "
                "Omit filters to list everything."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword to match against keys, body, and tags.",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Exact tag to filter by (exclusive).",
                    },
                    "scope": {
                        "type": "string",
                        "enum": _SCOPE_ENUM,
                        "default": SCOPE_AUTO,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10).",
                        "default": 10,
                    },
                },
            },
            execute=_search,
        ),
        Tool(
            name="memory_list",
            description="List all memory keys with brief previews in the chosen scope.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": _SCOPE_ENUM,
                        "default": SCOPE_AUTO,
                    },
                },
            },
            execute=_list,
        ),
        Tool(
            name="memory_delete",
            description="Remove a memory entry by key from a specific scope.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": [SCOPE_USER, SCOPE_CONVERSATION],
                    },
                },
                "required": ["key", "scope"],
            },
            execute=_delete,
        ),
        # ── Soft-deprecated legacy writer (still callable for back-compat) ──
        Tool(
            name="memory_update_user_model",
            description=(
                "[Deprecated alias for memory_set scope=user] Store a fact or "
                "preference about the user. Prefer memory_set directly."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
            execute=_update_user_model,
        ),
    ]


# ----------------------------------------------------------------------
# System-prompt pre-load helper
# ----------------------------------------------------------------------


async def render_user_memory_block(ctx: EngineContext, max_chars: int = 4096) -> str:
    """Render the user's memory dict as a compact markdown block for the system prompt.

    Returns an empty string if there's nothing to surface or if pre-load is
    disabled. The block is capped at ``max_chars`` and includes a soft prefix
    so the model knows how to use it.
    """
    user = await _user_node(ctx)
    if user is None:
        return ""
    mem = getattr(user, "memory", None)
    if not isinstance(mem, dict) or not mem:
        return ""

    parts: List[str] = ["# What I remember about you"]
    used = len(parts[0]) + 1
    for key in sorted(mem.keys()):
        body = str(mem[key] or "").strip()
        if not body:
            continue
        section = f"\n## {key}\n{body}"
        if used + len(section) > max_chars:
            parts.append(
                f"\n_(memory truncated to {max_chars} chars; "
                f"use memory_search for full text)_"
            )
            break
        parts.append(section)
        used += len(section)
    if len(parts) == 1:
        return ""
    return "".join(parts)
