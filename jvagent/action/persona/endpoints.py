"""API endpoints for PersonaAction.

This module provides REST API endpoints for PersonaAction interactions
and parameter management.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

logger = logging.getLogger(__name__)


# NOTE: The /actions/{action_id}/interact endpoint has been removed.
# Use /agents/{agent_id}/interact instead, which uses the InteractWalker
# to traverse InteractActions. PersonaAction is now a tool-based action
# and does not participate in the interact subsystem directly.


@endpoint(
    "/actions/{action_id}/parameters",
    methods=["GET"],
    auth=True,
    tags=["Persona Action"],
    response=success_response(
        data={
            "parameters": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of parameters",
                example=[
                    {
                        "id": "param_1",
                        "condition": "User asks for help",
                        "response": "Offer assistance politely",
                        "action": None,
                        "enabled": True,
                    }
                ],
            ),
            "count": ResponseField(
                field_type=int,
                description="Number of parameters",
                example=5,
            ),
        }
    ),
)
async def list_parameters_endpoint(
    action_id: str,
    enabled_only: bool = True,
) -> Dict[str, Any]:
    """List all parameters for a PersonaAction.


    **Args:**

    - action_id: ID of the PersonaAction
    - enabled_only: If True, only return enabled parameters


    **Returns:**

    Dictionary with parameters list and count
    """
    from jvagent.action.persona.persona_action import PersonaAction

    action = await PersonaAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"PersonaAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    if not isinstance(action, PersonaAction):
        raise ValidationError(
            message=f"Action '{action_id}' is not a PersonaAction",
            details={"action_id": action_id},
        )

    # Get parameters from the action's parameters attribute
    parameters = action.parameters or []

    # Filter by enabled if requested (parameters may have 'enabled' key)
    if enabled_only:
        parameters = [p for p in parameters if p.get("enabled", True)]

    return {
        "parameters": parameters,
        "count": len(parameters),
    }


@endpoint(
    "/actions/{action_id}/parameters",
    methods=["POST"],
    auth=True,
    tags=["Persona Action"],
    response=success_response(
        data={
            "id": ResponseField(
                field_type=str,
                description="ID of the created parameter",
                example="param_abc123",
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Parameter created successfully",
            ),
        }
    ),
)
async def create_parameter_endpoint(
    action_id: str,
    condition: str,
    response: str,
    action: Optional[str] = None,
    enabled: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new parameter for a PersonaAction.


    **Args:**

    - action_id: ID of the PersonaAction
    - condition: When this parameter applies
    - response: Behavioral instruction for the LLM
    - action: Optional action label to trigger
    - enabled: Whether the parameter is enabled
    - metadata: Optional metadata dictionary


    **Returns:**

    Dictionary with created parameter ID
    """
    from jvagent.action.persona.persona_action import PersonaAction

    action_node = await PersonaAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"PersonaAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    if not isinstance(action_node, PersonaAction):
        raise ValidationError(
            message=f"Action '{action_id}' is not a PersonaAction",
            details={"action_id": action_id},
        )

    if not condition or not condition.strip():
        raise ValidationError(
            message="condition is required",
            details={"condition": condition},
        )

    if not response or not response.strip():
        raise ValidationError(
            message="response is required",
            details={"response": response},
        )

    # Add parameter to the parameters list
    if action_node.parameters is None:
        action_node.parameters = []

    new_param = {
        "condition": condition.strip(),
        "response": response.strip(),
        "enabled": enabled,
    }
    if action:
        new_param["action"] = action
    if metadata:
        new_param["metadata"] = metadata

    action_node.parameters.append(new_param)
    await action_node.save()

    return {
        "id": f"param_{len(action_node.parameters) - 1}",  # Use index as ID
        "message": "Parameter created successfully",
    }


@endpoint(
    "/actions/{action_id}/parameters/{param_id}",
    methods=["PUT"],
    auth=True,
    tags=["Persona Action"],
    response=success_response(
        data={
            "parameter": ResponseField(
                field_type=Dict[str, Any],
                description="Updated parameter",
                example={
                    "id": "param_1",
                    "condition": "Updated condition",
                    "response": "Updated response",
                    "enabled": True,
                },
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Parameter updated successfully",
            ),
        }
    ),
)
async def update_parameter_endpoint(
    action_id: str,
    param_id: str,
    condition: Optional[str] = None,
    response: Optional[str] = None,
    action: Optional[str] = None,
    enabled: Optional[bool] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update a parameter for a PersonaAction.


    **Args:**

    - action_id: ID of the PersonaAction
    - param_id: ID of the parameter to update
    - condition: New condition (optional)
    - response: New response (optional)
    - action: New action label (optional)
    - enabled: New enabled status (optional)
    - metadata: Metadata updates (optional)


    **Returns:**

    Dictionary with updated parameter
    """
    from jvagent.action.persona.persona_action import PersonaAction

    action_node = await PersonaAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"PersonaAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    if not isinstance(action_node, PersonaAction):
        raise ValidationError(
            message=f"Action '{action_id}' is not a PersonaAction",
            details={"action_id": action_id},
        )

    updates: Dict[str, Any] = {}
    if condition is not None:
        updates["condition"] = condition.strip()
    if response is not None:
        updates["response"] = response.strip()
    if action is not None:
        updates["action"] = action
    if enabled is not None:
        updates["enabled"] = enabled
    if metadata is not None:
        updates["metadata"] = metadata

    if not updates:
        raise ValidationError(
            message="At least one field to update is required",
            details={},
        )

    # Parse param_id as index (format: "param_0", "param_1", etc.)
    try:
        param_index = int(param_id.replace("param_", ""))
    except (ValueError, AttributeError):
        raise ValidationError(
            message=f"Invalid parameter ID format: '{param_id}'",
            details={"param_id": param_id},
        )

    # Get parameters list
    if action_node.parameters is None:
        action_node.parameters = []

    if param_index < 0 or param_index >= len(action_node.parameters):
        raise ResourceNotFoundError(
            message=f"Parameter with ID '{param_id}' not found",
            details={"param_id": param_id},
        )

    # Update the parameter
    action_node.parameters[param_index].update(updates)
    await action_node.save()

    return {
        "parameter": action_node.parameters[param_index],
        "message": "Parameter updated successfully",
    }


@endpoint(
    "/actions/{action_id}/parameters/{param_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Persona Action"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Parameter deleted successfully",
            ),
        }
    ),
)
async def delete_parameter_endpoint(
    action_id: str,
    param_id: str,
) -> Dict[str, Any]:
    """Delete a parameter from a PersonaAction.


    **Args:**

    - action_id: ID of the PersonaAction
    - param_id: ID of the parameter to delete


    **Returns:**

    Dictionary with success message
    """
    from jvagent.action.persona.persona_action import PersonaAction

    action_node = await PersonaAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"PersonaAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    if not isinstance(action_node, PersonaAction):
        raise ValidationError(
            message=f"Action '{action_id}' is not a PersonaAction",
            details={"action_id": action_id},
        )

    # Parse param_id as index (format: "param_0", "param_1", etc.)
    try:
        param_index = int(param_id.replace("param_", ""))
    except (ValueError, AttributeError):
        raise ValidationError(
            message=f"Invalid parameter ID format: '{param_id}'",
            details={"param_id": param_id},
        )

    # Get parameters list
    if action_node.parameters is None:
        action_node.parameters = []

    if param_index < 0 or param_index >= len(action_node.parameters):
        raise ResourceNotFoundError(
            message=f"Parameter with ID '{param_id}' not found",
            details={"param_id": param_id},
        )

    # Delete the parameter
    action_node.parameters.pop(param_index)
    await action_node.save()

    return {"message": "Parameter deleted successfully"}


@endpoint(
    "/actions/{action_id}/parameters/import",
    methods=["POST"],
    auth=True,
    tags=["Persona Action"],
    response=success_response(
        data={
            "imported": ResponseField(
                field_type=int,
                description="Number of parameters imported",
                example=5,
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Imported 5 parameters",
            ),
        }
    ),
)
async def import_parameters_endpoint(
    action_id: str,
    parameters: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Import multiple parameters into a PersonaAction.


    **Args:**

    - action_id: ID of the PersonaAction
    - parameters: List of parameter dictionaries


    **Returns:**

    Dictionary with import count
    """
    from jvagent.action.persona.persona_action import PersonaAction

    action_node = await PersonaAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"PersonaAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    if not isinstance(action_node, PersonaAction):
        raise ValidationError(
            message=f"Action '{action_id}' is not a PersonaAction",
            details={"action_id": action_id},
        )

    if not parameters:
        raise ValidationError(
            message="parameters list is required and cannot be empty",
            details={},
        )

    # Initialize parameters list if needed
    if action_node.parameters is None:
        action_node.parameters = []

    # Add all parameters
    imported_count = 0
    for param in parameters:
        # Validate required fields
        if not param.get("condition") or not param.get("response"):
            continue

        action_node.parameters.append(param)
        imported_count += 1

    await action_node.save()

    return {
        "imported": imported_count,
        "message": f"Imported {imported_count} parameters",
    }
