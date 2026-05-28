"""Engine recovery-hatch tool: ``delegate_to_ia`` (ADR-0009 §6).

Surface for the engine to invoke an installed interact action by name
when:

- the user's intent matches an installed flow,
- the flow is not anchor-routable (operator omitted anchors), and
- Reflex therefore did not auto-DELEGATE to it upstream.

This is a graceful-fallback path, not the primary route. Conversational
IAs SHOULD declare anchors so Reflex's peer-awareness DELEGATE handles
them; the bootstrap warning fires when they don't (see the loader).

Dispatch mechanism — minimal coupling:

1. Engine model calls ``delegate_to_ia(name="HandoffInteractAction")``.
2. The tool validates the name against the eligible-IA list (excluding
   pattern orchestrators and always-execute IAs).
3. The tool appends the IA's class name to the helm's ``pending_ias``
   slot on :class:`BridgeState` — the same slot the helm reads when it
   dispatches a DELEGATE chain.
4. The tool sets ``session.finalized = True`` so the engine loop
   terminates this iteration.
5. ReasoningHelm's ``_step_impl`` reads ``pending_ias`` on its next
   tick and returns ``DELEGATE(...)`` to Bridge, which AC-checks
   ``tool:delegate:{name}`` and runs the IA inline.

Excluded from the eligible list (ADR-0009 §4 / §7):

- Pattern orchestrators (``manifest.pattern_orchestrator``) — Bridge
  itself; reaching it would recurse.
- Always-execute IAs (``always_execute=True``) — they already run on
  every turn through Bridge's curated walker queue.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jvagent.action.helm.reasoning.context import EngineContext
from jvagent.action.helm.reasoning.session import get_session
from jvagent.tooling.tool import Tool

logger = logging.getLogger(__name__)


# Visitor side-channel keys we reach into. Imported locally inside the
# builder so this module doesn't pull bridge imports at import time
# (engine tests build EngineContexts without Bridge present).


async def _enumerate_eligible_ias(ctx: EngineContext) -> List[Any]:
    """Resolve installed conversational IAs (per ADR-0009 §6 eligibility).

    Excludes pattern orchestrators and always-execute IAs. Anchorless
    conversational IAs ARE included — they are exactly the case this
    tool exists to recover.
    """
    agent = ctx.agent
    if agent is None:
        return []
    try:
        from jvagent.action.interact.base import InteractAction

        actions_mgr = await agent.get_actions_manager()
        if actions_mgr is None:
            return []
        all_enabled = await actions_mgr.get_all_actions(enabled_only=True)
    except Exception as exc:
        logger.debug("delegate_to_ia: action enumeration failed: %s", exc)
        return []

    eligible: List[Any] = []
    for action in all_enabled:
        if not isinstance(action, InteractAction):
            continue
        cls_name = action.__class__.__name__
        # Defense-in-depth: legacy class-name exclusion preserved
        # alongside the manifest pattern_orchestrator flag.
        # TODO(wave-10): drop literal once pattern_orchestrator universal.
        if cls_name in ("BridgeInteractAction", "CockpitInteractAction"):
            continue
        try:
            manifest = action.get_manifest()
        except Exception:
            continue
        if manifest.pattern_orchestrator:
            continue
        if getattr(action, "always_execute", False):
            continue
        eligible.append(action)
    return eligible


def _build_descriptions(ias: List[Any]) -> str:
    lines: List[str] = []
    for ia in ias:
        cls_name = ia.__class__.__name__
        try:
            desc = (ia.get_manifest().purpose or "").strip()
        except Exception:
            desc = ""
        if not desc:
            desc = "(no description declared)"
        lines.append(f"- {cls_name}: {desc}")
    return "\n".join(lines) or "(no eligible interact actions)"


def _build_delegate_to_ia_tools(
    ctx: EngineContext,
    eligible_ias: List[Any],
) -> List[Tool]:
    """Return the ``delegate_to_ia`` tool wired to the given context.

    The eligible-IA list is resolved at registry-assembly time so the
    description carries an up-to-date list of installed flows. If no
    eligible IA is installed, the tool is omitted from the registry by
    the assembler.
    """
    eligible_names = [ia.__class__.__name__ for ia in eligible_ias]
    descriptions = _build_descriptions(eligible_ias)

    async def _execute(name: str) -> Dict[str, Any]:
        target_name = (name or "").strip()
        if not target_name:
            return {
                "error": "missing required arg 'name'",
                "available": eligible_names,
            }
        if target_name not in eligible_names:
            return {
                "error": f"unknown or ineligible interact action: {target_name!r}",
                "available": eligible_names,
            }

        visitor = ctx.visitor
        if visitor is None:
            return {"error": "no visitor available — cannot dispatch"}

        from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR

        bridge_state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR, None)
        if bridge_state is None:
            return {
                "error": (
                    "no Bridge state attached to this visitor — "
                    "delegate_to_ia requires Bridge orchestration"
                )
            }

        # Helm name is fixed for ReasoningHelm. The slot is shared with
        # the existing DELEGATE-chain mechanism (BRIDGE-ROADMAP §C-6).
        slot = bridge_state.helm_states.setdefault("ReasoningHelm", {})
        pending = list(slot.get("pending_ias") or [])
        pending.append(target_name)
        slot["pending_ias"] = pending

        # Terminate the engine loop so the helm can return DELEGATE to
        # Bridge on the next visit. Without this, the engine keeps
        # iterating until max_iterations or final_response.
        session = get_session(visitor)
        session.finalized = True

        logger.info(
            "delegate_to_ia: engine queued DELEGATE to %r "
            "(pending_ias=%s, finalized=True)",
            target_name,
            slot["pending_ias"],
        )
        return {"ok": True, "delegated_to": target_name}

    description = (
        "Invoke an installed interact action by name when the user's intent "
        "matches an installed flow but the dispatch wasn't routed automatically. "
        "Recovery hatch — prefer to answer from your own tools when you can. "
        "Calling this tool ends the current engine turn; the named flow runs "
        "on the next walker visit.\n\n"
        f"Available flows:\n{descriptions}"
    )

    return [
        Tool(
            name="delegate_to_ia",
            description=description,
            parameters_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Exact class name of the interact action to invoke. "
                            "Must be one of the listed available flows."
                        ),
                    },
                },
                "required": ["name"],
            },
            execute=_execute,
        ),
    ]


__all__ = ["_build_delegate_to_ia_tools", "_enumerate_eligible_ias"]
