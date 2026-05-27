"""Regression tests for :meth:`BaseHelm.step` action-trace self-recording.

The convention (established by ``PersonaAction.respond`` at
``persona_action.py:465``) is that any action which performs work for a
turn appends its own class name to ``interaction.actions``. Operators
read that list to see, in order, every action that contributed to the
turn — Bridge entries (one per walker visit) interleaved with the helms
that ran inside each visit, plus PersonaAction at the end.

These tests pin three invariants:

1. ``BaseHelm.step`` is the sole self-recording site (subclasses
   override ``_step_impl`` and do NOT call ``record_action_execution``
   themselves — no double-recording).
2. The wrapper records the helm's ``helm_name()`` (which defaults to
   the class name, matching the convention used by InteractActions
   and PersonaAction).
3. A recording failure (interaction missing, save errors) MUST NOT
   propagate out of the helm — recording is a best-effort observability
   hook, never load-bearing for the turn.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import EMIT, YIELD, HelmStepResult


class _RecordingProbeHelm(BaseHelm):
    """Minimal BaseHelm subclass for testing the wrapper contract.

    Records the verb its ``_step_impl`` was asked to return so tests can
    confirm the wrapper invoked the impl exactly once. Defaults to YIELD
    when no script is set so accidental misuse fails loudly.
    """

    _scripted_verb: HelmStepResult = None  # type: ignore[assignment]
    _impl_calls: int = 0

    async def _step_impl(self, visitor, bridge_state) -> HelmStepResult:
        # Track on the instance — Pydantic doesn't allow ad-hoc attrs,
        # so we tunnel through a class-level dict keyed by id(self).
        _IMPL_CALL_COUNTER[id(self)] = _IMPL_CALL_COUNTER.get(id(self), 0) + 1
        return _SCRIPTED_VERB.get(id(self)) or YIELD()


_IMPL_CALL_COUNTER: dict = {}
_SCRIPTED_VERB: dict = {}


def _make_visitor(actions_list: list) -> MagicMock:
    """Build a mock visitor whose ``interaction.record_action_execution``
    appends to the given list, so tests can assert the recorded names."""
    visitor = MagicMock()
    visitor.interaction = MagicMock()
    visitor.interaction.record_action_execution = lambda name: actions_list.append(name)
    return visitor


@pytest.mark.asyncio
class TestStepWrapperRecordsHelmName:
    async def test_helm_name_recorded_on_yield(self):
        """A YIELD-returning helm still records — every step is work."""
        helm = _RecordingProbeHelm()
        _SCRIPTED_VERB[id(helm)] = YIELD()
        actions: list = []
        visitor = _make_visitor(actions)

        result = await helm.step(visitor, MagicMock())

        assert isinstance(result, YIELD)
        assert actions == ["_RecordingProbeHelm"]

    async def test_helm_name_recorded_on_emit(self):
        """EMIT-returning helms record the same way as YIELD."""
        helm = _RecordingProbeHelm()
        _SCRIPTED_VERB[id(helm)] = EMIT(text="hi", finalize=True)
        actions: list = []
        visitor = _make_visitor(actions)

        result = await helm.step(visitor, MagicMock())

        assert isinstance(result, EMIT)
        assert actions == ["_RecordingProbeHelm"]

    async def test_step_impl_called_exactly_once_per_step(self):
        """The wrapper must not double-invoke the impl (no retry / loop)."""
        helm = _RecordingProbeHelm()
        _IMPL_CALL_COUNTER[id(helm)] = 0
        _SCRIPTED_VERB[id(helm)] = YIELD()
        visitor = _make_visitor([])

        await helm.step(visitor, MagicMock())

        assert _IMPL_CALL_COUNTER[id(helm)] == 1

    async def test_recording_uses_helm_name_method(self):
        """Recording must use ``helm_name()`` so subclasses that override
        it get the override surfaced in the trace. Default is class name.

        Pydantic blocks instance-level method overrides, so test the
        override path with a subclass that supplies its own ``helm_name``.
        """

        class _CustomSlugHelm(_RecordingProbeHelm):
            def helm_name(self) -> str:
                return "CustomSlug"

        helm = _CustomSlugHelm()
        _SCRIPTED_VERB[id(helm)] = YIELD()
        actions: list = []
        visitor = _make_visitor(actions)

        await helm.step(visitor, MagicMock())

        assert actions == ["CustomSlug"]


@pytest.mark.asyncio
class TestStepWrapperDefensiveness:
    async def test_no_interaction_does_not_raise(self):
        """When the visitor has no interaction, step() must still return
        the verb without raising. Recording is best-effort."""
        helm = _RecordingProbeHelm()
        _SCRIPTED_VERB[id(helm)] = EMIT(text="x", finalize=True)
        visitor = MagicMock()
        visitor.interaction = None

        result = await helm.step(visitor, MagicMock())

        assert isinstance(result, EMIT)

    async def test_recording_failure_does_not_propagate(self):
        """If ``record_action_execution`` raises, the helm step still
        succeeds. Persona uses the same defensive posture."""
        helm = _RecordingProbeHelm()
        _SCRIPTED_VERB[id(helm)] = YIELD()
        visitor = MagicMock()
        visitor.interaction = MagicMock()

        def _boom(name):
            raise RuntimeError("storage backend down")

        visitor.interaction.record_action_execution = _boom

        # Must NOT raise — should still return the verb.
        result = await helm.step(visitor, MagicMock())
        assert isinstance(result, YIELD)


class TestSubclassesUseStepImplNotStep:
    """Each shipped helm overrides ``_step_impl`` (the wrapper's
    extension point), never the wrapper's ``step`` itself. If a future
    refactor forgets this, the helm wouldn't self-record and silently
    fall off the action trace."""

    def test_reflex_helm_overrides_step_impl(self):
        from jvagent.action.helm.reflex.reflex_helm import ReflexHelm

        # ``step`` on Reflex should be inherited from BaseHelm.
        assert ReflexHelm.step is BaseHelm.step
        # ``_step_impl`` must be defined directly on Reflex (not inherited).
        assert "_step_impl" in ReflexHelm.__dict__

    def test_reasoning_helm_overrides_step_impl(self):
        from jvagent.action.helm.reasoning.reasoning_helm import ReasoningHelm

        assert ReasoningHelm.step is BaseHelm.step
        assert "_step_impl" in ReasoningHelm.__dict__

    # PersonaHelm was scrapped (May 2026) — the dedicated helm was
    # never wired into a live agent's helm chain. Bridge handles
    # persona stylisation directly via deliver_via_persona from
    # BridgeInteractAction._publish_emit_via_persona. The
    # ``test_persona_helm_overrides_step_impl`` test that used to
    # live here has been removed alongside the helm.
