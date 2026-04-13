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
    roles=["admin"],
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
    roles=["admin"],
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
    roles=["admin"],
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

@endpoint(
    "/actions/{action_id}/issues",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "issue": ResponseField(field_type=Dict[str, Any], description="The created issue")
        }
    )
)
async def create_issue(
    action_id: str,
    is_anonymous: bool = EndpointField(
        default=False,
        description="Whether the issue is submitted anonymously"
    ),
    original_description: Optional[str] = EndpointField(
        default=None,
        description="Original description before any processing"
    ),
    reported_by_contact_id: Optional[int] = EndpointField(
        default=None,
        description="Contact ID of the person reporting the issue"
    ),
    reported_for_contact_id: Optional[int] = EndpointField(
        default=None,
        description="Contact ID of the person the issue is reported for"
    )
) -> Dict[str, Any]:
    """Create a new issue in the project."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    issue = await action.create_issue(
        is_anonymous=is_anonymous,
        original_description=original_description,
        reported_by_contact_id=reported_by_contact_id,
        reported_for_contact_id=reported_for_contact_id
    )
    return {"issue": issue}

@endpoint(
    "/actions/{action_id}/issues/{issue_id}",
    methods=["PUT"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "success": ResponseField(field_type=bool, description="Whether the update was successful")
        }
    )
)
async def update_issue(
    action_id: str,
    issue_id: int,
    title: Optional[str] = EndpointField(default=None, description="New title"),
    description: Optional[str] = EndpointField(default=None, description="New description"),
    priority: Optional[str] = EndpointField(default=None, description="New priority"),
    status: Optional[str] = EndpointField(default=None, description="New status"),
    category_id: Optional[int] = EndpointField(default=None, description="New category ID"),
    expected_resolution_date: Optional[str] = EndpointField(default=None, description="New expected resolution date")
) -> Dict[str, Any]:
    """Update an existing issue."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    success = await action.update_issue(
        issue_id=issue_id,
        title=title,
        description=description,
        priority=priority,
        status=status,
        category_id=category_id,
        expected_resolution_date=expected_resolution_date
    )
    return {"success": success}

@endpoint(
    "/actions/{action_id}/issues",
    methods=["GET"],
    auth=True,
    roles=["admin"],
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
    roles=["admin"],
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

@endpoint(
    "/actions/{action_id}/issue-categories",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "categories": ResponseField(field_type=List[Dict[str, Any]], description="List of issue categories")
        }
    )
)
async def get_issue_categories(action_id: str) -> Dict[str, Any]:
    """Retrieve all issue categories for the organization."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    categories = await action.get_issue_categories()
    return {"categories": categories}

# --- Contacts ---

@endpoint(
    "/actions/{action_id}/contacts/lookup",
    methods=["GET"],
    auth=True,
    roles=["admin"],
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

@endpoint(
    "/actions/{action_id}/contacts",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "contact": ResponseField(field_type=Dict[str, Any], description="The created contact response")
        }
    )
)
async def create_contact(action_id: str, name: str, phone: str, email: str) -> Dict[str, Any]:
    """Create a new contact."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    contact = await action.create_contact(name=name, phone=phone, email=email)
    return {"contact": contact}

@endpoint(
    "/actions/{action_id}/contacts/{contact_id}",
    methods=["PUT"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "success": ResponseField(field_type=bool, description="Whether the update was successful")
        }
    )
)
async def update_contact(action_id: str, contact_id: int, name: str, phone: str, email: str) -> Dict[str, Any]:
    """Update an existing contact."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    success = await action.update_contact(contact_id=contact_id, name=name, phone=phone, email=email)
    return {"success": success}

@endpoint(
    "/actions/{action_id}/contacts/subscription-link",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "url": ResponseField(field_type=str, description="The subscription page URL")
        }
    )
)
async def get_subscription_link(action_id: str, phone: str, name: str) -> Dict[str, Any]:
    """Get a subscription channels page URL for a contact."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    url = await action.get_channels_page(phone_number=phone, name=name)
    return {"url": url}

# --- Notices ---

@endpoint(
    "/actions/{action_id}/notices",
    methods=["GET"],
    auth=True,
    roles=["admin"],
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

# --- Projects ---

@endpoint(
    "/actions/{action_id}/projects",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "projects": ResponseField(field_type=List[Dict[str, Any]], description="List of projects")
        }
    )
)
async def list_projects(action_id: str, query: str = "") -> Dict[str, Any]:
    """Get projects for the organization."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    projects = await action.get_projects(query=query)
    return {"projects": projects}

# --- Comments ---

@endpoint(
    "/actions/{action_id}/projects/{project_id}/comments",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "comment": ResponseField(field_type=Dict[str, Any], description="The comment response")
        }
    )
)
async def post_project_comment(action_id: str, project_id: str, content: str) -> Dict[str, Any]:
    """Post a comment on a project."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    comment = await action.post_project_comment(project_id=project_id, content=content)
    return {"comment": comment}

@endpoint(
    "/actions/{action_id}/projects/{project_id}/issues/{issue_id}/comments",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "comment": ResponseField(field_type=Dict[str, Any], description="The comment response")
        }
    )
)
async def post_issue_comment(action_id: str, project_id: str, issue_id: str, content: str) -> Dict[str, Any]:
    """Post a comment on an issue."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    comment = await action.post_issue_comment(project_id=project_id, issue_id=issue_id, content=content)
    return {"comment": comment}

@endpoint(
    "/actions/{action_id}/comments",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["ResolvAPIAction"],
    response=success_response(
        data={
            "result": ResponseField(field_type=Dict[str, Any], description="The submission result")
        }
    )
)
async def submit_comment(
    action_id: str,
    content: str,
    report_id: str = "",
    attachments: List[str] = EndpointField(default_factory=list, description="List of attachment URLs")
) -> Dict[str, Any]:
    """Submit a comment to a project or issue with optional attachments."""
    action = await ResolvAPIAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(message=f"Action {action_id} not found")

    result = await action.submit_comment(content=content, report_id=report_id, attachments=attachments)
    return {"result": result}
