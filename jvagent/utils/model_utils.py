"""Utility functions for model calls and response handling."""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def model_call_to_json(
    model_action,
    prompt: str,
    system: Optional[str] = None,
    model: str = "gpt-4o",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    interaction = None,
    fallback_response: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Make a model call, convert the result to JSON, and return it.

    This is a single function that:
    1. Makes a call to the model action
    2. Parses the response as JSON
    3. Returns the parsed JSON dictionary

    Args:
        model_action: The ModelAction instance to use for the call
        prompt: User prompt for the model
        system: Optional system message
        model: Model name (default: "gpt-4o")
        temperature: Temperature for generation (default: 0.3)
        max_tokens: Maximum tokens (default: 4096)
        interaction: Optional Interaction to log to
        fallback_response: Optional fallback dict if parsing fails

    Returns:
        Dictionary containing the parsed JSON response from the model.
        If parsing fails, returns fallback_response or an error dict.

    Example:
        ```python
        from jvagent.action.model.base import ModelAction
        from jvagent.utils.model_utils import model_call_to_json

        # Get model action from agent
        model_action = await agent.get_action_by_type("OpenAIModelAction")

        # Make call and get JSON result
        result = await model_call_to_json(
            model_action=model_action,
            prompt="Generate a user profile as JSON",
            system="You are a helpful assistant that returns JSON"
        )

        # Use the result
        print(result)  # {'name': 'John', 'age': 30, ...}
        ```
    """
    if not model_action:
        logger.error("Model action is None")
        return fallback_response or {"error": "model_action_not_provided"}

    try:
        # Make the model call
        result = await model_action.query(
            prompt=prompt,
            system=system,
            history=None,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Log model result if interaction provided
        if interaction and result:
            interaction.add_action(type(model_action).__name__)
            interaction.add_model_result(result.to_dict())

        # Check if we got a response
        if not result or not result.response:
            logger.warning("Model returned no response")
            return fallback_response or {"error": "no_response"}

        # Parse the JSON response
        try:
            parsed_json = json.loads(result.response)
            return parsed_json
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse model response as JSON: {e}")
            logger.error(f"Response was: {result.response[:200]}")
            return fallback_response or {
                "error": "json_parse_error",
                "message": str(e),
                "raw_response": result.response[:500]
            }

    except Exception as e:
        logger.error(f"Error in model_call_to_json: {e}", exc_info=True)
        return fallback_response or {
            "error": "model_call_failed",
            "message": str(e)
        }
