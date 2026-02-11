"""PersonaAction base class - simplified tool-based action.

This module provides a simplified PersonaAction class that serves as a tool-based
action for applying agent prompts with configurable parameters.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.persona.prompts import (
    CONTINUATION_GUIDANCE_PROMPT,
    DIRECTIVES_SECTION_PROMPT,
    DIRECTIVE_COMPLIANCE_CHECK_PROMPT,
    NO_DIRECTIVES_SUB_PROMPT,
    PARAMETERS_SUB_PROMPT,
    SYSTEM_PROMPT_TEMPLATE,
    INTERPRETATION_INSIGHTS_PROMPT,
    RESPONSE_PROTOCOL_PROMPT,
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
        persona_description: Detailed agent description (for prompt formatting)
        persona_capabilities: List of agent capabilities (for prompt formatting)
    """

    # Persona attributes (for prompt formatting if using default template)
    persona_name: str = attribute(
        default="Agent", description="Agent display name"
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

    # Structured output configuration (optional, default False for backward compatibility)
    use_structured_output: bool = attribute(
        default=False,
        description="Enable structured JSON output with insights, context evaluation, and revisions (default: False for backward compatibility)"
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
                "condition": "User requests information that you have already provided.",
                "response": "Make the user aware that you have already provided the information."
            },
            {
                "condition": "The conversation seems repetitive.",
                "response": "Bring the circular conversation to the user's attention."
            },
            {
                "condition": "The user has diverged from the ongoing activity highlghted in conversation history",
                "response": "Respond but in closing, remind the user to return to complete the ongoing activity"
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
        with_event: bool = True,
        with_response: bool = True,
        max_statement_length: Optional[int] = None,
        transient: bool = False,
    ) -> str:
        """Main utility method for generating a response.

        Accepts the active interaction object with the utterance, injected directives
        and parameters, then makes the language model call with the composed prompt
        and returns the generated response.

        When visitor is provided and has response_bus and session_id, the generated
        response is delivered via the response bus (streaming: by the model; non-streaming:
        by a single publish) and interaction.response is updated accordingly.
        When no such visitor is provided, only interaction.response is set and saved.

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
            with_event: Include events in history (default: True)
            with_response: Include AI responses in history (default: True)
            max_statement_length: Truncate utterances/responses to this length (default: None, no truncation)
            transient: If True, skip appending response to interaction.response. Use for temporary
                messages like canned responses or typing indicators (default: False)

        Returns:
            Generated response string from the language model

        Raises:
            RuntimeError: If PersonaAction is not attached to an agent or model action not found
        """
        # Get model action (required=True raises error if not found)
        model_action = await self.get_model_action(required=True)

        # Add all persona parameters to the interaction (like interact actions do)
        # This allows accurate recording of persona-level parameters applied to each response
        # Duplicate prevention is handled by interaction.add_parameters()
        persona_action_name = self.get_class_name()
        persona_parameters_to_add = list(self.parameters) if self.parameters is not None else []
        
        if persona_parameters_to_add:
            # Only save if parameters were actually added (not duplicates)
            if interaction.add_parameters(persona_parameters_to_add, persona_action_name):
                await interaction.save()

        # Get unexecuted directives and parameters (now includes persona parameters)
        applicable_directives = interaction.get_unexecuted_directives()
        applicable_parameters = interaction.get_unexecuted_parameters()

        # Check for existing response to avoid repetition (multi-call awareness)
        if interaction.response:
            if not applicable_directives and not applicable_parameters:
                logger.debug(
                    f"PersonaAction.respond: Skipping - interaction already has response "
                    f"({len(interaction.response)} chars) and no new directives/parameters."
                )
                return interaction.response

        # Validate that there are directives and/or parameters to proceed
        # applicable_parameters now includes persona parameters from the interaction
        if not (applicable_directives or applicable_parameters):
            raise ValueError(
                "PersonaAction.respond: Cannot proceed - no directives or parameters found. "
                "At least one of the following must be present: "
                "unexecuted directives from other actions, unexecuted parameters from other actions, "
                "or PersonaAction's own parameters."
            )

        # Generate response using direct prompt approach
        return await self._generate_response(
            interaction, visitor, applicable_directives, applicable_parameters,
            use_history, history_limit, with_utterance, with_interpretation,
            with_event, with_response, max_statement_length, transient
        )

    async def _get_user_display_name(self, interaction: Interaction) -> str:
        """Resolve a friendly user name for prompt personalization."""
        try:
            user = await interaction.get_user()
            if user:
                return user.get_display_name()
        except Exception as e:
            logger.debug(f"PersonaAction: failed to resolve user display name: {e}")
        return "user"

    async def _pipe_response(
        self,
        response: str,
        interaction: Interaction,
        visitor: Optional[Any],
        streaming: bool,
        transient: bool = False,
    ) -> None:
        """Persist and optionally publish response to the response bus.

        When transient=True, the response is published but NOT appended to
        interaction.response. This is useful for temporary messages like
        canned responses or typing indicators.

        When there is no bus: persist to interaction.response (append if continuing)
        and save. When there is a bus and streaming: no-op (model already published).
        When there is a bus and non-streaming: PersonaAction is the sole publisher;
        call publish once.
        
        Args:
            response: The response content
            interaction: The interaction object
            visitor: Optional InteractWalker
            streaming: Whether streaming mode is active
            transient: If True, skip appending to interaction.response
        """
        if not response:
            return
        response_bus = getattr(visitor, "response_bus", None) if visitor else None
        has_bus = bool(response_bus and visitor and getattr(visitor, "session_id", None))

        if not has_bus:
            if not transient:
                current_response = interaction.response or ""
                response_changed = False
                # Continuing (append with separator) vs replace/set
                if current_response and current_response.strip() and current_response != response:
                    response_changed = interaction.set_response(f"{current_response}\n\n{response}")
                else:
                    response_changed = interaction.set_response(response)
                if response_changed:
                    await interaction.save()
            return

        if streaming:
            return

        channel = getattr(visitor, "channel", "default")
        if not channel or channel == "default":
            logger.warning(
                f"{self.get_class_name()}: Visitor channel not set or is 'default', "
                f"using channel='{channel}'. This may prevent channel adapters from receiving messages."
            )
        await response_bus.publish(
            session_id=visitor.session_id,
            content=response,
            channel=channel,
            stream=False,
            interaction_id=interaction.id,
            interaction=interaction,
            user_id=interaction.user_id if hasattr(interaction, "user_id") else None,
            streaming_complete=True,
            transient=transient,
        )

    def _parse_structured_output(self, response: str) -> Optional[str]:
        """Parse structured JSON output to extract final message content.

        When use_structured_output is enabled, the LLM returns a JSON response with:
        - insights: List of gathered insights
        - context_evaluation: Structured context assessment
        - revisions: List of revision attempts with the final message content

        This method extracts the final message content from the last revision.

        Args:
            response: The JSON string response from the LLM

        Returns:
            Final message content string, or None if parsing fails
        """
        try:
            # Try to parse JSON
            if isinstance(response, str):
                # Clean up potential markdown code blocks
                response_clean = response.strip()
                if response_clean.startswith("```json"):
                    response_clean = response_clean[7:]
                if response_clean.startswith("```"):
                    response_clean = response_clean[3:]
                if response_clean.endswith("```"):
                    response_clean = response_clean[:-3]
                response_clean = response_clean.strip()
                
                data = json.loads(response_clean)
            else:
                data = response

            # Extract final message from revisions
            if "revisions" in data and data["revisions"]:
                # Get the last revision's content
                last_revision = data["revisions"][-1]
                if "content" in last_revision:
                    final_content = last_revision["content"]
                    
                    # Log insights and context evaluation if available (for debugging)
                    if "insights" in data:
                        logger.debug(f"PersonaAction insights: {data['insights']}")
                    if "context_evaluation" in data:
                        logger.debug(f"PersonaAction context evaluation: {data['context_evaluation']}")
                    
                    return final_content

            # Fallback: if no revisions, return the whole response
            logger.warning("PersonaAction: Structured output enabled but no revisions found in response")
            return response if isinstance(response, str) else json.dumps(response)

        except json.JSONDecodeError as e:
            logger.warning(f"PersonaAction: Failed to parse structured JSON output: {e}. Returning raw response.")
            return response if isinstance(response, str) else str(response)
        except Exception as e:
            logger.error(f"PersonaAction: Error parsing structured output: {e}", exc_info=True)
            return response if isinstance(response, str) else str(response)

    async def _compose_prompt(
        self,
        interaction: Interaction,
        applicable_directives: List[Dict[str, Any]],
        applicable_parameters: List[Dict[str, Any]],
    ) -> str:
        """Compose the system prompt using the consolidated template.

        Args:
            interaction: The active interaction object
            applicable_directives: List of unexecuted directives
            applicable_parameters: List of unexecuted parameters

        Returns:
            Composed system prompt string
        """
        now = datetime.now()
        date_str = now.strftime("%A, %d %B, %Y")
        time_str = now.strftime("%I:%M %p")

        # Build continuation guidance if in multi-call mode
        continuation_guidance = ""
        if interaction.response:
            from jvagent.memory.conversation import Conversation
            
            previous_response = await Conversation.truncate_statement(
                interaction.response or "",
                max_length=2000,
                keep_last=True,
                interaction=interaction
            )
            user_utterance = await Conversation.truncate_statement(
                interaction.utterance or "",
                max_length=500,
                interaction=interaction
            )
            continuation_guidance = CONTINUATION_GUIDANCE_PROMPT.format(
                previous_response=previous_response or "(No previous response)",
                user_utterance=user_utterance or "(No user utterance)"
            )

        capabilities_str = (
            "\n".join(f"- {cap}" for cap in self.persona_capabilities)
            if self.persona_capabilities
            else "None specified"
        )

        interpretation_section = (
            INTERPRETATION_INSIGHTS_PROMPT.format(interpretation=interaction.interpretation)
            if interaction.interpretation and interaction.interpretation.strip()
            else ""
        )

        # Build directives section
        if applicable_directives:
            directive_list = "\n".join(
                f"{i+1}. {d.get('content', str(d))}" 
                for i, d in enumerate(applicable_directives)
            )
            directives_section = DIRECTIVES_SECTION_PROMPT.format(
                directive_list=directive_list,
                directive_count=len(applicable_directives)
            )
        else:
            directives_section = NO_DIRECTIVES_SUB_PROMPT

        # Build parameters section
        if applicable_parameters:
            parameter_list = "\n".join(
                format_parameter(p, index=i+1) 
                for i, p in enumerate(applicable_parameters)
            )
            parameters_section = PARAMETERS_SUB_PROMPT.format(parameter_list=parameter_list)
        else:
            parameters_section = ""

        # Build channel formatting section
        channel_directive = get_channel_directive(interaction.channel or "default")
        channel_formatting_section = (
            f"### CHANNEL FORMATTING\n{channel_directive}" if channel_directive else ""
        )

        # Format conditional sections
        interpretation_section = format_conditional_section(interpretation_section, bool(interpretation_section))
        directives_section = format_conditional_section(directives_section, bool(directives_section))
        parameters_section = format_conditional_section(parameters_section, bool(parameters_section))
        channel_formatting_section = format_conditional_section(channel_formatting_section, bool(channel_formatting_section))
        continuation_guidance = format_conditional_section(continuation_guidance, bool(continuation_guidance))

        prompt_template = self.system_prompt if self.system_prompt else SYSTEM_PROMPT_TEMPLATE
        composed = prompt_template.format(
            agent_name=self.persona_name,
            agent_description=self.persona_description,
            agent_capabilities=capabilities_str,
            user=await self._get_user_display_name(interaction),
            date=date_str,
            time=time_str,
            interpretation_section=interpretation_section,
            response_protocol=RESPONSE_PROTOCOL_PROMPT,
            directives_section=directives_section,
            parameters_section=parameters_section,
            channel_formatting_section=channel_formatting_section,
            continuation_guidance=continuation_guidance,
        )
        
        # Append compliance check for directive recency reinforcement
        if applicable_directives:
            checklist = "\n".join(
                f"[ ] Directive {i+1}: {d.get('content', str(d))}"
                for i, d in enumerate(applicable_directives)
            )
            composed += "\n\n" + DIRECTIVE_COMPLIANCE_CHECK_PROMPT.format(
                directive_checklist=checklist
            )
        
        return composed

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
            return []

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

        # Include current interaction's utterance and response in history if present (multi-call awareness)
        # Important: Add utterance BEFORE response to maintain chronological order
        if with_utterance and with_response and interaction.response:
            # Add current utterance first
            history.append({
                "role": "user",
                "content": _truncate(interaction.utterance),
            })
            # Then add current response
            history.append({
                "role": "assistant",
                "content": _truncate(interaction.response),
            })
        elif with_response and interaction.response:
            # Only add response if utterance not requested
            history.append({
                "role": "assistant",
                "content": _truncate(interaction.response),
            })

        return history if history else []

    async def _generate_response(
        self,
        interaction: Interaction,
        visitor: Optional[Any],
        applicable_directives: List[Dict[str, Any]],
        applicable_parameters: List[Dict[str, Any]],
        use_history: bool,
        history_limit: int,
        with_utterance: bool,
        with_interpretation: bool,
        with_event: bool,
        with_response: bool,
        max_statement_length: Optional[int],
        transient: bool,
    ) -> str:
        """Generate response using direct prompt approach.
        
        Composes the system prompt using _compose_prompt() and makes the language
        model call via model_action.generate() with system prompt and conversation history.
        
        Args:
            interaction: The active interaction object
            visitor: Optional InteractWalker for streaming support
            applicable_directives: List of unexecuted directives
            applicable_parameters: List of unexecuted parameters
            use_history: Whether to include conversation history
            history_limit: Number of past interactions to include
            with_utterance: Whether to include user utterances
            with_interpretation: Include interpretations in history
            with_event: Include events in history
            with_response: Include AI responses in history
            max_statement_length: Truncate to this length
            transient: If True, skip appending response to interaction.response
            
        Returns:
            Generated response string
        """
        # Get model action (required=True raises error if not found)
        model_action = await self.get_model_action(required=True)

        conversation_history = None
        if use_history:
            conversation_history = await self._get_conversation_history(
                interaction,
                history_limit,
                with_utterance=with_utterance,
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
            and getattr(visitor, "stream", False)
            and getattr(visitor, "response_bus", None)
            and getattr(visitor, "session_id", None)
        )

        # Get ResponseBus from visitor
        response_bus = getattr(visitor, "response_bus", None) if visitor else None

        prompt = interaction.utterance if with_utterance else ""
        
        # Inject directive reminder into user prompt for peak-attention reinforcement
        if applicable_directives and prompt:
            directive_hints = "; ".join(
                d.get('content', str(d)) for d in applicable_directives
            )
            prompt = f"{prompt}\n\n[SYSTEM: You MUST execute in your response: {directive_hints}]"

        # Make the language model call
        try:
            # Enable JSON response format if structured output is requested
            response_format = {"type": "json_object"} if self.use_structured_output else None
            
            response = await model_action.generate(
                prompt=prompt,
                stream=streaming,
                system=await self._compose_prompt(interaction, applicable_directives, applicable_parameters),
                history=conversation_history,
                calling_action_name=self.get_class_name(),
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_bus=response_bus if streaming else None,
                interaction=interaction if streaming else None,
                response_format=response_format,
                transient=transient,
            )

            # Parse structured output if enabled
            if self.use_structured_output and response:
                parsed_response = self._parse_structured_output(response)
                if parsed_response:
                    response = parsed_response
                # If parsing fails, response remains as-is (fallback)

            # Mark directives and parameters as executed only if we got a meaningful response
            if response and response.strip():
                if applicable_directives:
                    interaction.set_to_executed(directives=applicable_directives)
                if applicable_parameters:
                    interaction.set_to_executed(parameters=applicable_parameters)

            await self._pipe_response(response, interaction, visitor, streaming, transient)

            # Record PersonaAction execution AFTER response is generated and saved
            interaction.record_action_execution("PersonaAction")

            return response

        except Exception as e:
            logger.error(f"Error in PersonaAction._generate_response: {e}", exc_info=True)
            raise

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
            logger.error(f"Healthcheck failed: {e}", exc_info=True)
            return False
