"""PersonaAction base class and result container.

This module provides the core PersonaAction class for agent behavioral modeling
with LLM-driven parameters, action delegation, and event bus integration.
"""

import json
import logging
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import uuid

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.persona.events import (
    InteractionEventBus,
    ResponseAggregator,
)
from jvagent.action.persona.parameter import (
    DEFAULT_BASE_PARAMETERS,
    ParameterManager,
    PersonaParameter,
)
from jvagent.action.persona.prompts import (
    AGENT_PROMPT_TEMPLATE,
    CANNED_RESPONSE_PROMPT,
    DIRECTIVES_INSTRUCTION,
    FILTER_PARAMETER_PROMPT,
    NO_DIRECTIVES_INSTRUCTION,
    NO_PARAMETERS_INSTRUCTION,
    PARAMETER_DIRECTIVE,
    PARAMETERS_INSTRUCTION,
    get_channel_directive,
)
from jvagent.memory import Conversation, Interaction, Memory, User

logger = logging.getLogger(__name__)


class PersonaActionResult:
    """Result container for PersonaAction.interact().

    Provides access to the interaction result including response content,
    event bus for all emitted events, and user/session identifiers.

    Attributes:
        user_id: User identifier (always present)
        session_id: Session identifier (always present)
        response: Complete response text (non-streaming)
        stream: Async generator for streaming response
        canned_response: Immediate response (if any)
        interaction: The Interaction node
        event_bus: Event bus with all emitted events
    """

    def __init__(
        self,
        user_id: str,
        session_id: str,
        interaction: Interaction,
        event_bus: InteractionEventBus,
        response: Optional[str] = None,
        stream: Optional[AsyncGenerator[str, None]] = None,
        canned_response: Optional[str] = None,
    ):
        """Initialize the result.

        Args:
            user_id: User identifier
            session_id: Session identifier
            interaction: The Interaction node
            event_bus: Event bus with all emitted events
            response: Complete response text
            stream: Async generator for streaming
            canned_response: Immediate response
        """
        self.user_id = user_id
        self.session_id = session_id
        self.interaction = interaction
        self.event_bus = event_bus
        self.response = response
        self.stream = stream
        self.canned_response = canned_response
        self.is_streaming = stream is not None

    async def get_response(self) -> str:
        """Get the complete response.

        If streaming, consumes the stream and returns the full response.

        Returns:
            Complete response string
        """
        if self.response:
            return self.response
        if self.stream:
            chunks = []
            async for chunk in self.stream:
                chunks.append(chunk)
            self.response = "".join(chunks)
            return self.response
        return ""

    async def iter_stream(self) -> AsyncGenerator[str, None]:
        """Iterate over streaming response chunks.

        Yields:
            Response chunks as they arrive
        """
        if self.stream:
            async for chunk in self.stream:
                yield chunk
        elif self.response:
            yield self.response

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary.

        Returns:
            Dictionary representation with:
            - user_id: User identifier
            - session_id: Session identifier
            - response: Final response text
            - canned_response: Immediate response (if any)
            - interaction: Interaction details including:
              - actions: Actions involved in processing (in order)
              - directives: Directives from non-persona actions
              - parameters: Applicable parameters
              - model_log: All model call results
              - metrics: Processing metrics
        """
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "response": self.response,
            "canned_response": self.canned_response,
            "interaction": {
                "id": self.interaction.id,
                "utterance": self.interaction.utterance,
                "response": self.interaction.response,
                "actions": self.interaction.actions,
                "directives": self.interaction.directives,
                "parameters": self.interaction.parameters,
                "model_log": self.interaction.model_log,
            },
            "events": [e.to_dict() for e in self.event_bus.get_events()],
        }


class PersonaAction(Action):
    """Core interact action with LLM-driven behavioral parameters.

    PersonaAction provides agent behavioral modeling inspired by the Parlant
    PersonaInteractAction. It features:
    - Configurable persona (name, role, description, capabilities)
    - LLM-driven parameter filtering
    - Action delegation via standard execute() method
    - Event bus for async responses (canned responses, streaming)

    Attributes:
        persona_name: Agent display name
        persona_role: Agent role description
        persona_description: Detailed agent description
        persona_capabilities: List of agent capabilities
        model_action_type: Entity type of the ModelAction (e.g., "OpenAIModelAction")
        model_name: Default model name
        light_model_name: Model for parameter filtering (faster/cheaper)
        model_temperature: Temperature for LLM generation
        model_max_tokens: Max tokens for LLM generation
        canned_responses_enabled: Whether to use canned responses
        streaming: Whether to stream responses by default
        history_enabled: Whether to include conversation history
        history_size: Number of interactions to include in history
        tts_enabled: Whether text-to-speech is enabled
        timezone: Timezone for date/time in prompts
        base_parameters: Base behavioral parameters
    """

    # Agent Identity
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
        default="OpenAIModelAction",
        description="Entity type of the ModelAction to use (e.g., OpenAIModelAction)",
    )
    model_name: str = attribute(
        default="gpt-4o", description="Default model name"
    )
    light_model_name: str = attribute(
        default="gpt-4o-mini", description="Model for parameter filtering"
    )
    model_temperature: float = attribute(
        default=0.3, description="Temperature for LLM generation"
    )
    model_max_tokens: int = attribute(
        default=4096, description="Max tokens for LLM generation"
    )

    # Behavior
    canned_responses_enabled: bool = attribute(
        default=False, description="Whether to use canned responses"
    )
    streaming: bool = attribute(
        default=False, description="Whether to stream responses by default"
    )
    history_enabled: bool = attribute(
        default=True, description="Whether to include conversation history"
    )
    history_size: int = attribute(
        default=5, description="Number of interactions in history"
    )
    max_statement_length: int = attribute(
        default=500, description="Max length for statements in history"
    )
    tts_enabled: bool = attribute(
        default=False, description="Whether TTS is enabled"
    )
    timezone: str = attribute(
        default="UTC", description="Timezone for date/time"
    )

    # Parameters
    base_parameters: List[Dict[str, Any]] = attribute(
        default_factory=list, description="Base behavioral parameters"
    )

    # Custom prompt template (optional override)
    agent_prompt_template: str = attribute(
        default="", description="Custom agent prompt template"
    )

    # Internal state (transient)
    _parameter_manager: Optional[ParameterManager] = attribute(
        private=True, default=None
    )
    _parameters_initialized: bool = attribute(
        private=True, default=False
    )

    async def on_register(self) -> None:
        """Initialize parameters when action is registered."""
        await super().on_register()

        await self._ensure_parameter_manager()
        logger.info(f"PersonaAction '{self.label}' registered with parameters")

    async def _ensure_parameter_manager(self) -> ParameterManager:
        """Ensure the parameter manager is initialized with base parameters."""
        if not self._parameter_manager:
            self._parameter_manager = ParameterManager(self.id)

        if not self._parameters_initialized:
            await self._initialize_base_parameters()

        return self._parameter_manager

    async def _initialize_base_parameters(self) -> None:
        """Load default and configured base parameters once."""
        if self._parameters_initialized:
            return

        manager = self._parameter_manager or ParameterManager(self.id)
        base_params: List[Dict[str, Any]] = list(DEFAULT_BASE_PARAMETERS)
        base_params.extend(self.base_parameters)

        if base_params:
            await manager.import_parameters(base_params)

        self._parameter_manager = manager
        self._parameters_initialized = True

    async def interact(
        self,
        utterance: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        channel: str = "default",
        stream: bool = False,
    ) -> PersonaActionResult:
        """Main interaction method.

        Handles the full interaction flow:
        1. Resolve User/Conversation based on user_id/session_id
        2. Create Interaction and EventBus
        3. Process canned response (if enabled)
        4. Filter and apply parameters
        5. Execute parameter actions
        6. Build prompt and call ModelAction
        7. Return result with all events

        Args:
            utterance: User's input text
            user_id: Optional user identifier
            session_id: Optional session identifier
            channel: Communication channel
            stream: Whether to stream the response

        Returns:
            PersonaActionResult with response and event bus
        """
        # Get agent and memory
        agent = await self._get_agent()
        if not agent:
            raise RuntimeError("PersonaAction not attached to an agent")

        memory = await self._get_memory(agent)
        if not memory:
            raise RuntimeError("Agent has no Memory node")

        # Step 1: Resolve User and Conversation
        user, conversation, resolved_user_id, resolved_session_id = (
            await self._resolve_user_conversation(
                memory, user_id, session_id, channel
            )
        )

        # Step 2: Create Interaction and EventBus
        interaction = await conversation.create_interaction(
            utterance=utterance, channel=channel
        )
        event_bus = InteractionEventBus(interaction.id)
        aggregator = ResponseAggregator(event_bus)
        
        # Record PersonaAction as the first action involved in processing
        interaction.add_action("PersonaAction")

        # Emit interaction started
        await event_bus.emit_interaction_started(
            user_id=resolved_user_id,
            session_id=resolved_session_id,
            utterance=utterance,
            channel=channel,
        )

        try:
            # Step 3: Canned response (if enabled)
            canned_response = None
            if self.canned_responses_enabled and not interaction.is_new_user():
                canned_response = await self._get_canned_response(
                    utterance, event_bus, interaction
                )
                if canned_response:
                    interaction.canned_response = canned_response

            # Step 4: Build conversation history
            messages = []
            if self.history_enabled:
                messages = await conversation.get_transcript(
                    limit=self.history_size,
                    max_statement_length=self.max_statement_length,
                    with_events=True,
                )

            # Add current utterance
            messages.append({"human": utterance})

            # Add canned response to context if present
            if canned_response:
                messages.append({"ai": canned_response})

            # Step 5: Filter parameters
            parameters = await self._get_filtered_parameters(
                messages, event_bus, interaction
            )

            # Step 6: Execute parameter actions
            directives = await self._run_parameter_actions(
                parameters, conversation, interaction, event_bus
            )

            # Add channel formatting directive
            channel_directive = get_channel_directive(channel)
            if channel_directive:
                interaction.add_directive(channel_directive)
                directives.append(channel_directive)

            # If interaction already has response (set by action), return
            if interaction.has_response():
                await self._finalize_interaction(
                    interaction, event_bus, aggregator
                )
                return PersonaActionResult(
                    user_id=resolved_user_id,
                    session_id=resolved_session_id,
                    interaction=interaction,
                    event_bus=event_bus,
                    response=interaction.response,
                    canned_response=canned_response,
                )

            # Step 7: Build final prompt
            final_prompt = self._build_final_prompt(
                parameters, directives, user, conversation
            )
            messages.append({"system": final_prompt})

            # Step 8: Call ModelAction
            response = await self._call_model(
                messages, interaction, event_bus, stream
            )

            # Set response on interaction
            if response:
                interaction.set_response(response)

            # Finalize
            await self._finalize_interaction(interaction, event_bus, aggregator)

            return PersonaActionResult(
                user_id=resolved_user_id,
                session_id=resolved_session_id,
                interaction=interaction,
                event_bus=event_bus,
                response=response,
                canned_response=canned_response,
            )

        except Exception as e:
            await event_bus.emit_error(str(e))
            logger.error(f"Error in PersonaAction.interact: {e}", exc_info=True)
            raise

    async def _resolve_user_conversation(
        self,
        memory: Memory,
        user_id: Optional[str],
        session_id: Optional[str],
        channel: str,
    ) -> tuple[User, Conversation, str, str]:
        """Resolve User and Conversation based on provided IDs.

        Scenarios:
        1. No user_id, no session_id → Create User + Conversation → Return both
        2. session_id only → Lookup Conversation, get User → Use them
        3. user_id only → Get/Create User, create Conversation → Return new session_id
        4. Both provided → Validate and use

        Args:
            memory: Memory node
            user_id: Optional user identifier
            session_id: Optional session identifier
            channel: Communication channel

        Returns:
            Tuple of (User, Conversation, user_id, session_id)
        """
        # Case 1: No IDs - create new user and conversation
        if not user_id and not session_id:
            new_user_id = f"user_{uuid.uuid4().hex[:16]}"
            user = await memory.get_user(new_user_id, create_if_missing=True)
            if not user:
                raise RuntimeError("Failed to create user")
            conversation = await user.create_conversation(channel=channel)
            return user, conversation, new_user_id, conversation.session_id

        # Case 2: session_id only - lookup conversation
        if session_id and not user_id:
            conversation = await memory.get_conversation_by_session(session_id)
            if not conversation:
                raise ValueError(f"Session '{session_id}' not found")
            user = await memory.get_user(
                conversation.user_id, create_if_missing=False
            )
            if not user:
                raise RuntimeError(f"User for session '{session_id}' not found")
            return user, conversation, conversation.user_id, session_id

        # Case 3: user_id only - get/create user, create conversation
        if user_id and not session_id:
            user = await memory.get_user(user_id, create_if_missing=True)
            if not user:
                raise RuntimeError(f"Failed to get/create user '{user_id}'")
            conversation = await user.create_conversation(channel=channel)
            return user, conversation, user_id, conversation.session_id

        # Case 4: Both provided - validate and use
        if user_id and session_id:
            conversation = await memory.get_conversation_by_session(session_id)
            if not conversation:
                raise ValueError(f"Session '{session_id}' not found")
            if conversation.user_id != user_id:
                raise ValueError(
                    f"Session '{session_id}' does not belong to user '{user_id}'"
                )
            user = await memory.get_user(user_id, create_if_missing=False)
            if not user:
                raise RuntimeError(f"User '{user_id}' not found")
            return user, conversation, user_id, session_id

        raise ValueError("Invalid user_id/session_id combination")

    async def _get_agent(self) -> Optional[Any]:
        """Get the agent this action belongs to."""
        from jvagent.core.agent import Agent

        if self.agent_id:
            return await Agent.get(self.agent_id)
        return None

    async def _get_memory(self, agent: Any) -> Optional[Memory]:
        """Get the Memory node for the agent.

        Args:
            agent: The Agent node to get memory for

        Returns:
            Memory node if found, None otherwise
        """
        # Use string "Memory" for node filter as nodes() expects entity name string
        memories = await agent.nodes(node="Memory", direction="both")
        return memories[0] if memories else None

    async def _get_canned_response(
        self,
        utterance: str,
        event_bus: InteractionEventBus,
        interaction: Optional[Interaction] = None,
    ) -> Optional[str]:
        """Get a canned response for the utterance.

        Uses the light model to classify and generate quick responses.

        Args:
            utterance: User's input
            event_bus: Event bus for emitting events
            interaction: Optional interaction to log model calls to

        Returns:
            Canned response if applicable, None otherwise
        """
        prompt = CANNED_RESPONSE_PROMPT.format(
            agent_name=self.persona_name,
            agent_role=self.persona_role,
            agent_description=self.persona_description,
            agent_capabilities=", ".join(self.persona_capabilities),
            utterance=utterance,
        )

        try:
            result = await self._model_call(
                [{"system": prompt}], use_light_model=True, interaction=interaction
            )
            if not result:
                return None

            # Parse JSON response
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    return None

            if not result.get("canned", False):
                return None

            message = result.get("message", "")
            category = result.get("category", "")

            if message:
                await event_bus.emit_canned_response(message, category)

            # For complex requests, return the canned message but continue processing
            if category == "complex_request":
                return message

            # For simple requests/greetings, this is the final response
            return message

        except Exception as e:
            await event_bus.emit_log("error", f"Canned response error: {e}")
            return None

    async def _get_filtered_parameters(
        self,
        messages: List[Dict[str, str]],
        event_bus: InteractionEventBus,
        interaction: Optional[Interaction] = None,
    ) -> List[PersonaParameter]:
        """Filter parameters using LLM.

        Args:
            messages: Conversation messages
            event_bus: Event bus for emitting events
            interaction: Optional interaction to log model calls to

        Returns:
            List of applicable parameters
        """
        manager = await self._ensure_parameter_manager()

        all_params = await manager.list_parameters(
            enabled_only=True
        )
        if not all_params:
            logger.debug("No parameters available for filtering")
            return []

        logger.debug(f"Filtering {len(all_params)} parameters")

        # Build parameter list for filtering
        param_list = [
            {
                "id": p.id,
                "condition": p.condition,
                "action": p.action,
                "response": p.response,
            }
            for p in all_params
        ]

        prompt = FILTER_PARAMETER_PROMPT.format(
            agent_role=self.persona_role,
            agent_description=self.persona_description,
            agent_capabilities=", ".join(self.persona_capabilities),
            parameters=json.dumps(param_list, indent=2),
        )

        filter_messages = list(messages)
        filter_messages.append({"system": prompt})

        try:
            result = await self._model_call(
                filter_messages, use_light_model=True, interaction=interaction
            )
            if not result:
                logger.debug("Parameter filter model call returned no result")
                return []

            # Parse JSON response
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse parameter filter response: {result[:200]}")
                    return []

            applicable_ids = result.get("ids", [])
            logger.debug(f"Parameter filter returned IDs: {applicable_ids}")

            if not applicable_ids:
                return []

            # Filter to applicable parameters - ensure string comparison
            applicable_ids_str = [str(id_val) for id_val in applicable_ids]
            filtered = [
                p for p in all_params if p.id in applicable_ids_str
            ]

            logger.debug(f"Filtered to {len(filtered)} applicable parameters")

            await event_bus.emit_parameter_filtered(
                [p.to_dict() for p in filtered]
            )

            return filtered

        except Exception as e:
            await event_bus.emit_log(
                "error", f"Parameter filtering error: {e}"
            )
            return []

    async def _run_parameter_actions(
        self,
        parameters: List[PersonaParameter],
        conversation: Conversation,
        interaction: Interaction,
        event_bus: InteractionEventBus,
    ) -> List[str]:
        """Execute actions specified by parameters.

        Args:
            parameters: Filtered parameters
            conversation: Current conversation
            interaction: Current interaction
            event_bus: Event bus for emitting events

        Returns:
            List of directives from action executions
        """
        directives: List[str] = []
        agent = await self._get_agent()
        if not agent:
            return directives

        # Track which actions we've already run
        executed_actions: set = set()

        for param in parameters:
            # Record the parameter
            interaction.add_parameter(param.to_dict())
            
            if not param.action:
                # Just apply the parameter (no action to run)
                await event_bus.emit_parameter_applied(
                    param.id, param.condition, param.response
                )
                continue

            # Skip if we've already run this action
            if param.action in executed_actions:
                continue

            # Store utterance in context for action to access
            conversation.data_set("visitor_utterance", interaction.utterance)

            # Get the action
            action = await agent.get_action(action_label=param.action)
            if not action:
                await event_bus.emit_log(
                    "warning", f"Action '{param.action}' not found"
                )
                continue

            await event_bus.emit_action_triggered(param.action)
            executed_actions.add(param.action)

            try:
                # Call the action's execute method
                result = await action.execute(conversation, interaction)

                if isinstance(result, str):
                    directives.append(result)
                    await event_bus.emit_action_result(param.action, result)
                elif isinstance(result, list):
                    for r in result:
                        if isinstance(r, str):
                            directives.append(r)
                            await event_bus.emit_action_result(param.action, r)

                # Record the action in the interaction
                interaction.add_action(param.action)

            except Exception as e:
                await event_bus.emit_error(
                    f"Action '{param.action}' failed: {e}"
                )
                logger.error(
                    f"Error executing action '{param.action}': {e}",
                    exc_info=True,
                )

            # If action set response directly, we may stop
            if interaction.has_response():
                break

        return directives

    def _build_final_prompt(
        self,
        parameters: List[PersonaParameter],
        directives: List[str],
        user: User,
        conversation: Conversation,
    ) -> str:
        """Build the final system prompt.

        Args:
            parameters: Filtered parameters
            directives: Collected directives
            user: Current user
            conversation: Current conversation

        Returns:
            Complete system prompt
        """
        # Get current date/time
        now = datetime.now()
        date_str = now.strftime("%A, %d %B, %Y")
        time_str = now.strftime("%I:%M %p")

        # Build parameters section
        if parameters:
            params_str = "\n".join(
                f"{i+1}. {p.to_prompt_format()}"
                for i, p in enumerate(parameters)
            )
            parameters_prompt = (
                f"{PARAMETER_DIRECTIVE}\n{params_str}\n{PARAMETERS_INSTRUCTION}"
            )
        else:
            parameters_prompt = NO_PARAMETERS_INSTRUCTION

        # Build directives section
        if directives:
            directives_str = "\n".join(
                f"{i+1}. {d}" for i, d in enumerate(directives)
            )
            directives_prompt = f"{DIRECTIVES_INSTRUCTION}\n{directives_str}"
        else:
            directives_prompt = NO_DIRECTIVES_INSTRUCTION

        # Use custom template or default
        template = self.agent_prompt_template or AGENT_PROMPT_TEMPLATE

        # Build final prompt
        prompt = template.format(
            agent_name=self.persona_name,
            agent_role=self.persona_role,
            agent_description=self.persona_description,
            agent_capabilities="\n-".join(self.persona_capabilities),
            user=user.user_id,
            date=date_str,
            time=time_str,
            parameters=parameters_prompt,
            directives=directives_prompt,
        )

        # Add user model from context if available
        user_model = conversation.data_get("user_model")
        if user_model:
            prompt += f"\n### USER DETAILS\n{user_model}"

        return prompt

    async def _model_call(
        self,
        messages: List[Dict[str, str]],
        use_light_model: bool = False,
        interaction: Optional[Interaction] = None,
    ) -> Optional[Union[str, Dict[str, Any]]]:
        """Make a call to the model action.

        Converts persona action message format to ModelAction.query() format:
        - Extracts last human message as prompt
        - Extracts system message if present
        - Converts remaining messages to history format

        Args:
            messages: Messages in persona format ({"human": "...", "ai": "...", "system": "..."})
            use_light_model: Whether to use the light model
            interaction: Optional interaction to log the model result to

        Returns:
            Model response
        """
        agent = await self._get_agent()
        if not agent:
            return None

        # Get model action by entity type
        from jvagent.action.model.base import ModelAction

        model_action = await agent.get_action_by_type(self.model_action_type)
        if not model_action or not isinstance(model_action, ModelAction):
            logger.error(
                f"Model action of type '{self.model_action_type}' not found for agent"
            )
            return None

        try:
            # Convert persona message format to ModelAction format
            system_message: Optional[str] = None
            history: List[Dict[str, Any]] = []
            prompt: str = ""

            # Process messages: extract system, build history, get last human as prompt
            for msg in messages:
                if "system" in msg:
                    # System message (usually the final prompt)
                    system_message = msg["system"]
                elif "human" in msg:
                    # If we already have a prompt, add previous human/ai pair to history
                    if prompt:
                        # Add previous prompt as user message to history
                        history.append({"role": "user", "content": prompt})
                    # Update prompt to current human message
                    prompt = msg["human"]
                elif "ai" in msg:
                    # Add AI response to history (assistant role)
                    history.append({"role": "assistant", "content": msg["ai"]})

            # If no prompt found, use empty string
            if not prompt:
                logger.warning("No human message found in messages, using empty prompt")
                prompt = ""

            model_name = (
                self.light_model_name if use_light_model else self.model_name
            )
            result = await model_action.query(
                prompt=prompt,
                system=system_message,
                history=history if history else None,
                model=model_name,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
            )
            
            # Log the model result if interaction provided
            if interaction and result:
                # Record ModelAction in the actions list
                interaction.add_action(self.model_action_type)
                interaction.add_model_result(result.to_dict())
            
            return result.response if result else None
        except Exception as e:
            logger.error(f"Model call error: {e}", exc_info=True)
            return None

    async def _call_model(
        self,
        messages: List[Dict[str, str]],
        interaction: Interaction,
        event_bus: InteractionEventBus,
        stream: bool = False,
    ) -> Optional[str]:
        """Call the model for the main response.

        Args:
            messages: Messages to send
            interaction: Current interaction
            event_bus: Event bus
            stream: Whether to stream

        Returns:
            Response text
        """
        # For now, use non-streaming call
        # Streaming support would require async generator handling
        result = await self._model_call(messages, use_light_model=False, interaction=interaction)
        if result:
            await event_bus.emit_response_complete(str(result))
        return str(result) if result else None

    async def _finalize_interaction(
        self,
        interaction: Interaction,
        event_bus: InteractionEventBus,
        aggregator: ResponseAggregator,
    ) -> None:
        """Finalize the interaction.

        Args:
            interaction: Interaction to finalize
            event_bus: Event bus
            aggregator: Response aggregator
        """
        # Populate interaction from aggregator
        aggregator.populate_interaction(interaction)

        # Close and save interaction
        interaction.close_interaction()
        await interaction.save()

        # Emit completion event
        await event_bus.emit_interaction_complete({})

    async def execute(
        self,
        conversation: Conversation,
        interaction: Interaction,
    ) -> Optional[Union[str, List[str]]]:
        """Execute method for action delegation pattern.

        This allows PersonaAction to be triggered by other PersonaActions
        via the parameter action mechanism.

        Args:
            conversation: Current conversation
            interaction: Current interaction

        Returns:
            Optional directive string(s)
        """
        # PersonaAction as a delegated action would typically
        # just process and set the interaction response
        # For now, return None to indicate no directive
        return None

    async def healthcheck(self) -> bool:
        """Check if the PersonaAction is healthy.

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not self.persona_name or not self.persona_role:
                return False

            # Check if model action is available
            agent = await self._get_agent()
            if agent and self.model_action_type:
                action = await agent.get_action_by_type(self.model_action_type)
                if not action:
                    return False

            return True
        except Exception as e:
            logger.error(f"Healthcheck failed: {e}")
            return False

    # Parameter management methods

    async def get_parameters(
        self, enabled_only: bool = True
    ) -> List[PersonaParameter]:
        """Get all parameters.

        Args:
            enabled_only: If True, only return enabled parameters

        Returns:
            List of parameters
        """
        manager = await self._ensure_parameter_manager()
        return await manager.list_parameters(enabled_only)

    async def add_parameter(self, param_data: Dict[str, Any]) -> str:
        """Add a new parameter.

        Args:
            param_data: Parameter data dictionary

        Returns:
            ID of the added parameter
        """
        manager = await self._ensure_parameter_manager()
        param = PersonaParameter.from_dict(param_data)
        return await manager.add_parameter(param)

    async def update_parameter(
        self, param_id: str, updates: Dict[str, Any]
    ) -> Optional[PersonaParameter]:
        """Update a parameter.

        Args:
            param_id: Parameter ID
            updates: Updates to apply

        Returns:
            Updated parameter or None
        """
        manager = await self._ensure_parameter_manager()
        return await manager.update_parameter(param_id, updates)

    async def delete_parameter(self, param_id: str) -> bool:
        """Delete a parameter.

        Args:
            param_id: Parameter ID

        Returns:
            True if deleted
        """
        manager = await self._ensure_parameter_manager()
        return await manager.delete_parameter(param_id)

    async def import_parameters(self, params: List[Dict[str, Any]]) -> int:
        """Import parameters.

        Args:
            params: List of parameter dictionaries

        Returns:
            Number imported
        """
        manager = await self._ensure_parameter_manager()
        return await manager.import_parameters(params)
