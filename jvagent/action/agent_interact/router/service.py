"""Routing sub-service for ``AgentInteractAction`` (posture + route selection).

Uses the skill catalog plus enabled ``InteractAction`` handlers as the route
table; selected names become the walk path. Adapted from legacy
``InteractRouter`` patterns.
"""

import asyncio
import hashlib
import json
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set, Tuple

from jvagent.action.agent_interact.skill_handler.always_active import (
    always_active_from_skill_dir,
)
from jvagent.action.agent_interact.skill_handler.shim import AgentInteractVisitorShim
from jvagent.action.router.formatting import format_interaction_history
from jvagent.action.router.routing_result import (
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    RoutingResult,
    parse_routing_response,
)
from jvagent.core.app import App
from jvagent.core.cache import (
    get_interact_router_cache,
    interact_router_cache_key,
    set_interact_router_cache,
)

if TYPE_CHECKING:
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)

BUFFER_KEY = "deferred_fragments"


def _get_buffer(conversation: "Conversation") -> list:
    return list(conversation.context.get(BUFFER_KEY, []))


class AgentInteractRouter:
    """Routing engine embedded in ``AgentInteractAction``.

    Posture classification plus route (skill) selection in one fast LLM call,
    then canned responses and walk-path finalization.
    """

    def __init__(self, action: Any) -> None:
        self._action = action

    # ------------------------------------------------------------------
    # Properties forwarded from the owning action
    # ------------------------------------------------------------------

    @property
    def _router_model(self) -> str:
        return getattr(self._action, "router_model", "gpt-4o-mini")

    @property
    def _router_model_temperature(self) -> float:
        return getattr(self._action, "router_model_temperature", 0.1)

    @property
    def _router_model_max_tokens(self) -> int:
        return getattr(self._action, "router_model_max_tokens", 400)

    @property
    def _enable_canned_response(self) -> bool:
        return getattr(self._action, "enable_canned_response", True)

    @property
    def _canned_response_max_words(self) -> int:
        return getattr(self._action, "canned_response_max_words", 8)

    @property
    def _skip_canned_for_intents(self) -> List[str]:
        return getattr(
            self._action,
            "skip_canned_for_intents",
            ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
        )

    @property
    def _confidence_threshold(self) -> float:
        return getattr(self._action, "confidence_threshold", 0.7)

    @property
    def _enable_clarification(self) -> bool:
        return getattr(self._action, "enable_clarification", False)

    @property
    def _history_limit(self) -> int:
        return getattr(self._action, "history_limit", 3)

    @property
    def _enable_accumulation(self) -> bool:
        return getattr(self._action, "enable_accumulation", True)

    @property
    def _max_fragment_buffer(self) -> int:
        return getattr(self._action, "max_fragment_buffer", 5)

    @property
    def _enable_routing_cache(self) -> bool:
        return getattr(self._action, "enable_routing_cache", False)

    @property
    def _exceptions(self) -> List[str]:
        return getattr(self._action, "exceptions", [])

    @property
    def _pass_through_task_types(self) -> Sequence[str]:
        return getattr(self._action, "pass_through_task_types", ("INTERVIEW",))

    @property
    def _pass_through_when_media(self) -> bool:
        return getattr(self._action, "pass_through_when_media", True)

    @property
    def _media_bypass_actions(self) -> List[str]:
        return getattr(self._action, "media_bypass_actions", [])

    @property
    def _bypass_canned_response(self) -> str:
        return getattr(self._action, "bypass_canned_response", "One moment")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def route(self, visitor: Any) -> Tuple[str, Optional[RoutingResult]]:
        """Execute posture classification + skill selection.

        Returns (posture, routing_result).  On SUPPRESS/DEFER the walk path
        is already cleared and the caller should return immediately.
        """
        setattr(self._action, "_last_visitor", visitor)
        interaction = visitor.interaction
        if not interaction:
            logger.warning("AgentInteractRouter: No interaction available")
            return POSTURE_RESPOND, None

        if interaction.interpretation:
            logger.debug("AgentInteractRouter: Interaction already routed, skipping")
            return POSTURE_RESPOND, None

        try:
            agent = await self._action.get_agent()
            conversation = getattr(visitor, "conversation", None)
            if not agent:
                logger.error("AgentInteractRouter: Agent not found")
                return POSTURE_RESPOND, None

            dynamic_exceptions = await self._get_dynamic_exceptions(agent)
            combined_exceptions = list(set(self._exceptions + dynamic_exceptions))

            # ── Bypass: interview active ──
            if self._pass_through_task_types and conversation:
                active_tasks = conversation.get_active_tasks(status="active")
                for t in active_tasks:
                    if t.get("task_type") in self._pass_through_task_types:
                        action_name = t.get("action_name", "")
                        logger.debug(
                            "AgentInteractRouter: Bypass (active %s: %s)",
                            t.get("task_type"),
                            action_name,
                        )
                        result = RoutingResult(
                            posture=POSTURE_RESPOND,
                            interpretation="Bypass: active task",
                            intent_type="INTERACTIVE",
                            actions=[action_name] if action_name else [],
                            confidence=1.0,
                            canned_response=self._bypass_canned_response,
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
                            conversation,
                        )
                        return result.posture, result

            # ── Bypass: media attached ──
            if self._pass_through_when_media and self._media_bypass_actions:
                data = getattr(visitor, "data", None) or {}
                media_urls = data.get("image_urls") or data.get("whatsapp_media") or []
                if media_urls:
                    logger.debug(
                        "AgentInteractRouter: Bypass (media attached: %d items)",
                        len(media_urls),
                    )
                    result = RoutingResult(
                        posture=POSTURE_RESPOND,
                        interpretation="Bypass: media attached",
                        intent_type="INFORMATIONAL",
                        actions=list(self._media_bypass_actions),
                        confidence=1.0,
                        canned_response=self._bypass_canned_response,
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
                        conversation,
                    )
                    return result.posture, result

            model_action = await self._action.get_model_action(purpose="router")
            if not model_action:
                logger.error("AgentInteractRouter: Model action not found")
                return POSTURE_RESPOND, None

            # ── Collect skill + interact-action route tables ──
            if conversation:
                (
                    skill_descriptors,
                    route_action_descriptors,
                    interaction_history,
                ) = await asyncio.gather(
                    self._collect_skill_descriptors(agent, conversation),
                    self._collect_route_action_descriptors(agent),
                    conversation.get_interaction_history(
                        limit=self._history_limit,
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
                skill_descriptors, route_action_descriptors = await asyncio.gather(
                    self._collect_skill_descriptors(agent, None),
                    self._collect_route_action_descriptors(agent),
                )
                interaction_history = []

            if not skill_descriptors and not route_action_descriptors:
                logger.warning(
                    "AgentInteractRouter: No routes available for routing (session_id=%s)",
                    getattr(visitor, "session_id", None),
                )
                result = RoutingResult.error_result(
                    "No skills or interact actions available for routing",
                    interaction.utterance or "",
                )
                await self._finalize_routing(
                    visitor,
                    interaction,
                    agent,
                    result,
                    combined_exceptions,
                    conversation,
                )
                return result.posture, result

            # ── Cache lookup ──
            result = None
            cache_key = None
            if conversation and self._enable_routing_cache:
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
                active_tasks = conversation.get_active_tasks(status="active")
                active_task_fingerprint = (
                    hashlib.sha256(
                        json.dumps(
                            sorted([t.get("action_name", "") for t in active_tasks])
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
                    cache_key, caller_enabled=self._enable_routing_cache
                )
                if cached is not None:
                    result = RoutingResult.from_dict(cached)
                    result.actions = self._merge_and_validate_routes(
                        result.actions,
                        result.interact_actions,
                        skill_descriptors,
                        route_action_descriptors,
                    )
                    result.interact_actions = []

            if result is None:
                result = await self._route_direct(
                    interaction,
                    skill_descriptors,
                    route_action_descriptors,
                    interaction_history or [],
                    conversation,
                )
                if (
                    self._enable_routing_cache
                    and cache_key is not None
                    and result.confidence > 0
                ):
                    await set_interact_router_cache(
                        cache_key,
                        result.to_dict(),
                        caller_enabled=self._enable_routing_cache,
                    )

            interaction.response_posture = result.posture
            await interaction.save()

            if result.is_suppress():
                await self._handle_suppress(visitor)
                return result.posture, result

            if result.is_defer() and self._enable_accumulation:
                if conversation:
                    await self._handle_defer(visitor, interaction, conversation)
                else:
                    await visitor.set_walk_path([])
                return result.posture, result

            if result.is_respond() and conversation:
                await self._handle_respond(visitor, interaction, conversation)

            # ── Canned response ──
            await self._publish_canned_response(visitor, result)

            # ── Clarification ──
            result = await self._evaluate_confidence(result, visitor, interaction)

            # ── Finalize routing ──
            await self._finalize_routing(
                visitor, interaction, agent, result, combined_exceptions, conversation
            )

            return result.posture, result

        except Exception as e:
            logger.error(
                f"AgentInteractRouter: Error during routing: {e}", exc_info=True
            )
            return POSTURE_RESPOND, None

    # ------------------------------------------------------------------
    # Skill descriptor collection
    # ------------------------------------------------------------------

    async def _collect_skill_descriptors(
        self, agent: Any, conversation: Optional["Conversation"] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Collect skill descriptors for routing.

        Reads from the cached skill catalog (no disk I/O per call).
        Returns dict of skill_name -> {description, tags, plan_steps, always_active}.
        The converse fast-path skill is EXCLUDED — it's the default fallback.
        """
        actions_manager = await agent.get_actions_manager()
        if not actions_manager:
            return {}

        catalog = await self._get_cached_skill_catalog(agent, conversation)
        if not catalog or not catalog.skills:
            return {}

        descriptors: Dict[str, Dict[str, Any]] = {}
        for skill_name, skill_data in catalog.skills.items():
            d = skill_data.get("dir", "")
            descriptors[skill_name] = {
                "description": skill_data.get("description", ""),
                "tags": skill_data.get("metadata", {}).get("tags", []),
                "plan_steps": skill_data.get("plan_steps", []),
                "always_active": bool(skill_data.get("always_active", False))
                or (bool(d) and always_active_from_skill_dir(d)),
            }
        return descriptors

    async def _collect_route_action_descriptors(
        self, agent: Any
    ) -> Dict[str, Dict[str, Any]]:
        """Enabled ``InteractAction`` class names (excluding this action) for routing."""
        from jvagent.action.interact.base import InteractAction

        actions_manager = await agent.get_actions_manager()
        if not actions_manager:
            return {}

        self_name = self._action.get_class_name()
        descriptors: Dict[str, Dict[str, Any]] = {}
        for action in await actions_manager.get_actions(
            enabled_only=True, entity=InteractAction
        ):
            name = action.get_class_name()
            if name == self_name:
                continue
            descriptors[name] = {
                "kind": "interact_action",
                "description": getattr(action, "description", "") or "",
                "weight": getattr(action, "weight", 0),
            }
        return descriptors

    async def _get_cached_skill_catalog(
        self, agent: Any, conversation: Optional["Conversation"] = None
    ) -> Any:
        """Get or build the cached skill catalog for this agent."""
        from jvagent.action.skill.skill_catalog import SkillCatalog

        skill_state = (
            getattr(
                visitor := getattr(self._action, "_last_visitor", None),
                "_skill_state",
                None,
            )
            if hasattr(self._action, "_last_visitor")
            else None
        )
        if skill_state and skill_state.get("skill_catalog"):
            return skill_state["skill_catalog"]

        try:
            from jvagent.action.agent_interact.skill_handler.contracts import (
                SkillRunConfig,
            )

            cfg = SkillRunConfig(
                skills=getattr(self._action, "skills", None),
                skills_source=getattr(self._action, "skills_source", "both"),
                denied_skills=list(getattr(self._action, "denied_skills", [])),
            )

            agent_shim = AgentInteractVisitorShim(
                agent=agent,
                action_resolver=None,
                user_id=None,
                conversation=conversation,
                interaction=None,
                session_id=None,
                response_bus=None,
                channel=None,
            )

            catalog = await SkillCatalog.discover(
                visitor=agent_shim,
                skills_selector=cfg.skills,
                skills_source=cfg.skills_source,
                denied_skills=cfg.denied_skills or None,
            )
            return catalog
        except Exception as exc:
            logger.warning(
                "AgentInteractRouter: skill catalog discovery failed: %s", exc
            )
            return None

    # ------------------------------------------------------------------
    # Direct routing LLM call
    # ------------------------------------------------------------------

    async def _route_direct(
        self,
        interaction: "Interaction",
        skill_descriptors: Dict[str, Dict[str, Any]],
        route_action_descriptors: Dict[str, Dict[str, Any]],
        interaction_history: List[Dict[str, Any]],
        conversation: Optional["Conversation"] = None,
    ) -> RoutingResult:
        try:
            model_action = await self._action.get_model_action(
                required=True, purpose="router"
            )
            if not model_action:
                return RoutingResult.error_result(
                    "Could not get model action", interaction.utterance or ""
                )

            prompt = self._build_routing_prompt(
                utterance=interaction.utterance or "",
                skill_descriptors=skill_descriptors,
                route_action_descriptors=route_action_descriptors,
                interaction_history=interaction_history,
                conversation=conversation,
            )

            response = await model_action.generate(
                prompt=prompt,
                system=self._action.routing_system_prompt,
                temperature=self._router_model_temperature,
                max_tokens=self._router_model_max_tokens,
                model=self._router_model,
                calling_action_name=self._action.get_class_name(),
                interaction=interaction,
            )

            result = parse_routing_response(response)

            result.actions = self._merge_and_validate_routes(
                result.actions,
                result.interact_actions,
                skill_descriptors,
                route_action_descriptors,
            )
            result.interact_actions = []

            return result

        except Exception as e:
            logger.error(
                f"AgentInteractRouter: Direct routing failed: {e}", exc_info=True
            )
            return RoutingResult.error_result(str(e), interaction.utterance or "")

    def _build_routing_prompt(
        self,
        utterance: str,
        skill_descriptors: Dict[str, Dict[str, Any]],
        route_action_descriptors: Dict[str, Dict[str, Any]],
        interaction_history: List[Dict[str, Any]],
        conversation: Optional["Conversation"] = None,
    ) -> str:
        skills_json = json.dumps(skill_descriptors, indent=2)
        interact_actions_json = json.dumps(route_action_descriptors, indent=2)

        history_section = (
            format_interaction_history(interaction_history, conversation=conversation)
            if interaction_history
            else "(No previous conversation)"
        )

        active_tasks_section = ""
        if conversation:
            active_descriptions = conversation.get_active_tasks_for_context()
            if active_descriptions:
                task_lines = "\n".join(f"- {desc}" for desc in active_descriptions)
                active_tasks_section = f"ACTIVE TASKS:\n{task_lines}\n\n"

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
                prior_fragments_section = (
                    self._action.routing_prior_fragments_section.format(
                        fragments_list=fragments_list,
                    )
                )

        entity_field = ""
        canned_field = (
            ',\n  "canned_response": ""' if self._enable_canned_response else ""
        )
        optional_instructions = ""

        if self._enable_canned_response:
            skip_intents = ", ".join(self._skip_canned_for_intents)
            optional_instructions += (
                self._action.routing_canned_instructions_template.format(
                    max_words=self._canned_response_max_words,
                    skip_intents=skip_intents,
                )
            )

        prompt = self._action.routing_user_prompt_template.format(
            utterance=utterance,
            skills_json=skills_json,
            interact_actions_json=interact_actions_json,
            active_tasks_section=active_tasks_section,
            history_section=history_section,
            prior_fragments_section=prior_fragments_section,
            entity_field=entity_field,
            canned_field=canned_field,
            optional_instructions=optional_instructions,
        )
        return prompt

    # ------------------------------------------------------------------
    # Skill name resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_skill_names_to_keys(
        actions: List[str],
        skill_descriptors: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        """Resolve LLM-selected actions to valid skill names."""
        if not actions:
            return []

        valid_keys = set(skill_descriptors)

        resolved: List[str] = []
        for a in actions:
            a_str = str(a).strip() if a else ""
            if not a_str:
                continue
            if a_str in valid_keys:
                resolved.append(a_str)
            else:
                logger.debug(
                    "AgentInteractRouter: Dropping non-skill action '%s' (not in skill descriptors)",
                    a_str[:50],
                )

        return list(dict.fromkeys(resolved))

    def _merge_and_validate_routes(
        self,
        skill_names: List[str],
        interact_names: List[str],
        skill_descriptors: Dict[str, Dict[str, Any]],
        route_action_descriptors: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        """Keep valid skill keys and enabled interact action class names; dedupe order."""
        valid_ia: Set[str] = set(route_action_descriptors)
        resolved_skills = self._resolve_skill_names_to_keys(
            skill_names, skill_descriptors
        )
        out: List[str] = list(dict.fromkeys(resolved_skills))
        seen: Set[str] = set(out)

        for raw in interact_names:
            n = str(raw).strip() if raw else ""
            if n and n in valid_ia and n not in seen:
                seen.add(n)
                out.append(n)

        for raw in skill_names:
            n = str(raw).strip() if raw else ""
            if n and n in valid_ia and n not in seen:
                seen.add(n)
                out.append(n)

        return out

    # ------------------------------------------------------------------
    # Canned response
    # ------------------------------------------------------------------

    async def _publish_canned_response(
        self, visitor: Any, result: RoutingResult
    ) -> None:
        if not self._enable_canned_response:
            return

        if result.intent_type in self._skip_canned_for_intents:
            return

        canned = result.canned_response
        if not canned or not canned.strip():
            return

        interaction = visitor.interaction
        if not interaction:
            return

        if interaction.response:
            return

        try:
            await self._action.publish(visitor, canned.strip(), transient=True)
            interaction.canned_response = canned.strip()
            await interaction.save()
        except Exception as e:
            logger.warning(
                f"AgentInteractRouter: Failed to publish canned response: {e}"
            )

    # ------------------------------------------------------------------
    # Posture handlers
    # ------------------------------------------------------------------

    async def _handle_suppress(self, visitor: Any) -> None:
        await visitor.set_walk_path([])
        logger.info("AgentInteractRouter: SUPPRESS - cleared walk path, no response")

    async def _handle_defer(
        self,
        visitor: Any,
        interaction: "Interaction",
        conversation: "Conversation",
    ) -> None:
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
        if len(buffer) > self._max_fragment_buffer:
            buffer = buffer[-self._max_fragment_buffer :]
        await conversation.update_context({BUFFER_KEY: buffer})
        await visitor.set_walk_path([])
        logger.info(
            "AgentInteractRouter: DEFER - appended to buffer (%d fragments), no response",
            len(buffer),
        )

    async def _handle_respond(
        self,
        visitor: Any,
        interaction: "Interaction",
        conversation: "Conversation",
    ) -> None:
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
                    "AgentInteractRouter: RESPOND - injected directive with %d prior fragments",
                    len(fragments),
                )
            await conversation.update_context({BUFFER_KEY: []})

    # ------------------------------------------------------------------
    # Confidence / clarification
    # ------------------------------------------------------------------

    async def _evaluate_confidence(
        self,
        result: RoutingResult,
        visitor: Any,
        interaction: "Interaction",
    ) -> RoutingResult:
        if not result.should_clarify(self._confidence_threshold):
            return result

        issues = result.verification.issues_found if result.verification else []
        logger.info(
            "AgentInteractRouter: Low confidence (%.2f < %.2f), issues: %s",
            result.confidence,
            self._confidence_threshold,
            issues,
        )

        if self._enable_clarification:
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
                    await self._action.publish(visitor, clarification, stream=False)
                except Exception as e:
                    logger.warning(
                        "AgentInteractRouter: Failed to publish clarification: %s", e
                    )

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
        issues_text = ", ".join(str(i) for i in issues) if issues else "(none)"

        user_tpl = (
            self._action.routing_clarification_user_prompt_template or ""
        ).strip()
        if user_tpl:
            try:
                model_action = await self._action.get_model_action(purpose="router")
                if model_action:
                    primary_prompt = user_tpl.format(
                        utterance=utterance,
                        interpretation=interpretation,
                        intent_type=intent_type,
                        confidence=confidence,
                        issues=issues_text,
                    )
                    clarification = await model_action.generate(
                        prompt=primary_prompt,
                        temperature=0.7,
                        max_tokens=150,
                        model=self._router_model,
                        calling_action_name=(
                            f"{self._action.get_class_name()}_clarification_primary"
                        ),
                        interaction=interaction,
                    )
                    if clarification and clarification.strip():
                        return clarification.strip()
            except Exception as e:
                logger.warning(
                    "AgentInteractRouter: Primary clarification prompt failed: %s", e
                )

        fallbacks = self._action.routing_clarification_fallback_messages
        if not fallbacks:
            return ""
        template = random.choice(fallbacks)
        try:
            model_action = await self._action.get_model_action(purpose="router")
            if model_action:
                prompt = self._action.routing_clarification_paraphrase_prompt_template.format(
                    utterance=utterance,
                    template=template,
                )
                clarification = await model_action.generate(
                    prompt=prompt,
                    temperature=0.7,
                    max_tokens=100,
                    model=self._router_model,
                    calling_action_name=f"{self._action.get_class_name()}_clarification",
                    interaction=interaction,
                )
                if clarification and clarification.strip():
                    return clarification.strip()
        except Exception as e:
            logger.warning(
                "AgentInteractRouter: Paraphrase failed, using template: %s", e
            )
        return template

    # ------------------------------------------------------------------
    # Finalize routing
    # ------------------------------------------------------------------

    async def _finalize_routing(
        self,
        visitor: Any,
        interaction: "Interaction",
        agent: Any,
        result: RoutingResult,
        combined_exceptions: List[str],
        conversation: Optional["Conversation"] = None,
    ) -> None:
        routed_skills = result.actions
        all_allowed = list(set(routed_skills + combined_exceptions))

        if conversation:
            active_task = conversation.get_active_task(
                task_type="INTERVIEW", status="active"
            )
            active_interview_name = (
                active_task.get("action_name") if active_task else None
            )
            if active_interview_name:
                actions_manager = await agent.get_actions_manager()
                if actions_manager:
                    from jvagent.action.interact.base import InteractAction

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

        await self._store_routing_result(
            interaction,
            interpretation=result.interpretation,
            actions=all_allowed,
            intent_type=result.intent_type,
        )

        logger.info(
            "AgentInteractRouter: intent_type=%s, confidence=%.2f, routed to %d skills (+ %d exceptions)",
            result.intent_type,
            result.confidence,
            len(routed_skills),
            len(combined_exceptions),
        )

        await self._update_walk_path(visitor, agent, all_allowed)

    async def _store_routing_result(
        self,
        interaction: "Interaction",
        interpretation: str,
        actions: List[str],
        intent_type: str,
    ) -> None:
        interaction.interpretation = interpretation
        interaction.anchors = actions
        interaction.intent_type = intent_type
        await interaction.save()

    async def _update_walk_path(
        self,
        visitor: Any,
        agent: Any,
        allowed_actions: List[str],
    ) -> None:
        actions_manager = await agent.get_actions_manager()
        if not actions_manager:
            return

        from jvagent.action.interact.base import InteractAction

        all_enabled_actions = await actions_manager.get_actions(
            enabled_only=True, entity=InteractAction
        )

        allowed_set = set(allowed_actions)
        filtered_actions = [
            action
            for action in all_enabled_actions
            if action.get_class_name() in allowed_set
        ]
        filtered_actions = sorted(filtered_actions, key=lambda a: a.weight)

        curated = await visitor.curate_walk_path(filtered_actions)
        logger.info(
            "AgentInteractRouter: Updated walk path with %d actions", len(curated)
        )

    async def _get_dynamic_exceptions(self, agent: Any) -> List[str]:
        actions_manager = await agent.get_actions_manager()
        if not actions_manager:
            return []

        from jvagent.action.interact.base import InteractAction

        all_interact_actions = await actions_manager.get_all_actions(
            enabled_only=True, entity=InteractAction
        )
        return [
            a.get_class_name()
            for a in all_interact_actions
            if getattr(a, "always_execute", False)
        ]
