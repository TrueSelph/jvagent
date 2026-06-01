"""OrchestratorInteractAction — the single orchestrator (ADR-0012).

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
3. **Directive finalize** — emit any directives a rails IA-tool left unrendered.

Routing is tool selection; turn-lock is deterministic (``lock_active_flow``) or
an emergent flow property.
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional, Set, Tuple

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.orchestrator.access import delegate_resource_label
from jvagent.action.orchestrator.catalog import (
    build_catalog_tools,
    build_skill_meta_tools,
)
from jvagent.action.orchestrator.continuation import (
    active_flow_note,
    active_flow_owner,
    active_plan,
    plan_resume_note,
)
from jvagent.action.orchestrator.core_tools import build_core_tools, build_plan_tool
from jvagent.action.orchestrator.prompts import (
    FINALIZE_PROMPT,
    FLOW_IN_PROGRESS_PROMPT,
    LENGTH_LIMIT_PROMPT,
    NO_SKILLS_AVAILABLE,
    ORCHESTRATOR_SYSTEM_PROMPT,
    ORCHESTRATOR_USER_PROMPT_TEMPLATE,
    PLANNING_PROMPT,
    TOOL_USE_POLICY,
    render_history_section,
    render_identity_section,
    render_skills_section,
)
from jvagent.action.orchestrator.skills import discover_skill_docs
from jvagent.action.orchestrator.tools import (
    SkillTool,
    parse_json_object,
    render_observations_section,
    render_tools_section,
    wrap_action_tool,
)
from jvagent.tooling.tool_executor import bind_dispatch_context

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

DEFAULT_ACTIVATION_BUDGET = 24

# Keys the model commonly uses to carry user-facing text, in priority order.
_TEXT_KEYS = ("answer", "text", "content", "message", "reply", "response")

# Egress + indirection tools are never "steered" — saying "reply" is normal, and
# find_tool/use_skill are the sanctioned indirection we *want*. The same set is
# "non-substantive" for gearing: these don't count toward heavy-model escalation.
_STEER_EXEMPT = frozenset(
    {"reply", "respond", "find_tool", "load_tool", "find_skill", "use_skill"}
)
_NON_SUBSTANTIVE_TOOLS = _STEER_EXEMPT

# Significant-token stopwords for the lightweight relevance gates (flow
# anchoring + lean tool pre-surfacing). No model call — cheap token overlap.
_STOPWORDS = frozenset(
    {
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
)


def _significant_tokens(s: str) -> set:
    """Lowercase alnum tokens, len>2, minus stopwords — for relevance overlap."""
    return {
        w
        for w in re.findall(r"[a-z0-9]+", (s or "").lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def _text_candidate(decision: Dict[str, Any]) -> str:
    """Pull the first non-empty user-facing string from a model decision."""
    for key in _TEXT_KEYS:
        val = decision.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


class OrchestratorInteractAction(InteractAction):
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

    # The HEAVY profile — the reasoning/primary model (with reasoning_* below).
    model: str = attribute(default="gpt-4o-mini")
    model_action_type: str = attribute(default="OpenAILanguageModelAction")
    model_temperature: float = attribute(default=0.2)
    model_max_tokens: int = attribute(
        default=4096,
        description=(
            "Completion ceiling for the HEAVY reasoning model. The orchestrator "
            "is agentic by nature — each tick emits reasoning plus an action "
            "(often the substantive final answer), and thinking models spend "
            "tokens on reasoning that count against this budget — so it needs "
            "more headroom than a single-shot responder (a 2048 ceiling "
            "truncated long answers). The repeat-guard and activation_budget "
            "still bound runaway loops."
        ),
    )
    enforce_json_mode: bool = attribute(default=True)

    # -- Model gearing (ADR-0016): a LIGHT completion model for single-dimensional
    # turns (reply / one tool), the HEAVY model above for multi-step work. Set
    # `light_model` to engage gearing; empty = single-model (current behaviour).
    light_model: str = attribute(
        default="",
        description="Light/completion model id. Empty disables gearing. If set "
        "with no main `model`, the light model becomes the sole model (fallback).",
    )
    light_model_action_type: str = attribute(
        default="",
        description="LM action for the light model; empty = same as the heavy "
        "model_action_type.",
    )
    light_model_temperature: float = attribute(default=0.2)
    light_model_max_tokens: int = attribute(default=1024)
    escalate_after_tool_calls: int = attribute(
        default=2,
        description="Switch to the heavy model once the turn has made this many "
        "substantive tool calls (reply/respond/catalog/skill meta-tools excluded).",
    )
    escalate_on_skill: bool = attribute(
        default=True,
        description="Activating a skill (a multi-step SOP) escalates to heavy "
        "immediately.",
    )

    # -- Reasoning passthrough (only bites with a reasoning-capable model; the
    # default gpt-4o-mini ignores it). Threaded into the loop's model call so
    # the executive profile owns its own reasoning level. -------------------
    reasoning_enabled: Optional[bool] = attribute(
        default=None,
        description="Tri-state reasoning toggle. None = leave to the model "
        "action; True/False explicitly enable/disable for the loop call.",
    )
    reasoning_effort: Optional[str] = attribute(
        default=None,
        description="Reasoning effort hint (low | medium | high) for "
        "reasoning-capable models.",
    )
    reasoning_budget_tokens: int = attribute(
        default=0,
        description="Explicit thinking-token budget (e.g. Anthropic); 0 = let "
        "the provider map it from effort.",
    )
    reasoning_extra: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="Provider-specific reasoning params passed through verbatim.",
    )

    # -- Thinking / progress stream (only emits over a live response bus) ---
    stream_internal_progress: bool = attribute(
        default=False,
        description="When True, emit each loop tick's step as a transient "
        "'thought' bubble to the UI.",
    )
    stream_reasoning_trace: bool = attribute(
        default=False,
        description="When True, surface a reasoning model's thinking trace "
        "(result.thinking_content) as a transient thought, when the provider "
        "returns one.",
    )

    activation_budget: int = attribute(
        default=DEFAULT_ACTIVATION_BUDGET,
        description="Hard cap on think-act-observe iterations per turn.",
    )
    max_duration_seconds: float = attribute(
        default=0.0,
        description="Wall-clock cap on the whole turn (alongside the tick "
        "budget); 0 disables.",
    )
    max_statement_length: Optional[int] = attribute(
        default=None,
        description="Soft cap (characters) on the reply, applied as a prompt "
        "instruction; None disables.",
    )
    history_limit: int = attribute(default=4)
    clarify_text: str = attribute(
        default="Sorry, I didn't quite catch that — could you rephrase?",
    )
    # -- Prompt surface (every sub-prompt is overridable from agent.yaml).
    # Each defaults to the constant in prompts.py; placeholders shown below must
    # be preserved when overriding, and literal ``{`` / ``}`` doubled (``{{`` /
    # ``}}``) since these are str.format templates.
    system_prompt: str = attribute(
        default=ORCHESTRATOR_SYSTEM_PROMPT,
        description=(
            "The executive's main system-prompt body. Placeholders: "
            "{identity_section}, {tools_section}, {skills_section}."
        ),
    )
    system_prompt_extra: str = attribute(
        default="",
        description=(
            "Extra instructions appended to the system prompt each tick (after "
            "the base body, before the dynamic flow/length/policy sections). "
            "Safe additive override — leaves the base contract intact."
        ),
    )
    user_prompt: str = attribute(
        default=ORCHESTRATOR_USER_PROMPT_TEMPLATE,
        description=(
            "Per-tick user-prompt template. Placeholders: {history_section}, "
            "{utterance}, {observations_section}."
        ),
    )
    tool_use_policy_prompt: str = attribute(
        default=TOOL_USE_POLICY,
        description="Appended when block_raw_tool_invocation is on.",
    )
    flow_in_progress_prompt: str = attribute(
        default=FLOW_IN_PROGRESS_PROMPT,
        description="Appended while a flow is active. Placeholder: {flow_note}.",
    )
    length_limit_prompt: str = attribute(
        default=LENGTH_LIMIT_PROMPT,
        description="Appended when max_statement_length is set. Placeholder: {max_chars}.",
    )
    finalize_prompt: str = attribute(
        default=FINALIZE_PROMPT,
        description="Appended on the partial-compose finalize tick (no placeholders).",
    )
    no_skills_text: str = attribute(
        default=NO_SKILLS_AVAILABLE,
        description="Shown in the AVAILABLE SKILLS slot when no skills load.",
    )
    planning_prompt: str = attribute(
        default=PLANNING_PROMPT,
        description="Appended when planning is on (no placeholders). Nudges "
        "update_plan use for multi-step work.",
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
    planning: bool = attribute(
        default=False,
        description=(
            "When True, surface the `update_plan` tool so the model can record a "
            "multi-step plan that PERSISTS on the conversation TaskStore "
            "(task_type AGENTIC_LOOP). An unfinished plan is re-surfaced next "
            "turn so an interrupted multi-step turn resumes instead of "
            "re-planning (ADR-0019). Off by default — zero cost when unused; "
            "when on, cost is incurred only when the model calls update_plan."
        ),
    )

    # -- Skill overlay (native SOP skills; ADR-0011) ------------------------
    skills_source: str = attribute(default="both")
    skills: Any = attribute(default="-all")
    denied_skills: List[str] = attribute(default_factory=list)

    # -- Tooling / egress-UX controls (restored from Bridge/Helm) -----------
    enable_transient_ack: bool = attribute(
        default=False,
        description="Master switch: emit transient 'working on it' ack(s) while "
        "a slow turn runs (needs a live bus).",
    )
    first_emit_timeout_ms: int = attribute(
        default=1200,
        description="Delay (ms) before the FIRST ack fires. 0 = emit immediately. "
        "The ack only arms once a turn is complex (a skill, or multiple "
        "substantive tool calls), so simple turns never surface it.",
    )
    ack_interval_ms: int = attribute(
        default=12000,
        description="Delay (ms) between successive ack_statements after the "
        "first (kept generous so later lines don't appear too soon).",
    )
    ack_statements: List[str] = attribute(
        default_factory=lambda: ["One moment…", "Still working on it…"],
        description="Transient 'working on it' statement(s), emitted in order: "
        "the first after first_emit_timeout_ms, each subsequent after "
        "ack_interval_ms, while the turn runs.",
    )
    block_raw_tool_invocation: bool = attribute(
        default=False,
        description="When True: (1) the loop may only call tools currently "
        "surfaced (visible) — hidden tools must be reached via find_tool / a "
        "skill; and (2) a TOOL-USE POLICY is added so the user cannot steer "
        "tool selection — they state a goal, the agent chooses the tools.",
    )
    tool_tier: str = attribute(
        default="standard",
        description="Core-tool tier: minimal | standard | full.",
    )
    tool_call_timeout: float = attribute(
        default=0.0,
        description="Per-tool-call timeout (seconds); 0 disables.",
    )
    tool_thought_max_chars: int = attribute(
        default=0,
        description="Max characters of a tool result surfaced in the transient "
        "tool_call/tool_result thought (the TOOL CALLS UI detail). Default 0 = "
        "NO CAP — the full result is sent so structured results (e.g. JSON) "
        "stay complete and parseable in the UI; truncating mid-value yields "
        "invalid JSON. Set a positive value only to bound very large results "
        "on the bus envelope (the model always sees the full observation).",
    )
    lean_tool_threshold: int = attribute(
        default=15,
        description="Lean tool surfacing engages when the count of hideable "
        "capability tools (action + MCP tools) exceeds this — the long tail is "
        "kept off the prompt and reached via find_tool, keeping each loop tick "
        "small. 0 disables (always list every tool). Egress, meta-tools, core "
        "tools and active-flow tools are always visible regardless.",
    )
    lean_presurface_k: int = attribute(
        default=6,
        description="In lean mode, how many capability tools to pre-surface each "
        "turn by relevance to the user's message (token overlap, no model call), "
        "so common single-intent turns need no find_tool round-trip.",
    )
    pinned_tools: List[str] = attribute(
        default_factory=list,
        description="Tool-name globs (e.g. 'filing__*', 'case__create') that stay "
        "VISIBLE every turn even under lean surfacing — for capabilities that must "
        "be callable turn-1 regardless of how the user phrases things, without "
        "disabling lean for the rest. Empty by default. The skill-native "
        "equivalent is a SKILL.md with 'always-active: true' (pins its "
        "allowed-tools).",
    )

    # -- MCP tool servers (via jvagent/mcp MCPAction; ADR-0015) -------------
    tool_servers: Any = attribute(
        default="-all",
        description="MCP gateway actions to pull tools from: '-all' for every "
        "enabled MCPAction, or a finite list of action names.",
    )
    max_concurrent_tools: int = attribute(
        default=0,
        description="Bound on concurrent tool execution; 0 = unbounded.",
    )

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
        # request. The dispatch context is bound for the whole turn so
        # context-aware tools (per-user MCP servers) route correctly.
        with bind_dispatch_context(visitor):
            await self._run_loop(visitor)

        # A rails IA invoked as a tool emits via interaction.directives rather
        # than publishing — render any it left through the responder.
        await self._finalize_directives(visitor)

        # Light egress fallback: nothing emitted this turn → one default reply,
        # routed through the responder (channel formatting + no-bus safe).
        after = getattr(interaction, "response", "") or ""
        if after == before:
            await self._emit_reply(visitor, self.clarify_text)

    @staticmethod
    def _ia_emitted(interaction: Any) -> bool:
        """True if a dispatched IA produced user-facing output this turn.

        An IA emits either by setting ``interaction.response`` OR by queuing a
        directive (the rails/interview pattern, rendered by
        ``_finalize_directives`` after the loop). The locked path uses this so it
        doesn't mistake directive-based publishing for silence and echo the
        IA-as-tool status sentinel.
        """
        if interaction is None:
            return False
        if (getattr(interaction, "response", "") or "").strip():
            return True
        try:
            return bool(interaction.get_unexecuted_directives())
        except Exception:
            return False

    async def _finalize_directives(self, visitor: "InteractWalker") -> None:
        """Render any unrendered ``interaction.directives`` through the responder.

        Rails IAs deliver via the directive pattern (``visitor.add_directive``)
        rather than publishing. When an IA-tool runs and leaves directives
        without setting a response, this renders them through the responder
        (ReplyAction or PersonaAction fallback; ADR-0014).
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None:
            return
        if getattr(interaction, "response", "") or "":
            return  # already emitted
        try:
            unexecuted = interaction.get_unexecuted_directives()
        except Exception:
            unexecuted = None
        if not unexecuted:
            return
        responder = await self.get_responder()
        if responder is None:
            return
        try:
            await responder.respond(interaction, visitor=visitor)
        except Exception as exc:
            logger.warning("orchestrator: directive finalize failed: %s", exc)

    async def _resolve_action(self, name: str) -> Optional[Any]:
        try:
            return await self.get_action(name)
        except Exception as exc:
            logger.debug("orchestrator: get_action(%r) raised: %s", name, exc)
            return None

    async def _enforce_required_actions(self, docs: List[Any]) -> List[Any]:
        """Drop skills whose ``requires-actions`` don't all resolve (hard gate).

        Each distinct required Action *type* is resolved once (enabled-only,
        O(1) cached). A skill is kept only when every type it declares is
        present; otherwise it's hidden from the whole surface (list, find_skill,
        use_skill, always-active pinning) so the model never sees a skill whose
        dependencies are missing. Skills with no ``requires-actions`` pass
        through unchanged.
        """
        required: Set[str] = set()
        for d in docs:
            required.update(getattr(d, "requires_actions", ()) or ())
        if not required:
            return docs

        present: Set[str] = set()
        for type_name in required:
            if await self._resolve_action(type_name) is not None:
                present.add(type_name)

        kept: List[Any] = []
        for d in docs:
            needed = set(getattr(d, "requires_actions", ()) or ())
            missing = needed - present
            if missing:
                logger.info(
                    "orchestrator: skill %r hidden — required actions not "
                    "available: %s",
                    getattr(d, "name", "?"),
                    ", ".join(sorted(missing)),
                )
                continue
            kept.append(d)
        return kept

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
            if action is self or isinstance(action, OrchestratorInteractAction):
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
            logger.debug("orchestrator: curate_walk_path failed: %s", exc)

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
        skill_docs: Optional[List[Any]] = None,
        surface_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, SkillTool]:
        """Build the full tool surface and populate ``visible`` (the prompt set).

        Everything goes into the returned ``tools`` (so ``find_tool`` can surface
        anything). ``visible`` — what the model sees up front — holds the
        general tools always, but a turn-spanning flow's IA-tool ONLY when it is
        the active flow or the utterance is anchor-relevant. This keeps idle
        flow tools out of the prompt so a weak model can't spuriously trigger an
        interview on a greeting (the "always triggered" misroute).

        **Lean surfacing** (ADR-0018): plain action tools and MCP tools — the
        hideable *long tail* — are collected separately. When their count exceeds
        ``lean_tool_threshold`` the prompt keeps only the relevance pre-surfaced
        few (plus always-on egress/meta/core); the rest stay on the full surface,
        reachable via ``find_tool``. Below the threshold every tool is listed
        (unchanged). ``surface_meta["lean"]`` reports which path was taken.
        """
        agent = await self._safe_agent()
        tools: Dict[str, SkillTool] = {}
        # Hideable capability tools (action + MCP). Surfaced per the lean policy
        # applied after assembly; everything else is always visible.
        longtail: Set[str] = set()

        # Core tools (always visible), gated by tool_tier.
        for t in build_core_tools(self, self.tool_tier):
            tools[t.name] = t
            visible.add(t.name)

        # Resumable-plan tool (ADR-0019). Surfaced only when planning is on, so
        # a lean agent pays nothing; visitor-bound for TaskStore access.
        if self.planning:
            plan_tool = build_plan_tool(self, visitor)
            tools[plan_tool.name] = plan_tool
            visible.add(plan_tool.name)

        actions = await self._enabled_actions(agent)
        from jvagent.action.persona.persona_action import PersonaAction
        from jvagent.action.reply.reply_action import ReplyAction

        mcp_cls = self._mcp_action_class()

        for action in actions:
            if action is self or isinstance(action, OrchestratorInteractAction):
                continue
            if isinstance(action, (ReplyAction, PersonaAction)):
                continue
            # MCP gateways are surfaced by the dedicated, tool_servers-gated
            # block below — skip here so the gate is authoritative.
            if mcp_cls is not None and isinstance(action, mcp_cls):
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
                logger.debug("orchestrator: get_tools failed on %s: %s", action, exc)
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
                    # Plain capability tool — hideable long tail (lean policy
                    # decides visibility after assembly).
                    tools[name] = wrap_action_tool(tool)
                    longtail.add(name)

        # Egress reply/respond from the responder (ReplyAction preferred, PersonaAction
        # fallback; ADR-0014). Always visible; visitor-bound at dispatch.
        responder = await self.get_responder()
        if responder is not None:
            get_responder_tools = getattr(responder, "get_tools", None)
            responder_tools: List[Any] = []
            if callable(get_responder_tools):
                try:
                    result = get_responder_tools()
                    if inspect.isawaitable(result):
                        result = await result  # ReplyAction.get_tools is async
                    responder_tools = result or []
                except Exception as exc:
                    logger.debug("orchestrator: responder get_tools failed: %s", exc)
                    responder_tools = []
            for tool in responder_tools:
                name = getattr(tool, "name", None)
                if not name:
                    continue
                tools[name] = wrap_action_tool(tool, visitor=visitor)
                visible.add(name)

        # MCP tool servers (via jvagent/mcp MCPAction; ADR-0015). Tools surface
        # as ``mcp_{server}__{tool}`` and self-route per user via the dispatch
        # context bound around the loop.
        for mcp_action in self._select_mcp_actions(actions):
            get_mcp_tools = getattr(mcp_action, "get_tools", None)
            if not callable(get_mcp_tools):
                continue
            try:
                mcp_tools = await get_mcp_tools() or []
            except Exception as exc:
                logger.debug("orchestrator: MCP get_tools failed: %s", exc)
                continue
            for tool in mcp_tools:
                name = getattr(tool, "name", None)
                if not name:
                    continue
                # No visitor injection: an MCP tool forwards its kwargs verbatim
                # to the server, so a ``visitor`` kwarg would be serialized (and
                # fail). Per-user routing comes from the dispatch context bound
                # for the turn, not a kwarg.
                tools[name] = wrap_action_tool(
                    tool,
                    agent=agent,
                    user_id=getattr(visitor, "user_id", None),
                    channel=getattr(visitor, "channel", "default") or "default",
                    access_label=delegate_resource_label(name),
                )
                longtail.add(name)

        # Lean surfacing policy (ADR-0018): below the threshold list every
        # capability tool (unchanged); above it, keep only the relevance
        # pre-surfaced few + any the user named — the rest stay reachable via
        # find_tool. Always-on tools (core/egress/meta/flow) are untouched.
        lean = bool(self.lean_tool_threshold) and len(longtail) > int(
            self.lean_tool_threshold
        )
        if lean:
            keep = self._presurface_tools(
                utterance, longtail, tools, int(self.lean_presurface_k)
            )
            keep |= set(self._user_named_tools(utterance, longtail))
            visible |= keep
        else:
            visible |= longtail
        if surface_meta is not None:
            surface_meta["lean"] = lean

        # Skills (progressive disclosure; meta-tools visible). Two specs: ``jv``
        # (SOP referencing action/IA tools) and ``claude`` (standard folders run
        # via the code-execution substrate). Activating a Claude skill stages its
        # folder into the caller's per-user sandbox so its scripts are runnable.
        docs = self._discover_skills(agent)
        # Enforce ``requires-actions`` (hard gate): a skill whose declared
        # Action types don't all resolve (enabled) on this agent is hidden
        # entirely — dropped from the surfaced list, find_skill, use_skill, and
        # always-active pinning — so the model never sees a skill it can't run.
        docs = await self._enforce_required_actions(docs)
        if skill_docs is not None:
            skill_docs.extend(docs)
        code_exec = self._select_code_execution_action(actions)
        activate_hook = self._build_skill_activate_hook(code_exec, visitor)
        for name, t in build_skill_meta_tools(
            docs, set(tools.keys()), activated, visible, activate_hook=activate_hook
        ).items():
            tools[name] = t
            visible.add(name)

        # Tool catalog (find_tool/load_tool — visible so hidden tools are reachable).
        for name, t in build_catalog_tools(tools, visible).items():
            tools[name] = t
            visible.add(name)

        # Always-visible pins, applied AFTER the lean policy so they survive it
        # — for capabilities that must be callable turn-1 regardless of phrasing
        # (the lean relevance pre-surface can miss them). Two equivalent levers:
        #   1. ``pinned_tools`` globs (raw tool names).
        #   2. ``always-active: true`` skills, whose ``allowed-tools`` are pinned
        #      every turn (skill-native; mirrors use_skill surfacing without an
        #      activation round-trip).
        if self.pinned_tools:
            visible |= self._match_tool_globs(self.pinned_tools, set(tools.keys()))
        for d in docs:
            if getattr(d, "always_active", False):
                visible |= {t for t in getattr(d, "requires_tools", ()) if t in tools}
        return tools

    @staticmethod
    def _match_tool_globs(patterns: List[str], names: Set[str]) -> Set[str]:
        """Tool names in ``names`` matching any fnmatch glob in ``patterns``."""
        out: Set[str] = set()
        for raw in patterns or []:
            pat = str(raw).strip()
            if not pat:
                continue
            out |= {n for n in names if fnmatch.fnmatchcase(n, pat)}
        return out

    @staticmethod
    def _anchor_relevant(utterance: str, anchors: List[str]) -> bool:
        """True if the utterance shares a meaningful keyword with any anchor.

        A lightweight first-entry relevance gate (no model call) so an anchored
        flow's tool is surfaced only when the user's message plausibly concerns
        it. Token overlap on significant (len>2, non-stopword) words.
        """
        if not anchors:
            return False
        u = _significant_tokens(utterance)
        if not u:
            return False
        a: set = set()
        for anc in anchors:
            a |= _significant_tokens(anc)
        return bool(u & a)

    @staticmethod
    def _presurface_tools(
        utterance: str,
        candidates: Set[str],
        tools: Dict[str, "SkillTool"],
        k: int,
    ) -> Set[str]:
        """Top-``k`` candidate tools by relevance to ``utterance`` (no model call).

        Cheap token overlap between the user's significant words and each tool's
        name + description (name split on ``__``/``_``). Returns at most ``k``
        names with non-zero overlap; empty when nothing matches (the model then
        discovers via find_tool). Runs once per turn, so it adds no loop cost.
        """
        if k <= 0 or not candidates:
            return set()
        u = _significant_tokens(utterance)
        if not u:
            return set()
        scored: List[Tuple[int, str]] = []
        for name in candidates:
            tool = tools.get(name)
            desc = getattr(tool, "description", "") if tool else ""
            doc = _significant_tokens(
                name.replace("__", " ").replace("_", " ") + " " + desc
            )
            score = len(u & doc)
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return {name for _, name in scored[:k]}

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    async def _run_loop(self, visitor: "InteractWalker") -> None:
        activated: List[str] = []
        visible: Set[str] = set()
        skill_docs: List[Any] = []
        utterance = getattr(visitor, "utterance", "") or ""

        # Resolve the active flow first so the surface gates its tool into the
        # prompt only when relevant (active flow, or anchor-relevant utterance).
        flow_tool_names = await self._routable_flow_tool_names()
        flow_owner = active_flow_owner(visitor, flow_tool_names=flow_tool_names)
        surface_meta: Dict[str, Any] = {}
        tools = await self._assemble_tools(
            visitor, activated, visible, flow_owner, utterance, skill_docs, surface_meta
        )
        lean_surface = bool(surface_meta.get("lean"))
        skill_names = {getattr(d, "name", "") for d in skill_docs}
        skills_section = render_skills_section(skill_docs)

        # Hard turn-lock (lock_active_flow): when a control-task points to an IA
        # that furnished a tool, restrict the callable surface to that one tool
        # and dispatch it — the loop can only continue the flow, never route
        # elsewhere. The IA's tool is visitor-bound, AC-gated, and terminal
        # (so it owns the turn's output). The IA receives all input including
        # off-topic; interruption/cancel is the IA's own concern.
        if self.lock_active_flow and flow_owner and flow_owner in tools:
            locked_result = (await tools[flow_owner].run({})) or ""
            interaction = getattr(visitor, "interaction", None)
            # The locked IA "emits" either by setting a response OR by queuing a
            # directive (the rails/interview pattern — `_finalize_directives`
            # renders it after the loop). Checking only `interaction.response`
            # missed the directive path, so the orchestrator mistook a publishing
            # IA for a silent one and echoed the IA-as-tool status sentinel
            # ("(ran X)") as a spurious reply/directive (ADR-0013 follow-up).
            emitted = self._ia_emitted(interaction)
            ended = "locked"
            if not emitted:
                # The IA ran but produced nothing user-facing. NEVER echo its
                # internal status sentinel ("(ran X)" / "(no visitor available)"
                # / "(flow error: …)") — those are loop-internal. Surface a clean
                # message instead.
                res = locked_result.strip()
                if "access denied" in res.lower():
                    ended = "locked_denied"
                    await self._emit_reply(
                        visitor,
                        "You don't currently have access to continue this. Let "
                        "me know if there's something else I can help with.",
                    )
                else:
                    ended = (
                        "locked_error"
                        if res.startswith("(flow error")
                        else ("locked_silent")
                    )
                    await self._emit_reply(visitor, self.clarify_text)
            await self._record_orchestrator_activation(
                visitor,
                continuation_mode="locked",
                flow_owner=flow_owner,
                tools_invoked=[flow_owner],
                tick_count=0,
                ended_via=ended,
                activated=activated,
            )
            return

        flow_note = active_flow_note(flow_owner) if flow_owner else ""

        # Resumable-plan note (ADR-0019): a multi-step plan recorded on a prior
        # turn that still has pending steps is re-surfaced so the model resumes
        # it instead of re-planning. Resolved once at turn start (a plan the
        # model creates *this* turn doesn't need a resume note). Soft, like the
        # flow note — never a hard lock.
        plan_note = (
            plan_resume_note(active_plan(visitor, owner=self.get_class_name()))
            if self.planning
            else ""
        )

        observations: List[Dict[str, Any]] = []
        budget = max(1, int(self.activation_budget))
        history = await self._history(visitor)
        ticks = 0
        ended_via = "budget"
        last_sig: Optional[tuple] = None
        repeats = 0
        # Named-tool steering guard (block_raw_tool_invocation): tools the user
        # named literally this turn, deflected once each so the model re-plans
        # from intent rather than obeying the named tool.
        user_named_tools = (
            self._user_named_tools(utterance, set(tools.keys()))
            if self.block_raw_tool_invocation
            else frozenset()
        )
        deflected_named: Set[str] = set()
        nd_streak = 0  # consecutive unparseable model decisions
        # Model gearing (ADR-0016): light until the turn proves multi-step.
        substantive_tool_calls = 0
        ticks_light = 0
        ticks_heavy = 0
        started = time.time()
        deadline = (
            started + float(self.max_duration_seconds)
            if self.max_duration_seconds and self.max_duration_seconds > 0
            else 0.0
        )

        # The transient ack only applies once the heavy model is engaged (it is
        # the slow gear) — scheduled on the first heavy tick below, not up front.
        ack_task: Optional["asyncio.Task"] = None
        ack_started = False
        try:
            while budget > 0:
                if deadline and time.time() > deadline:
                    ended_via = "duration"
                    break
                budget -= 1
                ticks += 1
                # Gear selection (sticky): heavy once the turn is multi-step —
                # enough substantive tool calls, or a skill (multi-step SOP) is
                # active. Single-model agents always run heavy.
                gear = self._select_gear(substantive_tool_calls, bool(activated))
                if gear == "light":
                    ticks_light += 1
                else:
                    ticks_heavy += 1
                # Arm the transient ack only once the turn proves COMPLEX — a
                # skill is active, or it has made multiple substantive tool calls.
                # Simple single-tool / reply-only turns never surface a "working
                # on it" line (and so it can't trail after a fast reply).
                if not ack_started and (
                    bool(activated)
                    or substantive_tool_calls >= int(self.escalate_after_tool_calls)
                ):
                    ack_started = True
                    ack_task = self._schedule_first_emit_ack(visitor)
                visible_tools = [tools[n] for n in visible if n in tools]
                decision = await self._run_model(
                    visitor,
                    utterance,
                    history,
                    visible_tools,
                    observations,
                    flow_note,
                    skills_section,
                    gear=gear,
                    lean=lean_surface,
                    plan_note=plan_note,
                )
                if decision is None:
                    # A truncated/garbled decision (common when a verbose thinking
                    # model overruns the token cap). One transient miss → nudge
                    # and retry with the tool surface intact, so a productive turn
                    # isn't aborted mid-task. Only a persistent streak falls
                    # through to the partial-compose (work-done-but-can't-emit).
                    nd_streak += 1
                    if nd_streak >= 3:
                        ended_via = "no_decision"
                        break
                    observations.append(
                        {
                            "tool": "(parse)",
                            "args": {},
                            "observation": (
                                "(Your previous response was not a single valid "
                                "JSON object. Reply with exactly ONE JSON object "
                                "for your next step — a tool call or a final "
                                "answer. Keep it short.)"
                            ),
                        }
                    )
                    continue
                nd_streak = 0
                action, tool_name, args = self._normalize(decision, tools, skill_names)
                # Progress/reasoning line for the UI's REASONING disclosure. Fires
                # on both gears so single-step (light) turns still show their
                # reasoning, not just multi-step heavy ones.
                if self.stream_internal_progress:
                    await self._emit_thought(
                        visitor,
                        self._progress_line(action, tool_name, args, decision),
                    )
                if action == "final":
                    answer = _text_candidate(decision)
                    if answer:
                        await self._maybe_emit_final(visitor, answer)
                    ended_via = "final"
                    return
                if action == "tool":
                    # Steering guard: the user named this exact tool — deflect it
                    # once so tool selection stays the agent's call, driven by the
                    # goal rather than the named tool.
                    if (
                        tool_name in user_named_tools
                        and tool_name not in deflected_named
                    ):
                        deflected_named.add(tool_name)
                        observations.append(
                            {
                                "tool": tool_name,
                                "args": args,
                                "observation": (
                                    f"(You may not call {tool_name} just because "
                                    "the user named it. Tool selection is your "
                                    "responsibility — work out the user's "
                                    "underlying goal and choose the right "
                                    "tool(s) yourself, or answer directly.)"
                                ),
                            }
                        )
                        continue
                    tool = tools.get(tool_name)
                    if tool is None:
                        obs = f"(no such tool: {tool_name})"
                    elif self.block_raw_tool_invocation and tool_name not in visible:
                        # Surface discipline: a hidden tool must be reached via
                        # find_tool or a skill, not named raw.
                        obs = (
                            f"(tool {tool_name} is not directly available — use "
                            "find_tool or the relevant skill to reach it)"
                        )
                    else:
                        # Structured tool thought for the UI's TOOL CALLS panel:
                        # tool_call before, tool_result after (shared segment_id
                        # so they fold into one element). Substantive tools only.
                        tool_seg = (
                            f"toolcall-{uuid.uuid4().hex[:10]}"
                            if tool_name not in _NON_SUBSTANTIVE_TOOLS
                            else None
                        )
                        if tool_seg:
                            await self._emit_tool_thought(
                                visitor, "tool_call", tool_name, tool_seg, args=args
                            )
                        try:
                            if self.tool_call_timeout and self.tool_call_timeout > 0:
                                obs = await asyncio.wait_for(
                                    tool.run(args), timeout=self.tool_call_timeout
                                )
                            else:
                                obs = await tool.run(args)
                        except asyncio.TimeoutError:
                            obs = (
                                f"(tool {tool_name} timed out after "
                                f"{self.tool_call_timeout}s)"
                            )
                        except Exception as exc:
                            logger.warning(
                                "orchestrator: tool %r raised: %s", tool_name, exc
                            )
                            obs = f"(tool error: {exc})"
                        # After (fires on success, timeout, or error — obs is
                        # always a string by here).
                        if tool_seg:
                            await self._emit_tool_thought(
                                visitor, "tool_result", tool_name, tool_seg, obs=obs
                            )
                    observations.append(
                        {"tool": tool_name, "args": args, "observation": obs}
                    )
                    # Gearing: count substantive (non-meta, non-egress) tool calls
                    # toward escalation to the heavy model.
                    if tool is not None and tool_name not in _NON_SUBSTANTIVE_TOOLS:
                        substantive_tool_calls += 1
                    # Repeat guard: a model that keeps choosing the same tool with
                    # the same args makes no progress (e.g. re-activating a skill).
                    # Nudge once, then break before the budget is wasted.
                    sig = (tool_name, str(args))
                    repeats = repeats + 1 if sig == last_sig else 0
                    last_sig = sig
                    if repeats == 2:
                        observations.append(
                            {
                                "tool": "(guard)",
                                "args": {},
                                "observation": (
                                    f"(You have already called {tool_name} with "
                                    "this input and got the same result. Do NOT "
                                    "repeat it — use a different tool or finish "
                                    'with action "final".)'
                                ),
                            }
                        )
                    elif repeats >= 3:
                        ended_via = "repeat_guard"
                        return
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

            # Budget/time ran out mid-task. Rather than dropping to the generic
            # clarify fallback (which discards the work and misreports the
            # cause), force ONE compose so the user gets the agent's best answer
            # from what it gathered. Only when there's actual work to summarize.
            interaction = getattr(visitor, "interaction", None)
            emitted = bool(getattr(interaction, "response", "") if interaction else "")
            if (
                not emitted
                and ended_via in ("budget", "duration", "no_decision")
                and observations
            ):
                decision = await self._run_model(
                    visitor,
                    utterance,
                    history,
                    [],
                    observations,
                    skills_section=skills_section,
                    finalize=True,
                    gear="light",  # wrap-up is single-dimensional
                )
                answer = _text_candidate(decision) if decision else ""
                if answer:
                    await self._maybe_emit_final(visitor, answer)
                    ended_via = f"{ended_via}_finalized"
        finally:
            if ack_task is not None and not ack_task.done():
                ack_task.cancel()
            # Plan lifecycle (ADR-0019): close a fully-done plan, leave a plan
            # with pending steps ACTIVE so the next turn resumes it. Runs on
            # every loop exit; no-op when planning is off.
            await self._finalize_plan(visitor)
            await self._record_orchestrator_activation(
                visitor,
                continuation_mode="model_mediated" if flow_owner else "none",
                flow_owner=flow_owner,
                tools_invoked=[o.get("tool") for o in observations],
                tick_count=ticks,
                ended_via=ended_via,
                activated=activated,
                ticks_light=ticks_light,
                ticks_heavy=ticks_heavy,
            )

    async def _finalize_plan(self, visitor: "InteractWalker") -> None:
        """Close a completed plan; leave an unfinished one active for resume.

        ADR-0019 lifecycle, called from the loop's ``finally`` on every exit:
        if an orchestrator-owned ``AGENTIC_LOOP`` plan exists and all its steps
        are terminal, complete and delete it; if steps remain pending (natural
        end with parked work, or a budget/duration/crash cutoff), leave it
        active so the next turn re-surfaces it. No-op when planning is off.
        """
        if not self.planning:
            return
        try:
            handle = active_plan(visitor, owner=self.get_class_name())
            if handle is None or handle.has_pending_steps():
                return
            conversation = getattr(visitor, "conversation", None)
            if conversation is None:
                return
            from jvagent.memory.task_store import TaskStore

            await handle.complete(result="plan complete")
            await TaskStore(conversation).delete(handle.id)
        except Exception as exc:  # pragma: no cover - defensive cleanup
            logger.debug("orchestrator: _finalize_plan failed: %s", exc)

    async def _record_orchestrator_activation(
        self,
        visitor: "InteractWalker",
        *,
        continuation_mode: str,
        flow_owner: Optional[str],
        tools_invoked: List[Optional[str]],
        tick_count: int,
        ended_via: str,
        activated: List[str],
        ticks_light: int = 0,
        ticks_heavy: int = 0,
    ) -> None:
        """Append a per-turn ``orchestrator_activation`` event to
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
            "event_type": "orchestrator_activation",
            "data": {
                "continuation_mode": continuation_mode,
                "flow_owner": flow_owner,
                "lock_active_flow": bool(self.lock_active_flow),
                "tools_invoked": [t for t in tools_invoked if t],
                "tick_count": int(tick_count),
                "budget": int(self.activation_budget),
                "ended_via": ended_via,
                "skills_used": list(activated or []),
                "gearing": self._gearing_on(),
                "ticks_light": int(ticks_light),
                "ticks_heavy": int(ticks_heavy),
                "escalated": bool(ticks_heavy) and self._gearing_on(),
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
            logger.debug("orchestrator: activation record failed: %s", exc)

    async def _emit_reply(self, visitor: "InteractWalker", text: str) -> None:
        """Emit user-facing ``text`` through the responder (ReplyAction → channel
        formatting, identity rules, directive composition, and graceful no-bus
        handling; ADR-0014), falling back to a raw publish only if no responder
        is available or its reply fails."""
        if not (text or "").strip():
            return
        responder = await self.get_responder()
        reply = getattr(responder, "reply", None) if responder is not None else None
        if callable(reply):
            try:
                await reply(text, visitor)
                return
            except Exception as exc:
                logger.warning("orchestrator: responder.reply failed: %s", exc)
        await self.publish(visitor=visitor, content=text)

    async def _maybe_emit_final(self, visitor: "InteractWalker", answer: str) -> None:
        """Emit the loop's ``final`` answer unless that exact text was already
        emitted this turn.

        A terminal egress tool (``reply``/``respond`` or a terminal IA tool)
        returns from the loop before a ``final`` action can be reached, so
        reaching ``final`` means the answer has not been emitted as the turn's
        reply. Non-terminal publish tools (e.g. catalog ``emit_catalog_message``)
        DO append to ``interaction.response`` mid-turn — that must not suppress a
        distinct final answer such as a product skill's closing line. So suppress
        only when the exact answer text is already present in the response (the
        model echoed an already-emitted line), not merely because the response is
        non-empty.
        """
        answer = (answer or "").strip()
        if not answer:
            return
        interaction = getattr(visitor, "interaction", None)
        current = (
            (getattr(interaction, "response", "") or "")
            if interaction is not None
            else ""
        )
        if answer in current:
            return  # this exact text was already emitted this turn
        await self._emit_reply(visitor, answer)

    @staticmethod
    def _normalize(
        decision: Dict[str, Any],
        tools: Dict[str, SkillTool],
        skill_names: Optional[Set[str]] = None,
    ):
        """Normalize a model decision into ``(action, tool_name, args)``.

        Tolerant of common near-miss shapes the model emits, e.g.
        ``{"action":"reply","answer":"hi"}`` (tool name in ``action``, text in
        ``answer`` rather than ``args.text``) or ``{"tool":"x","args":{...}}``
        with no ``action``. For the persona text tools (``reply``/``respond``),
        the text is salvaged from ``answer``/``text``/``content``/``message``
        into ``args.text`` so a near-miss shape doesn't waste the step budget.

        Skills aren't tools — they run through the ``use_skill`` meta-tool — but
        the model routinely addresses a skill *as if* it were a tool, e.g.
        ``{"action":"use_skill","tool":"research"}``, ``{"action":"research"}``
        or ``{"tool":"research"}``. Any of these is rewritten to
        ``use_skill(name=<skill>)`` so a named skill actually activates instead
        of dispatching a non-existent tool.
        """
        raw_action = (decision.get("action") or "").strip()
        action = raw_action.lower()
        tool_field = (decision.get("tool") or "").strip()
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        text = _text_candidate(decision)

        names: FrozenSet[str] = frozenset(skill_names) if skill_names else frozenset()

        def _named_skill(*candidates: Any) -> str:
            for cand in candidates:
                c = (cand or "").strip() if isinstance(cand, str) else ""
                if c and c in names:
                    return c
            return ""

        if names and "use_skill" in tools:
            if action == "use_skill" or tool_field == "use_skill":
                skill = (args.get("name") or args.get("skill") or "").strip()
                if not skill:
                    skill = _named_skill(
                        tool_field if tool_field != "use_skill" else "",
                        args.get("topic"),
                        args.get("query"),
                        raw_action if raw_action.lower() != "use_skill" else "",
                    )
            else:
                # A skill name standing in for the action or tool field.
                skill = _named_skill(raw_action, tool_field)
            if skill:
                return "tool", "use_skill", {"name": skill}

        if action not in ("tool", "final"):
            if tool_field:
                action = "tool"
            elif action in tools:
                tool_field = raw_action
                action = "tool"
            elif text and "reply" in tools:
                # Bare text with no recognizable action → speak it.
                tool_field, action = "reply", "tool"
            elif text:
                action = "final"
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

    async def _emit_thought(self, visitor: "InteractWalker", text: str) -> None:
        """Emit a transient 'thought' bubble over the response bus, if live.

        No bus / no session → no-op (thoughts only stream in real time; they are
        never persisted to the interaction response). Best-effort.
        """
        body = (text or "").strip()
        if not body:
            return
        bus = getattr(visitor, "response_bus", None)
        session_id = getattr(visitor, "session_id", None)
        if not bus or not session_id:
            return
        interaction = getattr(visitor, "interaction", None)
        try:
            await bus.publish(
                session_id=session_id,
                content=body,
                channel=getattr(visitor, "channel", "default") or "default",
                category="thought",
                thought_type="reasoning",  # feeds the UI's REASONING disclosure
                transient=True,
                interaction=interaction,
                interaction_id=getattr(interaction, "id", None),
                user_id=getattr(interaction, "user_id", None),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("orchestrator: thought emit failed: %s", exc)

    async def _emit_tool_thought(
        self,
        visitor: "InteractWalker",
        phase: str,
        tool_name: str,
        segment_id: str,
        *,
        args: Optional[Dict[str, Any]] = None,
        obs: Any = None,
    ) -> None:
        """Emit a structured tool_call/tool_result thought over the bus.

        ``phase`` is ``"tool_call"`` (before dispatch — carries ``tool_args``) or
        ``"tool_result"`` (after — carries ``tool_result`` + ``is_error``). The
        shared ``segment_id`` folds the pair into one TOOL CALLS element in the
        UI. Transient (never persisted to ``interaction.response``); best-effort.
        """
        bus = getattr(visitor, "response_bus", None)
        session_id = getattr(visitor, "session_id", None)
        if not bus or not session_id:
            return
        interaction = getattr(visitor, "interaction", None)
        if phase == "tool_call":
            content = tool_name
            metadata: Dict[str, Any] = {
                "tool_name": tool_name,
                "tool_args": args or {},
            }
        else:
            text = obs if isinstance(obs, str) else str(obs)
            cap = self.tool_thought_max_chars
            capped = text[:cap] if cap and cap > 0 else text
            content = capped
            metadata = {
                "tool_name": tool_name,
                "tool_result": capped,
                "is_error": isinstance(obs, str) and obs.startswith("(tool error"),
            }
        try:
            await bus.publish(
                session_id=session_id,
                content=content,
                channel=getattr(visitor, "channel", "default") or "default",
                category="thought",
                thought_type=phase,
                segment_id=segment_id,
                transient=True,
                interaction=interaction,
                interaction_id=getattr(interaction, "id", None),
                user_id=getattr(interaction, "user_id", None),
                metadata=metadata,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("orchestrator: tool thought emit failed: %s", exc)

    def _schedule_first_emit_ack(
        self, visitor: "InteractWalker"
    ) -> Optional["asyncio.Task"]:
        """Schedule transient 'working on it' ack(s) while a slow turn runs.

        ``enable_transient_ack`` is the master switch. Emits each of
        ``ack_statements`` in order: the first after ``first_emit_timeout_ms``,
        each subsequent after ``ack_interval_ms`` (kept generous so later lines
        don't appear too soon), until the list is exhausted or the caller cancels
        (the turn produced output). Needs a live bus.
        """
        if not self.enable_transient_ack:
            return None
        statements = [
            s.strip()
            for s in (self.ack_statements or [])
            if isinstance(s, str) and s.strip()
        ]
        if not statements:
            return None
        bus = getattr(visitor, "response_bus", None)
        session_id = getattr(visitor, "session_id", None)
        if not bus or not session_id:
            return None
        interaction = getattr(visitor, "interaction", None)
        channel = getattr(visitor, "channel", "default") or "default"
        first_delay = max(0.0, float(self.first_emit_timeout_ms or 0) / 1000.0)
        interval = max(0.0, float(self.ack_interval_ms or 0) / 1000.0)
        # Channel-conditional shape: a streamed UI shows the ack as an ephemeral
        # status line in its activity strip (category=thought/status, kept out of
        # the answer transcript); a non-streamed channel (WhatsApp, etc.) has no
        # activity strip, so the ack must be a whole, delivered message
        # (category=user → relayed by the channel adapter; transient ⇒ not
        # persisted to interaction.response). Both stay transient.
        streamed_ui = bool(getattr(visitor, "stream", False))
        ack_kwargs: Dict[str, Any] = (
            {"category": "thought", "thought_type": "status"}
            if streamed_ui
            else {"category": "user"}
        )

        async def _ack() -> None:
            try:
                for i, stmt in enumerate(statements):
                    await asyncio.sleep(first_delay if i == 0 else interval)
                    await bus.publish(
                        session_id=session_id,
                        content=stmt,
                        channel=channel,
                        transient=True,
                        interaction=interaction,
                        interaction_id=getattr(interaction, "id", None),
                        user_id=getattr(interaction, "user_id", None),
                        **ack_kwargs,
                    )
            except asyncio.CancelledError:  # pragma: no cover - timing
                raise
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("orchestrator: transient ack failed: %s", exc)

        try:
            return asyncio.ensure_future(_ack())
        except Exception:  # pragma: no cover - no running loop
            return None

    @staticmethod
    def _user_named_tools(utterance: str, tool_names: Set[str]) -> FrozenSet[str]:
        """Surface tools whose name the user typed literally (steering attempt).

        Matches a tool when its full name appears in the message, or — for MCP
        tools (``mcp_<server>__<tool>``) — when the unqualified ``<tool>`` suffix
        does. Egress/indirection tools are exempt. Used only when
        ``block_raw_tool_invocation`` is on.
        """
        u = (utterance or "").lower()
        if not u:
            return frozenset()
        named: Set[str] = set()
        for name in tool_names:
            if name in _STEER_EXEMPT:
                continue
            triggers = [name]
            if name.startswith("mcp_") and "__" in name:
                triggers.append(name.split("__", 1)[1])
            if any(len(t) >= 4 and t.lower() in u for t in triggers):
                named.add(name)
        return frozenset(named)

    @staticmethod
    def _progress_line(
        action: str, tool_name: str, args: Dict[str, Any], decision: Dict[str, Any]
    ) -> str:
        """A short human progress line for a loop tick (stream_internal_progress)."""
        thought = decision.get("thought") or decision.get("reasoning")
        if isinstance(thought, str) and thought.strip():
            return thought.strip()
        if action == "tool" and tool_name:
            if tool_name == "use_skill":
                skill = (args or {}).get("name") or ""
                return f"Following the {skill} skill…" if skill else "Using a skill…"
            if tool_name in ("reply", "respond"):
                return "Composing a reply…"
            return f"Using {tool_name}…"
        if action == "final":
            return "Wrapping up…"
        return ""

    def _has_main_model(self) -> bool:
        return bool((self.model or "").strip())

    def _gearing_on(self) -> bool:
        # Gearing needs two distinct tiers: a light model AND a main model. A
        # light model with no main model is the single-model fallback (the light
        # model becomes the sole model) — so gearing is off.
        return bool((self.light_model or "").strip()) and self._has_main_model()

    def _select_gear(self, substantive_tool_calls: int, skill_active: bool) -> str:
        """Light until the turn proves multi-step, then heavy (sticky). Single-
        model agents (no light_model, or no main_model) always run one tier."""
        if not self._gearing_on():
            return "heavy"
        if (self.escalate_on_skill and skill_active) or (
            substantive_tool_calls >= int(self.escalate_after_tool_calls)
        ):
            return "heavy"
        return "light"

    async def _resolve_model_action(self, action_type: str) -> Any:
        """Resolve a model action by class name, falling back to the heavy one."""
        at = (action_type or "").strip()
        if at:
            try:
                action: Any = await self.get_action(at)
                if action is not None:
                    return action
            except Exception as exc:
                logger.debug("orchestrator: get_action(%r) failed: %s", at, exc)
        return await self.get_model_action(required=False)

    async def _light_profile(self):
        """The light/completion profile tuple (no reasoning)."""
        action = await self._resolve_model_action(
            self.light_model_action_type or self.model_action_type
        )
        return (
            action,
            (self.light_model or None),
            self.light_model_temperature,
            self.light_model_max_tokens,
            False,
        )

    async def _gear_model(self, gear: str):
        """Return (model_action, model_id, temperature, max_tokens, reasoning_on)
        for the requested gear. The light profile is used for the light gear when
        gearing is on; it is also used as the SOLE model (fallback) when a light
        model is configured but no main model is. Otherwise the heavy profile."""
        light_set = bool((self.light_model or "").strip())
        if light_set and (
            (gear == "light" and self._gearing_on()) or not self._has_main_model()
        ):
            return await self._light_profile()
        action = await self.get_model_action(required=False)
        return (
            action,
            (self.model or None),
            self.model_temperature,
            self.model_max_tokens,
            True,
        )

    def _reasoning_kwargs(self) -> Dict[str, Any]:
        """Reasoning passthrough for the loop's model call.

        Only emits keys when reasoning is configured, so non-reasoning models
        (the gpt-4o-mini default) see no change. The model action honors per-call
        ``reasoning_effort`` / ``reasoning`` over its own attribute, so the
        executive profile can run at its own reasoning level.
        """
        if self.reasoning_enabled is False:
            return {"reasoning_effort": None, "reasoning": {"enabled": False}}
        configured = (
            self.reasoning_effort
            or self.reasoning_enabled is True
            or self.reasoning_budget_tokens
            or self.reasoning_extra
        )
        if not configured:
            return {}
        out: Dict[str, Any] = {}
        if self.reasoning_effort:
            out["reasoning_effort"] = str(self.reasoning_effort)
        reasoning: Dict[str, Any] = {}
        if self.reasoning_enabled is not None:
            reasoning["enabled"] = bool(self.reasoning_enabled)
        if self.reasoning_effort:
            reasoning["effort"] = str(self.reasoning_effort)
        if self.reasoning_budget_tokens:
            reasoning["budget_tokens"] = int(self.reasoning_budget_tokens)
        if self.reasoning_extra:
            reasoning.update(self.reasoning_extra)
        if reasoning:
            out["reasoning"] = reasoning
        return out

    async def _safe_agent(self) -> Any:
        try:
            return await self.get_agent()
        except Exception:
            return None

    async def _render_identity(self) -> str:
        """Identity prefix for the system prompt, from the Agent (ADR-0014).

        Reads ``alias`` + ``role`` off the Agent node so the model reasons and
        writes as the agent. Empty string when neither is set.
        """
        agent = await self._safe_agent()
        return render_identity_section(
            getattr(agent, "alias", "") or "",
            getattr(agent, "role", "") or "",
        )

    @staticmethod
    def _fmt(template: str, default: str, **kwargs: Any) -> str:
        """``template.format(**kwargs)`` with a safe fallback.

        Overridable prompt templates come from agent.yaml; a malformed override
        (unknown placeholder, unescaped brace) must not crash a turn. On a format
        error we fall back to the built-in ``default`` (and ultimately the raw
        template) and log once.
        """
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning(
                "orchestrator: prompt override failed to format (%s); "
                "using the built-in default for this piece",
                exc,
            )
            try:
                return default.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return template

    def _compose_system_prompt(
        self,
        *,
        identity_section: str,
        tools_section: str,
        skills_section: str,
    ) -> str:
        """Build the base system prompt from the (overridable) ``system_prompt``
        template, then append ``system_prompt_extra`` if set."""
        base = self._fmt(
            self.system_prompt,
            ORCHESTRATOR_SYSTEM_PROMPT,
            identity_section=identity_section,
            tools_section=tools_section,
            skills_section=skills_section,
        )
        extra = (self.system_prompt_extra or "").strip()
        if extra:
            base = f"{base}\n\n{extra}"
        return base

    async def _routable_flow_tool_names(self) -> Set[str]:
        """Class names of routable IAs exposed as tools (flow continuation keys)."""
        agent = await self._safe_agent()
        names: Set[str] = set()
        for action in await self._enabled_actions(agent):
            if action is self or isinstance(action, OrchestratorInteractAction):
                continue
            if getattr(action, "always_execute", False):
                continue
            triggers_fn = getattr(action, "routing_triggers", None)
            triggers = (
                list(triggers_fn() or [])
                if callable(triggers_fn)
                else list(getattr(action, "anchors", None) or [])
            )
            if callable(getattr(action, "execute", None)) and triggers:
                get_name = getattr(action, "get_class_name", None)
                if callable(get_name):
                    names.add(get_name())
        return names

    async def _enabled_actions(self, agent: Any) -> List[Any]:
        if agent is None:
            return []
        try:
            mgr = await agent.get_actions_manager()
            return await mgr.get_all_actions(enabled_only=True) if mgr else []
        except Exception as exc:
            logger.debug("orchestrator: action enumeration failed: %s", exc)
            return []

    @staticmethod
    def _mcp_action_class() -> Optional[type]:
        """The MCPAction class, or None when the ``mcp`` extra isn't installed."""
        try:
            from jvagent.action.mcp.mcp_action import MCPAction

            return MCPAction
        except Exception:
            return None

    @staticmethod
    def _select_code_execution_action(actions: List[Any]) -> Optional[Any]:
        """The enabled CodeExecutionAction, if one is installed and on."""
        try:
            from jvagent.action.code_execution import CodeExecutionAction
        except Exception:
            return None
        for action in actions:
            if isinstance(action, CodeExecutionAction) and getattr(
                action, "enabled", False
            ):
                return action
        return None

    @staticmethod
    def _build_skill_activate_hook(
        code_exec: Optional[Any], visitor: Any
    ) -> Optional[Any]:
        """Hook that stages a Claude skill's folder into the per-user sandbox.

        Returns ``None`` when code execution is unavailable; otherwise an async
        ``(SkillDoc) -> Optional[str]`` that stages ``spec: claude`` skills and
        returns a note telling the model where to run them. JV skills are
        ignored (they execute by referencing already-surfaced tools).
        """
        if code_exec is None:
            return None

        async def _activate(doc: Any) -> Optional[str]:
            if getattr(doc, "spec", "jv") != "claude":
                return None
            directory = getattr(doc, "directory", "") or ""
            if not directory:
                return None
            try:
                rel = await code_exec.stage_skill(visitor, directory, doc.name)
            except Exception as exc:
                return f"(could not stage skill files: {exc})"
            return (
                f"This skill's files are staged at '{rel}/' in your sandbox. Run "
                f"its scripts with the code_execution__bash tool — e.g. "
                f"`python {rel}/scripts/<script>.py`. Read bundled files there "
                f"(e.g. `cat {rel}/reference.md`) only as needed."
            )

        return _activate

    def _select_mcp_actions(self, actions: List[Any]) -> List[Any]:
        """MCPAction instances to pull tools from, per ``tool_servers``.

        ``-all`` (default) selects every enabled MCPAction; a finite list selects
        by class name or package name. Returns [] when MCP isn't installed.
        """
        mcp_cls = self._mcp_action_class()
        if mcp_cls is None:
            return []
        selector = self.tool_servers
        mcp_actions = [a for a in actions if isinstance(a, mcp_cls)]
        if not mcp_actions:
            return []
        if isinstance(selector, str) and selector.strip() == "-all":
            return mcp_actions
        wanted = (
            {str(s).strip() for s in selector}
            if isinstance(selector, (list, tuple, set))
            else {str(selector).strip()}
        )
        if not wanted:
            return []

        def _names(a: Any) -> Set[str]:
            out: Set[str] = set()
            get_name = getattr(a, "get_class_name", None)
            if callable(get_name):
                out.add(get_name())
            for attr in ("name", "package_name"):
                val = getattr(a, attr, None)
                if isinstance(val, str) and val:
                    out.add(val)
            return out

        return [a for a in mcp_actions if _names(a) & wanted]

    def _discover_skills(self, agent: Any) -> List[Any]:
        try:
            return discover_skill_docs(
                agent,
                skills_source=self.skills_source,
                selector=self.skills,
                denied=list(self.denied_skills or []),
            )
        except Exception as exc:
            logger.debug("orchestrator: skill discovery failed: %s", exc)
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
        skills_section: str = "",
        finalize: bool = False,
        gear: str = "heavy",
        lean: bool = False,
        plan_note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """One model call → parsed JSON decision. Overridden/mocked in tests.

        ``finalize=True`` appends a hard stop instruction so the model wraps up
        with its best answer from what it has gathered instead of calling more
        tools — used when the loop runs out of budget/time (partial-compose).

        ``gear`` ("light"|"heavy") selects the model profile (ADR-0016): the
        light/completion model for single-dimensional steps, the heavy/reasoning
        model for multi-step work. When no ``light_model`` is configured both
        gears resolve to the heavy profile (single-model, unchanged).
        """
        model_action, model_id, temperature, max_tokens, reasoning_on = (
            await self._gear_model(gear)
        )
        if model_action is None:
            logger.warning("orchestrator: no model action (%s)", self.model_action_type)
            return None
        system_prompt = self._compose_system_prompt(
            identity_section=await self._render_identity(),
            tools_section=render_tools_section(tools, lean=lean),
            skills_section=skills_section or self.no_skills_text,
        )
        if flow_note:
            note = self._fmt(
                self.flow_in_progress_prompt,
                FLOW_IN_PROGRESS_PROMPT,
                flow_note=flow_note,
            )
            system_prompt = f"{system_prompt}\n\n{note}"
        # Planning (ADR-0019): when on, nudge the model to use update_plan for
        # multi-step work; when a prior plan is unfinished, re-surface it so the
        # turn resumes. Both gated to keep simple/lean turns untouched.
        if self.planning and not finalize:
            system_prompt = f"{system_prompt}\n\n{self.planning_prompt}"
            if plan_note:
                system_prompt = f"{system_prompt}\n\n{plan_note}"
        if self.max_statement_length and self.max_statement_length > 0:
            limit = self._fmt(
                self.length_limit_prompt,
                LENGTH_LIMIT_PROMPT,
                max_chars=int(self.max_statement_length),
            )
            system_prompt = f"{system_prompt}\n\n{limit}"
        if self.block_raw_tool_invocation:
            system_prompt = f"{system_prompt}\n\n{self.tool_use_policy_prompt}"
        if finalize:
            system_prompt = f"{system_prompt}\n\n{self.finalize_prompt}"
        user_prompt = self._fmt(
            self.user_prompt,
            ORCHESTRATOR_USER_PROMPT_TEMPLATE,
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
            "model": model_id,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "calling_action_name": self.get_class_name(),
        }
        if self.enforce_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if reasoning_on:  # reasoning only on the heavy gear
            kwargs.update(self._reasoning_kwargs())
        try:
            result = await model_action.query_messages(**kwargs)
        except Exception as exc:
            logger.warning("orchestrator: model call raised: %s", exc)
            return None
        # Surface the thinking trace only on the heavy gear (the light gear is a
        # completion model with no reasoning to show).
        if self.stream_reasoning_trace and gear == "heavy":
            trace = getattr(result, "thinking_content", None)
            if trace:
                await self._emit_thought(visitor, str(trace))
        raw = (getattr(result, "response", None) or "").strip()
        return parse_json_object(raw) if raw else None


__all__ = ["OrchestratorInteractAction"]
