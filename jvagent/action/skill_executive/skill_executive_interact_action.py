"""SkillExecutiveInteractAction — the single orchestrator (ADR-0012).

Runs the whole turn inside one ``execute()`` call:

1. **Walk-path curation** — drop tool-exposed (routable) IAs from the remaining
   walker queue so they are reached only by tool selection, not by
   self-executing every turn.
2. **Think-act-observe loop** over the unified tool surface (persona
   ``reply``/``respond``, anchored IAs-as-tools, action tools, core tools, skill
   + tool catalogs), one model call per tick, bounded by ``activation_budget``.
   When ``lock_active_flow`` is on and a control-task points to an IA, the loop's
   callable surface is restricted to that IA's tool and it is dispatched
   immediately (mechanistic turn-lock); otherwise the active flow is surfaced as
   routable context the model may continue or leave for an off-topic request.
3. **Directive finalize** — voice any directives a rails IA-tool left unrendered.

Routing is tool selection; turn-lock is deterministic (``lock_active_flow``) or
an emergent flow property.
"""

from __future__ import annotations

import inspect
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.skill_executive.access import delegate_resource_label
from jvagent.action.skill_executive.catalog import (
    build_catalog_tools,
    build_skill_meta_tools,
)
from jvagent.action.skill_executive.continuation import (
    active_flow_note,
    active_flow_owner,
)
from jvagent.action.skill_executive.core_tools import build_core_tools
from jvagent.action.skill_executive.prompts import (
    SKILL_EXECUTIVE_SYSTEM_PROMPT,
    SKILL_EXECUTIVE_USER_PROMPT_TEMPLATE,
    render_history_section,
)
from jvagent.action.skill_executive.skills import discover_skill_docs
from jvagent.action.skill_executive.tools import (
    SkillTool,
    parse_json_object,
    render_observations_section,
    render_tools_section,
    wrap_action_tool,
)

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

DEFAULT_ACTIVATION_BUDGET = 16

# Keys the model commonly uses to carry user-facing text, in priority order.
_TEXT_KEYS = ("answer", "text", "content", "message", "reply", "response")


def _text_candidate(decision: Dict[str, Any]) -> str:
    """Pull the first non-empty user-facing string from a model decision."""
    for key in _TEXT_KEYS:
        val = decision.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


class SkillExecutiveInteractAction(InteractAction):
    """The sole pattern orchestrator (ADR-0012), weight ``-200``."""

    weight: int = attribute(
        default=-200,
        description="Pattern-orchestrator slot at -200 (the sole turn orchestrator).",
    )
    description: str = attribute(
        default=(
            "Skill-executive orchestrator: runs a think-act-observe loop over "
            "the agent's tool surface (persona, anchored IAs, actions, core "
            "tools, skills); an in-progress flow continues when the model "
            "selects its tool."
        )
    )

    model: str = attribute(default="gpt-4o-mini")
    model_action_type: str = attribute(default="OpenAILanguageModelAction")
    model_temperature: float = attribute(default=0.2)
    model_max_tokens: int = attribute(default=1024)
    enforce_json_mode: bool = attribute(default=True)
    activation_budget: int = attribute(
        default=DEFAULT_ACTIVATION_BUDGET,
        description="Hard cap on think-act-observe iterations per turn.",
    )
    history_limit: int = attribute(default=4)
    persona_action: str = attribute(
        default="PersonaAction",
        description="Class name of the persona action furnishing reply/respond.",
    )
    clarify_text: str = attribute(
        default="Sorry, I didn't quite catch that — could you rephrase?",
    )
    lock_active_flow: bool = attribute(
        default=True,
        description=(
            "When True, an active flow control-task routes the turn "
            "deterministically to its IA (mechanistic turn-lock, bypassing the "
            "model loop). When False, the flow's tool is surfaced and "
            "continuation is model-mediated."
        ),
    )

    # -- Skill overlay (native SOP skills; ADR-0011) ------------------------
    skills_source: str = attribute(default="both")
    skills: Any = attribute(default="-all")
    denied_skills: List[str] = attribute(default_factory=list)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def execute(self, visitor: "InteractWalker") -> None:
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        before = getattr(interaction, "response", "") or ""

        # Curate the remaining walk path: routable IAs (exposed as tools) must
        # NOT also self-execute as weight-chain members — they are reached only
        # by the model selecting their tool. Keep self + always_execute IAs +
        # non-routable IAs (ADR-0012; pipeline citizenship).
        await self._curate_walk_path(visitor)

        # Think-act-observe loop over the unified tool surface. When
        # lock_active_flow is on and a control-task points to an IA, the loop's
        # callable surface is restricted to that IA's tool (mechanistic
        # turn-lock — see _run_loop); otherwise the active flow is surfaced as
        # routable context the model may continue or leave for an off-topic
        # request.
        await self._run_loop(visitor)

        # A rails IA invoked as a tool emits via interaction.directives rather
        # than publishing — render any it left through the persona.
        await self._finalize_directives(visitor)

        # Light egress fallback: nothing voiced this turn → one default reply.
        after = getattr(interaction, "response", "") or ""
        if after == before:
            await self.publish(visitor=visitor, content=self.clarify_text)

    async def _finalize_directives(self, visitor: "InteractWalker") -> None:
        """Voice any unrendered ``interaction.directives`` through the persona.

        Rails IAs deliver via the directive pattern (``visitor.add_directive``)
        rather than publishing. When an IA-tool runs and leaves directives
        without setting a response, this renders them through the persona.
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        if getattr(interaction, "response", "") or "":
            return  # already voiced
        try:
            unexecuted = interaction.get_unexecuted_directives()
        except Exception:
            unexecuted = None
        if not unexecuted:
            return
        persona = await self._resolve_action(self.persona_action)
        if persona is None:
            return
        try:
            await persona.respond(interaction, visitor=visitor)
        except Exception as exc:
            logger.warning("skill_executive: directive finalize failed: %s", exc)

    async def _resolve_action(self, name: str) -> Optional[Any]:
        try:
            return await self.get_action(name)
        except Exception as exc:
            logger.debug("skill_executive: get_action(%r) raised: %s", name, exc)
            return None

    async def _curate_walk_path(self, visitor: "InteractWalker") -> None:
        """Drop tool-exposed (routable) IAs from the remaining walk path.

        An anchored IA furnishes a tool via ``get_tools()`` and is reached only
        when the model selects that tool — it must NOT also run as an ordinary
        weight-chain member every turn (that was the "always triggered" cause).
        We keep: this orchestrator, ``always_execute`` IAs (auth/intro/audit),
        and any non-routable IA (no routing triggers → not a tool, so it should
        run in the chain). Best-effort — never breaks the turn.
        """
        curate = getattr(visitor, "curate_walk_path", None)
        if not callable(curate):
            return
        agent = await self._safe_agent()
        keep: List[Any] = [self]
        for action in await self._enabled_actions(agent):
            if action is self or isinstance(action, SkillExecutiveInteractAction):
                continue
            if getattr(action, "always_execute", False):
                keep.append(action)
                continue
            triggers_fn = getattr(action, "routing_triggers", None)
            triggers = (
                list(triggers_fn() or [])
                if callable(triggers_fn)
                else list(getattr(action, "anchors", None) or [])
            )
            if callable(getattr(action, "execute", None)) and triggers:
                continue  # routable/tool IA — omit from the walk path
            keep.append(action)  # non-routable / non-flow action — keep
        # ``curate_walk_path`` itself filters to InteractActions actually in the
        # remaining queue, so non-walk actions in ``keep`` are harmless.
        try:
            await curate(keep)
        except Exception as exc:
            logger.debug("skill_executive: curate_walk_path failed: %s", exc)

    # ------------------------------------------------------------------
    # Tool surface (per-turn; binds the visitor)
    # ------------------------------------------------------------------

    async def _assemble_tools(
        self,
        visitor: "InteractWalker",
        activated: List[str],
        visible: Set[str],
        flow_owner: Optional[str],
        utterance: str,
    ) -> Dict[str, SkillTool]:
        """Build the full tool surface and populate ``visible`` (the prompt set).

        Everything goes into the returned ``tools`` (so ``find_tool`` can surface
        anything). ``visible`` — what the model sees up front — holds the
        general tools always, but a turn-spanning flow's IA-tool ONLY when it is
        the active flow or the utterance is anchor-relevant. This keeps idle
        flow tools out of the prompt so a weak model can't spuriously trigger an
        interview on a greeting (the "always triggered" misroute).
        """
        agent = await self._safe_agent()
        tools: Dict[str, SkillTool] = {}

        # Core tools (always visible).
        for t in build_core_tools(self):
            tools[t.name] = t
            visible.add(t.name)

        persona = None
        actions = await self._enabled_actions(agent)
        from jvagent.action.persona.persona_action import PersonaAction

        for action in actions:
            if action is self or isinstance(action, SkillExecutiveInteractAction):
                continue
            if isinstance(action, PersonaAction):
                persona = action
                continue
            # A turn-spanning flow (duck-typed: execute + routing triggers)
            # furnishes its own tool via get_tools(); the orchestrator binds the
            # visitor + AC and gates it into the visible set by relevance.
            # Routing triggers come from the IA's manifest entry intents (clean),
            # NOT its runtime-merged anchor catalog.
            has_execute = callable(getattr(action, "execute", None))
            triggers_fn = getattr(action, "routing_triggers", None)
            triggers = (
                list(triggers_fn() or [])
                if callable(triggers_fn)
                else list(getattr(action, "anchors", None) or [])
            )
            is_flow = (
                has_execute
                and bool(triggers)
                and not getattr(action, "always_execute", False)
            )
            get_tools = getattr(action, "get_tools", None)
            if not callable(get_tools):
                continue
            try:
                action_tools = await get_tools() or []
            except Exception as exc:
                logger.debug("skill_executive: get_tools failed on %s: %s", action, exc)
                continue
            for tool in action_tools:
                name = getattr(tool, "name", None)
                if not name:
                    continue
                if is_flow:
                    # IA-as-tool: visitor-bound (forwards to execute()), AC-gated
                    # on tool:delegate:{name}, terminal (owns the turn's output).
                    # Visible only when active or anchor-relevant.
                    tools[name] = wrap_action_tool(
                        tool,
                        visitor=visitor,
                        terminal=True,
                        agent=agent,
                        user_id=getattr(visitor, "user_id", None),
                        channel=getattr(visitor, "channel", "default") or "default",
                        access_label=delegate_resource_label(name),
                    )
                    if name == flow_owner or self._anchor_relevant(utterance, triggers):
                        visible.add(name)
                else:
                    # Plain capability tool (always visible).
                    tools[name] = wrap_action_tool(tool)
                    visible.add(name)

        # Persona reply/respond (always visible). These publish through the
        # walker, so the visitor is injected into their dispatch.
        if persona is not None:
            get_persona_tools = getattr(persona, "get_tools", None)
            try:
                persona_tools = (
                    get_persona_tools() or [] if callable(get_persona_tools) else []
                )
            except Exception as exc:
                logger.debug("skill_executive: persona get_tools failed: %s", exc)
                persona_tools = []
            for tool in persona_tools:
                name = getattr(tool, "name", None)
                if not name:
                    continue
                tools[name] = wrap_action_tool(tool, visitor=visitor)
                visible.add(name)

        # Native SOP skills (progressive disclosure; meta-tools visible).
        docs = self._discover_skills(agent)
        for name, t in build_skill_meta_tools(
            docs, set(tools.keys()), activated
        ).items():
            tools[name] = t
            visible.add(name)

        # Tool catalog (find_tool/load_tool — visible so hidden tools are reachable).
        for name, t in build_catalog_tools(tools, visible).items():
            tools[name] = t
            visible.add(name)
        return tools

    @staticmethod
    def _anchor_relevant(utterance: str, anchors: List[str]) -> bool:
        """True if the utterance shares a meaningful keyword with any anchor.

        A lightweight first-entry relevance gate (no model call) so an anchored
        flow's tool is surfaced only when the user's message plausibly concerns
        it. Token overlap on significant (len>2, non-stopword) words.
        """
        if not anchors:
            return False
        stop = {
            "the",
            "and",
            "for",
            "with",
            "you",
            "your",
            "are",
            "can",
            "could",
            "would",
            "want",
            "like",
            "need",
            "please",
            "this",
            "that",
            "have",
            "how",
            "what",
            "who",
            "when",
            "where",
            "why",
            "about",
            "into",
            "from",
            "get",
            "got",
            "tell",
            "let",
            "all",
            "any",
            "out",
            "use",
            "now",
        }

        def toks(s: str) -> set:
            return {
                w
                for w in re.findall(r"[a-z0-9]+", (s or "").lower())
                if len(w) > 2 and w not in stop
            }

        u = toks(utterance)
        if not u:
            return False
        a: set = set()
        for anc in anchors:
            a |= toks(anc)
        return bool(u & a)

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    async def _run_loop(self, visitor: "InteractWalker") -> None:
        activated: List[str] = []
        visible: Set[str] = set()
        utterance = getattr(visitor, "utterance", "") or ""

        # Resolve the active flow first so the surface gates its tool into the
        # prompt only when relevant (active flow, or anchor-relevant utterance).
        flow_owner = active_flow_owner(visitor)
        tools = await self._assemble_tools(
            visitor, activated, visible, flow_owner, utterance
        )

        # Hard turn-lock (lock_active_flow): when a control-task points to an IA
        # that furnished a tool, restrict the callable surface to that one tool
        # and dispatch it — the loop can only continue the flow, never route
        # elsewhere. The IA's tool is visitor-bound, AC-gated, and terminal
        # (so it owns the turn's output). The IA receives all input including
        # off-topic; interruption/cancel is the IA's own concern.
        if self.lock_active_flow and flow_owner and flow_owner in tools:
            await tools[flow_owner].run({})
            await self._record_executive_activation(
                visitor,
                continuation_mode="locked",
                flow_owner=flow_owner,
                tools_invoked=[flow_owner],
                tick_count=0,
                ended_via="locked",
                activated=activated,
            )
            return

        flow_note = active_flow_note(flow_owner) if flow_owner else ""

        observations: List[Dict[str, Any]] = []
        budget = max(1, int(self.activation_budget))
        history = await self._history(visitor)
        ticks = 0
        ended_via = "budget"

        try:
            while budget > 0:
                budget -= 1
                ticks += 1
                visible_tools = [tools[n] for n in visible if n in tools]
                decision = await self._run_model(
                    visitor, utterance, history, visible_tools, observations, flow_note
                )
                if decision is None:
                    ended_via = "no_decision"
                    return
                action, tool_name, args = self._normalize(decision, tools)
                if action == "final":
                    answer = _text_candidate(decision)
                    if answer:
                        await self._maybe_voice_final(visitor, answer)
                    ended_via = "final"
                    return
                if action == "tool":
                    tool = tools.get(tool_name)
                    if tool is None:
                        obs = f"(no such tool: {tool_name})"
                    else:
                        try:
                            obs = await tool.run(args)
                        except Exception as exc:
                            logger.warning(
                                "skill_executive: tool %r raised: %s", tool_name, exc
                            )
                            obs = f"(tool error: {exc})"
                    observations.append(
                        {"tool": tool_name, "args": args, "observation": obs}
                    )
                    # End the turn once the user has been addressed: a persona
                    # reply (by name) or a terminal IA-tool that owns its own
                    # output. This also stops a model that keeps choosing the
                    # same tool from looping until the budget is exhausted.
                    if tool_name in ("reply", "respond") or (
                        tool is not None and tool.terminal
                    ):
                        ended_via = (
                            tool_name
                            if tool_name in ("reply", "respond")
                            else "ia_tool"
                        )
                        return
                    continue
                # Unknown action — stop rather than loop.
                ended_via = "unknown"
                return
        finally:
            await self._record_executive_activation(
                visitor,
                continuation_mode="model_mediated" if flow_owner else "none",
                flow_owner=flow_owner,
                tools_invoked=[o.get("tool") for o in observations],
                tick_count=ticks,
                ended_via=ended_via,
                activated=activated,
            )

    async def _record_executive_activation(
        self,
        visitor: "InteractWalker",
        *,
        continuation_mode: str,
        flow_owner: Optional[str],
        tools_invoked: List[Optional[str]],
        tick_count: int,
        ended_via: str,
        activated: List[str],
    ) -> None:
        """Append a per-turn ``executive_activation`` event to
        ``interaction.observability_metrics``.

        The orchestrator's own trace — continuation mode, the tools it invoked,
        ticks consumed, and how the turn ended — recorded alongside the
        per-``model_call`` events (it does not replace or alter them). This is
        the turn-level story the model-call events alone don't tell. Best-effort;
        never breaks the turn.
        """
        interaction = getattr(visitor, "interaction", None)
        metrics = getattr(interaction, "observability_metrics", None)
        if metrics is None or not hasattr(metrics, "append"):
            return
        event = {
            "event_type": "executive_activation",
            "data": {
                "continuation_mode": continuation_mode,
                "flow_owner": flow_owner,
                "lock_active_flow": bool(self.lock_active_flow),
                "tools_invoked": [t for t in tools_invoked if t],
                "tick_count": int(tick_count),
                "budget": int(self.activation_budget),
                "ended_via": ended_via,
                "skills_used": list(activated or []),
            },
            "timestamp": time.time(),
        }
        try:
            metrics.append(event)
            saver = getattr(interaction, "save", None)
            if callable(saver):
                result = saver()
                if inspect.isawaitable(result):
                    await result
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("skill_executive: activation record failed: %s", exc)

    async def _maybe_voice_final(self, visitor: "InteractWalker", answer: str) -> None:
        """If the loop ends with text but nothing was voiced, publish it once."""
        interaction = getattr(visitor, "interaction", None)
        if interaction is not None and interaction.response:
            return  # already voiced via reply/respond/IA
        await self.publish(visitor=visitor, content=answer)

    @staticmethod
    def _normalize(decision: Dict[str, Any], tools: Dict[str, SkillTool]):
        """Normalize a model decision into ``(action, tool_name, args)``.

        Tolerant of common near-miss shapes the model emits, e.g.
        ``{"action":"reply","answer":"hi"}`` (tool name in ``action``, text in
        ``answer`` rather than ``args.text``) or ``{"tool":"x","args":{...}}``
        with no ``action``. For the persona text tools (``reply``/``respond``),
        the text is salvaged from ``answer``/``text``/``content``/``message``
        into ``args.text`` so a near-miss shape doesn't waste the step budget.
        """
        action = (decision.get("action") or "").strip().lower()
        tool_field = (decision.get("tool") or "").strip()
        text = _text_candidate(decision)
        if action not in ("tool", "final"):
            if tool_field:
                action = "tool"
            elif action in tools:
                tool_field = decision.get("action") or ""
                action = "tool"
            elif text and "reply" in tools:
                # Bare text with no recognizable action → speak it.
                tool_field, action = "reply", "tool"
            elif text:
                action = "final"
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        if (
            action == "tool"
            and tool_field in ("reply", "respond")
            and not args.get("text")
        ):
            if text:
                args = {**args, "text": text}
        return action, tool_field, args

    # ------------------------------------------------------------------
    # Helpers (overridable in tests)
    # ------------------------------------------------------------------

    async def _safe_agent(self) -> Any:
        try:
            return await self.get_agent()
        except Exception:
            return None

    async def _enabled_actions(self, agent: Any) -> List[Any]:
        if agent is None:
            return []
        try:
            mgr = await agent.get_actions_manager()
            return await mgr.get_all_actions(enabled_only=True) if mgr else []
        except Exception as exc:
            logger.debug("skill_executive: action enumeration failed: %s", exc)
            return []

    def _discover_skills(self, agent: Any) -> List[Any]:
        try:
            return discover_skill_docs(
                agent,
                skills_source=self.skills_source,
                selector=self.skills,
                denied=list(self.denied_skills or []),
            )
        except Exception as exc:
            logger.debug("skill_executive: skill discovery failed: %s", exc)
            return []

    async def _history(self, visitor: "InteractWalker") -> List[Dict[str, str]]:
        interaction = getattr(visitor, "interaction", None)
        conversation = getattr(visitor, "conversation", None)
        if conversation is None or interaction is None:
            return []
        getter = getattr(conversation, "get_interaction_history", None)
        if not callable(getter):
            return []
        try:
            return (
                await getter(
                    limit=int(self.history_limit),
                    excluded=getattr(interaction, "id", None),
                    formatted=True,
                )
                or []
            )
        except Exception:
            return []

    async def _run_model(
        self,
        visitor: "InteractWalker",
        utterance: str,
        history: List[Dict[str, str]],
        tools: List[SkillTool],
        observations: List[Dict[str, Any]],
        flow_note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """One model call → parsed JSON decision. Overridden/mocked in tests."""
        model_action = await self.get_model_action(required=False)
        if model_action is None:
            logger.warning(
                "skill_executive: no model action (%s)", self.model_action_type
            )
            return None
        system_prompt = SKILL_EXECUTIVE_SYSTEM_PROMPT.format(
            tools_section=render_tools_section(tools)
        )
        if flow_note:
            system_prompt = f"{system_prompt}\n\nFLOW IN PROGRESS:\n{flow_note}"
        user_prompt = SKILL_EXECUTIVE_USER_PROMPT_TEMPLATE.format(
            history_section=render_history_section(history),
            utterance=utterance or "(no message)",
            observations_section=render_observations_section(observations),
        )
        kwargs: Dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "system": system_prompt,
            "prompt_for_observability": user_prompt,
            "tools": None,
            "model": self.model or None,
            "temperature": self.model_temperature,
            "max_tokens": self.model_max_tokens,
            "calling_action_name": self.get_class_name(),
        }
        if self.enforce_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            result = await model_action.query_messages(**kwargs)
        except Exception as exc:
            logger.warning("skill_executive: model call raised: %s", exc)
            return None
        raw = (getattr(result, "response", None) or "").strip()
        return parse_json_object(raw) if raw else None


__all__ = ["SkillExecutiveInteractAction"]
