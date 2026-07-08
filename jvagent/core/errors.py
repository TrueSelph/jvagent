"""Exception taxonomy for jvagent.

The codebase has hundreds of bare ``except Exception:`` clauses, most of
which collapse transient and permanent failures into the same path. This
module defines the four buckets we actually care about so call sites can
make explicit retry / surface / abort decisions:

- :class:`TransientError` — retryable: timeouts, connection resets, 5xx,
  Redis/DynamoDB temporary unavailability. Caller should back off and retry.
- :class:`IntegrationError` — permanent failure from an external system:
  401/403/404 from a provider, bad webhook URL, missing OAuth token.
  Caller should surface the failure to the user; retry will not help.
- :class:`ConfigError` — operator-fixable: malformed YAML, missing env
  var, invalid ``api_key`` shape. Caller should fail fast at startup
  and never retry.
- :class:`LogicError` — bug in jvagent itself (assertion-style). Caller
  should re-raise and let observability catch it.

Adoption is incremental. New code should raise the taxonomy directly; old
``except Exception:`` blocks get migrated when touched. Use
:func:`classify_exception` to opt into the bucket without changing the
exception's identity (helpful when wrapping third-party calls).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Type, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


class JvAgentError(Exception):
    """Base for all classified jvagent failures."""


class TransientError(JvAgentError):
    """Temporary failure; retryable with backoff."""


class IntegrationError(JvAgentError):
    """Permanent failure from an external system; surface, do not retry."""


class ConfigError(JvAgentError):
    """Operator-fixable configuration mistake; fail fast."""


class LogicError(JvAgentError):
    """Internal invariant violation; re-raise."""


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def classify_exception(exc: BaseException) -> Type[JvAgentError]:
    """Return the taxonomy bucket that best matches *exc*.

    The mapping is conservative — anything we can't confidently classify
    falls into :class:`IntegrationError` so the caller gets a "permanent,
    surface it" signal rather than retrying forever.
    """
    if isinstance(exc, asyncio.CancelledError):
        # Re-raise cancellations; never reclassify.
        raise exc

    # Direct taxonomy matches.
    if isinstance(exc, JvAgentError):
        for bucket in (TransientError, IntegrationError, ConfigError, LogicError):
            if isinstance(exc, bucket):
                return bucket

    # httpx (lazy import — keep this module dependency-light)
    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return TransientError
        if isinstance(exc, httpx.HTTPStatusError):
            status = getattr(exc.response, "status_code", 0)
            if status in {408, 425, 429, 500, 502, 503, 504}:
                return TransientError
            if 400 <= status < 600:
                return IntegrationError
    except ImportError:
        pass

    # Standard library
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return TransientError
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        # These usually indicate caller bugs OR malformed external data.
        # Default to LogicError so they don't get silently retried.
        return LogicError

    return IntegrationError


def is_transient(exc: BaseException) -> bool:
    """``True`` when *exc* should be retried."""
    try:
        return classify_exception(exc) is TransientError
    except asyncio.CancelledError:
        raise


def exception_bucket_name(exc: BaseException) -> str:
    """Return the taxonomy bucket class name for logging (``TransientError``, …)."""
    try:
        return classify_exception(exc).__name__
    except asyncio.CancelledError:
        raise


def log_classified_exception(
    log: logging.Logger,
    exc: BaseException,
    msg: str,
    *args: object,
    level: int = logging.WARNING,
    exc_info: bool = False,
) -> None:
    """Log *exc* with its taxonomy bucket appended to *msg*."""
    bucket = exception_bucket_name(exc)
    log.log(
        level,
        "%s [%s: %s]",
        msg % args if args else msg,
        bucket,
        exc,
        exc_info=exc_info,
    )


async def retry_if_transient(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    initial_delay: float = 0.25,
    backoff: float = 2.0,
    max_delay: float = 8.0,
) -> T:
    """Call async *fn* up to *max_attempts* times when failures are transient.

    Non-transient failures propagate immediately. ``asyncio.CancelledError`` is
    never retried.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    delay = initial_delay
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            last_exc = exc
            if attempt >= max_attempts or not is_transient(exc):
                raise
            logger.debug(
                "retry_if_transient: attempt %d/%d failed (%s); retrying in %.2fs",
                attempt,
                max_attempts,
                exception_bucket_name(exc),
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * backoff, max_delay)
    assert last_exc is not None
    raise last_exc


__all__ = [
    "ConfigError",
    "IntegrationError",
    "JvAgentError",
    "LogicError",
    "TransientError",
    "classify_exception",
    "exception_bucket_name",
    "is_transient",
    "log_classified_exception",
    "retry_if_transient",
]
