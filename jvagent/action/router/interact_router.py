"""InteractRouter action for intent-based routing of interactions.

InteractRouter analyzes incoming utterances using an LLM to:
1. Classify intent type (REQUEST, QUERY, ANSWER, NAVIGATION, CONTINUATION, AMBIGUOUS)
2. Route to appropriate actions based on intent and context
3. Apply intelligent routing rules that balance intent with ongoing activities
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import dspy

from jvspatial.core.annotations import attribute
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.model.dspy import DSPyLM, format_conversation_history_for_dspy
from jvagent.action.router.dspy import RouterModule
from jvagent.action.router.prompts import (
    ROUTING_PROMPT_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE,
    ROUTER_CLASSIFICATION_SIGNATURE,
)
from jvagent.memory.conversation import Conversation

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class InteractRouter(InteractAction):
    """Router action that classifies intent and routes to appropriate InteractActions.

    InteractRouter runs first (negative weight) to:
    1. Collect anchors from all InteractActions
    2. Build conversation history context
    3. Classify intent type and route via DSPy
    4. Store routing results on Interaction node

    Key Routing Principle:
        Clear user intent (REQUEST, QUERY, NAVIGATION) takes precedence over ongoing activities.
        Only ANSWER, CONTINUATION, and AMBIGUOUS intents defer to ongoing activities.

    Attributes:
        model_action_type: Type of LanguageModelAction to use
        model: Model identifier (e.g., "gpt-4o-mini")
        model_temperature: Temperature for LLM generation (default: 0.1)
        model_max_tokens: Max tokens for LLM generation (default: 500)
        history_limit: Number of previous interactions to include (default: 3)
        weight: Execution weight (default: -100 to run first)
        exceptions: List of action names that always execute
        router_classification_signature: DSPy signature docstring (customizable)
    """

    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Type of LanguageModelAction to use for LLM calls"
    )
    model: Optional[str] = attribute(
        default="gpt-4o-mini",
        description="Model identifier to use"
    )
    model_temperature: float = attribute(
        default=0.1,
        description="Temperature for LLM generation (lower for more consistent routing)"
    )
    model_max_tokens: int = attribute(
        default=500,
        description="Max tokens for LLM generation"
    )
    history_limit: int = attribute(
        default=3,
        description="Number of previous interactions to include in conversation history",
        ge=0
    )
    weight: int = attribute(
        default=-100,
        description="Execution weight (negative to run first)"
    )
    exceptions: List[str] = attribute(
        default_factory=list,
        description="List of InteractAction entity names that must always execute"
    )
    router_classification_signature: str = attribute(
        default=ROUTER_CLASSIFICATION_SIGNATURE,
        description="DSPy signature docstring for RouterClassification (customizable in agent.yaml)"
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute intent classification and routing.

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
            
            # Get dynamic exceptions (actions with always_execute=True)
            dynamic_exceptions = await self._get_dynamic_exceptions(agent)
            combined_exceptions = list(set(self.exceptions + dynamic_exceptions))

            if not anchors_dict:
                logger.warning("InteractRouter: No anchors available for routing")
                await self._store_routing_result(
                    interaction,
                    interpretation=f"User said: {interaction.utterance[:50]}",
                    actions=combined_exceptions,
                    intent_type="UNCLEAR",
                )
                await self._update_walk_path(visitor, agent, combined_exceptions)
                return

            # Get conversation history
            conversation = await Conversation.get(interaction.conversation_id)
            interaction_history = []
            if conversation:
                interaction_history = await conversation.get_interaction_history(
                    limit=self.history_limit,
                    excluded=interaction.id,
                    with_utterance=True,
                    with_response=True,
                    with_interpretation=False,
                    with_event=True,
                    formatted=True,
                )

            # Route using DSPy
            routing_data = await self._route_with_dspy(
                interaction,
                anchors_dict,
                interaction_history,
            )

            if routing_data:
                routed_actions = routing_data.get("actions", [])
                intent_type = routing_data.get("intent_type", "UNCLEAR")

                # SOCIAL intent must not route to ongoing activity: gratitude/acknowledgment need no action
                if intent_type == "SOCIAL":
                    routed_actions = []
                    logger.debug("InteractRouter: SOCIAL intent - clearing routed actions")

                all_allowed = list(set(routed_actions + combined_exceptions))
                
                await self._store_routing_result(
                    interaction,
                    interpretation=routing_data.get("interpretation", ""),
                    actions=all_allowed,
                    intent_type=intent_type,
                )
                
                logger.info(
                    f"InteractRouter: intent_type={intent_type}, "
                    f"routed to {len(routed_actions)} actions (+ {len(combined_exceptions)} exceptions)"
                )
                
                await self._update_walk_path(visitor, agent, all_allowed)
            else:
                # Routing failed - fall back to exceptions only
                await self._store_routing_result(
                    interaction,
                    interpretation=f"User said: {interaction.utterance[:50]}",
                    actions=combined_exceptions,
                    intent_type="UNCLEAR",
                )
                logger.warning("InteractRouter: Routing failed, using exceptions only")
                await self._update_walk_path(visitor, agent, combined_exceptions)

        except Exception as e:
            logger.error(f"InteractRouter: Error during routing: {e}", exc_info=True)

    async def _store_routing_result(
        self,
        interaction: "Interaction",
        interpretation: str,
        actions: List[str],
        intent_type: str,
    ) -> None:
        """Store routing results on the interaction.

        Args:
            interaction: The interaction to update
            interpretation: LLM-generated interpretation
            actions: List of action names to route to
            intent_type: Classified intent type
        """
        interaction.interpretation = interpretation
        interaction.anchors = actions
        interaction.intent_type = intent_type
        await interaction.save()

    async def _update_walk_path(
        self,
        visitor: "InteractWalker",
        agent: Any,
        allowed_actions: List[str],
    ) -> None:
        """Update the walker's walk path with allowed actions.

        Args:
            visitor: The InteractWalker
            agent: The agent instance
            allowed_actions: List of action names to allow
        """
        actions_manager = await agent.get_actions_manager()
        if not actions_manager:
            return

        all_enabled_actions = await actions_manager.get_actions(
            enabled_only=True, entity=InteractAction
        )

        # Filter to only allowed actions
        allowed_set = set(allowed_actions)
        filtered_actions = [
            action for action in all_enabled_actions
            if action.get_class_name() in allowed_set
        ]

        # Sort by weight
        filtered_actions = sorted(filtered_actions, key=lambda a: a.weight)

        curated = await visitor.curate_walk_path(filtered_actions)
        logger.info(f"InteractRouter: Updated walk path with {len(curated)} actions")

    async def _get_dynamic_exceptions(self, agent: Any) -> List[str]:
        """Get list of InteractAction entity names that have always_execute=True.

        Args:
            agent: Agent instance

        Returns:
            List of entity names that should always execute
        """
        actions_manager = await agent.get_actions_manager()
        if not actions_manager:
            return []

        all_interact_actions = await actions_manager.get_all_actions(
            enabled_only=True, entity=InteractAction
        )
        return [
            a.get_class_name()
            for a in all_interact_actions
            if getattr(a, "always_execute", False)
        ]

    async def _route_with_dspy(
        self,
        interaction: "Interaction",
        anchors_dict: Dict[str, List[str]],
        interaction_history: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Route using DSPy module.

        Args:
            interaction: The current interaction
            anchors_dict: Dictionary of action names to anchor lists
            interaction_history: Formatted conversation history

        Returns:
            Dictionary with: interpretation, actions, intent_type, confidence
            Returns None on error
        """
        try:
            model_action = await self.get_model_action(required=True)
            if not model_action:
                logger.warning("InteractRouter: Could not get model action")
                return None

            # Format inputs
            formatted_history = format_conversation_history_for_dspy(interaction_history)
            anchors_json = json.dumps(anchors_dict, indent=2)

            # Create DSPy LM adapter
            lm = DSPyLM(
                model_action=model_action,
                model_type="chat",
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                cache=False,
            )

            with dspy.context(lm=lm):
                router_module = RouterModule(action_instance=self)

                router_kwargs = {
                    "user_utterance": interaction.utterance or "",
                    "available_actions": anchors_json,
                }
                if formatted_history:
                    router_kwargs["conversation_history"] = formatted_history

                routing_result = await router_module.aforward(**router_kwargs)

                return {
                    "interpretation": routing_result.get("interpretation", ""),
                    "actions": routing_result.get("actions", []),
                    "intent_type": routing_result.get("intent_type", "UNCLEAR"),
                    "confidence": routing_result.get("confidence", 1.0),
                }

        except Exception as e:
            logger.error(f"InteractRouter: DSPy routing failed: {e}", exc_info=True)
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

        all_interact_actions = await actions_manager.get_actions(
            enabled_only=True, entity=InteractAction
        )
        # Exclude this router
        interact_actions = [a for a in all_interact_actions if a.id != self.id]

        logger.debug(f"InteractRouter: Found {len(interact_actions)} InteractActions")

        anchors_dict: Dict[str, List[str]] = {}
        for action in interact_actions:
            entity_name = action.get_class_name()

            # Skip actions that always execute or are in exceptions
            if getattr(action, "always_execute", False) or entity_name in self.exceptions:
                logger.debug(f"InteractRouter: Skipping {entity_name} (always_execute or exception)")
                continue

            # Get anchors
            anchors = getattr(action, 'anchors', None)
            description = getattr(action, 'description', None)
            
            if anchors is None:
                context = getattr(action, 'context', {}) if hasattr(action, 'context') else {}
                if isinstance(context, dict):
                    anchors = context.get('anchors', [])
                else:
                    anchors = []

            if anchors and isinstance(anchors, list) and (description or len(anchors) > 0):
                if entity_name not in anchors_dict:
                    anchors_dict[entity_name] = []
                if description:
                    anchors_dict[entity_name].append(description)
                anchors_dict[entity_name].extend(anchors)
                logger.debug(f"InteractRouter: Added {len(anchors)} anchors for {entity_name}")

        logger.debug(f"InteractRouter: Collected anchors from {len(anchors_dict)} actions")
        return anchors_dict
