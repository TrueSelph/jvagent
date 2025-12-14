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
    ACTION_PARAMETERS_SECTION_TEMPLATE,
    AGENT_PROMPT_TEMPLATE,
    DIRECTIVE_GROUP_TEMPLATE,
    DIRECTIVES_INSTRUCTION,
    NO_DIRECTIVES_INSTRUCTION,
    NO_PARAMETERS_INSTRUCTION,
    PARAMETER_CONDITION_EVALUATION_INSTRUCTION,
    PARAMETER_DIRECTIVE,
    PARAMETERS_INSTRUCTION,
    STANDALONE_PARAMETERS_SECTION_TEMPLATE,
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
        model: Default model name
        model_temperature: Temperature for LLM generation
        model_max_tokens: Max tokens for LLM generation
        persona_name: Agent display name (for prompt formatting)
        persona_role: Agent role description (for prompt formatting)
        persona_description: Detailed agent description (for prompt formatting)
        persona_capabilities: List of agent capabilities (for prompt formatting)
    """

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

    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use (e.g., OpenAILanguageModelAction)",
    )
    model: str = attribute(
        default="gpt-4o", description="Default model name"
    )
    model_temperature: float = attribute(
        default=0.3, description="Temperature for LLM generation"
    )
    model_max_tokens: int = attribute(
        default=4096, description="Max tokens for LLM generation"
    )

    # Main prompt (default property)
    prompt: str = attribute(
        default="", description="Main agent prompt template"
    )

    # Standard collection of configurable parameters
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description="Standard collection of configurable parameters to apply when executing the prompt",
    )

    async def on_register(self) -> None:
        """Initialize when action is registered."""
        await super().on_register()
        logger.info(f"PersonaAction '{self.label}' registered")

    async def respond(
        self,
        interaction: Interaction,
        visitor: Optional[Any] = None,
        use_utterance: bool = True,
        use_history: bool = True,
        history_limit: int = 3,
        with_interpretation: bool = False,
        with_event: bool = False,
        with_response: bool = True,
        max_statement_length: Optional[int] = None,
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
            visitor: Optional InteractWalker for streaming support
            use_utterance: Whether to include the user's utterance in the prompt (default: True)
            use_history: Whether to include conversation history (default: True)
            history_limit: Number of past interactions to include in history (default: 3)
            with_interpretation: Include interpretations in history (default: False)
            with_event: Include events in history (default: False)
            with_response: Include AI responses in history (default: True)
            max_statement_length: Truncate utterances/responses to this length (default: None, no truncation)

        Returns:
            Generated response string from the language model

        Raises:
            RuntimeError: If PersonaAction is not attached to an agent or model action not found
        """
        # Get model action (required=True raises error if not found)
        model_action = await self.get_model_action(required=True)

        # Record PersonaAction in interaction
        interaction.add_action("PersonaAction")

        # Validate that there are directives and/or parameters to proceed
        # Get unexecuted directives and parameters from interaction
        applicable_directives = interaction.get_unexecuted_directives()
        applicable_parameters = interaction.get_unexecuted_parameters()
        
        # Check PersonaAction's own parameters
        persona_parameters = list(self.parameters) if self.parameters else []
        
        # Check if we have any directives or parameters to work with
        has_directives = bool(applicable_directives)
        has_interaction_parameters = bool(applicable_parameters)
        has_persona_parameters = bool(persona_parameters)
        
        if not (has_directives or has_interaction_parameters or has_persona_parameters):
            error_msg = (
                "PersonaAction.respond: Cannot proceed - no directives or parameters found. "
                "At least one of the following must be present: "
                "unexecuted directives from other actions, unexecuted parameters from other actions, "
                "or PersonaAction's own parameters."
            )
            logger.warning(error_msg)
            raise ValueError(error_msg)

        # Compose the prompt
        system_prompt = self._compose_prompt(interaction)

        if use_history:
            # Get conversation history with configurable options
            # Note: with_utterance in history is controlled by with_response (if we include responses, we include utterances)
            conversation_history = await self._get_conversation_history(
                interaction,
                history_limit,
                with_utterance=with_response,  # Include utterances when including responses (they come in pairs)
                with_response=with_response,
                with_interpretation=with_interpretation,
                with_event=with_event,
                max_statement_length=max_statement_length,
            )
        else:
            conversation_history = None

        if use_utterance:
            utterance = interaction.utterance
        else:
            utterance = " "

        streaming = bool(
            visitor
            and getattr(visitor, "stream_mode", True)
            and getattr(visitor, "response_bus", None)
            and getattr(visitor, "session_id", None)
        )

        # Get ResponseBus from visitor
        response_bus = None
        if visitor:
            response_bus = getattr(visitor, "response_bus", None)

        # Make the language model call using generate() directly
        # This will automatically handle streaming or non-streaming via ResponseBus if provided
        try:
            logger.debug(f"PersonaAction.respond: conversation_history={conversation_history}")

            # Always pass model explicitly to ensure PersonaAction's override takes precedence
            # Log the model being used for debugging
            logger.debug(
                f"PersonaAction.respond: Using model='{self.model}' "
                f"(model_action.model='{getattr(model_action, 'model', 'N/A')}')"
            )

            response = await model_action.generate(
                prompt=utterance,
                stream=streaming,
                system=system_prompt,
                history=conversation_history,
                calling_action_label=self.get_class_name(),
                model=self.model,  # Explicitly pass PersonaAction's model to override LanguageModelAction's default
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_bus=response_bus,  # Direct ResponseBus publishing
                interaction=interaction,  # Interaction node (required if response_bus provided, contains session_id, channel, id)
            )

            return response

        except Exception as e:
            logger.error(f"Error in PersonaAction.respond: {e}", exc_info=True)
            raise

    def _compose_prompt(self, interaction: Interaction) -> str:
        """Compose the system prompt from the main prompt and interaction context.

        Groups directives by action_label and associates each directive with parameters
        from the same action. Extracts only essential fields to minimize token usage.

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

        # Get unexecuted directives and parameters from interaction
        applicable_directives = interaction.get_unexecuted_directives()
        applicable_parameters = interaction.get_unexecuted_parameters()

        # Separate PersonaAction's own parameters from interaction parameters
        # PersonaAction parameters are in self.parameters (not in interaction.parameters)
        persona_parameters = list(self.parameters)  # PersonaAction's own parameters
        interaction_parameters = [
            p for p in applicable_parameters
            if p.get("action_label") != "PersonaAction"
        ]  # Parameters from other actions

        # Build PersonaAction parameters section (if any)
        persona_params_section = ""
        if persona_parameters:
            persona_params_str = "\n".join(
                f"{i+1}. {self._format_parameter(p)}"
                for i, p in enumerate(persona_parameters)
            )
            persona_params_section = (
                f"{PARAMETER_DIRECTIVE}\n"
                f"{PARAMETER_CONDITION_EVALUATION_INSTRUCTION}\n"
                f"{persona_params_str}\n"
                f"{PARAMETERS_INSTRUCTION}\n"
                f"Note: These PersonaAction parameters apply to ALL directives (if any directives are provided)."
            )

        # Group directives by action_label
        directives_by_action: Dict[str, List[Dict[str, Any]]] = {}
        for directive in applicable_directives:
            action_label = directive.get("action_label", "Unknown")
            if action_label not in directives_by_action:
                directives_by_action[action_label] = []
            directives_by_action[action_label].append(directive)

        # Group interaction parameters by action_label
        parameters_by_action: Dict[str, List[Dict[str, Any]]] = {}
        for param in interaction_parameters:
            action_label = param.get("action_label", "Unknown")
            if action_label not in parameters_by_action:
                parameters_by_action[action_label] = []
            parameters_by_action[action_label].append(param)

        # Build directive groups section (if directives exist)
        directives_section = ""
        actions_with_directives = set(directives_by_action.keys())
        
        if applicable_directives:
            directive_groups = []
            for action_label, directives in directives_by_action.items():
                # Extract only content field from directives (essential field only)
                directive_contents = [
                    d.get("content", str(d)) for d in directives
                ]
                directive_text = "\n".join(
                    f"{i+1}. {content}" for i, content in enumerate(directive_contents)
                )

                # Get parameters from the same action
                action_params = parameters_by_action.get(action_label, [])
                action_params_section = ""
                if action_params:
                    # Extract only condition and response fields (essential fields only)
                    params_list = "\n".join(
                        f"{i+1}. {self._format_parameter(p)}"
                        for i, p in enumerate(action_params)
                    )
                    action_params_section = ACTION_PARAMETERS_SECTION_TEMPLATE.format(
                        action_label=action_label,
                        parameters_list=params_list,
                    )

                # Format directive group
                directive_group = DIRECTIVE_GROUP_TEMPLATE.format(
                    action_label=action_label,
                    directive_content=directive_text,
                    action_parameters_section=action_params_section,
                )
                directive_groups.append(directive_group)

            directives_section = (
                f"{DIRECTIVES_INSTRUCTION}\n"
                f"{''.join(directive_groups)}"
            )
        else:
            directives_section = NO_DIRECTIVES_INSTRUCTION

        # Build standalone parameters section (parameters from actions without directives)
        standalone_params_section = ""
        actions_with_only_params = {
            action_label: params
            for action_label, params in parameters_by_action.items()
            if action_label not in actions_with_directives
        }
        
        if actions_with_only_params:
            standalone_groups = []
            for action_label, params in actions_with_only_params.items():
                # Extract only condition and response fields (essential fields only)
                params_list = "\n".join(
                    f"{i+1}. {self._format_parameter(p)}"
                    for i, p in enumerate(params)
                )
                standalone_group = STANDALONE_PARAMETERS_SECTION_TEMPLATE.format(
                    action_label=action_label,
                    parameters_list=params_list,
                )
                standalone_groups.append(standalone_group)

            standalone_params_section = (
                f"{PARAMETER_DIRECTIVE}\n"
                f"{PARAMETER_CONDITION_EVALUATION_INSTRUCTION}\n"
                f"{''.join(standalone_groups)}\n"
                f"{PARAMETERS_INSTRUCTION}\n"
                f"Note: Also follow PersonaAction parameters above (if provided)."
            )

        # Build final parameters section
        # Combine PersonaAction parameters and standalone parameters
        if persona_params_section and standalone_params_section:
            parameters_section = f"{persona_params_section}\n\n{standalone_params_section}"
        elif persona_params_section:
            parameters_section = persona_params_section
        elif standalone_params_section:
            parameters_section = standalone_params_section
        else:
            parameters_section = NO_PARAMETERS_INSTRUCTION

        # Mark interaction parameters and directives as executed (but NOT PersonaAction's own parameters)
        if interaction_parameters:
            interaction.set_to_executed(parameters=interaction_parameters)
        if applicable_directives:
            interaction.set_to_executed(directives=applicable_directives)

        # Compose final prompt
        final_prompt = base_prompt
        if "{parameters}" in base_prompt:
            final_prompt = final_prompt.replace("{parameters}", parameters_section)
        else:
            if parameters_section != NO_PARAMETERS_INSTRUCTION:
                final_prompt += f"\n\n{parameters_section}"

        if "{directives}" in base_prompt:
            final_prompt = final_prompt.replace("{directives}", directives_section)
        else:
            final_prompt += f"\n\n{directives_section}"

        return final_prompt

    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = False,
        max_statement_length: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get formatted conversation history for the language model.

        Uses get_interaction_history to retrieve conversation history formatted
        as role/content pairs for language models.

        Args:
            interaction: Current interaction
            history_limit: Number of past interactions to include
            with_utterance: Include user utterances in history (default: True)
            with_response: Include AI responses in history (default: True)
            with_interpretation: Include interpretations in history (default: False)
            with_event: Include events in history (default: False)
            max_statement_length: Truncate utterances/responses to this length (default: None)

        Returns:
            List of message dictionaries with 'role' and 'content' keys, or None if disabled
        """
        if history_limit <= 0:
            return None

        from jvagent.memory.conversation import Conversation

        # Get conversation
        conversation = await Conversation.get(interaction.conversation_id)
        if not conversation:
            return None

        # Get conversation history formatted for language model with configurable options
        history = await conversation.get_interaction_history(
            limit=history_limit,
            excluded=interaction.id,
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            formatted=True,  # Format as role/content pairs for language models
            max_statement_length=max_statement_length,
        )

        return history if history else None

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
            action = await self.get_model_action()
            if not action:
                return False

            return True
        except Exception as e:
            logger.error(f"Healthcheck failed: {e}")
            return False
