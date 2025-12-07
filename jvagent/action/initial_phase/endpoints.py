"""API endpoints for InitialPhaseAction.

This module provides REST API endpoints for InitialPhaseAction processing
and management of parameters, competencies, and workflows.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/process",
    methods=["POST"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "user_id": ResponseField(
                field_type=str,
                description="User identifier",
                example="usr_abc123",
            ),
            "session_id": ResponseField(
                field_type=str,
                description="Session identifier",
                example="sess_xyz789",
            ),
            "instructions": ResponseField(
                field_type=Dict[str, Any],
                description="Structured JSON instructions",
                example={
                    "simplified_intent": "user wants to subscribe",
                    "applicable_parameters": [],
                    "required_workflows": ["subscription_workflow"],
                    "required_actions": ["subscription_action"],
                    "context": {},
                    "metadata": {},
                },
            ),
            "interaction": ResponseField(
                field_type=Dict[str, Any],
                description="Interaction details",
                example={
                    "id": "int_123",
                    "utterance": "I want to subscribe",
                    "actions": ["InitialPhaseAction"],
                    "parameters": [],
                },
            ),
            "events": ResponseField(
                field_type=List[Dict[str, Any]],
                description="All events emitted during processing",
                example=[],
            ),
            "processing_duration": ResponseField(
                field_type=float,
                description="Total processing time in seconds",
                example=1.5,
            ),
        }
    ),
)
async def process_endpoint(
    action_id: str,
    utterance: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    channel: str = "default",
) -> Dict[str, Any]:
    """Process user utterance through Initial Phase.

    This endpoint handles the full Initial Phase flow:
    - Resolves or creates User/Conversation
    - Generates embeddings
    - Performs vector search for parameters/competencies
    - Uses LLM to generate structured instructions
    - Returns JSON instructions for downstream processing

    Args:
        action_id: ID of the InitialPhaseAction
        utterance: User's input text
        user_id: Optional user identifier
        session_id: Optional session identifier
        channel: Communication channel

    Returns:
        Dictionary with instructions and processing details
    """
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action = await InitialPhaseAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    if not isinstance(action, InitialPhaseAction):
        raise ValidationError(
            message=f"Action '{action_id}' is not an InitialPhaseAction",
            details={"action_id": action_id, "action_type": type(action).__name__},
        )

    if not utterance or not utterance.strip():
        raise ValidationError(
            message="utterance is required and cannot be empty",
            details={"utterance": utterance},
        )

    try:
        result = await action.process(
            utterance=utterance.strip(),
            user_id=user_id,
            session_id=session_id,
            channel=channel,
        )
        return result.to_dict()
    except ValueError as e:
        raise ValidationError(message=str(e), details={"error": str(e)})


# =============================================================================
# Parameter Management Endpoints
# =============================================================================


@endpoint(
    "/actions/{action_id}/parameters",
    methods=["GET"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "parameters": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of parameters",
                example=[],
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
    """List all parameters for an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action = await InitialPhaseAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    parameters = await action.get_parameters(enabled_only=enabled_only)
    return {
        "parameters": [p.to_dict() for p in parameters],
        "count": len(parameters),
    }


@endpoint(
    "/actions/{action_id}/parameters",
    methods=["POST"],
    auth=True,
    tags=["Initial Phase Action"],
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
    workflow: Optional[str] = None,
    enabled: bool = True,
    execution_requirement: str = "conditional",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new parameter for an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action_node = await InitialPhaseAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
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

    param_id = await action_node.add_parameter({
        "condition": condition.strip(),
        "response": response.strip(),
        "action": action,
        "workflow": workflow,
        "enabled": enabled,
        "execution_requirement": execution_requirement,
        "metadata": metadata or {},
    })

    return {
        "id": param_id,
        "message": "Parameter created successfully",
    }


@endpoint(
    "/actions/{action_id}/parameters/{param_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Initial Phase Action"],
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
    """Delete a parameter from an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action_node = await InitialPhaseAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    deleted = await action_node.delete_parameter(param_id)
    if not deleted:
        raise ResourceNotFoundError(
            message=f"Parameter with ID '{param_id}' not found",
            details={"param_id": param_id},
        )

    return {"message": "Parameter deleted successfully"}


# =============================================================================
# Competency Management Endpoints
# =============================================================================


@endpoint(
    "/actions/{action_id}/competencies",
    methods=["GET"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "competencies": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of competencies",
            ),
            "count": ResponseField(
                field_type=int,
                description="Number of competencies",
            ),
        }
    ),
)
async def list_competencies_endpoint(
    action_id: str,
    enabled_only: bool = True,
) -> Dict[str, Any]:
    """List all competencies for an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action = await InitialPhaseAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    competencies = await action.get_competencies(enabled_only=enabled_only)
    return {
        "competencies": [c.to_dict() for c in competencies],
        "count": len(competencies),
    }


@endpoint(
    "/actions/{action_id}/competencies",
    methods=["POST"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "id": ResponseField(
                field_type=str,
                description="ID of the created competency",
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
            ),
        }
    ),
)
async def create_competency_endpoint(
    action_id: str,
    name: str,
    description: str,
    states: Optional[List[Dict[str, Any]]] = None,
    actions: Optional[List[str]] = None,
    workflows: Optional[List[str]] = None,
    enabled: bool = True,
    execution_requirement: str = "conditional",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new competency for an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action_node = await InitialPhaseAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    comp_id = await action_node.add_competency({
        "name": name,
        "description": description,
        "states": states or [],
        "actions": actions or [],
        "workflows": workflows or [],
        "enabled": enabled,
        "execution_requirement": execution_requirement,
        "metadata": metadata or {},
    })

    return {
        "id": comp_id,
        "message": "Competency created successfully",
    }


@endpoint(
    "/actions/{action_id}/competencies/{comp_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Success message",
            ),
        }
    ),
)
async def delete_competency_endpoint(
    action_id: str,
    comp_id: str,
) -> Dict[str, Any]:
    """Delete a competency from an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action_node = await InitialPhaseAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    deleted = await action_node.delete_competency(comp_id)
    if not deleted:
        raise ResourceNotFoundError(
            message=f"Competency with ID '{comp_id}' not found",
            details={"comp_id": comp_id},
        )

    return {"message": "Competency deleted successfully"}


# =============================================================================
# Workflow Management Endpoints
# =============================================================================


@endpoint(
    "/actions/{action_id}/workflows",
    methods=["GET"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "workflows": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of workflows",
            ),
            "count": ResponseField(
                field_type=int,
                description="Number of workflows",
            ),
        }
    ),
)
async def list_workflows_endpoint(
    action_id: str,
    enabled_only: bool = True,
) -> Dict[str, Any]:
    """List all workflows for an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action = await InitialPhaseAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    workflows = await action.get_workflows(enabled_only=enabled_only)
    return {
        "workflows": [w.to_dict() for w in workflows],
        "count": len(workflows),
    }


@endpoint(
    "/actions/{action_id}/workflows",
    methods=["POST"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "id": ResponseField(
                field_type=str,
                description="ID of the created workflow",
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
            ),
        }
    ),
)
async def create_workflow_endpoint(
    action_id: str,
    name: str,
    description: str,
    steps: Optional[List[Dict[str, Any]]] = None,
    enabled: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new workflow for an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action_node = await InitialPhaseAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    workflow_id = await action_node.add_workflow({
        "name": name,
        "description": description,
        "steps": steps or [],
        "enabled": enabled,
        "metadata": metadata or {},
    })

    return {
        "id": workflow_id,
        "message": "Workflow created successfully",
    }


@endpoint(
    "/actions/{action_id}/workflows/{workflow_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Success message",
            ),
        }
    ),
)
async def delete_workflow_endpoint(
    action_id: str,
    workflow_id: str,
) -> Dict[str, Any]:
    """Delete a workflow from an InitialPhaseAction."""
    from jvagent.action.initial_phase.base import InitialPhaseAction

    action_node = await InitialPhaseAction.get(action_id)
    if not action_node:
        raise ResourceNotFoundError(
            message=f"InitialPhaseAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    deleted = await action_node.delete_workflow(workflow_id)
    if not deleted:
        raise ResourceNotFoundError(
            message=f"Workflow with ID '{workflow_id}' not found",
            details={"workflow_id": workflow_id},
        )

    return {"message": "Workflow deleted successfully"}


This module provides REST API endpoints for PersonaAction interactions
and parameter management.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/interact",
    methods=["POST"],
    auth=True,
    tags=["Initial Phase Action"],
    response=success_response(
        data={
            "user_id": ResponseField(
                field_type=str,
                description="User identifier (always returned)",
                example="usr_abc123",
            ),
            "session_id": ResponseField(
                field_type=str,
                description="Session identifier (always returned)",
                example="sess_xyz789",
            ),
            "response": ResponseField(
                field_type=str,
                description="Complete agent response",
                example="Hello! How can I help you today?",
            ),
            "canned_response": ResponseField(
                field_type=Optional[str],  # type: ignore[arg-type]
                description="Immediate response (if canned responses enabled)",
                example="Please wait while I process your request...",
            ),
            "interaction": ResponseField(
                field_type=Dict[str, Any],
                description="Interaction details",
                example={
                    "id": "int_123",
                    "utterance": "Hello",
                    "response": "Hi there!",
                    "actions": ["PersonaAction", "OpenAIModelAction"],
                    "directives": [],
                    "parameters": [{"id": "param_1", "condition": "...", "response": "..."}],
                    "model_log": [{"prompt": "...", "response": "...", "metrics": {"total_tokens": 100, "duration": 1.5}}],
                },
            ),
            "events": ResponseField(
                field_type=List[Dict[str, Any]],
                description="All events emitted during interaction",
                example=[
                    {
                        "event_type": "interaction_started",
                        "interaction_id": "int_123",
                        "timestamp": "2025-01-01T12:00:00",
                        "data": {},
                    }
                ],
            ),
        }
    ),
)
async def interact_endpoint(
    action_id: str,
    utterance: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    channel: str = "default",
    stream: bool = False,
) -> Dict[str, Any]:
    """Process a user interaction through the PersonaAction.

    This endpoint handles the main interaction flow:
    - Resolves or creates User/Conversation based on provided IDs
    - Processes the utterance through parameter filtering and action delegation
    - Returns the agent response along with all interaction events

    Request Scenarios:
    1. First message (no user_id, no session_id):
       Creates User + Conversation, returns both IDs

    2. Continue conversation (session_id only):
       Uses existing Conversation, returns user_id from session

    3. New conversation for existing user (user_id only):
       Gets/Creates User, creates new Conversation, returns new session_id

    4. Both provided (user_id + session_id):
       Validates they match, uses existing Conversation

    Args:
        action_id: ID of the PersonaAction to use
        utterance: User's input text
        user_id: Optional user identifier
        session_id: Optional session identifier to continue conversation
        channel: Communication channel (default, whatsapp, web, etc.)
        stream: Whether to stream the response (not yet supported)

    Returns:
        Dictionary with user_id, session_id, response, and events
    """
    from jvagent.action.persona.base import PersonaAction

    # Get the action
    action = await PersonaAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"PersonaAction with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    if not isinstance(action, PersonaAction):
        raise ValidationError(
            message=f"Action '{action_id}' is not a PersonaAction",
            details={"action_id": action_id, "action_type": type(action).__name__},
        )

    if not utterance or not utterance.strip():
        raise ValidationError(
            message="utterance is required and cannot be empty",
            details={"utterance": utterance},
        )

    try:
        result = await action.interact(
            utterance=utterance.strip(),
            user_id=user_id,
            session_id=session_id,
            channel=channel,
            stream=stream,
        )
        return result.to_dict()
    except ValueError as e:
        raise ValidationError(message=str(e), details={"error": str(e)})


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

    Args:
        action_id: ID of the PersonaAction
        enabled_only: If True, only return enabled parameters

    Returns:
        Dictionary with parameters list and count
    """
    from jvagent.action.persona.base import PersonaAction

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

    parameters = await action.get_parameters(enabled_only=enabled_only)
    return {
        "parameters": [p.to_dict() for p in parameters],
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

    Args:
        action_id: ID of the PersonaAction
        condition: When this parameter applies
        response: Behavioral instruction for the LLM
        action: Optional action label to trigger
        enabled: Whether the parameter is enabled
        metadata: Optional metadata dictionary

    Returns:
        Dictionary with created parameter ID
    """
    from jvagent.action.persona.base import PersonaAction

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

    param_id = await action_node.add_parameter({
        "condition": condition.strip(),
        "response": response.strip(),
        "action": action,
        "enabled": enabled,
        "metadata": metadata or {},
    })

    return {
        "id": param_id,
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

    Args:
        action_id: ID of the PersonaAction
        param_id: ID of the parameter to update
        condition: New condition (optional)
        response: New response (optional)
        action: New action label (optional)
        enabled: New enabled status (optional)
        metadata: Metadata updates (optional)

    Returns:
        Dictionary with updated parameter
    """
    from jvagent.action.persona.base import PersonaAction

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

    param = await action_node.update_parameter(param_id, updates)
    if not param:
        raise ResourceNotFoundError(
            message=f"Parameter with ID '{param_id}' not found",
            details={"param_id": param_id},
        )

    return {
        "parameter": param.to_dict(),
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

    Args:
        action_id: ID of the PersonaAction
        param_id: ID of the parameter to delete

    Returns:
        Dictionary with success message
    """
    from jvagent.action.persona.base import PersonaAction

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

    deleted = await action_node.delete_parameter(param_id)
    if not deleted:
        raise ResourceNotFoundError(
            message=f"Parameter with ID '{param_id}' not found",
            details={"param_id": param_id},
        )

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

    Args:
        action_id: ID of the PersonaAction
        parameters: List of parameter dictionaries

    Returns:
        Dictionary with import count
    """
    from jvagent.action.persona.base import PersonaAction

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

    count = await action_node.import_parameters(parameters)
    return {
        "imported": count,
        "message": f"Imported {count} parameters",
    }
