"""InteractRouter action for intent-based routing of interactions.

InteractRouter analyzes incoming utterances using an LLM to generate interpretations
and match against published anchors from other InteractActions to determine routing.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import dspy

from jvspatial.core.annotations import attribute

from jvspatial.core import on_visit

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
    """Router action that analyzes utterances and routes to appropriate InteractActions.

    InteractRouter runs first (negative weight) to:
    1. Collect anchors from all InteractActions
    2. Build conversation history context
    3. Call LLM to generate interpretation and match anchors (via DSPy or legacy)
    4. Store routing results on Interaction node

    Attributes:
        model_action_type: Type of LanguageModelAction to use (e.g., "OpenAILanguageModelAction")
        model: Optional model identifier to override model_action.model
        model_temperature: Temperature for LLM generation (default: 0.1 for consistent routing)
        model_max_tokens: Max tokens for LLM generation (default: 500)
        use_dspy: Whether to use DSPy for routing (default: True, enables optimization)
        history_limit: Number of previous interactions to include in context (default: 3)
        weight: Execution weight (default: -100 to run first)
        exceptions: List of InteractAction entity names that must always execute, regardless of routing
        system_prompt: Optional override for the system prompt used during routing (legacy path only)
        routing_prompt: Optional template for the routing prompt with placeholders: {utterance}, {anchors_json}
            (Note: Conversation history is passed separately to the LLM via history parameter, legacy path only)
    """

    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Type of LanguageModelAction to use for LLM calls (e.g., 'OpenAILanguageModelAction'). If empty, uses first available."
    )
    model: Optional[str] = attribute(
        default="gpt-4o-mini",
        description="Model identifier to use (overrides model_action.model if provided). Used for both DSPy and legacy paths."
    )
    model_temperature: float = attribute(
        default=0.1,
        description="Temperature for LLM generation (lower for more consistent routing). Used for both DSPy and legacy paths."
    )
    model_max_tokens: int = attribute(
        default=500,
        description="Max tokens for LLM generation. Used for both DSPy and legacy paths."
    )
    use_dspy: bool = attribute(
        default=True,
        description="Whether to use DSPy for routing (enables optimization). Falls back to legacy if False."
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
    enable_dspy_cache: bool = attribute(
        default=True,
        description="Whether to enable DSPy caching for improved latency (default: True). Caching reduces LLM API calls for identical requests."
    )
    cache_disk_enabled: Optional[bool] = attribute(
        default=None,
        description="Whether to enable disk cache (default: None, uses DSPy default). Set to False to disable disk cache, True to enable, or None to use DSPy's default."
    )
    cache_memory_enabled: Optional[bool] = attribute(
        default=None,
        description="Whether to enable memory cache (default: None, uses DSPy default). Set to False to disable memory cache, True to enable, or None to use DSPy's default."
    )
    
    # DSPy signature docstring (single source of truth, can be overridden in agent.yaml for runtime customization)
    router_classification_signature: str = attribute(
        default=ROUTER_CLASSIFICATION_SIGNATURE,
        description="DSPy signature docstring for RouterClassification. Can be overridden in agent.yaml for runtime customization. Defaults to ROUTER_CLASSIFICATION_SIGNATURE from prompts.py",
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
                dynamic_exceptions = await self._get_dynamic_exceptions(agent)
                combined_exceptions = list(set(self.exceptions + dynamic_exceptions))
                interaction.anchors = combined_exceptions
                await interaction.save()

                # Update walker's walk path with exception actions only
                actions_manager = await agent.get_actions_manager()
                if actions_manager:
                    all_enabled_actions = await actions_manager.get_actions(
                        enabled_only=True, entity=InteractAction
                    )
                    
                    # Filter to only exception actions (by entity name)
                    exception_entity_names = set(combined_exceptions)
                    exception_actions = [
                        action for action in all_enabled_actions
                        if action.get_class_name() in exception_entity_names
                    ]
                    
                    # Sort by weight to maintain execution order
                    exception_actions = sorted(exception_actions, key=lambda a: a.weight)
                    
                    # Update walker's walk path
                    curated = await visitor.curate_walk_path(exception_actions)
                    
                    logger.info(
                        f"InteractRouter: Updated walk path with {len(curated)} exception actions (no anchors available)"
                    )
                return

            # Get conversation history (formatted as role/content pairs)
            # Include user utterances, AI responses, interpretations, and events for richer context
            conversation = await Conversation.get(interaction.conversation_id)
            interaction_history = []
            if conversation:
                interaction_history = await conversation.get_interaction_history(
                    limit=self.history_limit,  # Respects the configured/default history_limit
                    excluded=interaction.id,  # Exclude current interaction
                    with_utterance=True,  # Include user utterances for context
                    with_response=True,  # Include AI responses for context
                    with_interpretation=False,  # Omit interpretations for routing context
                    with_event=True,  # Include events for system context
                    formatted=True,
                )

            # Route using DSPy or legacy path
            if self.use_dspy:
                routing_data = await self._route_with_dspy(
                    interaction,
                    anchors_dict,
                    interaction_history,
                )
                
                # If DSPy routing fails, fall back to legacy
                if routing_data is None:
                    logger.warning("InteractRouter: DSPy routing failed, falling back to legacy")
                    routing_data = await self._route_legacy(
                        interaction,
                        anchors_dict,
                        interaction_history,
                        model_action,
                    )
            else:
                # Use legacy routing path
                routing_data = await self._route_legacy(
                    interaction,
                    anchors_dict,
                    interaction_history,
                    model_action,
                )

            # Store on interaction
            # Always set interpretation to indicate InteractRouter has executed
            if routing_data:
                interaction.interpretation = routing_data.get("interpretation", "")
                # Combine routed actions with exceptions (exceptions always included)
                routed_actions = routing_data.get("actions", [])
                # Compute dynamic exceptions from InteractActions that always_execute
                dynamic_exceptions = await self._get_dynamic_exceptions(agent)
                combined_exceptions = list(set(self.exceptions + dynamic_exceptions))
                all_allowed = list(set(routed_actions + combined_exceptions))
                interaction.anchors = all_allowed

                # Create event entry for successful routing
                # if all_allowed:
                #     if routed_actions and combined_exceptions:
                #         routes_str = ", ".join(routed_actions)
                #         exceptions_str = ", ".join(combined_exceptions)
                #         event_message = f"Routed to {routes_str}, with exceptions {exceptions_str}"
                #     elif combined_exceptions:
                #         exceptions_str = ", ".join(combined_exceptions)
                #         event_message = f"Routed to exceptions {exceptions_str}"
                #     else:
                #         routes_str = ", ".join(routed_actions)
                #         event_message = f"Routed to {routes_str}"
                #     await visitor.add_event(event_message)

                await interaction.save()

                logger.info(
                    f"InteractRouter: Routed to {len(routed_actions)} actions "
                    f"(+ {len(combined_exceptions)} exceptions, total: {len(all_allowed)})"
                )

                # Update walker's walk path with routed actions
                # Get all enabled InteractActions from Actions node
                actions_manager = await agent.get_actions_manager()
                if actions_manager:
                    all_enabled_actions = await actions_manager.get_actions(
                        enabled_only=True, entity=InteractAction
                    )
                    
                    # Filter to only routed actions (by entity name)
                    routed_entity_names = set(all_allowed)
                    routed_actions = [
                        action for action in all_enabled_actions
                        if action.get_class_name() in routed_entity_names
                    ]
                    
                    # Sort by weight to maintain execution order
                    routed_actions = sorted(routed_actions, key=lambda a: a.weight)
                    
                    # Update walker's walk path
                    curated = await visitor.curate_walk_path(routed_actions)
                    
                    logger.info(
                        f"InteractRouter: Updated walk path with {len(curated)} actions"
                    )
            else:
                # Even if parsing fails, set interpretation to indicate router executed
                # This ensures the walker knows routing was attempted
                interaction.interpretation = f"User said: {interaction.utterance[:50]}"
                # Compute dynamic exceptions even if parsing fails
                dynamic_exceptions = await self._get_dynamic_exceptions(agent)
                combined_exceptions = list(set(self.exceptions + dynamic_exceptions))
                interaction.anchors = combined_exceptions  # Only exceptions if no routing data
                await interaction.save()

                logger.warning(
                    f"InteractRouter: Failed to parse routing response, "
                    f"only exceptions will execute: {combined_exceptions}"
                )

                # Update walker's walk path with exception actions only
                actions_manager = await agent.get_actions_manager()
                if actions_manager:
                    all_enabled_actions = await actions_manager.get_actions(
                        enabled_only=True, entity=InteractAction
                    )
                    
                    # Filter to only exception actions (by entity name)
                    exception_entity_names = set(combined_exceptions)
                    exception_actions = [
                        action for action in all_enabled_actions
                        if action.get_class_name() in exception_entity_names
                    ]
                    
                    # Sort by weight to maintain execution order
                    exception_actions = sorted(exception_actions, key=lambda a: a.weight)
                    
                    # Update walker's walk path
                    curated = await visitor.curate_walk_path(exception_actions)
                    
                    logger.info(
                        f"InteractRouter: Updated walk path with {len(curated)} exception actions"
                    )

        except Exception as e:
            logger.error(f"InteractRouter: Error during routing: {e}", exc_info=True)
            # Don't raise - let other actions continue

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
        """Route using DSPy module with caching support.
        
        Args:
            interaction: The current interaction
            anchors_dict: Dictionary of action names to anchor lists
            interaction_history: Formatted conversation history
            
        Returns:
            Dictionary with keys: interpretation, actions (list), confidence
            Returns None on error (caller should handle fallback)
        """
        try:
            # Get model action
            model_action = await self.get_model_action(required=True)
            if not model_action:
                logger.warning("InteractRouter: Could not get model action for DSPy routing")
                return None
            
            # Configure DSPy cache if custom settings provided
            if self.enable_dspy_cache and (self.cache_disk_enabled is not None or self.cache_memory_enabled is not None):
                # Only configure if at least one setting is explicitly provided
                # This allows fine-grained control while defaulting to DSPy's sensible defaults
                try:
                    # Get current cache settings or use defaults
                    current_cache = getattr(dspy, 'cache', None)
                    
                    # Only reconfigure if we need to change from defaults
                    if current_cache is None or (
                        (self.cache_disk_enabled is not None and current_cache.enable_disk_cache != self.cache_disk_enabled) or
                        (self.cache_memory_enabled is not None and current_cache.enable_memory_cache != self.cache_memory_enabled)
                    ):
                        # Use DSPy's configure_cache if we need custom settings
                        # Note: This affects global cache, so we only do it if explicitly configured
                        # configure_cache is available via dspy.clients import
                        from dspy.clients import configure_cache, DISK_CACHE_DIR, DISK_CACHE_LIMIT
                        
                        configure_cache(
                            enable_disk_cache=self.cache_disk_enabled if self.cache_disk_enabled is not None else True,
                            enable_memory_cache=self.cache_memory_enabled if self.cache_memory_enabled is not None else True,
                            disk_cache_dir=DISK_CACHE_DIR,
                            disk_size_limit_bytes=DISK_CACHE_LIMIT,
                        )
                        logger.debug("InteractRouter: Configured DSPy cache with custom settings")
                except Exception as e:
                    logger.warning(f"InteractRouter: Failed to configure DSPy cache: {e}, using defaults")
            
            # Format conversation history for DSPy
            formatted_history = format_conversation_history_for_dspy(interaction_history)
            
            # Format anchors dict to JSON string
            anchors_json = json.dumps(anchors_dict, indent=2)
            
            # Create DSPy LM adapter with caching enabled/disabled based on configuration
            # Pass model, temperature, and max_tokens to allow agent.yaml overrides
            lm = DSPyLM(
                model_action=model_action,
                model_type="chat",
                model=self.model,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                cache=self.enable_dspy_cache,  # Enable/disable cache based on configuration
            )
            
            # Track cache state for logging
            cache_enabled = self.enable_dspy_cache and lm.cache
            
            # Configure DSPy with the adapter
            with dspy.context(lm=lm):
                # Create router module instance with action instance for signature docstring
                router_module = RouterModule(action_instance=self)
                
                # Build kwargs for router, include history if available
                router_kwargs = {
                    "user_utterance": interaction.utterance or "",
                    "available_actions": anchors_json,
                }
                if formatted_history:
                    router_kwargs["conversation_history"] = formatted_history
                
                # Call router module with async forward
                # Note: Cache hits are handled transparently by DSPyLM's request_cache decorator
                import time
                start_time = time.time()
                routing_result = await router_module.aforward(**router_kwargs)
                elapsed_time = time.time() - start_time
                
                # Log cache status (cache hits are very fast, typically < 1ms)
                if cache_enabled:
                    # Very fast responses (< 10ms) are likely cache hits
                    if elapsed_time < 0.01:
                        logger.debug(f"InteractRouter: Likely cache hit (response time: {elapsed_time*1000:.2f}ms)")
                    else:
                        logger.debug(f"InteractRouter: Cache miss or first request (response time: {elapsed_time*1000:.2f}ms)")
                
                # Return structured result matching legacy format
                return {
                    "interpretation": routing_result.get("interpretation", ""),
                    "actions": routing_result.get("actions", []),
                    "confidence": routing_result.get("confidence", 1.0),
                }
                
        except Exception as e:
            logger.error(
                f"InteractRouter: Failed to route via DSPy: {e}",
                exc_info=True
            )
            return None

    async def _route_legacy(
        self,
        interaction: "Interaction",
        anchors_dict: Dict[str, List[str]],
        interaction_history: List[Dict[str, Any]],
        model_action: Any,
    ) -> Optional[Dict[str, Any]]:
        """Route using legacy prompt-based approach.
        
        Args:
            interaction: The current interaction
            anchors_dict: Dictionary of action names to anchor lists
            interaction_history: Formatted conversation history
            model_action: The LanguageModelAction instance to use
            
        Returns:
            Dictionary with keys: interpretation, actions (list)
            Returns None on error
        """
        try:
            # Build routing prompt (history will be passed separately to LLM)
            prompt = self._build_routing_prompt(
                interaction.utterance,
                anchors_dict
            )

            # Determine model parameters (use overrides if provided, otherwise model_action defaults)
            model_param = self.model if self.model is not None else getattr(model_action, "model", None)
            temperature = self.model_temperature
            max_tokens = self.model_max_tokens

            response_text = await model_action.generate(
                prompt=prompt,
                stream=False,
                system=self.system_prompt,
                history=interaction_history,  # Pass formatted history directly to LLM
                calling_action_name=self.get_class_name(),
                model=model_param,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Parse response
            routing_data = self._parse_routing_response(response_text)
            return routing_data
            
        except Exception as e:
            logger.error(
                f"InteractRouter: Failed to route via legacy path: {e}",
                exc_info=True
            )
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

            # Skip actions that always execute or are in exceptions - no need to route to them
            if getattr(action, "always_execute", False) or entity_name in self.exceptions:
                logger.debug(
                    f"InteractRouter: Skipping {entity_name} from routing prompt "
                    f"(always_execute={getattr(action, 'always_execute', False)}, "
                    f"in exceptions={entity_name in self.exceptions})"
                )
                continue

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
