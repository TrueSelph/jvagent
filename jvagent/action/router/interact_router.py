"""InteractRouter action for intent-based routing with Chain of Verification.

InteractRouter analyzes incoming utterances using direct LLM calls with
Chain of Verification (CoVe) prompting to:
1. Classify intent type (CONVERSATIONAL, INFORMATIONAL, INTERACTIVE, DIRECTIVE, UNCLEAR)
2. Route to appropriate actions based on verified intent and context
3. Generate optional canned responses for immediate user feedback
4. Trigger clarification when confidence is below threshold
"""

import json
import logging
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.router.prompts import (
    ROUTER_SYSTEM_PROMPT,
    ROUTING_PROMPT_TEMPLATE,
    HISTORY_SECTION_TEMPLATE,
    CLARIFICATION_PROMPT_TEMPLATE,
    DEFAULT_CLARIFICATION_MESSAGES,
)
from jvagent.action.router.routing_result import (
    RoutingResult,
    parse_routing_response,
)
from jvagent.memory.conversation import Conversation

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class InteractRouter(InteractAction):
    """Router action with Chain of Verification for accurate intent routing.

    InteractRouter runs first (negative weight) to:
    1. Collect anchors from all InteractActions
    2. Build conversation history context
    3. Route via direct LLM call with Chain of Verification
    4. Optionally publish canned response for immediate feedback
    5. Evaluate confidence and trigger clarification if needed
    6. Store routing results on Interaction node

    Key Features:
        - Chain of Verification (CoVe) for improved routing accuracy
        - Confidence-calibrated routing with threshold-based clarification
        - Dynamic canned response generation tailored to user input
        - Rich interpretation reusable for RAG and entity extraction

    Attributes:
        model_action_type: Type of LanguageModelAction to use
        model: Model identifier (e.g., "gpt-4o-mini")
        model_temperature: Temperature for LLM generation (default: 0.1)
        model_max_tokens: Max tokens for LLM generation (default: 900)
        confidence_threshold: Minimum confidence to proceed without clarification
        enable_clarification: Whether to request clarification on low confidence
        enable_canned_response: Whether to publish immediate acknowledgments
        canned_response_max_words: Max words for canned response
        skip_canned_for_intents: Intent types that skip canned response
        include_verification_trace: Include verification in logs
        history_limit: Number of previous interactions to include
        weight: Execution weight (default: -100 to run first)
        exceptions: List of action names that always execute
    """

    # Model configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Type of LanguageModelAction to use for LLM calls"
    )
    model: Optional[str] = attribute(
        default="gpt-4o-mini",
        description="Model identifier to use (fast model recommended for routing)"
    )
    model_temperature: float = attribute(
        default=0.1,
        description="Temperature for LLM generation (lower for more consistent routing)"
    )
    model_max_tokens: int = attribute(
        default=400,
        description="Max tokens for LLM generation (reduced for faster routing)"
    )

    # Confidence settings (CoVe-calibrated)
    confidence_threshold: float = attribute(
        default=0.7,
        description="Minimum confidence score to proceed without clarification",
        ge=0.0,
        le=1.0
    )
    enable_clarification: bool = attribute(
        default=True,
        description="Whether to request clarification when confidence is below threshold"
    )

    # Canned response settings (dynamically generated)
    enable_canned_response: bool = attribute(
        default=True,
        description="Toggle dynamic canned response generation on/off"
    )
    canned_response_max_words: int = attribute(
        default=8,
        description="Maximum words for canned response",
        ge=1,
        le=20
    )
    skip_canned_for_intents: List[str] = attribute(
        default_factory=lambda: ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
        description="Intent types that should not receive canned responses"
    )

    # Entity extraction settings
    extract_entities: bool = attribute(
        default=False,
        description="Whether to extract entities (adds latency, only needed if downstream actions require it)"
    )

    # Clarification settings
    generate_dynamic_clarification: bool = attribute(
        default=False,
        description="Use LLM to generate clarification (adds latency) vs using templates"
    )

    # Routing configuration
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

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute intent classification and routing with Chain of Verification.

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
                result = RoutingResult.error_result(
                    "No actions available for routing",
                    interaction.utterance or ""
                )
                await self._finalize_routing(
                    visitor, interaction, agent, result, combined_exceptions
                )
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

            # Route using direct LLM call
            result = await self._route_direct(
                interaction,
                anchors_dict,
                interaction_history,
            )

            # Publish canned response if enabled and appropriate
            await self._publish_canned_response(visitor, result)

            # Evaluate confidence and handle clarification
            result = await self._evaluate_confidence(result, visitor, interaction)

            # Finalize routing
            await self._finalize_routing(
                visitor, interaction, agent, result, combined_exceptions
            )

        except Exception as e:
            logger.error(f"InteractRouter: Error during routing: {e}", exc_info=True)

    async def _route_direct(
        self,
        interaction: "Interaction",
        anchors_dict: Dict[str, List[str]],
        interaction_history: List[Dict[str, Any]],
    ) -> RoutingResult:
        """Route using direct LLM call with Chain of Verification.

        Args:
            interaction: The current interaction
            anchors_dict: Dictionary of action names to anchor lists
            interaction_history: Formatted conversation history

        Returns:
            RoutingResult with verified routing data
        """
        try:
            model_action = await self.get_model_action(required=True)
            if not model_action:
                return RoutingResult.error_result(
                    "Could not get model action",
                    interaction.utterance or ""
                )

            # Build the routing prompt
            prompt = self._build_routing_prompt(
                utterance=interaction.utterance or "",
                anchors_dict=anchors_dict,
                interaction_history=interaction_history,
            )

            # Single LLM call with Chain of Verification
            response = await model_action.generate(
                prompt=prompt,
                system=ROUTER_SYSTEM_PROMPT,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                model=self.model,
                calling_action_name=self.get_class_name(),
            )

            # Parse the response into RoutingResult
            result = parse_routing_response(response)

            return result

        except Exception as e:
            logger.error(f"InteractRouter: Direct routing failed: {e}", exc_info=True)
            return RoutingResult.error_result(str(e), interaction.utterance or "")

    def _build_routing_prompt(
        self,
        utterance: str,
        anchors_dict: Dict[str, List[str]],
        interaction_history: List[Dict[str, Any]],
    ) -> str:
        """Build the routing prompt with optional entity extraction.

        Args:
            utterance: User's current message
            anchors_dict: Dictionary of action names to anchor lists
            interaction_history: Formatted conversation history

        Returns:
            Complete routing prompt string
        """
        # Format anchors as JSON
        anchors_json = json.dumps(anchors_dict, indent=2)

        # Format history section
        history_section = ""
        if interaction_history:
            history_text = self._format_history(interaction_history)
            history_section = HISTORY_SECTION_TEMPLATE.format(history=history_text)

        # Conditional entity extraction field
        entity_field = ',\n  "extracted_entities": {}' if self.extract_entities else ''
        
        # Conditional canned response field
        canned_field = ',\n  "canned_response": ""' if self.enable_canned_response else ''
        
        # Optional instructions
        optional_instructions = ""
        if self.extract_entities:
            optional_instructions += "\n5. Extract mentioned entities as flexible key-value pairs"
        if self.enable_canned_response:
            skip_intents = ", ".join(self.skip_canned_for_intents)
            optional_instructions += f"\n6. Generate a GENERIC, BRIEF, HUMAN-LIKE canned response for immediate acknowledgment only (e.g. 'Let me see..', 'One moment..', [generate more examples]), NO assumed pronouncements (e.g. I can do that.., etc. ). EXCEPT for {skip_intents} intents (use empty string)"

        # Build the complete prompt
        prompt = ROUTING_PROMPT_TEMPLATE.format(
            utterance=utterance,
            anchors_json=anchors_json,
            history_section=history_section,
            entity_field=entity_field,
            canned_field=canned_field,
            optional_instructions=optional_instructions,
        )

        return prompt

    def _format_history(self, interaction_history: List[Dict[str, Any]]) -> str:
        """Format interaction history for the prompt with context signals.

        Prepends a context line highlighting key signals from the last interaction:
        - Whether the assistant asked a question
        - Whether there's an ongoing activity

        Handles both formats from conversation.get_interaction_history():
        - formatted=True: list of dicts with 'role' and 'content' (user/assistant/system)
        - formatted=False: list of dicts with 'utterance', 'response', 'events' per interaction

        Args:
            interaction_history: List of interaction history entries

        Returns:
            Formatted history string with context line
        """
        if not interaction_history:
            return "(No previous conversation)"

        # Detect format: role/content (formatted=True from get_interaction_history) vs human/ai or utterance/response
        first_entry = interaction_history[0] if interaction_history else {}
        is_role_content = isinstance(first_entry, dict) and "role" in first_entry and "content" in first_entry

        # Extract context signals from the last entry
        context_signals = []
        last_entry = interaction_history[-1] if interaction_history else None

        if last_entry and isinstance(last_entry, dict):
            if is_role_content:
                if last_entry.get("role") == "assistant":
                    content = last_entry.get("content") or ""
                    if content.strip().endswith("?"):
                        context_signals.append("Last assistant asked a question")
                for e in reversed(interaction_history[-10:]):
                    if isinstance(e, dict) and (e.get("content") or "").startswith("[EVENT]"):
                        ev = e["content"]
                        if "Ongoing Activity:" in ev:
                            activity_name = ev.replace("[EVENT] ", "").replace("Ongoing Activity:", "").strip()
                            context_signals.append(f"Ongoing activity: {activity_name}")
                            break
            else:
                if "ai" in last_entry:
                    ai_msg = last_entry["ai"]
                    if ai_msg and ai_msg.strip().endswith("?"):
                        context_signals.append("Last assistant asked a question")
                if "events" in last_entry:
                    for event in last_entry["events"]:
                        ev_str = event.get("content", event) if isinstance(event, dict) else str(event)
                        if "Ongoing Activity:" in ev_str:
                            activity_name = ev_str.replace("[EVENT] ", "").replace("Ongoing Activity:", "").strip()
                            context_signals.append(f"Ongoing activity: {activity_name}")
                            break

        # Build the history lines
        lines = []

        # Add context line if we have signals
        if context_signals:
            context_line = "Context: " + ". ".join(context_signals) + "."
            lines.append(context_line)
            lines.append("")  # Empty line for readability

        # Add the full history
        for i, entry in enumerate(interaction_history):
            is_last = (i == len(interaction_history) - 1)

            if isinstance(entry, dict):
                if is_role_content:
                    role = entry.get("role", "")
                    content = entry.get("content") or ""
                    if role == "user":
                        lines.append(f"User: {content}")
                    elif role == "assistant":
                        if is_last and content.strip().endswith("?"):
                            lines.append(f"Assistant (question): {content}")
                        else:
                            lines.append(f"Assistant: {content}")
                    elif role == "system" and (content or "").startswith("[EVENT]"):
                        if "Ongoing Activity:" in content:
                            lines.append(f"[Ongoing] {content.replace('[EVENT] ', '').replace('Ongoing Activity:', '').strip()}")
                        else:
                            lines.append(content)
                else:
                    if "human" in entry:
                        lines.append(f"User: {entry['human']}")
                    elif "utterance" in entry:
                        lines.append(f"User: {entry['utterance']}")
                    if "ai" in entry:
                        ai_msg = entry["ai"]
                        if is_last and ai_msg and ai_msg.strip().endswith("?"):
                            lines.append(f"Assistant (question): {ai_msg}")
                        else:
                            lines.append(f"Assistant: {ai_msg}")
                    elif "response" in entry and entry["response"]:
                        resp = entry["response"]
                        if is_last and resp.strip().endswith("?"):
                            lines.append(f"Assistant (question): {resp}")
                        else:
                            lines.append(f"Assistant: {resp}")
                    if "events" in entry:
                        for event in entry["events"]:
                            ev_str = event.get("content", event) if isinstance(event, dict) else str(event)
                            if "Ongoing Activity:" in ev_str:
                                lines.append(f"[Ongoing] {ev_str.replace('Ongoing Activity:', '').strip()}")
                            else:
                                lines.append(f"[EVENT] {ev_str}")
            elif isinstance(entry, str):
                lines.append(entry)

        return "\n".join(lines) if lines else "(No previous conversation)"

    async def _publish_canned_response(
        self,
        visitor: "InteractWalker",
        result: RoutingResult,
    ) -> None:
        """Publish LLM-generated canned response if enabled and appropriate.

        Args:
            visitor: The InteractWalker
            result: The routing result with canned response
        """
        if not self.enable_canned_response:
            return

        # Skip for certain intent types
        if result.intent_type in self.skip_canned_for_intents:
            logger.debug(
                f"InteractRouter: Skipping canned response for intent {result.intent_type}"
            )
            return

        # Get the dynamically generated canned response
        canned = result.canned_response
        if not canned or not canned.strip():
            return

        # Set canned_response field directly instead of publishing to response
        interaction = visitor.interaction
        if not interaction:
            logger.warning("InteractRouter: No interaction available for canned response")
            return

        # Only publish canned response if interaction.response is empty
        if interaction.response:
            logger.debug(
                "InteractRouter: Skipping canned response - interaction already has response"
            )
            return

        try:
            interaction.canned_response = canned.strip()
            await interaction.save()
            
            # Publish canned response with transient=True
            # This sends to user but keeps interaction.response = None
            await self.publish(
                visitor,
                canned.strip(),
                transient=True
            )
            logger.debug(f"InteractRouter: Published canned response: {canned}")
        except Exception as e:
            logger.warning(f"InteractRouter: Failed to publish canned response: {e}")

    async def _evaluate_confidence(
        self,
        result: RoutingResult,
        visitor: "InteractWalker",
        interaction: "Interaction",
    ) -> RoutingResult:
        """Evaluate confidence and trigger clarification if needed.

        Args:
            result: The routing result to evaluate
            visitor: The InteractWalker
            interaction: The current interaction

        Returns:
            Updated RoutingResult (may have needs_clarification set)
        """
        if result.confidence >= self.confidence_threshold:
            return result

        issues = result.verification.issues_found if result.verification else []
        logger.info(
            f"InteractRouter: Low confidence ({result.confidence:.2f} < {self.confidence_threshold}), "
            f"issues: {issues}"
        )

        if self.enable_clarification:
            # Generate and publish clarification message
            clarification = await self._generate_clarification(
                interaction.utterance or "",
                result.interpretation,
                result.intent_type,
                result.confidence,
                issues,
            )

            if clarification:
                try:
                    await self.publish(visitor, clarification, stream=False)
                    logger.debug(f"InteractRouter: Published clarification: {clarification}")
                except Exception as e:
                    logger.warning(f"InteractRouter: Failed to publish clarification: {e}")

            # Mark as needing clarification
            result.needs_clarification = True
            result.intent_type = "UNCLEAR"

        return result

    async def _generate_clarification(
        self,
        utterance: str,
        interpretation: str,
        intent_type: str,
        confidence: float,
        issues: List[str],
    ) -> str:
        """Generate a clarification message.

        Args:
            utterance: Original user message
            interpretation: Current interpretation
            intent_type: Classified intent type
            confidence: Confidence score
            issues: Issues found during verification

        Returns:
            Clarification message string
        """
        # Use fast template-based clarification unless dynamic generation is enabled
        if not self.generate_dynamic_clarification:
            return random.choice(DEFAULT_CLARIFICATION_MESSAGES)
        
        # LLM-based clarification generation (adds latency)
        try:
            model_action = await self.get_model_action()
            if model_action:
                prompt = CLARIFICATION_PROMPT_TEMPLATE.format(
                    utterance=utterance,
                    interpretation=interpretation,
                    intent_type=intent_type,
                    confidence=confidence,
                    issues=", ".join(issues) if issues else "Low confidence in routing",
                )

                clarification = await model_action.generate(
                    prompt=prompt,
                    temperature=0.7,
                    max_tokens=100,
                    model=self.model,
                    calling_action_name=f"{self.get_class_name()}_clarification",
                )

                if clarification and clarification.strip():
                    return clarification.strip()

        except Exception as e:
            logger.warning(f"InteractRouter: Failed to generate clarification: {e}")

        # Fallback to default clarification messages
        return random.choice(DEFAULT_CLARIFICATION_MESSAGES)

    async def _finalize_routing(
        self,
        visitor: "InteractWalker",
        interaction: "Interaction",
        agent: Any,
        result: RoutingResult,
        combined_exceptions: List[str],
    ) -> None:
        """Finalize routing by storing results and updating walk path.

        Args:
            visitor: The InteractWalker
            interaction: The interaction to update
            agent: The agent instance
            result: The routing result
            combined_exceptions: Actions that always execute
        """
        routed_actions = result.actions

        # CONVERSATIONAL intent must not route to any actions
        if result.intent_type == "CONVERSATIONAL":
            routed_actions = []
            logger.debug("InteractRouter: CONVERSATIONAL intent - clearing routed actions")

        # Combine with exceptions
        all_allowed = list(set(routed_actions + combined_exceptions))

        # Store routing results on interaction
        await self._store_routing_result(
            interaction,
            interpretation=result.interpretation,
            actions=all_allowed,
            intent_type=result.intent_type,
        )

        # Log routing summary
        logger.info(
            f"InteractRouter: intent_type={result.intent_type}, "
            f"confidence={result.confidence:.2f}, "
            f"routed to {len(routed_actions)} actions (+ {len(combined_exceptions)} exceptions)"
        )

        # Update walk path
        await self._update_walk_path(visitor, agent, all_allowed)

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
