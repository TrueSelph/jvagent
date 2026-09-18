"""Isolation for process-wide response egress state."""

import pytest

from jvagent.action.response.response_bus import clear_interaction_egress


@pytest.fixture(autouse=True)
def _clear_interaction_egress():
    """Keep placeholder interaction IDs from leaking across unit tests."""
    clear_interaction_egress()
    yield
    clear_interaction_egress()
