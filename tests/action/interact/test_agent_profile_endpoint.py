"""The public agent-profile endpoint feeds an embedded chat header.

Whatever it returns is read by end customers on a third-party site, so the
field choice is a product decision, not an implementation detail.
"""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.interact.avatar_endpoints import _first_str


def test_description_is_preferred_over_role():
    """``description`` is the customer-facing blurb under the agent's name in
    the embed header. ``role`` (ADR-0014 identity half) is only a fallback when
    description is unset."""
    agent = SimpleNamespace(
        role="a friendly assistant for order questions",
        description="Order help for Evolve Business Solutions.",
    )
    assert _first_str(agent, "description", "role") == (
        "Order help for Evolve Business Solutions."
    )


def test_role_still_used_when_no_description_is_set():
    agent = SimpleNamespace(role="Handles billing questions.", description="")
    assert _first_str(agent, "description", "role") == "Handles billing questions."


def test_absent_on_both_yields_none():
    assert _first_str(SimpleNamespace(), "description", "role") is None
    assert _first_str(SimpleNamespace(description="   "), "description", "role") is None
