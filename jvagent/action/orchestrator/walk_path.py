"""Walk-path curation for OrchestratorInteractAction."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class OrchestratorWalkPathMixin:
    async def _curate_walk_path(self, visitor: "InteractWalker") -> None:
        """Drop tool-exposed (routable) IAs from the remaining walk path.

        An anchored IA furnishes a tool via ``get_tools()`` and is reached only
        when the model selects that tool — it must NOT also run as an ordinary
        weight-chain member every turn (that was the "always triggered" cause).
        We keep: this orchestrator, ``always_execute`` IAs (auth/intro/audit),
        and any non-routable IA (no routing triggers → not a tool, so it should
        run in the chain). Best-effort — never breaks the turn.
        """
        curate = getattr(visitor, "curate_walk_path", None)
        if not callable(curate):
            return
        agent = await self._safe_agent()
        keep: List[Any] = [self]
        for action in await self._enabled_interact_actions(agent):
            if action is self or type(action).__name__ == "OrchestratorInteractAction":
                continue
            if getattr(action, "always_execute", False):
                keep.append(action)
                continue
            triggers_fn = getattr(action, "routing_triggers", None)
            triggers = (
                list(triggers_fn() or [])
                if callable(triggers_fn)
                else list(getattr(action, "anchors", None) or [])
            )
            if triggers:
                continue  # routable/tool IA — omit from the walk path
            keep.append(action)  # non-routable IA — keep in the weight chain
        try:
            await curate(keep)
        except Exception as exc:
            logger.debug("orchestrator: curate_walk_path failed: %s", exc)

