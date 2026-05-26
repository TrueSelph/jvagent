"""BaseHelm — abstract Action subclass orchestrated by ``BridgeInteractAction``.

A helm is **not** an ``InteractAction``: the walker does not visit it directly.
Bridge visits the helm via ``await helm.step(visitor, bridge_state)`` exactly
once per Bridge walker visit, then dispatches the returned ``HelmStepResult``
verb.

Subclasses MUST implement :meth:`step` and SHOULD declare a manifest in their
package ``info.yaml`` (loader integration arrives at milestone D).

Each helm's :meth:`step` issues **at most one** model call. This invariant
(ADR-0002 / ADR-0007) is load-bearing across cockpit and bridge.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.helm.contracts import HelmStepResult

if TYPE_CHECKING:
    from jvagent.action.bridge.state import BridgeState
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class BaseHelm(Action):
    """Abstract base for all helms orchestrated by Bridge.

    Subclasses inherit the standard ``Action`` lifecycle (``on_register``,
    ``on_enable``, ``on_startup``, ``on_disable``, ``on_deregister``). Helms
    contribute neither cockpit tools nor capabilities by default; override
    :meth:`get_tools` / :meth:`get_capabilities` if a helm needs to expose
    either (uncommon — helms are orchestrated by Bridge, not by another helm).

    The ``latency_class`` attribute is a convenience mirror of the manifest
    field. It is consulted by Bridge when deciding whether to publish an
    ``ack-on-shift`` before a ``SHIFT`` (milestone E wires this from the
    manifest; B reads it from this attribute as a fallback).
    """

    description: str = attribute(
        default="Abstract helm — subclasses override step() to produce a HelmStepResult.",
        description="Helm description (subclasses should override).",
    )
    latency_class: str = attribute(
        default="quick",
        description=(
            "One of: instant | quick | deliberate | long. Used by Bridge for "
            "ack-on-shift gating. Mirrors the manifest field; manifest takes "
            "precedence when present."
        ),
    )
    can_interrupt: bool = attribute(
        default=False,
        description=(
            "True iff this helm is allowed to emit SHIFT(interrupt=True). "
            "Defaults False; set True on Reflex-class helms."
        ),
    )
    can_emit_directly: bool = attribute(
        default=True,
        description=(
            "False forces this helm to never EMIT (it must SHIFT or DELEGATE). "
            "Used by classifier-style helms at milestone E."
        ),
    )

    @abstractmethod
    async def step(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """Execute one helm step and return a single verb.

        Implementations:

        - MUST issue at most one language-model call per invocation.
        - MUST NOT mutate ``bridge_state.gear_trace`` / ``shift_count`` —
          Bridge owns those fields.
        - MAY read and write per-helm state via
          ``bridge_state.helm_states.setdefault(self.helm_name(), {})``.
        - MAY publish thoughts/progress via ``visitor.publish(...)`` for
          observability but MUST emit user-facing text only through an
          ``EMIT`` verb.

        Returns:
            One of :class:`EMIT`, :class:`EXECUTE`, :class:`SHIFT`,
            :class:`DELEGATE`, :class:`YIELD`.
        """

    def helm_name(self) -> str:
        """Stable identifier used for ``BridgeState.current_helm`` and AC labels.

        Defaults to the class name (e.g. ``"ReflexHelm"``). Subclasses MAY
        override to provide a shorter slug, but the AC resource label
        ``tool:helm:{helm_name}`` MUST match whatever this returns.
        """
        return self.__class__.__name__
