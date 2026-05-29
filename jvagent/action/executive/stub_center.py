"""StubCenter — deterministic center for Executive unit tests.

Pops a pre-scripted sequence of :class:`CenterDirective`s. Test-only: it has no
``info.yaml`` and is never loaded by the action loader. Tests instantiate it
directly and inject it via a monkeypatched center lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.executive.base import BaseCenter
from jvagent.action.executive.contracts import RETURN, CenterDirective, Result

if TYPE_CHECKING:
    from jvagent.action.executive.context import TurnContext
    from jvagent.action.executive.state import Frame


class StubCenter(BaseCenter):
    """Scripted-output center for tests.

    Usage::

        c = StubCenter()
        c.set_script([STEP(), RETURN(Result(content="done"))])

    When the script is exhausted, ``tick`` returns ``RETURN(Result("<stub:
    exhausted>"))`` to keep tests deterministic instead of raising.

    Set ``double_model_call=True`` to make ``tick`` acquire the model budget
    twice — used to prove the loop enforces one-model-call-per-tick.
    """

    description: str = attribute(default="Test-only stub center. Not for production.")

    def _scratch(self) -> Dict[str, Any]:
        state = self.__dict__.get("_scratch_state")
        if state is None:
            state = {"script": [], "call_count": 0, "double_model_call": False}
            object.__setattr__(self, "_scratch_state", state)
        return state

    def set_script(self, script: List[CenterDirective]) -> None:
        self._scratch()["script"] = list(script)

    def set_double_model_call(self, value: bool = True) -> None:
        self._scratch()["double_model_call"] = bool(value)

    @property
    def call_count(self) -> int:
        return int(self._scratch()["call_count"])

    def set_name(self, name: str) -> None:
        """Override ``center_name`` so several stubs are distinguishable."""

        def _name(_n: str = name) -> str:
            return _n

        self.__dict__["center_name"] = _name

    async def tick(
        self,
        ctx: "TurnContext",
        frame: "Frame",
    ) -> CenterDirective:
        scratch = self._scratch()
        scratch["call_count"] += 1
        if scratch["double_model_call"]:
            # Two model-call acquisitions in one tick — the loop must abort.
            ctx.use_model()
            ctx.use_model()
        script: List[CenterDirective] = scratch["script"]
        if not script:
            return RETURN(Result(content="<stub: exhausted>"))
        nxt: Optional[CenterDirective] = script.pop(0)
        return nxt  # type: ignore[return-value]


__all__ = ["StubCenter"]
