"""Action CRUD endpoints for managing actions via RESTful API.

This module provides endpoints for:
- Getting action details
- Updating actions (enabled status, description, properties)
- Listing actions for an agent
- Enabling/disabling actions
- Reloading actions
- Checking action health
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.core.pager import ObjectPager

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}",
    methods=["GET"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(
                field_type=Dict[str, Any],
                description="Action information",
                example={
                    "id": "action_123",
                    "agent_id": "agent_456",
                    "namespace": "jvagent",
                    "label": "example_action",
                    "description": "Example action",
                    "enabled": True,
                },
            )
        }
    ),
)
async def get_action(action_id: str) -> Dict[str, Any]:
    """Get a specific action by ID.

    Retrieves full action information including:


    - **Identity**: namespace, label, description
    - **Status**: enabled/disabled
    - **Configuration and metadata**
    - **Package information**: version, type


    The action ID follows the format: `n.{ActionType}.{unique_id}`

    Example: `n.ExampleAction.abc123`, `n.OpenAILanguageModelAction.xyz789`


    **Args:**

    - action_id: ID of the action to retrieve


    **Returns:**

    Dictionary with complete action information


    **Raises:**

    - ResourceNotFoundError: If action not found
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    return {"action": await action.export()}


@endpoint(
    "/actions/{action_id}",
    methods=["PUT"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(
                field_type=Dict[str, Any],
                description="Updated action information",
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Action updated successfully",
            ),
            "update_result": ResponseField(
                field_type=Dict[str, Any],
                description="Update operation result",
                example={
                    "success": True,
                    "updated": {"var_a": 60, "var_b": 10},
                    "skipped": {"invalid_field": "invalid_property"},
                    "message": "Partially updated: 2 succeeded, 1 skipped",
                },
            ),
        }
    ),
)
async def update_action(
    action_id: str,
    enabled: Optional[bool] = None,
    description: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update an action.

    Uses `Action.enable()`, `Action.disable()` methods for lifecycle management.
    Custom properties can be updated via the properties parameter.


    **Args:**

    - action_id: ID of the action to update
    - enabled: Whether the action should be enabled
    - description: New description
    - properties: Dictionary of property names to values for runtime updates


    **Returns:**

    Dictionary with updated action information


    **Raises:**

    - ResourceNotFoundError: If action not found


    **Example Request Body:**

    ```json
    {
        "enabled": true,
        "description": "Updated action description",
        "properties": {
            "var_a": 60,
            "var_b": 10,
            "timeout": 45,
            "retries": 5
        }
    }
    ```

    Or update properties directly (for ExampleAction):

    ```json
    {
        "var_a": 60,
        "var_b": 10
    }
    ```
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    # Track all updates
    updated_fields: Dict[str, Any] = {}
    skipped_fields: Dict[str, str] = {}
    needs_save = False

    # Update enabled status using Action methods
    if enabled is not None:
        if enabled != action.enabled:
            if enabled:
                await action.enable()
            else:
                await action.disable()
            updated_fields["enabled"] = enabled
            needs_save = True

    # Update description
    if description is not None:
        if description != action.description:
            action.description = description
            updated_fields["description"] = description
            needs_save = True

    # Update custom properties (runtime configuration changes)
    # Use entity-centric update() inherited from Object - works correctly for ExampleAction
    properties_result = None
    if properties:
        # Call update() directly on the entity instance
        # This inherits from Object.update() and correctly uses ExampleAction's class hierarchy
        properties_result = await action.update(
            properties, skip_protected=True, skip_private=True
        )

        # Merge properties update results
        if properties_result["updated"]:
            updated_fields.update(properties_result["updated"])
            needs_save = True

        if properties_result["skipped"]:
            skipped_fields.update(properties_result["skipped"])

            # Log any skipped properties
            for prop_name, reason in properties_result["skipped"].items():
                logger.warning(f"Could not update property '{prop_name}': {reason}")

    # Save if any updates were made and trigger lifecycle hooks
    if needs_save:
        await action.save()
        # Trigger reload hook if action is already registered
        await action.on_reload()

    # Build combined update result
    has_updates = len(updated_fields) > 0
    has_skipped = len(skipped_fields) > 0

    if has_updates and not has_skipped:
        message = f"Successfully updated {len(updated_fields)} field(s)"
    elif has_updates and has_skipped:
        message = f"Partially updated: {len(updated_fields)} succeeded, {len(skipped_fields)} skipped"
    elif has_skipped:
        message = f"Update failed: {len(skipped_fields)} field(s) skipped"
    else:
        message = "No changes to apply"

    update_result = {
        "success": has_updates,
        "updated": updated_fields,
        "skipped": skipped_fields,
        "message": message,
    }

    return {
        "action": await action.export(),
        "message": message,
        "update_result": update_result,
    }


@endpoint(
    "/agents/{agent_id}/actions",
    methods=["GET"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "actions": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of actions",
            ),
            "total": ResponseField(
                field_type=int,
                description="Total number of actions",
                example=100,
            ),
            "page": ResponseField(
                field_type=int,
                description="Current page number",
                example=1,
            ),
            "per_page": ResponseField(
                field_type=int,
                description="Number of actions per page",
                example=10,
            ),
            "total_pages": ResponseField(
                field_type=int,
                description="Total number of pages",
                example=10,
            ),
            "has_previous": ResponseField(
                field_type=bool,
                description="Whether there's a previous page",
                example=False,
            ),
            "has_next": ResponseField(
                field_type=bool,
                description="Whether there's a next page",
                example=True,
            ),
            "previous_page": ResponseField(
                field_type=Optional[int],  # type: ignore[arg-type]
                description="Previous page number",
                example=None,
            ),
            "next_page": ResponseField(
                field_type=Optional[int],  # type: ignore[arg-type]
                description="Next page number",
                example=2,
            ),
        }
    ),
)
async def list_agent_actions(
    agent_id: str,
    page: int = 1,
    per_page: int = 10,
    enabled_only: bool = False,
) -> Dict[str, Any]:
    """List actions for an agent using entity-centric pagination.

    Uses ObjectPager which automatically performs class-aware queries that include
    all Action subclasses (e.g., ExampleAction) through database-driven class discovery.
    This ensures dynamically loaded action classes are found even if not yet imported.


    **Args:**

    - agent_id: ID of the agent
    - page: Page number (1-based)
    - per_page: Items per page
    - enabled_only: Only return enabled actions


    **Returns:**

    Dictionary with paginated list of actions and pagination metadata
    """
    # Build entity-centric filters
    filters = {"context.agent_id": agent_id}
    if enabled_only:
        filters["context.enabled"] = True

    # ObjectPager uses _build_database_query_async with enable_class_discovery=True
    # This automatically discovers and includes all Action subclasses from the database
    pager = ObjectPager(Action, page_size=per_page, filters=filters)
    actions = await pager.get_page(page=page)

    # Convert to dicts
    actions_list = [await a.export() for a in actions]

    # Get pagination info
    pagination_info = pager.to_dict()

    return {
        "actions": actions_list,
        "total": pagination_info["total_items"],
        "page": pagination_info["current_page"],
        "per_page": pagination_info["page_size"],
        "total_pages": pagination_info["total_pages"],
        "has_previous": pagination_info["has_previous"],
        "has_next": pagination_info["has_next"],
        "previous_page": pagination_info["previous_page"],
        "next_page": pagination_info["next_page"],
    }


@endpoint(
    "/actions/{action_id}/enable",
    methods=["POST"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(
                field_type=Dict[str, Any], description="Action information"
            ),
            "message": ResponseField(field_type=str, description="Success message"),
        }
    ),
)
async def enable_action_endpoint(action_id: str) -> Dict[str, Any]:
    """Enable an action using `Action.enable()` method.


    **Args:**

    - action_id: ID of the action to enable


    **Returns:**

    Dictionary with updated action information
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    await action.enable()

    return {
        "action": await action.to_dict(),
        "message": "Action enabled successfully",
    }


@endpoint(
    "/actions/{action_id}/disable",
    methods=["POST"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(
                field_type=Dict[str, Any], description="Action information"
            ),
            "message": ResponseField(field_type=str, description="Success message"),
        }
    ),
)
async def disable_action_endpoint(action_id: str) -> Dict[str, Any]:
    """Disable an action using `Action.disable()` method.


    **Args:**

    - action_id: ID of the action to disable


    **Returns:**

    Dictionary with updated action information
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    await action.disable()

    return {
        "action": await action.to_dict(),
        "message": "Action disabled successfully",
    }


@endpoint(
    "/actions/{action_id}/reload",
    methods=["POST"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(
                field_type=Dict[str, Any], description="Action information"
            ),
            "message": ResponseField(field_type=str, description="Success message"),
        }
    ),
)
async def reload_action_endpoint(action_id: str) -> Dict[str, Any]:
    """Reload an action using `Action.reload()` method.


    **Args:**

    - action_id: ID of the action to reload


    **Returns:**

    Dictionary with action information
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    await action.reload()

    return {
        "action": await action.to_dict(),
        "message": "Action reloaded successfully",
    }


@endpoint(
    "/actions/{action_id}/health",
    methods=["GET"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "health": ResponseField(
                field_type=Dict[str, Any], description="Health information"
            ),
        }
    ),
)
async def check_action_health(action_id: str) -> Dict[str, Any]:
    """Check action health using `Action.healthcheck()` method.


    **Args:**

    - action_id: ID of the action to check


    **Returns:**

    Dictionary with health information
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    health = await action.healthcheck()

    # Normalize result
    if isinstance(health, bool):
        health = {"healthy": health}
    elif not isinstance(health, dict):
        health = {"healthy": True, "result": health}

    return {"health": health}
