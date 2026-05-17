"""CockpitRouter: lightweight pre-cockpit posture classification + skill selection."""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.cockpit.catalog.skill_catalog import SkillCatalog
from jvagent.action.cockpit.catalog.skill_discovery import always_active_from_skill_dir
from jvagent.action.cockpit.registry.shim import CockpitVisitorShim
from jvagent.action.cockpit.routing.prompts import (
    ROUTING_CANNED_INSTRUCTIONS_TEMPLATE,
    ROUTING_CLARIFICATION_FALLBACK_MESSAGES,
    ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE,
    ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE,
    ROUTING_SYSTEM_PROMPT,
    ROUTING_USER_PROMPT_TEMPLATE,
)
from jvagent.action.cockpit.routing.types import (
    POSTURE_RESPOND,
    RoutingResult,
    format_interaction_history,
    parse_routing_response,
)
from jvagent.core.cache import (
    get_interact_router_cache,
    interact_router_cache_key,
    set_interact_router_cache,
)

logger = logging.getLogger(__name__)


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

        # Local pre-classifier — cheap heuristic that fires BEFORE any
        # agent / model resolution. When the utterance is unambiguous
        # smalltalk and no active task is in flight, we synthesise a
        # converse-skill route and skip the router LLM call entirely.
        # See ``preclassifier.py`` for the rules and bucket definitions.
        if getattr(self._action, "enable_router_preclassifier", True):
            from jvagent.action.cockpit.routing.preclassifier import maybe_preclassify

            preclassified = maybe_preclassify(
                visitor,
                interaction.utterance or "",
                enabled=True,
            )
            if preclassified is not None:
                interaction.response_posture = preclassified.posture
                interp = (preclassified.interpretation or "").strip()
                if interp and not getattr(interaction, "interpretation", ""):
                    try:
                        interaction.interpretation = interp
                    except Exception:
                        pass
                try:
                    await interaction.save()
                except Exception as exc:
                    logger.debug(
                        "CockpitRouter: preclassifier interaction.save failed: %s",
                        exc,
                    )
                logger.debug(
                    "CockpitRouter: pre-classifier short-circuit (%s) — skipping LLM",
                    preclassified.raw_response,
                )
                return preclassified.posture, preclassified

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
                (
                    skill_descriptors,
                    interact_action_descriptors,
                    interaction_history,
                ) = await asyncio_gather_router(
                    self._collect_skill_descriptors(agent, conversation),
                    self._collect_interact_action_descriptors(),
                    conversation.get_interaction_history(
                        limit=getattr(self._action, "history_limit", 3),
                        excluded=interaction.id,
                        with_utterance=True,
                        with_response=True,
                        with_interpretation=True,
                        with_event=True,
                        with_posture=True,
                        formatted=False,
                        max_statement_length=getattr(
                            self._action, "max_statement_length", None
                        ),
                    ),
                )
            else:
                skill_descriptors = await self._collect_skill_descriptors(agent, None)
                interact_action_descriptors = (
                    await self._collect_interact_action_descriptors()
                )
                interaction_history = []

            if not skill_descriptors and not interact_action_descriptors:
                logger.warning("CockpitRouter: no routes available")
                result = RoutingResult.error_result(
                    "No skills or interact_actions available for routing",
                    interaction.utterance or "",
                )
                return result.posture, result

            result = await self._run_llm_route(
                interaction,
                skill_descriptors,
                interact_action_descriptors,
                interaction_history or [],
                conversation,
            )

            interaction.response_posture = result.posture
            # Persist the router's interpretation so downstream stages
            # (engine pre-dispatch with ``source: interpretation``, audit
            # tooling, history rendering) can read it without re-running
            # the router. Empty strings are tolerated.
            interp = getattr(result, "interpretation", "") or ""
            if interp and not getattr(interaction, "interpretation", ""):
                try:
                    interaction.interpretation = interp.strip()
                except Exception:
                    pass
            await interaction.save()

            if result.is_suppress():
                return result.posture, result

            if result.is_defer() and getattr(self._action, "enable_accumulation", True):
                return result.posture, result

            # Publish canned lead-in (LLM-generated by the routing call) before
            # the engine runs. The strict lead-in-only rules in
            # ``ROUTING_CANNED_INSTRUCTIONS_TEMPLATE`` keep this fragmentary
            # and language-matched.
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
        interact_action_descriptors: Dict[str, Dict[str, Any]],
        interaction_history: List[Dict[str, Any]],
        conversation: Any,
    ) -> RoutingResult:
        # Cache check — skip LLM call when an identical (utterance,
        # active-task fingerprint) pair was routed within the cache TTL.
        # Operator-controlled by ``enable_interact_router_cache`` perf knob;
        # default off. Hits return a re-validated RoutingResult; misses
        # fall through to the LLM call below and write the result back.
        cache_enabled = bool(
            getattr(self._action, "enable_interact_router_cache", False)
        )
        cache_key = (
            self._build_cache_key(interaction, conversation) if cache_enabled else None
        )
        if cache_key:
            cached = await get_interact_router_cache(cache_key, caller_enabled=True)
            if cached:
                cached_result = self._restore_cached_routing_result(
                    cached, skill_descriptors, interact_action_descriptors
                )
                if cached_result is not None:
                    logger.debug("CockpitRouter: cache hit (key=%s…)", cache_key[:12])
                    return cached_result

        model_action = await self._action.get_model_action(
            required=True, purpose="router"
        )
        skills_json = json.dumps(skill_descriptors, indent=2)
        interact_actions_json = json.dumps(interact_action_descriptors, indent=2)
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
            persona_tone_hint = await self._build_persona_tone_hint()
            optional_instructions += ROUTING_CANNED_INSTRUCTIONS_TEMPLATE.format(
                max_words=self._canned_response_max_words,
                skip_intents=skip_intents,
                persona_tone_hint=persona_tone_hint,
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

        active_tasks_section = self._build_active_tasks_section()

        routing_user_template = getattr(
            self._action, "routing_user_prompt_template", ROUTING_USER_PROMPT_TEMPLATE
        )
        prompt = routing_user_template.format(
            utterance=interaction.utterance or "",
            skills_json=skills_json,
            interact_actions_json=interact_actions_json,
            active_tasks_section=active_tasks_section,
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
        result.interact_actions = self._validate_routes(
            result.interact_actions, interact_action_descriptors
        )
        # Promote ``converse`` to a structural skill route. ``parse_routing_response``
        # clears actions on CONVERSATIONAL intent; if the catalog has ``converse``
        # we inject it so the dispatch is skill-driven instead of intent-driven.
        # Cockpit's gate checks for ``actions == ["converse"]`` and takes the
        # persona-only fast path.
        from jvagent.action.cockpit.delivery.gates import CONVERSE_SKILL_NAMES

        if (
            result.intent_type == "CONVERSATIONAL"
            and not result.actions
            and not result.interact_actions
        ):
            for converse_name in CONVERSE_SKILL_NAMES:
                if converse_name in skill_descriptors:
                    result.actions = [converse_name]
                    break

        # Cache write — store the validated, post-injection result so a
        # repeat utterance within the TTL window skips the LLM round-trip.
        if cache_key:
            try:
                await set_interact_router_cache(
                    cache_key, result.to_dict(), caller_enabled=True
                )
            except Exception as exc:
                logger.debug("CockpitRouter: cache write failed: %s", exc)

        return result

    def _validate_routes(
        self, actions: List[str], descriptors: Dict[str, Dict[str, Any]]
    ) -> List[str]:
        return [a for a in actions if a in descriptors]

    async def _collect_interact_action_descriptors(self) -> Dict[str, Dict[str, Any]]:
        """Build descriptor map for routable InteractActions on the agent.

        Excludes:
        - The cockpit action itself (cannot delegate to self).
        - InteractActions with ``always_execute=True`` (they run regardless of routing).

        Each entry: ``{"description": "...", "weight": int}``. The class name is the
        key, so the router can return exact ``interact_actions`` array entries.
        """
        agent = await self._action.get_agent()
        if agent is None:
            return {}

        try:
            from jvagent.action.interact.base import InteractAction

            actions_mgr = await agent.get_actions_manager()
            if actions_mgr is None:
                return {}

            all_actions = await actions_mgr.get_all_actions(enabled_only=True)
        except Exception as exc:
            logger.debug("CockpitRouter: interact action enumeration failed: %s", exc)
            return {}

        cockpit_class = self._action.__class__.__name__
        descriptors: Dict[str, Dict[str, Any]] = {}
        for action in all_actions:
            try:
                if not isinstance(action, InteractAction):
                    continue
                cls_name = action.__class__.__name__
                if cls_name == cockpit_class:
                    continue
                if bool(getattr(action, "always_execute", False)):
                    continue
                description = (
                    getattr(action, "description", None)
                    or action.__class__.__doc__
                    or ""
                ).strip()
                # Trim long docstrings so the router prompt stays small.
                short_desc = " ".join(description.split())[:240]
                descriptors[cls_name] = {
                    "description": short_desc,
                    "weight": int(getattr(action, "weight", 0)),
                }
            except Exception:
                continue
        return descriptors

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

    async def _build_persona_tone_hint(self) -> str:
        """Build a short tonal hint to splice into the canned-response prompt.

        Returns either an empty string (no persona context available) or a
        leading-space clause like `` (Tonally match the agent persona: …)``
        that splices cleanly into the canned-response instructions.
        """
        try:
            persona = await self._action.get_action("PersonaAction")
            if not persona or not getattr(persona, "enabled", True):
                return ""
            name = (getattr(persona, "persona_name", "") or "").strip()
            desc = (getattr(persona, "persona_description", "") or "").strip()
            if not desc:
                return ""
            short = " ".join(desc.split())[:200]
            tag = f"{name} — {short}" if name else short
            return f' (Tonally match the agent persona: "{tag}".)'
        except Exception:
            return ""

    def _build_cache_key(self, interaction: Any, conversation: Any) -> Optional[str]:
        """Build the router cache key for the current routing call.

        Returns None when there's not enough context (no conversation, no
        utterance) — caller treats None as "skip cache". Active tasks are
        folded into the fingerprint so a fragment routed when an interview
        is in flight gets a different key than the same fragment after the
        interview completes.
        """
        if conversation is None:
            return None
        utterance = (interaction.utterance or "").strip()
        if not utterance:
            return None
        conv_id = getattr(conversation, "id", "") or ""
        if not conv_id:
            return None
        active_fp = ""
        try:
            store = self._visitor.tasks
            active = store.list(status="active")
            parts: List[str] = []
            for handle in active:
                owner = (handle.owner_action or "").strip()
                state = ""
                data = handle.data or {}
                if isinstance(data, dict):
                    state = str(data.get("state") or "").strip()
                if owner:
                    parts.append(f"{owner}:{state}")
            active_fp = ",".join(sorted(parts))
        except Exception:
            active_fp = ""
        # Include user_id so the cache cannot bleed routing decisions
        # across users (AUDIT-interact-cockpit HIGH-04).
        user_id = ""
        try:
            user_id = str(getattr(self._visitor, "user_id", "") or "")
        except Exception:
            user_id = ""
        return interact_router_cache_key(
            conversation_id=conv_id,
            utterance=utterance,
            last_interaction_ids=(),
            buffer_fingerprint="",
            active_task_fingerprint=active_fp,
            user_id=user_id,
        )

    def _restore_cached_routing_result(
        self,
        cached: Dict[str, Any],
        skill_descriptors: Dict[str, Dict[str, Any]],
        interact_action_descriptors: Dict[str, Dict[str, Any]],
    ) -> Optional[RoutingResult]:
        """Rebuild a RoutingResult from a cached dict, re-validating routes.

        Re-validation guards against catalog drift between cache write and
        read (a skill removed from the agent's selector, an interact_action
        disabled, etc.). Returns None if reconstruction fails — caller
        falls through to the live LLM path.
        """
        try:
            result = RoutingResult.from_dict(
                cached, raw_response=cached.get("raw_response", "<cache>")
            )
        except Exception as exc:
            logger.debug("CockpitRouter: cache deserialise failed: %s", exc)
            return None
        result.actions = self._validate_routes(result.actions, skill_descriptors)
        result.interact_actions = self._validate_routes(
            result.interact_actions, interact_action_descriptors
        )
        return result

    def _build_active_tasks_section(self) -> str:
        """Render active tasks on the conversation for the routing prompt.

        Surfaces every task with status ``active`` on the current conversation,
        grouped by ``owner_action``. The router uses this to:

        - Route fragments (``"Yes"``, ``"No"``, single tokens) back to the
          owning interact_action when an interview / multi-step flow is in
          progress.
        - Avoid spawning parallel handlers for the same flow type
          (e.g. don't pick ``FeedbackInterviewInteractAction`` while a
          ``ReportInterviewInteractAction`` is already active).

        Returns an empty string when there's no visitor / conversation, no
        TaskStore, or no active tasks — keeps the prompt clean for fresh
        conversations.
        """
        visitor = self._visitor
        if visitor is None or getattr(visitor, "conversation", None) is None:
            return ""
        try:
            store = visitor.tasks
        except Exception:
            return ""
        try:
            active = store.list(status="active")
        except Exception as exc:
            logger.debug("CockpitRouter: tasks.list failed: %s", exc)
            return ""
        if not active:
            return ""

        # Group by owner_action so the model sees one entry per ongoing flow.
        # Multiple tasks under the same owner are unusual but can happen
        # if dedup didn't fire — collapse to one line so the prompt isn't
        # noisy.
        seen_owners: Dict[str, Dict[str, Any]] = {}
        for handle in active:
            owner = (handle.owner_action or "").strip() or "(unspecified)"
            if owner in seen_owners:
                continue
            seen_owners[owner] = {
                "id": handle.id,
                "title": (handle.title or "").strip(),
                "task_type": handle.task_type or "",
                "data": handle.data or {},
            }

        lines = [
            "ACTIVE TASKS (the user is mid-flow on these — fragments and"
            " short answers should route back to the owning handler):"
        ]
        for owner, info in seen_owners.items():
            data = info.get("data") or {}
            state = ""
            if isinstance(data, dict):
                state = str(data.get("state") or "").strip()
            type_label = info.get("task_type") or ""
            type_part = f" [{type_label}]" if type_label else ""
            state_part = f" (state: {state})" if state else ""
            lines.append(f"- owner_action={owner}{type_part}{state_part}")

        lines.append(
            "Routing rule: if the current message is a fragment / short reply "
            "(yes/no/value) and an owner_action above matches a listed "
            "INTERACT ACTION, prefer that owner over starting a parallel one."
        )
        return "\n".join(lines) + "\n\n"

    async def _build_capability_search_section(self, utterance: str) -> str:
        """Run a unified cockpit_search across skills + interact_actions + tools.

        Used only when ``router_use_cockpit_search`` is enabled. Returns a
        prompt-ready section to splice into the routing user prompt; empty
        string on any failure.
        """
        if not utterance:
            return ""
        try:
            from jvagent.action.cockpit.tools.search import search_for_router

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
