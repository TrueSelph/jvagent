"""API endpoints for model actions.

Provides HTTP endpoints that wrap the programmatic model action interface,
supporting both synchronous and streaming queries.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi.responses import StreamingResponse
from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from jvagent.action.model.language.base import LanguageModelAction

logger = logging.getLogger(__name__)


# ============================================================================
# Query Endpoint
# ============================================================================


@endpoint(
    "/actions/{action_id}/query",
    methods=["POST"],
    auth=True,
    tags=["Model Action"],
    response=success_response(
        data={
            "response": ResponseField(
                field_type=str,
                description="Complete response text (sync mode)",
            ),
            "metrics": ResponseField(
                field_type=Dict[str, Any],
                description="Query metrics including token usage and duration",
                example={
                    "prompt_tokens": 20,
                    "completion_tokens": 150,
                    "total_tokens": 170,
                    "duration": 1.234,
                },
            ),
            "model": ResponseField(
                field_type=str,
                description="Model identifier used",
                example="gpt-4o-mini",
            ),
            "provider": ResponseField(
                field_type=str,
                description="Provider name",
                example="openai",
            ),
            "finish_reason": ResponseField(
                field_type=str,
                description="Completion finish reason",
                example="stop",
            ),
            "tool_calls": ResponseField(
                field_type=List[Dict[str, Any]],
                description="Function calls made (if any)",
            ),
        }
    ),
)
async def query_model_action(
    action_id: str,
    prompt: Any,  # Can be string or list of content parts
    stream: bool = False,
    system: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
) -> Any:
    """Query a model action with a prompt.

    This endpoint handles language model queries through the model action.
    Supports both text-only and multimodal (text + images) queries:

    - Supports both synchronous and streaming responses
    - Handles text-only and multimodal (text + images) queries
    - Allows runtime model parameter overrides

    Request Modes:

    1. Text Query (simple string prompt):
       Provides a text prompt and receives a text response

    2. Multimodal Query (array with content parts):
       Provides text and images for vision-capable models

    3. Streaming Query (stream=true):
       Returns Server-Sent Events (SSE) with response chunks

    Args:
        action_id: ID of the model action to query
        prompt: User prompt - can be:

            - String: Simple text prompt
            - List: Multimodal content with text and images

        stream: Whether to stream the response (default: False)
        system: Optional system message to guide model behavior
        history: Optional conversation history (can include multimodal)
        tools: Optional list of tool/function definitions for function calling
        model: Optional model override (uses action's default model if not provided)
        temperature: Optional temperature override (0.0-2.0)
        max_tokens: Optional max tokens override
        top_p: Optional top_p override (0.0-1.0)

    Returns:
        For sync: Dictionary with response, metrics, and metadata

        For stream: StreamingResponse with SSE events containing:

            - delta: Response text chunk
            - metrics: Token usage (on final event)
            - finish_reason: Completion reason (on final event)

    Raises:
        ResourceNotFoundError: If action not found

    Examples:
        Text query::

            POST /actions/abc123/query
            {
                "prompt": "Explain quantum computing",
                "system": "You are a physics expert",
                "model": "gpt-4o",
                "temperature": 0.7
            }

        Multimodal query::

            POST /actions/abc123/query
            {
                "prompt": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://..."}}
                ]
            }

        Streaming query::

            POST /actions/abc123/query
            {
                "prompt": "Tell me a story",
                "stream": true
            }
    """
    # Get the model action
    action = await LanguageModelAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Model action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    # Build kwargs for query
    kwargs: Dict[str, Any] = {}
    if model is not None:
        kwargs["model"] = model
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if top_p is not None:
        kwargs["top_p"] = top_p

    # Execute query
    result = await action.query(
        prompt=prompt, stream=stream, system=system, history=history, tools=tools, **kwargs
    )

    # Handle streaming response
    if stream:

        async def event_stream():
            """Generate SSE events for streaming response."""
            try:
                # Stream content chunks
                async for chunk in result.iter_stream():
                    event = {
                        "delta": chunk,
                        "metrics": None,
                        "finish_reason": None,
                    }
                    yield f"data: {json.dumps(event)}\n\n"

                # Send final event with metrics and finish reason
                final_event = {
                    "delta": "",
                    "metrics": result.metrics,
                    "finish_reason": result.finish_reason,
                    "tool_calls": result.tool_calls,
                }
                yield f"data: {json.dumps(final_event)}\n\n"
                yield "data: [DONE]\n\n"

            except Exception as e:
                logger.error(f"Streaming error: {e}")
                error_event = {
                    "error": str(e),
                }
                yield f"data: {json.dumps(error_event)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # Handle sync response
    return result.to_dict()


# ============================================================================
# Metrics Endpoint
# ============================================================================


@endpoint(
    "/actions/{action_id}/metrics",
    methods=["GET"],
    auth=True,
    tags=["Model Action"],
    response=success_response(
        data={
            "total_requests": ResponseField(
                field_type=int,
                description="Total number of requests made",
                example=150,
            ),
            "total_tokens": ResponseField(
                field_type=int,
                description="Cumulative token usage",
                example=45000,
            ),
            "total_cost": ResponseField(
                field_type=float,
                description="Estimated total cost in USD",
                example=0.675,
            ),
            "total_duration": ResponseField(
                field_type=float,
                description="Cumulative query duration in seconds",
                example=125.5,
            ),
            "average_duration": ResponseField(
                field_type=float,
                description="Average query duration in seconds",
                example=0.837,
            ),
            "model": ResponseField(
                field_type=str,
                description="Model identifier",
                example="gpt-4o-mini",
            ),
            "provider": ResponseField(
                field_type=str,
                description="Provider name",
                example="openai",
            ),
        }
    ),
)
async def get_model_action_metrics(action_id: str) -> Dict[str, Any]:
    """Get usage metrics for a model action.

    Returns comprehensive usage statistics including:

    - Total requests made through this action
    - Cumulative token usage (prompt + completion)
    - Estimated cost in USD based on model pricing
    - Total and average query duration

    Metrics are accumulated across all queries and persist until reset.

    Args:
        action_id: ID of the model action

    Returns:
        Dictionary with metrics including:

            - total_requests: Number of queries made
            - total_tokens: Cumulative token usage
            - total_cost: Estimated cost in USD
            - total_duration: Cumulative query time in seconds
            - average_duration: Average query time in seconds
            - model: Model identifier
            - provider: Provider name (openai, openrouter, etc.)

    Raises:
        ResourceNotFoundError: If action not found
    """
    action = await LanguageModelAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Model action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    # Calculate average duration
    average_duration = (
        action.total_duration / action.total_requests if action.total_requests > 0 else 0.0
    )

    return {
        "total_requests": action.total_requests,
        "total_tokens": action.total_tokens,
        "total_cost": action.total_cost,
        "total_duration": action.total_duration,
        "average_duration": average_duration,
        "model": action.model,
        "provider": getattr(
            action, "provider", action.get_class_name().replace("LanguageModelAction", "").lower()
        ),
    }


# ============================================================================
# Template Endpoint
# ============================================================================


@endpoint(
    "/actions/{action_id}/templates",
    methods=["GET"],
    auth=True,
    tags=["Model Action"],
    response=success_response(
        data={
            "templates": ResponseField(
                field_type=List[str],
                description="List of available template names",
            ),
        }
    ),
)
async def list_model_action_templates(action_id: str) -> Dict[str, Any]:
    """List available prompt templates for a model action.

    Templates are reusable prompt patterns that can be rendered with variables.
    They are useful for:

    - Standardizing prompt formats across queries
    - Reducing code duplication for common prompts
    - Managing prompt versioning and updates

    Args:
        action_id: ID of the model action

    Returns:
        Dictionary with list of available template names

    Raises:
        ResourceNotFoundError: If action not found
    """
    action = await LanguageModelAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Model action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    # Get template manager and list templates
    from jvagent.action.model.language.templates import TemplateManager

    manager = TemplateManager(action)
    templates = await manager.list_templates()

    return {
        "templates": templates,
    }


@endpoint(
    "/actions/{action_id}/templates/{template_name}/render",
    methods=["POST"],
    auth=True,
    tags=["Model Action"],
    response=success_response(
        data={
            "rendered": ResponseField(
                field_type=str,
                description="Rendered template string",
            ),
        }
    ),
)
async def render_model_action_template(
    action_id: str,
    template_name: str,
    variables: Dict[str, Any],
) -> Dict[str, Any]:
    """Render a prompt template with provided variables.

    Substitutes placeholders in the template with actual values.
    Templates use Python string formatting syntax (e.g., {variable_name}).

    Example::

        Template: "Summarize the following {content_type}: {content}"
        Variables: {"content_type": "article", "content": "..."}
        Result: "Summarize the following article: ..."

    Args:
        action_id: ID of the model action
        template_name: Name of the template to render
        variables: Dictionary mapping variable names to values

    Returns:
        Dictionary with rendered template string

    Raises:
        ResourceNotFoundError: If action or template not found
    """
    action = await LanguageModelAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Model action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    try:
        rendered = await action.apply_template(template_name, **variables)
        return {
            "rendered": rendered,
        }
    except Exception as e:
        logger.error(f"Template rendering failed: {e}")
        raise ResourceNotFoundError(
            message=f"Template '{template_name}' not found or rendering failed",
            details={"template_name": template_name, "error": str(e)},
        )
