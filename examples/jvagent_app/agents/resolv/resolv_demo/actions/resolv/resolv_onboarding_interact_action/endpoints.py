"""API endpoints for resolv onboarding interact action.

This module defines all HTTP endpoints for the resolv onboarding interact action.
Endpoints are automatically discovered when this module is imported.
"""

import logging
from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.api.endpoints.response import ResponseField, success_response

from .resolv_onboarding_interact_action import ResolvOnboardingInteractAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/get_contact_groups",
    methods=["GET"],
    auth=True,
    tags=["ResolvOnboardingInteractAction"],
    response=success_response(
        data={
            "success": ResponseField(field_type=bool, example=True),
            "data": ResponseField(field_type=list, example=[]),
            "message": ResponseField(field_type=str, example="Contact groups retrieved successfully"),
            "details": ResponseField(field_type=Optional[Dict[str, Any]], example=None),
        }
    )
)
async def endpoint_get_contact_groups(action_id: str) -> dict[str, Any]:
    """Get contact groups."""
    try:
        action = await ResolvOnboardingInteractAction.get(action_id)
        if not action:
            return {
                "success": False,
                "message": f"ResolvOnboardingInteractAction with ID '{action_id}' not found",
                "details": {"action_id": action_id}
            }
        
        groups = await action.get_contact_groups()
        if groups is None:
            return {
                "success": False,
                "message": "Failed to retrieve contact groups",
                "details": {"error": "No contact groups found"}
            }
        else:
            return {
                "success": True,
                "data": groups,
                "message": "Contact groups retrieved successfully",
                "details": None
            }
    except Exception as e:
        return {
            "success": False,
            "message": "Failed to retrieve contact groups",
            "details": {"error": str(e)}
        }


@endpoint(
    "/actions/{action_id}/update_default_contact_groups",
    methods=["POST"],
    auth=True,
    tags=["ResolvOnboardingInteractAction"],
    response=success_response(
        data={
            "success": ResponseField(field_type=bool, example=True),
            "data": ResponseField(field_type=list[str], example=[]),
            "message": ResponseField(field_type=str, example="Contact groups updated successfully"),
            "details": ResponseField(field_type=Optional[Dict[str, Any]], example=None),
        }
    )
)
async def update_default_contact_groups(action_id: str, group: list[str]) -> dict[str, Any]:
    """Update default contact groups."""
    try:
        action = await ResolvOnboardingInteractAction.get(action_id)
        if not action:
            return {
                "success": False,
                "message": f"ResolvOnboardingInteractAction with ID '{action_id}' not found",
                "details": {"action_id": action_id}
            }
        
        result = await action.update_default_contact_groups(group)
        logger.warning("result")
        logger.warning(result)
        return {
            "success": True,
            "data": group,
            "message": "Default contact groups updated successfully",
            "details": None
        }
    except Exception as e:
        return {
            "success": False,
            "message": "Failed to update default contact groups",
            "details": {"error": str(e)}
        }