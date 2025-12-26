"""Example Action Implementation

This is a boilerplate action that demonstrates the structure and lifecycle
of a custom action in jvagent.

All configuration is done via typed Pydantic fields, not a config dictionary.
"""

import logging
import json
from typing import Any, Dict, List, Optional
from jvagent.action.interact.base import InteractAction
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute


from .data_node import DataNode

logger = logging.getLogger(__name__)


class GatherInfoInteractAction(InteractAction):
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
    use_history: bool = attribute(
        default=True, description="Use conversation history for LLM generation"
    )
    max_statement_length: int = attribute(
        default=100, description="Max length of statement to include in history"
    )
    history_limit: int = attribute(
        default=5, description="Max number of statements to include in history"
    )

    # Configuration properties (type-safe, validated)
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    directive_template: Optional[str] = attribute(
        default=None,
        description="Optional template for formatting the directive. Uses default structured format if not provided. Placeholder: {results}",
    )
    always_execute: bool = attribute(
        default=True,
        description="Always execute regardless of routing (first-time user intro handler).",
    )
    state_index: List[Dict[str, Any]] = attribute(
        default=[],
        description="State index passed from parent action",
    )

    # TODO try to use the revision prompt instead to avoid extracting the same information multiple times
    extraction_prompt: str = attribute(
        default="""
        Review the user's message and the conversation history to accurately extract the following entities.
        Be strict on the constraints specified for each entity. Return a JSON object with keys exactly as listed below.
        Include only keys for which you could extract a valid value adhering to all constraints.

        Entities to extract:
        {entities}

        Return ONLY the JSON object with the extracted entities, no delimiters. Do not include any other text or explanation.
        The JSON must have the following structure (only include keys with valid values):
        {sample_json}
        """,
        description="Prompt template for extraction",
    )

    async def on_register(self) -> None:
        """Called when action is registered."""
        logger.debug(f"GatherInfoInteractAction registered")
        nodes = []
        for state in self.state_index:
            node = await DataNode.create(agent_id=self.agent_id, state=state, label=state.get("name", ""))
            nodes.append(node)

            if len(nodes) == 1:
                await self.connect(node)
            else:
                await nodes[-2].connect(node)
            logger.debug(f"GatherInfoInteractAction: Connected nodes: {nodes}")

    async def on_reload(self) -> None:
        """Called when action is reloaded."""
        logger.debug("GatherInfoInteractAction reloaded")
        await self.on_register()

    # ============================================================================
    # Custom Action Methods
    # ============================================================================

    def generate_extraction_prompt(self, question_index: List[Dict[str, Any]]) -> str:
        """Accepts the question index schema and prepares an extraction prompt.

        Args:
            question_index: List of dictionaries defining the schema and constraints.
        """
        entities_list = []
        sample_json_lines = []

        for item in question_index:
            key = item.get('name')
            constraints = item.get('constraints', {})

            if not key or not constraints:
                continue

            desc = constraints.get('description', '')
            other_constraints = {k: v for k, v in constraints.items() if k != 'description'}
            constraint_strs = [f"{k}: {v}" for k, v in other_constraints.items()]
            constraint_part = f" ({', '.join(constraint_strs)})" if constraint_strs else ""
            entities_list.append(f"- {key}: {desc}{constraint_part}")
            sample_json_lines.append(f"  '{key}': '<extracted value>'")

        entities = "\n".join(entities_list)
        sample_json = '{\n' + ',\n'.join(sample_json_lines) + '\n}'

        # Prepare the prompt
        prompt = self.extraction_prompt.format(entities=entities, sample_json=sample_json)

        return prompt

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute the action with input data.

        Args:
            visitor: The InteractWalker visiting this action
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("GatherInfoInteractAction: No interaction available")
            return

        # Generate extraction prompt based on state_index
        prompt = self.generate_extraction_prompt(self.state_index)

        response = await self.call_llm(prompt, visitor, use_history=self.use_history, json_only=True)
        if isinstance(response, str):
            response = self.extract_json(response)

        if response:
            await self.update_responses(response, interaction)

        directive = await self.get_directive(interaction)
        logger.debug(f"Directive: {directive}")

        if directive:
            directives = [directive]
            # TODO: Get the parent action name and description and use it in events and the key for conversation data
            await visitor.add_event(f"gathering information for signing up for jvagent")
            # Generate response via PersonaAction with directive
            await self.respond(
                visitor,
                directives=directives,
                parameters=self.parameters if self.parameters else None
            )

    def extract_json(self, response: str) -> Dict[str, Any]:
        """Extract entities from the response."""
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.warning("InterviewInteractAction: Failed to extract entities from response")
            return {}

    async def get_directive(self, interaction: Interaction) -> str:
        """Get directive from walking through DataNodes to find unanswered questions."""
        conversation = await interaction.get_conversation()
        from .gather_info_walker import GatherInfoWalker
        walker = GatherInfoWalker(
            conversation=conversation,
        )
        logger.debug("GatherInfoWalker created")

        await walker.spawn(self)

        # Return empty string if no directive was found
        return walker.directive if walker.directive else ""

    async def update_responses(self, response:dict, interaction:Interaction) -> None:
        conversation = await interaction.get_conversation()
        stored_responses = conversation.data_get(key=f"interview_session")

        if type(stored_responses) is not dict or not stored_responses:
            stored_responses = {}

        # Merge new responses into stored_responses
        for (key, value) in response.items():
            stored_responses[key] = value

        logger.debug(f"Updated responses: {stored_responses}")

        conversation.data_set(key=f"interview_session", value=stored_responses)

    async def call_llm(self, prompt: str, visitor: "InteractWalker", use_history: bool = True, json_only: bool = True) -> str:
        """Call the LLM with the given prompt."""
        model_action = await self.get_model_action(required=True)
        if use_history:
            conversation_history = await self._get_conversation_history(
                visitor.interaction,
                self.history_limit,
                with_utterance=True,
                with_response=False,
                with_interpretation=False,
                with_event=True,
                max_statement_length=self.max_statement_length,
            )
        try:
            response = await model_action.generate(
                prompt=visitor.utterance,
                stream=False,
                system=prompt,
                history=conversation_history,
                calling_action_name=self.get_class_name(),
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_format={"type": "json_object"} if json_only else None,
            )
        except Exception as e:
            logger.error(f"InterviewInteractAction: Failed to extract entities: {e}", exc_info=True)
            raise
        return response

    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = False,
        with_interpretation: bool = False,
        with_event: bool = True,
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
