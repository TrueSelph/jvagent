"""Example Persona Action implementation.

This module provides an example PersonaAction with custom configuration
for the example agent.
"""

import logging
from typing import Any, Dict, List

from jvspatial.core.annotations import attribute

from jvagent.action.persona.base import PersonaAction

logger = logging.getLogger(__name__)


class ExamplePersonaAction(PersonaAction):
    """Example PersonaAction with custom persona configuration.

    This is an example implementation showing how to customize the PersonaAction
    for a specific agent. The persona is configured with:
    - Custom name, role, and description
    - Specific capabilities
    - Custom parameters

    Configuration can be overridden via agent.yaml context.
    """

    # Override persona defaults for the example agent
    persona_name: str = attribute(
        default="Example Assistant",
        description="Agent display name",
    )
    persona_role: str = attribute(
        default="A helpful AI assistant for demonstrations",
        description="Agent role description",
    )
    persona_description: str = attribute(
        default=(
            "You are a friendly and knowledgeable assistant that helps users "
            "understand how the jvagent framework works. You provide clear, "
            "concise answers and demonstrate best practices."
        ),
        description="Detailed agent description",
    )
    persona_capabilities: List[str] = attribute(
        default_factory=lambda: [
            "Answer questions about jvagent",
            "Demonstrate action delegation",
            "Process user interactions with behavioral parameters",
            "Provide streaming and non-streaming responses",
        ],
        description="List of agent capabilities",
    )

    # Custom parameters for this agent
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "condition": "User asks about jvagent",
                "response": "Explain jvagent as a modular AI agent framework built on jvspatial.",
            },
            {
                "condition": "User requests a demonstration",
                "response": "Provide a brief demonstration with example outputs.",
            },
            {
                "condition": "User asks technical questions",
                "response": "Give accurate technical details while keeping explanations accessible.",
            },
        ],
        description="Standard collection of configurable parameters to apply when executing the prompt",
    )

    async def on_register(self) -> None:
        """Initialize the example persona action."""
        await super().on_register()
        logger.info(
            f"ExamplePersonaAction '{self.label}' registered with persona: {self.persona_name}"
        )

