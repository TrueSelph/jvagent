"""Harness conformance suite — native, embedded, and remote share these fixtures."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import NativeCaller


@pytest.fixture
def native_caller() -> NativeCaller:
    return NativeCaller(
        agent_id="agent-conformance",
        user_id="user-conformance",
        session_id="sess-conformance",
    )
