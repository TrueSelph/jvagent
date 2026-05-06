"""Interact endpoint for agent interactions.

This module provides the common entry point for agent interactions,
replacing the PersonaAction interact endpoint.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, cast

from fastapi import Request
from fastapi.responses import StreamingResponse
from jvspatial import create_task, flush_deferred_entities
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import (
    JVSpatialAPIException,
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
from jvagent.core.channel import normalize_channel

logger = logging.getLogger(__name__)

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
    await flush_deferred_entities(interaction, strict=False)
    usage = getattr(interaction, "usage", None)
    if usage:
        try:
            user = await interaction.get_user()
            if user and hasattr(user, "add_usage_from_interaction"):
                await user.add_usage_from_interaction(usage)
        except Exception as e:
            logger.warning(
                "Failed to update user usage stats: interaction_id=%s user_id=%s error=%s",
                getattr(interaction, "id", None),
                getattr(interaction, "user_id", None),
                e,
            )


async def _run_background_actions(walker: "InteractWalker") -> None:
    """Execute deferred background InteractActions after the interaction is closed.

    Called as a fire-and-forget asyncio task once the response has been sent to
    the client.  Each action runs in isolation - an error in one action does NOT
    prevent subsequent actions from running.

    Args:
        walker: The InteractWalker whose background_actions list will be executed.
    """
    if not walker.background_actions:
        return

    for action in walker.background_actions:
        try:
            action_name = (
                action.get_class_name()
                if hasattr(action, "get_class_name")
                else action.__class__.__name__
            )
            logger.debug(f"Running background action: {action_name}")
            if not await walker.enforce_interact_action_access(
                action, stage="background"
            ):
                continue
            # Temporarily mark as current action so convenience methods work
            walker._current_action = action
            walker._skip_current_action_record = False
            await action.execute(walker)
            logger.debug(f"Background action completed: {action_name}")
        except Exception as e:
            agent_id = getattr(action, "agent_id", None)
            interaction_id = walker.interaction.id if walker.interaction else None
            logger.error(
                f"Error in background action {getattr(action, 'label', action.__class__.__name__)}: {e}",
                exc_info=True,
                extra={
                    "agent_id": agent_id,
                    "interaction_id": interaction_id,
                    "action_class": action.__class__.__name__,
                },
            )
        finally:
            walker._current_action = None
            walker._skip_current_action_record = False


from jvagent.core.profiling import profile_enabled, profiled_request

# Import INTERACTION level to ensure it's registered and available for logging
from jvagent.logging.service import INTERACTION_LEVEL_NUMBER

_TRUNCATE_LEN = 200

_STREAM_CLIENT_ERROR = (
    "Something went wrong while processing your request. "
    "If you need help, contact support with the request_id from this response."
)


def _sse_error_event(
    request_id: str, *, message: Optional[str] = None
) -> Dict[str, Any]:
    """Build a client-safe SSE error payload (no exception text)."""
    return {
        "type": "error",
        "message": message or _STREAM_CLIENT_ERROR,
        "request_id": request_id,
    }


def _sanitize_visitor_data_for_log(visitor_data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize visitor.data for safe logging (no PII bloat, no media/base64).

    Replaces media, quoted_message; truncates body/caption; summarizes whatsapp_media.
    """
    if not visitor_data:
        return {}
    out: Dict[str, Any] = {}
    for key, val in visitor_data.items():
        if key == "whatsapp_payload" and isinstance(val, dict):
            payload = {}
            for pk, pv in val.items():
                if pk == "media":
                    payload[pk] = "<media>"
                elif pk == "quoted_message":
                    payload[pk] = "<quoted_message>"
                elif pk in ("body", "caption") and isinstance(pv, str):
                    payload[pk] = (
                        pv[:_TRUNCATE_LEN] + "..." if len(pv) > _TRUNCATE_LEN else pv
                    )
                else:
                    payload[pk] = pv
            out[key] = payload
        elif key == "whatsapp_media" and isinstance(val, list):
            out[key] = [{"type": "media", "count": len(val)}]
        else:
            out[key] = val
    return out


def _build_interaction_log_data(
    interaction,
    app_id,
    agent_id=None,
    active_tasks=None,
    completed_tasks=None,
    visitor_data: Optional[Dict[str, Any]] = None,
):
    """Build comprehensive log data dictionary for interaction logging.

    This function extracts all available interaction data and builds a complete
    log payload that includes the full interaction state, metadata, and context.

    Args:
        interaction: Interaction node instance
        app_id: Application ID
        agent_id: Optional agent ID
        active_tasks: Optional list of active tasks from conversation (conversation-level)
        visitor_data: Optional visitor.data dict (walker.data) for inclusion in logs

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
        "completed_tasks": completed_tasks if completed_tasks is not None else [],
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

    # Include sanitized visitor.data for inspection and diagnosis
    if visitor_data:
        log_data["interact_data"] = _sanitize_visitor_data_for_log(visitor_data)

    return log_data, message


# Module-level flag to track if rate limiter has been initialized from config
_rate_limiter_initialized = False


def _initialize_rate_limiter_from_config() -> None:
    """Initialize rate limiter from config (env > app.yaml > default).

    Env vars: JVAGENT_INTERACT_RATE_LIMIT_PER_MINUTE, JVAGENT_INTERACT_MAX_UTTERANCE_LENGTH
    """
    global _rate_limiter_initialized
    if _rate_limiter_initialized:
        return

    rate_limit = 60
    max_length: Optional[int] = 2000

    try:
        from jvagent.core.app_context import get_app_root
        from jvagent.core.config import get_config_value, load_app_config

        app_config = load_app_config(get_app_root())
        rate_limit = int(
            get_config_value(
                app_config,
                "interact.rate_limit_per_minute",
                "JVAGENT_INTERACT_RATE_LIMIT_PER_MINUTE",
                60,
            )
        )
        raw_max = get_config_value(
            app_config,
            "interact.max_utterance_length",
            "JVAGENT_INTERACT_MAX_UTTERANCE_LENGTH",
            2000,
        )
        if raw_max is None:
            max_length = None
        elif isinstance(raw_max, str) and raw_max.strip().lower() in (
            "none",
            "null",
            "",
        ):
            max_length = None
        else:
            try:
                max_length = int(raw_max)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid interact.max_utterance_length value %r; using default 2000",
                    raw_max,
                )
                max_length = 2000

        initialize_rate_limiter(
            rate_limit_per_minute=rate_limit,
            max_utterance_length=max_length,
        )
        logger.info(
            f"Initialized rate limiter: {rate_limit} req/min, "
            f"max_utterance_length={max_length or 'unlimited'}"
        )
    except Exception as e:
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
                    "events, active_tasks, completed_tasks, observability_metrics, streamed."
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
                    "completed_tasks": [],
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
       Creates User + Conversation with a generated session_id; returns both IDs

    2. **session_id only** (custom or existing):
       If a Conversation exists for that id: resume it and return its user_id.
       Otherwise: create an anonymous User and a new Conversation using that ``session_id``

    3. **New conversation for existing user** (user_id only):
       Gets/Creates User, creates new Conversation with a generated session_id

    4. **Both** (user_id + session_id):
       Gets/Creates User. If no Conversation exists for ``session_id``: create one for
       that user with your ``session_id``. If it exists: validate it belongs to
       ``user_id``, then resume


    **Args:**

    - agent_id: ID of the agent to interact with
    - utterance: User's input text
    - channel: Communication channel (default, whatsapp, etc.). default = web.
    - data: Optional dictionary payload
    - session_id: Optional session id (continue an existing session or pin a new one)
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
    if not await rate_limiter.check_rate_limit(client_ip, agent_id):
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
    await rate_limiter.record_request(client_ip, agent_id)

    # Normalize channel: web/empty -> default
    channel = normalize_channel(channel)

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
                    _stream_interaction(walker, agent, request),
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
                    for item in report or []:
                        if isinstance(item, dict) and item.get("access_denied"):
                            raise ValidationError(
                                message="Access denied.",
                                details={
                                    "channel": channel,
                                    "request_id": profile.request_id,
                                },
                            )
                    error_code = getattr(walker, "_bootstrap_error", None)
                    error_detail = next(
                        (
                            item.get("error")
                            for item in (report or [])
                            if isinstance(item, dict) and "error" in item
                        ),
                        None,
                    )
                    logger.error(
                        "interact_not_created",
                        extra={
                            "agent_id": agent_id,
                            "channel": channel,
                            "request_id": profile.request_id,
                            "bootstrap_error": error_code,
                            "detail": error_detail,
                        },
                    )
                    msg = "Interaction was not created during traversal"
                    if error_code:
                        msg = f"{msg} [{error_code}]"
                    raise RuntimeError(msg)

                # Mark interaction as not streamed
                interaction.streamed = False

                # Clear interaction from context
                from jvagent.action.model.context import set_interaction

                set_interaction(None)

                await interaction.close_interaction()

                # Flush deferred saves (interaction and conversation) with error handling
                async with profile.measure("flush_saves"):
                    await flush_deferred_entities(
                        interaction, walker.conversation, strict=False
                    )

                # Compute usage after flush so all model_call events are present
                await _finalize_usage(interaction)

                # Log interaction using INTERACTION level
                try:
                    from jvagent.core.app import App

                    app = await App.get()
                    app_id = app.id if app else ""
                    active_tasks = []
                    completed_tasks = []
                    if walker.conversation:
                        active_tasks = walker.conversation.get_tasks(status="active")
                        completed_tasks = walker.conversation.get_tasks(
                            status="completed"
                        )
                    log_data, message = _build_interaction_log_data(
                        interaction,
                        app_id,
                        agent_id,
                        active_tasks=active_tasks,
                        completed_tasks=completed_tasks,
                        visitor_data=walker.data,
                    )
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

                # Fire background actions (await in Lambda to ensure it finishes before the execution freezes)
                if walker.background_actions:
                    await _run_background_actions(walker)

                return result

        except JVSpatialAPIException:
            # Preserve typed API errors (404 ResourceNotFoundError, 429 RateLimitError,
            # 422 ValidationError, etc.) so the correct HTTP status and message are
            # returned to the client instead of being collapsed into a generic 422.
            raise
        except ValueError as e:
            raise ValidationError(message=str(e), details={"error": str(e)})
        except Exception as e:
            logger.error(
                "Error in interact endpoint: %s request_id=%s",
                e,
                profile.request_id,
                exc_info=True,
            )
            raise ValidationError(
                message=_STREAM_CLIENT_ERROR,
                details={"request_id": profile.request_id},
            ) from e
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
    walker: InteractWalker, agent: Agent, request: Optional[Request] = None
) -> AsyncGenerator[str, None]:
    """Stream interaction as SSE chunks.

    Args:
        walker: InteractWalker instance
        agent: Agent node
        request: Optional FastAPI Request used to detect client disconnection so
            the in-flight walker can be cancelled when the user stops generation.

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

    walk_task: Optional[asyncio.Task] = None
    try:
        # Start walker in background (concurrent with early interaction polling).
        walker_start = time.time()
        walk_task = cast(
            asyncio.Task,
            await create_task(
                walker.spawn(agent),
                name="interact_stream_spawn",
                concurrent=True,
            ),
        )

        # Wait for interaction to be created
        max_wait = 5.0  # Maximum seconds to wait for interaction
        waited = 0.0
        while not walker.interaction and waited < max_wait:
            if request is not None and await request.is_disconnected():
                walk_task.cancel()
                return
            await asyncio.sleep(0.1)
            waited += 0.1

        if not walker.interaction:
            try:
                await walk_task
            except Exception as e:
                logger.error(
                    "Stream walker failed before interaction: request_id=%s",
                    profile.request_id,
                    exc_info=True,
                )
                yield format_sse_chunk(_sse_error_event(profile.request_id))
                return
            stream_report = await walker.get_report()
            for item in stream_report or []:
                if isinstance(item, dict) and item.get("access_denied"):
                    yield format_sse_chunk(
                        _sse_error_event(
                            profile.request_id,
                            message="Access denied.",
                        )
                    )
                    return
            error_code = getattr(walker, "_bootstrap_error", None)
            error_detail = next(
                (
                    item.get("error")
                    for item in (stream_report or [])
                    if isinstance(item, dict) and "error" in item
                ),
                None,
            )
            logger.error(
                "interact_not_created",
                extra={
                    "agent_id": walker.agent_id,
                    "channel": walker.channel,
                    "request_id": profile.request_id,
                    "bootstrap_error": error_code,
                    "detail": error_detail,
                    "stream": True,
                },
            )
            stream_msg = "Interaction was not created during traversal."
            if error_code:
                stream_msg = (
                    f"Interaction was not created during traversal [{error_code}]."
                )
            yield format_sse_chunk(
                _sse_error_event(
                    profile.request_id,
                    message=stream_msg,
                )
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
                        except Exception:
                            profile.record(
                                "walker_execution", time.time() - walker_start
                            )
                            logger.error(
                                "Stream walker task failed: request_id=%s",
                                profile.request_id,
                                exc_info=True,
                            )
                            yield format_sse_chunk(_sse_error_event(profile.request_id))
                        break

                    # Client-disconnect detection: stop the walker if the user
                    # cancelled generation (closed the SSE connection).
                    if request is not None and await request.is_disconnected():
                        logger.info(
                            "Client disconnected; cancelling interact walker: "
                            "interaction_id=%s session_id=%s request_id=%s",
                            getattr(interaction, "id", None),
                            walker.session_id,
                            profile.request_id,
                        )
                        walk_task.cancel()
                        return

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
            await flush_deferred_entities(
                interaction, walker.conversation, strict=False
            )

            # Compute usage after flush so all model_call events are present
            await _finalize_usage(interaction)

            # Log interaction using INTERACTION level
            try:
                from jvagent.core.app import App

                app = await App.get()
                app_id = app.id if app else ""
                active_tasks = []
                completed_tasks = []
                if walker.conversation:
                    active_tasks = walker.conversation.get_tasks(status="active")
                    completed_tasks = walker.conversation.get_tasks(status="completed")
                agent_id_for_logging = (
                    walker.agent_id if hasattr(walker, "agent_id") else agent.id
                )
                log_data, message = _build_interaction_log_data(
                    interaction,
                    app_id,
                    agent_id_for_logging,
                    active_tasks=active_tasks,
                    completed_tasks=completed_tasks,
                    visitor_data=walker.data,
                )
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

            # Fire background actions after final chunk is yielded
            if walker.background_actions:
                await create_task(
                    _run_background_actions(walker),
                    name="interact_background_actions",
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
            await flush_deferred_entities(
                interaction, walker.conversation, strict=False
            )

            # Compute usage after flush so all model_call events are present
            await _finalize_usage(interaction)

            # Log interaction using INTERACTION level
            try:
                from jvagent.core.app import App

                app = await App.get()
                app_id = app.id if app else ""
                active_tasks = []
                completed_tasks = []
                if walker.conversation:
                    active_tasks = walker.conversation.get_tasks(status="active")
                    completed_tasks = walker.conversation.get_tasks(status="completed")
                agent_id_from_walker = (
                    walker.agent_id if hasattr(walker, "agent_id") else None
                )
                log_data, message = _build_interaction_log_data(
                    interaction,
                    app_id,
                    agent_id_from_walker,
                    active_tasks=active_tasks,
                    completed_tasks=completed_tasks,
                    visitor_data=walker.data,
                )
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

            # Fire background actions after final chunk is yielded
            if walker.background_actions:
                await create_task(
                    _run_background_actions(walker),
                    name="interact_background_actions",
                )

            # Log profile summary
            await finalize_profile(profile.request_id, log=True)

    except Exception as e:
        logger.error(
            "Error in stream_interaction: %s request_id=%s",
            e,
            profile.request_id,
            exc_info=True,
        )
        # Still log profile on error
        profile.record("total_stream_time", time.time() - stream_start_time)
        profile.record("error", 0)  # Mark that an error occurred
        await finalize_profile(profile.request_id, log=True)
        yield format_sse_chunk(_sse_error_event(profile.request_id))
    finally:
        # If the generator exits while the walker is still running (e.g. client
        # disconnect propagated as GeneratorExit, or an unexpected error), cancel
        # the walker so it does not keep working on a dropped request.
        if walk_task is not None and not walk_task.done():
            walk_task.cancel()
            try:
                await walk_task
            except (asyncio.CancelledError, Exception):
                pass
        # Clear profile context
        set_current_profile(None)
        # Request-scoped cache cleanup (probabilistic, serverless-friendly)
        try:
            from jvagent.core.cache import maybe_cleanup_on_request

            await maybe_cleanup_on_request()
        except Exception:
            pass  # Cleanup errors should never fail the request
