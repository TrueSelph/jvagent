"""A reply may not claim a source the turn never consulted.

Live failure: after research was assimilated, the agent answered "what was
Eldon's most recent workshop?" with no tool call at all (a guess), then answered
"was this from the knowledge base?" with "yes, retrieved from the knowledge
base" — a false statement about its own provenance, which is worse than the
guess. Both turns ran one light tick and invoked only `reply`.

The response parameters already forbid unverified claims. This enforces the one
case that is machine-checkable: the loop knows no tool ran.
"""

from __future__ import annotations

from jvagent.action.orchestrator.loop_helpers import unsupported_source_claim

# --- detector ---------------------------------------------------------------


def test_assertions_of_retrieval_are_caught():
    for text in (
        "Yes, the information was retrieved from the knowledge base we created.",
        "I searched the knowledge base and found three documents.",
        "According to the knowledge base, he founded two companies.",
        "Based on the search results, the workshop was in March.",
        "The search results show two entries.",
        "That came from the knowledge base.",
    ):
        assert unsupported_source_claim(text), text


def test_offers_and_plans_are_not_claims():
    """Only assertions are guarded — an agent must stay free to offer a lookup."""
    for text in (
        "I can search the knowledge base if you like.",
        "Would you like me to look in the knowledge base?",
        "I'll check the knowledge base next.",
        "Shall I consult the documents?",
        "If you want, I could search the knowledge base.",
    ):
        assert not unsupported_source_claim(text), text


def test_ordinary_answers_are_untouched():
    for text in (
        "Eldon's most recent workshop focused on digital marketing.",
        "Your order ships Tuesday.",
        "",
    ):
        assert not unsupported_source_claim(text), text


# --- loop wiring ------------------------------------------------------------


def _orchestrator():
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    return OrchestratorInteractAction()


def test_guard_fires_only_when_no_substantive_tool_ran():
    ex = _orchestrator()
    claim = "Yes, that was retrieved from the knowledge base."

    nudge = ex._grounding_deflection(claim, 0)
    assert nudge is not None
    assert "not called ANY tool" in nudge["observation"]
    assert claim in nudge["observation"]

    # A turn that actually did work is not second-guessed about which tool.
    assert ex._grounding_deflection(claim, 1) is None


def test_guard_ignores_a_reply_making_no_claim():
    ex = _orchestrator()
    assert ex._grounding_deflection("The workshop was about marketing.", 0) is None


def test_guard_can_be_disabled():
    ex = _orchestrator()
    ex.enforce_grounded_claims = False
    assert ex._grounding_deflection("I searched the knowledge base.", 0) is None
