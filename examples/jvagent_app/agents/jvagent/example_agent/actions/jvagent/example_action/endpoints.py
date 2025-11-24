"""API endpoints for the example action.

This module defines all HTTP endpoints for the example action.
Endpoints are automatically discovered and registered when this module is imported.
"""

import logging
from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError

from .example_action import ExampleAction

logger = logging.getLogger(__name__)


@endpoint(
    "/actions/{action_id}/multiply",
    methods=["GET"],
    auth=True,
    tags=["Example Action"],
    response=success_response(
        data={
            "result": ResponseField(
                field_type=int,
                description="Result of var_a * var_b",
            ),
            "var_a": ResponseField(
                field_type=int,
                description="First variable value",
            ),
            "var_b": ResponseField(
                field_type=int,
                description="Second variable value",
            ),
        }
    ),
)
async def multiply_endpoint(action_id: str) -> Dict[str, Any]:
    """Calculate and return var_a * var_b.

    This endpoint demonstrates runtime property access and can be used
    to test that property updates via the update endpoint take effect.

    Args:
        action_id: ID of the action

    Returns:
        Dictionary with multiplication result and current variable values
    """
    action = await ExampleAction.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found",
            details={"action_id": action_id},
        )

    # Access properties directly from the ExampleAction instance
    result = action.var_a * action.var_b

    logger.info(f"Multiply endpoint called: {action.var_a} * {action.var_b} = {result}")

    return {
        "result": result,
        "var_a": action.var_a,
        "var_b": action.var_b,
    }
