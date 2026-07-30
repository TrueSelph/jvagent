"""The public agent-profile endpoint feeds an embedded chat header.

Whatever it returns is read by end customers on a third-party site, so the
field choice is a product decision, not an implementation detail.
"""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.interact.avatar_endpoints import _first_str


def test_role_is_preferred_over_the_operator_description():
    """``description`` is the operator's note about the agent and is routinely
    architectural — the example agent's begins "Orchestrator-pattern agent. One
    orchestrator (-200) locks onto an active flow…", which is what a visitor to a
    customer site actually saw. ``role`` is the customer-facing half of the
    identity (ADR-0014) and is what belongs under the agent's name."""
    agent = SimpleNamespace(
        role="a friendly assistant for order questions",
        description="Orchestrator-pattern agent. One orchestrator (-200) locks onto…",
    )
    assert _first_str(agent, "role", "description") == (
        "a friendly assistant for order questions"
    )


def test_description_still_used_when_no_role_is_set():
    agent = SimpleNamespace(role="", description="Handles billing questions.")
    assert _first_str(agent, "role", "description") == "Handles billing questions."


def test_absent_on_both_yields_none():
    assert _first_str(SimpleNamespace(), "role", "description") is None
    assert _first_str(SimpleNamespace(role="   "), "role", "description") is None
