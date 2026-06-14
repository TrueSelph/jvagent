"""Tests for jvagent's Logger compatibility shim.

Asserts that ``logger.error(..., details={...})`` does not raise TypeError
once :func:`jvagent._logging_compat.install` has been called, and that the
``details`` dict is properly forwarded as ``extra["details"]`` on the
emitted :class:`logging.LogRecord`.
"""

import logging

import pytest

from jvagent._logging_compat import JvAgentLogger, install


@pytest.fixture(autouse=True)
def _ensure_installed():
    install()
    yield


def _capture_records(logger_name: str) -> list[logging.LogRecord]:
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=logging.DEBUG)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield captured, logger
    finally:
        logger.removeHandler(handler)


def test_details_kwarg_does_not_raise():
    """logger.error(..., details=...) used to raise TypeError on stdlib."""
    logger = logging.getLogger("jvagent.tests.compat.no_raise")
    assert isinstance(logger, JvAgentLogger), "shim not installed"
    # If install() hadn't run, this would raise TypeError.
    logger.error("test message", details={"k": "v"})


def test_details_forwarded_into_extra():
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger("jvagent.tests.compat.forwarded")
    logger.setLevel(logging.DEBUG)
    handler = _Capture(level=logging.DEBUG)
    logger.addHandler(handler)
    try:
        logger.error("boom", details={"agent_id": "a1", "code": "x"})
    finally:
        logger.removeHandler(handler)

    assert len(captured) == 1
    rec = captured[0]
    # `extra` keys are merged into the LogRecord dict by stdlib.
    assert getattr(rec, "details", None) == {"agent_id": "a1", "code": "x"}


def test_caller_supplied_extra_details_wins():
    """If caller passes both extra={'details':...} and details=..., extra wins."""
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger("jvagent.tests.compat.precedence")
    logger.setLevel(logging.DEBUG)
    handler = _Capture(level=logging.DEBUG)
    logger.addHandler(handler)
    try:
        logger.error(
            "boom",
            extra={"details": {"src": "extra"}},
            details={"src": "details_kwarg"},
        )
    finally:
        logger.removeHandler(handler)

    assert len(captured) == 1
    assert getattr(captured[0], "details", None) == {"src": "extra"}


def test_existing_loggers_still_work_without_details():
    """Loggers created before install() are stdlib; details= would fail there.

    But jvagent's own modules are imported after install() runs, so this
    test ensures the new class is what jvagent module loggers get.
    """
    logger = logging.getLogger("jvagent.core")
    assert isinstance(logger, JvAgentLogger)
