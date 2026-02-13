"""API endpoints for ResolvAPIAction.

This module defines the HTTP endpoints for interacting with the Resolv platform
via the ResolvAPIAction.
"""

import logging
from typing import Any, Dict, List, Optional, Union

from jvspatial.api import endpoint
from jvspatial.api.decorators import EndpointField
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from .resolv_api_action import ResolvAPIAction

logger = logging.getLogger(__name__)

# --- Contact Groups ---

@endpoint(
    "/actions/{action_id}/contact-groups",
    methods=["GET"],
    auth=True,
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "groups": ResponseField(field_type=List[Dict[str, Any]], description="List of contact groups")
        }
    )
)
async def get_contact_groups(action_id: str, project_groups: bool = True) -> Dict[str, Any]:
    """Retrieve all contact groups for the organization."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
    groups = await action.get_contact_groups(project_groups=project_groups)
    return {"groups": groups}

@endpoint(
    "/actions/{action_id}/contact-groups/{group_id}/subscribe",
    methods=["POST"],
    auth=True,
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "success": ResponseField(field_type=bool, description="Whether the subscription was successful")
        }
    )
)
async def subscribe_contact_to_group(action_id: str, group_id: int, contact_id: int) -> Dict[str, Any]:
    """Add a contact to a contact group."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
    success = await action.subscribe_contact_to_group(contact_id=contact_id, group_id=group_id)
    return {"success": success}

@endpoint(
    "/actions/{action_id}/contact-groups/{group_id}/unsubscribe",
    methods=["POST"],
    auth=True,
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "success": ResponseField(field_type=bool, description="Whether the unsubscription was successful")
        }
    )
)
async def unsubscribe_contact_from_group(action_id: str, group_id: int, contact_id: int) -> Dict[str, Any]:
    """Remove a contact from a contact group."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
    success = await action.unsubscribe_contact_from_group(contact_id=contact_id, group_id=group_id)
    return {"success": success}
# --- Issues ---

# @endpoint(
#     "/actions/{action_id}/issues",
#     methods=["POST"],
#     auth=True,
#     tags=["ResolvAPIAction"],
#     response=success_response(
#         data={
#             "issue": ResponseField(field_type=Dict[str, Any], description="The created issue")
#         }
#     )
# )
@endpoint(
    "/actions/{action_id}/issues",
    methods=["POST"],
    auth=True,
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "issue": ResponseField(field_type=Dict[str, Any], description="The created issue")
        }
    )
)
async def create_issue(
    action_id: str,
    title: str = EndpointField(
        default="Report",
        description="Title of the issue",
        examples=["Bug Report", "Feature Request", "Support Issue"]
    ),
    is_anonymous: bool = EndpointField(
        default=False,
        description="Whether the issue is submitted anonymously"
    ),
    description: Optional[str] = EndpointField(
        default=None,
        description="Detailed description of the issue"
    ),
    original_description: Optional[str] = EndpointField(
        default=None,
        description="Original description before any processing"
    ),
    priority: str = EndpointField(
        default="medium",
        description="Priority level of the issue",
        examples=["low", "medium", "high", "critical"]
    ),
    category_id: Optional[int] = EndpointField(
        default=None,
        description="ID of the issue category"
    ),
    reported_by_contact_id: Optional[int] = EndpointField(
        default=None,
        description="Contact ID of the person reporting the issue"
    ),
    reported_for_contact_id: Optional[int] = EndpointField(
        default=None,
        description="Contact ID of the person the issue is reported for"
    ),
    expected_resolution_date: Optional[str] = EndpointField(
        default=None,
        description="Expected date for issue resolution (ISO format)"
    ),
    ai_overview: Optional[str] = EndpointField(
        default=None,
        description="AI-generated overview of the issue"
    )
) -> Dict[str, Any]:
    """Create a new issue in the project."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
    issue = await action.create_issue(
        title=title,
        is_anonymous=is_anonymous,
        description=description,
        original_description=original_description,
        priority=priority,
        category_id=category_id,
        reported_by_contact_id=reported_by_contact_id,
        reported_for_contact_id=reported_for_contact_id,
        expected_resolution_date=expected_resolution_date,
        ai_overview=ai_overview
    )
    return {"issue": issue}

# async def create_issue(action_id: str, **kwargs) -> Dict[str, Any]:
#     """Create a new issue in the project."""
#     action = await ResolvAPIAction.get(action_id)
#     if not action:
#         raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
#     issue = await action.create_issue(**kwargs)
#     return {"issue": issue}

@endpoint(
    "/actions/{action_id}/issues",
    methods=["GET"],
    auth=True,
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "issues": ResponseField(field_type=List[Dict[str, Any]], description="List of issues")
        }
    )
)
async def list_issues(action_id: str, query: str = "") -> Dict[str, Any]:
    """List issues for the project."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
    issues = await action.list_issues(query=query)
    return {"issues": issues}

@endpoint(
    "/actions/{action_id}/issues/{issue_id}",
    methods=["GET"],
    auth=True,
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "issue": ResponseField(field_type=Dict[str, Any], description="The detailed issue")
        }
    )
)
async def get_issue(action_id: str, issue_id: str) -> Dict[str, Any]:
    """Retrieve detailed information about a specific issue."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
    issue = await action.get_issue_by_id(issue_id=issue_id)
    return {"issue": issue}

# --- Contacts ---

@endpoint(
    "/actions/{action_id}/contacts/lookup",
    methods=["GET"],
    auth=True,
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "contact": ResponseField(field_type=Dict[str, Any], description="The contact information")
        }
    )
)
async def lookup_contact(action_id: str, phone: str, name: str = "", email: str = "") -> Dict[str, Any]:
    """Get or create a contact by phone number."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
    contact = await action.get_contact_by_phone(phone=phone, name=name, email=email)
    return {"contact": contact}

# --- Notices ---

@endpoint(
    "/actions/{action_id}/notices",
    methods=["GET"],
    auth=True,
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "notices": ResponseField(field_type=List[Dict[str, Any]], description="List of notices")
        }
    )
)
async def list_notices(action_id: str, status: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve all notices for the organization."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")
    
    notices = await action.get_all_notices(status=status)
    return {"notices": notices}
