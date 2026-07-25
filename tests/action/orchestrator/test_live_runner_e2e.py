"""The live CUCS runner driving the real orchestrator loop (canned decisions).

Lives here rather than in tests/testing/ because it needs the orchestrator
fixtures. No model is called and nothing is billed -- only the model *decision*
is canned, so the loop, the telemetry capture and the assertion pass are all
real.
"""

from __future__ import annotations

from jvagent.testing.live_runner import LiveScenarioRunner


async def test_runner_observes_a_real_loop(make_orchestrator, monkeypatch):
    """Drive the actual orchestrator loop and confirm the runner reports what it
    did. Only the model decision is canned."""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()
    orchestrator = make_orchestrator(
        actions=[reply],
        decisions=[
            {"action": "tool", "tool": "reply", "args": {"text": "Hey there!"}},
            {"action": "final", "answer": ""},
        ],
    )

    scenario = {
        "schema": "jvagent.use-case/v1",
        "id": "test.greeting",
        "title": "greeting replies and stops",
        "given": {"channel": "web"},
        "turns": [
            {
                "id": "greet",
                "when": {"user": "hi"},
                "then": {
                    "loop": {"ended_via": "reply", "must_reply": True},
                    "publish": {"contains": ["Hey"]},
                },
            }
        ],
    }

    result = await LiveScenarioRunner(orchestrator).run(scenario)
    assert result.passed, result.failures
    assert result.turns[0].reply == "Hey there!"
    assert "reply" in result.turns[0].tools_invoked


async def test_runner_reports_a_failing_expectation(make_orchestrator, monkeypatch):
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()
    orchestrator = make_orchestrator(
        actions=[reply],
        decisions=[
            {"action": "tool", "tool": "reply", "args": {"text": "I'll look into it."}},
            {"action": "final", "answer": ""},
        ],
    )

    scenario = {
        "schema": "jvagent.use-case/v1",
        "id": "test.announce",
        "title": "announcing without acting is caught",
        "given": {"channel": "web"},
        "turns": [
            {
                "id": "ask",
                "when": {"user": "look up the weather"},
                "then": {"loop": {"must_not_announce": True}},
            }
        ],
    }

    result = await LiveScenarioRunner(orchestrator).run(scenario)
    assert not result.passed
    assert any("announced an action" in f for f in result.failures)


async def test_runner_restores_patched_methods(make_orchestrator, monkeypatch):
    """The runner patches a class-level method to read the loop's telemetry;
    leaking it would corrupt every later scenario in the same process — and an
    A/B run does hundreds of scenarios back to back."""
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    before_record = OrchestratorInteractAction._record_orchestrator_activation

    orchestrator = make_orchestrator(
        actions=[], decisions=[{"action": "final", "answer": "done"}]
    )
    scenario = {
        "schema": "jvagent.use-case/v1",
        "id": "test.noop",
        "title": "noop",
        "given": {},
        "turns": [{"id": "t", "when": {"user": "hi"}}],
    }
    await LiveScenarioRunner(orchestrator).run(scenario)

    assert OrchestratorInteractAction._record_orchestrator_activation is before_record
