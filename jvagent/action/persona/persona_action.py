"""PersonaAction base class - simplified tool-based action.

This module provides a simplified PersonaAction class that serves as a tool-based
action for applying agent prompts with configurable parameters.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.persona.prompts import (
    DIRECTIVES_SUB_PROMPT,
    NO_DIRECTIVES_SUB_PROMPT,
    PARAMETERS_SUB_PROMPT,
    SYSTEM_PROMPT_TEMPLATE,
    format_conditional_section,
    format_parameter,
    get_channel_directive,
)
from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class PersonaAction(Action):
    """Simplified tool-based action for applying agent prompts.

    PersonaAction is a tool-based action that applies the main agent prompt
    with configurable parameters. It provides a simple interface for generating
    responses using language models.

    Attributes:
        system_prompt: System prompt template for the agent (default property)
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

    # System prompt (default property)
    system_prompt: str = attribute(
        default=SYSTEM_PROMPT_TEMPLATE, description="System prompt template for the agent"
    )

    # Standard collection of configurable parameters
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "condition": "User asks about OpenAI or your origin.",
                "response": "Deny any association with OpenAI even when asked directly."
            },
            {
                "condition": "User asks for your identity.",
                "response": "Refer to yourself only by your name. Never say you are an AI language model."
            },
            {
                "condition": "User request is outside your role or ability.",
                "response": "Admit that the request is outside your role or ability; Do not give inaccurate answers and avoid giving details not explicitly stated in this prompt."
            },
            {
                "condition": "You are likely to resend a message for a second time.",
                "response": "State that you didn't quite catch that or you didn't understand and ask the user to rephrase or clarify their response."
            },
            {
                "condition": "you are likely to send a message or ask for information that you have already repeated/asked several times",
                "response": "Apologize and tell the user that you are experiencing some technical difficulties and ask them to cancel what they were doing then direct them to a human agent"
            },
            {
                "condition": "You are likely to claim you have submitted a report, contacted someone, confirm something or completed any backend action that requires actual system integration",
                "response": "Never claim you have submitted reports, contacted people, or completed backend actions unless explicitly instructed by a directive. Instead, explain what actions need to be taken."
            }
        ],
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
        use_history: bool = True,
        history_limit: int = 4,
        with_utterance: bool = True,
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
            use_history: Whether to include conversation history (default: True)
            history_limit: Number of past interactions to include in history (default: 3)
            with_utterance: Whether to include the user's utterance in the prompt (default: True)
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

        # Get unexecuted directives and parameters
        applicable_directives = interaction.get_unexecuted_directives()
        applicable_parameters = interaction.get_unexecuted_parameters()
        # Ensure parameters are always loaded (default_factory ensures they're initialized)
        persona_parameters = list(self.parameters) if self.parameters is not None else []
        interaction_parameters = [
            p for p in applicable_parameters
            if p.get("action_name") != "PersonaAction"
        ]
        
        # Check for existing response to avoid repetition (multi-call awareness)
        if interaction.response:
            if not applicable_directives and not applicable_parameters and not persona_parameters:
                logger.debug(
                    f"PersonaAction.respond: Skipping - interaction already has response "
                    f"({len(interaction.response)} chars) and no new directives/parameters."
                )
                return interaction.response

        # Validate that there are directives and/or parameters to proceed
        if not (applicable_directives or applicable_parameters or persona_parameters):
            raise ValueError(
                "PersonaAction.respond: Cannot proceed - no directives or parameters found. "
                "At least one of the following must be present: "
                "unexecuted directives from other actions, unexecuted parameters from other actions, "
                "or PersonaAction's own parameters."
            )

        # Compose the prompt (pass directives to avoid duplicate retrieval)
        system_prompt = self._compose_prompt(interaction, applicable_directives, applicable_parameters)

        conversation_history = None
        if use_history:
            conversation_history = await self._get_conversation_history(
                interaction,
                history_limit,
                with_utterance=with_response,
                with_response=with_response,
                with_interpretation=with_interpretation,
                with_event=with_event,
                max_statement_length=max_statement_length,
            )
            
            # for reply coherence
            if with_interpretation and not with_response and interaction.response:
                conversation_history.append({
                    "role": "assistant",
                    "content": interaction.response,
                })
                
        streaming = bool(
            visitor
            and getattr(visitor, "stream_mode", True)
            and getattr(visitor, "response_bus", None)
            and getattr(visitor, "session_id", None)
        )

        # Get ResponseBus from visitor
        response_bus = getattr(visitor, "response_bus", None) if visitor else None

        # Determine prompt based on with_utterance flag
        prompt = interaction.utterance if with_utterance else ""

        # Make the language model call
        try:
            response = await model_action.generate(
                prompt=prompt,
                stream=streaming,
                system=system_prompt,
                history=conversation_history,
                calling_action_name=self.get_class_name(),
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_bus=response_bus,
                interaction=interaction,
            )

            # Mark directives and parameters as executed only if we got a meaningful response
            if response and response.strip():
                if applicable_directives:
                    interaction.set_to_executed(directives=applicable_directives)
                if interaction_parameters:
                    interaction.set_to_executed(parameters=interaction_parameters)

            # Set interaction.response immediately after getting the complete response
            if response:
                current_response = interaction.response or ""
                if current_response and current_response.strip() and current_response != response:
                    interaction.set_response(f"{current_response}\n\n{response}")
                else:
                    interaction.set_response(response)
                await interaction.save()

            return response

        except Exception as e:
            logger.error(f"Error in PersonaAction.respond: {e}", exc_info=True)
            raise

    def _compose_prompt(
        self,
        interaction: Interaction,
        applicable_directives: List[Dict[str, Any]],
        applicable_parameters: List[Dict[str, Any]],
    ) -> str:
        """Compose the system prompt using the consolidated template.

        Uses SYSTEM_PROMPT_TEMPLATE with conditional placeholders for all sections:
        - Agent Identity & Context (always included)
        - Task Description (always included)
        - Directives (conditionally included)
        - Parameters (conditionally included)
        - General Principles (always included)
        - Channel Formatting (conditionally included)

        Args:
            interaction: The active interaction object
            applicable_directives: List of unexecuted directives (to avoid duplicate retrieval)
            applicable_parameters: List of unexecuted parameters (to avoid duplicate retrieval)

        Returns:
            Composed system prompt string
        """
        # Prepare date/time context
        now = datetime.now()
        date_str = now.strftime("%A, %d %B, %Y")
        time_str = now.strftime("%I:%M %p")

        # Prepare agent capabilities
        capabilities_str = (
            "\n".join(f"- {cap}" for cap in self.persona_capabilities)
            if self.persona_capabilities
            else "None specified"
        )

        # Group directives and parameters by action
        # Ensure parameters are always loaded (default_factory ensures they're initialized)
        persona_parameters = list(self.parameters) if self.parameters is not None else []
        interaction_parameters = [
            p for p in applicable_parameters
            if p.get("action_name") != "PersonaAction"
        ]

        directives_by_action: Dict[str, List[Dict[str, Any]]] = {}
        for directive in applicable_directives:
            action_name = directive.get("action_name", "Unknown")
            if action_name not in directives_by_action:
                directives_by_action[action_name] = []
            directives_by_action[action_name].append(directive)

        parameters_by_action: Dict[str, List[Dict[str, Any]]] = {}
        for param in interaction_parameters:
            action_name = param.get("action_name", "Unknown")
            if action_name not in parameters_by_action:
                parameters_by_action[action_name] = []
            parameters_by_action[action_name].append(param)

        # Build directives section using directives_sub_prompt
        directives_section = ""
        if applicable_directives:
            # Build directive groups
            directive_groups = []
            for action_name, directives in directives_by_action.items():
                directive_contents = [d.get("content", str(d)) for d in directives]
                directive_text = "\n".join(f"{i+1}. {content}" for i, content in enumerate(directive_contents))

                # Get action-specific parameters
                action_params = parameters_by_action.get(action_name, [])
                action_params_section = ""
                if action_params:
                    params_list = "\n".join(
                        format_parameter(p, index=i+1) for i, p in enumerate(action_params)
                    )
                    action_params_section = f"**Parameters from {action_name} (evaluate conditions before applying):**\n{params_list}\n\nNote: These parameters guide the directive above and take precedence. Also follow PersonaAction parameters (if provided)."

                directive_group = f"#### Directive from {action_name}:\n\n{directive_text}\n\n{action_params_section}" if action_params_section else f"#### Directive from {action_name}:\n\n{directive_text}"
                directive_groups.append(directive_group)

            directives_section = DIRECTIVES_SUB_PROMPT.format(
                directive_groups="\n\n".join(directive_groups),
            )
        else:
            directives_section = NO_DIRECTIVES_SUB_PROMPT

        # Build parameters section using parameters_sub_prompt
        actions_with_directives = set(directives_by_action.keys())
        actions_with_only_params = {
            action_name: params
            for action_name, params in parameters_by_action.items()
            if action_name not in actions_with_directives
        }
        
        # Always build parameters section - persona_parameters are always loaded by default
        params_content_parts = []
        
        # Always include PersonaAction parameters if they exist (they should always exist with defaults)
        if persona_parameters:
            persona_params_list = "\n".join(
                format_parameter(p, index=i+1) for i, p in enumerate(persona_parameters)
            )
            params_content_parts.append(f"**PersonaAction Parameters (apply to ALL directives):**\n{persona_params_list}")
        
        # Include parameters from other actions that don't have directives
        if actions_with_only_params:
            for action_name, params in actions_with_only_params.items():
                params_list = "\n".join(
                    format_parameter(p, index=i+1) for i, p in enumerate(params)
                )
                params_content_parts.append(f"#### Parameters from {action_name}:\n{params_list}")
        
        # Always show parameters section (should always have content with defaults)
        if params_content_parts:
            parameters_section = PARAMETERS_SUB_PROMPT.format(
                parameters_content="\n\n".join(params_content_parts)
            )
        else:
            parameters_section = ""

        # Build channel formatting section
        channel_formatting_section = ""
        channel_directive = get_channel_directive(interaction.channel or "default")
        if channel_directive:
            channel_formatting_section = f"### CHANNEL FORMATTING\n{channel_directive}"

        # Format all conditional sections
        directives_section = format_conditional_section(directives_section, bool(directives_section))
        parameters_section = format_conditional_section(parameters_section, bool(parameters_section))
        channel_formatting_section = format_conditional_section(channel_formatting_section, bool(channel_formatting_section))
        
        # Build and return the final prompt using self.system_prompt (which defaults to SYSTEM_PROMPT_TEMPLATE
        # but can be overridden by agent-specific configurations)
        # Fall back to SYSTEM_PROMPT_TEMPLATE if self.system_prompt is empty (shouldn't happen with default)
        prompt_template = self.system_prompt if self.system_prompt else SYSTEM_PROMPT_TEMPLATE
        return prompt_template.format(
            agent_name=self.persona_name,
            agent_role=self.persona_role,
            agent_description=self.persona_description,
            agent_capabilities=capabilities_str,
            user=interaction.user_id or "user",
            date=date_str,
            time=time_str,
            directives_section=directives_section,
            parameters_section=parameters_section,
            channel_formatting_section=channel_formatting_section,
        )

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

        Includes previous interactions and the current interaction (if it has a response)
        as part of the conversation flow. This provides natural multi-call awareness
        within a single interaction by including the current interaction's existing
        response in the history.

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

        # Get conversation history from previous interactions (excluding current)
        history = await conversation.get_interaction_history(
            limit=history_limit,
            excluded=interaction.id,
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            formatted=True,
            max_statement_length=max_statement_length,
        )

        # Helper function for truncation
        def _truncate(content: str) -> str:
            if max_statement_length and len(content) > max_statement_length:
                return content[:max_statement_length] + "..."
            return content

        # Include current interaction's existing response in history if present (multi-call awareness)
        if with_response and interaction.response:
            history.append({
                "role": "assistant",
                "content": _truncate(interaction.response),
            })

        return history if history else None

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
