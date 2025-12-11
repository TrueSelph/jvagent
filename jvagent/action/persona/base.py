"""PersonaAction base class - simplified tool-based action.

This module provides a simplified PersonaAction class that serves as a tool-based
action for applying agent prompts with configurable parameters.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.persona.prompts import (
    AGENT_PROMPT_TEMPLATE,
    DIRECTIVES_INSTRUCTION,
    NO_DIRECTIVES_INSTRUCTION,
    NO_PARAMETERS_INSTRUCTION,
    PARAMETER_DIRECTIVE,
    PARAMETERS_INSTRUCTION,
)
from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class PersonaAction(Action):
    """Simplified tool-based action for applying agent prompts.

    PersonaAction is a tool-based action that applies the main agent prompt
    with configurable parameters. It provides a simple interface for generating
    responses using language models.

    Attributes:
        prompt: Main agent prompt template (default property)
        parameters: Standard collection of configurable parameters to apply when executing the prompt
        model_action_type: Entity type of the LanguageModelAction (e.g., "OpenAILanguageModelAction")
        model_name: Default model name
        model_temperature: Temperature for LLM generation
        model_max_tokens: Max tokens for LLM generation
        persona_name: Agent display name (for prompt formatting)
        persona_role: Agent role description (for prompt formatting)
        persona_description: Detailed agent description (for prompt formatting)
        persona_capabilities: List of agent capabilities (for prompt formatting)
    """

    # Main prompt (default property)
    prompt: str = attribute(
        default="", description="Main agent prompt template"
    )

    # Standard collection of configurable parameters
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="Standard collection of configurable parameters to apply when executing the prompt",
    )

    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use (e.g., OpenAILanguageModelAction)",
    )
    model_name: str = attribute(
        default="gpt-4o", description="Default model name"
    )
    model_temperature: float = attribute(
        default=0.3, description="Temperature for LLM generation"
    )
    model_max_tokens: int = attribute(
        default=4096, description="Max tokens for LLM generation"
    )

    # Persona attributes (for prompt formatting if using default template)
    persona_name: str = attribute(
        default="Agent", description="Agent display name"
    )
    persona_role: str = attribute(
        default="An AI Assistant", description="Agent role description"
    )
    persona_description: str = attribute(
        default="You are friendly and helpful",
        description="Detailed agent description",
    )
    persona_capabilities: List[str] = attribute(
        default_factory=list, description="List of agent capabilities"
    )

    async def on_register(self) -> None:
        """Initialize when action is registered."""
        await super().on_register()
        logger.info(f"PersonaAction '{self.label}' registered")

    async def respond(
        self,
        interaction: Interaction,
        visitor: Optional[Any] = None,
    ) -> str:
        """Main utility method for generating a response.

        Accepts the active interaction object with the utterance, injected directives
        and parameters, then makes the language model call with the composed prompt
        and returns the generated response.

        Args:
            interaction: The active interaction object containing:
                - utterance: User's input text
                - directives: Injected directives from other actions
                - parameters: Applicable parameters for this interaction

        Returns:
            Generated response string from the language model

        Raises:
            RuntimeError: If PersonaAction is not attached to an agent or model action not found
        """
        # Get agent
        agent = await self.get_agent()
        if not agent:
            raise RuntimeError("PersonaAction not attached to an agent")

        # Get model action
        from jvagent.action.model.language.base import LanguageModelAction

        model_action = await agent.get_action_by_type(self.model_action_type)
        if not model_action or not isinstance(model_action, LanguageModelAction):
            raise RuntimeError(
                f"Model action of type '{self.model_action_type}' not found for agent"
            )

        # Record PersonaAction in interaction
        interaction.add_action("PersonaAction")

        # Compose the prompt
        system_prompt = self._compose_prompt(interaction)

        streaming = bool(
            visitor
            and getattr(visitor, "stream_mode", True)
            and getattr(visitor, "response_bus", None)
            and getattr(visitor, "session_id", None)
        )

        # Streaming callbacks to ResponseBus (if available)
        def on_chunk(chunk: str) -> None:
            if not streaming:
                return
            try:
                asyncio.create_task(
                    visitor.response_bus.publish_message(  # type: ignore[attr-defined]
                        session_id=visitor.session_id,  # type: ignore[attr-defined]
                        content=chunk,
                        channel=getattr(visitor, "channel", "default"),
                        message_type="stream_chunk",
                        interaction_id=interaction.id,
                    )
                )
            except Exception as exc:
                logger.warning("PersonaAction on_chunk callback error: %s", exc, exc_info=True)

        def on_end(full_text: str) -> None:
            if not streaming:
                return
            try:
                asyncio.create_task(
                    visitor.response_bus.publish_message(  # type: ignore[attr-defined]
                        session_id=visitor.session_id,  # type: ignore[attr-defined]
                        content=full_text,
                        channel=getattr(visitor, "channel", "default"),
                        message_type="final",
                        interaction_id=interaction.id,
                    )
                )
                interaction.set_response(full_text)
            except Exception as exc:
                logger.warning("PersonaAction on_end callback error: %s", exc, exc_info=True)

        # Make the language model call
        try:
            response = await model_action.generate(
                prompt=interaction.utterance,
                stream=streaming,
                system=system_prompt,
                model=self.model_name,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                on_stream_chunk=on_chunk if streaming else None,
                on_stream_end=on_end if streaming else None,
            )

            # Log the model result
            interaction.add_action(self.model_action_type)
            interaction.add_model_result(
                {
                    "response": response,
                    "model": self.model_name,
                    "is_streaming": streaming,
                }
            )

            # Set response on interaction
            if response:
                interaction.set_response(response)

            # If not streaming, optionally publish a single message to bus
            if (not streaming) and visitor and getattr(visitor, "response_bus", None) and getattr(visitor, "session_id", None):
                await visitor.response_bus.publish_message(  # type: ignore[attr-defined]
                    session_id=visitor.session_id,  # type: ignore[attr-defined]
                    content=response,
                    channel=getattr(visitor, "channel", "default"),
                    message_type="final",
                    interaction_id=interaction.id,
                )
            interaction.set_to_executed(interaction.parameters,interaction.get_directives())
            interaction.parameters = []
            return response

        except Exception as e:
            logger.error(f"Error in PersonaAction.respond: {e}", exc_info=True)
            raise

    def _compose_prompt(self, interaction: Interaction) -> str:
        """Compose the system prompt from the main prompt and interaction context.

        Args:
            interaction: The active interaction object

        Returns:
            Composed system prompt string
        """
        # Use the main prompt if provided, otherwise use default template
        if self.prompt:
            base_prompt = self.prompt
        else:
            # Use default template with persona attributes
            now = datetime.now()
            date_str = now.strftime("%A, %d %B, %Y")
            time_str = now.strftime("%I:%M %p")

            base_prompt = AGENT_PROMPT_TEMPLATE.format(
            agent_name=self.persona_name,
            agent_role=self.persona_role,
            agent_description=self.persona_description,
                agent_capabilities="\n-".join(self.persona_capabilities) if self.persona_capabilities else "",
                user=interaction.user_id or "user",
                date=date_str,
                time=time_str,
                parameters="",  # Will be added below
                directives="",  # Will be added below
            )

        # Build parameters section from interaction parameters and configured parameters
        all_parameters = list(self.parameters)  # Start with configured parameters

        applicable_parameters = [
            p for p in interaction.parameters
            if p not in interaction._executed_parameters
        ]
        all_parameters.extend(applicable_parameters)  # Add interaction parameters
        interaction.set_to_executed(parameters=applicable_parameters) # Add parameters to executed list

        if all_parameters:
            # Format parameters for prompt
            params_str = "\n".join(
                f"{i+1}. {self._format_parameter(p)}"
                for i, p in enumerate(all_parameters)
            )
            parameters_prompt = (
                f"{PARAMETER_DIRECTIVE}\n{params_str}\n{PARAMETERS_INSTRUCTION}"
            )
        else:
            parameters_prompt = NO_PARAMETERS_INSTRUCTION

        # Build directives section from interaction directives if not already executed
        applicable_directives = [
            d for d in interaction.directives
            if d not in interaction._executed_directives
        ]
        directives = applicable_directives
        if directives:
            interaction.set_to_executed(directives=applicable_directives)  # Add directives to executed list
            directives_str = "\n".join(
                f"{i+1}. {d}" for i, d in enumerate(directives)
            )
            directives_prompt = f"{DIRECTIVES_INSTRUCTION}\n{directives_str}"
        else:
            directives_prompt = NO_DIRECTIVES_INSTRUCTION

        # Compose final prompt
        final_prompt = base_prompt
        if "{parameters}" in base_prompt:
            final_prompt = final_prompt.replace("{parameters}", parameters_prompt)
        else:
            final_prompt += f"\n\n{parameters_prompt}"

        if "{directives}" in base_prompt:
            final_prompt = final_prompt.replace("{directives}", directives_prompt)
        else:
            final_prompt += f"\n\n{directives_prompt}"

        return final_prompt

    def _format_parameter(self, param: Dict[str, Any]) -> str:
        """Format a parameter dictionary for inclusion in the prompt.

        Args:
            param: Parameter dictionary (may have 'condition', 'response', etc.)

        Returns:
            Formatted parameter string
        """
        if isinstance(param, dict):
            condition = param.get("condition", "")
            response = param.get("response", "")
            if condition and response:
                return f"When {condition}, {response.lower()}"
            elif condition:
                return condition
            elif response:
                return response
            else:
                return str(param)
        return str(param)

    async def healthcheck(self) -> bool:
        """Check if the PersonaAction is healthy.

        Returns:
            True if healthy, False otherwise
        """
        try:
            # Check if model action is available
            agent = await self.get_agent()
            if agent and self.model_action_type:
                from jvagent.action.model.language.base import LanguageModelAction
                action = await agent.get_action_by_type(self.model_action_type)
                if not action or not isinstance(action, LanguageModelAction):
                    return False

            return True
        except Exception as e:
            logger.error(f"Healthcheck failed: {e}")
            return False
