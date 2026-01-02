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
    DIRECTIVES_SUB_PROMPT,
    NO_DIRECTIVES_SUB_PROMPT,
    PARAMETERS_SUB_PROMPT,
    SYSTEM_PROMPT_TEMPLATE,
    INTERPRETATION_INSIGHTS_PROMPT,
    REVISION_MECHANISM_PROMPT,
    CONTEXT_EVALUATION_PROMPT,
    PRIORITIZATION_INSTRUCTIONS_PROMPT,
    DIRECTIVE_PRE_CHECK_PROMPT,
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
            with_event: Include events in history (default: True)
            with_response: Include AI responses in history (default: True)
            max_statement_length: Truncate utterances/responses to this length (default: None, no truncation)

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
            interaction.add_parameters(persona_parameters_to_add, persona_action_name)
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

        # Compose the prompt (pass directives and all applicable parameters including persona parameters)
        system_prompt = await self._compose_prompt(interaction, applicable_directives, applicable_parameters)

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
            and getattr(visitor, "stream_mode", True)
            and getattr(visitor, "response_bus", None)
            and getattr(visitor, "session_id", None)
        )

        # Get ResponseBus from visitor
        response_bus = getattr(visitor, "response_bus", None) if visitor else None

        # # Determine prompt based on with_utterance flag and multi-call awareness
        # # If interaction has a response, the utterance is already in history (added by _get_conversation_history)
        # # So we pass empty prompt to avoid duplication at the end
        # if with_utterance and with_response and interaction.response:
        #     prompt = ""  # Don't duplicate utterance - it's already in history
        # else:
        #     prompt = interaction.utterance if with_utterance else ""
        prompt = interaction.utterance if with_utterance else ""

        # Make the language model call
        try:
            # Enable JSON response format if structured output is requested
            response_format = {"type": "json_object"} if self.use_structured_output else None
            
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
                response_format=response_format,
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

            # Set interaction.response immediately after getting the complete response
            if response:
                current_response = interaction.response or ""
                if current_response and current_response.strip() and current_response != response:
                    interaction.set_response(f"{current_response}\n\n{response}")
                else:
                    interaction.set_response(response)
                await interaction.save()

            # Record PersonaAction execution AFTER response is generated and saved
            # This ensures PersonaAction appears after the InteractAction that called it
            # in the actions list, preserving the call order
            interaction.record_action_execution("PersonaAction")

            return response

        except Exception as e:
            logger.error(f"Error in PersonaAction.respond: {e}", exc_info=True)
            raise

    async def _get_user_display_name(self, interaction: Interaction) -> str:
        """Resolve a friendly user name for prompt personalization."""
        try:
            user = await interaction.get_user()
            if user:
                return user.get_display_name()
        except Exception as e:
            logger.debug(f"PersonaAction: failed to resolve user display name: {e}")
        return "user"

    def _build_interpretation_insights_section(self, interpretation: Optional[str]) -> str:
        """Build the interpretation/insights section when interpretation is available.

        Args:
            interpretation: The interaction.interpretation field (set by InteractRouter)

        Returns:
            Formatted interpretation section, or empty string if no interpretation
        """
        if not interpretation or not interpretation.strip():
            return ""
        
        return INTERPRETATION_INSIGHTS_PROMPT.format(interpretation=interpretation)

    def _build_context_evaluation_section(self) -> str:
        """Build the context evaluation section.

        Returns:
            Formatted context evaluation section
        """
        return CONTEXT_EVALUATION_PROMPT

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

        Uses SYSTEM_PROMPT_TEMPLATE with conditional placeholders for all sections:
        - General Instructions (always included)
        - Agent Identity & Context (always included)
        - Task Description (always included)
        - Interpretation/Insights (conditionally included)
        - Revision Mechanism (always included)
        - Prioritization Instructions (always included)
        - Context Evaluation (always included)
        - Directives (conditionally included)
        - Parameters (conditionally included)
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

        # Detect continuation mode (multi-call scenario)
        is_continuation = bool(interaction.response)

        # Build continuation guidance if in multi-call mode
        continuation_guidance = ""
        if is_continuation:
            from jvagent.memory.conversation import Conversation
            
            # For continuation, use sensible defaults (truncate_statement will use agent's max_statement_length if available)
            # Previous response: keep last 2000 chars for context
            previous_response = await Conversation.truncate_statement(
                interaction.response or "",
                max_length=2000,  # Override: keep last 2000 chars for continuation context
                keep_last=True,
                interaction=interaction
            )
            
            # User utterance: use 500 chars default (truncate_statement will use agent's max_statement_length if available)
            user_utterance = await Conversation.truncate_statement(
                interaction.utterance or "",
                max_length=500,  # Override: 500 chars for utterance in continuation
                interaction=interaction
            )
            
            continuation_guidance = CONTINUATION_GUIDANCE_PROMPT.format(
                previous_response=previous_response or "(No previous response)",
                user_utterance=user_utterance or "(No user utterance)"
            )

        # Prepare agent capabilities
        capabilities_str = (
            "\n".join(f"- {cap}" for cap in self.persona_capabilities)
            if self.persona_capabilities
            else "None specified"
        )

        # Build interpretation/insights section (if interpretation available)
        interpretation_section = self._build_interpretation_insights_section(interaction.interpretation)

        # Build revision mechanism section (always included for quality)
        revision_mechanism = REVISION_MECHANISM_PROMPT

        # Build prioritization instructions section (always included)
        prioritization_instructions = PRIORITIZATION_INSTRUCTIONS_PROMPT

        # Build context evaluation section (always included)
        context_evaluation = self._build_context_evaluation_section()

        # Build directives section - consolidate all directives into a single numbered list
        directives_section = ""
        directive_pre_check = ""
        directive_count = len(applicable_directives)
        
        if applicable_directives:
            # Create a single numbered list of all directives (no action tagging)
            directive_list = "\n".join(
                f"{i+1}. {d.get('content', str(d))}" 
                for i, d in enumerate(applicable_directives)
            )
            directives_section = DIRECTIVES_SUB_PROMPT.format(
                directive_list=directive_list,
                directive_count=directive_count
            )
            
            # Build directive pre-check section (summary for verification)
            directive_summary = "\n".join(
                f"Directive #{i+1}: {d.get('content', str(d))[:100]}{'...' if len(d.get('content', str(d))) > 100 else ''}"
                for i, d in enumerate(applicable_directives)
            )
            directive_pre_check = DIRECTIVE_PRE_CHECK_PROMPT.format(
                directive_count=directive_count,
                directive_summary=directive_summary
            )
        else:
            directives_section = NO_DIRECTIVES_SUB_PROMPT

        # Build parameters section - consolidate all parameters into a single numbered list
        parameters_section = ""
        if applicable_parameters:
            # Create a single numbered list of all parameters (no action tagging)
            parameter_list = "\n".join(
                format_parameter(p, index=i+1) 
                for i, p in enumerate(applicable_parameters)
            )
            parameters_section = PARAMETERS_SUB_PROMPT.format(
                parameter_list=parameter_list
            )
        else:
            parameters_section = ""

        # Build channel formatting section
        channel_formatting_section = ""
        channel_directive = get_channel_directive(interaction.channel or "default")
        if channel_directive:
            channel_formatting_section = f"### CHANNEL FORMATTING\n{channel_directive}"

        # Format all conditional sections
        interpretation_section = format_conditional_section(interpretation_section, bool(interpretation_section))
        directives_section = format_conditional_section(directives_section, bool(directives_section))
        parameters_section = format_conditional_section(parameters_section, bool(parameters_section))
        channel_formatting_section = format_conditional_section(channel_formatting_section, bool(channel_formatting_section))
        continuation_guidance = format_conditional_section(continuation_guidance, bool(continuation_guidance))
        directive_pre_check = format_conditional_section(directive_pre_check, bool(directive_pre_check))

        # Build and return the final prompt using self.system_prompt (which defaults to SYSTEM_PROMPT_TEMPLATE
        # but can be overridden by agent-specific configurations)
        # Fall back to SYSTEM_PROMPT_TEMPLATE if self.system_prompt is empty (shouldn't happen with default)
        prompt_template = self.system_prompt if self.system_prompt else SYSTEM_PROMPT_TEMPLATE
        return prompt_template.format(
            agent_name=self.persona_name,
            agent_description=self.persona_description,
            agent_capabilities=capabilities_str,
            user=await self._get_user_display_name(interaction),
            date=date_str,
            time=time_str,
            interpretation_section=interpretation_section,
            revision_mechanism=revision_mechanism,
            prioritization_instructions=prioritization_instructions,
            context_evaluation=context_evaluation,
            directives_section=directives_section,
            parameters_section=parameters_section,
            channel_formatting_section=channel_formatting_section,
            continuation_guidance=continuation_guidance,
            directive_pre_check=directive_pre_check,
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
