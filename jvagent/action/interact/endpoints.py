"""Interact endpoint for agent interactions.

This module provides the common entry point for agent interactions,
replacing the PersonaAction interact endpoint.
"""

import logging
from typing import Any, Dict, List, Optional

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.core.agent import Agent
from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


@endpoint(
    "/agents/{agent_id}/interact",
    methods=["POST"],
    auth=True,
    tags=["Interact"],
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
                field_type=Optional[str],  # type: ignore[arg-type]
                description="Agent response (if set by InteractAction)",
                example="Hello! How can I help you today?",
            ),
            "interaction": ResponseField(
                field_type=Dict[str, Any],
                description="Interaction details",
                example={
                    "id": "int_123",
                    "utterance": "Hello",
                    "response": "Hi there!",
                    "actions": ["InteractAction1", "InteractAction2"],
                    "directives": [],
                    "parameters": [],
                    "model_log": [],
                },
            ),
            "report": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Walker traversal report",
                example=[
                    {
                        "interaction_created": {
                            "interaction_id": "int_123",
                            "user_id": "usr_abc123",
                            "session_id": "sess_xyz789",
                        }
                    }
                ],
            ),
        }
    ),
)
async def interact_endpoint(
    agent_id: str,
    utterance: str,
    channel: str = "default",
    data: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Process a user interaction through the interact subsystem.

    This endpoint is the common entry point for agent interactions. It:


    - Resolves or creates User/Conversation based on provided IDs
    - Spawns InteractWalker to traverse InteractActions
    - Returns the interaction result with response


    **Request Scenarios:**

    1. **First message** (no user_id, no session_id):
       Creates User + Conversation, returns both IDs

    2. **Continue conversation** (session_id only):
       Uses existing Conversation, returns user_id from session

    3. **New conversation for existing user** (user_id only):
       Gets/Creates User, creates new Conversation, returns new session_id

    4. **Both provided** (user_id + session_id):
       Validates they match, uses existing Conversation


    **Args:**

    - agent_id: ID of the agent to interact with
    - utterance: User's input text
    - channel: Communication channel (default, whatsapp, web, etc.)
    - data: Optional dictionary payload
    - session_id: Optional session identifier to continue conversation
    - user_id: Optional user identifier


    **Returns:**

    Dictionary with user_id, session_id, response, interaction, and report


    **Raises:**

    - ResourceNotFoundError: If agent not found
    - ValidationError: If utterance is empty or invalid
    """
    # Validate agent exists
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    if not utterance or not utterance.strip():
        raise ValidationError(
            message="utterance is required and cannot be empty",
            details={"utterance": utterance},
        )

    # Create walker
    walker = InteractWalker(
        agent_id=agent_id,
        utterance=utterance.strip(),
        channel=channel,
        data=data or {},
        session_id=session_id,
        user_id=user_id,
    )

    try:
        # Spawn walker directly on the Agent node (skips Root -> Agent traversal)
        # The walker will then traverse to Actions -> InteractActions
        await walker.spawn(agent)

        # Get report
        report = await walker.get_report()

        # Get interaction result
        interaction = walker.interaction
        if not interaction:
            raise RuntimeError("Interaction was not created during traversal")

        # Build response
        result: Dict[str, Any] = {
            "user_id": walker.user_id or "",
            "session_id": walker.session_id or "",
            "response": interaction.response,
            "interaction": {
                "id": interaction.id,
                "utterance": interaction.utterance,
                "response": interaction.response,
                "actions": interaction.actions,
                "directives": interaction.directives,
                "parameters": interaction.parameters,
                "model_log": interaction.model_log,
            },
            "report": report,
        }

        return result

    except ValueError as e:
        raise ValidationError(message=str(e), details={"error": str(e)})
    except Exception as e:
        logger.error(f"Error in interact endpoint: {e}", exc_info=True)
        raise ValidationError(
            message=f"Interaction failed: {str(e)}",
            details={"error": str(e)},
        )

