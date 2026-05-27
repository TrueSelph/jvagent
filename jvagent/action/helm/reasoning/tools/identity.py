"""Identity harness tools for the engine.

Surfaces caller identity to the model so it can address the user by name
when one has been provided. Returns ``"unknown"`` when no name is on file —
the model should ask the user rather than guess.
"""

from __future__ import annotations

from typing import Any, List, Optional

from jvagent.action.helm.reasoning.context import EngineContext
from jvagent.tooling.tool import Tool


async def _load_user_node(ctx: EngineContext) -> Optional[Any]:
    if not ctx.user_id or not ctx.agent:
        return None
    try:
        memory = await ctx.agent.get_memory()
        if not memory:
            return None
        return await memory.get_user(ctx.user_id)
    except Exception:
        return None


def _coerce_name(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return s


def _build_identity_tools(ctx: EngineContext) -> List[Tool]:
    """Return harness tools that surface the caller's identity to the model."""

    async def _get_user_name() -> str:
        user_node = await _load_user_node(ctx)
        if user_node is None:
            return "unknown (no authenticated user)"
        display = _coerce_name(getattr(user_node, "display_name", None))
        name = _coerce_name(getattr(user_node, "name", None))
        chosen = display or name
        if chosen:
            return chosen
        return "unknown (no name on file — ask the user how they would like to be addressed)"

    return [
        Tool(
            name="get_user_name",
            description=(
                "Return the user's preferred name (``display_name`` or ``name`` "
                "from their User record), or ``unknown`` if none is on file. "
                "Use this before greeting the user by name. If the value is "
                "``unknown``, ask the user how they would like to be addressed "
                "and persist the answer with ``memory_update_user_model`` "
                "(key=``name``)."
            ),
            parameters_schema={"type": "object", "properties": {}, "required": []},
            execute=_get_user_name,
        ),
    ]
