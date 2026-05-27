"""Response delivery helpers for ReasoningHelm.

A thin shim over the unified ``deliver_via_persona`` entrypoint in
``persona_delivery.py``. Used for the engine's final response (after the
think-act-observe loop terminates).

The standalone-Cockpit ancestor also exported ``deliver_conversational``
for the conversational fast-path (skip-engine, persona-only); that was
removed in the Phase-2 ReasoningHelm distillation because ReflexHelm
owns smalltalk upstream via direct EMIT.
"""

from __future__ import annotations

from typing import Any, Optional

from jvagent.action.helm.reasoning.catalog.skill_catalog import SkillCatalog
from jvagent.action.helm.reasoning.context import EngineResult
from jvagent.action.helm.reasoning.delivery.persona_delivery import deliver_via_persona


async def deliver_final_response(
    action: Any,
    visitor: Any,
    result: EngineResult,
    *,
    response_mode: str = "publish",
    degenerate_response_max_chars: int = 25,
    skill_catalog: Optional[SkillCatalog] = None,
) -> None:
    """Deliver the engine's final response via the unified entrypoint.

    Honors per-skill ``response_mode`` and ``verbatim_final`` overrides via
    the supplied ``skill_catalog``. Degenerate responses (short content)
    skip persona rewording and publish raw.
    """
    final_response = result.final_response
    if not final_response or not final_response.strip():
        return

    await deliver_via_persona(
        action,
        visitor,
        content=final_response,
        response_mode=response_mode,
        degenerate_response_max_chars=degenerate_response_max_chars,
        skill_catalog=skill_catalog,
        engine_result=result,
    )


__all__ = ["deliver_final_response"]
