"""InteractRouter action for intent-based routing with Chain of Verification.

InteractRouter analyzes incoming utterances using direct LLM calls with
Chain of Verification (CoVe) prompting to:
1. Classify intent type (CONVERSATIONAL, INFORMATIONAL, INTERACTIVE, DIRECTIVE, UNCLEAR)
2. Route to appropriate actions based on verified intent and context
3. Generate optional canned responses for immediate user feedback
4. Trigger clarification when confidence is below threshold
"""

import asyncio
import hashlib
import json
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.router.formatting import format_interaction_history
from jvagent.action.router.prompts import (
    CANNED_RESPONSE_INSTRUCTIONS_TEMPLATE,
    CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE,
    CLARIFICATION_PROMPT_TEMPLATE,
    DEFAULT_CLARIFICATION_MESSAGES,
    PRIOR_FRAGMENTS_SECTION,
    ROUTER_SYSTEM_PROMPT,
    ROUTING_PROMPT_TEMPLATE,
)
from jvagent.action.router.routing_result import (
    POSTURE_RESPOND,
    RoutingResult,
    parse_routing_response,
)
from jvagent.core.app import App
from jvagent.core.cache import (
    get_interact_router_cache,
    interact_router_cache_key,
    set_interact_router_cache,
)
from jvagent.memory.conversation import Conversation
from jvagent.memory.user_long_memory import UserLongMemory

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)

BUFFER_KEY = "deferred_fragments"


def _get_buffer(conversation: Conversation) -> list:
    """Get deferred fragments buffer."""
    return list(conversation.context.get(BUFFER_KEY, []))


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
        model_max_tokens: Max tokens for LLM generation (default: 400)
        confidence_threshold: Minimum confidence to proceed without clarification
        enable_clarification: Whether to request clarification on low confidence
        enable_canned_response: Whether to publish immediate acknowledgments
        canned_response_max_words: Max words for canned response
        skip_canned_for_intents: Intent types that skip canned response
        history_limit: Number of previous interactions to include
        weight: Execution weight (default: -100 to run first)
        exceptions: List of action names that always execute
    """

    # Model configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Type of LanguageModelAction to use for LLM calls",
    )
    model: Optional[str] = attribute(
        default="gpt-4o-mini",
        description="Model identifier to use (fast model recommended for routing)",
    )
    model_temperature: float = attribute(
        default=0.1,
        description="Temperature for LLM generation (lower for more consistent routing)",
    )
    model_max_tokens: int = attribute(
        default=400,
        description="Max tokens for LLM generation (reduced for faster routing)",
    )

    # Confidence settings (CoVe-calibrated)
    confidence_threshold: float = attribute(
        default=0.7,
        description="Minimum confidence score to proceed without clarification",
        ge=0.0,
        le=1.0,
    )
    enable_clarification: bool = attribute(
        default=False,
        description="Whether to request clarification when confidence is below threshold",
    )

    # Canned response settings (dynamically generated)
    enable_canned_response: bool = attribute(
        default=True, description="Toggle dynamic canned response generation on/off"
    )
    canned_response_max_words: int = attribute(
        default=8, description="Maximum words for canned response", ge=1, le=20
    )
    skip_canned_for_intents: List[str] = attribute(
        default_factory=lambda: ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
        description="Intent types that should not receive canned responses",
    )

    # Entity extraction settings
    extract_entities: bool = attribute(
        default=False,
        description="Whether to extract entities (adds latency, only needed if downstream actions require it)",
    )

    # Clarification settings
    generate_dynamic_clarification: bool = attribute(
        default=False,
        description="Use LLM to generate clarification (adds latency) vs using templates",
    )

    # Routing configuration
    history_limit: int = attribute(
        default=3,
        description="Number of previous interactions to include in conversation history",
        ge=0,
    )
    weight: int = attribute(
        default=-200,
        description="Execution weight (negative to run first; -200)",
    )
    exceptions: List[str] = attribute(
        default_factory=list,
        description="List of InteractAction entity names that must always execute",
    )

    pass_through_task_types: Sequence[str] = attribute(
        default=("INTERVIEW",),
        description="Task types that skip router LLM when active; proceed as RESPOND",
    )
    pass_through_when_media: bool = attribute(
        default=True,
        description="Skip router LLM when user has attached media (images, documents)",
    )
    bypass_canned_response: str = attribute(
        default="One moment",
        description="Instant canned response for bypass paths (interview/media)",
    )
    media_bypass_actions: List[str] = attribute(
        default_factory=list,
        description="When non-empty and media attached, route to these actions without LLM",
    )
    enable_accumulation: bool = attribute(
        default=True,
        description="Enable DEFER posture and fragment accumulation",
    )
    max_fragment_buffer: int = attribute(
        default=5,
        description="Max deferred fragments to retain",
        ge=1,
        le=20,
    )
    enable_routing_cache: bool = attribute(
        default=False,
        description="Enable routing cache to skip LLM for repeated context (requires enable_interact_router_cache in app.yaml)",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute unified posture classification and routing in one LLM call.

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
            # Prefer visitor.conversation (same instance flushed at request end) for buffer persistence
            agent = await self.get_agent()
            conversation = getattr(
                visitor, "conversation", None
            ) or await Conversation.get(interaction.conversation_id)
            if not agent:
                logger.error("InteractRouter: Agent not found")
                return

            dynamic_exceptions = await self._get_dynamic_exceptions(agent)
            combined_exceptions = list(set(self.exceptions + dynamic_exceptions))

            # Bypass: interview active -> 0 LLM, route to active task (before model_action)
            if self.pass_through_task_types and conversation:
                active_tasks = conversation.get_tasks(status="active")
                for t in active_tasks:
                    if t.get("task_type") in self.pass_through_task_types:
                        action_name = t.get("owner_action", "")
                        logger.debug(
                            f"InteractRouter: Bypass (active {t.get('task_type')}: {action_name})"
                        )
                        result = RoutingResult(
                            posture=POSTURE_RESPOND,
                            interpretation="Bypass: active task",
                            intent_type="INTERACTIVE",
                            actions=[action_name] if action_name else [],
                            confidence=1.0,
                            canned_response=self.bypass_canned_response,
                        )
                        interaction.response_posture = POSTURE_RESPOND
                        await interaction.save()
                        await self._handle_respond(visitor, interaction, conversation)
                        await self._publish_canned_response(visitor, result)
                        await self._finalize_routing(
                            visitor,
                            interaction,
                            agent,
                            result,
                            combined_exceptions,
                            conversation=conversation,
                        )
                        return

            # Bypass: media attached + media_bypass_actions configured
            if self.pass_through_when_media and self.media_bypass_actions:
                data = getattr(visitor, "data", None) or {}
                media_urls = data.get("image_urls") or data.get("whatsapp_media") or []
                if media_urls:
                    logger.debug(
                        "InteractRouter: Bypass (media attached: %d items)",
                        len(media_urls),
                    )
                    result = RoutingResult(
                        posture=POSTURE_RESPOND,
                        interpretation="Bypass: media attached",
                        intent_type="INFORMATIONAL",
                        actions=list(self.media_bypass_actions),
                        confidence=1.0,
                        canned_response=self.bypass_canned_response,
                    )
                    interaction.response_posture = POSTURE_RESPOND
                    await interaction.save()
                    await self._handle_respond(visitor, interaction, conversation)
                    await self._publish_canned_response(visitor, result)
                    await self._finalize_routing(
                        visitor,
                        interaction,
                        agent,
                        result,
                        combined_exceptions,
                        conversation=conversation,
                    )
                    return

            model_action = await self.get_model_action()
            if not model_action:
                logger.error("InteractRouter: Model action not found")
                return

            if conversation:
                anchors_dict, interaction_history = await asyncio.gather(
                    self._collect_anchors(agent, conversation=conversation),
                    conversation.get_interaction_history(
                        limit=self.history_limit,
                        excluded=interaction.id,
                        with_utterance=True,
                        with_response=True,
                        with_interpretation=True,
                        with_event=True,
                        with_posture=True,
                        formatted=False,
                    ),
                )
            else:
                anchors_dict = await self._collect_anchors(agent, conversation=None)
                interaction_history = []

            if not anchors_dict:
                logger.warning(
                    "InteractRouter: No anchors available for routing "
                    "(session_id=%s)",
                    getattr(visitor, "session_id", None),
                )
                result = RoutingResult.error_result(
                    "No actions available for routing",
                    interaction.utterance or "",
                )

                await self._finalize_routing(
                    visitor,
                    interaction,
                    agent,
                    result,
                    combined_exceptions,
                    conversation=conversation,
                )
                return

            # Cache lookup (skip LLM when enabled and hit)
            result = None
            cache_key = None
            if conversation and self.enable_routing_cache:
                last_interaction_ids = tuple(
                    e.get("interaction_id", "") for e in (interaction_history or [])
                )
                buffer = _get_buffer(conversation)
                buffer_fingerprint = (
                    hashlib.sha256(
                        json.dumps([b.get("utterance", "") for b in buffer]).encode()
                    ).hexdigest()
                    if buffer
                    else ""
                )
                active_tasks = conversation.get_tasks(status="active")
                active_task_fingerprint = (
                    hashlib.sha256(
                        json.dumps(
                            sorted([t.get("owner_action", "") for t in active_tasks])
                        ).encode()
                    ).hexdigest()
                    if active_tasks
                    else ""
                )
                cache_key = interact_router_cache_key(
                    interaction.conversation_id,
                    interaction.utterance or "",
                    last_interaction_ids,
                    buffer_fingerprint,
                    active_task_fingerprint,
                )
                cached = await get_interact_router_cache(
                    cache_key, caller_enabled=self.enable_routing_cache
                )
                if cached is not None:
                    result = RoutingResult.from_dict(cached)
                    result.actions = self._resolve_actions_to_keys(
                        result.actions, anchors_dict
                    )

            if result is None:
                result = await self._route_direct(
                    interaction,
                    anchors_dict,
                    interaction_history or [],
                    conversation=conversation,
                )
                if (
                    self.enable_routing_cache
                    and cache_key is not None
                    and result.confidence > 0
                ):
                    await set_interact_router_cache(
                        cache_key,
                        result.to_dict(),
                        caller_enabled=self.enable_routing_cache,
                    )

            # Handle posture: SUPPRESS/DEFER early exit
            interaction.response_posture = result.posture
            await interaction.save()

            if result.is_suppress():
                await self._handle_suppress(visitor)
                return

            if result.is_defer() and self.enable_accumulation:
                if conversation:
                    await self._handle_defer(visitor, interaction, conversation)
                else:
                    await visitor.set_walk_path([])
                    logger.warning(
                        "InteractRouter: DEFER but no conversation - cannot persist buffer"
                    )
                return

            if result.is_respond():
                if conversation:
                    await self._handle_respond(visitor, interaction, conversation)

            # Publish canned response (before save for latency)
            await self._publish_canned_response(visitor, result)

            # Evaluate confidence and handle clarification
            result = await self._evaluate_confidence(result, visitor, interaction)

            # Finalize routing
            await self._finalize_routing(
                visitor,
                interaction,
                agent,
                result,
                combined_exceptions,
                conversation=conversation,
            )

        except Exception as e:
            logger.error(f"InteractRouter: Error during routing: {e}", exc_info=True)

    async def _route_direct(
        self,
        interaction: "Interaction",
        anchors_dict: Dict[str, List[str]],
        interaction_history: List[Dict[str, Any]],
        conversation: Optional[Conversation] = None,
    ) -> RoutingResult:
        """Route using direct LLM call with Chain of Verification.

        Args:
            interaction: The current interaction
            anchors_dict: Dictionary of action names to anchor lists
            interaction_history: Formatted conversation history
            conversation: Optional Conversation for task tracker context

        Returns:
            RoutingResult with verified routing data
        """
        try:
            model_action = await self.get_model_action(required=True)
            if not model_action:
                return RoutingResult.error_result(
                    "Could not get model action", interaction.utterance or ""
                )

            # Build the routing prompt
            prompt = self._build_routing_prompt(
                utterance=interaction.utterance or "",
                anchors_dict=anchors_dict,
                interaction_history=interaction_history,
                conversation=conversation,
            )

            # Single LLM call with Chain of Verification
            response = await model_action.generate(
                prompt=prompt,
                system=ROUTER_SYSTEM_PROMPT,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                model=self.model,
                calling_action_name=self.get_class_name(),
                interaction=interaction,
            )

            # Parse the response into RoutingResult
            result = parse_routing_response(response)

            # Resolve actions: LLM may return descriptions instead of keys; map back to action names
            result.actions = self._resolve_actions_to_keys(result.actions, anchors_dict)

            return result

        except Exception as e:
            logger.error(f"InteractRouter: Direct routing failed: {e}", exc_info=True)
            return RoutingResult.error_result(str(e), interaction.utterance or "")

    def _build_routing_prompt(
        self,
        utterance: str,
        anchors_dict: Dict[str, List[str]],
        interaction_history: List[Dict[str, Any]],
        conversation: Optional[Conversation] = None,
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

        # Format conversation history
        history_section = (
            format_interaction_history(interaction_history, conversation=conversation)
            if interaction_history
            else "(No previous conversation)"
        )

        # Build ACTIVE TASKS section only when there are active tasks
        active_tasks_section = ""
        if conversation:
            active_descriptions = conversation.get_active_tasks_for_context()
            if active_descriptions:
                task_lines = "\n".join(f"- {desc}" for desc in active_descriptions)
                active_tasks_section = f"ACTIVE TASKS:\n{task_lines}\n\n"

        # Build PRIOR DEFERRED FRAGMENTS section when buffer has content
        prior_fragments_section = ""
        if conversation:
            buffer = _get_buffer(conversation)
            prior_fragments = [
                b.get("utterance", "").strip() for b in buffer if b.get("utterance")
            ]
            if prior_fragments:
                fragments_list = "\n".join(
                    f'  {i + 1}. "{f}"' for i, f in enumerate(prior_fragments)
                )
                prior_fragments_section = PRIOR_FRAGMENTS_SECTION.format(
                    fragments_list=fragments_list,
                )

        # Conditional entity extraction field
        entity_field = ',\n  "extracted_entities": {}' if self.extract_entities else ""

        # Conditional canned response field
        canned_field = (
            ',\n  "canned_response": ""' if self.enable_canned_response else ""
        )

        # Optional instructions
        optional_instructions = ""
        if self.extract_entities:
            optional_instructions += (
                "\n5. Extract mentioned entities as flexible key-value pairs"
            )
        if self.enable_canned_response:
            skip_intents = ", ".join(self.skip_canned_for_intents)
            optional_instructions += CANNED_RESPONSE_INSTRUCTIONS_TEMPLATE.format(
                max_words=self.canned_response_max_words,
                skip_intents=skip_intents,
            )

        # Build the complete prompt
        prompt = ROUTING_PROMPT_TEMPLATE.format(
            utterance=utterance,
            anchors_json=anchors_json,
            active_tasks_section=active_tasks_section,
            history_section=history_section,
            prior_fragments_section=prior_fragments_section,
            entity_field=entity_field,
            canned_field=canned_field,
            optional_instructions=optional_instructions,
        )

        return prompt

    @staticmethod
    def _resolve_actions_to_keys(
        actions: List[str],
        anchors_dict: Dict[str, List[str]],
    ) -> List[str]:
        """Resolve actions to valid action names (keys). LLM may return descriptions.

        Args:
            actions: Raw actions from LLM (may include descriptions)
            anchors_dict: Map of action_name -> [description, ...anchors]

        Returns:
            List of valid action names (keys only), or [] if none valid
        """
        if not actions:
            return []

        valid_keys = set(anchors_dict)
        # Build reverse map: anchor/description -> action_name
        anchor_to_action: Dict[str, str] = {}
        for action_name, anchors_list in anchors_dict.items():
            for anchor in anchors_list:
                if anchor and isinstance(anchor, str):
                    anchor_to_action[anchor.strip()] = action_name

        resolved: List[str] = []
        for a in actions:
            a_str = str(a).strip() if a else ""
            if not a_str:
                continue
            if a_str in valid_keys:
                resolved.append(a_str)
            elif a_str in anchor_to_action:
                resolved.append(anchor_to_action[a_str])
            else:
                logger.debug(
                    "InteractRouter: Dropping non-key action '%s' (not in anchors keys)",
                    a_str[:50],
                )

        return list(dict.fromkeys(resolved))  # preserve order, dedupe

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

        # Skip for certain intent types (e.g. CONVERSATIONAL)
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
            logger.warning(
                "InteractRouter: No interaction available for canned response"
            )
            return

        # Only publish canned response if interaction.response is empty
        if interaction.response:
            logger.debug(
                "InteractRouter: Skipping canned response - interaction already has response"
            )
            return

        try:
            # Publish first (user sees message before persistence)
            await self.publish(visitor, canned.strip(), transient=True)
            interaction.canned_response = canned.strip()
            await interaction.save()
            logger.debug(f"InteractRouter: Published canned response: {canned}")
        except Exception as e:
            logger.warning(f"InteractRouter: Failed to publish canned response: {e}")

    async def _handle_suppress(self, visitor: "InteractWalker") -> None:
        """Clear walk path so no response is emitted (SUPPRESS posture)."""
        await visitor.set_walk_path([])
        logger.info("InteractRouter: SUPPRESS - cleared walk path, no response")

    async def _handle_defer(
        self,
        visitor: "InteractWalker",
        interaction: "Interaction",
        conversation: "Conversation",
    ) -> None:
        """Append utterance to buffer and clear walk path (DEFER posture)."""
        buffer = _get_buffer(conversation)
        app = await App.get()
        if app:
            now_dt = await app.now()
            now = now_dt.isoformat() if isinstance(now_dt, datetime) else now_dt
        else:
            now = datetime.now(timezone.utc).isoformat()
        buffer.append(
            {
                "utterance": interaction.utterance or "",
                "interaction_id": interaction.id,
                "timestamp": now,
            }
        )
        if len(buffer) > self.max_fragment_buffer:
            buffer = buffer[-self.max_fragment_buffer :]
        await conversation.update_context({BUFFER_KEY: buffer})
        await visitor.set_walk_path([])
        logger.info(
            f"InteractRouter: DEFER - appended to buffer ({len(buffer)} fragments), no response"
        )

    async def _handle_respond(
        self,
        visitor: "InteractWalker",
        interaction: "Interaction",
        conversation: "Conversation",
    ) -> None:
        """Consume buffer if non-empty, inject directive, let pipeline proceed (RESPOND posture)."""
        buffer = _get_buffer(conversation)
        if buffer:
            fragments = [
                b.get("utterance", "").strip() for b in buffer if b.get("utterance")
            ]
            if fragments:
                directive = (
                    f"The user's current message completes a fragmented thought. "
                    f"Prior fragments: {repr(fragments)}. Treat them as a unified request."
                )
                await visitor.add_directive(directive)
                logger.info(
                    f"InteractRouter: RESPOND - injected directive with {len(fragments)} prior fragments"
                )
            await conversation.update_context({BUFFER_KEY: []})

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
        if not result.should_clarify(self.confidence_threshold):
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
                interaction=interaction,
            )

            if clarification:
                try:
                    await self.publish(visitor, clarification, stream=False)
                    logger.debug(
                        f"InteractRouter: Published clarification: {clarification}"
                    )
                except Exception as e:
                    logger.warning(
                        f"InteractRouter: Failed to publish clarification: {e}"
                    )

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
        *,
        interaction: Optional["Interaction"] = None,
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
        # Template-based: LLM paraphrases the template to match user's language
        if not self.generate_dynamic_clarification:
            template = random.choice(DEFAULT_CLARIFICATION_MESSAGES)
            try:
                model_action = await self.get_model_action()
                if model_action:
                    prompt = CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE.format(
                        utterance=utterance,
                        template=template,
                    )
                    clarification = await model_action.generate(
                        prompt=prompt,
                        temperature=0.7,
                        max_tokens=100,
                        model=self.model,
                        calling_action_name=f"{self.get_class_name()}_clarification_paraphrase",
                        interaction=interaction,
                    )
                    if clarification and clarification.strip():
                        return clarification.strip()
            except Exception as e:
                logger.warning(
                    f"InteractRouter: Paraphrase failed, using template: {e}"
                )
            return template

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
                    interaction=interaction,
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
        conversation: Optional[Conversation] = None,
    ) -> None:
        """Finalize routing by storing results and updating walk path.

        Args:
            visitor: The InteractWalker
            interaction: The interaction to update
            agent: The agent instance
            result: The routing result
            combined_exceptions: Actions that always execute
            conversation: Optional Conversation for active interview routing
        """
        # parse_routing_response already clears actions for CONVERSATIONAL intent
        routed_actions = result.actions

        # Combine with exceptions
        all_allowed = list(set(routed_actions + combined_exceptions))

        # When an interview is active, filter out other interview actions (safety net)
        if conversation:
            active_task = conversation.get_task(
                task_type="INTERVIEW", status="active"
            )
            active_interview_name = (
                active_task.get("owner_action") if active_task else None
            )
            if active_interview_name:
                actions_manager = await agent.get_actions_manager()
                if actions_manager:
                    all_interact_actions = await actions_manager.get_actions(
                        enabled_only=True, entity=InteractAction
                    )
                    interview_names = {
                        a.get_class_name()
                        for a in all_interact_actions
                        if getattr(a, "task_type", None) == "INTERVIEW"
                    }
                    all_allowed = [
                        name
                        for name in all_allowed
                        if name not in interview_names or name == active_interview_name
                    ]

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

        Note: interaction.anchors is overwritten with routed action names (not
        anchor phrases). This is the list of InteractAction class names that
        the walker should allow for this interaction.

        Args:
            interaction: The interaction to update
            interpretation: LLM-generated interpretation
            actions: List of action names to route to (routed action names)
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
            action
            for action in all_enabled_actions
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

    async def _collect_anchors(
        self, agent: Any, conversation: Optional[Conversation] = None
    ) -> Dict[str, List[str]]:
        """Collect anchors from all InteractActions.

        When an interview task is active, only that interview action's anchors
        are included; other interview actions are excluded to prevent routing
        to multiple interviews.

        Args:
            agent: Agent instance
            conversation: Optional Conversation for active task routing

        Returns:
            Dictionary mapping entity names to anchor statement lists
        """
        actions_manager = await agent.get_actions_manager()
        if not actions_manager:
            return {}

        all_interact_actions = await actions_manager.get_actions(
            enabled_only=True, entity=InteractAction
        )
        # Exclude this router
        interact_actions = [a for a in all_interact_actions if a.id != self.id]

        # When an interview is active, only allow that interview's anchors
        active_interview_name: Optional[str] = None
        if conversation:
            active_task = conversation.get_task(
                task_type="INTERVIEW", status="active"
            )
            active_interview_name = (
                active_task.get("owner_action") if active_task else None
            )

        logger.debug(f"InteractRouter: Found {len(interact_actions)} InteractActions")

        anchors_dict: Dict[str, List[str]] = {}
        for action in interact_actions:
            entity_name = action.get_class_name()

            # Skip actions that always execute or are in exceptions
            if (
                getattr(action, "always_execute", False)
                or entity_name in self.exceptions
            ):
                logger.debug(
                    f"InteractRouter: Skipping {entity_name} (always_execute or exception)"
                )
                continue

            # When an interview is active, exclude other interview actions
            if active_interview_name:
                if getattr(action, "task_type", None) == "INTERVIEW":
                    if entity_name != active_interview_name:
                        logger.debug(
                            f"InteractRouter: Skipping {entity_name} "
                            f"(interview routing: active is {active_interview_name})"
                        )
                        continue

            # Get anchors: prefer dynamic (get_anchors hook) over static self.anchors
            dynamic_anchors = None
            try:
                dynamic_anchors = await action.get_anchors(conversation)
            except Exception as exc:
                logger.warning(
                    f"InteractRouter: get_anchors() failed for {entity_name}: {exc}"
                )

            anchors = (
                dynamic_anchors
                if dynamic_anchors is not None
                else getattr(action, "anchors", None)
            )
            description = getattr(action, "description", None)

            if anchors is None:
                context = (
                    getattr(action, "context", {}) if hasattr(action, "context") else {}
                )
                if isinstance(context, dict):
                    anchors = context.get("anchors", [])
                else:
                    anchors = []

            if (
                anchors
                and isinstance(anchors, list)
                and (description or len(anchors) > 0)
            ):
                if entity_name not in anchors_dict:
                    anchors_dict[entity_name] = []
                if description:
                    anchors_dict[entity_name].append(description)
                anchors_dict[entity_name].extend(anchors)
                logger.debug(
                    f"InteractRouter: Added {len(anchors)} anchors for {entity_name}"
                )

        logger.debug(
            f"InteractRouter: Collected anchors from {len(anchors_dict)} actions"
        )
        return anchors_dict
