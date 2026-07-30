"""VaultToolContext — lightweight adapter that provides the ``ctx`` interface
expected by ``custom_tools.py`` functions when called from ``@tool`` methods
on ``ArtifactHandlerInteractAction`` instead of the interview hook system.

The interview ``HookExecutionContext`` provides ``ctx.visitor``, ``ctx.args``,
``ctx.say()``, ``ctx.tool_response()``, and ``ctx.interview`` (for resolving
actions). This adapter provides the same interface without an interview session,
so the existing ``custom_tools.py`` functions can be called unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class _InterviewShim:
    """Minimal shim that provides ``get_action(name)`` via the agent.

    The vault skill uses ``ctx.interview.get_action("PageIndexAction")`` and
    ``ctx.interview.get_action("AccessControlAction")`` to resolve actions.
    This shim delegates to ``Agent.get()`` on the action's agent node.
    """

    def __init__(self, action: Any) -> None:
        self._action = action

    async def get_action(self, name: str) -> Any:
        try:
            return await self._action.get_action(name)
        except Exception:
            return None


class VaultToolContext:
    """Adapter that mimics ``HookExecutionContext`` for vault tool dispatch.

    Attributes
    ----------
    visitor : InteractWalker
        The current visitor (user/session context).
    args : dict
        LLM-provided tool arguments.
    interview : _InterviewShim
        Shim providing ``get_action()`` for resolving PageIndexAction etc.
    """

    def __init__(
        self,
        visitor: Any,
        args: Dict[str, Any],
        action: Any,
    ) -> None:
        self.visitor = visitor
        self.args = args
        self._action = action
        self._messages: List[Dict[str, str]] = []
        self._directives: List[str] = []
        self._interview_shim: Optional[_InterviewShim] = None

    @property
    def interview(self) -> _InterviewShim:
        if self._interview_shim is None:
            self._interview_shim = _InterviewShim(self._action)
        return self._interview_shim

    def say(self, message: str, **_kwargs: Any) -> None:
        """Record a user-facing directive for inclusion in the tool response."""
        if message and str(message).strip():
            self._messages.append({"message": str(message).strip()})

    def add_directive(self, directive: str) -> None:
        """Record a directive to be applied via visitor.add_directive() after the
        tool returns. Use for user-facing status/readiness announcements that
        should be composed by ReplyAction rather than included in the tool result.
        """
        if directive and str(directive).strip():
            self._directives.append(str(directive).strip())

    def tool_response(
        self, *, ok: Optional[bool] = None, status: str = "ok", **data: Any
    ) -> str:
        """Build the control/return envelope as a JSON string.

        Merges any recorded ``say`` messages into a ``response_directive``
        so they reach the user (mirrors interview ``_finalize_tool_response``).
        """
        if ok is None:
            ok = status not in ("error", "validation_failed")
        payload: Dict[str, Any] = {"ok": ok, "status": status}
        payload.update({k: v for k, v in data.items() if v is not None})

        if self._messages and "response_directive" not in payload:
            parts = [msg["message"] for msg in self._messages if msg["message"]]
            if parts:
                payload["response_directive"] = "\n".join(parts)
            self._messages.clear()

        return json.dumps(payload)
