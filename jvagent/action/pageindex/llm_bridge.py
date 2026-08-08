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
from typing import Any, Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

_tls = threading.local()


class PageIndexCancelled(Exception):
    """Raised when PDF ingestion is cooperatively cancelled (e.g. document timeout)."""


def attach_pageindex_cancel_event(event: Optional[threading.Event]) -> None:
    """Register cancel event for the current thread (PDF executor worker). Pass None to clear."""
    if event is None:
        _tls.cancel_event = None
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
    text, _ = await llm_acompletion_with_usage(model, prompt, _real_impl=_real_impl)
    return text


async def llm_acompletion_with_usage(
    model: str,
    prompt: str,
    _real_impl=None,
) -> Tuple[str, Dict[str, int]]:
    """Async litellm-style call returning (text, usage_dict).

    usage_dict contains prompt_tokens, completion_tokens, total_tokens (0 if unknown).
    """
    usage: Dict[str, int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    check_pageindex_cancelled()
    action = get_pageindex_model_action()
    if action:
        try:
            result = await action.query_sync(prompt, temperature=0)
            text = await result.get_response() if result else ""
            if (
                result
                and hasattr(result, "metrics")
                and isinstance(result.metrics, dict)
            ):
                usage["prompt_tokens"] = result.metrics.get("prompt_tokens", 0) or 0
                usage["completion_tokens"] = (
                    result.metrics.get("completion_tokens", 0) or 0
                )
                usage["total_tokens"] = result.metrics.get("total_tokens", 0) or 0
            return text, usage
        except PageIndexCancelled:
            raise
        except Exception as e:
            logger.warning(
                f"PageIndex jvagent LLM call failed, falling back to direct: {e}"
            )
            if _real_impl:
                text = await _real_impl(model, prompt)
                return text, usage
            return "", usage
    if _real_impl:
        text = await _real_impl(model, prompt)
        return text, usage
    return "", usage


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
