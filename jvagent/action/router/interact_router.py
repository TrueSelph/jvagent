"""InteractRouter action for intent-based routing of interactions.

InteractRouter analyzes incoming utterances using an LLM to generate interpretations
and match against published anchors from other InteractActions to determine routing.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvspatial.core import on_visit

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.model.language.base import LanguageModelAction

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class InteractRouter(InteractAction):
    """Router action that analyzes utterances and routes to appropriate InteractActions.

    InteractRouter runs first (negative weight) to:
    1. Collect anchors from all InteractActions
    2. Build conversation history context
    3. Call LLM to generate interpretation and match anchors
    4. Store routing results on Interaction node

    Attributes:
        model_action_type: Type of LanguageModelAction to use (e.g., "OpenAILanguageModelAction")
        history_limit: Number of previous interactions to include in context (default: 10)
        weight: Execution weight (default: -100 to run first)
        exceptions: List of InteractAction entity names that must always execute, regardless of routing
        system_prompt: Optional override for the system prompt used during routing
        routing_prompt: Optional template for the routing prompt with placeholders: {utterance}, {conversation_history}, {anchors_json}
    """

    model_action_type: str = attribute(
        default="",
        description="Type of LanguageModelAction to use for LLM calls (e.g., 'OpenAILanguageModelAction'). If empty, uses first available."
    )
    history_limit: int = attribute(
        default=10,
        description="Number of previous interactions to include in conversation history",
        ge=0
    )
    weight: int = attribute(
        default=-100,
        description="Execution weight (negative to run first)"
    )
    exceptions: List[str] = attribute(
        default_factory=list,
        description="List of InteractAction entity names that must always execute, regardless of routing results"
    )
    system_prompt: Optional[str] = attribute(
        default=None,
        description="Optional override for the system prompt used during routing. If not provided, uses default system prompt."
    )
    routing_prompt: Optional[str] = attribute(
        default=None,
        description=(
            "Optional template for the routing prompt. "
            "Placeholders: {utterance}, {conversation_history}, {anchors_json}. "
            "If not provided, uses default routing prompt template."
        )
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute routing analysis on the interaction.

        The @on_visit decorator is required on child class implementations since
        the decorator on the abstract base class method doesn't apply to overridden methods.

        Args:
            visitor: The InteractWalker visiting this action
        """

        logger.debug(f"InteractRouter: Executing with visitor: {visitor}")

        interaction = visitor.interaction
        if not interaction:
            logger.warning("InteractRouter: No interaction available")
            return

        # Skip if already routed
        if interaction.interpretation:
            logger.debug("InteractRouter: Interaction already routed, skipping")
            return

        try:
            # Get agent and model action
            agent = await self.get_agent()
            if not agent:
                logger.error("InteractRouter: Agent not found")
                return

            model_action = await self._get_model_action(agent)
            if not model_action:
                logger.error("InteractRouter: Model action not found")
                return

            # Collect anchors from all InteractActions
            anchors_dict = await self._collect_anchors(agent)
            if not anchors_dict:
                logger.warning("InteractRouter: No anchors available for routing")
                # Store empty routing result
                interaction.interpretation = f"User said: {interaction.utterance[:50]}"
                interaction.anchors = []
                interaction.routing_confidence = 0.0
                await interaction.save()
                return

            # Build conversation history context
            conversation_context = await self._build_conversation_context(interaction)

            # Build routing prompt
            prompt = self._build_routing_prompt(
                interaction.utterance,
                conversation_context,
                anchors_dict
            )

            # Call LLM
            result = await model_action.query(
                prompt=prompt,
                system=self._get_system_prompt(),
                temperature=0.3,  # Lower temperature for more consistent routing
                max_tokens=500,
            )

            # Log model result
            if result:
                interaction.add_action(self.get_class_name())
                interaction.add_model_result(result.to_dict())

            # Parse response
            response_text = await result.get_response() if result else ""
            routing_data = self._parse_routing_response(response_text)

            # Store on interaction
            # Always set interpretation to indicate InteractRouter has executed
            if routing_data:
                interaction.interpretation = routing_data.get("interpretation", "")
                # Combine routed entities with exceptions (exceptions always included)
                routed_entities = routing_data.get("entities", [])
                all_allowed = list(set(routed_entities + self.exceptions))
                interaction.anchors = all_allowed
                interaction.routing_confidence = routing_data.get("confidence")
            else:
                # Even if parsing fails, set interpretation to indicate router executed
                # This ensures the walker knows routing was attempted
                interaction.interpretation = f"User said: {interaction.utterance[:50]}"
                interaction.anchors = self.exceptions.copy()  # Only exceptions if no routing data
                interaction.routing_confidence = 0.0

            await interaction.save()

            if routing_data:
                logger.info(
                    f"InteractRouter: Routed to {len(routed_entities)} entities "
                    f"(+ {len(self.exceptions)} exceptions, total: {len(all_allowed)}) "
                    f"(confidence: {interaction.routing_confidence})"
                )
            else:
                logger.warning(
                    f"InteractRouter: Failed to parse routing response, "
                    f"only exceptions will execute: {self.exceptions}"
                )

        except Exception as e:
            logger.error(f"InteractRouter: Error during routing: {e}", exc_info=True)
            # Don't raise - let other actions continue


    async def _get_model_action(self, agent: Any) -> Optional[LanguageModelAction]:
        """Get the model action for LLM calls.

        Args:
            agent: Agent instance

        Returns:
            LanguageModelAction instance or None
        """
        if self.model_action_type:
            model_action = await agent.get_action_by_type(self.model_action_type)
            if model_action and isinstance(model_action, LanguageModelAction):
                return model_action

        # Fallback: find first available LanguageModelAction
        from jvagent.action.model.language.base import LanguageModelAction as LanguageModelActionBase
        actions_manager = await agent.get_actions_manager()
        if actions_manager:
            all_actions = await actions_manager.get_actions(enabled_only=True)
            for action in all_actions:
                if isinstance(action, LanguageModelActionBase):
                    return action

        return None

    async def _collect_anchors(self, agent: Any) -> Dict[str, List[str]]:
        """Collect anchors from all InteractActions.

        Args:
            agent: Agent instance

        Returns:
            Dictionary mapping entity names to anchor statement lists
        """
        from jvagent.action.interact.base import InteractAction

        actions_manager = await agent.get_actions_manager()
        if not actions_manager:
            return {}

        # Get all InteractActions (excluding this router)
        # Use entity parameter to filter directly to InteractAction types
        all_interact_actions = await actions_manager.get_actions(
            enabled_only=True, entity=InteractAction
        )
        # Filter out this router
        interact_actions = [a for a in all_interact_actions if a.id != self.id]

        logger.debug(f"InteractRouter: Found {len(interact_actions)} InteractActions (excluding router)")

        # Collect anchors
        anchors_dict: Dict[str, List[str]] = {}
        for action in interact_actions:
            entity_name = action.get_class_name()

            # Get anchors - check both attribute and context directly
            anchors = getattr(action, 'anchors', None)
            if anchors is None:
                # Try to get from context if attribute not set
                context = getattr(action, 'context', {}) if hasattr(action, 'context') else {}
                if isinstance(context, dict):
                    anchors = context.get('anchors', [])
                else:
                    anchors = []

            logger.debug(
                f"InteractRouter: Checking {entity_name} (id: {action.id}, enabled: {action.enabled}) "
                f"for anchors: {anchors} (type: {type(anchors)})"
            )

            # Ensure anchors is a list and not empty
            if anchors and isinstance(anchors, list) and len(anchors) > 0:
                # Automatically ascribe the entity name to the anchor statements
                if entity_name not in anchors_dict:
                    anchors_dict[entity_name] = []
                anchors_dict[entity_name].extend(anchors)
                logger.debug(f"InteractRouter: Added {len(anchors)} anchors for {entity_name}")

        logger.debug(f"InteractRouter: Collected anchors from {len(anchors_dict)} actions: {list(anchors_dict.keys())}")
        return anchors_dict

    async def _build_conversation_context(
        self, interaction: Any
    ) -> List[Dict[str, Any]]:
        """Build conversation history context from previous interactions.

        Args:
            interaction: Current interaction

        Returns:
            List of conversation history entries
        """
        from jvagent.memory.conversation import Conversation

        # Get conversation
        conversation = await Conversation.get(interaction.conversation_id)
        if not conversation:
            return []

        # Get previous interactions (excluding current)
        # Get more than needed to ensure we have enough after filtering
        all_recent = await conversation.get_interactions(
            limit=self.history_limit,  # Get extra to account for current interaction
            reverse=True
        )

        # Filter out current interaction
        history = [
            i for i in all_recent
            if i.id != interaction.id
        ]

        # Limit to requested number and reverse to chronological order (oldest first)
        history = history[:self.history_limit]
        history.reverse()

        # Build context entries
        context: List[Dict[str, Any]] = []
        for prev_interaction in history:
            entry: Dict[str, Any] = {
                "utterance": prev_interaction.utterance,
            }
            if prev_interaction.response:
                entry["response"] = prev_interaction.response
            if prev_interaction.events:
                entry["events"] = prev_interaction.events
            if prev_interaction.interpretation:
                entry["interpretation"] = prev_interaction.interpretation
            context.append(entry)

        return context

    def _build_routing_prompt(
        self,
        utterance: str,
        conversation_context: List[Dict[str, Any]],
        anchors_dict: Dict[str, List[str]]
    ) -> str:
        """Build the LLM prompt for routing analysis.

        Args:
            utterance: Current user utterance
            conversation_context: Conversation history
            anchors_dict: Available anchors dictionary

        Returns:
            Formatted prompt string
        """
        # Build conversation history text
        history_text = ""
        if conversation_context:
            history_text = "\n\n## Previous Intents:\n"
            for i, entry in enumerate(conversation_context, 1):
                history_text += f"\n### Turn {i}:\n"
                # history_text += f"User: {entry.get('utterance', '')}\n"
                # if entry.get("response"):
                #     history_text += f"Assistant: {entry.get('response')}\n"
                # if entry.get("events"):
                #     history_text += f"Events: {', '.join(entry.get('events', []))}\n"
                if entry.get("interpretation"):
                    history_text += f"Previous Intent: {entry.get('interpretation')}\n"

        # Build anchors text
        anchors_text = json.dumps(anchors_dict, indent=2)

        # Use custom routing prompt template if provided
        if self.routing_prompt:
            return self.routing_prompt.format(
                utterance=utterance,
                previous_intents=history_text,
                anchors_json=anchors_text,
            )

        # Default routing prompt template
        prompt = f"""## Current Utterance:
        {utterance}

        {history_text}

        ## Available Anchors:
        The following anchors represent capabilities of different InteractActions. Each entity name maps to a list of anchor statements that describe when that action should be used.

        {anchors_text}

        ## Task:
        1. Generate a concise interpretation (under 50 words) that summarizes the user's intent with applicable contextual references.
          Note if the user is responding to question or any ongoing events
          This interpretation should mention if the user is requesting information, providing information or doing both.
          Example: "User has requested an update on the report bearing reference number 12345"

        2. Match the interpretation against the anchor statements to identify which entity names (InteractActions) should handle this request.

        3. Return your analysis in JSON format:
        {{
            "interpretation": "Your concise interpretation here",
            "entities": ["entity_name1", "entity_name2"],
            "confidence": 0.85
        }}

        Return ONLY valid JSON, no additional text."""

        return prompt

    def _get_system_prompt(self) -> str:
        """Get the system prompt for routing.

        Returns:
            System prompt string
        """
        # Use custom system prompt if provided
        if self.system_prompt:
            return self.system_prompt

        # Default system prompt
        return """You are an intent analysis system that interprets user utterances and routes them to appropriate InteractActions based on published anchor statements.

Your role is to:
1. Understand the user's intent from their utterance and conversation context
2. Generate a concise interpretation (under 50 words) that captures the intent
3. Match the interpretation against available anchor statements
4. Return the matching entity names (InteractAction identifiers) in JSON format

Be precise and only match when there's clear alignment between the user's intent and an anchor statement."""

    def _parse_routing_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """Parse the LLM routing response.

        Args:
            response_text: Raw response text from LLM

        Returns:
            Parsed routing data or None if invalid
        """
        if not response_text:
            return None

        # Try to extract JSON from response (may have markdown code blocks)
        json_text = response_text.strip()

        # Remove markdown code blocks if present
        if json_text.startswith("```"):
            lines = json_text.split("\n")
            # Remove first line (```json or ```)
            lines = lines[1:]
            # Remove last line (```)
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            json_text = "\n".join(lines)

        try:
            data = json.loads(json_text)

            # Validate structure
            if not isinstance(data, dict):
                return None

            interpretation = data.get("interpretation", "")
            entities = data.get("entities", [])
            confidence = data.get("confidence", 0.0)

            # Validate types
            if not isinstance(interpretation, str):
                return None
            if not isinstance(entities, list):
                return None
            if not isinstance(confidence, (int, float)):
                return None

            # Clamp confidence to 0.0-1.0
            confidence = max(0.0, min(1.0, float(confidence)))

            return {
                "interpretation": interpretation.strip(),
                "entities": [str(e) for e in entities if e],  # Filter empty strings
                "confidence": confidence,
            }

        except json.JSONDecodeError as e:
            logger.warning(f"InteractRouter: Failed to parse JSON response: {e}")
            logger.debug(f"InteractRouter: Response text: {response_text[:200]}")
            return None
