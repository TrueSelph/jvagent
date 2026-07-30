"""Run CUCS scenarios against a LIVE model (CUCS phase 2).

The CUCS spec (ADR-0027) describes deterministic E2E: ``harness.decisions``
supplies canned model decisions so a scenario asserts orchestrator plumbing
without a model in the loop. That is the right tool for testing the harness, and
the wrong one for testing the *prompt* — a canned decision cannot tell you
whether the model would have made it.

This module is the inverse. It omits ``harness`` entirely, lets the real model
drive the loop, and records what it actually did: which tools it called, how the
turn ended, what it said. That makes a prompt change measurable — run the same
scenarios against two prompt variants and compare compliance.

Scenarios are ordinary CUCS files; a turn simply leaves ``harness`` out. The
observations land in a ``loop`` assertion namespace (``then.loop``), which the
schema already permits since ``assertions`` sets ``additionalProperties: true``.

**This costs money.** Every turn is one or more real model calls against the
configured provider. Nothing here runs as part of the normal test suite.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

logger = logging.getLogger(__name__)

# Phrases that mark a turn as *announcing* rather than acting -- the specific
# failure the "Act, don't announce" rule exists to prevent, and the one a weaker
# model regresses into first. Deliberately narrow: these match a stated intention
# to act next, not a description of something already done.
ANNOUNCEMENT_PATTERNS = (
    r"\bI(?:'ll| will)\s+(?:now\s+)?(?:go ahead and\s+)?(?:start|begin|do|run|search|look|write|create|check|fetch|prepare|put together)",
    r"\blet me (?:now\s+)?(?:go ahead and\s+)?(?:start|begin|do|run|search|look|write|create|check|fetch)",
    r"\bI'm going to\s+(?:now\s+)?(?:start|begin|do|run|search|look|write|create|check|fetch)",
    r"\bgive me a (?:moment|second|minute) while I\b",
)


@dataclass
class TurnObservation:
    """What the orchestrator actually did on one turn."""

    turn_id: str
    utterance: str
    tools_invoked: List[str] = field(default_factory=list)
    activated_skills: List[str] = field(default_factory=list)
    ended_via: str = ""
    tick_count: int = 0
    reply: str = ""
    error: str = ""

    @property
    def announced_without_acting(self) -> bool:
        """True when the reply states an intention to act and the turn then
        stopped without a substantive tool call after it.

        This is the observable form of "Act, don't announce": saying "I'll search
        for that now" *is* fine if a search follows, and is the failure mode if
        the turn ends there.
        """
        if not self.reply:
            return False
        announced = any(
            re.search(p, self.reply, re.IGNORECASE) for p in ANNOUNCEMENT_PATTERNS
        )
        if not announced:
            return False
        return not self.substantive_tools

    @property
    def substantive_tools(self) -> List[str]:
        """Tools that did work, excluding egress and discovery meta-tools."""
        from jvagent.action.orchestrator.constants import NON_SUBSTANTIVE_TOOLS

        return [
            t
            for t in self.tools_invoked
            if t and t not in NON_SUBSTANTIVE_TOOLS and not t.startswith("(")
        ]


@dataclass
class ScenarioResult:
    scenario_id: str
    turns: List[TurnObservation] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def build_visitor(
    utterance: str,
    *,
    channel: str = "web",
    user_id: str = "cucs_user",
    new_user: bool = False,
    context: Optional[Dict[str, Any]] = None,
    conversation: Any = None,
) -> Any:
    """A walker stand-in for a live scenario turn.

    Mirrors ``tests/action/orchestrator/conftest.py``. ``conversation`` is passed
    back in on later turns so a multi-turn scenario keeps its task store and
    context, which is what makes flow continuation observable at all.
    """
    directives: List[Dict[str, Any]] = []
    interaction = MagicMock()
    interaction.id = f"cucs_{abs(hash(utterance)) % 10**8}"
    interaction.utterance = utterance
    interaction.response = ""
    interaction.directives = directives
    parameters: List[Dict[str, Any]] = []
    interaction.parameters = parameters
    interaction.has_emitted = lambda: bool((interaction.response or "").strip())

    def add_directive(content: str, action_name: str = "ReplyAction") -> bool:
        directives.append(
            {"action_name": action_name, "content": content, "executed": False}
        )
        return True

    interaction.add_directive = add_directive
    interaction.get_unexecuted_directives = lambda: [
        d for d in directives if not d.get("executed")
    ]

    # Parameters accumulate the way they do in production. This used to be a
    # no-op MagicMock with a permanently empty pool, which made every CUCS run
    # exercise a configuration that no real turn has: an empty pool falls back
    # to the framework core, and the core always works. Two egress bugs on this
    # branch survived the A/B for exactly that reason — the pool is
    # orchestration-scoped, and code that read it without unioning the response
    # core silently governed nothing.
    def add_parameter(parameter: Dict[str, Any], action_name: str = "") -> bool:
        if not isinstance(parameter, dict) or not parameter:
            return False
        entry = dict(parameter)
        entry.setdefault("action_name", action_name)
        if entry in parameters:
            return False
        parameters.append(entry)
        return True

    def add_parameters(params: List[Dict[str, Any]], action_name: str = "") -> bool:
        # Every parameter must be offered. A generator inside any() would
        # short-circuit on the first success and silently drop the rest.
        added = False
        for parameter in params or []:
            if add_parameter(parameter, action_name):
                added = True
        return added

    interaction.add_parameter = add_parameter
    interaction.add_parameters = add_parameters
    interaction.get_unexecuted_parameters = lambda: [
        p for p in parameters if not p.get("executed")
    ]
    interaction.set_to_executed = lambda directives=None, parameters=None: None

    def set_response(text: str) -> bool:
        interaction.response = text
        return True

    interaction.set_response = set_response
    interaction.mark_emitted = lambda: None
    interaction.save = AsyncMock()

    if conversation is None:
        conversation = MagicMock()
        conversation.context = dict(context or {})
        conversation.tasks = []
        conversation.save = AsyncMock()
        # Prior turns, in the shape get_interaction_history(formatted=True)
        # returns. A multi-turn scenario is meaningless without it: with an empty
        # history the agent genuinely cannot recall turn 1 on turn 2, so a recall
        # scenario fails for a harness reason and reads as an agent bug.
        conversation.cucs_history = []

        async def get_interaction_history(**_kw):
            return list(conversation.cucs_history)

        conversation.get_interaction_history = get_interaction_history

    visitor = MagicMock()
    # A MagicMock agent stringifies to "_MagicMock_name_mock._agent.id_id_<addr>",
    # and per-user file storage builds a directory from it — so every scenario
    # run left a fresh junk folder behind (84 of them after one A/B sweep).
    # Pin a stable id so runs reuse one scope instead of littering.
    visitor.agent_id = "n.Agent.cucs_live_runner"
    visitor.user_id = user_id
    visitor.channel = channel
    visitor.utterance = utterance
    visitor.new_user = new_user
    visitor.interaction = interaction
    visitor.conversation = conversation
    visitor.data = {}
    visitor.add_directives = AsyncMock()
    visitor.visit = AsyncMock()
    # No response bus: ReplyAction._pipe_response then takes its no-bus branch
    # and writes interaction.response itself. A MagicMock would make both of
    # these truthy, pushing egress down the publish path where the reply is
    # never recorded -- and patching _pipe_response to compensate double-counts
    # whatever the real one already wrote.
    visitor.response_bus = None
    visitor.session_id = None
    return visitor


class LiveScenarioRunner:
    """Drive CUCS scenarios through a real orchestrator and a real model."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    async def run(self, scenario: Dict[str, Any]) -> ScenarioResult:
        """Run every turn of *scenario*, collecting observations per turn."""
        from jvagent.action.orchestrator.orchestrator_interact_action import (
            OrchestratorInteractAction,
        )

        given = scenario.get("given") or {}
        result = ScenarioResult(scenario_id=scenario.get("id", "<unnamed>"))
        sink: Dict[str, Any] = {}

        original_record = OrchestratorInteractAction._record_orchestrator_activation

        async def record(self, visitor, **kwargs):
            # The orchestrator's own telemetry is the source of truth for what
            # the loop did -- reusing it avoids a second, drifting definition of
            # "which tools ran".
            sink.update(kwargs)
            return await original_record(self, visitor, **kwargs)

        # mypy: patching a bound method is the point here -- the loop's own
        # telemetry is the only complete record of what it did.
        OrchestratorInteractAction._record_orchestrator_activation = record  # type: ignore[method-assign]

        conversation = None
        try:
            for turn in scenario.get("turns") or []:
                sink.clear()
                utterance = (turn.get("when") or {}).get("user", "")
                visitor = build_visitor(
                    utterance,
                    channel=given.get("channel", "web"),
                    new_user=bool(given.get("new_user", False)),
                    context=given.get("context") or {},
                    conversation=conversation,
                )
                conversation = visitor.conversation  # carry state across turns

                observation = TurnObservation(
                    turn_id=turn.get("id", "?"), utterance=utterance
                )
                try:
                    await self.orchestrator.execute(visitor)
                except Exception as exc:  # a crashed turn is a scenario failure
                    observation.error = f"{type(exc).__name__}: {exc}"
                    logger.warning("live scenario turn raised: %s", exc)

                observation.tools_invoked = [
                    t for t in (sink.get("tools_invoked") or []) if t
                ]
                observation.activated_skills = list(sink.get("activated") or [])
                observation.ended_via = str(sink.get("ended_via") or "")
                observation.tick_count = int(sink.get("tick_count") or 0)
                observation.reply = (visitor.interaction.response or "").strip()
                result.turns.append(observation)

                history = getattr(conversation, "cucs_history", None)
                if history is not None:
                    history.append({"role": "user", "content": utterance})
                    if observation.reply:
                        history.append(
                            {"role": "assistant", "content": observation.reply}
                        )

                result.failures.extend(
                    f"[{observation.turn_id}] {msg}"
                    for msg in evaluate_turn(turn.get("then") or {}, observation)
                )
        finally:
            OrchestratorInteractAction._record_orchestrator_activation = (  # type: ignore[method-assign]
                original_record
            )

        return result


def evaluate_turn(then: Dict[str, Any], observed: TurnObservation) -> List[str]:
    """Evaluate a turn's ``then`` block; return human-readable failures.

    Only the namespaces a live run can observe are evaluated here: ``loop``
    (what the orchestrator did) and ``publish`` (what the user was told). The
    deterministic namespaces (``task_graph``, ``session``) belong to the canned
    harness path and are ignored rather than silently reported as passing.
    """
    failures: List[str] = []
    if observed.error:
        failures.append(f"turn raised {observed.error}")

    # A dead turn must never satisfy a negative assertion. "The reply must not
    # say X" is trivially true of an empty reply, so a turn where the model call
    # failed -- a bad key, a timeout, a rate limit -- would otherwise score as a
    # clean pass on exactly the scenarios that assert what the agent must NOT
    # say. That turns an outage into a green A/B run, which is worse than no
    # measurement at all.
    if not observed.reply and not observed.tools_invoked:
        failures.append(
            "turn produced no reply and invoked no tools — the model call "
            f"likely failed (ended_via={observed.ended_via!r}); "
            "treating as inconclusive, not as a pass"
        )
        return failures
    if observed.ended_via == "no_decision":
        failures.append(
            "loop ended via 'no_decision' — the model returned nothing "
            "parseable; treating as inconclusive, not as a pass"
        )
        return failures

    loop = then.get("loop") or {}
    called = observed.tools_invoked

    for name in loop.get("tools_called") or []:
        if name not in called:
            failures.append(f"expected tool {name!r} to be called; called={called}")
    for name in loop.get("tools_not_called") or []:
        if name in called:
            failures.append(f"tool {name!r} should not have been called")
    for name in loop.get("skills_activated") or []:
        if name not in observed.activated_skills:
            failures.append(
                f"expected skill {name!r} to activate; "
                f"activated={observed.activated_skills}"
            )

    expected_end = loop.get("ended_via")
    if expected_end and observed.ended_via != expected_end:
        failures.append(
            f"ended_via was {observed.ended_via!r}, expected {expected_end!r}"
        )

    minimum = loop.get("min_substantive_tools")
    if minimum is not None and len(observed.substantive_tools) < int(minimum):
        failures.append(
            f"only {len(observed.substantive_tools)} substantive tool call(s) "
            f"({observed.substantive_tools}), expected at least {minimum}"
        )

    if loop.get("must_not_announce") and observed.announced_without_acting:
        failures.append(
            "reply announced an action without performing it: "
            f"{observed.reply[:160]!r}"
        )

    if loop.get("must_reply") and not observed.reply:
        failures.append("turn produced no user-facing reply")

    publish = then.get("publish") or {}
    for needle in publish.get("contains") or []:
        if needle.lower() not in observed.reply.lower():
            failures.append(f"reply missing expected text {needle!r}")
    pattern = publish.get("matches")
    if pattern and not re.search(pattern, observed.reply, re.IGNORECASE):
        failures.append(f"reply did not match /{pattern}/")
    for pattern in publish.get("not_matches") or []:
        if re.search(pattern, observed.reply, re.IGNORECASE):
            failures.append(
                f"reply matched forbidden /{pattern}/: {observed.reply[:160]!r}"
            )

    return failures


__all__ = [
    "LiveScenarioRunner",
    "ScenarioResult",
    "TurnObservation",
    "build_visitor",
    "evaluate_turn",
    "ANNOUNCEMENT_PATTERNS",
]
