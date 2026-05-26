"""Bridge — multi-helm orchestrator InteractAction.

Bridge ships as a peer to :mod:`jvagent.action.cockpit`. It composes one or
more helms (see :mod:`jvagent.action.helm`) into a single ``InteractAction``
at weight ``-200``. Each Bridge walker visit issues at most one helm step,
preserving the one-model-call-per-visit invariant established by ADR-0002.

Public exports:

- :class:`BridgeInteractAction` — the InteractAction subclass.
- :class:`BridgeState` — the per-turn state object stored on
  ``visitor._bridge_state``.
- :class:`BridgeAccessDenied` — raised by ``check_helm_access`` /
  ``check_delegate_access`` when AccessControl denies a target.
"""

from jvagent.action.bridge import endpoints  # noqa: F401  (endpoint registration)
from jvagent.action.bridge.access import (
    BridgeAccessDenied,
    check_delegate_access,
    check_helm_access,
)
from jvagent.action.bridge.bridge_interact_action import BridgeInteractAction
from jvagent.action.bridge.state import BridgeState

__all__ = [
    "BridgeAccessDenied",
    "BridgeInteractAction",
    "BridgeState",
    "check_delegate_access",
    "check_helm_access",
]
