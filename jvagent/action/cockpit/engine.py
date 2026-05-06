"""CockpitEngine: think-act-observe loop with single-step iteration.

Each call to ``step()`` executes exactly one model call. The calling action
controls iteration by checking the step result and re-adding itself to the
walker walk path when more steps are needed (walker-revisit pattern).
"""

import logging
import time
from typing import Any, Dict, List, Optional

from jvagent.action.cockpit.context import (
    CockpitContext,
    CockpitResult,
    CockpitState,
    CockpitStepResult,
)
from jvagent.action.cockpit.contracts import TerminationReason
from jvagent.tooling.tool_executor import ToolExecutionEngine
from jvagent.tooling.tool_registry import ToolRegistry
from jvagent.tooling.tool_serializer import ToolSerializer

logger = logging.getLogger(__name__)

COCKPIT_SYSTEM_PROMPT = """\
You are {agent_name}.
{agent_description}

You are an intelligent agent with access to a cockpit full of tools. Work in a think-act-observe loop:
analyze the request, choose the right tools, execute them carefully, then respond with grounded evidence.

# Tool-use cycle
- When you need to use tools, output ONLY tool calls — no accompanying text.
- Tool results are provided in your next turn. After receiving them, analyze the results and decide your next action.
- Continue calling tools until you have all the information needed.
- When ready to respond to the user, output your final text response with no tool calls.
- To end the turn early, call response_publish with finalize=true.
{task_planning}
# System
- Ground claims in evidence from tools and this conversation thread.
- Do not present unverifiable knowledge as established fact.
- All text you output outside of tool use is displayed to the user.
- Tool results and user messages may include system-reminder tags.

# Doing tasks
- Analyze the request and identify its distinct parts before acting.
- Use only the minimum necessary tools and adapt based on observed results.
- Read/observe before changing; keep actions tightly scoped.
- If an approach fails, diagnose the failure before switching tactics.
- Report outcomes faithfully.

# Tool-use discipline
- Provide clear, valid arguments for every tool call.
- If repeated calls produce the same outcome without progress, change strategy.
- If a tool fails, try a substantively different approach.
- Base claims on observed tool output. Cite concrete returned details.

# Response presentation
- Write your response as a natural, direct statement.
- Do not narrate your process: skip phrases like "I searched...", "I found...",
  "the tool returned...".
- Cite sources by title and URL for web, or document/article title for internal KB.{capability_search_note}{skill_index}
"""

CAPABILITY_SEARCH_NOTE = """

# Capability discovery
- When you need a capability you don't recognise in your tool list, call cockpit_search
  with a short, intent-focused query (e.g. 'send email', 'web search', 'read pdf').
- It returns ranked skills and tools. Pick the best match and proceed; for skills,
  call skill_read first to load the SOP."""


def _tool_call_signature(tc: Dict[str, Any]) -> str:
    """Stable signature of a tool call (name + arguments fingerprint).

    Used for stuck detection: identical (name, args) across iterations is
    genuine repetition; same name with different args is legitimate refinement.
    """
    import hashlib
    import json as _json

    fn = tc.get("function") if isinstance(tc, dict) else None
    name = (fn or {}).get("name", "unknown") if isinstance(fn, dict) else "unknown"
    raw_args = (fn or {}).get("arguments") if isinstance(fn, dict) else None
    if isinstance(raw_args, dict):
        try:
            normalized = _json.dumps(raw_args, sort_keys=True, default=str)
        except Exception:
            normalized = str(raw_args)
    elif isinstance(raw_args, str):
        normalized = raw_args
    else:
        normalized = ""
    fp = hashlib.blake2b(normalized.encode("utf-8"), digest_size=6).hexdigest()
    return f"{name}::{fp}"


TASK_PLANNING_BLOCK = """\

# Task planning
- For multi-step requests, create a plan first using task_create_plan with numbered steps.
- Before each step, call task_update_step with status "in_progress".
- After completing a step, call task_update_step with status "done" and a brief result.
- If a step fails, call task_update_step with status "failed" and explain why, then try an alternative.
- Use task_get_status to review progress if you lose track of where you are.
"""


class CockpitEngine:
    """The cockpit think-act-observe engine.

    Instantiates per run. Uses ``ToolExecutionEngine`` for dispatch and
    ``LanguageModelAction.query_messages()`` for model calls.

    Usage (walker-revisit pattern)::

        engine = CockpitEngine(ctx)
        await engine.initialize()
        # Then call step() on each walker visit until final_response.
    """

    def __init__(self, ctx: CockpitContext) -> None:
        self.ctx = ctx
        self._registry: Optional[ToolRegistry] = None
        self._tool_executor: Optional[ToolExecutionEngine] = None
        self._messages: List[Dict[str, Any]] = []
        self._tools_serialized: List[Dict[str, Any]] = []
        self._iteration: int = 0
        self._start: float = 0.0
        self._activated_skills: List[str] = []
        self._recent_tool_names: List[List[str]] = []
        # Per-iteration tool-call signatures (name + arguments fingerprint).
        # Used by _check_stuck to distinguish "calling same tool repeatedly with
        # different args" (legitimate refinement) from "same call over and over"
        # (genuine stuck pattern).
        self._recent_tool_signatures: List[List[str]] = []
        self._initialized: bool = False

    async def initialize(self) -> None:
        """Set up registry, system prompt, history, and initial user message.

        Call once at the start of a cockpit run, before the first ``step()``.
        """
        self._registry = await assemble_cockpit_tools(self.ctx)
        self._tool_executor = ToolExecutionEngine(
            self._registry,
            call_timeout=self.ctx.config.tool_call_timeout,
            max_concurrent=self.ctx.config.max_concurrent_tools,
            sanitize_errors=self.ctx.config.sanitize_tool_errors,
        )

        self._tools_serialized = ToolSerializer.serialize_all(self._registry.list())

        system_prompt = self._build_system_prompt()
        self._messages = []
        self._messages.append({"role": "system", "content": system_prompt})

        history = await self._build_history()
        self._messages.extend(history)

        self._messages.append({"role": "user", "content": self.ctx.utterance})

        self._start = time.monotonic()
        self._iteration = 0
        self._activated_skills = list(self.ctx.preloaded_skills)
        self._recent_tool_names = []
        self._initialized = True

    async def step(self) -> CockpitStepResult:
        """Execute one model call and return the result.

        Returns a ``CockpitStepResult`` indicating whether to continue
        (``tool_calls``) or deliver (``final_response`` / terminal states).
        """
        if not self._initialized:
            raise RuntimeError(
                "CockpitEngine.initialize() must be called before step()"
            )

        self._iteration += 1
        elapsed = time.monotonic() - self._start
        cfg = self.ctx.config

        # Budget checks
        if elapsed >= cfg.max_duration_seconds:
            return CockpitStepResult(
                status="timeout",
                final_response="I was unable to complete the task within the time limit.",
                termination_reason=TerminationReason.TIME_CAP,
                iterations=self._iteration,
                duration_seconds=elapsed,
                activated_skills=list(self._activated_skills),
            )

        if self._iteration > cfg.max_iterations:
            return CockpitStepResult(
                status="budget_exhausted",
                final_response=(
                    "I've reached the maximum number of steps for this task without "
                    "completing it. Please let me know if you'd like me to continue."
                ),
                termination_reason=TerminationReason.ITER_CAP,
                iterations=self._iteration,
                duration_seconds=elapsed,
                activated_skills=list(self._activated_skills),
            )

        result = await self.ctx.model_action.query_messages(
            self._messages,
            stream=False,
            tools=self._tools_serialized,
        )

        # Surface model thinking / extended-reasoning content as a reasoning
        # thought so users (or downstream observers) can see how the model is
        # reasoning between tool calls. Gated by stream_internal_progress.
        await self._emit_thinking_thought(result)

        if result.tool_calls:
            # Dispatch all tool calls first — side effects must execute
            # even if the finalized flag is set (e.g. task_update_step
            # alongside response_publish).
            for tc in result.tool_calls:
                fn = tc.get("function", {})
                tc_name = fn.get("name", "unknown")
                logger.debug(
                    "CockpitEngine [%d]: model called tool '%s'",
                    self._iteration,
                    tc_name,
                )

            tool_results = await self._tool_executor.dispatch(result.tool_calls)

            self._messages.append(
                {"role": "assistant", "content": None, "tool_calls": result.tool_calls}
            )
            for tr in tool_results:
                self._messages.append(tr.tool_result_message())

            # Track tool names + call signatures (name + args fingerprint) for stuck detection
            tool_names = [
                tc.get("function", {}).get("name", "unknown")
                for tc in result.tool_calls
            ]
            self._recent_tool_names.append(tool_names)
            self._recent_tool_signatures.append(
                [_tool_call_signature(tc) for tc in result.tool_calls]
            )

            # Check for finalized flag AFTER dispatching — response_publish
            # already published the content, but other tools in the batch
            # must still execute their side effects.
            visitor_state = getattr(self.ctx.visitor, "_skill_state", None) or {}
            if visitor_state.get("cockpit_finalized"):
                return CockpitStepResult(
                    status="final_response",
                    final_response="",  # Content already published via response_publish
                    termination_reason=TerminationReason.COMPLETED,
                    iterations=self._iteration,
                    duration_seconds=time.monotonic() - self._start,
                    activated_skills=list(self._activated_skills),
                )

            # All-errors short-circuit: if every tool call failed, don't loop
            if tool_results and all(
                getattr(tr, "is_error", False) for tr in tool_results
            ):
                error_details = "\n".join(
                    f"- {tc.get('function', {}).get('name', '?')}: {getattr(tr, 'content', '')[:200]}"
                    for tc, tr in zip(result.tool_calls, tool_results)
                )
                return CockpitStepResult(
                    status="final_response",
                    final_response=(
                        f"All tools returned errors:\n{error_details}\n\n"
                        "Please try a different approach."
                    ),
                    termination_reason=TerminationReason.ERROR,
                    iterations=self._iteration,
                    duration_seconds=time.monotonic() - self._start,
                    activated_skills=list(self._activated_skills),
                )

            # Stuck detection
            if self._check_stuck():
                return CockpitStepResult(
                    status="stuck",
                    final_response=(
                        "I seem to be making the same actions repeatedly without progress. "
                        "Let me try a different approach."
                    ),
                    termination_reason=TerminationReason.STUCK,
                    iterations=self._iteration,
                    duration_seconds=time.monotonic() - self._start,
                    activated_skills=list(self._activated_skills),
                )

            if cfg.stream_internal_progress and self.ctx.stream:
                await self._emit_tool_progress(
                    result.tool_calls, tool_results, self._iteration
                )

            return CockpitStepResult(
                status="tool_calls",
                iterations=self._iteration,
                duration_seconds=time.monotonic() - self._start,
                activated_skills=list(self._activated_skills),
            )

        # No tool calls — model produced a final text response
        response_text = await result.get_response()
        logger.debug(
            "CockpitEngine [%d]: model produced final response (%d chars)",
            self._iteration,
            len(response_text),
        )
        return CockpitStepResult(
            status="final_response",
            final_response=response_text,
            termination_reason=TerminationReason.COMPLETED,
            iterations=self._iteration,
            duration_seconds=time.monotonic() - self._start,
            activated_skills=list(self._activated_skills),
        )

    def save_state(self) -> CockpitState:
        """Capture engine state for observability/debugging.

        The engine instance is persisted across walker visits via
        ``visitor._skill_state["cockpit_engine"]``, so state restoration
        is handled by reusing the same engine rather than deserializing.
        """
        return CockpitState(
            messages=list(self._messages),
            iteration=self._iteration,
            activated_skills=list(self._activated_skills),
            started_at=self._start,
            tools_serialized=list(self._tools_serialized),
            recent_tool_names=[list(names) for names in self._recent_tool_names],
            recent_tool_signatures=[
                list(sigs) for sigs in self._recent_tool_signatures
            ],
        )

    def _check_stuck(self) -> bool:
        """Return True if recent tool calls show repetitive patterns.

        Two checks (both gated by ``stuck_min_iterations`` to avoid false
        positives during the early iterations of a legitimate multi-step plan):

        1. Jaccard similarity on tool-call **signature** sets (name + args
           fingerprint) across a sliding window. Calling the same tool with
           progressively different arguments is NOT stuck — it's refinement.
        2. Same primary tool **signature** repeated N consecutive times — i.e.
           identical name+args, not just same name.
        """
        cfg = self.ctx.config
        window_size = getattr(cfg, "stuck_detection_window", 4)
        threshold = getattr(cfg, "stuck_intent_jaccard_threshold", 0.65)
        repeat_limit = getattr(cfg, "stuck_primary_tool_repeat", 4)
        min_iters = getattr(cfg, "stuck_min_iterations", 4)

        # Don't engage stuck detection until the model has had time to work.
        if len(self._recent_tool_signatures) < min_iters:
            return False

        # Check A: Jaccard similarity on tool-call signature sets.
        if len(self._recent_tool_signatures) >= window_size:
            window = self._recent_tool_signatures[-window_size:]
            sets = [set(sigs) for sigs in window if sigs]
            if len(sets) >= 2:
                all_similar = True
                for i in range(len(sets) - 1):
                    intersection = len(sets[i] & sets[i + 1])
                    union = len(sets[i] | sets[i + 1])
                    if union == 0:
                        continue
                    jaccard = intersection / union
                    if jaccard < threshold:
                        all_similar = False
                        break
                if all_similar:
                    return True

        # Check B: Same primary tool signature repeated N consecutive times.
        if len(self._recent_tool_signatures) >= repeat_limit:
            recent = self._recent_tool_signatures[-repeat_limit:]
            primary_sigs = [sigs[0] if sigs else "" for sigs in recent]
            if len(set(primary_sigs)) == 1 and primary_sigs[0]:
                return True

        return False

    def _build_system_prompt(self) -> str:
        skill_index = ""
        task_planning = ""
        capability_search_note = ""

        skill_state = getattr(self.ctx.visitor, "_skill_state", None) or {}
        catalog = skill_state.get("skill_catalog")
        cfg = self.ctx.config

        large_catalog = bool(
            catalog and len(catalog.skills) > cfg.skill_index_inline_max_skills
        )

        if catalog and self.ctx.preloaded_skills:
            filtered = {
                k: v
                for k, v in catalog.skills.items()
                if k in self.ctx.preloaded_skills
            }
            if filtered:
                try:
                    from jvagent.action.cockpit.skill_catalog import SkillCatalog

                    sub = SkillCatalog(filtered)
                    skill_index = (
                        "\n\n# Available skills\n"
                        + sub.render_catalog()
                        + "\n\nUse skill_read with the exact skill name before activating any skill."
                    )
                except Exception:
                    pass
        elif catalog:
            n = len(catalog.skills)
            if n <= cfg.skill_index_inline_max_skills:
                try:
                    skill_index = (
                        "\n\n# Available skills\n"
                        + catalog.render_catalog()
                        + "\n\nUse skill_read with the exact skill name before activating any skill."
                    )
                except Exception:
                    pass
            elif cfg.enable_skill_helper_tools:
                skill_index = (
                    "\n\n"
                    + "You have access to multiple Claude-style skill bundles. "
                    + "Use skill_search with keywords from the user's request, "
                    + "then call skill_read to load the full instructions."
                )

        # Advertise cockpit_search prominently when the catalog is large or the
        # agent has many action tools — these are the cases where listing
        # everything inline isn't viable.
        if cfg.enable_cockpit_search and large_catalog:
            capability_search_note = CAPABILITY_SEARCH_NOTE

        if cfg.plan_first:
            task_planning = TASK_PLANNING_BLOCK

        return COCKPIT_SYSTEM_PROMPT.format(
            agent_name=self.ctx.agent_name,
            agent_description=self.ctx.agent_description,
            skill_index=skill_index,
            task_planning=task_planning,
            capability_search_note=capability_search_note,
        )

    async def _build_history(self) -> List[Dict[str, Any]]:
        if not self.ctx.conversation or self.ctx.config.history_limit <= 0:
            return []

        try:
            raw = await self.ctx.conversation.get_interaction_history(
                limit=self.ctx.config.history_limit,
                excluded=self.ctx.interaction.id if self.ctx.interaction else None,
                with_utterance=True,
                with_response=True,
                formatted=False,
            )
            messages: List[Dict[str, Any]] = []
            for entry in raw or []:
                role = entry.get("role", "")
                content = entry.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                if content:
                    messages.append({"role": role, "content": content})
            return messages
        except Exception:
            return []

    async def _emit_thinking_thought(self, result: Any) -> None:
        """Publish the model's thinking/reasoning text as a transient thought.

        Honors ``stream_internal_progress``. Many providers (Anthropic
        extended thinking, OpenAI reasoning models, Ollama thinking-capable
        models) populate ``result.thinking_content`` after the call completes;
        this surfaces that text on the response bus so users can see how the
        model reasoned between tool calls.
        """
        ctx = self.ctx
        cfg = ctx.config
        if not getattr(cfg, "stream_internal_progress", True):
            return
        thinking = getattr(result, "thinking_content", None)
        if not thinking or not isinstance(thinking, str) or not thinking.strip():
            return
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return
        await ctx.response_bus.publish(
            session_id=ctx.session_id,
            content=thinking.strip(),
            channel=ctx.channel,
            stream=ctx.stream,
            interaction_id=ctx.interaction.id,
            interaction=ctx.interaction,
            user_id=ctx.user_id,
            streaming_complete=True,
            transient=True,
            category="thought",
            thought_type="reasoning",
        )

    async def _emit_tool_progress(
        self,
        tool_calls: List[Dict[str, Any]],
        results: List[Any],
        iteration: int,
    ) -> None:
        ctx = self.ctx
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return
        for tc, tr in zip(tool_calls, results):
            fn = tc.get("function", {})
            status = (
                "failed"
                if (
                    getattr(tr, "is_error", False)
                    or (
                        isinstance(tr, dict)
                        and tr.get("content", "").startswith("Error:")
                    )
                )
                else "ok"
            )
            content = f"[{status}] {fn.get('name','')}"
            await ctx.response_bus.publish(
                session_id=ctx.session_id,
                content=content,
                channel=ctx.channel,
                stream=ctx.stream,
                interaction_id=ctx.interaction.id,
                interaction=ctx.interaction,
                user_id=ctx.user_id,
                streaming_complete=True,
                transient=True,
                category="thought",
                thought_type="tool_progress",
            )


# Late import to avoid circular dependency
from jvagent.action.cockpit.registry import assemble_cockpit_tools
