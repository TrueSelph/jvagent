"""``ExecutiveInteractAction`` — the central executive (ADR-0010).

A single InteractAction at weight ``-200`` that owns a frame-stack control loop.
It is the agent's prefrontal cortex: it engages trivial conversation, knows all
centers, holds working memory, activates centers (the only thing that does),
integrates their results, and decides when to respond. Centers are leaves that
``STEP`` or ``RETURN``. The Persona center is the sole egress.

The loop runs entirely inside one ``execute()`` call — no walker-revisit — and
returns once, so the walker continues its weight chain afterwards (pipeline
citizenship; ADR-0010 §2.4). Per-tick guarantees (one model call, access
control, observability, streaming flush, runaway bound) are re-derived here at
loop level (ADR-0010 §3).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.executive.access import (
    ExecutiveAccessDenied,
    check_center_access,
)
from jvagent.action.executive.base import BaseCenter
from jvagent.action.executive.context import TurnContext
from jvagent.action.executive.contracts import (
    ACTIVATE,
    RESPOND,
    RETURN,
    STEP,
    WORKING_MEMORY_VISITOR_ATTR,
    YIELD,
    Brief,
    Result,
    is_center_directive,
    is_executive_directive,
)
from jvagent.action.executive.prompts import (
    EXECUTIVE_SYSTEM_PROMPT,
    EXECUTIVE_USER_PROMPT_TEMPLATE,
    render_capabilities_section,
    render_centers_section,
    render_working_memory_section,
)
from jvagent.action.executive.registry import (
    CapabilityRegistry,
    build_registry_from_agent,
)
from jvagent.action.executive.state import (
    DEFAULT_ACTIVATION_BUDGET,
    Frame,
    ModelBudget,
    ModelBudgetExceeded,
    WorkingMemory,
)
from jvagent.action.executive.sustained import (
    clear_sustained,
    has_active_ia_task,
    read_sustained,
    write_sustained,
)
from jvagent.action.interact.base import InteractAction

# Default phrases that break a sustained activation (turn-lock) in the reflex
# path, returning control to the Executive. The lock-owning IA's own intent
# classifier may use a richer set (manifest.interrupt_phrases).
DEFAULT_INTERRUPT_PHRASES = ("stop", "cancel", "quit", "never mind", "nevermind")

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

# Class names of competing pattern orchestrators that share the -200 slot.
_CONFLICTING_ORCHESTRATORS = ("BridgeInteractAction", "CockpitInteractAction")


def detect_pattern_conflict(class_names: List[str]) -> Optional[str]:
    """Return a warning message if Executive co-installs with Bridge/Cockpit.

    All three occupy weight ``-200`` and turn ownership would be ambiguous
    (ADR-0010 §3 inv. 7 / SPEC §11 inv. 9). Returns ``None`` when there is no
    conflict.
    """
    names = set(class_names)
    if "ExecutiveInteractAction" not in names:
        return None
    clash = [n for n in _CONFLICTING_ORCHESTRATORS if n in names]
    if not clash:
        return None
    return (
        "ExecutiveInteractAction cannot coexist with "
        + " / ".join(clash)
        + " — all occupy weight -200. Install exactly one pattern orchestrator."
    )


class ExecutiveInteractAction(InteractAction):
    """Central-executive orchestrator (ADR-0010).

    Configuration (override in ``agent.yaml.context:``):

    - ``centers``: ordered list of center class names to recruit from.
    - ``persona_center``: class name of the egress (Persona) center.
    - ``activation_budget``: hard cap on ticks per turn (runaway bound).
    - ``denied_response_text``: published on the safe-fallback path.
    - ``enable_transient_ack``: master switch for ACTIVATE ack lead-ins.
    """

    weight: int = attribute(
        default=-200,
        description="Pattern-orchestrator slot at -200 (mutually exclusive with Bridge/Cockpit).",
    )
    description: str = attribute(
        default=(
            "Central executive: engages light conversation, activates "
            "specialist centers (skills, IA), integrates results, and voices "
            "through the Persona center. Frame-stack control loop; no peer shifts."
        )
    )
    centers: List[str] = attribute(
        default_factory=list,
        description="Ordered list of center class names to recruit from.",
    )
    persona_center: str = attribute(
        default="PersonaCenter",
        description="Class name of the egress (Persona) center. Sole producer of final prose.",
    )
    activation_budget: int = attribute(
        default=DEFAULT_ACTIVATION_BUDGET,
        description="Hard cap on executive ticks + center activations per turn.",
    )
    denied_response_text: str = attribute(
        default="Sorry, I can't do that here.",
        description="Published when the safe-fallback path activates (AC denial / budget exhaustion).",
    )
    enable_transient_ack: bool = attribute(
        default=True,
        description="If True, ACTIVATE.ack lead-ins are published. False suppresses all canned acks.",
    )
    ia_center: str = attribute(
        default="IACenter",
        description="Center that handles anchored rails IAs (reflex/anchor target).",
    )
    interrupt_phrases: List[str] = attribute(
        default_factory=lambda: list(DEFAULT_INTERRUPT_PHRASES),
        description="Phrases that break a sustained activation in the reflex path.",
    )

    # -- Executive cognition (light model) ----------------------------------
    model: str = attribute(
        default="gpt-4o-mini",
        description="Light model driving the Executive's routing/conversation decision.",
    )
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="LanguageModelAction subclass that drives the Executive call.",
    )
    model_temperature: float = attribute(default=0.2)
    model_max_tokens: int = attribute(default=512)
    enforce_json_mode: bool = attribute(
        default=True,
        description="Pass response_format=json_object to the provider when supported.",
    )
    history_limit: int = attribute(
        default=4, description="Prior turns included in the routing prompt."
    )
    routing_max_tier: Optional[int] = attribute(
        default=None,
        description="If set, only capabilities at tier <= this are surfaced for routing.",
    )
    clarify_text: str = attribute(
        default="Sorry, I didn't quite catch that — could you rephrase?",
        description="Voiced when the Executive's decision is unavailable/unparseable.",
    )

    # ------------------------------------------------------------------
    # Center resolution (overridable in tests)
    # ------------------------------------------------------------------

    async def _lookup_center(self, name: str) -> Optional[BaseCenter]:
        """Return the center instance for ``name`` or ``None``.

        Tests monkeypatch this to inject ``StubCenter`` instances without the
        loader. Production resolves via ``Action.get_action`` (O(1)).
        """
        try:
            center: Any = await self.get_action(name)
        except Exception as exc:
            logger.warning("executive: get_action(%r) raised: %s", name, exc)
            return None
        if center is None:
            return None
        if not isinstance(center, BaseCenter):
            logger.warning(
                "executive: resolved action %r is not a BaseCenter (got %s); ignoring",
                name,
                type(center).__name__,
            )
            return None
        return center

    async def _resolve_centers_map(self) -> Dict[str, BaseCenter]:
        resolved: Dict[str, BaseCenter] = {}
        for name in self.centers or []:
            center = await self._lookup_center(name)
            if center is not None:
                resolved[center.center_name()] = center
        return resolved

    def _center_info(self, centers: Dict[str, BaseCenter]) -> List[Dict[str, str]]:
        """Build ``[{name, purpose}]`` for the activatable (non-persona) centers.

        The purpose comes from the center's manifest (``info.yaml``) and falls
        back to its ``description`` — it is what lets the Executive route
        reasoning to Skills and structured flows to IA (live-smoke finding).
        """
        info: List[Dict[str, str]] = []
        for name, center in centers.items():
            if name == self.persona_center:
                continue
            purpose = ""
            try:
                purpose = (getattr(center.get_manifest(), "purpose", "") or "").strip()
            except Exception:
                purpose = ""
            if not purpose:
                purpose = (getattr(center, "description", "") or "").strip()
            info.append({"name": name, "purpose": purpose})
        return info

    async def _safe_get_agent(self) -> Any:
        try:
            return await self.get_agent()
        except Exception as exc:
            logger.debug("executive: get_agent failed: %s", exc)
            return None

    async def _curate_walker_queue(self, visitor: "InteractWalker", agent: Any) -> None:
        """Restrict the remaining walker queue to ``{self} ∪ always_execute IAs``.

        Routable IAs (anchored, non-``always_execute``) are owned by the IA
        center and MUST NOT also self-run as weight-chain members — otherwise an
        anchored flow (e.g. a turn-locking interview) executes every turn in
        parallel with the Executive (live-smoke finding, 2026-05-29).
        ``always_execute`` IAs (intro / audit) are preserved — that is the
        substance of "pipeline citizenship" (ADR-0010 §2.4, amended). The IA
        center remains the sole path to routable IAs.
        """
        if agent is None:
            return
        try:
            actions_mgr = await agent.get_actions_manager()
            if actions_mgr is None:
                return
            all_enabled = await actions_mgr.get_all_actions(enabled_only=True)
        except Exception as exc:
            logger.debug("executive: curate enumeration failed: %s", exc)
            return
        my_class = self.__class__.__name__
        always_run: List[Any] = []
        for action in all_enabled:
            if not isinstance(action, InteractAction):
                continue
            if action is self or action.__class__.__name__ == my_class:
                continue
            if not bool(getattr(action, "always_execute", False)):
                continue
            always_run.append(action)
        always_run.sort(key=lambda a: int(getattr(a, "weight", 0)))
        combined: List[Any] = [self] + always_run
        try:
            await visitor.curate_walk_path(combined)
        except Exception as exc:
            logger.debug("executive: curate_walk_path failed: %s", exc)

    # ------------------------------------------------------------------
    # Working-memory plumbing
    # ------------------------------------------------------------------

    def _get_or_init_wm(self, visitor: "InteractWalker") -> WorkingMemory:
        wm = getattr(visitor, WORKING_MEMORY_VISITOR_ATTR, None)
        if wm is None or not isinstance(wm, WorkingMemory):
            # Fresh per-turn working memory. Sustained activation (turn-lock)
            # is NOT rehydrated here — it lives in the conversation TaskStore
            # and the reflex resumes from it (ADR-0010 §2.5, TaskStore-backed).
            wm = WorkingMemory(turn_started_at=time.monotonic())
            setattr(visitor, WORKING_MEMORY_VISITOR_ATTR, wm)
        return wm

    async def _persist_suspended(
        self,
        visitor: "InteractWalker",
        wm: WorkingMemory,
        centers: Dict[str, BaseCenter],
        registry: CapabilityRegistry,
    ) -> None:
        """Persist (or clear) sustained activation on the conversation TaskStore.

        A non-IA center's sustain is recorded as an ``executive_sustained``
        task. An IA center's sustain is left to the rails IA's *own* task (the
        unification) — we only write a fallback executive task if the IA did
        not create one. A non-sustaining turn clears the executive task.
        """
        conversation = getattr(visitor, "conversation", None)
        if conversation is None:
            return
        try:
            if wm.suspended is None:
                await clear_sustained(conversation)
                return
            center = wm.suspended.get("center")
            brief = wm.suspended.get("brief") or {}
            if center == self.ia_center and await has_active_ia_task(
                conversation, set(centers.keys()), registry
            ):
                # The rails IA owns its task — don't duplicate it.
                return
            await write_sustained(conversation, center=center, brief=brief)
        except Exception as exc:
            logger.debug("executive: failed to persist sustained activation: %s", exc)

    def _clear_wm(self, visitor: "InteractWalker") -> None:
        if hasattr(visitor, WORKING_MEMORY_VISITOR_ATTR):
            try:
                delattr(visitor, WORKING_MEMORY_VISITOR_ATTR)
            except AttributeError:
                pass

    # ------------------------------------------------------------------
    # Capability registry (overridable in tests)
    # ------------------------------------------------------------------

    async def _build_registry(
        self,
        agent: Any,
        centers: Dict[str, BaseCenter],
    ) -> CapabilityRegistry:
        """Build the per-turn capability registry.

        Default: enumerate the agent's anchored rails IAs and map them to the
        IA center (skills are added in a later milestone). Best-effort — an
        empty registry is a valid result. Tests monkeypatch this to inject
        capabilities directly.
        """
        if agent is None:
            return CapabilityRegistry()
        try:
            actions_mgr = await agent.get_actions_manager()
            enabled = (
                await actions_mgr.get_all_actions(enabled_only=True)
                if actions_mgr
                else []
            )
        except Exception as exc:
            logger.debug("executive: registry enumeration failed: %s", exc)
            enabled = []
        return build_registry_from_agent(
            agent, ia_center=self.ia_center, enabled_actions=enabled
        )

    def _is_interrupt(self, utterance: str) -> bool:
        u = (utterance or "").strip().lower()
        if not u:
            return False
        return any((p or "").strip().lower() in u for p in self.interrupt_phrases)

    async def _owner_allows_interrupt(self, resume: Dict[str, Any]) -> bool:
        """Whether the executive-level interrupt bypass may steal this turn.

        Defers to the lock owner's interruptibility contract:

        - A rails-IA-owned lock (resumed via the IA center) reads the IA's
          ``manifest.can_interrupt``. An IA declaring ``can_interrupt: false``
          owns its own cancellation, so the bypass is denied and the utterance
          is forwarded into the IA (its intent classifier breaks the lock).
        - A generic / non-IA sustained activation has no self-cancellation, so
          the bypass is allowed (default ``True``) — the safety hatch.
        """
        if resume.get("center") != self.ia_center:
            return True
        slots = (resume.get("brief") or {}).get("slots") or {}
        ia_name = slots.get("capability") or slots.get("ia")
        if not ia_name:
            return True
        try:
            action = await self.get_action(str(ia_name))
            manifest = action.get_manifest() if action else None
        except Exception as exc:
            logger.debug(
                "executive: can_interrupt lookup failed for %r: %s", ia_name, exc
            )
            return True
        if manifest is None:
            return True
        return bool(getattr(manifest, "can_interrupt", True))

    # ------------------------------------------------------------------
    # Reflex pre-pass (deterministic; no model). Returns an ACTIVATE to
    # short-circuit the Executive, or None to fall through to the Executive.
    # ------------------------------------------------------------------

    async def _reflex(
        self,
        visitor: "InteractWalker",
        centers: Dict[str, BaseCenter],
        wm: WorkingMemory,
        registry: CapabilityRegistry,
    ) -> Optional[ACTIVATE]:
        utterance = (getattr(visitor, "utterance", "") or "").strip()
        if not utterance:
            return None

        # 1. Resume a sustained activation (turn-lock) from the conversation
        #    TaskStore. The executive-level interrupt bypass only applies when
        #    the lock OWNER permits interruption (manifest ``can_interrupt``).
        #    A non-interruptible owner (e.g. an interview declaring
        #    ``can_interrupt: false``) owns its own cancellation, so phrases
        #    like "cancel" / "nevermind" must be forwarded INTO it — otherwise
        #    they get stolen by the Executive and the owner never sees the
        #    cancel, never releases the lock (live-smoke finding, 2026-05-30).
        conversation = getattr(visitor, "conversation", None)
        resume = await read_sustained(conversation, set(centers.keys()), registry)
        if resume and resume.get("center") in centers:
            if self._is_interrupt(utterance) and await self._owner_allows_interrupt(
                resume
            ):
                pass  # interruptible owner — fall through to the Executive
            else:
                bp = resume.get("brief") or {}
                brief = Brief(
                    intent=bp.get("intent", "") or utterance,
                    slots=dict(bp.get("slots", {})),
                    constraints=list(bp.get("constraints", [])),
                )
                return ACTIVATE(center=resume["center"], brief=brief, on_done="voice")

        # 2. Deterministic anchor match → activate the handling center.
        cap = registry.match_anchor(utterance) if registry else None
        if cap is not None and cap.center in centers:
            return ACTIVATE(
                center=cap.center,
                brief=Brief(intent=utterance, slots={"capability": cap.id}),
                on_done="voice",
            )
        return None

    # ------------------------------------------------------------------
    # Executive cognition (real LM impl M4; tests monkeypatch this).
    # ------------------------------------------------------------------

    async def _executive_tick(self, ctx: TurnContext):  # -> ExecutiveDirective
        """Decide the next move: ACTIVATE a center, RESPOND, or YIELD.

        One light-model call per tick. The model returns a structured JSON
        decision (no function-calling) which is parsed and validated against
        the activatable centers. On model failure or an unparseable/invalid
        decision, the Executive RESPONDs a brief clarification so the user
        always hears back (ADR-0010 OQ #5).
        """
        activatable = [ci["name"] for ci in ctx.center_info] or [
            c for c in ctx.center_names if c != self.persona_center
        ]
        system_prompt, user_prompt = await self._build_routing_prompt(ctx, activatable)
        raw = await self._call_router_model(ctx, system_prompt, user_prompt)
        if not raw:
            logger.info("executive: router model produced no output → clarify")
            return RESPOND(content=self.clarify_text)
        decision = self._parse_decision(raw, set(activatable))
        if decision is None:
            logger.warning(
                "executive: undecodable/invalid routing decision %r → clarify",
                raw[:200],
            )
            return RESPOND(content=self.clarify_text)
        return decision

    async def _build_routing_prompt(
        self,
        ctx: TurnContext,
        activatable: List[str],
    ) -> tuple[str, str]:
        routing_view = (
            ctx.registry.routing_view(max_tier=self.routing_max_tier)
            if ctx.registry
            else []
        )
        centers_for_prompt = ctx.center_info or [{"name": n} for n in activatable]
        system_prompt = EXECUTIVE_SYSTEM_PROMPT.format(
            centers_section=render_centers_section(centers_for_prompt),
            capabilities_section=render_capabilities_section(routing_view),
        )
        history = await self._build_history(ctx.visitor)
        wm_results = [(r.content or "").strip() for r in ctx.wm.results]
        user_prompt = EXECUTIVE_USER_PROMPT_TEMPLATE.format(
            history_section=history or "(no prior turns)",
            working_memory_section=render_working_memory_section(wm_results),
            utterance=ctx.utterance or "(empty)",
        )
        return system_prompt, user_prompt

    async def _build_history(self, visitor: "InteractWalker") -> str:
        """Render the last ``history_limit`` turns into a compact text block."""
        conversation = getattr(visitor, "conversation", None)
        if conversation is None:
            return ""
        try:
            interaction = getattr(visitor, "interaction", None)
            excluded = getattr(interaction, "id", None) if interaction else None
            turns = await conversation.get_interaction_history(
                limit=max(1, int(self.history_limit)),
                excluded=excluded,
                with_utterance=True,
                with_response=True,
                formatted=False,
            )
        except Exception as exc:
            logger.debug("executive: history fetch failed: %s", exc)
            return ""
        if not turns:
            return ""
        lines: List[str] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            utt = (turn.get("utterance") or "").strip()
            resp = (turn.get("response") or "").strip()
            if utt:
                lines.append(f"USER: {utt}")
            if resp:
                lines.append(f"AGENT: {resp}")
        return "\n".join(lines)

    async def _call_router_model(
        self,
        ctx: TurnContext,
        system_prompt: str,
        user_prompt: str,
    ) -> Optional[str]:
        """Issue the single light-model call for this tick. Returns raw text or None.

        Acquires the per-tick model budget first (enforces one-call-per-tick).
        Tests monkeypatch this to return canned JSON without a real provider.
        """
        ctx.use_model()
        model_action = await self.get_model_action(required=False)
        if model_action is None:
            logger.warning(
                "executive: no model action available (model_action_type=%r)",
                self.model_action_type,
            )
            return None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        kwargs: Dict[str, Any] = {
            "messages": messages,
            "stream": False,
            "system": system_prompt,
            "prompt_for_observability": user_prompt,
            "tools": None,
            "model": self.model or None,
            "temperature": self.model_temperature,
            "max_tokens": self.model_max_tokens,
            "calling_action_name": self.__class__.__name__,
        }
        if self.enforce_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            result = await model_action.query_messages(**kwargs)
        except Exception as exc:
            logger.warning("executive: router model call raised: %s", exc)
            return None
        return (getattr(result, "response", None) or "").strip() or None

    def _parse_decision(self, raw: str, activatable: set):  # -> ExecutiveDirective|None
        """Parse the router model's JSON into a validated executive verb.

        Returns ``None`` on any parse/validation failure so the caller can
        clarify rather than act on a malformed decision.
        """
        obj = _parse_json_object(raw)
        if obj is None:
            return None
        action = (obj.get("action") or "").strip().lower()
        if action == "respond":
            content = (obj.get("content") or "").strip()
            return RESPOND(content=content) if content else None
        if action == "activate":
            center = (obj.get("center") or "").strip()
            if center not in activatable:
                return None
            on_done = obj.get("on_done") or "integrate"
            if on_done not in ("voice", "integrate"):
                on_done = "integrate"
            ack = obj.get("ack")
            ack = ack.strip() if isinstance(ack, str) and ack.strip() else None
            return ACTIVATE(
                center=center,
                brief=Brief(intent=(obj.get("intent") or "").strip()),
                on_done=on_done,
                ack=ack,
            )
        if action == "yield":
            return YIELD()
        return None

    # ------------------------------------------------------------------
    # Main entry point — the control loop
    # ------------------------------------------------------------------

    async def execute(self, visitor: "InteractWalker") -> None:
        if not self._ensure_interaction(visitor):
            return

        centers = await self._resolve_centers_map()
        agent = await self._safe_get_agent()
        wm = self._get_or_init_wm(visitor)
        registry = await self._build_registry(agent, centers)

        # Curate the walker queue: routable IAs are owned by the IA center and
        # must not self-run; only {self + always_execute} remain (ADR-0010 §2.4).
        await self._curate_walker_queue(visitor, agent)

        # Seed the stack on first entry: reflex short-circuit, else Executive.
        if not wm.stack:
            reflex = await self._reflex(visitor, centers, wm, registry)
            if reflex is not None:
                await self._begin_activation(visitor, wm, centers, agent, reflex)
            if not wm.stack and not wm.finalized:
                wm.push(Frame(actor="executive"))

        budget = max(1, int(self.activation_budget))
        while wm.stack and not wm.finalized and budget > 0:
            budget -= 1
            wm.activation_count += 1
            frame = wm.current
            assert frame is not None  # loop guard above guarantees a frame
            mb = ModelBudget(max_calls=1)
            ctx = TurnContext(
                visitor=visitor,
                wm=wm,
                model_budget=mb,
                action=self,
                agent=agent,
                registry=registry,
                center_names=[c for c in centers if c != self.persona_center],
                center_info=self._center_info(centers),
            )
            try:
                if frame.actor == "executive":
                    await self._tick_executive(visitor, wm, centers, agent, ctx)
                else:
                    await self._tick_center(visitor, wm, centers, ctx, frame)
            except ModelBudgetExceeded as exc:
                logger.warning(
                    "executive: tick by %r exceeded model budget: %s",
                    frame.actor,
                    exc,
                )
                wm.record(frame.actor, "ABORT", "model_budget_exceeded")
                await self._safe_fallback(visitor, wm)
                break
            except Exception as exc:  # pragma: no cover (exercised in tests)
                logger.exception(
                    "executive: tick by %r raised; safe-falling back: %s",
                    frame.actor,
                    exc,
                )
                await self._safe_fallback(visitor, wm)
                break
            self._flush_stream(visitor)

        if not wm.finalized and budget <= 0:
            logger.warning("executive: activation budget exhausted; safe-falling back")
            wm.record("executive", "ABORT", "activation_budget_exhausted")
            await self._safe_fallback(visitor, wm)

        await self._persist_suspended(visitor, wm, centers, registry)
        self._persist_observability(visitor, wm)
        self._clear_wm(visitor)
        # The walker continues to the next weight-ordered IA after we return —
        # but only {self + always_execute} remain after _curate_walker_queue,
        # so routable IAs run via the IA center, not the chain (ADR-0010 §2.4).

    # ------------------------------------------------------------------
    # Executive tick
    # ------------------------------------------------------------------

    async def _tick_executive(
        self,
        visitor: "InteractWalker",
        wm: WorkingMemory,
        centers: Dict[str, BaseCenter],
        agent: Any,
        ctx: TurnContext,
    ) -> None:
        directive = await self._executive_tick(ctx)
        if not is_executive_directive(directive):
            logger.error(
                "executive: cognition returned non-executive verb %r; yielding",
                type(directive).__name__,
            )
            wm.record("executive", "ERROR", f"bad_verb:{type(directive).__name__}")
            wm.finalized = True
            return

        if isinstance(directive, ACTIVATE):
            wm.record("executive", "ACTIVATE", directive.center)
            await self._begin_activation(visitor, wm, centers, agent, directive)
            return
        if isinstance(directive, RESPOND):
            wm.record("executive", "RESPOND", "")
            await self._egress(
                visitor,
                content=directive.content,
                verbatim=directive.verbatim,
                meta=directive.meta,
            )
            wm.finalized = True
            return
        # YIELD
        wm.record("executive", "YIELD", "")
        wm.finalized = True

    async def _begin_activation(
        self,
        visitor: "InteractWalker",
        wm: WorkingMemory,
        centers: Dict[str, BaseCenter],
        agent: Any,
        verb: ACTIVATE,
    ) -> None:
        """Push a center frame after AC + resolution checks. Safe-fallback on failure."""
        if verb.center not in centers:
            logger.warning(
                "executive: ACTIVATE target %r is not a resolved center; safe-falling back",
                verb.center,
            )
            wm.record("executive", "ERROR", f"unknown_center:{verb.center}")
            await self._safe_fallback(visitor, wm)
            return
        try:
            await check_center_access(
                agent,
                center_name=verb.center,
                user_id=getattr(visitor, "user_id", None),
                channel=getattr(visitor, "channel", "default") or "default",
            )
        except ExecutiveAccessDenied as denied:
            logger.info("executive: ACTIVATE denied by AC: %s", denied.resource)
            wm.record("executive", "DENIED", denied.resource)
            await self._safe_fallback(visitor, wm)
            return

        if self.enable_transient_ack and verb.ack:
            await self.publish(visitor=visitor, content=verb.ack, transient=True)

        wm.push(
            Frame(
                actor=verb.center,
                brief=verb.brief or Brief(),
                on_done=verb.on_done,
            )
        )

    # ------------------------------------------------------------------
    # Center tick
    # ------------------------------------------------------------------

    async def _tick_center(
        self,
        visitor: "InteractWalker",
        wm: WorkingMemory,
        centers: Dict[str, BaseCenter],
        ctx: TurnContext,
        frame: Frame,
    ) -> None:
        center = centers.get(frame.actor)
        if center is None:
            logger.warning(
                "executive: active center %r vanished mid-turn; safe-falling back",
                frame.actor,
            )
            wm.record(frame.actor, "ERROR", "center_missing")
            await self._safe_fallback(visitor, wm)
            return

        directive = await center.tick(ctx, frame)
        self._record_center_execution(visitor, frame.actor)

        if not is_center_directive(directive):
            logger.error(
                "executive: center %r returned non-center verb %r; safe-falling back",
                frame.actor,
                type(directive).__name__,
            )
            wm.record(frame.actor, "ERROR", f"bad_verb:{type(directive).__name__}")
            await self._safe_fallback(visitor, wm)
            return

        if isinstance(directive, STEP):
            wm.record(frame.actor, "STEP", "")
            if directive.scratch:
                frame.scratch.update(directive.scratch)
            return  # same frame re-ticked next loop iteration

        # RETURN
        result: Result = directive.result or Result()
        wm.record(frame.actor, "RETURN", f"on_done={frame.on_done}")
        wm.pop()
        wm.results.append(result)

        if directive.sustain:
            wm.suspended = {
                "center": frame.actor,
                "brief": {
                    "intent": (frame.brief.intent if frame.brief else ""),
                    "slots": dict(frame.brief.slots) if frame.brief else {},
                    "constraints": list(frame.brief.constraints) if frame.brief else [],
                },
            }
        else:
            # A non-sustaining RETURN clears any prior sustained activation
            # (e.g. a turn-locked flow that just completed / was cancelled),
            # so it is not persisted and re-resumed next turn.
            wm.suspended = None

        if frame.on_done == "voice":
            await self._egress(
                visitor,
                content=result.content,
                verbatim=result.verbatim,
                meta=result.meta,
            )
            wm.finalized = True
            return

        # integrate → ensure an Executive frame is beneath to integrate/respond.
        if not wm.stack and not wm.finalized:
            wm.push(Frame(actor="executive"))

    def _record_center_execution(self, visitor: "InteractWalker", name: str) -> None:
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        try:
            interaction.record_action_execution(name)
        except Exception as exc:
            logger.debug("executive: record_action_execution(%s) failed: %s", name, exc)

    # ------------------------------------------------------------------
    # Egress — the Persona center is the sole producer of final prose.
    # M1 fallback: direct publish. M3 routes through the Persona center.
    # ------------------------------------------------------------------

    async def _egress(
        self,
        visitor: "InteractWalker",
        *,
        content: str,
        verbatim: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not content:
            return
        # The Persona center is the sole egress (ADR-0010 §2.4). Resolve it and
        # delegate; fall back to a raw publish only if it is absent or errors,
        # so the user always hears back.
        persona = await self._lookup_center(self.persona_center)
        voice = getattr(persona, "voice", None)
        if persona is not None and callable(voice):
            try:
                published = await voice(
                    visitor, content=content, verbatim=verbatim, meta=meta
                )
                if published:
                    return
            except Exception as exc:
                logger.warning(
                    "executive: persona center voice() failed; raw publish: %s", exc
                )
        await self.publish(visitor=visitor, content=content, metadata=meta or None)

    # ------------------------------------------------------------------
    # Hooks / safety nets
    # ------------------------------------------------------------------

    def _flush_stream(self, visitor: "InteractWalker") -> None:
        """Streaming flush boundary between ticks (full impl M9)."""
        return None

    async def _safe_fallback(
        self, visitor: "InteractWalker", wm: WorkingMemory
    ) -> None:
        if self.denied_response_text:
            await self.publish(visitor=visitor, content=self.denied_response_text)
        wm.finalized = True

    def _persist_observability(
        self, visitor: "InteractWalker", wm: WorkingMemory
    ) -> None:
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        # Append per-tick events to the standard observability_metrics list.
        metrics = getattr(interaction, "observability_metrics", None)
        if isinstance(metrics, list):
            for ev in wm.trace:
                try:
                    metrics.append(
                        {
                            "event_type": "executive_tick",
                            "data": ev.to_dict(),
                            "timestamp": ev.at_monotonic,
                        }
                    )
                except Exception as exc:
                    logger.debug("executive: failed to append tick event: %s", exc)
        # Turn-level summary onto parameters.
        params = getattr(interaction, "parameters", None)
        payload = wm.to_observability()
        try:
            if isinstance(params, dict):
                params["executive_observability"] = payload
            elif isinstance(params, list):
                params.append(
                    {
                        "action_name": self.__class__.__name__,
                        "content": "executive_observability",
                        "executive_observability": payload,
                    }
                )
        except Exception as exc:
            logger.debug("executive: failed to persist observability: %s", exc)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from a model response. None if none parses.

    Tolerates leading/trailing prose or markdown fences around the object.
    """
    candidate = (raw or "").strip()
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(candidate)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


__all__ = ["ExecutiveInteractAction", "detect_pattern_conflict"]
