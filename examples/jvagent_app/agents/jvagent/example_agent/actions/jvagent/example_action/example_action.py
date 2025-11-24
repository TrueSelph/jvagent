"""Example Action Implementation

This is a boilerplate action that demonstrates the structure and lifecycle
of a custom action in jvagent.

All configuration is done via typed Pydantic fields, not a config dictionary.
"""

import logging
from typing import Any, Dict

from jvagent.action.base import Action
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)


class ExampleAction(Action):
    """Example action implementation.

    This action demonstrates:
    - Basic action structure
    - Lifecycle hooks
    - Type-safe configuration via properties
    - File operations
    - Runtime property updates

    Configuration Properties:
        All action configuration should be defined using the attribute() standard.
        These can be overridden in agent.yaml and updated at runtime via the API.
    """

    # Configuration properties (type-safe, validated)
    timeout: int = attribute(default=30, description="Operation timeout in seconds", ge=1)
    retries: int = attribute(default=3, description="Number of retry attempts", ge=0, le=10)
    api_endpoint: str = attribute(default="https://api.example.com", description="API endpoint URL")

    # Test properties for runtime updates
    var_a: int = attribute(default=5, description="First variable for multiplication test")
    var_b: int = attribute(default=10, description="Second variable for multiplication test")

    async def on_register(self) -> None:
        """Called when action is registered.

        Use this hook to:
        - Initialize resources
        - Validate configuration
        - Set up connections
        """
        # Access configuration properties directly
        logger.info(f"ExampleAction registered:")
        logger.info(f"  Timeout: {self.timeout}s")
        logger.info(f"  Retries: {self.retries}")
        logger.info(f"  API Endpoint: {self.api_endpoint}")

    async def on_enable(self) -> None:
        """Called when action is enabled.

        Use this hook to:
        - Start background tasks
        - Initialize active resources
        - Connect to external services
        """
        logger.info(f"ExampleAction enabled (timeout={self.timeout}s)")

    async def on_disable(self) -> None:
        """Called when action is disabled.

        Use this hook to:
        - Stop background tasks
        - Clean up active resources
        - Disconnect from external services
        """
        logger.info("ExampleAction disabled")

    async def on_reload(self) -> None:
        """Called when action is reloaded.

        Use this hook to:
        - Refresh configuration
        - Reinitialize resources
        - Update connections
        """
        logger.info("ExampleAction reloaded")

    async def post_register(self) -> None:
        """Called after all actions are registered.

        Use this hook to:
        - Perform cross-action initialization
        - Set up inter-action communication
        - Validate action dependencies
        """
        logger.info("ExampleAction post-register complete")

    async def pulse(self) -> Dict[str, Any]:
        """Called for periodic operations.

        Returns:
            Dictionary with pulse status information
        """
        return {"status": "healthy", "timeout": self.timeout, "retries": self.retries}

    async def healthcheck(self) -> Dict[str, Any]:
        """Perform health check.

        Returns:
            Dictionary with health status
        """
        return {
            "healthy": True,
            "status": "operational",
            "config": {
                "timeout": self.timeout,
                "retries": self.retries,
                "api_endpoint": self.api_endpoint,
            },
        }

    # ============================================================================
    # Custom Action Methods
    # ============================================================================

    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the action with input data.

        Args:
            input_data: Input parameters for the action

        Returns:
            Result dictionary
        """
        # Example: Use configuration properties
        logger.debug(f"Executing with timeout: {self.timeout}s, retries: {self.retries}")

        result = {
            "processed": True,
            "input": input_data,
            "output": "Action executed successfully",
            "timeout_used": self.timeout,
        }

        return result
