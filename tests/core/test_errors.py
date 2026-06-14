"""Tests for the exception taxonomy in ``jvagent.core.errors``."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from jvagent.core.errors import (
    ConfigError,
    IntegrationError,
    LogicError,
    TransientError,
    classify_exception,
    is_transient,
)


def test_taxonomy_buckets_classify_themselves() -> None:
    assert classify_exception(TransientError("x")) is TransientError
    assert classify_exception(IntegrationError("x")) is IntegrationError
    assert classify_exception(ConfigError("x")) is ConfigError
    assert classify_exception(LogicError("x")) is LogicError


def test_httpx_timeout_is_transient() -> None:
    exc = httpx.TimeoutException("read timed out", request=None)
    assert classify_exception(exc) is TransientError
    assert is_transient(exc) is True


def test_httpx_status_5xx_is_transient() -> None:
    response = httpx.Response(503, request=httpx.Request("GET", "http://x"))
    exc = httpx.HTTPStatusError("503", request=response.request, response=response)
    assert classify_exception(exc) is TransientError


def test_httpx_status_4xx_is_integration_failure() -> None:
    response = httpx.Response(404, request=httpx.Request("GET", "http://x"))
    exc = httpx.HTTPStatusError("404", request=response.request, response=response)
    assert classify_exception(exc) is IntegrationError


def test_stdlib_connection_error_is_transient() -> None:
    assert classify_exception(ConnectionError("reset")) is TransientError


def test_value_error_is_logic_not_retryable() -> None:
    assert classify_exception(ValueError("boom")) is LogicError
    assert is_transient(ValueError("boom")) is False


def test_unknown_exception_defaults_to_integration() -> None:
    class Weird(Exception):
        pass

    assert classify_exception(Weird()) is IntegrationError


def test_cancelled_error_is_re_raised_not_classified() -> None:
    with pytest.raises(asyncio.CancelledError):
        classify_exception(asyncio.CancelledError())
