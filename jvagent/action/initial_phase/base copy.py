"""InitialPhaseAction - Event-driven input collection and instruction generation.

This module provides the core InitialPhaseAction class for collecting inputs
(utterance, parameters, context, competencies), filtering via vector search,
evaluating with LLM, and outputting structured JSON instructions.
"""

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.initial_phase.events import InitialPhaseEventBus
from jvagent.action.initial_phase.models import (
    Competency,
    ExecutionRequirement,
    InitialPhaseInstructions,
    Parameter,
    Workflow,
)
from jvagent.action.initial_phase.prompts import (
    INITIAL_PHASE_EVALUATION_PROMPT,
    format_competencies_for_prompt,
    format_history_for_prompt,
    format_parameters_for_prompt,
)
from jvagent.action.initial_phase.typesense_manager import TypesenseManager
from jvagent.memory import Conversation, Interaction, Memory, User

logger = logging.getLogger(__name__)


class InitialPhaseResult:
    """Result container for InitialPhaseAction.process().

    Provides access to the processing result including structured instructions,
    event bus for all emitted events, and user/session identifiers.

    Attributes:
        user_id: User identifier
        session_id: Session identifier
        instructions: Structured instructions (InitialPhaseInstructions)
        interaction: The Interaction node
        event_bus: Event bus with all emitted events
        processing_duration: Total processing time in seconds
    """

    def __init__(
        self,
        user_id: str,
        session_id: str,
        instructions: InitialPhaseInstructions,
        interaction: Interaction,
        event_bus: InitialPhaseEventBus,
        processing_duration: float = 0.0,
    ):
        """Initialize the result.

        Args:
            user_id: User identifier
            session_id: Session identifier
            instructions: Structured instructions
            interaction: The Interaction node
            event_bus: Event bus with all emitted events
            processing_duration: Total processing time in seconds
        """
        self.user_id = user_id
        self.session_id = session_id
        self.instructions = instructions
        self.interaction = interaction
        self.event_bus = event_bus
        self.processing_duration = processing_duration

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary.

        Returns:
            Dictionary representation with:
            - user_id: User identifier
            - session_id: Session identifier
            - instructions: The structured instructions JSON
            - interaction: Interaction details
            - events: All events emitted during processing
            - processing_duration: Total processing time
        """
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "instructions": self.instructions.to_dict(),
            "interaction": {
                "id": self.interaction.id,
                "utterance": self.interaction.utterance,
                "actions": self.interaction.actions,
                "parameters": self.interaction.parameters,
            },
            "events": [e.to_dict() for e in self.event_bus.get_events()],
            "processing_duration": self.processing_duration,
        }

    def to_json(self) -> str:
        """Convert result to JSON string.

        Returns:
            JSON string representation
        """
        return json.dumps(self.to_dict(), indent=2)


class InitialPhaseAction(Action):
    """Initial Phase Action for event-driven input processing.

    The InitialPhaseAction is the first phase of agent processing. It:
    1. Collects inputs (utterance, parameters, context, competencies)
    2. Filters parameters using vector search (Typesense)
    3. Uses LLM to evaluate and generate final instructions
    4. Outputs structured JSON instructions for downstream processing

    This action extends the base Action class and provides:
    - Parameter management with execution requirements
    - Competency management for complex behaviors
    - Workflow definitions
    - Vector search integration (Typesense)
    - LLM-driven instruction generation
    - Comprehensive event tracking

    Attributes:
        agent_name: Agent display name
        agent_role: Agent role description
        agent_description: Detailed agent description
        agent_capabilities: List of agent capabilities
        model_action_type: Entity type of the ModelAction
        model_name: Model name for LLM evaluation
        model_temperature: Temperature for LLM generation
        model_max_tokens: Max tokens for LLM generation
        typesense_host: Typesense server host
        typesense_port: Typesense server port
        typesense_protocol: Typesense protocol (http/https)
        typesense_api_key: Typesense API key
        embedding_dim: Dimension of embedding vectors
        vector_search_top_k: Number of results from vector search
        history_enabled: Whether to include conversation history
        history_size: Number of interactions to include
        timezone: Timezone for date/time
    """

    # Agent Identity
    agent_name: str = attribute(default="Agent", description="Agent display name")
    agent_role: str = attribute(
        default="An AI Assistant", description="Agent role description"
    )
    agent_description: str = attribute(
        default="You are friendly and helpful",
        description="Detailed agent description",
    )
    agent_capabilities: List[str] = attribute(
        default_factory=list, description="List of agent capabilities"
    )

    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAIModelAction",
        description="Entity type of the ModelAction to use",
    )
    model_name: str = attribute(default="gpt-4o", description="Model name for LLM")
    model_temperature: float = attribute(
        default=0.3, description="Temperature for LLM generation"
    )
    model_max_tokens: int = attribute(
        default=4096, description="Max tokens for LLM generation"
    )

    # Typesense Configuration
    typesense_host: str = attribute(
        default="localhost", description="Typesense server host"
    )
    typesense_port: int = attribute(
        default=8108, description="Typesense server port"
    )
    typesense_protocol: str = attribute(
        default="http", description="Typesense protocol (http/https)"
    )
    typesense_api_key: str = attribute(
        default="xyz", description="Typesense API key"
    )
    embedding_dim: int = attribute(
        default=1536, description="Dimension of embedding vectors"
    )
    vector_search_top_k: int = attribute(
        default=10, description="Number of results from vector search"
    )

    # Behavior
    history_enabled: bool = attribute(
        default=True, description="Whether to include conversation history"
    )
    history_size: int = attribute(
        default=5, description="Number of interactions in history"
    )
    logger.info(f"InitialPhaseAction registered")

    async def _initialize(self) -> None:
        """Initialize Typesense and collections."""
        if self._initialized:
            return

        # Initialize Typesense manager
        self._typesense_manager = TypesenseManager(
            host=self.typesense_host,
            port=self.typesense_port,
            protocol=self.typesense_protocol,
            api_key=self.typesense_api_key,
            embedding_dim=self.embedding_dim,
        )

        # Initialize collections
        try:
            await self._typesense_manager.initialize_collections()
            logger.info("Typesense collections initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Typesense collections: {e}")

        self._initialized = True

    async def process(
        self,
        utterance: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        channel: str = "default",
    ) -> InitialPhaseResult:
        """Main processing method for Initial Phase.

        This is the core method that handles the full Initial Phase flow:
        1. Resolve User/Conversation based on user_id/session_id
        2. Create Interaction and EventBus
        3. Build conversation context
        4. Generate embedding for utterance
        5. Vector search for relevant parameters/competencies
        6. LLM evaluation to generate instructions
        7. Return structured result

        Args:
            utterance: User's input text
            user_id: Optional user identifier
            session_id: Optional session identifier
            channel: Communication channel

        Returns:
            InitialPhaseResult with instructions and event bus
        """
        start_time = time.time()

        # Ensure initialized
        await self._initialize()

        # Get agent and memory
        agent = await self._get_agent()
        if not agent:
            raise RuntimeError("InitialPhaseAction not attached to an agent")

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
        event_bus = InitialPhaseEventBus(interaction.id)

        # Record InitialPhaseAction as the first action involved
        interaction.add_action("InitialPhaseAction")

        # Emit phase started
        await event_bus.emit_phase_started(
            utterance=utterance,
            user_id=resolved_user_id,
            session_id=resolved_session_id,
        )

        try:
            # Step 3: Build conversation history
            messages = []
            if self.history_enabled:
                messages = await conversation.get_transcript(
                    limit=self.history_size,
                    max_statement_length=self.max_statement_length,
                    with_events=True,
                )

            # Step 4: Generate embedding for utterance
            embedding = await self._generate_embedding(utterance)

            # Step 5: Vector search for parameters and competencies
            search_start = time.time()
            await event_bus.emit_vector_search_started(utterance)

            filtered_params, filtered_comps = await self._vector_search(
                embedding, event_bus
            )

            search_duration = time.time() - search_start
            await event_bus.emit_vector_search_complete(
                parameters_found=len(filtered_params),
                competencies_found=len(filtered_comps),
                search_duration=search_duration,
            )

            # Step 6: LLM evaluation to generate instructions
            eval_start = time.time()
            await event_bus.emit_llm_evaluation_started()

            instructions = await self._generate_instructions(
                utterance=utterance,
                user=user,
                conversation=conversation,
                messages=messages,
                filtered_parameters=filtered_params,
                filtered_competencies=filtered_comps,
                is_first_interaction=len(messages) == 0,
                event_bus=event_bus,
                interaction=interaction,
            )

            eval_duration = time.time() - eval_start
            await event_bus.emit_llm_evaluation_complete(
                simplified_intent=instructions.simplified_intent,
                parameters_count=len(instructions.applicable_parameters),
                actions_count=len(instructions.required_actions),
                workflows_count=len(instructions.required_workflows),
                evaluation_duration=eval_duration,
            )

            # Emit instructions generated
            await event_bus.emit_instructions_generated(instructions.to_dict())

            # Record parameters in interaction
            for param in instructions.applicable_parameters:
                interaction.add_parameter(param)

            # Step 7: Finalize
            await interaction.save()

            total_duration = time.time() - start_time
            await event_bus.emit_phase_complete(total_duration)

            return InitialPhaseResult(
                user_id=resolved_user_id,
                session_id=resolved_session_id,
                instructions=instructions,
                interaction=interaction,
                event_bus=event_bus,
                processing_duration=total_duration,
            )

        except Exception as e:
            await event_bus.emit_error(str(e))
            logger.error(f"Error in InitialPhaseAction.process: {e}", exc_info=True)
            raise

    async def _resolve_user_conversation(
        self,
        memory: Memory,
        user_id: Optional[str],
        session_id: Optional[str],
        channel: str,
    ) -> tuple[User, Conversation, str, str]:
        """Resolve User and Conversation based on provided IDs.

        Same logic as PersonaAction for consistency.
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
        """Get the Memory node for the agent."""
        memories = await agent.nodes(node="Memory", direction="both")
        return memories[0] if memories else None

    async def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using ModelAction.

        Args:
            text: Text to embed

        Returns:
            Embedding vector
        """
        agent = await self._get_agent()
        if not agent:
            raise RuntimeError("No agent available for embedding generation")

        # Get model action
        from jvagent.action.model.base import ModelAction

        model_action = await agent.get_action_by_type(self.model_action_type)
        if not model_action or not isinstance(model_action, ModelAction):
            raise RuntimeError(
                f"Model action of type '{self.model_action_type}' not found"
            )

        # Generate embedding
        try:
            embedding = await model_action.embed(text)
            return embedding if embedding else []
        except Exception as e:
            logger.error(f"Error generating embedding: {e}", exc_info=True)
            return []

    async def _vector_search(
        self, query_embedding: List[float], event_bus: InitialPhaseEventBus
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Perform vector search for parameters and competencies.

        Args:
            query_embedding: Query embedding vector
            event_bus: Event bus for logging

        Returns:
            Tuple of (filtered_parameters, filtered_competencies)
        """
        if not self._typesense_manager:
            await event_bus.emit_log("warning", "Typesense manager not initialized")
            return [], []

        try:
            # Search parameters
            param_results = await self._typesense_manager.search_parameters(
                query_embedding=query_embedding,
                top_k=self.vector_search_top_k,
                enabled_only=True,
            )

            # Search competencies
            comp_results = await self._typesense_manager.search_competencies(
                query_embedding=query_embedding,
                top_k=max(5, self.vector_search_top_k // 2),
                enabled_only=True,
            )

            await event_bus.emit_parameters_filtered(
                [p['id'] for p in param_results]
            )
            await event_bus.emit_competencies_filtered(
                [c['id'] for c in comp_results]
            )

            return param_results, comp_results

        except Exception as e:
            await event_bus.emit_error(f"Vector search error: {e}")
            logger.error(f"Vector search error: {e}", exc_info=True)
            return [], []

    async def _generate_instructions(
        self,
        utterance: str,
        user: User,
        conversation: Conversation,
        messages: List[Dict[str, str]],
        filtered_parameters: List[Dict[str, Any]],
        filtered_competencies: List[Dict[str, Any]],
        is_first_interaction: bool,
        event_bus: InitialPhaseEventBus,
        interaction: Interaction,
    ) -> InitialPhaseInstructions:
        """Generate structured instructions using LLM evaluation.

        Args:
            utterance: User utterance
            user: User node
            conversation: Conversation node
            messages: Conversation history
            filtered_parameters: Parameters from vector search
            filtered_competencies: Competencies from vector search
            is_first_interaction: Whether this is the first interaction
            event_bus: Event bus
            interaction: Interaction node

        Returns:
            InitialPhaseInstructions object
        """
        # Get current date/time
        now = datetime.now()
        date_str = now.strftime("%A, %d %B, %Y")
        time_str = now.strftime("%I:%M %p")

        # Get agent actions and workflows
        agent = await self._get_agent()
        available_actions = await self._get_available_actions(agent)
        available_workflows = list(self._workflows.values())

        # Format prompt components
        history_text = format_history_for_prompt(messages, max_length=10)
        params_text = format_parameters_for_prompt(filtered_parameters)
        comps_text = format_competencies_for_prompt(filtered_competencies)

        # Build prompt
        prompt = INITIAL_PHASE_EVALUATION_PROMPT.format(
            agent_role=self.agent_role,
            agent_description=self.agent_description,
            agent_capabilities="\n- ".join(self.agent_capabilities),
            date=date_str,
            time=time_str,
            user_id=user.user_id,
            is_first_interaction=is_first_interaction,
            utterance=utterance,
            history=history_text,
            filtered_parameters=params_text,
            competencies=comps_text,
            available_actions=self._format_actions(available_actions),
            available_workflows=self._format_workflows(available_workflows),
        )

        # Call model
        result = await self._model_call(
            prompt=prompt, system=None, interaction=interaction
        )

        if not result:
            # Fallback: return basic instructions
            await event_bus.emit_log("warning", "LLM returned no result, using fallback")
            return InitialPhaseInstructions(
                simplified_intent="user message",
                applicable_parameters=[],
                required_workflows=[],
                required_actions=[],
                context={},
                metadata={"fallback": True},
            )

        # Parse JSON response
        try:
            instructions_dict = json.loads(result)
            return InitialPhaseInstructions.from_dict(instructions_dict)
        except json.JSONDecodeError as e:
            await event_bus.emit_error(f"Failed to parse instructions JSON: {e}")
            logger.error(f"Failed to parse instructions: {result[:200]}")
            # Return fallback
            return InitialPhaseInstructions(
                simplified_intent="user message",
                applicable_parameters=[],
                required_workflows=[],
                required_actions=[],
                context={},
                metadata={"error": "json_parse_error", "fallback": True},
            )

    async def _get_available_actions(self, agent: Any) -> List[str]:
        """Get list of available action labels from agent.

        Args:
            agent: Agent node

        Returns:
            List of action labels
        """
        if not agent:
            return []

        try:
            actions = await agent.get_actions()
            return [action.label for action in actions if action.enabled]
        except Exception as e:
            logger.error(f"Error getting available actions: {e}")
            return []

    def _format_actions(self, actions: List[str]) -> str:
        """Format actions for prompt."""
        if not actions:
            return "No actions available."
        return "\n- ".join([""] + actions)

    def _format_workflows(self, workflows: List[Workflow]) -> str:
        """Format workflows for prompt."""
        if not workflows:
            return "No workflows available."
        lines = []
        for wf in workflows:
            lines.append(f"- {wf.id}: {wf.name}")
            lines.append(f"  Description: {wf.description}")
        return "\n".join(lines)

    async def _model_call(
        self,
        prompt: str,
        system: Optional[str] = None,
        interaction: Optional[Interaction] = None,
    ) -> Optional[str]:
        """Make a call to the model action.

        Args:
            prompt: User prompt
            system: System message (optional)
            interaction: Interaction to log to (optional)

        Returns:
            Model response text
        """
        agent = await self._get_agent()
        if not agent:
            return None

        from jvagent.action.model.base import ModelAction

        model_action = await agent.get_action_by_type(self.model_action_type)
        if not model_action or not isinstance(model_action, ModelAction):
            logger.error(
                f"Model action of type '{self.model_action_type}' not found"
            )
            return None

        try:
            result = await model_action.query(
                prompt=prompt,
                system=system,
                history=None,
                model=self.model_name,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
            )

            # Log model result if interaction provided
            if interaction and result:
                interaction.add_action(self.model_action_type)
                interaction.add_model_result(result.to_dict())

            return result.response if result else None
        except Exception as e:
            logger.error(f"Model call error: {e}", exc_info=True)
            return None

    # ========================================================================
    # Parameter Management
    # ========================================================================

    async def add_parameter(
        self, parameter_data: Dict[str, Any], embedding: Optional[List[float]] = None
    ) -> str:
        """Add a parameter and index in Typesense.

        Args:
            parameter_data: Parameter data dictionary
            embedding: Optional pre-computed embedding

        Returns:
            Parameter ID
        """
        param = Parameter.from_dict(parameter_data)

        # Generate ID if not provided
        if not param.id:
            param.id = f"param_{uuid.uuid4().hex[:12]}"

        # Generate embedding if not provided
        if not embedding:
            embedding = await self._generate_embedding(param.condition)

        # Store locally
        self._parameters[param.id] = param

        # Index in Typesense
        await self._initialize()
        if self._typesense_manager:
            await self._typesense_manager.upsert_parameter(param, embedding)

        return param.id

    async def update_parameter(
        self, param_id: str, updates: Dict[str, Any]
    ) -> Optional[Parameter]:
        """Update a parameter.

        Args:
            param_id: Parameter ID
            updates: Updates to apply

        Returns:
            Updated parameter or None
        """
        if param_id not in self._parameters:
            return None

        param = self._parameters[param_id]

        # Apply updates
        for key, value in updates.items():
            if hasattr(param, key):
                setattr(param, key, value)

        # Re-generate embedding if condition changed
        if "condition" in updates:
            embedding = await self._generate_embedding(param.condition)
            if self._typesense_manager:
                await self._typesense_manager.upsert_parameter(param, embedding)

        return param

    async def delete_parameter(self, param_id: str) -> bool:
        """Delete a parameter.

        Args:
            param_id: Parameter ID

        Returns:
            True if deleted
        """
        if param_id not in self._parameters:
            return False

        del self._parameters[param_id]

        if self._typesense_manager:
            await self._typesense_manager.delete_parameter(param_id)

        return True

    async def get_parameters(self, enabled_only: bool = True) -> List[Parameter]:
        """Get all parameters.

        Args:
            enabled_only: Only return enabled parameters

        Returns:
            List of parameters
        """
        params = list(self._parameters.values())
        if enabled_only:
            params = [p for p in params if p.enabled]
        return params

    # ========================================================================
    # Competency Management
    # ========================================================================

    async def add_competency(
        self, competency_data: Dict[str, Any], embedding: Optional[List[float]] = None
    ) -> str:
        """Add a competency and index in Typesense.

        Args:
            competency_data: Competency data dictionary
            embedding: Optional pre-computed embedding

        Returns:
            Competency ID
        """
        comp = Competency.from_dict(competency_data)

        # Generate ID if not provided
        if not comp.id:
            comp.id = f"comp_{uuid.uuid4().hex[:12]}"

        # Generate embedding if not provided
        if not embedding:
            embedding = await self._generate_embedding(comp.description)

        # Store locally
        self._competencies[comp.id] = comp

        # Index in Typesense
        await self._initialize()
        if self._typesense_manager:
            await self._typesense_manager.upsert_competency(comp, embedding)

        return comp.id

    async def delete_competency(self, comp_id: str) -> bool:
        """Delete a competency.

        Args:
            comp_id: Competency ID

        Returns:
            True if deleted
        """
        if comp_id not in self._competencies:
            return False

        del self._competencies[comp_id]

        if self._typesense_manager:
            await self._typesense_manager.delete_competency(comp_id)

        return True

    async def get_competencies(self, enabled_only: bool = True) -> List[Competency]:
        """Get all competencies.

        Args:
            enabled_only: Only return enabled competencies

        Returns:
            List of competencies
        """
        comps = list(self._competencies.values())
        if enabled_only:
            comps = [c for c in comps if c.enabled]
        return comps

    # ========================================================================
    # Workflow Management
    # ========================================================================

    async def add_workflow(self, workflow_data: Dict[str, Any]) -> str:
        """Add a workflow.

        Args:
            workflow_data: Workflow data dictionary

        Returns:
            Workflow ID
        """
        workflow = Workflow.from_dict(workflow_data)

        # Generate ID if not provided
        if not workflow.id:
            workflow.id = f"wf_{uuid.uuid4().hex[:12]}"

        self._workflows[workflow.id] = workflow
        return workflow.id

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow.

        Args:
            workflow_id: Workflow ID

        Returns:
            True if deleted
        """
        if workflow_id not in self._workflows:
            return False

        del self._workflows[workflow_id]
        return True

    async def get_workflows(self, enabled_only: bool = True) -> List[Workflow]:
        """Get all workflows.

        Args:
            enabled_only: Only return enabled workflows

        Returns:
            List of workflows
        """
        wfs = list(self._workflows.values())
        if enabled_only:
            wfs = [w for w in wfs if w.enabled]
        return wfs

    async def healthcheck(self) -> bool:
        """Check if the InitialPhaseAction is healthy.

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not self.agent_name or not self.agent_role:
                return False

            # Check if Typesense is accessible
            if self._typesense_manager:
                # Simple check - try to access client
                return self._typesense_manager.client is not None

            return True
        except Exception as e:
            logger.error(f"Healthcheck failed: {e}")
            return False


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


class InitialPhaseActionResult:
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


# class PersonaAction(Action):
#     """Core interact action with LLM-driven behavioral parameters.

#     PersonaAction provides agent behavioral modeling inspired by the Parlant
#     PersonaInteractAction. It features:
#     - Configurable persona (name, role, description, capabilities)
#     - LLM-driven parameter filtering
#     - Action delegation via standard execute() method
#     - Event bus for async responses (canned responses, streaming)

#     Attributes:
#         persona_name: Agent display name
#         persona_role: Agent role description
#         persona_description: Detailed agent description
#         persona_capabilities: List of agent capabilities
#         model_action_type: Entity type of the ModelAction (e.g., "OpenAIModelAction")
#         model_name: Default model name
#         light_model_name: Model for parameter filtering (faster/cheaper)
#         model_temperature: Temperature for LLM generation
#         model_max_tokens: Max tokens for LLM generation
#         canned_responses_enabled: Whether to use canned responses
#         streaming: Whether to stream responses by default
#         history_enabled: Whether to include conversation history
#         history_size: Number of interactions to include in history
#         tts_enabled: Whether text-to-speech is enabled
#         timezone: Timezone for date/time in prompts
#         base_parameters: Base behavioral parameters
#     """

#     # Agent Identity
#     persona_name: str = attribute(
#         default="Agent", description="Agent display name"
#     )
#     persona_role: str = attribute(
#         default="An AI Assistant", description="Agent role description"
#     )
#     persona_description: str = attribute(
#         default="You are friendly and helpful",
#         description="Detailed agent description",
#     )
#     persona_capabilities: List[str] = attribute(
#         default_factory=list, description="List of agent capabilities"
#     )

#     # Model Configuration
#     model_action_type: str = attribute(
#         default="OpenAIModelAction",
#         description="Entity type of the ModelAction to use (e.g., OpenAIModelAction)",
#     )
#     model_name: str = attribute(
#         default="gpt-4o", description="Default model name"
#     )
#     light_model_name: str = attribute(
#         default="gpt-4o-mini", description="Model for parameter filtering"
#     )
#     model_temperature: float = attribute(
#         default=0.3, description="Temperature for LLM generation"
#     )
#     model_max_tokens: int = attribute(
#         default=4096, description="Max tokens for LLM generation"
#     )

#     # Behavior
#     canned_responses_enabled: bool = attribute(
#         default=False, description="Whether to use canned responses"
#     )
#     streaming: bool = attribute(
#         default=False, description="Whether to stream responses by default"
#     )
#     history_enabled: bool = attribute(
#         default=True, description="Whether to include conversation history"
#     )
#     history_size: int = attribute(
#         default=5, description="Number of interactions in history"
#     )
#     max_statement_length: int = attribute(
#         default=500, description="Max length for statements in history"
#     )
#     tts_enabled: bool = attribute(
#         default=False, description="Whether TTS is enabled"
#     )
#     timezone: str = attribute(
#         default="UTC", description="Timezone for date/time"
#     )

#     # Parameters
#     base_parameters: List[Dict[str, Any]] = attribute(
#         default_factory=list, description="Base behavioral parameters"
#     )

#     # Custom prompt template (optional override)
#     agent_prompt_template: str = attribute(
#         default="", description="Custom agent prompt template"
#     )

#     # Internal state (transient)
#     _parameter_manager: Optional[ParameterManager] = attribute(
#         private=True, default=None
#     )
#     _parameters_initialized: bool = attribute(
#         private=True, default=False
#     )

#     async def on_register(self) -> None:
#         """Initialize parameters when action is registered."""
#         await super().on_register()

#         await self._ensure_parameter_manager()
#         logger.info(f"PersonaAction '{self.label}' registered with parameters")

#     async def _ensure_parameter_manager(self) -> ParameterManager:
#         """Ensure the parameter manager is initialized with base parameters."""
#         if not self._parameter_manager:
#             self._parameter_manager = ParameterManager(self.id)

#         if not self._parameters_initialized:
#             await self._initialize_base_parameters()

#         return self._parameter_manager

#     async def _initialize_base_parameters(self) -> None:
#         """Load default and configured base parameters once."""
#         if self._parameters_initialized:
#             return

#         manager = self._parameter_manager or ParameterManager(self.id)
#         base_params: List[Dict[str, Any]] = list(DEFAULT_BASE_PARAMETERS)
#         base_params.extend(self.base_parameters)

#         if base_params:
#             await manager.import_parameters(base_params)

#         self._parameter_manager = manager
#         self._parameters_initialized = True

#     async def interact(
#         self,
#         utterance: str,
#         user_id: Optional[str] = None,
#         session_id: Optional[str] = None,
#         channel: str = "default",
#         stream: bool = False,
#     ) -> PersonaActionResult:
#         """Main interaction method.

#         Handles the full interaction flow:
#         1. Resolve User/Conversation based on user_id/session_id
#         2. Create Interaction and EventBus
#         3. Process canned response (if enabled)
#         4. Filter and apply parameters
#         5. Execute parameter actions
#         6. Build prompt and call ModelAction
#         7. Return result with all events

#         Args:
#             utterance: Users input text
#             user_id: Optional user identifier
#             session_id: Optional session identifier
#             channel: Communication channel
#             stream: Whether to stream the response

#         Returns:
#             PersonaActionResult with response and event bus
#         """
#         # Get agent and memory
#         agent = await self._get_agent()
#         if not agent:
#             raise RuntimeError("PersonaAction not attached to an agent")

#         memory = await self._get_memory(agent)
#         if not memory:
#             raise RuntimeError("Agent has no Memory node")

#         # Step 1: Resolve User and Conversation
#         user, conversation, resolved_user_id, resolved_session_id = (
#             await self._resolve_user_conversation(
#                 memory, user_id, session_id, channel
#             )
#         )

#         # Step 2: Create Interaction and EventBus
#         interaction = await conversation.create_interaction(
#             utterance=utterance, channel=channel
#         )
#         event_bus = InteractionEventBus(interaction.id)
#         aggregator = ResponseAggregator(event_bus)

#         # Record PersonaAction as the first action involved in processing
#         interaction.add_action("PersonaAction")

#         # Emit interaction started
#         await event_bus.emit_interaction_started(
#             user_id=resolved_user_id,
#             session_id=resolved_session_id,
#             utterance=utterance,
#             channel=channel,
#         )

#         try:
#             # Step 3: Canned response (if enabled)
#             canned_response = None
#             if self.canned_responses_enabled and not interaction.is_new_user():
#                 canned_response = await self._get_canned_response(
#                     utterance, event_bus, interaction
#                 )
#                 if canned_response:
#                     interaction.canned_response = canned_response

#             # Step 4: Build conversation history
#             messages = []
#             if self.history_enabled:
#                 messages = await conversation.get_transcript(
#                     limit=self.history_size,
#                     max_statement_length=self.max_statement_length,
#                     with_events=True,
#                 )

#             # Add current utterance
#             messages.append({"human": utterance})

#             # Add canned response to context if present
#             if canned_response:
#                 messages.append({"ai": canned_response})

#             # Step 5: Filter parameters
#             parameters = await self._get_filtered_parameters(
#                 messages, event_bus, interaction
#             )

#             # Step 6: Execute parameter actions
#             directives = await self._run_parameter_actions(
#                 parameters, conversation, interaction, event_bus
#             )

#             # Add channel formatting directive
#             channel_directive = get_channel_directive(channel)
#             if channel_directive:
#                 interaction.add_directive(channel_directive)
#                 directives.append(channel_directive)

#             # If interaction already has response (set by action), return
#             if interaction.has_response():
#                 await self._finalize_interaction(
#                     interaction, event_bus, aggregator
#                 )
#                 return PersonaActionResult(
#                     user_id=resolved_user_id,
#                     session_id=resolved_session_id,
#                     interaction=interaction,
#                     event_bus=event_bus,
#                     response=interaction.response,
#                     canned_response=canned_response,
#                 )

#             # Step 7: Build final prompt
#             final_prompt = self._build_final_prompt(
#                 parameters, directives, user, conversation
#             )
#             messages.append({"system": final_prompt})

#             # Step 8: Call ModelAction
#             response = await self._call_model(
#                 messages, interaction, event_bus, stream
#             )

#             # Set response on interaction
#             if response:
#                 interaction.set_response(response)

#             # Finalize
#             await self._finalize_interaction(interaction, event_bus, aggregator)

#             return PersonaActionResult(
#                 user_id=resolved_user_id,
#                 session_id=resolved_session_id,
#                 interaction=interaction,
#                 event_bus=event_bus,
#                 response=response,
#                 canned_response=canned_response,
#             )

#         except Exception as e:
#             await event_bus.emit_error(str(e))
#             logger.error(f"Error in PersonaAction.interact: {e}", exc_info=True)
#             raise

#     async def _resolve_user_conversation(
#         self,
#         memory: Memory,
#         user_id: Optional[str],
#         session_id: Optional[str],
#         channel: str,
#     ) -> tuple[User, Conversation, str, str]:
#         """Resolve User and Conversation based on provided IDs.

#         Scenarios:
#         1. No user_id, no session_id → Create User + Conversation → Return both
#         2. session_id only → Lookup Conversation, get User → Use them
#         3. user_id only → Get/Create User, create Conversation → Return new session_id
#         4. Both provided → Validate and use

#         Args:
#             memory: Memory node
#             user_id: Optional user identifier
#             session_id: Optional session identifier
#             channel: Communication channel

#         Returns:
#             Tuple of (User, Conversation, user_id, session_id)
#         """
#         # Case 1: No IDs - create new user and conversation
#         if not user_id and not session_id:
#             new_user_id = f"user_{uuid.uuid4().hex[:16]}"
#             user = await memory.get_user(new_user_id, create_if_missing=True)
#             if not user:
#                 raise RuntimeError("Failed to create user")
#             conversation = await user.create_conversation(channel=channel)
#             return user, conversation, new_user_id, conversation.session_id

#         # Case 2: session_id only - lookup conversation
#         if session_id and not user_id:
#             conversation = await memory.get_conversation_by_session(session_id)
#             if not conversation:
#                 raise ValueError(f"Session '{session_id}' not found")
#             user = await memory.get_user(
#                 conversation.user_id, create_if_missing=False
#             )
#             if not user:
#                 raise RuntimeError(f"User for session '{session_id}' not found")
#             return user, conversation, conversation.user_id, session_id

#         # Case 3: user_id only - get/create user, create conversation
#         if user_id and not session_id:
#             user = await memory.get_user(user_id, create_if_missing=True)
#             if not user:
#                 raise RuntimeError(f"Failed to get/create user '{user_id}'")
#             conversation = await user.create_conversation(channel=channel)
#             return user, conversation, user_id, conversation.session_id

#         # Case 4: Both provided - validate and use
#         if user_id and session_id:
#             conversation = await memory.get_conversation_by_session(session_id)
#             if not conversation:
#                 raise ValueError(f"Session '{session_id}' not found")
#             if conversation.user_id != user_id:
#                 raise ValueError(
#                     f"Session '{session_id}' does not belong to user '{user_id}'"
#                 )
#             user = await memory.get_user(user_id, create_if_missing=False)
#             if not user:
#                 raise RuntimeError(f"User '{user_id}' not found")
#             return user, conversation, user_id, session_id

#         raise ValueError("Invalid user_id/session_id combination")

#     async def _get_agent(self) -> Optional[Any]:
#         """Get the agent this action belongs to."""
#         from jvagent.core.agent import Agent

#         if self.agent_id:
#             return await Agent.get(self.agent_id)
#         return None

#     async def _get_memory(self, agent: Any) -> Optional[Memory]:
#         """Get the Memory node for the agent.

#         Args:
#             agent: The Agent node to get memory for

#         Returns:
#             Memory node if found, None otherwise
#         """
#         # Use string "Memory" for node filter as nodes() expects entity name string
#         memories = await agent.nodes(node="Memory", direction="both")
#         return memories[0] if memories else None

#     async def _get_canned_response(
#         self,
#         utterance: str,
#         event_bus: InteractionEventBus,
#         interaction: Optional[Interaction] = None,
#     ) -> Optional[str]:
#         """Get a canned response for the utterance.

#         Uses the light model to classify and generate quick responses.

#         Args:
#             utterance: User's input
#             event_bus: Event bus for emitting events
#             interaction: Optional interaction to log model calls to

#         Returns:
#             Canned response if applicable, None otherwise
#         """
#         prompt = CANNED_RESPONSE_PROMPT.format(
#             agent_name=self.persona_name,
#             agent_role=self.persona_role,
#             agent_description=self.persona_description,
#             agent_capabilities=", ".join(self.persona_capabilities),
#             utterance=utterance,
#         )

#         try:
#             result = await self._model_call(
#                 [{"system": prompt}], use_light_model=True, interaction=interaction
#             )
#             if not result:
#                 return None

#             # Parse JSON response
#             if isinstance(result, str):
#                 try:
#                     result = json.loads(result)
#                 except json.JSONDecodeError:
#                     return None

#             if not result.get("canned", False):
#                 return None

#             message = result.get("message", "")
#             category = result.get("category", "")

#             if message:
#                 await event_bus.emit_canned_response(message, category)

#             # For complex requests, return the canned message but continue processing
#             if category == "complex_request":
#                 return message

#             # For simple requests/greetings, this is the final response
#             return message

#         except Exception as e:
#             await event_bus.emit_log("error", f"Canned response error: {e}")
#             return None

#     async def _get_filtered_parameters(
#         self,
#         messages: List[Dict[str, str]],
#         event_bus: InteractionEventBus,
#         interaction: Optional[Interaction] = None,
#     ) -> List[PersonaParameter]:
#         """Filter parameters using LLM.

#         Args:
#             messages: Conversation messages
#             event_bus: Event bus for emitting events
#             interaction: Optional interaction to log model calls to

#         Returns:
#             List of applicable parameters
#         """
#         manager = await self._ensure_parameter_manager()

#         all_params = await manager.list_parameters(
#             enabled_only=True
#         )
#         if not all_params:
#             logger.debug("No parameters available for filtering")
#             return []

#         logger.debug(f"Filtering {len(all_params)} parameters")

#         # Build parameter list for filtering
#         param_list = [
#             {
#                 "id": p.id,
#                 "condition": p.condition,
#                 "action": p.action,
#                 "response": p.response,
#             }
#             for p in all_params
#         ]

#         prompt = FILTER_PARAMETER_PROMPT.format(
#             agent_role=self.persona_role,
#             agent_description=self.persona_description,
#             agent_capabilities=", ".join(self.persona_capabilities),
#             parameters=json.dumps(param_list, indent=2),
#         )

#         filter_messages = list(messages)
#         filter_messages.append({"system": prompt})

#         try:
#             result = await self._model_call(
#                 filter_messages, use_light_model=True, interaction=interaction
#             )
#             if not result:
#                 logger.debug("Parameter filter model call returned no result")
#                 return []

#             # Parse JSON response
#             if isinstance(result, str):
#                 try:
#                     result = json.loads(result)
#                 except json.JSONDecodeError:
#                     logger.warning(f"Failed to parse parameter filter response: {result[:200]}")
#                     return []

#             applicable_ids = result.get("ids", [])
#             logger.debug(f"Parameter filter returned IDs: {applicable_ids}")

#             if not applicable_ids:
#                 return []

#             # Filter to applicable parameters - ensure string comparison
#             applicable_ids_str = [str(id_val) for id_val in applicable_ids]
#             filtered = [
#                 p for p in all_params if p.id in applicable_ids_str
#             ]

#             logger.debug(f"Filtered to {len(filtered)} applicable parameters")

#             await event_bus.emit_parameter_filtered(
#                 [p.to_dict() for p in filtered]
#             )

#             return filtered

#         except Exception as e:
#             await event_bus.emit_log(
#                 "error", f"Parameter filtering error: {e}"
#             )
#             return []

#     async def _run_parameter_actions(
#         self,
#         parameters: List[PersonaParameter],
#         conversation: Conversation,
#         interaction: Interaction,
#         event_bus: InteractionEventBus,
#     ) -> List[str]:
#         """Execute actions specified by parameters.

#         Args:
#             parameters: Filtered parameters
#             conversation: Current conversation
#             interaction: Current interaction
#             event_bus: Event bus for emitting events

#         Returns:
#             List of directives from action executions
#         """
#         directives: List[str] = []
#         agent = await self._get_agent()
#         if not agent:
#             return directives

#         # Track which actions we've already run
#         executed_actions: set = set()

#         for param in parameters:
#             # Record the parameter
#             interaction.add_parameter(param.to_dict())

#             if not param.action:
#                 # Just apply the parameter (no action to run)
#                 await event_bus.emit_parameter_applied(
#                     param.id, param.condition, param.response
#                 )
#                 continue

#             # Skip if we've already run this action
#             if param.action in executed_actions:
#                 continue

#             # Store utterance in context for action to access
#             conversation.data_set("visitor_utterance", interaction.utterance)

#             # Get the action
#             action = await agent.get_action(action_label=param.action)
#             if not action:
#                 await event_bus.emit_log(
#                     "warning", f"Action '{param.action}' not found"
#                 )
#                 continue

#             await event_bus.emit_action_triggered(param.action)
#             executed_actions.add(param.action)

#             try:
#                 # Call the action's execute method
#                 result = await action.execute(conversation, interaction)

#                 if isinstance(result, str):
#                     directives.append(result)
#                     await event_bus.emit_action_result(param.action, result)
#                 elif isinstance(result, list):
#                     for r in result:
#                         if isinstance(r, str):
#                             directives.append(r)
#                             await event_bus.emit_action_result(param.action, r)

#                 # Record the action in the interaction
#                 interaction.add_action(param.action)

#             except Exception as e:
#                 await event_bus.emit_error(
#                     f"Action '{param.action}' failed: {e}"
#                 )
#                 logger.error(
#                     f"Error executing action '{param.action}': {e}",
#                     exc_info=True,
#                 )

#             # If action set response directly, we may stop
#             if interaction.has_response():
#                 break

#         return directives

#     def _build_final_prompt(
#         self,
#         parameters: List[PersonaParameter],
#         directives: List[str],
#         user: User,
#         conversation: Conversation,
#     ) -> str:
#         """Build the final system prompt.

#         Args:
#             parameters: Filtered parameters
#             directives: Collected directives
#             user: Current user
#             conversation: Current conversation

#         Returns:
#             Complete system prompt
#         """
#         # Get current date/time
#         now = datetime.now()
#         date_str = now.strftime("%A, %d %B, %Y")
#         time_str = now.strftime("%I:%M %p")

#         # Build parameters section
#         if parameters:
#             params_str = "\n".join(
#                 f"{i+1}. {p.to_prompt_format()}"
#                 for i, p in enumerate(parameters)
#             )
#             parameters_prompt = (
#                 f"{PARAMETER_DIRECTIVE}\n{params_str}\n{PARAMETERS_INSTRUCTION}"
#             )
#         else:
#             parameters_prompt = NO_PARAMETERS_INSTRUCTION

#         # Build directives section
#         if directives:
#             directives_str = "\n".join(
#                 f"{i+1}. {d}" for i, d in enumerate(directives)
#             )
#             directives_prompt = f"{DIRECTIVES_INSTRUCTION}\n{directives_str}"
#         else:
#             directives_prompt = NO_DIRECTIVES_INSTRUCTION

#         # Use custom template or default
#         template = self.agent_prompt_template or AGENT_PROMPT_TEMPLATE

#         # Build final prompt
#         prompt = template.format(
#             agent_name=self.persona_name,
#             agent_role=self.persona_role,
#             agent_description=self.persona_description,
#             agent_capabilities="\n-".join(self.persona_capabilities),
#             user=user.user_id,
#             date=date_str,
#             time=time_str,
#             parameters=parameters_prompt,
#             directives=directives_prompt,
#         )

#         # Add user model from context if available
#         user_model = conversation.data_get("user_model")
#         if user_model:
#             prompt += f"\n### USER DETAILS\n{user_model}"

#         return prompt

#     async def _model_call(
#         self,
#         messages: List[Dict[str, str]],
#         use_light_model: bool = False,
#         interaction: Optional[Interaction] = None,
#     ) -> Optional[Union[str, Dict[str, Any]]]:
#         """Make a call to the model action.

#         Converts persona action message format to ModelAction.query() format:
#         - Extracts last human message as prompt
#         - Extracts system message if present
#         - Converts remaining messages to history format

#         Args:
#             messages: Messages in persona format ({"human": "...", "ai": "...", "system": "..."})
#             use_light_model: Whether to use the light model
#             interaction: Optional interaction to log the model result to

#         Returns:
#             Model response
#         """
#         agent = await self._get_agent()
#         if not agent:
#             return None

#         # Get model action by entity type
#         from jvagent.action.model.base import ModelAction

#         model_action = await agent.get_action_by_type(self.model_action_type)
#         if not model_action or not isinstance(model_action, ModelAction):
#             logger.error(
#                 f"Model action of type '{self.model_action_type}' not found for agent"
#             )
#             return None

#         try:
#             # Convert persona message format to ModelAction format
#             system_message: Optional[str] = None
#             history: List[Dict[str, Any]] = []
#             prompt: str = ""

#             # Process messages: extract system, build history, get last human as prompt
#             for msg in messages:
#                 if "system" in msg:
#                     # System message (usually the final prompt)
#                     system_message = msg["system"]
#                 elif "human" in msg:
#                     # If we already have a prompt, add previous human/ai pair to history
#                     if prompt:
#                         # Add previous prompt as user message to history
#                         history.append({"role": "user", "content": prompt})
#                     # Update prompt to current human message
#                     prompt = msg["human"]
#                 elif "ai" in msg:
#                     # Add AI response to history (assistant role)
#                     history.append({"role": "assistant", "content": msg["ai"]})

#             # If no prompt found, use empty string
#             if not prompt:
#                 logger.warning("No human message found in messages, using empty prompt")
#                 prompt = ""

#             model_name = (
#                 self.light_model_name if use_light_model else self.model_name
#             )
#             result = await model_action.query(
#                 prompt=prompt,
#                 system=system_message,
#                 history=history if history else None,
#                 model=model_name,
#                 temperature=self.model_temperature,
#                 max_tokens=self.model_max_tokens,
#             )

#             # Log the model result if interaction provided
#             if interaction and result:
#                 # Record ModelAction in the actions list
#                 interaction.add_action(self.model_action_type)
#                 interaction.add_model_result(result.to_dict())

#             return result.response if result else None
#         except Exception as e:
#             logger.error(f"Model call error: {e}", exc_info=True)
#             return None

#     async def _call_model(
#         self,
#         messages: List[Dict[str, str]],
#         interaction: Interaction,
#         event_bus: InteractionEventBus,
#         stream: bool = False,
#     ) -> Optional[str]:
#         """Call the model for the main response.

#         Args:
#             messages: Messages to send
#             interaction: Current interaction
#             event_bus: Event bus
#             stream: Whether to stream

#         Returns:
#             Response text
#         """
#         # For now, use non-streaming call
#         # Streaming support would require async generator handling
#         result = await self._model_call(messages, use_light_model=False, interaction=interaction)
#         if result:
#             await event_bus.emit_response_complete(str(result))
#         return str(result) if result else None

#     async def _finalize_interaction(
#         self,
#         interaction: Interaction,
#         event_bus: InteractionEventBus,
#         aggregator: ResponseAggregator,
#     ) -> None:
#         """Finalize the interaction.

#         Args:
#             interaction: Interaction to finalize
#             event_bus: Event bus
#             aggregator: Response aggregator
#         """
#         # Populate interaction from aggregator
#         aggregator.populate_interaction(interaction)

#         # Close and save interaction
#         interaction.close_interaction()
#         await interaction.save()

#         # Emit completion event
#         await event_bus.emit_interaction_complete({})

#     async def execute(
#         self,
#         conversation: Conversation,
#         interaction: Interaction,
#     ) -> Optional[Union[str, List[str]]]:
#         """Execute method for action delegation pattern.

#         This allows PersonaAction to be triggered by other PersonaActions
#         via the parameter action mechanism.

#         Args:
#             conversation: Current conversation
#             interaction: Current interaction

#         Returns:
#             Optional directive string(s)
#         """
#         # PersonaAction as a delegated action would typically
#         # just process and set the interaction response
#         # For now, return None to indicate no directive
#         return None

#     async def healthcheck(self) -> bool:
#         """Check if the PersonaAction is healthy.

#         Returns:
#             True if healthy, False otherwise
#         """
#         try:
#             if not self.persona_name or not self.persona_role:
#                 return False

#             # Check if model action is available
#             agent = await self._get_agent()
#             if agent and self.model_action_type:
#                 action = await agent.get_action_by_type(self.model_action_type)
#                 if not action:
#                     return False

#             return True
#         except Exception as e:
#             logger.error(f"Healthcheck failed: {e}")
#             return False

#     # Parameter management methods

#     async def get_parameters(
#         self, enabled_only: bool = True
#     ) -> List[PersonaParameter]:
#         """Get all parameters.

#         Args:
#             enabled_only: If True, only return enabled parameters

#         Returns:
#             List of parameters
#         """
#         manager = await self._ensure_parameter_manager()
#         return await manager.list_parameters(enabled_only)

#     async def add_parameter(self, param_data: Dict[str, Any]) -> str:
#         """Add a new parameter.

#         Args:
#             param_data: Parameter data dictionary

#         Returns:
#             ID of the added parameter
#         """
#         manager = await self._ensure_parameter_manager()
#         param = PersonaParameter.from_dict(param_data)
#         return await manager.add_parameter(param)

#     async def update_parameter(
#         self, param_id: str, updates: Dict[str, Any]
#     ) -> Optional[PersonaParameter]:
#         """Update a parameter.

#         Args:
#             param_id: Parameter ID
#             updates: Updates to apply

#         Returns:
#             Updated parameter or None
#         """
#         manager = await self._ensure_parameter_manager()
#         return await manager.update_parameter(param_id, updates)

#     async def delete_parameter(self, param_id: str) -> bool:
#         """Delete a parameter.

#         Args:
#             param_id: Parameter ID

#         Returns:
#             True if deleted
#         """
#         manager = await self._ensure_parameter_manager()
#         return await manager.delete_parameter(param_id)

#     async def import_parameters(self, params: List[Dict[str, Any]]) -> int:
#         """Import parameters.

#         Args:
#             params: List of parameter dictionaries

#         Returns:
#             Number imported
#         """
#         manager = await self._ensure_parameter_manager()
#         return await manager.import_parameters(params)
# stop