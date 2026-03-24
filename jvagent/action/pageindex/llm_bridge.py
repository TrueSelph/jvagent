"""LLM bridge for PageIndex: delegates to jvagent LanguageModelAction when available.

When a model action is set in context, LLM calls use it for observability and
token tracking. Otherwise falls back to core.utils direct OpenAI calls.

Cooperative cancellation: PDF ingestion runs in a thread pool; asyncio timeout
cannot stop the thread. A shared threading.Event is attached per worker thread
so LLM entry points can abort after timeout.
"""

import asyncio
import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

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


async def ChatGPT_API_async(
    model: str,
    prompt: str,
    api_key: Optional[str] = None,
    _real_impl=None,
) -> str:
    """Async LLM call: uses jvagent model when in context, else real utils."""
    check_pageindex_cancelled()
    action = get_pageindex_model_action()
    if action:
        try:
            result = await action.query_sync(prompt, model=model)
            return await result.get_response() if result else "Error"
        except PageIndexCancelled:
            raise
        except Exception as e:
            logger.warning(
                f"PageIndex jvagent LLM call failed, falling back to direct: {e}"
            )
            if _real_impl:
                return await _real_impl(model, prompt, api_key)
            return "Error"
    if _real_impl:
        return await _real_impl(model, prompt, api_key)
    return "Error"


def ChatGPT_API(
    model: str,
    prompt: str,
    api_key: Optional[str] = None,
    chat_history: Optional[list] = None,
    _real_impl=None,
) -> str:
    """Sync LLM call: uses jvagent model when in context, else real utils."""
    check_pageindex_cancelled()
    action = get_pageindex_model_action()
    if action:
        try:
            result = _run_async_from_sync(action.query_sync(prompt, model=model))
            return _run_async_from_sync(result.get_response()) if result else "Error"
        except PageIndexCancelled:
            raise
        except Exception as e:
            logger.warning(
                f"PageIndex jvagent LLM call failed, falling back to direct: {e}"
            )
            if _real_impl:
                return _real_impl(model, prompt, api_key, chat_history)
            return "Error"
    if _real_impl:
        return _real_impl(model, prompt, api_key, chat_history)
    return "Error"


def ChatGPT_API_with_finish_reason(
    model: str,
    prompt: str,
    api_key: Optional[str] = None,
    chat_history: Optional[list] = None,
    _real_impl=None,
) -> tuple:
    """Sync LLM call with finish reason: uses jvagent model when in context."""
    check_pageindex_cancelled()
    action = get_pageindex_model_action()
    if action:
        try:
            result = _run_async_from_sync(action.query_sync(prompt, model=model))
            if not result:
                return "Error", "error"
            text = _run_async_from_sync(result.get_response())
            reason = getattr(result, "finish_reason", None) or "stop"
            return text, "finished" if reason == "stop" else "max_output_reached"
        except PageIndexCancelled:
            raise
        except Exception as e:
            logger.warning(
                f"PageIndex jvagent LLM call failed, falling back to direct: {e}"
            )
            if _real_impl:
                return _real_impl(model, prompt, api_key, chat_history)
            return "Error", "error"
    if _real_impl:
        return _real_impl(model, prompt, api_key, chat_history)
    return "Error", "error"
