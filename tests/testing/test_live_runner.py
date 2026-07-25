"""The live CUCS runner, exercised with a mocked model (no spend, no network).

The runner exists to measure prompt changes against a real model, but the runner
itself must be trustworthy before any of its numbers are. These tests pin the
observation capture and the assertion vocabulary using canned decisions, so a
broken harness fails here rather than silently reporting a false regression on a
run that costs money.
"""

from __future__ import annotations

from jvagent.testing.live_runner import (
    LiveScenarioRunner,
    TurnObservation,
    evaluate_turn,
)


def _obs(**kw) -> TurnObservation:
    base = dict(turn_id="t", utterance="u")
    base.update(kw)
    return TurnObservation(**base)


# --- announcement detection -------------------------------------------------
#
# "Act, don't announce" is the rule most likely to regress on a weaker model and
# the hardest to observe, so its detector needs to be right in both directions.


def test_announcement_without_a_tool_call_is_a_failure():
    observed = _obs(reply="Sure — I'll search for that now.", tools_invoked=["reply"])
    assert observed.announced_without_acting is True


def test_announcement_followed_by_real_work_is_fine():
    """Saying what you're doing is only a failure when you then don't do it."""
    observed = _obs(
        reply="I'll search for that now.",
        tools_invoked=["web_search__search", "reply"],
    )
    assert observed.announced_without_acting is False


def test_past_tense_report_is_not_an_announcement():
    observed = _obs(reply="I searched for that and found three results.")
    assert observed.announced_without_acting is False


def test_plain_answer_is_not_an_announcement():
    observed = _obs(reply="The capital of France is Paris.")
    assert observed.announced_without_acting is False


# --- substantive tool accounting -------------------------------------------


def test_egress_and_meta_tools_are_not_substantive():
    observed = _obs(tools_invoked=["find_tool", "use_skill", "reply", "(guard)"])
    assert observed.substantive_tools == []


def test_real_work_counts_as_substantive():
    observed = _obs(tools_invoked=["web_search__search", "reply"])
    assert observed.substantive_tools == ["web_search__search"]


# --- assertion vocabulary ---------------------------------------------------


def test_tools_called_and_not_called():
    observed = _obs(tools_invoked=["use_skill", "reply"])
    assert evaluate_turn({"loop": {"tools_called": ["use_skill"]}}, observed) == []
    assert evaluate_turn({"loop": {"tools_called": ["web_search__search"]}}, observed)
    assert evaluate_turn({"loop": {"tools_not_called": ["use_skill"]}}, observed)


def test_min_substantive_tools():
    observed = _obs(tools_invoked=["web_search__search", "reply"])
    assert evaluate_turn({"loop": {"min_substantive_tools": 1}}, observed) == []
    assert evaluate_turn({"loop": {"min_substantive_tools": 2}}, observed)


def test_ended_via_and_must_reply():
    observed = _obs(ended_via="reply", reply="hello")
    assert evaluate_turn({"loop": {"ended_via": "reply"}}, observed) == []
    assert evaluate_turn({"loop": {"ended_via": "final"}}, observed)
    assert evaluate_turn({"loop": {"must_reply": True}}, _obs(reply=""))


def test_publish_contains_and_not_matches():
    observed = _obs(reply="Your project is called Northwind.")
    assert evaluate_turn({"publish": {"contains": ["Northwind"]}}, observed) == []
    assert evaluate_turn({"publish": {"contains": ["Southwind"]}}, observed)
    assert (
        evaluate_turn({"publish": {"not_matches": ["can'?t recall"]}}, observed) == []
    )
    denial = _obs(reply="Sorry, I can't recall that.")
    assert evaluate_turn({"publish": {"not_matches": ["can'?t recall"]}}, denial)


def test_deterministic_namespaces_are_ignored_not_silently_passed():
    """task_graph belongs to the canned-harness path. A live run cannot observe
    it, so it must not be reported either way."""
    observed = _obs(tools_invoked=["reply"])
    assert evaluate_turn({"task_graph": {"pushed": "whatever"}}, observed) == []


def test_a_raised_turn_is_a_failure():
    assert evaluate_turn({}, _obs(error="RuntimeError: boom"))


# --- a broken run must not look like a clean one ----------------------------
#
# Negative assertions ("the reply must not say X") are trivially satisfied by an
# empty reply. Without this guard an outage -- bad key, timeout, rate limit --
# scores as a green A/B run on exactly the scenarios that assert what the agent
# must NOT say, which is worse than having no measurement.


def test_dead_turn_fails_instead_of_passing_vacuously():
    dead = _obs(reply="", tools_invoked=[], ended_via="no_decision")
    failures = evaluate_turn({"publish": {"not_matches": ["I can'?t"]}}, dead)
    assert failures
    assert any("inconclusive" in f for f in failures)


def test_no_decision_is_inconclusive_even_with_a_reply():
    stalled = _obs(reply="", tools_invoked=["reply"], ended_via="no_decision")
    assert any("inconclusive" in f for f in evaluate_turn({}, stalled))


def test_a_real_turn_still_passes_its_negative_assertion():
    live = _obs(
        reply="Sure, here's the answer.", tools_invoked=["reply"], ended_via="reply"
    )
    assert evaluate_turn({"publish": {"not_matches": ["I can'?t"]}}, live) == []
