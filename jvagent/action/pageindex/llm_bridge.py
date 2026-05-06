"""LLM bridge for PageIndex: delegates to jvagent LanguageModelAction when available.

When a model action is set in context, LLM calls use it for observability and
token tracking. Otherwise falls back to core.utils litellm entry points.

Cooperative cancellation: PDF ingestion runs in a thread pool; asyncio timeout
cannot stop the thread. A shared threading.Event is attached per worker thread
so LLM entry points can abort after timeout.
"""

import asyncio
import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

_tls = threading.local()


class PageIndexCancelled(Exception):
    """Raised when PDF ingestion is cooperatively cancelled (e.g. document timeout)."""


def attach_pageindex_cancel_event(event: Optional[threading.Event]) -> None:
    """Register cancel event for the current thread (PDF executor worker). Pass None to clear."""
    if event is None:
        if hasattr(_tls, "cancel_event"):
            delattr(_tls, "cancel_event")
    else:
        _tls.cancel_event = event


def check_pageindex_cancelled() -> None:
    """Raise PageIndexCancelled if the current thread's cancel event is set."""
    ev = getattr(_tls, "cancel_event", None)
    if ev is not None and ev.is_set():
        raise PageIndexCancelled()


def signal_pageindex_cancel(event: Optional[threading.Event]) -> None:
    """Request cooperative cancellation (safe if event is None)."""
    if event is not None:
        event.set()


_pageindex_model_action: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "_pageindex_model_action", default=None
)

_executor = ThreadPoolExecutor(max_workers=4)


def set_pageindex_model_action(action: Optional[Any]) -> None:
    """Set the LanguageModelAction to use for PageIndex LLM calls."""
    _pageindex_model_action.set(action)


def get_pageindex_model_action() -> Optional[Any]:
    """Get the current LanguageModelAction for PageIndex LLM calls."""
    return _pageindex_model_action.get()


def _run_async_from_sync(coro) -> Any:
    """Run an async coroutine from a sync context (handles already-running loop)."""
    try:
        asyncio.get_running_loop()
        future = _executor.submit(asyncio.run, coro)
        return future.result()
    except RuntimeError:
        return asyncio.run(coro)


async def llm_acompletion(
    model: str,
    prompt: str,
    _real_impl=None,
) -> str:
    """Async litellm-style call: jvagent model when in context, else core utils."""
    check_pageindex_cancelled()
    action = get_pageindex_model_action()
    if action:
        try:
            # PageIndex algorithm relies on deterministic single-shot output
            # (TOC detection, JSON tree-search node selection, fuzzy title
            # match). Pin temperature=0 the same way upstream litellm path
            # does; the model action's own default is typically 0.7.
            result = await action.query_sync(prompt, temperature=0)
            return await result.get_response() if result else ""
        except PageIndexCancelled:
            raise
        except Exception as e:
            logger.warning(
                f"PageIndex jvagent LLM call failed, falling back to direct: {e}"
            )
            if _real_impl:
                return await _real_impl(model, prompt)
            return ""
    if _real_impl:
        return await _real_impl(model, prompt)
    return ""


def llm_completion(
    model: str,
    prompt: str,
    chat_history: Optional[list] = None,
    return_finish_reason: bool = False,
    _real_impl=None,
) -> Union[str, tuple]:
    """Sync litellm-style call: jvagent model when in context, else core utils."""
    check_pageindex_cancelled()
    if chat_history:
        if _real_impl:
            return _real_impl(model, prompt, chat_history, return_finish_reason)
        return ("", "error") if return_finish_reason else ""

    action = get_pageindex_model_action()
    if action:
        try:
            # See note in llm_acompletion: pin temperature=0 to preserve
            # PageIndex algorithm determinism when bridging to a
            # LanguageModelAction whose default temperature is non-zero.
            result = _run_async_from_sync(action.query_sync(prompt, temperature=0))
            if return_finish_reason:
                if not result:
                    return "", "error"
                text = _run_async_from_sync(result.get_response())
                reason = getattr(result, "finish_reason", None) or "stop"
                finish = "finished" if reason == "stop" else "max_output_reached"
                return text, finish
            if not result:
                return ""
            return _run_async_from_sync(result.get_response())
        except PageIndexCancelled:
            raise
        except Exception as e:
            logger.warning(
                f"PageIndex jvagent LLM call failed, falling back to direct: {e}"
            )
            if _real_impl:
                return _real_impl(model, prompt, chat_history, return_finish_reason)
            return ("", "error") if return_finish_reason else ""
    if _real_impl:
        return _real_impl(model, prompt, chat_history, return_finish_reason)
    return ("", "error") if return_finish_reason else ""
