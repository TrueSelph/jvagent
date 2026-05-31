"""Per-category ``info.yaml`` scaffolding for new InteractActions.

This module turns "which category am I?" into a concrete ``info.yaml``
manifest payload. A future ``jvagent action create --type interact_action``
subcommand can drive an interactive prompt over this surface; for Wave 9
we ship the category templates and the dict-shape helper so the
authoring workflow is testable today.

Six categories:

1. **anchor_routable** (default) — exposed as an IA-as-tool when intent matches.
   Requires 3-5 anchor phrases.
2. **chain_internal** — invoked only via DELEGATE from a parent IA.
3. **always_execute** — runs on every turn (intro, audit, telemetry).
4. **synchronous** — engine tool returning a value to the model loop.
5. **pattern_orchestrator** — weight-routed orchestrator (Orchestrator).
6. **multi_turn_flow** — turn-spanning flow (interview, signup); records a
   control-task on the conversation TaskStore while active.

Each template produces a manifest dict that can be merged into a fresh
``info.yaml``. Categories that exclude themselves from routing set the
relevant flags so the bootstrap warning (loader anchor-authoring check)
does not fire spuriously.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Category enumeration
# ---------------------------------------------------------------------------

CATEGORY_ANCHOR_ROUTABLE = "anchor_routable"
CATEGORY_CHAIN_INTERNAL = "chain_internal"
CATEGORY_ALWAYS_EXECUTE = "always_execute"
CATEGORY_SYNCHRONOUS = "synchronous"
CATEGORY_PATTERN_ORCHESTRATOR = "pattern_orchestrator"
CATEGORY_MULTI_TURN_FLOW = "multi_turn_flow"

VALID_CATEGORIES = (
    CATEGORY_ANCHOR_ROUTABLE,
    CATEGORY_CHAIN_INTERNAL,
    CATEGORY_ALWAYS_EXECUTE,
    CATEGORY_SYNCHRONOUS,
    CATEGORY_PATTERN_ORCHESTRATOR,
    CATEGORY_MULTI_TURN_FLOW,
)


@dataclass(frozen=True)
class CategorySpec:
    """Static metadata about an IA category — used by interactive prompts."""

    key: str
    label: str
    description: str
    requires_anchors: bool
    requires_pattern_orchestrator_confirmation: bool


CATEGORY_SPECS: Dict[str, CategorySpec] = {
    CATEGORY_ANCHOR_ROUTABLE: CategorySpec(
        key=CATEGORY_ANCHOR_ROUTABLE,
        label="Anchor-routable (default)",
        description=(
            "Exposed as an IA-as-tool when user intent matches anchors. "
            "Requires 3-5 anchor phrases."
        ),
        requires_anchors=True,
        requires_pattern_orchestrator_confirmation=False,
    ),
    CATEGORY_CHAIN_INTERNAL: CategorySpec(
        key=CATEGORY_CHAIN_INTERNAL,
        label="Chain-internal",
        description="Invoked only via DELEGATE from another IA.",
        requires_anchors=False,
        requires_pattern_orchestrator_confirmation=False,
    ),
    CATEGORY_ALWAYS_EXECUTE: CategorySpec(
        key=CATEGORY_ALWAYS_EXECUTE,
        label="Always-execute (sidecar)",
        description="Runs on every turn (intro, audit, telemetry).",
        requires_anchors=False,
        requires_pattern_orchestrator_confirmation=False,
    ),
    CATEGORY_SYNCHRONOUS: CategorySpec(
        key=CATEGORY_SYNCHRONOUS,
        label="Synchronous (engine tool)",
        description="Returns a value to the engine's tool-call loop.",
        requires_anchors=False,
        requires_pattern_orchestrator_confirmation=False,
    ),
    CATEGORY_PATTERN_ORCHESTRATOR: CategorySpec(
        key=CATEGORY_PATTERN_ORCHESTRATOR,
        label="Pattern orchestrator",
        description=(
            "Runs by walker weight (Orchestrator). Only one orchestrator "
            "per agent. Requires explicit confirmation — this is rare."
        ),
        requires_anchors=False,
        requires_pattern_orchestrator_confirmation=True,
    ),
    CATEGORY_MULTI_TURN_FLOW: CategorySpec(
        key=CATEGORY_MULTI_TURN_FLOW,
        label="Multi-turn flow",
        description=(
            "Turn-spanning flow (interview, signup). Records a control-task "
            "while active; continued when the orchestrator selects its tool."
        ),
        requires_anchors=False,
        requires_pattern_orchestrator_confirmation=False,
    ),
}


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest_payload(
    category: str,
    *,
    purpose: str,
    anchors: Optional[List[str]] = None,
    latency_class: str = "quick",
    return_value_description: Optional[str] = None,
) -> Dict[str, Any]:
    """Return an ``info.yaml`` ``manifest:`` dict for the given category.

    Validation:

    - ``category`` must be one of :data:`VALID_CATEGORIES`.
    - ``anchor_routable`` requires ``anchors`` with at least 3 entries.
    - ``synchronous`` requires ``return_value_description``.

    Authoring conventions:

    - ``anchor_routable`` — default; ``routable_by_anchor: true`` (omitted
      since it is the default).
    - ``chain_internal`` — sets ``routable_by_anchor: false``.
    - ``always_execute`` — the class-level ``always_execute=True`` flag
      handles dispatch; the manifest only contributes ``purpose`` and
      ``latency_class``. ``routable_by_anchor`` is set to ``false`` for
      defensive clarity.
    - ``synchronous`` — ``routable_by_anchor: false``. The description
      embeds the return-value contract since that is what the engine
      shows the model in the tool description.
    - ``pattern_orchestrator`` — ``pattern_orchestrator: true`` and
      ``routable_by_anchor: false``.
    - ``multi_turn_flow`` — ``latency_class: deliberate`` plus optional
      ``activates_on`` for first-entry routing.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"unknown category {category!r}; valid choices: "
            f"{sorted(VALID_CATEGORIES)}"
        )

    payload: Dict[str, Any] = {
        "purpose": purpose,
        "latency_class": latency_class,
    }

    if category == CATEGORY_ANCHOR_ROUTABLE:
        anchor_list = [a for a in (anchors or []) if isinstance(a, str) and a.strip()]
        if len(anchor_list) < 3:
            raise ValueError(
                "anchor_routable IAs require at least 3 anchor phrases; "
                f"got {len(anchor_list)}"
            )
        payload["activates_on"] = anchor_list
        # routable_by_anchor default is True, so omit.

    elif category == CATEGORY_CHAIN_INTERNAL:
        payload["routable_by_anchor"] = False

    elif category == CATEGORY_ALWAYS_EXECUTE:
        payload["routable_by_anchor"] = False

    elif category == CATEGORY_SYNCHRONOUS:
        if not return_value_description:
            raise ValueError(
                "synchronous IAs require a return_value_description so the "
                "engine's tool surface can describe what the model gets back"
            )
        payload["routable_by_anchor"] = False
        payload["purpose"] = (
            f"{purpose.rstrip()}\n\nReturns: {return_value_description.strip()}"
        )

    elif category == CATEGORY_PATTERN_ORCHESTRATOR:
        payload["pattern_orchestrator"] = True
        payload["routable_by_anchor"] = False

    elif category == CATEGORY_MULTI_TURN_FLOW:
        payload["latency_class"] = "deliberate"
        anchor_list = [a for a in (anchors or []) if isinstance(a, str) and a.strip()]
        if anchor_list:
            payload["activates_on"] = anchor_list

    return payload


__all__ = [
    "CATEGORY_ANCHOR_ROUTABLE",
    "CATEGORY_CHAIN_INTERNAL",
    "CATEGORY_ALWAYS_EXECUTE",
    "CATEGORY_SYNCHRONOUS",
    "CATEGORY_PATTERN_ORCHESTRATOR",
    "CATEGORY_MULTI_TURN_FLOW",
    "CATEGORY_SPECS",
    "CategorySpec",
    "VALID_CATEGORIES",
    "build_manifest_payload",
]
