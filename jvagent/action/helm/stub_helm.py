"""StubHelm — deterministic helm used in Bridge unit tests.

The stub returns a pre-scripted sequence of verbs from its in-memory script.
Tests configure it via :meth:`set_script` and inspect :attr:`call_count` /
:meth:`last_state_snapshot` after each Bridge visit.

This helm is **test-only**. It is not registered with a production
``info.yaml`` and is never loaded by the action loader. Tests instantiate it
directly via ``StubHelm()`` (sidestepping the loader) and inject it onto the
``BridgeInteractAction`` under test via a monkey-patched ``_lookup_helm``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from jvspatial.core.annotations import attribute

from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import EMIT, HelmStepResult

if TYPE_CHECKING:
    from jvagent.action.bridge.state import BridgeState
    from jvagent.action.interact.interact_walker import InteractWalker


class StubHelm(BaseHelm):
    """Scripted-output helm for tests.

    Usage::

        helm = StubHelm()
        helm.set_script([EMIT("hi"), YIELD()])

    Each ``step()`` call pops and returns the next scripted verb. When the
    script is exhausted, ``step()`` returns ``EMIT(text="<stub: script exhausted>",
    finalize=True)`` to keep tests deterministic instead of raising.

    The script and call-tracking state live on a per-instance ``_state`` dict
    attached lazily (sidesteps Pydantic's attribute restrictions on Node
    subclasses).
    """

    description: str = attribute(default="Test-only stub helm. Not for production.")

    def _scratch(self) -> Dict[str, Any]:
        # ``_scratch_state`` is a plain instance attribute set on first access.
        # We use ``object.__setattr__`` to bypass Pydantic's restricted setter.
        state = self.__dict__.get("_scratch_state")
        if state is None:
            state = {
                "script": [],
                "call_count": 0,
                "last_state_snapshot": {},
            }
            object.__setattr__(self, "_scratch_state", state)
        return state

    def set_script(self, script: List[HelmStepResult]) -> None:
        """Replace the scripted verb sequence (mutates in place)."""
        self._scratch()["script"] = list(script)

    @property
    def script(self) -> List[HelmStepResult]:
        return list(self._scratch()["script"])

    @property
    def call_count(self) -> int:
        return int(self._scratch()["call_count"])

    @property
    def last_state_snapshot(self) -> Dict[str, Any]:
        """Read-only copy of the most recent ``bridge_state.helm_states`` slot
        observed by this helm. Useful for asserting handoff_state propagation
        in tests."""
        return dict(self._scratch()["last_state_snapshot"])

    async def _step_impl(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """Stub override of the BaseHelm abstract.

        The auto-recording on ``interaction.actions`` is applied by the
        :meth:`BaseHelm.step` wrapper — tests that assert recording on a
        StubHelm just need to mock or spy ``interaction.record_action_execution``.
        """
        scratch = self._scratch()
        scratch["call_count"] += 1
        slot = bridge_state.helm_states.get(self.helm_name(), {})
        scratch["last_state_snapshot"] = dict(slot) if isinstance(slot, dict) else {}

        script: List[HelmStepResult] = scratch["script"]
        if not script:
            return EMIT(text="<stub: script exhausted>", finalize=True)
        return script.pop(0)
