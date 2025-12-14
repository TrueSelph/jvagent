"""Interact endpoint for agent interactions.

This module provides the common entry point for agent interactions,
replacing the PersonaAction interact endpoint.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi.responses import StreamingResponse

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.core.agent import Agent
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.response.streaming import create_sse_response, format_sse_chunk

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
                    "observability_metrics": [],
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
    stream: bool = False,
) -> Any:
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
    - stream: If True, return SSE stream; if False, return consolidated response


    **Returns:**

    - If stream=True: StreamingResponse with SSE chunks
    - If stream=False: Dictionary with user_id, session_id, response, interaction, and report


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
        stream_mode=stream,
    )

    try:
        if stream:
            # Streaming mode: return SSE response
            return create_sse_response(
                _stream_interaction(walker, agent),
                headers={"X-Session-ID": walker.session_id or ""},
            )
        else:
            # Non-streaming mode: wait for completion and return consolidated response
            # Spawn walker directly on the Agent node (skips Root -> Agent traversal)
            # The walker will then traverse to Actions -> InteractActions
            await walker.spawn(agent)

            # Get report
            report = await walker.get_report()

            # Get interaction result
            interaction = walker.interaction
            if not interaction:
                raise RuntimeError("Interaction was not created during traversal")

            # Mark interaction as not streamed
            interaction.streamed = False
            
            # Finalize interaction (accumulate streamed data and observability)
            if walker.response_bus:
                await walker.response_bus.finalize_interaction(
                    interaction_id=interaction.id,
                    interaction=interaction,
                    session_id=walker.session_id or "",
                    channel=walker.channel,
                )
            
            # Clear interaction_id from context
            from jvagent.action.model.context import set_interaction_id
            set_interaction_id(None)
            
            interaction.close_interaction()
            await interaction.save()

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
                    "observability_metrics": interaction.observability_metrics,
                    "streamed": interaction.streamed,
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


async def _stream_interaction(
    walker: InteractWalker, agent: Agent
) -> AsyncGenerator[str, None]:
    """Stream interaction as SSE chunks.

    Args:
        walker: InteractWalker instance
        agent: Agent node

    Yields:
        SSE-formatted string chunks
    """
    try:
        # Start walker in background
        walk_task = asyncio.create_task(walker.spawn(agent))

        # Wait for interaction to be created
        max_wait = 5.0  # Maximum seconds to wait for interaction
        waited = 0.0
        while not walker.interaction and waited < max_wait:
            await asyncio.sleep(0.1)
            waited += 0.1

        if not walker.interaction:
            yield format_sse_chunk(
                {
                    "type": "error",
                    "message": "Interaction was not created during traversal",
                }
            )
            return

        interaction = walker.interaction
        interaction.streamed = True
        await interaction.save()

        # Send initial message
        yield format_sse_chunk(
            {
                "type": "start",
                "interaction_id": interaction.id,
                "session_id": walker.session_id or "",
                "user_id": walker.user_id or "",
            }
        )

        # Subscribe to response bus messages
        if walker.response_bus and walker.session_id:
            # Use asyncio.Queue to avoid polling delays and improve latency
            message_queue: asyncio.Queue[Any] = asyncio.Queue()

            async def message_callback(message: Any) -> None:
                """Callback to receive new messages."""
                if message.interaction_id != interaction.id:
                    return
                await message_queue.put(message)

            await walker.response_bus.subscribe(
                walker.session_id, message_callback, receive_chunks=True
            )

            try:
                # Stream messages as they arrive
                while True:
                    # Exit once walker is done and we've drained the queue
                    if walk_task.done() and message_queue.empty():
                        try:
                            await walk_task
                        except Exception as e:
                            yield format_sse_chunk(
                                {"type": "error", "message": f"Walker error: {str(e)}"}
                            )
                        break

                    try:
                        message = await asyncio.wait_for(message_queue.get(), timeout=0.25)
                        yield format_sse_chunk(
                            {"type": "message", "message": message.to_dict()}
                        )
                    except asyncio.TimeoutError:
                        continue
            finally:
                # Cleanup subscription
                await walker.response_bus.unsubscribe(walker.session_id, message_callback)

            # Finalize interaction (accumulate streamed data and observability)
            if walker.response_bus:
                await walker.response_bus.finalize_interaction(
                    interaction_id=interaction.id,
                    interaction=interaction,
                    session_id=walker.session_id or "",
                    channel=walker.channel,
                )

            # Clear interaction_id from context
            from jvagent.action.model.context import set_interaction_id
            set_interaction_id(None)

            # Close interaction
            interaction.close_interaction()
            await interaction.save()

            # Send final consolidated response
            report = await walker.get_report()
            yield format_sse_chunk(
                {
                    "type": "final",
                    "interaction": {
                        "id": interaction.id,
                        "utterance": interaction.utterance,
                        "response": interaction.response,
                        "actions": interaction.actions,
                        "directives": interaction.directives,
                        "parameters": interaction.parameters,
                        "observability_metrics": interaction.observability_metrics,
                        "streamed": interaction.streamed,
                    },
                    "report": report,
                }
            )

            # Note: subscription cleaned up in finally above
        else:
            # No response bus, just wait for walker and send final response
            await walk_task

            # Finalize interaction (accumulate streamed data and observability)
            # Even without response bus, we should still finalize if bus becomes available
            from jvagent.core.app import App
            app = await App.get()
            if app:
                response_bus = await app.get_response_bus()
                if response_bus:
                    await response_bus.finalize_interaction(
                        interaction_id=interaction.id,
                        interaction=interaction,
                        session_id=walker.session_id or "",
                        channel=walker.channel,
                    )

            # Clear interaction_id from context
            from jvagent.action.model.context import set_interaction_id
            set_interaction_id(None)

            # Close interaction
            interaction.close_interaction()
            await interaction.save()

            report = await walker.get_report()
            yield format_sse_chunk(
                {
                    "type": "final",
                    "interaction": {
                        "id": interaction.id,
                        "utterance": interaction.utterance,
                        "response": interaction.response,
                        "actions": interaction.actions,
                        "directives": interaction.directives,
                        "parameters": interaction.parameters,
                        "observability_metrics": interaction.observability_metrics,
                        "streamed": interaction.streamed,
                    },
                    "report": report,
                }
            )

    except Exception as e:
        logger.error(f"Error in stream_interaction: {e}", exc_info=True)
        yield format_sse_chunk(
            {
                "type": "error",
                "message": f"Streaming error: {str(e)}",
            }
        )

