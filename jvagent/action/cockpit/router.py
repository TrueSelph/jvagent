"""CockpitRouter: lightweight pre-cockpit posture classification + skill selection.

Self-contained — imports only from core modules (no agent_interact dependency).
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.cockpit.routing_types import (
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
    RoutingResult,
    format_interaction_history,
    parse_routing_response,
)
from jvagent.action.cockpit.shim import CockpitVisitorShim
from jvagent.action.cockpit.skill_catalog import SkillCatalog
from jvagent.action.cockpit.skill_discovery import always_active_from_skill_dir
from jvagent.core.cache import (
    get_interact_router_cache,
    interact_router_cache_key,
    set_interact_router_cache,
)

logger = logging.getLogger(__name__)

ROUTING_SYSTEM_PROMPT = """\
You are a routing classifier. Analyze the user's message and determine:
1. The posture: RESPOND (handle normally), SUPPRESS (ignore silently), or DEFER (accumulate for later).
2. The intent type: CONVERSATIONAL, INFORMATIONAL, DIRECTIVE, INTERACTIVE, or UNCLEAR.
3. The interpretation: a brief summary of what the user wants.
4. The recommended skills from the available skills list.

Respond ONLY with a JSON object:
{
  "posture": "RESPOND|SUPPRESS|DEFER",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|DIRECTIVE|INTERACTIVE|UNCLEAR",
  "interpretation": "brief summary",
  "skills": ["skill_name_1", "skill_name_2"],
  "confidence": 0.0-1.0,
  "canned_response": "brief acknowledgement if appropriate"
}
"""

ROUTING_USER_PROMPT_TEMPLATE = """\
User message: {utterance}

Available skills:
{skills_json}

{history_section}
{entity_field}
{canned_field}
{optional_instructions}
{prior_fragments_section}

Classify the intent and recommend skills.
"""

ROUTING_CANNED_INSTRUCTIONS_TEMPLATE = """\

If the intent is suitable for a brief canned response (greetings, simple questions), provide one in the "canned_response" field. Keep it under {max_words} words. Skip canned responses for: {skip_intents}.
"""

ROUTING_CLARIFICATION_FALLBACK_MESSAGES = [
    "Could you tell me more about what you need?",
    "I'd like to help — could you rephrase that?",
    "Can you provide more details about your request?",
]

ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE = """\
The user said: "{utterance}"
Our initial interpretation: {interpretation}
Intent type: {intent_type}
Confidence: {confidence}
Issues: {issues}

Please provide a clarification question to ask the user.
"""

ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE = """\
Rephrase this clarification question naturally and concisely: "{template}"
"""

ROUTING_PRIOR_FRAGMENTS_SECTION = ""


class CockpitRouter:
    """Lightweight pre-cockpit router: posture classification + skill selection."""

    def __init__(self, action: Any) -> None:
        self._action = action
        self._visitor: Any = None

    async def route(self, visitor: Any) -> Tuple[str, Optional[RoutingResult]]:
        self._visitor = visitor
        interaction = visitor.interaction

        if not interaction:
            logger.warning("CockpitRouter: no interaction available")
            return POSTURE_RESPOND, None

        if interaction.interpretation:
            logger.debug("CockpitRouter: already routed, skipping")
            return POSTURE_RESPOND, None

        try:
            agent = await self._action.get_agent()
            conversation = getattr(visitor, "conversation", None)
            if not agent:
                return POSTURE_RESPOND, None

            model_action = await self._action.get_model_action(purpose="router")
            if not model_action:
                logger.error("CockpitRouter: model action not found")
                return POSTURE_RESPOND, None

            if conversation:
                skill_descriptors, interaction_history = await asyncio_gather_router(
                    self._collect_skill_descriptors(agent, conversation),
                    conversation.get_interaction_history(
                        limit=getattr(self._action, "history_limit", 3),
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
                skill_descriptors = await self._collect_skill_descriptors(agent, None)
                interaction_history = []

            if not skill_descriptors:
                logger.warning("CockpitRouter: no routes available")
                result = RoutingResult.error_result(
                    "No skills available for routing", interaction.utterance or ""
                )
                return result.posture, result

            result = await self._run_llm_route(
                interaction,
                skill_descriptors,
                interaction_history or [],
                conversation,
            )

            interaction.response_posture = result.posture
            await interaction.save()

            if result.is_suppress():
                return result.posture, result

            if result.is_defer() and getattr(self._action, "enable_accumulation", True):
                return result.posture, result

            # Canned response publishing
            canned = result.canned_response
            if (
                self._enable_canned_response
                and canned
                and canned.strip()
                and result.intent_type not in self._skip_canned_for_intents
            ):
                try:
                    await self._action.publish(visitor, canned.strip(), transient=True)
                    interaction.canned_response = canned.strip()
                    await interaction.save()
                except Exception as e:
                    logger.warning(
                        "CockpitRouter: failed to publish canned response: %s", e
                    )

            return result.posture, result

        except Exception as exc:
            logger.error("CockpitRouter: error during routing: %s", exc, exc_info=True)
            return POSTURE_RESPOND, None

    async def _run_llm_route(
        self,
        interaction: Any,
        skill_descriptors: Dict[str, Dict[str, Any]],
        interaction_history: List[Dict[str, Any]],
        conversation: Any,
    ) -> RoutingResult:
        model_action = await self._action.get_model_action(
            required=True, purpose="router"
        )
        skills_json = json.dumps(skill_descriptors, indent=2)
        history_section = (
            format_interaction_history(interaction_history, conversation=conversation)
            if interaction_history
            else "(No previous conversation)"
        )

        optional_instructions = ""
        canned_field = ""
        if self._enable_canned_response:
            canned_field = ',\n  "canned_response": ""'
            skip_intents = ", ".join(self._skip_canned_for_intents)
            optional_instructions += ROUTING_CANNED_INSTRUCTIONS_TEMPLATE.format(
                max_words=self._canned_response_max_words,
                skip_intents=skip_intents,
            )

        # Optional: enrich prompt with cockpit_search results (skills + interact_actions + tools).
        # Off by default (latency-sensitive); enable via router_use_cockpit_search.
        if getattr(self._action, "router_use_cockpit_search", False):
            try:
                capability_section = await self._build_capability_search_section(
                    interaction.utterance or ""
                )
                if capability_section:
                    optional_instructions += "\n\n" + capability_section
            except Exception as exc:
                logger.debug("CockpitRouter: cockpit_search enrich failed: %s", exc)

        routing_user_template = getattr(
            self._action, "routing_user_prompt_template", ROUTING_USER_PROMPT_TEMPLATE
        )
        prompt = routing_user_template.format(
            utterance=interaction.utterance or "",
            skills_json=skills_json,
            interact_actions_json="{}",
            active_tasks_section="",
            history_section=history_section,
            prior_fragments_section="",
            entity_field="",
            canned_field=canned_field,
            optional_instructions=optional_instructions,
        )

        response = await model_action.generate(
            prompt=prompt,
            system=getattr(
                self._action, "routing_system_prompt", ROUTING_SYSTEM_PROMPT
            ),
            temperature=getattr(self._action, "router_model_temperature", 0.1),
            max_tokens=getattr(self._action, "router_model_max_tokens", 400),
            model=getattr(self._action, "router_model", "gpt-4o-mini"),
            calling_action_name=self._action.get_class_name(),
            interaction=interaction,
        )

        result = parse_routing_response(response)
        result.actions = self._validate_routes(result.actions, skill_descriptors)
        return result

    def _validate_routes(
        self, actions: List[str], skill_descriptors: Dict[str, Dict[str, Any]]
    ) -> List[str]:
        return [a for a in actions if a in skill_descriptors]

    async def _collect_skill_descriptors(
        self, agent: Any, conversation: Any = None
    ) -> Dict[str, Dict[str, Any]]:
        catalog = await self._get_cached_catalog(agent, conversation)
        if not catalog or not catalog.skills:
            return {}

        descriptors: Dict[str, Dict[str, Any]] = {}
        for skill_name, skill_data in catalog.skills.items():
            scope_hint = str(skill_data.get("scope_hint") or "").strip()
            description = str(skill_data.get("description") or "").strip()
            descriptors[skill_name] = {
                "description": (
                    f"{description} (scope: {scope_hint})"
                    if scope_hint
                    else description
                ),
                "tags": skill_data.get("metadata", {}).get("tags", []),
                "plan_steps": skill_data.get("plan_steps", []),
                "always_active": bool(skill_data.get("always_active", False))
                or bool(always_active_from_skill_dir(skill_data.get("dir", ""))),
            }
        return descriptors

    async def _build_capability_search_section(self, utterance: str) -> str:
        """Run a unified cockpit_search across skills + interact_actions + tools.

        Used only when ``router_use_cockpit_search`` is enabled. Returns a
        prompt-ready section to splice into the routing user prompt; empty
        string on any failure.
        """
        if not utterance:
            return ""
        try:
            from jvagent.action.cockpit.search_tools import search_for_router

            agent = await self._action.get_agent()
            conversation = getattr(self._visitor, "conversation", None)
            shim = CockpitVisitorShim(
                agent=agent,
                action_resolver=None,
                user_id=None,
                conversation=conversation,
                interaction=None,
                session_id=None,
                response_bus=None,
                channel=None,
            )
            shim._agent = agent  # search_for_router reads ctx.agent
            catalog = await self._get_cached_catalog(agent, conversation)
            output = await search_for_router(
                agent=agent,
                visitor_shim=shim,
                catalog=catalog,
                query=utterance,
                limit=5,
            )
            output = (output or "").strip()
            if not output:
                return ""
            return (
                "Capability search (for context only — recommend skills as usual):\n"
                + output
            )
        except Exception as exc:
            logger.debug("CockpitRouter: capability search section failed: %s", exc)
            return ""

    async def _get_cached_catalog(self, agent: Any, conversation: Any = None) -> Any:
        skill_state = (
            getattr(self._action, "_skill_state", None)
            or getattr(self._visitor, "_skill_state", None)
            or {}
        )
        catalog = skill_state.get("skill_catalog")
        if catalog is not None and isinstance(catalog, SkillCatalog):
            return catalog

        try:
            agent_shim = CockpitVisitorShim(
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
                skills_selector=getattr(self._action, "skills", None),
                skills_source=getattr(self._action, "skills_source", "both"),
                denied_skills=list(getattr(self._action, "denied_skills", [])),
            )
            return catalog
        except Exception as exc:
            logger.warning("CockpitRouter: catalog discovery failed: %s", exc)
            return None

    @property
    def _enable_canned_response(self) -> bool:
        return getattr(self._action, "enable_canned_response", True)

    @property
    def _skip_canned_for_intents(self) -> List[str]:
        return getattr(
            self._action,
            "skip_canned_for_intents",
            ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
        )

    @property
    def _canned_response_max_words(self) -> int:
        return getattr(self._action, "canned_response_max_words", 8)


async def asyncio_gather_router(*args: Any) -> Any:
    import asyncio

    return await asyncio.gather(*args)
