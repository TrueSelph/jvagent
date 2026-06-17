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
3. **Directive finalize** — emit any directives a locked IA-tool left unrendered.

Routing is tool selection; turn-lock is deterministic (``lock_active_flow``) or
an emergent flow property.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import inspect
import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional, Set, Tuple

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.utils.uploads import (
    DEFAULT_UPLOAD_KEYS,
    collect_uploads,
    decode_text,
    human_size,
)
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
from jvagent.action.orchestrator.core_tools import (
    build_artifact_tools,
    build_core_tools,
    build_plan_tool,
    build_proactive_tools,
)
from jvagent.action.orchestrator.prompts import (
    FINALIZE_PROMPT,
    FLOW_IN_PROGRESS_PROMPT,
    LENGTH_LIMIT_PROMPT,
    MEMORY_PROMPT,
    NO_SKILLS_AVAILABLE,
    ORCHESTRATOR_SYSTEM_PROMPT,
    ORCHESTRATOR_USER_PROMPT_TEMPLATE,
    PLANNING_PROMPT,
    SAFEGUARDS_REMINDER,
    TOOL_USE_POLICY,
    render_capabilities_section,
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
from jvagent.action.parameters import (
    accumulate_action_parameters,
    orchestration_parameters,
    orchestrator_core_parameters,
    render_parameters,
    reply_core_parameters,
)
from jvagent.tooling.tool_executor import bind_dispatch_context

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

DEFAULT_ACTIVATION_BUDGET = 24

# Keys the model commonly uses to carry user-facing text, in priority order.
_TEXT_KEYS = ("answer", "text", "content", "message", "reply", "response")

# Back-reference cues for the deterministic artifact recall seed (ADR-0021 S3):
# the utterance reads like it refers to something shown/told earlier. Only ever
# consulted when the conversation already holds image artifacts, so domain words
# (house, car) and comparatives won't false-trigger absent a prior upload.
_BACKREF_CUE = re.compile(
    r"\b(image|images|photo|photos|picture|pictures|pic|pics|screenshot|"
    r"file|files|document|documents|doc|docs|attachment|attachments|"
    r"upload|uploaded|sent|showed|shown|shared|earlier|before|previous|"
    r"them|those|these|it|that|compare|comparison|which|more|most|describe|"
    r"luxur)\w*",
    re.IGNORECASE,
)
# Bound the recall seed so it can't bloat the prompt: most-recent N artifacts,
# each payload truncated.
_RECALL_MAX_ARTIFACTS = 2
_RECALL_MAX_CHARS = 1200

# Cap files ingested as artifacts per turn (defensive against pathological
# multi-file payloads); extra files are ignored with a debug log.
_MAX_UPLOADS_PER_TURN = 20

# Egress + indirection tools are never "steered" — saying "reply" is normal, and
# find_tool/use_skill are the sanctioned indirection we *want*. The same set is
# "non-substantive" for gearing: these don't count toward heavy-model escalation.
_STEER_EXEMPT = frozenset(
    {"reply", "respond", "find_tool", "load_tool", "find_skill", "use_skill"}
)
_NON_SUBSTANTIVE_TOOLS = _STEER_EXEMPT

# A ``requires-actions`` spec is an Action class name with an optional inline
# version constraint, PEP 508-style: the comparison operator is the delimiter
# (``PageIndexAction>=2.0``, ``WebFetchAction==1.4.0``, ``X>=1.0,<2.0``).
_REQ_VERSION_OP = re.compile(r"[<>=!~]")


def _parse_action_requirement(spec: str) -> Tuple[str, str]:
    """Split a requires-actions spec into ``(action_type_name, constraint)``.

    The constraint is "" when the spec is a bare class name. The first
    comparison operator marks the boundary, so no separate delimiter is needed.
    """
    s = (spec or "").strip()
    m = _REQ_VERSION_OP.search(s)
    if not m:
        return s, ""
    return s[: m.start()].strip(), s[m.start() :].strip()


def _version_satisfies(version: str, constraint: str) -> bool:
    """Whether ``version`` (an Action's ``get_version()``) meets ``constraint``.

    Uses PEP 440 specifier semantics. Fails closed when a constraint is given
    but the action reports no/uncomparable version (can't prove the requirement
    is met); an *unparseable constraint* (skill-author typo) degrades to
    presence-only so one bad string doesn't silently nuke the skill.
    """
    if not constraint:
        return True
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
    except Exception:  # pragma: no cover - packaging is a standard dependency
        logger.warning(
            "requires-actions: 'packaging' unavailable; cannot enforce version "
            "constraint %r (treating as presence-only)",
            constraint,
        )
        return True
    try:
        spec = SpecifierSet(constraint)
    except Exception:
        logger.warning(
            "requires-actions: invalid version constraint %r; ignoring it",
            constraint,
        )
        return True
    if not version:
        return False
    try:
        return spec.contains(Version(version), prereleases=True)
    except Exception:
        logger.warning(
            "requires-actions: action version %r is not comparable to %r; "
            "failing the gate",
            version,
            constraint,
        )
        return False


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
    include_history_events: bool = attribute(
        default=True,
        description="Include interaction [EVENT] lines in loop history.",
    )
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
            "{identity_section}, {tools_section}, {skills_section}, "
            "{capabilities_section}, {parameters_section}."
        ),
    )
    # The Orchestrator's native core: the ``loop``-scoped hardening, applied in
    # the agentic loop (rendered into this system prompt). Reuses the common
    # ``Action.parameters`` subsystem. The Orchestrator also accumulates every
    # enabled action's params onto the interaction each turn; the response-scoped
    # ones are owned by the ReplyAction and applied in the response prompt.
    # Operators may extend/override per agent in agent.yaml.
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=orchestrator_core_parameters,
        description=(
            "The executive's native loop-scoped behavioural parameters, each "
            "{scope, condition?, response}. Applied in the agentic loop; pooled "
            "with every action's params onto the interaction each turn."
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
            "Per-tick user-prompt template. Placeholders: {utterance}, "
            "{observations_section}. (Conversation history is supplied as "
            "structured prior messages, not text; a legacy {history_section} "
            "placeholder is still accepted but rendered empty.)"
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
    memory_prompt: str = attribute(
        default=MEMORY_PROMPT,
        description="Memory-access protocol rendered in the LOOP PROTOCOL: search "
        "memory (the conversation in context + saved artifacts) before answering "
        "from a blank or claiming you can't recall. Set empty to omit.",
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
    auto_start_skills_on_new_user: Any = attribute(
        default_factory=list,
        description=(
            "Skill names to activate via use_skill when visitor.new_user is true. "
            "A list or single string. Order matters; the first task-lock skill in "
            "the list that activates becomes the locked surface for that turn. "
            "Empty disables. Mechanical use_skill only; bootstrap runs via requires-actions binding."
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
    proactive_tasks_enabled: bool = attribute(
        default=True,
        description="When True, surface queue_task for proactive task enqueueing.",
    )
    default_max_attempts: int = attribute(
        default=3,
        description="Default retry ceiling for queue_task when max_attempts is omitted.",
    )
    vision: bool = attribute(
        default=False,
        description=(
            "When True, images in visitor.data['image_urls'] are interpreted via "
            "VisionAction (its own multimodal model). With ingest_uploads on "
            "(default) the interpretation is consolidated into the image's "
            "source='upload' artifact (per-image); the standalone reflex storing a "
            "separate source='vision' artifact runs only as the fallback when "
            "ingest_uploads is off (ADR-0021). Off by default — no VisionAction "
            "call when unused. Requires a VisionAction on the agent and is skipped "
            "when visitor.data['image_interpretation'] is False."
        ),
    )
    ingest_uploads: bool = attribute(
        default=True,
        description=(
            "When True (default), every uploaded file in visitor.data (keys in "
            "`upload_data_keys`) is persisted to the per-user file storage and "
            "recorded as ONE consolidated source='upload' conversation artifact "
            "(ADR-0021 S4) — its file reference (path/mime/size) plus its "
            "content/understanding: text decoded into the payload, images enriched "
            "in place with a per-image VisionAction interpretation (file + "
            "interpretation in a single artifact, not two), other binaries a "
            "descriptor. `_interpret_upload` is the per-kind extension point for "
            "document interpreters."
        ),
    )
    upload_data_keys: List[str] = attribute(
        default_factory=lambda: list(DEFAULT_UPLOAD_KEYS),
        description="visitor.data keys scanned for uploaded files to ingest.",
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

        await self._finalize_proactive_task(visitor)

        # Single post-loop egress authority — renders any queued rails-IA
        # directives once, else falls back to clarify_text. Gated by the per-turn
        # emitted latch (set at every delivery choke point) so the turn never
        # double-sends. The loop's terminal reply/respond/final paths emit
        # directly and latch, so this no-ops when they already delivered.
        await self._egress(visitor)

    @staticmethod
    def _ia_emitted(interaction: Any) -> bool:
        """True if a dispatched IA produced user-facing output this turn.

        An IA emits either by setting ``interaction.response`` OR by queuing a
        directive (the directive-based publishing pattern, rendered by
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

    async def _egress(self, visitor: "InteractWalker") -> None:
        """The single post-loop egress authority.

        Runs only when nothing was delivered during the loop (terminal
        reply/respond/final paths emit directly and latch ``interaction.emitted``).
        Renders any queued rails-IA directives once, then falls back to
        ``clarify_text`` — all gated by the emitted latch so the turn never
        double-sends.
        """
        interaction = getattr(visitor, "interaction", None)
        if interaction is None or interaction.has_emitted():
            return
        # Gather any directives a rails IA queued this turn (no model text to add).
        await self._send_reply(visitor)
        if not interaction.has_emitted():
            await self._send_reply(visitor, self.clarify_text)

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
        if interaction.has_emitted():
            return  # already delivered this turn
        try:
            unexecuted = interaction.get_unexecuted_directives()
        except Exception:
            unexecuted = None
        if not unexecuted:
            return
        responder = await self.get_responder()
        if responder is None:
            return
        # The directive already carries any divergence / stay-on-script guidance:
        # the interview injects its own ``active_task_description`` into the
        # question directive on a diverged turn (see InterviewAction /
        # interview/engine), so the host just renders whatever was queued. No
        # host-side active-task injection here.
        #
        # The executive's response params are already on interaction.parameters
        # (seeded at loop start), so the responder renders them from the subsystem
        # — no need to pass them explicitly here.
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

    async def _interpret_upload(self, visitor: Any, item: Any) -> str:
        """Derive an interpretation for one upload, by kind (ADR-0021 S4).

        The single extension point that enriches an upload artifact with derived
        understanding. Today: images → a per-image VisionAction description (so
        an uploaded image is ONE artifact = file + its own interpretation, not
        two). Other kinds return "" here; documents/binaries get their own
        interpreters later (extraction/summary) by extending this dispatch —
        their artifact already carries the file reference + metadata.
        Returns "" when there is no interpreter or interpretation is suppressed.
        """
        if item.kind != "image":
            return ""
        if not self.vision:
            return ""
        data = getattr(visitor, "data", None) or {}
        if data.get("image_interpretation") is False:
            return ""
        vision = await self._resolve_action("VisionAction")
        if vision is None or not hasattr(vision, "describe"):
            return ""
        if item.raw is not None:
            entry: Any = {
                "base64": base64.b64encode(item.raw).decode("ascii"),
                "mime_type": item.mime,
                "filename": item.filename,
            }
        elif item.url:
            entry = {"url": item.url}
        else:
            return ""
        try:
            return (await vision.describe(visitor=visitor, images=[entry])) or ""
        except Exception as exc:
            logger.warning("ingest_uploads: image interpret failed: %s", exc)
            return ""

    async def _ingest_uploads(self, visitor: Any) -> str:
        """Persist every uploaded file in ``visitor.data`` as an artifact (S4).

        ADR-0021 S4. For each file across ``upload_data_keys`` (images, docs,
        generic attachments): write the bytes to the caller's per-user file
        storage and record ONE ``source="upload"`` conversation artifact that is
        the single home for that file — its reference (``path``/``mime``/
        ``size``) plus its content/understanding: text files decoded into the
        payload, images enriched in place with a per-image interpretation
        (consolidated, not a second artifact), other binaries a descriptor.
        Bytes are reaped with the artifact. Best-effort and bounded; returns the
        concatenated image interpretation(s) to seed the loop ("" if none).
        """
        if not self.ingest_uploads:
            return ""
        data = getattr(visitor, "data", None) or {}
        keys = list(self.upload_data_keys or DEFAULT_UPLOAD_KEYS)
        items = collect_uploads(data, keys)
        if not items:
            return ""
        conversation = getattr(visitor, "conversation", None)
        if conversation is None or not hasattr(conversation, "add_artifact"):
            return ""
        interaction = getattr(visitor, "interaction", None)

        from jvagent.core.sandbox import (
            resolve_agent_user,
            resolve_user_sandbox_relpath,
            sanitize_segment,
        )

        try:
            agent_id, user_id = await resolve_agent_user(visitor)
        except Exception:
            agent_id, user_id = (self.agent_id or ""), ""
        base_rel = resolve_user_sandbox_relpath(agent_id, user_id)
        iid = getattr(interaction, "id", "") or "turn"

        app = None
        try:
            from jvagent.core.app import App

            app = await App.get()
        except Exception:
            app = None

        seen: Set[Tuple[str, int]] = set()
        written = 0
        seeds: List[str] = []
        for idx, item in enumerate(items):
            if written >= _MAX_UPLOADS_PER_TURN:
                logger.debug(
                    "ingest_uploads: capped at %d files", _MAX_UPLOADS_PER_TURN
                )
                break
            dedup = (item.filename, item.size)
            if dedup in seen:
                continue
            seen.add(dedup)

            # Persist bytes to the per-user slice (lean graph: path, not blob).
            path = ""
            if item.raw is not None and app is not None:
                safe = sanitize_segment(item.filename, default=f"file_{idx}")
                candidate = f"{base_rel}/uploads/{iid}/{idx}_{safe}"
                try:
                    if await app.save_file(
                        candidate, item.raw, metadata={"mime": item.mime}
                    ):
                        path = candidate
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("ingest_uploads: save failed for %s: %s", safe, exc)

            # Derived understanding enriches the SAME artifact (consolidation).
            interpretation = await self._interpret_upload(visitor, item)
            tags = ["upload", item.kind, item.filename]
            if interpretation:
                payload = interpretation
                summary = (interpretation.strip().split("\n", 1)[0] or "")[:160]
                tags.append("interpreted")
                if item.kind == "image":
                    tags.append("vision")
                seeds.append(interpretation)
            elif item.kind == "text" and item.raw is not None:
                payload = decode_text(item.raw)
                summary = f"{item.filename} ({item.mime}, {human_size(item.size)})"
            else:
                loc = path or item.url or "(bytes not stored)"
                payload = (
                    f"Uploaded {item.kind}: {item.filename} "
                    f"({item.mime}, {human_size(item.size)}). Stored at: {loc}"
                )
                summary = f"{item.filename} ({item.mime}, {human_size(item.size)})"
            try:
                await conversation.add_artifact(
                    interaction,
                    name=item.filename or f"upload:{iid}:{idx}",
                    data=payload,
                    summary=summary,
                    source="upload",
                    kind=item.kind,
                    tags=tags,
                    filename=item.filename,
                    mime=item.mime,
                    size=item.size,
                    path=path,
                )
                written += 1
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("ingest_uploads: artifact write failed: %s", exc)
        return "\n\n---\n\n".join(seeds)

    async def _vision_reflex(self, visitor: Any) -> str:
        """Pre-loop image interpretation (ADR-0021).

        When ``vision`` is on, the current turn carries images, and vision isn't
        suppressed: run ``VisionAction`` (its own multimodal model), persist the
        interpretation as a ``source:"vision"`` conversation artifact, and return
        the text to seed the loop so this turn's reply uses it. Best-effort —
        any failure returns "" and the turn proceeds without vision.
        """
        if not self.vision:
            return ""
        data = getattr(visitor, "data", None) or {}
        if data.get("image_interpretation") is False:
            return ""
        if not (data.get("image_urls") or []):
            return ""
        vision = await self._resolve_action("VisionAction")
        if vision is None or not hasattr(vision, "describe"):
            return ""
        try:
            text = await vision.describe(visitor=visitor)
        except Exception as exc:
            logger.warning("orchestrator: vision reflex failed: %s", exc)
            return ""
        if not text:
            return ""
        conversation = getattr(visitor, "conversation", None)
        interaction = getattr(visitor, "interaction", None)
        if conversation is not None and hasattr(conversation, "add_artifact"):
            try:
                iid = getattr(interaction, "id", "") or ""
                summary = (text.strip().split("\n", 1)[0] or "")[:160]
                await conversation.add_artifact(
                    interaction,
                    name=(
                        f"image_interpretation:{iid}" if iid else "image_interpretation"
                    ),
                    data=text,
                    summary=summary,
                    source="vision",
                    tags=["image", "vision"],
                )
            except Exception as exc:
                logger.warning("orchestrator: vision artifact write failed: %s", exc)
        return text

    async def _artifact_recall_seed(self, visitor: Any) -> str:
        """Deterministically recall earlier image artifacts on a back-reference.

        ADR-0021 S3. The vision reflex covers turns that carry a *new* image; a
        weak model still fails to recall a *prior* image when the user refers
        back to it ("which house is nicer", "compare them"). When vision is on,
        this turn has no new image, the conversation holds image artifacts, and
        the utterance reads like a back-reference, seed the most recent image
        interpretation(s) into the loop so recall doesn't depend on the model
        choosing list_artifacts/get_artifact. Best-effort: returns "" on any miss.
        """
        if not self.vision:
            return ""
        data = getattr(visitor, "data", None) or {}
        if data.get("image_urls"):  # a new image → the vision reflex handles it
            return ""
        utterance = (getattr(visitor, "utterance", "") or "").lower()
        if not utterance or not _BACKREF_CUE.search(utterance):
            return ""
        conversation = getattr(visitor, "conversation", None)
        if conversation is None or not hasattr(conversation, "get_artifacts"):
            return ""
        try:
            # Consolidated image artifacts are source="upload" tagged "image"
            # (S4); legacy standalone interpretations are source="vision".
            items = await conversation.get_artifacts(tags=["image"])
            if not items:
                items = await conversation.get_artifacts(source="vision")
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("artifact recall seed: query failed: %s", exc)
            return ""
        if not items:
            return ""
        # Most recent first, capped, each payload bounded to keep the prompt lean.
        chunks: List[str] = []
        for art in list(items)[-_RECALL_MAX_ARTIFACTS:]:
            text = (getattr(art, "data", "") or "").strip()
            if text:
                chunks.append(text[:_RECALL_MAX_CHARS])
        return "\n\n---\n\n".join(chunks)

    async def _enforce_required_actions(self, docs: List[Any]) -> List[Any]:
        """Drop skills whose ``requires-actions`` aren't satisfied (hard gate).

        Each spec is an Action class name with an optional inline PEP 508-style
        version constraint (the comparison operator is the delimiter), e.g.
        ``CodeExecutionAction``, ``PageIndexAction>=2.0``, ``WebFetchAction==1.4.0``.
        A skill is kept only when every spec it declares is met — the Action
        type resolves (enabled-only, O(1) cached) AND, when a constraint is
        given, the resolved Action's ``get_version()`` satisfies it. Otherwise
        the skill is hidden from the whole surface (list, find_skill, use_skill,
        always-active pinning) so the model never sees a skill it can't run.
        Skills with no ``requires-actions`` pass through unchanged.
        """
        specs_by_doc: List[Tuple[Any, List[Tuple[str, str]]]] = []
        type_names: Set[str] = set()
        for d in docs:
            reqs = [
                _parse_action_requirement(s)
                for s in (getattr(d, "requires_actions", ()) or ())
            ]
            specs_by_doc.append((d, reqs))
            type_names.update(name for name, _ in reqs if name)
        if not type_names:
            return docs

        # Resolve each distinct type once; cache versions for constrained ones.
        actions: Dict[str, Any] = {}
        versions: Dict[str, str] = {}
        for name in type_names:
            action = await self._resolve_action(name)
            actions[name] = action
            if action is not None:
                try:
                    versions[name] = (await action.get_version()) or ""
                except Exception:
                    versions[name] = ""

        kept: List[Any] = []
        for d, reqs in specs_by_doc:
            unmet: List[str] = []
            for name, constraint in reqs:
                if not name:
                    continue
                if actions.get(name) is None:
                    unmet.append(f"{name}{constraint} (not available)")
                elif constraint and not _version_satisfies(
                    versions.get(name, ""), constraint
                ):
                    have = versions.get(name) or "unknown"
                    unmet.append(f"{name}{constraint} (have {have})")
            if unmet:
                logger.info(
                    "orchestrator: skill %r hidden — unmet requires-actions: %s",
                    getattr(d, "name", "?"),
                    ", ".join(unmet),
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
        for action in await self._enabled_interact_actions(agent):
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
            if triggers:
                continue  # routable/tool IA — omit from the walk path
            keep.append(action)  # non-routable IA — keep in the weight chain
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

        for t in build_proactive_tools(self, visitor):
            tools[t.name] = t
            visible.add(t.name)

        # Artifact back-reference tools (ADR-0021). Surfaced with vision (its
        # only producer today) so the model can list/read prior artifacts (e.g.
        # a past image interpretation) without re-upload; visitor-bound for
        # conversation access.
        if self.vision:
            for t in build_artifact_tools(self, visitor):
                tools[t.name] = t
                visible.add(t.name)

        actions = await self._enabled_actions(agent)
        from jvagent.action.reply.reply_action import ReplyAction

        mcp_cls = self._mcp_action_class()

        for action in actions:
            if action is self or isinstance(action, OrchestratorInteractAction):
                continue
            if isinstance(action, ReplyAction):
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
                    # decides visibility after assembly). Actions that set
                    # ``binds_tools_to_visitor`` receive the live visitor at wrap.
                    wrap_visitor = (
                        visitor
                        if getattr(action, "binds_tools_to_visitor", False)
                        else None
                    )
                    tools[name] = wrap_action_tool(tool, visitor=wrap_visitor)
                    longtail.add(name)

        # Egress reply/respond tools are ORCHESTRATOR-owned (ADR-0025): the model
        # calls reply/respond → the orchestrator (the AUTHOR) queues the text as an
        # interaction.directive → the responder (ReplyAction) gathers the whole
        # queue and sends ONE reply. ReplyAction never adds directives itself, so
        # it stays a pure conduit and interaction.directives reflects the turn's
        # authored output (including model-authored / skill turns).
        responder = await self.get_responder()
        if responder is not None:
            from jvagent.tooling.tool import Tool
            from jvagent.tooling.tool_result import ToolResult

            _egress_text_schema = {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "The text."}},
                "required": ["text"],
            }

            async def _egress_exec(
                visitor: Any = None, text: str = "", **kwargs: Any
            ) -> Any:
                txt = text
                if not (txt or "").strip():
                    for k in (
                        "message",
                        "content",
                        "answer",
                        "reply",
                        "response",
                        "body",
                    ):
                        v = kwargs.get(k)
                        if isinstance(v, str) and v.strip():
                            txt = v
                            break
                await self._send_reply(visitor, txt)
                return ToolResult(content="(replied to user)")

            _reply_tool = Tool(
                name="reply",
                description=(
                    "Send your reply to the user. Pass your final text; any pending "
                    "directives/parameters are applied automatically."
                ),
                parameters_schema=_egress_text_schema,
                execute=_egress_exec,
            )
            _respond_tool = Tool(
                name="respond",
                description=(
                    "Reply to the user in the agent's identity (styled, "
                    "identity-consistent)."
                ),
                parameters_schema=_egress_text_schema,
                execute=_egress_exec,
            )
            tools["reply"] = wrap_action_tool(_reply_tool, visitor=visitor)
            tools["respond"] = wrap_action_tool(_respond_tool, visitor=visitor)
            visible.add("reply")
            visible.add("respond")

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
            # Relevance signal = the user's message PLUS any in-progress plan's
            # checklist. A parked multi-step plan resumed on a low-signal turn
            # ("Well?", "continue") would otherwise surface nothing and force the
            # model through find_tool round-trips for tools the next plan step
            # needs (e.g. pageindex__assimilate for "add to knowledge base").
            relevance_text = utterance
            plan_steps = self._open_plan_step(visitor)
            if plan_steps:
                relevance_text = f"{utterance}\n{plan_steps}"
            keep = self._presurface_tools(
                relevance_text, longtail, tools, int(self.lean_presurface_k)
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
        from jvagent.action.orchestrator.skill_tasks import compose_skill_activate_hooks

        activate_hook, reactivate_hook = compose_skill_activate_hooks(
            actions, visitor, code_exec
        )
        for name, t in build_skill_meta_tools(
            docs,
            set(tools.keys()),
            activated,
            visible,
            activate_hook=activate_hook,
            reactivate_hook=reactivate_hook,
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
    # Auto-start skills on new user
    # ------------------------------------------------------------------

    def _normalized_auto_start_skill_names(self) -> List[str]:
        """Skill names from config (list or single string)."""
        raw = self.auto_start_skills_on_new_user
        if isinstance(raw, str):
            items = [raw] if raw.strip() else []
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            items = []
        out: List[str] = []
        seen: Set[str] = set()
        for item in items:
            name = str(item).strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out

    @staticmethod
    def _is_new_user(visitor: Any) -> bool:
        if getattr(visitor, "new_user", False):
            return True
        conversation = getattr(visitor, "conversation", None)
        if conversation is None:
            return False
        ctx = getattr(conversation, "context", None) or {}
        return bool(ctx.get("new_user"))

    async def _has_runnable_work(self, visitor: Any) -> bool:
        """Engagement state (ADR-0026 invariant 7): a task the orchestrator can drain
        is runnable right now. Used so the turn does not finalize idle while runnable
        work remains. Scoped to drainable types (SKILL + registered runners)."""
        from jvagent.action.orchestrator.skill_tasks import task_store_for_conversation
        from jvagent.action.orchestrator.task_runners import runnable_task_types
        from jvagent.memory.task_graph import pick_top_runnable

        store = getattr(visitor, "tasks", None) or task_store_for_conversation(
            getattr(visitor, "conversation", None)
        )
        if store is None:
            return False
        try:
            return (
                pick_top_runnable(store, task_types=runnable_task_types()) is not None
            )
        except Exception:
            return False

    async def _drain_runnable_tasks(
        self, visitor: Any, observations: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Drain non-SKILL runnable tasks via their registered runners (ADR-0026
        §2.4/§3): the standard mechanism that keeps the orchestrator watching the
        work graph regardless of any skill turn-lock.

        Resolves the top runnable task; SKILL tasks are advanced by the orchestrator's
        own think-act loop, so they are left for the skill path. A non-skill type with
        a registered runner is dispatched: a ``completed`` result re-resolves (a parent
        may unblock), a ``blocked`` result yields one egress directive (stay engaged),
        ``advanced`` keeps draining. Bounded by ``activation_budget``. Inert until a
        consumer registers a runner, so it never changes skill-only behavior. Returns a
        blocking egress directive, or ``None`` when nothing non-skill is runnable.
        """
        from jvagent.action.orchestrator.skill_tasks import task_store_for_conversation
        from jvagent.action.orchestrator.task_runners import (
            BUILTIN_LOOP_ADVANCED,
            RunContext,
            get_task_runner,
            runnable_task_types,
        )
        from jvagent.memory.task_graph import pick_top_runnable

        store = getattr(visitor, "tasks", None) or task_store_for_conversation(
            getattr(visitor, "conversation", None)
        )
        if store is None:
            return None
        budget = max(1, int(getattr(self, "activation_budget", 0) or 1))
        for _ in range(budget):
            top = pick_top_runnable(store, task_types=runnable_task_types())
            if top is None:
                return None  # store drained
            ttype = str(getattr(top, "task_type", "") or "").upper()
            if ttype in BUILTIN_LOOP_ADVANCED:
                return None  # SKILL/PROACTIVE are advanced by the loop, not a runner
            runner = get_task_runner(ttype)
            if runner is None:  # pragma: no cover - guarded by runnable_task_types
                return None
            try:
                result = await runner(
                    RunContext(
                        orchestrator=self,
                        visitor=visitor,
                        task=top,
                        observations=observations,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "orchestrator: task runner for %r raised: %s", ttype, exc
                )
                return None
            for ob in getattr(result, "observations", None) or []:
                if isinstance(ob, dict):
                    observations.append(ob)
            status = getattr(result, "status", "advanced")
            if status == "blocked":
                return getattr(result, "directive", None)
            if status == "completed":
                fresh = store.get(top.id)
                if fresh is not None and fresh.status not in (
                    "completed",
                    "failed",
                    "cancelled",
                ):
                    try:
                        await fresh.complete()
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("orchestrator: runner-complete failed: %s", exc)
                continue
            # advanced → keep draining
        return None

    async def _find_active_task_lock_skill_doc(
        self, visitor: Any, skill_docs: List[Any], actions: List[Any]
    ) -> Optional[Any]:
        """Return the SkillDoc for an active task-lock task, if any."""
        from jvagent.action.orchestrator.skill_tasks import (
            resolve_active_task_lock_skill,
        )

        return await resolve_active_task_lock_skill(
            visitor,
            skill_docs,
            actions,
            lock_active_flow=self.lock_active_flow,
            auto_start_names=self._normalized_auto_start_skill_names(),
        )

    def _should_auto_start_skills(
        self,
        visitor: Any,
        skill_docs: List[Any],
        *,
        active_skill_doc: Optional[Any],
        flow_owner: Optional[str],
    ) -> bool:
        names = self._normalized_auto_start_skill_names()
        if not names:
            return False
        if active_skill_doc is not None:
            return False
        if flow_owner:
            return False
        if not self._is_new_user(visitor):
            return False
        skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
        return any(n in skill_by_name for n in names)

    async def _apply_active_task_lock_skill(
        self,
        skill_doc: Any,
        loop_actions: List[Any],
        visitor: Any,
        utterance: str,
        tools: Dict[str, Any],
        visible: Set[str],
        activated: List[str],
        observations: List[Dict[str, Any]],
        skill_docs: Optional[List[Any]] = None,
    ) -> Tuple[Dict[str, Any], Set[str], str]:
        """Turn-lock surface prep — delegated to generic skill_tasks helpers."""
        from jvagent.action.orchestrator.skill_tasks import apply_task_lock_turn

        return await apply_task_lock_turn(
            skill_doc,
            loop_actions,
            visitor,
            user_message=utterance,
            tools=tools,
            visible=visible,
            activated=activated,
            observations=observations,
            skill_docs=skill_docs,
        )

    async def _apply_task_lock_after_use_skill(
        self,
        *,
        skill_name: str,
        activation_obs: str,
        skill_docs: List[Any],
        loop_actions: List[Any],
        visitor: Any,
        utterance: str,
        tools: Dict[str, Any],
        visible: Set[str],
        activated: List[str],
        observations: List[Dict[str, Any]],
    ) -> Tuple[Optional[Any], Dict[str, Any], Set[str], str, Optional[str]]:
        """Run turn-lock prep when use_skill first-activates a locked skill mid-loop.

        Pre-loop ``apply_task_lock_turn`` covers auto-start and resumed tasks;
        model-driven ``use_skill`` on tick 1 skipped that path, so message
        evaluation / next_field prep never ran on the activation turn.

        Returns a 5th element: a terminal *detour directive* when a prerequisite was
        pushed this turn. The detour's first question must be asked by the server and
        end the turn — handed to the model as a fillable observation it fabricates the
        answer and races past the gate (ADR-0026: the detour start is
        orchestrator-delivered, not model-mediated).
        """
        if not (activation_obs or "").startswith("Activated skill"):
            return None, tools, visible, "", None
        doc = next(
            (d for d in skill_docs if getattr(d, "name", None) == skill_name),
            None,
        )
        if doc is None or not getattr(doc, "task_lock", False):
            return None, tools, visible, "", None
        # Declarative gate (ADR-0026): if the activated skill has an unmet
        # precondition, push the prerequisite task and redirect the lock to it (the
        # gated skill is now blocked and resumes when the prerequisite completes).
        # Chained so a prerequisite with its own unmet precondition pushes too.
        from jvagent.action.orchestrator.skill_tasks import (
            action_for_skill,
            push_unmet_prerequisites,
        )

        pushed_any = False
        for _ in range(8):
            pushed = await push_unmet_prerequisites(visitor, doc, loop_actions)
            if not pushed:
                break
            pushed_any = True
            prereq_doc = next(
                (d for d in skill_docs if getattr(d, "name", None) == pushed), None
            )
            if prereq_doc is None:
                break
            doc = prereq_doc
        tools, visible, skills_section = await self._apply_active_task_lock_skill(
            doc,
            loop_actions,
            visitor,
            utterance,
            tools,
            visible,
            activated,
            observations,
            skill_docs=skill_docs,
        )
        from jvagent.action.orchestrator.skill_tasks import (
            prune_task_lock_tools_for_actions,
        )

        await prune_task_lock_tools_for_actions(loop_actions, visitor, tools, visible)
        # When a prerequisite was just pushed, end the turn on its first question so
        # the model cannot answer it itself. Generic, duck-typed — any task-lock
        # action may expose the hook; absent it, fall through to normal egress.
        detour_directive: Optional[str] = None
        if pushed_any:
            bound = action_for_skill(doc, loop_actions)
            if bound is not None and hasattr(bound, "task_lock_entry_directive"):
                try:
                    detour_directive = await bound.task_lock_entry_directive(
                        doc.name, visitor
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("orchestrator: detour entry directive failed: %s", exc)
        return doc, tools, visible, skills_section, detour_directive

    async def _reground_parent_lock(
        self,
        parent_doc: Any,
        loop_actions: List[Any],
        visitor: Any,
        observations: List[Dict[str, Any]],
    ) -> None:
        """Re-surface a locked parent task right after a companion finishes, so the
        model resumes it in the same turn instead of waiting for next-turn
        re-grounding. Appends the parent's pending-step status (via its bound
        action's ``prepare_task_lock_turn``) and an explicit return directive."""
        from jvagent.action.orchestrator.skill_tasks import action_for_skill

        bound = action_for_skill(parent_doc, loop_actions)
        if bound is not None and hasattr(bound, "prepare_task_lock_turn"):
            try:
                prep = await bound.prepare_task_lock_turn(parent_doc.name, visitor)
                for ob in getattr(prep, "observations", None) or []:
                    if isinstance(ob, dict):
                        ob.setdefault("kind", "server_prep")
                        observations.append(ob)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("orchestrator: parent re-ground failed: %s", exc)
        observations.append(
            {
                "tool": "(task-lock)",
                "args": {},
                "observation": (
                    f"(Side request handled. Return to {parent_doc.name} now and "
                    "continue its current step — include that step in your reply. "
                    "Do not start anything else.)"
                ),
                "kind": "server_prep",
            }
        )

    async def _maybe_resume_after_completion(
        self,
        obs: str,
        completed_doc: Any,
        skill_docs: List[Any],
        loop_actions: List[Any],
        visitor: Any,
        utterance: str,
        tools: Dict[str, Any],
        visible: Set[str],
        activated: List[str],
        observations: List[Dict[str, Any]],
    ) -> Optional[Tuple[Any, Dict[str, Any], Set[str], str, Optional[str]]]:
        """Drain step (ADR-0026): if a task-lock skill just completed and another
        task is now the top runnable task, resume it in the same turn.

        Returns ``(resumed_doc, tools, visible, skills_section, terminal_directive)``.
        When the resumed skill can voice its own next question (``terminal_directive``
        set), the caller delivers it and ends the turn — the resume is
        orchestrator-driven, not model-mediated, so the model never gets to fabricate
        the next answer. Otherwise ``terminal_directive`` is ``None`` and the caller
        keeps draining with a re-ground note. Returns ``None`` when nothing is waiting
        (inert until prerequisites are pushed).
        """
        try:
            data = json.loads(obs)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if not (data.get("interview_complete") or data.get("status") == "completed"):
            return None
        # A skill completed → its task is closed and its session cleared. Re-resolve
        # the top runnable task-lock skill.
        parent = await self._find_active_task_lock_skill_doc(
            visitor, skill_docs, loop_actions
        )
        if parent is None:
            return None
        if getattr(parent, "name", None) == getattr(completed_doc, "name", None):
            return None  # same skill still owns the lock — not a resume
        from jvagent.action.orchestrator.skill_tasks import (
            _active_skill_task,
            action_for_skill,
            task_store_for_conversation,
        )

        # The gated task carries the original request as its seed (seed_from:
        # [utterance]); a pushed prerequisite has none. That presence is the clean
        # discriminator between "resume the gated service with its original request"
        # and "enter a fresh prerequisite".
        store = task_store_for_conversation(getattr(visitor, "conversation", None))
        ptask = _active_skill_task(store, parent.name) if store else None
        seed_utterance = str(
            (getattr(ptask, "seed", None) or {}).get("utterance") or ""
        )
        # Re-apply the parent's locked surface. When resuming the gated service, run
        # its activation against the *original* request so its first fields extract
        # from it (extraction is model-owned, so this is fed as the activation input,
        # not auto-filled) instead of re-asking for what the user already provided.
        tools, visible, skills_section = await self._apply_active_task_lock_skill(
            parent,
            loop_actions,
            visitor,
            seed_utterance or utterance,
            tools,
            visible,
            activated,
            observations,
            skill_docs=skill_docs,
        )
        _, rd = self._result_next(obs)
        ack = self._directive_user_text(rd)

        if seed_utterance:
            # Resume the gated service: hand the model the original request to fill
            # the pending field(s), then continue. Consume the seed so it is not
            # re-injected on any later resume.
            if ptask is not None:
                try:
                    remaining = {
                        k: v for k, v in (ptask.seed or {}).items() if k != "utterance"
                    }
                    await ptask.set_seed(remaining)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("orchestrator: seed consume failed: %s", exc)
            observations.append(
                {
                    "tool": "(task-resume)",
                    "args": {},
                    "observation": (
                        "(A prerequisite just completed; the account/session is now "
                        f"in place. {ack} Resume {parent.name}: the user's original "
                        f'request was "{seed_utterance}". Fill the pending field(s) '
                        "from it, then continue — do not re-ask for anything already "
                        "in that request and do not start anything else.)"
                    ),
                    "kind": "server_prep",
                }
            )
            return parent, tools, visible, skills_section, None

        # Fresh prerequisite (no seed): deliver its first question terminally
        # (server-driven), prefixed with the completed step's acknowledgement, so the
        # model cannot fabricate the answer the way the detour-start would.
        bound = action_for_skill(parent, loop_actions)
        entry = None
        if bound is not None and hasattr(bound, "task_lock_entry_directive"):
            try:
                entry = await bound.task_lock_entry_directive(parent.name, visitor)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("orchestrator: resume entry directive failed: %s", exc)
        if entry:
            question = self._directive_user_text(entry)
            terminal = "Tell the user: " + (f"{ack} {question}".strip())
            return parent, tools, visible, skills_section, terminal
        # No deliverable question — fall back to a re-ground note.
        observations.append(
            {
                "tool": "(task-resume)",
                "args": {},
                "observation": (
                    "(A prerequisite just completed; its account/session is now in "
                    f"place. {ack} Resume {parent.name} now and continue its next "
                    "step in your reply — do not start anything else.)"
                ),
                "kind": "server_prep",
            }
        )
        return parent, tools, visible, skills_section, None

    @staticmethod
    def _directive_user_text(directive: str) -> str:
        """Strip a directive's ``Tell the user:`` prefix and any model-facing guidance
        (separated by the invisible U+2063) down to the user-facing sentence."""
        text = (directive or "").strip()
        if not text:
            return ""
        text = text.split("\u2063", 1)[0].strip()
        low = text.lower()
        if low.startswith("tell the user:"):
            text = text[len("tell the user:") :].strip()
        return text

    async def _run_tool_observation(
        self,
        tools: Dict[str, Any],
        tool_name: str,
        args: Dict[str, Any],
        observations: List[Dict[str, Any]],
    ) -> None:
        tool = tools.get(tool_name)
        if tool is None:
            observations.append(
                {
                    "tool": tool_name,
                    "args": args,
                    "observation": f"(no such tool: {tool_name})",
                }
            )
            return
        try:
            if self.tool_call_timeout and self.tool_call_timeout > 0:
                obs = await asyncio.wait_for(
                    tool.run(args), timeout=self.tool_call_timeout
                )
            else:
                obs = await tool.run(args)
        except asyncio.TimeoutError:
            obs = f"(tool {tool_name} timed out after {self.tool_call_timeout}s)"
        except Exception as exc:
            logger.warning("orchestrator: onboard tool %r raised: %s", tool_name, exc)
            obs = f"(tool error: {exc})"
        observations.append({"tool": tool_name, "args": args, "observation": obs or ""})

    async def _seed_auto_start_skills(
        self,
        visitor: Any,
        skill_docs: List[Any],
        tools: Dict[str, Any],
        observations: List[Dict[str, Any]],
    ) -> Optional[Any]:
        """Mechanically use_skill for each configured new-user skill.
        Returns the first task-lock skill doc in config order, if any."""
        names = self._normalized_auto_start_skill_names()
        skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
        activated_names: List[str] = []
        first_locked: Optional[Any] = None
        for name in names:
            doc = skill_by_name.get(name)
            if doc is None:
                continue
            await self._run_tool_observation(
                tools,
                "use_skill",
                {"name": name},
                observations,
            )
            activated_names.append(name)
            if first_locked is None and getattr(doc, "task_lock", False):
                first_locked = doc
        if activated_names:
            observations.append(
                {
                    "tool": "(auto-start)",
                    "args": {},
                    "observation": (
                        "Skills auto-activated for new user: "
                        + ", ".join(activated_names)
                        + ". Follow each skill's procedure above. Do not call "
                        "use_skill again for those names this turn."
                    ),
                }
            )
        return first_locked

    async def _seed_proactive_dispatch(
        self,
        visitor: Any,
        skill_docs: List[Any],
        tools: Dict[str, Any],
        observations: List[Dict[str, Any]],
    ) -> None:
        """Pre-load proactive task context and optional skill before the loop.

        The scheduler (ADR-0022) claims an eligible proactive task (pending → active)
        and passes it via ``visitor.data``. As a fallback the orchestrator resolves a
        claimed proactive task straight from the work graph — so proactive work is
        drained from the store like any other task, not only via the side channel
        (ADR-0026 unification: scheduler eligibility-gates + claims; the drain
        dispatches)."""
        data = getattr(visitor, "data", None) or {}
        task_id = data.get("proactive_task_id")
        directive = str(data.get("proactive_directive") or "").strip()
        skill_hint = str(data.get("proactive_skill") or "").strip()
        if not task_id or not directive:
            resolved = self._resolve_active_proactive(visitor)
            if resolved is None:
                return
            task_id, directive, skill_hint = resolved
            # Mirror into visitor.data so the rest of the turn (e.g.
            # _finalize_proactive_task) treats a store-resolved task identically to a
            # scheduler-passed one.
            if not isinstance(getattr(visitor, "data", None), dict):
                visitor.data = {}
            visitor.data.update(
                {
                    "is_proactive": True,
                    "proactive_task_id": task_id,
                    "proactive_directive": directive,
                    "proactive_skill": skill_hint,
                }
            )
        observations.append(
            {
                "tool": "(proactive-task)",
                "args": {},
                "observation": (
                    f"Proactive task {task_id} is active this turn. "
                    f"Complete this objective: {directive}"
                ),
            }
        )
        skill = skill_hint
        if not skill:
            return
        skill_by_name = {d.name: d for d in skill_docs if getattr(d, "name", None)}
        if skill not in skill_by_name:
            return
        await self._run_tool_observation(
            tools,
            "use_skill",
            {"name": skill},
            observations,
        )

    def _resolve_active_proactive(self, visitor: Any) -> Optional[Tuple[str, str, str]]:
        """Resolve a claimed (active) proactive task from the work graph as
        ``(task_id, directive, skill)``, or ``None``. The graph treats a proactive
        task as runnable only once the scheduler has claimed it (active), so this
        never fires for a still-queued (pending) task."""
        from jvagent.action.orchestrator.skill_tasks import task_store_for_conversation
        from jvagent.memory.task_graph import pick_top_runnable
        from jvagent.memory.task_proactive import (
            PROACTIVE_TASK_TYPE,
            ProactiveTaskSpec,
        )

        store = getattr(visitor, "tasks", None) or task_store_for_conversation(
            getattr(visitor, "conversation", None)
        )
        if store is None:
            return None
        try:
            top = pick_top_runnable(store, task_types=[PROACTIVE_TASK_TYPE])
            if top is None:
                return None
            spec = ProactiveTaskSpec.from_task_handle(top)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("orchestrator: proactive resolve failed: %s", exc)
            return None
        directive = str(getattr(spec, "directive", "") or "").strip()
        if not directive:
            return None
        return top.id, directive, str(getattr(spec, "skill", "") or "").strip()

    async def _finalize_proactive_task(self, visitor: Any) -> None:
        """Complete, requeue, or fail an in-flight proactive dispatch."""
        data = getattr(visitor, "data", None) or {}
        task_id = data.get("proactive_task_id")
        if not task_id:
            return
        store = getattr(visitor, "tasks", None)
        if store is None:
            return
        from jvagent.action.task_monitor.finalize import finalize_proactive_task

        await finalize_proactive_task(
            store,
            str(task_id),
            interaction=getattr(visitor, "interaction", None),
        )

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    async def _accumulate_parameters(self, interaction: Any) -> None:
        """Pool every enabled action's scoped parameters onto
        ``interaction.parameters`` — the accumulation step of the common
        subsystem. Params are queued like directives (observable, persisted,
        de-duplicated); each injection site then renders only its scope: the
        orchestration loop prompt here, the response prompt under the
        ReplyAction. Core params carry ``ambient`` so seeding them doesn't force
        a compose at the egress.
        """
        if interaction is None:
            return
        agent = await self._safe_agent()
        actions = await self._enabled_actions(agent) if agent else [self]
        try:
            if await accumulate_action_parameters(interaction, actions):
                await interaction.save()
        except Exception as exc:
            logger.debug("orchestrator: accumulating parameters failed: %s", exc)

    async def _run_loop(self, visitor: "InteractWalker") -> None:
        activated: List[str] = []
        visible: Set[str] = set()
        skill_docs: List[Any] = []
        utterance = getattr(visitor, "utterance", "") or ""

        # Accumulate every action's scoped params onto the interaction so they
        # flow through the subsystem of record (observable + deduped) and each
        # injection site renders its scope.
        interaction = getattr(visitor, "interaction", None)
        await self._accumulate_parameters(interaction)

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
        # Advertised abilities: each enabled action's get_capabilities() merged
        # with the skill descriptions. Sourced from the actions/skills directly
        # (not the lean-surfaced tool list), so it stays complete even when most
        # callable tools are hidden behind find_tool — the model then never
        # under-claims an ability ("I can't sign you up…" while holding the
        # signup flow). Stable for the turn.
        capabilities_section = render_capabilities_section(
            await self._collect_capabilities(skill_docs)
        )
        # Orchestration-scoped rules from the accumulated pool, rendered into the
        # system prompt — they govern how the executive reasons. Response-scoped
        # rules belong to the reply compose, not here. Falls back to this action's
        # own orchestration core when the interaction has no pool yet.
        # The orchestration rules govern how the executive reasons; the core
        # response params are applied here too as safeguards, because the
        # executive can author a user-facing reply directly (the fast ``reply``
        # path applies no compose-time shaping). The reply compose renders the
        # response set as well — whichever path produces user text is hardened.
        _pool = getattr(interaction, "parameters", None) or self.parameters
        parameters_section = render_parameters(
            orchestration_parameters(_pool) + reply_core_parameters()
        )

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
            # directive (the directive-based publishing pattern — `_finalize_directives`
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
            await self._finalize_plan(visitor)
            return

        if flow_owner and flow_owner not in tools:
            from jvagent.action.orchestrator.continuation import (
                cancel_orphan_flow_tasks,
            )

            # Locked-in skill tasks use the skill name as owner_action — they
            # are not routable IA tools, so exempt them from the orphan sweep.
            _locked_skill_names: Set[str] = {
                d.name
                for d in skill_docs
                if getattr(d, "task_lock", False) and getattr(d, "name", None)
            }
            await cancel_orphan_flow_tasks(
                visitor,
                routable_tool_names=set(tools.keys()),
                locked_skill_names=_locked_skill_names,
            )
            flow_owner = active_flow_owner(visitor, flow_tool_names=flow_tool_names)

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

        agent = await self._safe_agent()
        loop_actions = await self._enabled_actions(agent) if agent else [self]

        observations: List[Dict[str, Any]] = []
        # Pre-loop upload ingestion (ADR-0021 S4): persist EVERY uploaded file in
        # visitor.data to per-user storage as ONE consolidated source="upload"
        # artifact each — images enriched in place with a per-image
        # interpretation (file + understanding in a single artifact). Returns the
        # image interpretation(s) to seed the loop. Best-effort; never blocks.
        try:
            vision_seed = await self._ingest_uploads(visitor)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("orchestrator: upload ingestion failed: %s", exc)
            vision_seed = ""
        # Fallback to the standalone vision reflex only when ingestion produced
        # no image interpretation (e.g. ingest_uploads disabled) — preserves
        # vision for that config without double-interpreting images.
        if not vision_seed:
            vision_seed = await self._vision_reflex(visitor)
        if vision_seed:
            observations.append(
                {
                    "tool": "interpret_images",
                    "args": {},
                    "observation": f"Interpretation of attached image(s): {vision_seed}",
                }
            )
        else:
            # No new image, but the user may be referring back to one shown
            # earlier — deterministically recall its stored interpretation so a
            # weak model doesn't claim it can't remember (ADR-0021 S3).
            recall_seed = await self._artifact_recall_seed(visitor)
            if recall_seed:
                observations.append(
                    {
                        "tool": "get_artifact",
                        "args": {},
                        "observation": (
                            "Recalled interpretation of image(s) the user shared "
                            f"earlier in this conversation:\n{recall_seed}"
                        ),
                    }
                )

        await self._seed_proactive_dispatch(visitor, skill_docs, tools, observations)

        active_skill_doc = await self._find_active_task_lock_skill_doc(
            visitor, skill_docs, loop_actions
        )

        if active_skill_doc is None and self._should_auto_start_skills(
            visitor,
            skill_docs,
            active_skill_doc=active_skill_doc,
            flow_owner=flow_owner,
        ):
            first_locked = await self._seed_auto_start_skills(
                visitor, skill_docs, tools, observations
            )
            if first_locked is not None:
                active_skill_doc = first_locked
            elif activated:
                for doc in skill_docs:
                    if getattr(doc, "task_lock", False) and doc.name in activated:
                        active_skill_doc = doc
                        break

        locked_pending_directive: Optional[str] = None
        if active_skill_doc is not None:
            prep_obs_before = len(observations)
            tools, visible, skills_section = await self._apply_active_task_lock_skill(
                active_skill_doc,
                loop_actions,
                visitor,
                utterance,
                tools,
                visible,
                activated,
                observations,
                skill_docs=skill_docs,
            )
            await self._emit_server_prep_tool_thoughts(
                visitor, observations, since_index=prep_obs_before
            )

        from jvagent.action.orchestrator.skill_tasks import (
            prune_task_lock_tools_for_actions,
        )

        await prune_task_lock_tools_for_actions(loop_actions, visitor, tools, visible)

        # Companion capabilities the active lock permits. Skill names gate
        # use_skill (a side-skill like FAQ is allowed; switching to an unrelated
        # skill is not). Tool names mark a companion detour so the parent task can
        # be re-grounded the moment the side request is handled.
        locked_companion_skill_names: Set[str] = set()
        locked_companion_tools: Set[str] = set()
        if active_skill_doc is not None:
            from jvagent.action.orchestrator.skill_tasks import (
                _companion_surface,
                resolve_lock_companions,
            )

            _companion_skills, _companion_globs = resolve_lock_companions(
                active_skill_doc, skill_docs
            )
            locked_companion_skill_names = {
                d.name for d in _companion_skills if getattr(d, "name", None)
            }
            _allowed, _ = _companion_surface(_companion_skills, _companion_globs, tools)
            # Tool names only — use_skill is handled by the skill-name branch.
            locked_companion_tools = {t for t in _allowed if t != "use_skill"}

        budget = max(1, int(self.activation_budget))
        history = await self._history(visitor)
        ticks = 0
        ended_via = "budget"
        last_sig: Optional[tuple] = None
        repeats = 0
        # Directive contract: a tool result carries the authoritative next step.
        # ``pending_chain`` holds a tool the model MUST call before it can finalize
        # (so it can't fabricate "you're all set" without running it); a terminal
        # "Tell the user:" directive with no chain is the turn's reply and is
        # delivered directly in the loop body (so the model can't re-decide and
        # re-run the same tool). Both are enforced below — generically, no tool
        # is named in code.
        pending_chain: Optional[str] = None
        chain_deflections = 0
        # Plan-completion guard (thin, plan-gated): when an active multi-step plan
        # still has open steps, a bare "I'll do X next" narration (coerced to a
        # reply) or a premature ``final`` is deflected once so the loop keeps
        # going instead of ending the turn mid-task. Bounded so a deliberate
        # reply/finish is never blocked for long.
        plan_deflections = 0
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

        # Standing store drain (ADR-0026 §3/§2.4): the orchestrator watches the work
        # graph every turn, independent of any skill turn-lock. Dispatch non-skill
        # runnable tasks via their registered runners first; a task that blocks on
        # external input owns the turn's egress. Inert until a consumer registers a
        # runner — SKILL tasks fall through to the skill path/loop below.
        drain_directive = await self._drain_runnable_tasks(visitor, observations)
        if drain_directive:
            await self._send_reply(visitor, drain_directive, compose=True)
            ended_via = "drain_reply"
            return

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
                    capabilities_section=capabilities_section,
                    parameters_section=parameters_section,
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
                if (
                    action == "tool"
                    and tool_name in skill_names
                    and tool_name not in tools
                    and active_skill_doc is not None
                    and tool_name == getattr(active_skill_doc, "name", None)
                ):
                    observations.append(
                        {
                            "tool": tool_name,
                            "args": args,
                            "observation": (
                                f"({tool_name} is the active locked skill, not a "
                                "callable tool. Follow the ACTIVE SKILL procedure "
                                "and use its listed tools, or reply/respond to the "
                                "user. Do not invoke the skill name as a tool.)"
                            ),
                        }
                    )
                    continue
                # Progress/reasoning line for the UI's REASONING disclosure. Fires
                # on both gears so single-step (light) turns still show their
                # reasoning, not just multi-step heavy ones. Skip when substantive
                # tool thoughts will surface the same tick in TOOL CALLS.
                if self.stream_internal_progress:
                    await self._emit_thought(
                        visitor,
                        self._progress_line(action, tool_name, args, decision),
                    )
                if action == "final":
                    if pending_chain and chain_deflections < 2:
                        # A tool result told the model to call ``pending_chain``
                        # next. Don't let it finalize (or claim completion) until
                        # that step has run.
                        chain_deflections += 1
                        observations.append(
                            {
                                "tool": "(guard)",
                                "args": {},
                                "observation": (
                                    f"(The task is not finished — call "
                                    f"{pending_chain} now to continue. Do NOT give a "
                                    "final answer or claim the process is "
                                    "complete until it has run.)"
                                ),
                            }
                        )
                        continue
                    if plan_deflections < 2:
                        open_steps = self._open_plan_step(visitor)
                        if open_steps:
                            # An active multi-step plan still has open steps —
                            # don't finalize mid-task. Nudge the model to run the
                            # next step (or close the plan if it's really done).
                            plan_deflections += 1
                            observations.append(
                                {
                                    "tool": "(guard)",
                                    "args": {},
                                    "observation": (
                                        "(Your active plan still has unfinished "
                                        "steps:\n"
                                        f"{open_steps}\n"
                                        "Do the next step now with a tool call — do "
                                        "NOT end the turn or claim completion. If "
                                        "the task is genuinely done, call "
                                        "update_plan to close it, then finalize.)"
                                    ),
                                }
                            )
                            continue
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
                    if (
                        pending_chain
                        and tool_name in ("reply", "respond")
                        and chain_deflections < 2
                    ):
                        # A chained step is pending — don't let the model reply
                        # (e.g. announce completion) before it runs.
                        chain_deflections += 1
                        observations.append(
                            {
                                "tool": "(guard)",
                                "args": {},
                                "observation": (
                                    f"(The task is not finished — call "
                                    f"{pending_chain} now, not reply/respond. Do NOT "
                                    "tell the user the process is complete until it has run.)"
                                ),
                            }
                        )
                        continue
                    if (
                        tool_name in ("reply", "respond")
                        and args.get("_coerced_from_text")
                        and plan_deflections < 2
                    ):
                        open_steps = self._open_plan_step(visitor)
                        if open_steps:
                            # Bare narration ("I'll do X next") got coerced to a
                            # reply, which would end the turn. An active plan still
                            # has open steps, so treat it as a thought and keep
                            # going rather than delivering a mid-task progress
                            # message as the final reply. (A deliberate reply
                            # carries no ``_coerced_from_text`` sentinel and is
                            # never blocked.)
                            plan_deflections += 1
                            observations.append(
                                {
                                    "tool": "(guard)",
                                    "args": {},
                                    "observation": (
                                        "(Don't just narrate — your active plan "
                                        "still has unfinished steps:\n"
                                        f"{open_steps}\n"
                                        "Perform the next step now with a tool call. "
                                        "Only call reply when you have something the "
                                        "user needs, or you're truly blocked.)"
                                    ),
                                }
                            )
                            continue
                    # Internal routing sentinel — never forward to a tool.
                    if isinstance(args, dict):
                        args.pop("_coerced_from_text", None)
                    # Companion gate: while a skill holds the turn-lock, use_skill
                    # may only (re)activate the locked skill itself or a declared
                    # companion. Switching to an unrelated skill would abandon the
                    # active task — block it and steer back.
                    if (
                        active_skill_doc is not None
                        and tool_name == "use_skill"
                        and (args or {}).get("name")
                        and (args or {}).get("name") != active_skill_doc.name
                        and (args or {}).get("name") not in locked_companion_skill_names
                    ):
                        allowed = (
                            ", ".join(sorted(locked_companion_skill_names)) or "none"
                        )
                        observations.append(
                            {
                                "tool": tool_name,
                                "args": args,
                                "observation": (
                                    f"({(args or {}).get('name')} cannot be started "
                                    f"while {active_skill_doc.name} is in progress. "
                                    f"Permitted companions: {allowed}. Finish or "
                                    "cancel the active task first, then switch.)"
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
                    if tool_name == "use_skill":
                        skill_name = ((args or {}).get("name") or "").strip()
                        prep_obs_before = len(observations)
                        locked_doc, tools, visible, new_section, detour_directive = (
                            await self._apply_task_lock_after_use_skill(
                                skill_name=skill_name,
                                activation_obs=obs if isinstance(obs, str) else "",
                                skill_docs=skill_docs,
                                loop_actions=loop_actions,
                                visitor=visitor,
                                utterance=utterance,
                                tools=tools,
                                visible=visible,
                                activated=activated,
                                observations=observations,
                            )
                        )
                        if locked_doc is not None:
                            active_skill_doc = locked_doc
                            if new_section:
                                skills_section = new_section
                            await self._emit_server_prep_tool_thoughts(
                                visitor, observations, since_index=prep_obs_before
                            )
                            # A prerequisite was pushed: deliver its first question
                            # as the turn's terminal reply so the model cannot
                            # fabricate the answer and skip the gate. The directive
                            # contract below reads a JSON tool-result, so frame it as
                            # one (no next_tool ⇒ it is treated as the terminal reply).
                            if detour_directive:
                                obs = json.dumps(
                                    {"response_directive": detour_directive}
                                )
                    # Companion detour: a companion capability (tool or skill) was
                    # used while a parent skill holds the turn-lock. Re-ground the
                    # parent in place so the model returns to it as soon as the side
                    # request is handled — same turn, not next.
                    if active_skill_doc is not None and (
                        tool_name in locked_companion_tools
                        or (
                            tool_name == "use_skill"
                            and ((args or {}).get("name") or "").strip()
                            in locked_companion_skill_names
                        )
                    ):
                        rg_before = len(observations)
                        await self._reground_parent_lock(
                            active_skill_doc, loop_actions, visitor, observations
                        )
                        if len(observations) > rg_before:
                            await self._emit_server_prep_tool_thoughts(
                                visitor, observations, since_index=rg_before
                            )
                    # Directive contract (see loop-state init): a tool result may
                    # carry the authoritative next step. A pending ``next_tool`` is a
                    # chain the model MUST take before it can finalize; a bare
                    # "Tell the user:" directive with no chain is the turn's reply,
                    # delivered directly so the model cannot re-decide (e.g. re-call
                    # the same tool). Generic — no tool is named in code.
                    if isinstance(obs, str):
                        nt, rd = self._result_next(obs)
                        if nt:
                            # The result chains to another tool the model MUST call.
                            pending_chain = nt
                            chain_deflections = 0
                        elif rd.strip().lower().startswith("tell the user:"):
                            # Drain (ADR-0026): before ending on a completion's
                            # terminal reply, re-resolve the task lock. If a task-lock
                            # skill just completed and a parent task is now the top
                            # runnable, resume it in THIS turn instead of ending — the
                            # resumed task produces the egress. Inert until prerequisites
                            # exist (nothing blocked ⇒ no parent to resume).
                            resumed = await self._maybe_resume_after_completion(
                                obs,
                                active_skill_doc,
                                skill_docs,
                                loop_actions,
                                visitor,
                                utterance,
                                tools,
                                visible,
                                activated,
                                observations,
                            )
                            if resumed is not None:
                                (
                                    active_skill_doc,
                                    tools,
                                    visible,
                                    skills_section,
                                    resume_terminal,
                                ) = resumed
                                if resume_terminal:
                                    # The resumed skill voices its own next question:
                                    # deliver it and end the turn (server-driven
                                    # resume — the model cannot fabricate the answer).
                                    await self._send_reply(
                                        visitor, resume_terminal, compose=True
                                    )
                                    ended_via = "resume_reply"
                                    return
                                continue
                            # Terminal reply directive with no chain — deliver it
                            # and end so the model cannot re-decide (e.g. re-run a
                            # tool it already ran). Compose (not literal relay): the
                            # directive may carry model-facing guidance that must be
                            # rendered into the agent's voice, not leaked verbatim.
                            await self._send_reply(visitor, rd, compose=True)
                            ended_via = "directive_reply"
                            return
                        elif pending_chain and tool_name == pending_chain:
                            # The pending chain just ran and produced no further
                            # chain — it's satisfied; let the model finalize.
                            pending_chain = None
                            chain_deflections = 0
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

            # Invariant 7 (ADR-0026): the loop ended, but the orchestrator must not
            # finalize idle while runnable work remains. Drain non-skill runnable
            # tasks now (some may have become runnable mid-turn — e.g. a completion
            # unblocked one); if one blocks on input it owns the egress. Inert until a
            # runner is registered, so skill-only turns are unaffected.
            interaction = getattr(visitor, "interaction", None)
            emitted = bool(getattr(interaction, "response", "") if interaction else "")
            if not emitted:
                drain_directive = await self._drain_runnable_tasks(
                    visitor, observations
                )
                if drain_directive:
                    await self._send_reply(visitor, drain_directive, compose=True)
                    ended_via = f"{ended_via}_drained"
                    return
                emitted = bool(
                    getattr(interaction, "response", "") if interaction else ""
                )

            # Budget/time ran out mid-task. Rather than dropping to the generic
            # clarify fallback (which discards the work and misreports the
            # cause), force ONE compose so the user gets the agent's best answer
            # from what it gathered. Only when there's actual work to summarize.
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
                    capabilities_section=capabilities_section,
                    parameters_section=parameters_section,
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
            rec_continuation_mode = (
                "locked"
                if active_skill_doc
                else ("model_mediated" if flow_owner else "none")
            )
            rec_flow_owner = active_skill_doc.name if active_skill_doc else flow_owner
            await self._record_orchestrator_activation(
                visitor,
                continuation_mode=rec_continuation_mode,
                flow_owner=rec_flow_owner,
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

    @staticmethod
    def _result_next(obs: str) -> Tuple[Optional[str], str]:
        """Parse ``(next_tool, response_directive)`` from a tool result.

        A tool result's JSON may carry the authoritative next step; the
        orchestrator honors it generically (chain enforcement + terminal-reply
        delivery). Returns ``(None, "")`` for non-JSON or malformed results.
        """
        try:
            data = json.loads(obs)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None, ""
        if not isinstance(data, dict):
            return None, ""
        nt = data.get("next_tool")
        rd = data.get("response_directive")
        return (
            nt if isinstance(nt, str) else None,
            rd if isinstance(rd, str) else "",
        )

    async def _send_reply(
        self, visitor: "InteractWalker", text: str = "", *, compose: bool = False
    ) -> None:
        """Producer egress (ADR-0025). The orchestrator is the AUTHOR: it queues
        the model's reply as an ``interaction.directive`` (attributed to itself),
        then the responder (ReplyAction) GATHERS the whole queue and sends ONE
        reply. ReplyAction never adds directives — producers queue them.

        With no ``text`` this just flushes whatever directives are already queued
        (e.g. a rails IA's). ``interaction.directives`` therefore reflects the
        turn's authored output, including model-authored/skill turns.

        ``compose=True`` forces an identity compose (``respond``) instead of the
        responder's N=1 literal relay fast path. Use it when ``text`` is an
        authored directive that still carries model-facing guidance (e.g. an
        interview engine directive: "Tell the user: <prompt> You may paraphrase …
        call <tool> …"). Relaying that literally would leak the guidance to the
        user; composing renders the user-facing intent in the agent's voice. The
        compose step replaces the per-turn reasoning the model used to do before
        it called reply itself.
        """
        interaction = getattr(visitor, "interaction", None)
        text = (text or "").strip()
        # Drop model-only composition guidance (everything after the U+2063 marker).
        # An orchestrator-authored reply is composed/relayed directly, not handed to
        # the model to relay, so the guidance ("You may paraphrase…", "Do not…",
        # tool-chain hints) is vestigial here — and a weak compose model would echo
        # it verbatim to the user. The user-facing text is always before the marker.
        if text:
            text = text.split("\u2063", 1)[0].strip()
        if interaction is not None and text:
            framed = (
                text
                if text.lower().startswith("tell the user")
                else f"Tell the user: {text}"
            )
            try:
                interaction.add_directive(framed, self.get_class_name())
            except Exception:
                pass
        responder = await self.get_responder()
        if compose and responder is not None:
            respond = getattr(responder, "respond", None)
            if callable(respond):
                try:
                    await respond(interaction, visitor=visitor)
                    return
                except Exception as exc:
                    logger.warning("orchestrator: responder.respond failed: %s", exc)
        gather = getattr(responder, "gather", None) if responder is not None else None
        if callable(gather):
            try:
                await gather(visitor)
                return
            except Exception as exc:
                logger.warning("orchestrator: responder.gather failed: %s", exc)
        # No responder/gather — best-effort raw publish so the turn isn't silent.
        if text:
            await self.publish(visitor=visitor, content=text)

    async def _emit_reply(self, visitor: "InteractWalker", text: str) -> None:
        """Emit user-facing ``text`` — routes through :meth:`_send_reply` so the
        reply is queued as an interaction.directive and gathered by the responder."""
        if not (text or "").strip():
            return
        await self._send_reply(visitor, text)

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

    def _open_plan_step(self, visitor: Any) -> Optional[str]:
        """Return a short description of the active plan's first unfinished step.

        Returns ``None`` when planning is off, there is no orchestrator-owned
        plan, or the plan has no pending steps. Used by the loop's completion
        guard to keep a multi-step turn going instead of ending on a premature
        ``final`` / narration-coerced reply.
        """
        if not self.planning:
            return None
        try:
            plan = active_plan(visitor, owner=self.get_class_name())
            if plan is None or not plan.has_pending_steps():
                return None
            checklist = plan.format_plan()
        except Exception:
            return None
        if not checklist or checklist == "(no steps)":
            return None
        return checklist

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
                # Bare text with no recognizable action → speak it. Tag it so the
                # loop can tell this narration-coerced reply from a deliberate
                # reply call (the plan-completion guard only deflects the former).
                tool_field, action = "reply", "tool"
                args = {**args, "_coerced_from_text": True}
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

    async def _emit_server_prep_tool_thoughts(
        self,
        visitor: "InteractWalker",
        observations: List[Dict[str, Any]],
        *,
        since_index: int = 0,
    ) -> None:
        """Surface server-injected skill prep in the TOOL CALLS panel."""
        for entry in observations[since_index:]:
            if entry.get("kind") != "server_prep":
                continue
            tool = str(entry.get("tool") or "(skill-prep)")
            seg = f"prep-{uuid.uuid4().hex[:10]}"
            await self._emit_tool_thought(
                visitor,
                "tool_call",
                tool,
                seg,
                args=entry.get("args") if isinstance(entry.get("args"), dict) else {},
            )
            await self._emit_tool_thought(
                visitor,
                "tool_result",
                tool,
                seg,
                obs=entry.get("observation") or "",
            )

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
        capabilities_section: str = "",
        parameters_section: str = "",
        loop_protocol_extra: str = "",
    ) -> str:
        """Build the base system prompt from the (overridable) ``system_prompt``
        template, then append ``system_prompt_extra`` if set."""
        base = self._fmt(
            self.system_prompt,
            ORCHESTRATOR_SYSTEM_PROMPT,
            identity_section=identity_section,
            tools_section=tools_section,
            skills_section=skills_section,
            capabilities_section=capabilities_section,
            parameters_section=parameters_section,
            loop_protocol_extra=loop_protocol_extra,
        )
        extra = (self.system_prompt_extra or "").strip()
        if extra:
            base = f"{base}\n\n{extra}"
        return base

    async def _routable_flow_tool_names(self) -> Set[str]:
        """Class names of routable IAs exposed as tools (flow continuation keys)."""
        agent = await self._safe_agent()
        names: Set[str] = set()
        for action in await self._enabled_interact_actions(agent):
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
            if triggers:
                get_name = getattr(action, "get_class_name", None)
                if callable(get_name):
                    names.add(get_name())
        return names

    async def _enabled_interact_actions(self, agent: Any) -> List[InteractAction]:
        return [
            action
            for action in await self._enabled_actions(agent)
            if isinstance(action, InteractAction)
        ]

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

    async def _collect_capabilities(self, skill_docs: List[Any]) -> List[str]:
        """The "WHAT YOU CAN DO" digest source: the agent's advertised abilities
        merged with the available skill descriptions.

        Aggregation across actions lives on ``Agent.collect_capabilities()``; here
        we only append skill descriptions on top. Sourced from the actions/skills
        themselves (not the lean-surfaced tool list), so the digest stays complete
        regardless of tool surfacing and the model never under-claims an ability.
        """
        agent = await self._safe_agent()
        caps: List[str] = []
        collector = getattr(agent, "collect_capabilities", None) if agent else None
        if callable(collector):
            try:
                collected = collector()
                if inspect.isawaitable(collected):
                    collected = await collected
                caps = [str(c).strip() for c in (collected or []) if str(c).strip()]
            except Exception as exc:
                # The digest is non-essential; never let it crash a turn.
                logger.debug("orchestrator: collect_capabilities failed: %s", exc)
        for d in skill_docs or []:
            desc = (getattr(d, "description", "") or "").strip()
            if desc:
                caps.append(desc)
        return caps

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
                    with_event=bool(self.include_history_events),
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
        capabilities_section: str = "",
        parameters_section: str = "",
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
        # Loop-protocol extras live INSIDE the loop-protocol section of the
        # system prompt (not trailing after the rules): planning, the tool-use
        # policy, and the upload-memory affordance are all about how to run the
        # loop. Each is gated; ordered planning → tool-use → memory.
        loop_extra: List[str] = []
        # Planning (ADR-0019): nudge update_plan for multi-step work; re-surface
        # an unfinished prior plan so the turn resumes. Off on the finalize tick.
        if self.planning and not finalize:
            loop_extra.append(self.planning_prompt)
            if plan_note:
                loop_extra.append(plan_note)
        # Tool-use policy: tool selection is the agent's job, not the user's to
        # dictate (gated by block_raw_tool_invocation).
        if self.block_raw_tool_invocation:
            loop_extra.append(self.tool_use_policy_prompt)
        # Memory-access protocol: search memory (the conversation in context +
        # saved artifacts) before answering from a blank or claiming you can't
        # recall. A standing protocol — not vision-gated; the artifact-tool part
        # is phrased conditionally so it's safe when those tools aren't surfaced.
        if self.memory_prompt:
            loop_extra.append(self.memory_prompt)
        loop_protocol_extra = ("\n\n" + "\n\n".join(loop_extra)) if loop_extra else ""

        system_prompt = self._compose_system_prompt(
            identity_section=await self._render_identity(),
            tools_section=render_tools_section(tools, lean=lean),
            skills_section=skills_section or self.no_skills_text,
            capabilities_section=capabilities_section,
            parameters_section=parameters_section,
            loop_protocol_extra=loop_protocol_extra,
        )
        if flow_note:
            note = self._fmt(
                self.flow_in_progress_prompt,
                FLOW_IN_PROGRESS_PROMPT,
                flow_note=flow_note,
            )
            system_prompt = f"{system_prompt}\n\n{note}"
        if self.max_statement_length and self.max_statement_length > 0:
            limit = self._fmt(
                self.length_limit_prompt,
                LENGTH_LIMIT_PROMPT,
                max_chars=int(self.max_statement_length),
            )
            system_prompt = f"{system_prompt}\n\n{limit}"
        if finalize:
            system_prompt = f"{system_prompt}\n\n{self.finalize_prompt}"
        # Conversation history travels as structured prior messages — the
        # model's designated history channel — NOT dumped as text into the user
        # turn. The user prompt carries only the current message + this turn's
        # steps. ``history_section`` is passed empty so an override template that
        # still references it renders cleanly. (``history_section`` is left blank
        # because enforce_json_mode keeps the decision structured even with real
        # assistant/user turns in context.)
        user_prompt = self._fmt(
            self.user_prompt,
            ORCHESTRATOR_USER_PROMPT_TEMPLATE,
            history_section="",
            utterance=utterance or "(no message)",
            observations_section=render_observations_section(observations),
        )
        # Peak-attention reinforcement: the OPERATING-RULES reminder rides in the
        # user turn (the slot the model weights most), so a weak model actually
        # obeys the safeguards when it writes a reply — the same technique that
        # got it to comply with directives in ReplyAction.
        user_prompt = f"{user_prompt}\n\n{SAFEGUARDS_REMINDER}"
        prior_messages = list(history or [])
        kwargs: Dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system_prompt},
                *prior_messages,
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "system": system_prompt,
            "history": prior_messages,
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
