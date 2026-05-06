"""CockpitRouter: lightweight pre-cockpit posture classification + skill selection.

Self-contained — imports only from core modules (no agent_interact dependency).
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jvagent.action.cockpit.catalog.skill_catalog import SkillCatalog
from jvagent.action.cockpit.catalog.skill_discovery import always_active_from_skill_dir
from jvagent.action.cockpit.registry.shim import CockpitVisitorShim
from jvagent.action.cockpit.routing.types import (
    POSTURE_DEFER,
    POSTURE_RESPOND,
    POSTURE_SUPPRESS,
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

ROUTING_SYSTEM_PROMPT = """You are a unified classification and routing intelligence for a conversational cockpit agent. First classify response posture (RESPOND/SUPPRESS/DEFER), then — only when posture is RESPOND — classify intent, select skills, and (when appropriate) emit a brief canned lead-in.

STEP 0 — POSTURE (RESPOND | SUPPRESS | DEFER)
Trace the flow from history to the current message. What was the most recent assistant message? How does the current user message relate?

RESPOND — use when:
- Greeting, opener, first contact ("Hey", "Hi", "Hello") — ALWAYS RESPOND
- Question, request, substantive statement
- User sent media (images, documents) — ALWAYS RESPOND; treat as request to view/interpret
- Answer (affirmative OR negative) to assistant's direct question ("ok", "yes", "no", "no sorry", "nope", "sure" after "Would you like X?")
- Gratitude for directly preceding assistant help ("Thanks!" after answer) — allow "you're welcome"
- Short but contextually coherent message; when in doubt, use RESPOND
- Personal-fact statements like "my name is..." or "remember that I..." — DIRECTIVE intent, RESPOND

SUPPRESS — use ONLY when:
- Social closing (goodbye) AND exchange already concluded or same closing already exchanged
- Redundant gratitude after assistant already said "you're welcome"
- Hanging acknowledgment ("ok", "alright") with nothing to answer AND exchange at natural pause
- NEVER SUPPRESS: direct answer to question ("No sorry"), greetings, "thanks" before "you're welcome", any new request, any personal-fact statement.

DEFER — use ONLY when:
- Utterance genuinely unintelligible/fragmentary ("Actually...", "wait no I") AND history lacks context
- NEVER DEFER: User sent media; use RESPOND.

STEP 1 — ROUTE SELECTION (only when posture=RESPOND)
Two route classes are available:

A. **skills** — capability bundles invoked through the cockpit engine (tool-driven research / synthesis / multi-step work). Pick from the SKILLS CATALOG. Use exact skill keys, never descriptions.
B. **interact_actions** — specialized response handlers that run AS InteractActions, without the cockpit engine. Pick from the INTERACT ACTIONS CATALOG. Use exact class names.

DECISION RULES:
- Choose **skills only** when the request needs tool-driven exploration / synthesis / data retrieval and no specialized handler matches.
- Choose **interact_actions only** when a listed handler is purpose-built for this request type (e.g., explicit handoff, structured form-fill, dedicated workflow) and no engine-level reasoning is needed.
- Choose **both** when the request needs research first AND a specialized handler afterward (engine produces output, then the interact_action runs).
- The cockpit engine has harness tools beyond skills (memory, artifacts, task planning, conversation search). A request that doesn't match any listed skill or interact_action can still be handled — emit ``skills: []`` and ``interact_actions: []`` and the engine will figure it out.

CORE PRINCIPLES:
- CONVERSATIONAL intent (greetings, thanks, smalltalk) MUST have empty skills [] AND empty interact_actions [].
- canned_response (when emitted): non-conclusive **lead-in only** — a fragment or stall that the engine's main reply will continue in the same turn; never a standalone sentence that answers, refuses, advises, redirects, or closes the topic.

INTENT TYPES (when posture=RESPOND):
- CONVERSATIONAL: greeting, thanks, smalltalk only; no request.
- INFORMATIONAL: question, lookup, knowledge retrieval.
- INTERACTIVE: multi-turn (interview / form-fill / back-and-forth).
- DIRECTIVE: direct command, imperative ("search for X", "remember that...", "save Z").
- UNCLEAR: cannot determine.

GROUNDING:
- Use this prompt, history, the skill catalog, and any tool output as admissible evidence for posture, intent, and interpretation.
- Do not treat general pretrained world knowledge as authoritative; when unsure, lower confidence.
"""

ROUTING_USER_PROMPT_TEMPLATE = """CONVERSATION STATE:
{active_tasks_section}{history_section}{prior_fragments_section}
CURRENT USER MESSAGE:
{utterance}

SKILLS CATALOG (JSON keys = only valid "skills" array entries):
{skills_json}

INTERACT ACTIONS CATALOG (JSON keys = only valid "interact_actions" array entries):
{interact_actions_json}

TASK: 1) Classify posture (RESPOND/SUPPRESS/DEFER). 2) If posture=RESPOND, classify intent and fill skills + interact_actions; otherwise use skills=[], interact_actions=[], canned_response="", intent_type="UNCLEAR".

POSTURE RULES (recap):
- RESPOND: greeting (always), question, request, answer to question, gratitude for help, personal-fact statement, contextually coherent message. When in doubt, RESPOND.
- SUPPRESS: closing after exchange concluded; redundant thanks; hanging "ok" with nothing to answer. NEVER for direct answers, greetings, or new requests.
- DEFER: genuinely unintelligible fragment AND no context. NEVER for media attachments.

RULES:
1. The ">>> USER RESPONDS NOW <<<" marker in history indicates the transition to the current user message.
2. Output posture first; then interpretation, intent_type, skills, interact_actions, confidence (and canned_response when posture=RESPOND).
3. CONVERSATIONAL intent MUST have empty skills [] AND empty interact_actions [].
4. Each skills array entry MUST be an exact SKILLS CATALOG key, NOT a description or tag.
5. Each interact_actions array entry MUST be an exact INTERACT ACTIONS CATALOG key (class name), NOT a description.
6. Use interact_actions ONLY when a listed handler is purpose-built for this request and no tool-driven engine work is needed.
7. Use both skills AND interact_actions when engine work must precede a specialized handler.
8. If the assistant's most recent message was a question and the user answers, use INTERACTIVE.
9. Lower confidence if ambiguous{optional_instructions}

INTERPRETATION: Brief synopsis of user intent and why this posture applies. Target one sentence, ~15-30 words.

OUTPUT (JSON only):
{{
  "posture": "RESPOND|SUPPRESS|DEFER",
  "interpretation": "Brief synopsis of user intent and why this posture applies.",
  "intent_type": "CONVERSATIONAL|INFORMATIONAL|INTERACTIVE|DIRECTIVE|UNCLEAR",
  "skills": ["SkillName1"],
  "interact_actions": ["ClassName1"],
  "confidence": 0.0-1.0{entity_field}{canned_field}
}}"""

ROUTING_CANNED_INSTRUCTIONS_TEMPLATE = """
7. canned_response: use "" when intent_type is one of: {skip_intents}. Otherwise same language as the CURRENT USER MESSAGE; ≤{max_words} words; vary wording across turns.{persona_tone_hint}

   STRICT — lead-in acknowledgement ONLY (must sound incomplete; the real reply follows immediately after in the same turn):
   - ALLOWED: hesitation, filler, or a short fragment with no full thought (e.g. "Hmm…", "One sec…", "Let me see…", "On it…", "Looking that up…" in the user's language). Reference the topic when natural ("Hmm… looking into Silvies Online…").
   - FORBIDDEN — **no conclusive or substantive content whatsoever**: no answers, explanations, outcomes, reasons, advice, instructions to the user, refusals, limits, policy, apologies-for-limits, workarounds, redirects, or any string that could read as a finished message. If it could stand alone in chat, it is wrong.
   - FORBIDDEN patterns (illustrative, not exhaustive): two clauses that resolve or pivot ("…, but you can…"; "…, so …"); "I can't …" / "I'm unable …" / "You should …" / "Try …" / anything that addresses the user's request without an obvious follow-on in the same bubble. Also forbidden: pre-emptive "Here's what I found…" / "Got it, here's…" — those imply the answer is already coming.
   - BAD: "I can't check the time, but you can look at your device." — explains and concludes; belongs in the main reply only, never in canned_response.
   - BAD: "Here's what I found about Silvies Online." — pre-empts the answer.
   - GOOD: "Hmm…" / "Just a moment…" / "On it — pulling up Silvies Online…" — acknowledges processing only; carries zero standalone substance.
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
        interaction_history: List[Dict[str, Any]],
        conversation: Any,
    ) -> RoutingResult:
        model_action = await self._action.get_model_action(
            required=True, purpose="router"
        )
        skills_json = json.dumps(skill_descriptors, indent=2)
        interact_action_descriptors = await self._collect_interact_action_descriptors()
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

        routing_user_template = getattr(
            self._action, "routing_user_prompt_template", ROUTING_USER_PROMPT_TEMPLATE
        )
        prompt = routing_user_template.format(
            utterance=interaction.utterance or "",
            skills_json=skills_json,
            interact_actions_json=interact_actions_json,
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
        result.interact_actions = self._validate_routes(
            result.interact_actions, interact_action_descriptors
        )
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
        # Production mode (Milestone G) silences user-facing stalls.
        if bool(getattr(self._action, "production_mode", False)):
            return False
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
