"""Interact endpoint for agent interactions.

This module provides the common entry point for agent interactions,
replacing the PersonaAction interact endpoint.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import (
    RateLimitError,
    ResourceNotFoundError,
    ValidationError,
)

from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.interact.rate_limiter import (
    extract_client_ip,
    get_rate_limiter,
    initialize_rate_limiter,
)
from jvagent.action.interact.response_builder import build_interact_response
from jvagent.action.response.streaming import create_sse_response, format_sse_chunk
from jvagent.core.agent import Agent

logger = logging.getLogger(__name__)

from jvagent.action.interact.utils import flush_deferred_saves

# Import profiling utilities


async def _finalize_usage(interaction: Any) -> None:
    """Compute usage from observability_metrics and update user stats.

    Runs after walker completes and flush, so all model_call events are present.
    """
    if not interaction:
        return
    if hasattr(interaction, "compute_usage"):
        interaction.compute_usage()
        await interaction.save()
    if hasattr(interaction, "usage") and interaction.usage:
        try:
            user = await interaction.get_user()
            if user and hasattr(user, "add_usage_from_interaction"):
                await user.add_usage_from_interaction(interaction.usage)
        except Exception as e:
            logger.warning(
                "Failed to update user usage stats: interaction_id=%s user_id=%s error=%s",
                getattr(interaction, "id", None),
                getattr(interaction, "user_id", None),
                e,
            )


from jvagent.core.profiling import profile_enabled, profiled_request

# Import INTERACTION level to ensure it's registered and available for logging
from jvagent.logging.service import INTERACTION_LEVEL_NUMBER


def _build_interaction_log_data(interaction, app_id, agent_id=None, active_tasks=None):
    """Build comprehensive log data dictionary for interaction logging.

    This function extracts all available interaction data and builds a complete
    log payload that includes the full interaction state, metadata, and context.

    Args:
        interaction: Interaction node instance
        app_id: Application ID
        agent_id: Optional agent ID
        active_tasks: Optional list of active tasks from conversation (conversation-level)

    Returns:
        Tuple of (log_data_dict, message_string) for logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
    """
    # Extract all interaction fields
    interaction_id = interaction.id if hasattr(interaction, "id") else None
    user_id = interaction.user_id if hasattr(interaction, "user_id") else ""
    session_id = interaction.session_id if hasattr(interaction, "session_id") else ""
    conversation_id = (
        interaction.conversation_id if hasattr(interaction, "conversation_id") else ""
    )
    utterance = interaction.utterance if hasattr(interaction, "utterance") else ""
    response = interaction.response if hasattr(interaction, "response") else None
    channel = interaction.channel if hasattr(interaction, "channel") else "default"
    interpretation = (
        interaction.interpretation if hasattr(interaction, "interpretation") else None
    )
    anchors = interaction.anchors if hasattr(interaction, "anchors") else []
    actions = interaction.actions if hasattr(interaction, "actions") else []
    directives = interaction.directives if hasattr(interaction, "directives") else []
    parameters = interaction.parameters if hasattr(interaction, "parameters") else []
    events = interaction.events if hasattr(interaction, "events") else []
    observability_metrics = (
        interaction.observability_metrics
        if hasattr(interaction, "observability_metrics")
        else []
    )
    streamed = interaction.streamed if hasattr(interaction, "streamed") else False
    closed = interaction.closed if hasattr(interaction, "closed") else False
    usage = getattr(interaction, "usage", None) or {}
    started_at = (
        interaction.started_at.isoformat()
        if hasattr(interaction, "started_at") and interaction.started_at
        else None
    )
    completed_at = (
        interaction.completed_at.isoformat()
        if hasattr(interaction, "completed_at") and interaction.completed_at
        else None
    )

    # Get full interaction state (comprehensive export)
    if hasattr(interaction, "get_state"):
        interaction_data = interaction.get_state()
    else:
        # Fallback: build comprehensive state manually
        interaction_data = {
            "id": interaction_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "session_id": session_id,
            "utterance": utterance,
            "channel": channel,
            "response": response,
            "actions": actions,
            "directives": directives,
            "parameters": parameters,
            "events": events,
            "observability_metrics": observability_metrics,
            "usage": usage,
            "interpretation": interpretation,
            "anchors": anchors,
            "started_at": started_at,
            "completed_at": completed_at,
            "closed": closed,
            "streamed": streamed,
        }

    # Build message
    message = (
        f"Interaction: {utterance[:100]}" if utterance else "Interaction completed"
    )
    if response:
        message += f" → {response[:100]}"

    # Build event code
    event_code = "interaction_completed"
    if closed:
        event_code = "interaction_closed"

    # Calculate duration if available
    duration = None
    if hasattr(interaction, "get_duration"):
        duration = interaction.get_duration()
        if duration <= 0:
            duration = None

    # Build comprehensive extra dict for logger
    # All fields in 'extra' will be captured by DBLogHandler and stored in log_data
    # Only interaction properties are included (no nested 'details' dict)
    log_data = {
        "event_code": event_code,
        # Core identifiers
        "app_id": app_id,
        "agent_id": agent_id or "",
        "user_id": user_id,
        "session_id": session_id,
        "interaction_id": interaction_id or "",
        "conversation_id": conversation_id,
        # Full interaction payload
        "interaction_data": interaction_data,
        # Interaction properties
        "utterance": utterance,
        "response": response,
        "channel": channel,
        "actions": actions,
        "directives": directives,
        "parameters": parameters,
        "events": events,
        "active_tasks": active_tasks if active_tasks is not None else [],
        "interpretation": interpretation,
        "anchors": anchors,
        "streamed": streamed,
        "closed": closed,
        "has_response": response is not None,
        "action_count": len(actions),
        "started_at": started_at,
        "completed_at": completed_at,
    }

    # Add duration if available
    if duration is not None:
        log_data["duration_seconds"] = duration

    return log_data, message


# Module-level flag to track if rate limiter has been initialized from config
_rate_limiter_initialized = False


def _initialize_rate_limiter_from_config() -> None:
    """Initialize rate limiter from app.yaml config (called once at module load)."""
    global _rate_limiter_initialized
    if _rate_limiter_initialized:
        return

    try:
        import os

        from jvagent.core.app_loader import AppLoader

        # Try to find app.yaml in current directory or parent directories
        app_path = os.getcwd()
        loader = AppLoader(app_path)
        descriptor = loader.load_app_descriptor()

        if descriptor and descriptor.config:
            interact_config = descriptor.config.get("interact", {})
            rate_limit = interact_config.get("rate_limit_per_minute", 60)
            max_length = interact_config.get("max_utterance_length", 2000)

            # Handle None/null values
            if max_length == "None" or max_length is None:
                max_length = None

            initialize_rate_limiter(
                rate_limit_per_minute=rate_limit,
                max_utterance_length=max_length,
            )
            logger.info(
                f"Initialized rate limiter: {rate_limit} req/min, "
                f"max_utterance_length={max_length or 'unlimited'}"
            )
        else:
            # Use defaults
            initialize_rate_limiter()
            logger.debug("Using default rate limiter configuration")
    except Exception as e:
        # If config loading fails, use defaults
        logger.debug(f"Could not load rate limiter config, using defaults: {e}")
        initialize_rate_limiter()

    _rate_limiter_initialized = True


# Initialize on module import
_initialize_rate_limiter_from_config()


@endpoint(
    "/agents/{agent_id}/interact",
    methods=["POST"],
    auth=False,
    tags=["Agent"],
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
                description="Agent response (always returned)",
                example="Hello! How can I help you today?",
            ),
            "interaction": ResponseField(
                field_type=Optional[Dict[str, Any]],  # type: ignore[arg-type]
                description=(
                    "Interaction details (development mode only). Excluded in production mode. "
                    "Includes: id, utterance, response, actions, directives, parameters, "
                    "events, active_tasks, observability_metrics, streamed."
                ),
                example={
                    "id": "int_123",
                    "utterance": "Hello",
                    "response": "Hi there!",
                    # Development mode only:
                    "actions": ["InteractAction1", "InteractAction2"],
                    "directives": [],
                    "parameters": [],
                    "events": [],
                    "active_tasks": [],
                    "observability_metrics": [],
                },
                default=None,
            ),
            "report": ResponseField(
                field_type=Optional[List[Dict[str, Any]]],  # type: ignore[arg-type]
                description=(
                    "Walker traversal report (development mode only). "
                    "Excluded in production mode."
                ),
                example=[
                    {
                        "interaction_created": {
                            "interaction_id": "int_123",
                            "user_id": "usr_abc123",
                            "session_id": "sess_xyz789",
                        }
                    }
                ],
                default=None,
            ),
        }
    ),
    response_model_exclude_none=True,
)
async def interact_endpoint(
    request: Request,
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
    - RateLimitError: If rate limit exceeded
    """
    # Get rate limiter instance (initialized at module load)
    rate_limiter = get_rate_limiter()

    # Extract client IP
    client_ip = extract_client_ip(request)
    if not client_ip:
        logger.warning("Could not extract client IP for rate limiting")
        client_ip = "unknown"

    # Check rate limit
    if not rate_limiter.check_rate_limit(client_ip, agent_id):
        raise RateLimitError(
            message=f"Rate limit exceeded: {rate_limiter.rate_limit_per_minute} requests per minute",
            details={
                "rate_limit": rate_limiter.rate_limit_per_minute,
                "ip": client_ip,
                "agent_id": agent_id,
            },
        )

    # Validate utterance length
    is_valid, error_message = rate_limiter.validate_utterance_length(utterance)
    if not is_valid:
        raise ValidationError(
            message=error_message or "utterance exceeds maximum length",
            details={
                "utterance_length": len(utterance),
                "max_length": rate_limiter.max_utterance_length,
            },
        )

    # Record the request for rate limiting
    rate_limiter.record_request(client_ip, agent_id)

    # Start profiling for this request
    async with profiled_request() as profile:
        # Set profile in context for LM calls to record their timing
        from jvagent.core.profiling import set_current_profile

        set_current_profile(profile)

        try:
            # Validate agent exists (use cache if enabled)
            async with profile.measure("agent_lookup"):
                from jvagent.core.cache import get_cached_agent

                agent = await get_cached_agent(agent_id)

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
                stream=stream,
            )

            if stream:
                # Streaming mode: return SSE response
                # Note: Profiling for streaming is handled in _stream_interaction
                return create_sse_response(
                    _stream_interaction(walker, agent),
                    headers={"X-Session-ID": walker.session_id or ""},
                )
            else:
                # Non-streaming mode: wait for completion and return consolidated response
                # Spawn walker directly on the Agent node (skips Root -> Agent traversal)
                # The walker will then traverse to Actions -> InteractActions
                async with profile.measure("walker_execution"):
                    await walker.spawn(agent)

                # Get report
                async with profile.measure("get_report"):
                    report = await walker.get_report()

                # Get interaction result
                interaction = walker.interaction
                if not interaction:
                    raise RuntimeError("Interaction was not created during traversal")

                # Mark interaction as not streamed
                interaction.streamed = False

                # Clear interaction from context
                from jvagent.action.model.context import set_interaction

                set_interaction(None)

                await interaction.close_interaction()

                # Flush deferred saves (interaction and conversation) with error handling
                async with profile.measure("flush_saves"):
                    await flush_deferred_saves(interaction, walker.conversation)

                # Compute usage after flush so all model_call events are present
                await _finalize_usage(interaction)

                # Log interaction using INTERACTION level
                try:
                    from jvagent.core.app import App

                    app = await App.get()
                    if app:
                        active_tasks = []
                        if walker.conversation:
                            active_tasks = walker.conversation.get_active_tasks(
                                status="active"
                            )
                        log_data, message = _build_interaction_log_data(
                            interaction, app.id, agent_id, active_tasks=active_tasks
                        )
                        # Use logger.log() directly to ensure extra parameter is passed correctly
                        logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
                except Exception as e:
                    # Log error but don't fail the request
                    logger.warning(f"Failed to log interaction: {e}")

                # Build response with environment-based filtering
                async with profile.measure("build_response"):
                    result = await build_interact_response(
                        user_id=walker.user_id or "",
                        session_id=walker.session_id or "",
                        interaction=interaction,
                        report=report,
                    )

                return result

        except ValueError as e:
            raise ValidationError(message=str(e), details={"error": str(e)})
        except Exception as e:
            logger.error(f"Error in interact endpoint: {e}", exc_info=True)
            raise ValidationError(
                message=f"Interaction failed: {str(e)}",
                details={"error": str(e)},
            )
        finally:
            # Clear profile context
            set_current_profile(None)
            # Request-scoped cache cleanup (probabilistic, serverless-friendly)
            try:
                from jvagent.core.cache import maybe_cleanup_on_request

                await maybe_cleanup_on_request()
            except Exception:
                pass  # Cleanup errors should never fail the request


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
    import time

    from jvagent.core.profiling import (
        finalize_profile,
        get_or_create_profile,
        profile_enabled,
        set_current_profile,
    )

    # Manual profiling for streaming (context manager doesn't work well with generators)
    profile = await get_or_create_profile()
    stream_start_time = time.time()

    # Set profile in context for LM calls to record their timing
    set_current_profile(profile)

    try:
        # Start walker in background
        walker_start = time.time()
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

        # Record time to interaction creation
        profile.record("interaction_created", time.time() - walker_start)

        interaction = walker.interaction
        interaction.streamed = True
        # Deferred save mode is already enabled, no need to save here

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
                            profile.record(
                                "walker_execution", time.time() - walker_start
                            )
                        except Exception as e:
                            profile.record(
                                "walker_execution", time.time() - walker_start
                            )
                            yield format_sse_chunk(
                                {"type": "error", "message": f"Walker error: {str(e)}"}
                            )
                        break

                    try:
                        message = await asyncio.wait_for(
                            message_queue.get(), timeout=0.25
                        )
                        yield format_sse_chunk(
                            {"type": "message", "message": message.to_dict()}
                        )
                    except asyncio.TimeoutError:
                        continue
            finally:
                # Cleanup subscription
                await walker.response_bus.unsubscribe(
                    walker.session_id, message_callback
                )

            # Clear interaction from context
            from jvagent.action.model.context import set_interaction

            set_interaction(None)

            # Close interaction
            await interaction.close_interaction()
            # Flush deferred saves (interaction and conversation) with error handling
            await flush_deferred_saves(interaction, walker.conversation)

            # Compute usage after flush so all model_call events are present
            await _finalize_usage(interaction)

            # Log interaction using INTERACTION level
            try:
                from jvagent.core.app import App

                app = await App.get()
                if app:
                    active_tasks = []
                    if walker.conversation:
                        active_tasks = walker.conversation.get_active_tasks(
                            status="active"
                        )
                    agent_id_for_logging = (
                        walker.agent_id if hasattr(walker, "agent_id") else agent.id
                    )
                    log_data, message = _build_interaction_log_data(
                        interaction,
                        app.id,
                        agent_id_for_logging,
                        active_tasks=active_tasks,
                    )
                    # Use logger.log() directly to ensure extra parameter is passed correctly
                    logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
            except Exception as e:
                # Log error but don't fail the request
                logger.warning(f"Failed to log interaction: {e}")

            # Send final consolidated response (filtered for production)
            report_start = time.time()
            report = await walker.get_report()
            final_response = await build_interact_response(
                user_id=walker.user_id or "",
                session_id=walker.session_id or "",
                interaction=interaction,
                report=report,
            )
            profile.record("build_response", time.time() - report_start)
            profile.record("total_stream_time", time.time() - stream_start_time)

            yield format_sse_chunk(
                {
                    "type": "final",
                    **final_response,  # Spread the filtered response
                }
            )

            # Log profile summary
            await finalize_profile(profile.request_id, log=True)

            # Note: subscription cleaned up in finally above
        else:
            # No response bus, just wait for walker and send final response
            await walk_task
            profile.record("walker_execution", time.time() - walker_start)

            # Clear interaction from context
            from jvagent.action.model.context import set_interaction

            set_interaction(None)

            # Close interaction
            await interaction.close_interaction()
            # Flush deferred saves (interaction and conversation) with error handling
            await flush_deferred_saves(interaction, walker.conversation)

            # Compute usage after flush so all model_call events are present
            await _finalize_usage(interaction)

            # Log interaction using INTERACTION level
            try:
                from jvagent.core.app import App

                app = await App.get()
                if app:
                    active_tasks = []
                    if walker.conversation:
                        active_tasks = walker.conversation.get_active_tasks(
                            status="active"
                        )
                    agent_id_from_walker = (
                        walker.agent_id if hasattr(walker, "agent_id") else None
                    )
                    log_data, message = _build_interaction_log_data(
                        interaction,
                        app.id,
                        agent_id_from_walker,
                        active_tasks=active_tasks,
                    )
                    # Use logger.log() directly to ensure extra parameter is passed correctly
                    logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
            except Exception as e:
                # Log error but don't fail the request
                logger.warning(f"Failed to log interaction: {e}")

            report_start = time.time()
            report = await walker.get_report()
            final_response = await build_interact_response(
                user_id=walker.user_id or "",
                session_id=walker.session_id or "",
                interaction=interaction,
                report=report,
            )
            profile.record("build_response", time.time() - report_start)
            profile.record("total_stream_time", time.time() - stream_start_time)

            yield format_sse_chunk(
                {
                    "type": "final",
                    **final_response,  # Spread the filtered response
                }
            )

            # Log profile summary
            await finalize_profile(profile.request_id, log=True)

    except Exception as e:
        logger.error(f"Error in stream_interaction: {e}", exc_info=True)
        # Still log profile on error
        profile.record("total_stream_time", time.time() - stream_start_time)
        profile.record("error", 0)  # Mark that an error occurred
        await finalize_profile(profile.request_id, log=True)
        yield format_sse_chunk(
            {
                "type": "error",
                "message": f"Streaming error: {str(e)}",
            }
        )
    finally:
        # Clear profile context
        set_current_profile(None)
        # Request-scoped cache cleanup (probabilistic, serverless-friendly)
        try:
            from jvagent.core.cache import maybe_cleanup_on_request

            await maybe_cleanup_on_request()
        except Exception:
            pass  # Cleanup errors should never fail the request
