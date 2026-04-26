"""HTTP endpoints for PricingAction CRUD and assessment."""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from .pricing_action import PricingAction, PricingRubricNode

logger = logging.getLogger(__name__)


async def _get_pricing_action() -> PricingAction:
    """Resolve the PricingAction instance from the graph."""
    from jvagent.core.app import App

    app = await App.get()
    if not app:
        raise ResourceNotFoundError(message="App node not found")

    # Find PricingAction through any agent that has it
    action = await PricingAction.find_one()
    if not action:
        raise ResourceNotFoundError(
            message="PricingAction not found. Ensure it is registered on an agent."
        )
    return action


# ── List Rubrics ─────────────────────────────


@endpoint(
    "/pricing/rubrics",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Pricing"],
    summary="List all pricing rubrics",
    description="Returns all active pricing rubrics (or all if active_only=false).",
)
async def pricing_list_rubrics(active_only: bool = True) -> Dict[str, Any]:
    action = await _get_pricing_action()
    rubrics = await action.list_rubrics(active_only=active_only)
    return {"success": True, "rubrics": rubrics}


# ── Get Rubric ───────────────────────────────


@endpoint(
    "/pricing/rubrics/{name}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Pricing"],
    summary="Get a pricing rubric by name",
)
async def pricing_get_rubric(name: str) -> Dict[str, Any]:
    action = await _get_pricing_action()
    rubric = await action.get_rubric(name)
    if not rubric:
        raise ResourceNotFoundError(message=f"Rubric '{name}' not found")
    d = await rubric.to_dict()
    return {"success": True, "rubric": d}


# ── Create Rubric ────────────────────────────


@endpoint(
    "/pricing/rubrics",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Pricing"],
    summary="Create a new pricing rubric",
    description="Creates a new pricing rubric from JSON body. Name must be unique.",
)
async def pricing_create_rubric(data: Dict[str, Any]) -> Dict[str, Any]:
    action = await _get_pricing_action()
    try:
        rubric = await action.create_rubric(data)
    except ValueError as e:
        raise ValidationError(message=str(e))
    return {"success": True, "rubric": rubric}


# ── Update Rubric ────────────────────────────


@endpoint(
    "/pricing/rubrics/{name}",
    methods=["PUT"],
    auth=True,
    roles=["admin"],
    tags=["Pricing"],
    summary="Update a pricing rubric",
    description="Partial update of an existing rubric. Only provided fields are changed.",
)
async def pricing_update_rubric(name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    action = await _get_pricing_action()
    rubric = await action.update_rubric(name, data)
    if not rubric:
        raise ResourceNotFoundError(message=f"Rubric '{name}' not found")
    return {"success": True, "rubric": rubric}


# ── Delete Rubric ────────────────────────────


@endpoint(
    "/pricing/rubrics/{name}",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Pricing"],
    summary="Soft-delete a pricing rubric",
    description="Marks the rubric as inactive (soft delete).",
)
async def pricing_delete_rubric(name: str) -> Dict[str, Any]:
    action = await _get_pricing_action()
    deleted = await action.delete_rubric(name)
    if not deleted:
        raise ResourceNotFoundError(message=f"Rubric '{name}' not found")
    return {"success": True}


# ── Activate Rubric ──────────────────────────


@endpoint(
    "/pricing/rubrics/{name}/activate",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Pricing"],
    summary="Set the active rubric",
    description="Sets the named rubric as the active default.",
)
async def pricing_activate_rubric(name: str) -> Dict[str, Any]:
    action = await _get_pricing_action()
    rubric = await action.get_rubric(name)
    if not rubric:
        raise ResourceNotFoundError(message=f"Rubric '{name}' not found")
    action.active_rubric = name
    await action.save()
    return {"success": True, "active_rubric": name}


# ── Assess ───────────────────────────────────


@endpoint(
    "/pricing/assess",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Pricing"],
    summary="Compute a pricing assessment",
    description=(
        "Applies the named rubric (or active rubric) to the provided scope parameters "
        "and returns a full PricingAssessment with line items, totals, and assumptions."
    ),
)
async def pricing_assess(
    data: Dict[str, Any],
) -> Dict[str, Any]:
    action = await _get_pricing_action()
    rubric_name = data.get("rubric_name") or action.active_rubric
    scope_params = data.get("scope_params", {})
    try:
        assessment = await action.assess(rubric_name, scope_params)
    except ValueError as e:
        raise ValidationError(message=str(e))
    return {"success": True, "assessment": assessment}
