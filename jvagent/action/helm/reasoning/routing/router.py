"""EngineRouter: unified capability selection (ADR-0008).

The router presents a single ``CAPABILITIES AVAILABLE`` catalog to the model
(skills + routable interact_actions), asks the model to pick by name, then
decodes ``kind`` from the registry to produce a :class:`RoutingResult` whose
``selected`` list is downstream-authoritative.

The router catalog excludes:

- Always-execute IAs (``always_execute=True``) — scheduled by Bridge's
  walker queue.
- Chain-internal IAs (``manifest.routable_by_anchor=False``) — reachable
  only via explicit DELEGATE from a parent IA.
- The orchestrator itself (Bridge) — guard against recursion.

Posture is removed: ReflexHelm gates SUPPRESS/DEFER upstream before any turn
reaches this router.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.helm.reasoning.catalog.skill_catalog import SkillCatalog
from jvagent.action.helm.reasoning.catalog.skill_discovery import (
    always_active_from_skill_dir,
)
from jvagent.action.helm.reasoning.registry.shim import EngineVisitorShim
from jvagent.action.helm.reasoning.routing.prompts import (
    build_routing_system_prompt,
    build_routing_user_prompt_template,
)
from jvagent.action.helm.reasoning.routing.types import (
    CapabilityRef,
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


class EngineRouter:
    """Unified-catalog router for ReasoningHelm (ADR-0008)."""

    def __init__(self, action: Any) -> None:
        self._action = action
        self._visitor: Any = None

    async def route(self, visitor: Any) -> Tuple[None, Optional[RoutingResult]]:
        """Run the router LLM call (or hit the cache) and return the parsed result.

        The first tuple element is retained for signature compatibility with
        the legacy ``(posture, result)`` shape — it is always ``None`` under
        ADR-0008 because posture has been removed.
        """
        self._visitor = visitor
        interaction = visitor.interaction

        if not interaction:
            logger.warning("EngineRouter: no interaction available")
            return None, None

        if interaction.interpretation:
            logger.debug("EngineRouter: already routed, skipping")
            return None, None

        try:
            agent = await self._action.get_agent()
            conversation = getattr(visitor, "conversation", None)
            if not agent:
                return None, None

            model_action = await self._action.get_model_action(purpose="router")
            if not model_action:
                logger.error("EngineRouter: model action not found")
                return None, None

            if conversation:
                (
                    skill_descriptors,
                    ia_descriptors,
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
                ia_descriptors = await self._collect_interact_action_descriptors()
                interaction_history = []

            if not skill_descriptors and not ia_descriptors:
                logger.warning("EngineRouter: no capabilities available")
                result = RoutingResult.error_result(
                    "No capabilities available for routing",
                    interaction.utterance or "",
                )
                return None, result

            capability_catalog = self._build_unified_catalog(
                skill_descriptors, ia_descriptors
            )

            result = await self._run_llm_route(
                interaction,
                skill_descriptors,
                ia_descriptors,
                capability_catalog,
                interaction_history or [],
                conversation,
            )

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

            return None, result

        except Exception as exc:
            logger.error("EngineRouter: error during routing: %s", exc, exc_info=True)
            return None, None

    async def _run_llm_route(
        self,
        interaction: Any,
        skill_descriptors: Dict[str, Dict[str, Any]],
        ia_descriptors: Dict[str, Dict[str, Any]],
        capability_catalog: Dict[str, Dict[str, Any]],
        interaction_history: List[Dict[str, Any]],
        conversation: Any,
    ) -> RoutingResult:
        # Cache check — skip LLM call when an identical (utterance,
        # active-task fingerprint) pair was routed within the cache TTL.
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
                    cached, skill_descriptors, ia_descriptors
                )
                if cached_result is not None:
                    logger.debug("EngineRouter: cache hit (key=%s…)", cache_key[:12])
                    return cached_result

        model_action = await self._action.get_model_action(
            required=True, purpose="router"
        )
        capabilities_json = json.dumps(capability_catalog, indent=2)
        history_section = (
            format_interaction_history(interaction_history, conversation=conversation)
            if interaction_history
            else "(No previous conversation)"
        )

        optional_instructions = ""

        # Optional: enrich prompt with capability_search results.
        if getattr(self._action, "router_use_capability_search", False):
            try:
                capability_section = await self._build_capability_search_section(
                    interaction.utterance or ""
                )
                if capability_section:
                    optional_instructions += "\n\n" + capability_section
            except Exception as exc:
                logger.debug("EngineRouter: capability_search enrich failed: %s", exc)

        active_tasks_section = self._build_active_tasks_section()

        routing_user_template = getattr(
            self._action,
            "routing_user_prompt_template",
            build_routing_user_prompt_template(),
        )
        prompt = routing_user_template.format(
            utterance=interaction.utterance or "",
            capabilities_json=capabilities_json,
            active_tasks_section=active_tasks_section,
            history_section=history_section,
            prior_fragments_section="",
            optional_instructions=optional_instructions,
        )

        system_prompt = getattr(
            self._action,
            "routing_system_prompt",
            build_routing_system_prompt(),
        )
        response = await model_action.generate(
            prompt=prompt,
            system=system_prompt,
            temperature=getattr(self._action, "router_model_temperature", 0.1),
            max_tokens=getattr(self._action, "router_model_max_tokens", 400),
            model=getattr(self._action, "router_model", "gpt-4o-mini"),
            calling_action_name=self._action.get_class_name(),
            interaction=interaction,
        )

        result = parse_routing_response(response)
        result.selected = self._validate_selected(result.selected, capability_catalog)

        if cache_key:
            try:
                await set_interact_router_cache(
                    cache_key, result.to_dict(), caller_enabled=True
                )
            except Exception as exc:
                logger.debug("EngineRouter: cache write failed: %s", exc)

        return result

    def _build_unified_catalog(
        self,
        skill_descriptors: Dict[str, Dict[str, Any]],
        ia_descriptors: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Merge skill + IA descriptors into a single catalog presented to the LLM.

        The model only sees ``description`` (and a short ``tags`` hint for
        skills) — ``kind`` is omitted from the prompt because the dispatch
        decode reads it from the registry post-LLM.
        """
        merged: Dict[str, Dict[str, Any]] = {}
        for name, info in skill_descriptors.items():
            merged[name] = {
                "description": info.get("description", ""),
            }
            tags = info.get("tags")
            if tags:
                merged[name]["tags"] = tags
        for name, info in ia_descriptors.items():
            merged[name] = {
                "description": info.get("description", ""),
            }
        return merged

    def _validate_selected(
        self,
        selected: List[CapabilityRef],
        capability_catalog: Dict[str, Dict[str, Any]],
        skill_descriptors: Optional[Dict[str, Dict[str, Any]]] = None,
        ia_descriptors: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[CapabilityRef]:
        """Drop capabilities the LLM hallucinated and re-decode ``kind`` from the registry."""
        del skill_descriptors, ia_descriptors  # signature symmetry with cache path
        return [c for c in selected if c.name in capability_catalog]

    async def _collect_interact_action_descriptors(self) -> Dict[str, Dict[str, Any]]:
        """Build descriptor map for routable InteractActions on the agent.

        Excludes:

        - The helm action itself (cannot delegate to self).
        - The orchestrator wrapper (``BridgeInteractAction``) — guard
          against recursion into Bridge.
        - InteractActions with ``always_execute=True`` (scheduled by the
          walker queue, not by the router).
        - InteractActions whose manifest declares ``routable_by_anchor=False``
          (chain-internal; reachable only via DELEGATE from a parent IA).
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
            logger.debug("EngineRouter: interact action enumeration failed: %s", exc)
            return {}

        helm_class = self._action.__class__.__name__
        _ORCHESTRATOR_EXCLUSIONS = {"BridgeInteractAction"}
        descriptors: Dict[str, Dict[str, Any]] = {}
        for action in all_actions:
            try:
                if not isinstance(action, InteractAction):
                    continue
                cls_name = action.__class__.__name__
                if cls_name == helm_class:
                    continue
                if cls_name in _ORCHESTRATOR_EXCLUSIONS:
                    continue
                if bool(getattr(action, "always_execute", False)):
                    continue
                try:
                    manifest = action.get_manifest()
                    if not manifest.routable_by_anchor:
                        continue
                except Exception as exc:
                    logger.debug(
                        "EngineRouter: manifest read failed for %s: %s; "
                        "treating as routable by default",
                        cls_name,
                        exc,
                    )
                description = (
                    getattr(action, "description", None)
                    or action.__class__.__doc__
                    or ""
                ).strip()
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

    def _build_cache_key(self, interaction: Any, conversation: Any) -> Optional[str]:
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
        ia_descriptors: Dict[str, Dict[str, Any]],
    ) -> Optional[RoutingResult]:
        """Rebuild a :class:`RoutingResult` from cache, re-validating selections.

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
            logger.debug("EngineRouter: cache deserialise failed: %s", exc)
            return None
        catalog = self._build_unified_catalog(skill_descriptors, ia_descriptors)
        result.selected = self._validate_selected(result.selected, catalog)
        return result

    def _build_active_tasks_section(self) -> str:
        """Render active tasks on the conversation for the routing prompt.

        Surfaces every task with status ``active`` on the current conversation,
        grouped by ``owner_action``. The router uses this to route fragments
        back to the owning interact_action when an interview / multi-step
        flow is in progress, and to avoid spawning parallel handlers.
        Returns an empty string when there's no visitor / conversation, no
        TaskStore, or no active tasks.
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
            logger.debug("EngineRouter: tasks.list failed: %s", exc)
            return ""
        if not active:
            return ""

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
            "CAPABILITY, prefer that owner over starting a parallel one."
        )
        return "\n".join(lines) + "\n\n"

    async def _build_capability_search_section(self, utterance: str) -> str:
        if not utterance:
            return ""
        try:
            from jvagent.action.helm.reasoning.tools.search import search_for_router

            agent = await self._action.get_agent()
            conversation = getattr(self._visitor, "conversation", None)
            shim = EngineVisitorShim(
                agent=agent,
                action_resolver=None,
                user_id=None,
                conversation=conversation,
                interaction=None,
                session_id=None,
                response_bus=None,
                channel=None,
            )
            shim._agent = agent
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
            logger.debug("EngineRouter: capability search section failed: %s", exc)
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
            agent_shim = EngineVisitorShim(
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
            logger.warning("EngineRouter: catalog discovery failed: %s", exc)
            return None


async def asyncio_gather_router(*args: Any) -> Any:
    import asyncio

    return await asyncio.gather(*args)
