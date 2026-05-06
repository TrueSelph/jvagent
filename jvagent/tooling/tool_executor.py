import asyncio
import contextvars
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_observability import ToolExecutionEnvelope
from jvagent.tooling.tool_registry import ToolRegistry
from jvagent.tooling.tool_result import ToolResult

logger = logging.getLogger(__name__)


# Per-task slot holding the visitor for the currently-dispatched tool call.
# Tool closures that need caller identity (notably MCP filesystem dispatch
# which routes to per-user subprocesses) read this via
# ``get_dispatch_visitor()`` instead of accepting a new kwarg, so the
# ``Tool`` API stays minimal and existing tools are unaffected. Set by
# ``ToolExecutionEngine.dispatch`` for the duration of each call and reset
# on completion.
_dispatch_visitor_var: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "jvagent_tool_dispatch_visitor", default=None
)


def get_dispatch_visitor() -> Optional[Any]:
    """Return the visitor (e.g. InteractWalker) of the currently-dispatched tool call.

    Returns ``None`` outside of a tool dispatch or when the engine was
    constructed without a visitor (e.g. raw scripted tool runs).
    """
    return _dispatch_visitor_var.get()


def _input_fingerprint(arguments: str) -> str:
    import hashlib

    return hashlib.blake2b((arguments or "").encode(), digest_size=4).hexdigest()


class ToolExecutionEngine:
    """Dispatches tool-call dicts to registered ``Tool`` instances concurrently.

    Designed to be the single execution engine used by ``CockpitEngine``.
    Receives raw tool-call dicts from ``ModelActionResult.tool_calls``, looks
    up the matching ``Tool`` in the registry, calls ``Tool.call(**args)``, and
    collects the results with observability envelopes.

    Args:
        registry: The ``ToolRegistry`` holding all available tools.
        call_timeout: Seconds before an individual tool call is cancelled.
        max_concurrent: Maximum in-flight tool calls at once.
        sanitize_errors: Replace detailed errors with generic messages.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        call_timeout: float = 60.0,
        max_concurrent: int = 5,
        sanitize_errors: bool = True,
        visitor: Optional[Any] = None,
    ) -> None:
        self._registry = registry
        self.call_timeout = call_timeout
        self.max_concurrent = max_concurrent
        self.sanitize_errors = sanitize_errors
        self.envelopes: List[ToolExecutionEnvelope] = []
        # Visitor (e.g. InteractWalker) shared across all tool calls in this
        # engine instance; surfaced to tool closures via ``get_dispatch_visitor``
        # so per-user routing (MCP filesystem subprocess, etc.) works without
        # changing the Tool API.
        self._visitor = visitor

    async def dispatch(
        self,
        tool_calls: List[Dict[str, Any]],
    ) -> List[ToolResult]:
        """Execute a batch of tool calls concurrently (bounded).

        Returns:
            List of ``ToolResult`` in the same order as **tool_calls**.
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _one(call: Dict[str, Any]) -> ToolResult:
            async with semaphore:
                return await self._dispatch_one(call)

        # Bind the visitor for the duration of this batch via the
        # per-task ContextVar. Concurrent ``_one`` coroutines spawned by
        # ``asyncio.gather`` inherit the parent task's context at creation
        # time, so each tool closure sees the same visitor without needing
        # explicit propagation.
        token = _dispatch_visitor_var.set(self._visitor)
        try:
            return list(await asyncio.gather(*(_one(c) for c in tool_calls)))
        finally:
            _dispatch_visitor_var.reset(token)

    async def _dispatch_one(self, call: Dict[str, Any]) -> ToolResult:
        fn = call.get("function", {})
        tool_name = fn.get("name", "")
        tool_call_id = call.get("id", "")
        args_raw = fn.get("arguments") or {}
        if isinstance(args_raw, str):
            import json

            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {}
        else:
            args = args_raw

        envelope = ToolExecutionEnvelope(
            attempt_id=uuid.uuid4().hex[:8],
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            input_fingerprint=_input_fingerprint(
                json.dumps(args) if isinstance(args, dict) else str(args)
            ),
            start_ts=time.monotonic(),
        )
        self.envelopes.append(envelope)

        tool = self._registry.get(tool_name)
        if tool is None:
            available = self._registry.names()
            result = ToolResult.error(
                f"Tool '{tool_name}' is not available. Available tools: {available}",
                tool_call_id=tool_call_id,
            )
            envelope.close(content=result.content, is_error=True)
            return result

        try:
            raw = await asyncio.wait_for(tool.call(**args), timeout=self.call_timeout)
            if isinstance(raw, ToolResult):
                result = raw
            elif isinstance(raw, str):
                result = ToolResult(content=raw)
            else:
                import json

                result = ToolResult(content=json.dumps(raw))
        except asyncio.TimeoutError:
            msg = f"Tool call '{tool_name}' timed out after {self.call_timeout}s"
            result = ToolResult.error(msg, tool_call_id=tool_call_id)
            envelope.close(content=msg, is_error=True)
            return result
        except Exception as exc:
            # When sanitize_errors is on, treat the full exception (including
            # provider response bodies, headers, partial credentials) as
            # untrusted and never write it to the operator log. The envelope
            # still captures the raw exception for observability hooks that
            # opt in.
            if self.sanitize_errors:
                logger.warning(
                    "Tool dispatch '%s' failed: %s",
                    tool_name,
                    type(exc).__name__,
                )
                msg = f"Tool execution failed: {tool_name}"
            else:
                logger.warning(
                    "Tool dispatch '%s' failed: %s",
                    tool_name,
                    exc,
                    exc_info=True,
                )
                msg = str(exc)
            result = ToolResult.error(msg, tool_call_id=tool_call_id)
            envelope.close(content=str(exc), is_error=True, exc=exc)
            return result

        if not result.content.strip():
            result = ToolResult.empty(tool_name, tool_call_id=tool_call_id)

        result.metadata["tool_call_id"] = tool_call_id
        envelope.close(content=result.content, is_error=result.is_error)
        return result

    def success_rate(self) -> Optional[float]:
        if not self.envelopes:
            return None
        return sum(1 for e in self.envelopes if not e.is_error) / len(self.envelopes)

    def total_latency_ms(self) -> int:
        return sum(e.latency_ms for e in self.envelopes)

    def reset(self) -> None:
        self.envelopes.clear()
