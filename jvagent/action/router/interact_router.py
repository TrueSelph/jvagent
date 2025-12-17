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
from jvagent.action.router.prompts import (
    ROUTING_PROMPT_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE,
)

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
        routing_prompt: Optional template for the routing prompt with placeholders: {utterance}, {anchors_json}
            (Note: Conversation history is passed separately to the LLM via history parameter)
    """

    model_action_type: str = attribute(
        default="",
        description="Type of LanguageModelAction to use for LLM calls (e.g., 'OpenAILanguageModelAction'). If empty, uses first available."
    )
    history_limit: int = attribute(
        default=5,
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
        default=SYSTEM_PROMPT_TEMPLATE,
        description="Optional override for the system prompt used during routing. If not provided, uses default system prompt."
    )
    routing_prompt: Optional[str] = attribute(
        default=ROUTING_PROMPT_TEMPLATE,
        description=(
            "Optional template for the routing prompt. "
            "Placeholders: {utterance}, {anchors_json}. "
            "Note: Conversation history is passed separately to the LLM via history parameter. "
            "If not provided, uses default routing prompt template."
        ),
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

            model_action = await self.get_model_action()
            if not model_action:
                logger.error("InteractRouter: Model action not found")
                return

            # Collect anchors from all InteractActions
            anchors_dict = await self._collect_anchors(agent)
            if not anchors_dict:
                logger.warning("InteractRouter: No anchors available for routing")
                # Store empty routing result
                interaction.interpretation = f"User said: {interaction.utterance[:50]}"
                # Even with no anchors, always include configured and dynamic exceptions
                from jvagent.action.interact.base import InteractAction

                actions_manager = await agent.get_actions_manager()
                dynamic_exceptions: List[str] = []
                if actions_manager:
                    all_interact_actions = await actions_manager.get_all_actions(
                        enabled_only=True, entity=InteractAction
                    )
                    dynamic_exceptions = [
                        a.get_class_name()
                        for a in all_interact_actions
                        if getattr(a, "always_execute", False)
                    ]

                combined_exceptions = list(set(self.exceptions + dynamic_exceptions))
                interaction.anchors = combined_exceptions
                await interaction.save()
                return

            # Get conversation history (formatted as role/content pairs)
            from jvagent.memory.conversation import Conversation

            conversation = await Conversation.get(interaction.conversation_id)
            interaction_history = []
            if conversation:
                interaction_history = await conversation.get_interaction_history(
                    limit=self.history_limit,
                    with_utterance=False,
                    with_response=False,
                    with_interpretation=True,
                    with_event=True,
                    formatted=True,
                )

                logger.debug(f"InteractRouter: Conversation history: {interaction_history}")

            # Build routing prompt (history will be passed separately to LLM)
            prompt = self._build_routing_prompt(
                interaction.utterance,
                anchors_dict
            )


            response_text = await model_action.generate(
                prompt=prompt,
                stream=False,
                system=self.system_prompt,
                history=interaction_history,  # Pass formatted history directly to LLM
                calling_action_name=self.get_class_name(),
                temperature=0.1,  # Lower temperature for more consistent routing
                max_tokens=500,
            )

            # Parse response
            routing_data = self._parse_routing_response(response_text)

            # Store on interaction
            # Always set interpretation to indicate InteractRouter has executed
            if routing_data:
                interaction.interpretation = routing_data.get("interpretation", "")
                # Combine routed actions with exceptions (exceptions always included)
                routed_actions = routing_data.get("actions", [])
                # Compute dynamic exceptions from InteractActions that always_execute
                from jvagent.action.interact.base import InteractAction

                actions_manager = await agent.get_actions_manager()
                dynamic_exceptions: List[str] = []
                if actions_manager:
                    all_interact_actions = await actions_manager.get_all_actions(
                        enabled_only=True, entity=InteractAction
                    )
                    dynamic_exceptions = [
                        a.get_class_name()
                        for a in all_interact_actions
                        if getattr(a, "always_execute", False)
                    ]

                combined_exceptions = list(set(self.exceptions + dynamic_exceptions))
                all_allowed = list(set(routed_actions + combined_exceptions))
                interaction.anchors = all_allowed

                # Create event entry for successful routing
                if all_allowed:
                    anchors_str = ", ".join(all_allowed)
                    event_message = f"System routed to: {anchors_str}"
                    await visitor.add_event(event_message)

                await interaction.save()

                logger.info(
                    f"InteractRouter: Routed to {len(routed_actions)} actions "
                    f"(+ {len(combined_exceptions)} exceptions, total: {len(all_allowed)})"
                )
            else:
                # Even if parsing fails, set interpretation to indicate router executed
                # This ensures the walker knows routing was attempted
                interaction.interpretation = f"User said: {interaction.utterance[:50]}"
                # Compute dynamic exceptions even if parsing fails
                from jvagent.action.interact.base import InteractAction

                actions_manager = await agent.get_actions_manager()
                dynamic_exceptions: List[str] = []
                if actions_manager:
                    all_interact_actions = await actions_manager.get_all_actions(
                        enabled_only=True, entity=InteractAction
                    )
                    dynamic_exceptions = [
                        a.get_class_name()
                        for a in all_interact_actions
                        if getattr(a, "always_execute", False)
                    ]

                combined_exceptions = list(set(self.exceptions + dynamic_exceptions))
                interaction.anchors = combined_exceptions  # Only exceptions if no routing data
                await interaction.save()

                logger.warning(
                    f"InteractRouter: Failed to parse routing response, "
                    f"only exceptions will execute: {combined_exceptions}"
                )

        except Exception as e:
            logger.error(f"InteractRouter: Error during routing: {e}", exc_info=True)
            # Don't raise - let other actions continue


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
            description = getattr(action, 'description', None)
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
            if anchors and isinstance(anchors, list) and (description or len(anchors) > 0):
                # Automatically ascribe the entity name to the anchor statements
                if entity_name not in anchors_dict:
                    anchors_dict[entity_name] = []
                anchors_dict[entity_name].append(description)
                anchors_dict[entity_name].extend(anchors)
                logger.debug(f"InteractRouter: Added {len(anchors)} anchors for {entity_name}")

        logger.debug(f"InteractRouter: Collected anchors from {len(anchors_dict)} actions: {list(anchors_dict.keys())}")
        return anchors_dict

    def _build_routing_prompt(
        self,
        utterance: str,
        anchors_dict: Dict[str, List[str]]
    ) -> str:
        """Build the LLM prompt for routing analysis.

        Note: Conversation history (past interpretations and events) is passed separately
        to the LLM via the history parameter, so it is not included in this prompt.

        Args:
            utterance: Current user utterance
            anchors_dict: Available anchors dictionary

        Returns:
            Formatted prompt string
        """
        # Build anchors text
        anchors_text = json.dumps(anchors_dict, indent=2)

        # Use routing prompt template (defaults to ROUTING_PROMPT_TEMPLATE, can be overridden)
        # Fall back to ROUTING_PROMPT_TEMPLATE if routing_prompt is None
        prompt_template = self.routing_prompt if self.routing_prompt is not None else ROUTING_PROMPT_TEMPLATE
        return prompt_template.format(
            utterance=utterance,
            anchors_json=anchors_text,
        )

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
            actions = data.get("actions", [])

            # Validate types
            if not isinstance(interpretation, str):
                return None
            if not isinstance(actions, list):
                return None

            return {
                "interpretation": interpretation.strip(),
                "actions": [str(a) for a in actions if a],  # Filter empty strings
            }

        except json.JSONDecodeError as e:
            logger.warning(f"InteractRouter: Failed to parse JSON response: {e}")
            logger.debug(f"InteractRouter: Response text: {response_text[:200]}")
            return None
