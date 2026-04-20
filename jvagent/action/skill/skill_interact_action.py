"""SkillInteractAction: agentic loop implementing think-act-observe.

An InteractAction that runs the think-act-observe loop, enabling long-running
agents that intelligently execute skills, track tasks, and run tools/MCPs
as configured. The LLM decides which tools to call, ToolExecutor dispatches
them, and results feed back into the conversation for the next iteration.
"""

import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.skill.action_resolver import ActionResolver
from jvagent.action.skill.loop_context import LoopContext, LoopContextConfig
from jvagent.action.skill.prompts import (
    FINAL_REVIEW_PROMPT,
    FORCED_TERMINATION_PROMPT,
    GROUNDING_INSTRUCTION_TEMPLATE,
    LIST_SKILLS_TOOL_DESCRIPTION,
    READ_SKILL_RESULT_TEMPLATE,
    SKILL_AGENT_SYSTEM_PROMPT,
    SKILL_FIRST_RETRY_PROMPT,
    SKILL_SEARCH_TOOL_DESCRIPTION,
    TOOL_CALL_ANNOUNCE_TEMPLATE,
)
from jvagent.action.skill.skill_catalog import SkillCatalog
from jvagent.action.skill.stuck_detector import StuckDetector, StuckDetectorConfig
from jvagent.action.skill.tool_executor import ToolExecutor

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.memory.services.task_service import TaskHandle

logger = logging.getLogger(__name__)

# How many full tool results to keep before summarizing older ones
_DEFAULT_MAX_FULL_TOOL_RESULTS = 10


class LoopState(str, Enum):
    MODEL = "MODEL"
    TOOLS = "TOOLS"
    TERMINATE = "TERMINATE"


class TerminationReason(str, Enum):
    COMPLETED = "completed"
    ITER_CAP = "max_iterations"
    TIME_CAP = "timed_out"
    ERROR = "failed"


class SkillInteractAction(InteractAction):
    """InteractAction implementing a think-act-observe agentic loop.

    When activated by the InteractRouter, this action:
    1. Resolves skill bundles from built-in/app-local catalogs
    2. Initializes a ToolExecutor with tools from configured MCP servers
    3. Runs the agentic loop: LLM thinks → calls tools → observes results → repeats
    4. Tracks multi-step progress via TaskService on Conversation.active_tasks
    5. Streams intermediate progress as transient adhoc messages
    6. Publishes the final response when the loop completes

    Attributes:
        max_iterations: Hard cap on think-act-observe cycles.
        max_duration_seconds: Wall-clock timeout for the agentic loop.
        thinking_budget_tokens: Anthropic extended thinking budget (0 = disabled).
        model_action_type: LanguageModelAction entity type.
        model: Model identifier.
        model_temperature: Temperature for LLM generation.
        model_max_tokens: Max tokens for LLM generation.
        tool_servers: Names of MCPAction instances providing tools.
        allow_local_tools: Whether ToolExecutor can register local Python tools.
        stream_thinking: Stream extended thinking content as adhoc.
        stream_tool_progress: Stream tool call status as adhoc.
        max_full_tool_results: Keep last N tool results in full; summarize older.
        local_tools_path: Optional absolute path to a directory containing tool modules.
    """

    weight: int = attribute(
        default=-60,
        description="Execution weight (after InteractRouter, before Persona)",
    )
    description: str = attribute(
        default="Long-running agentic loop for multi-step tasks with tool use.",
        description="Action description",
    )
    max_iterations: int = attribute(
        default=25,
        description="Hard cap on think-act-observe cycles",
    )
    max_duration_seconds: float = attribute(
        default=300.0,
        description="Wall-clock timeout for the agentic loop (seconds)",
    )
    thinking_budget_tokens: int = attribute(
        default=0,
        description="Anthropic extended thinking budget (0 = disabled)",
    )
    model_action_type: str = attribute(
        default="AnthropicLanguageModelAction",
        description="LanguageModelAction entity type",
    )
    model: str = attribute(
        default="claude-sonnet-4-20250514",
        description="Model identifier",
    )
    model_temperature: float = attribute(
        default=0.3,
        description="Temperature for LLM generation",
    )
    model_max_tokens: int = attribute(
        default=8192,
        description="Max tokens for LLM generation",
    )
    skills: Any = attribute(
        default=None,
        description="Skill selector: '-all' | list of names/globs | None (no skill bundles exposed)",
    )
    denied_skills: List[str] = attribute(
        default_factory=list,
        description="Names/globs to exclude from the resolved skill bundle set",
    )
    skills_source: str = attribute(
        default="both",
        description="Skill bundle source: 'builtin' | 'app' | 'both' | 'none'",
    )
    tool_servers: List[str] = attribute(
        default_factory=list,
        description="Names of MCPAction instances providing tools",
    )
    allow_local_tools: bool = attribute(
        default=False,
        description="Whether ToolExecutor can register local Python tools",
    )
    stream_thinking: bool = attribute(
        default=True,
        description="Stream extended thinking content as adhoc",
    )
    stream_tool_progress: bool = attribute(
        default=True,
        description="Stream tool call status as adhoc",
    )
    commit_intermediate_messages: bool = attribute(
        default=True,
        description=(
            "If True, any text the model emits alongside tool calls (mid-loop "
            "user-facing commentary) is published as a user-category message and "
            "appended to interaction.response so the conversation history reflects "
            "what the assistant said to the user."
        ),
    )
    relay_thoughts_to_channels: bool = attribute(
        default=False,
        description="If True, thought messages may be relayed to channel adapters that opt in.",
    )
    max_full_tool_results: int = attribute(
        default=_DEFAULT_MAX_FULL_TOOL_RESULTS,
        description="Keep last N tool results in full; summarize older",
    )
    max_tool_result_tokens: int = attribute(
        default=400,
        description="Max estimated tokens retained for an individual tool result message",
    )
    tool_result_truncation_chars: int = attribute(
        default=500,
        description="Max characters streamed for individual tool-result thought updates",
    )
    history_limit: int = attribute(
        default=5,
        description="How many prior interactions to include in initial context",
    )
    call_timeout_seconds: float = attribute(
        default=60.0,
        description="Timeout in seconds for each tool call",
    )
    response_mode: str = attribute(
        default="publish",
        description=(
            "How to deliver the final response: 'publish' (direct bus delivery, default) "
            "or 'respond' (route through PersonaAction for persona-enriched responses "
            "with parameters, directives, and persona attributes)"
        ),
    )
    task_sync_every_steps: int = attribute(
        default=3,
        description="How many tracker steps to buffer before persisting metadata",
    )
    local_tools_path: Optional[str] = attribute(
        default=None,
        description="Optional absolute path to a folder containing local Python tools (.py files)",
    )
    strict_grounding: bool = attribute(
        default=True,
        description="If True, enforce grounding-focused prompting and skill scope constraints",
    )
    plan_first: bool = attribute(
        default=True,
        description="If True, instruct model to provide a brief plan before non-trivial tool use",
    )
    enable_skill_helper_tools: bool = attribute(
        default=True,
        description="If True, register list_skills and skill_search helper tools",
    )
    max_skill_activations: int = attribute(
        default=5,
        description="Maximum number of skill activations allowed within one loop",
    )
    stuck_detection_window: int = attribute(
        default=3,
        description="Number of identical consecutive tool-call signatures before stuck warning",
    )
    max_midcourse_corrections: int = attribute(
        default=2,
        description="Maximum stuck-detection reminders before forced termination",
    )
    final_review: bool = attribute(
        default=False,
        description="If True, run a final grounding review pass before publishing response",
    )
    prioritize_skills_first: bool = attribute(
        default=True,
        description=(
            "If True, enforce one skill-first retry before accepting a no-tool final answer "
            "when skills are available"
        ),
    )
    skill_first_retry_limit: int = attribute(
        default=1,
        description="Maximum number of skill-first retry nudges in a loop",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Entry point: load skill, build tool registry, run the agentic loop.

        Args:
            visitor: The InteractWalker visiting this action.
        """
        if not self._ensure_interaction(visitor):
            logger.warning("SkillInteractAction: No interaction available")
            await visitor.unrecord_action_execution()
            return

        interaction = visitor.interaction
        conversation = visitor.conversation
        if not conversation:
            logger.warning("SkillInteractAction: No conversation available")
            await visitor.unrecord_action_execution()
            return

        tool_executor: Optional[ToolExecutor] = None
        try:
            # 0. Create ActionResolver and attach to visitor for skill tool access
            agent = getattr(visitor, "_agent", None)
            visitor.action_resolver = ActionResolver(agent) if agent else None

            # 1. Discover skill bundles via SkillCatalog
            skill_catalog = await SkillCatalog.discover(
                visitor=visitor,
                skills_selector=self.skills,
                skills_source=self.skills_source,
                denied_skills=getattr(self, "denied_skills", None),
            )
            discovered_skills = skill_catalog.skills

            local_paths: List[str] = []
            if self.local_tools_path:
                local_paths.append(self.local_tools_path)

            # 2. Initialize ToolExecutor
            tool_executor = ToolExecutor(
                call_timeout=self.call_timeout_seconds,
                sanitize_errors=True,
            )
            await tool_executor.initialize(
                visitor=visitor,
                tool_servers=self.tool_servers,
                local_tools_paths=local_paths,
            )

            # 4. Register skill bundles and inject read_skill tool
            if not skill_catalog.is_empty:
                for skill_name, skill_data in discovered_skills.items():
                    tool_executor.register_skill_bundle(
                        skill_name=skill_name,
                        dir_path=skill_data["dir"],
                        tool_files=skill_data.get("tool_files", []),
                        allowed_tools=skill_data.get("allowed_tools", []),
                    )

                async def read_skill_handler(args):
                    skill_name = args.get("skill_name")
                    if skill_name not in discovered_skills:
                        return f"Error: Skill '{skill_name}' not found."

                    skill_data = discovered_skills[skill_name]

                    # Check activation limit
                    limit_error = skill_catalog.check_activation_limit(
                        skill_name=skill_name,
                        activated_skills=tool_executor.activated_skills,
                        max_activations=self.max_skill_activations,
                    )
                    if limit_error:
                        return limit_error

                    # Validate required actions are available
                    req_error = await skill_catalog.validate_requirements(
                        skill_name=skill_name,
                        action_resolver=visitor.action_resolver,
                    )
                    if req_error:
                        return req_error

                    registered_tools = await tool_executor.activate_skill(skill_name)
                    scope_hint = str(
                        skill_data.get("scope_hint")
                        or skill_data.get("description")
                        or "the workflow described in this skill"
                    )
                    result_text = READ_SKILL_RESULT_TEMPLATE.format(
                        skill_name=skill_name,
                        tools=(
                            ", ".join(registered_tools)
                            if registered_tools
                            else "(none)"
                        ),
                        content=skill_data["content"],
                    )
                    if self.strict_grounding:
                        result_text += "\n\n" + GROUNDING_INSTRUCTION_TEMPLATE.format(
                            skill_name=skill_name,
                            scope_hint=scope_hint,
                        )
                    return result_text

                tool_executor.register_dynamic_tool(
                    name="read_skill",
                    tool_def_dict={
                        "name": "read_skill",
                        "description": "Read the full instructions/SOP for a specific capability/skill.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "skill_name": {
                                    "type": "string",
                                    "description": "The name of the skill to read.",
                                }
                            },
                            "required": ["skill_name"],
                        },
                    },
                    handler=read_skill_handler,
                )
                if self.enable_skill_helper_tools:
                    tool_executor.register_dynamic_tool(
                        name="list_skills",
                        tool_def_dict={
                            "name": "list_skills",
                            "description": LIST_SKILLS_TOOL_DESCRIPTION,
                            "parameters": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                        handler=lambda args: skill_catalog.render_catalog(),
                    )

                    async def skill_search_handler(args):
                        query = str(args.get("query", "")).strip()
                        top_k_raw = args.get("top_k", 5)
                        try:
                            top_k = max(1, int(top_k_raw))
                        except (TypeError, ValueError):
                            top_k = 5
                        return SkillCatalog(discovered_skills).search(
                            query, top_k=top_k
                        )

                    tool_executor.register_dynamic_tool(
                        name="skill_search",
                        tool_def_dict={
                            "name": "skill_search",
                            "description": SKILL_SEARCH_TOOL_DESCRIPTION,
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "Search phrase for skill intent.",
                                    },
                                    "top_k": {
                                        "type": "integer",
                                        "description": "Maximum number of skills to return.",
                                        "default": 5,
                                    },
                                },
                                "required": ["query"],
                            },
                        },
                        handler=skill_search_handler,
                    )

            if not tool_executor.get_tool_names():
                logger.warning(
                    "SkillInteractAction: No tools available, "
                    "proceeding without tools (reasoning-only mode)"
                )

            task_description = (
                f"Agentic task: {interaction.utterance[:100]}"
                if interaction.utterance
                else "Agentic task"
            )
            async with visitor.tasks.track(
                description=task_description,
                task_type="AGENTIC_LOOP",
                action_name=self.get_class_name(),
                metadata={
                    "skills": self.skills,
                    "skills_source": self.skills_source,
                    "strict_grounding": self.strict_grounding,
                    "plan_first": self.plan_first,
                    "final_review": self.final_review,
                    "max_skill_activations": self.max_skill_activations,
                    "stuck_detection_window": self.stuck_detection_window,
                    "max_midcourse_corrections": self.max_midcourse_corrections,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "iterations": 0,
                    "tools_called": [],
                    "thinking_tokens_used": 0,
                    "steps": [],
                    "completed_at": None,
                    "total_duration_seconds": None,
                },
            ) as task:
                # Inject persona context into the loop
                persona_action = None
                agent = getattr(visitor, "_agent", None)
                if agent:
                    actions_manager = await agent.get_actions_manager()
                    if actions_manager:
                        enabled_actions = await actions_manager.get_actions(
                            enabled_only=True
                        )
                        for action in enabled_actions:
                            if action.get_class_name() == "PersonaAction":
                                persona_action = action
                                break

                # 5. Run the agentic loop
                final_response, termination_reason, stuck_corrections = (
                    await self._run_agentic_loop(
                        visitor=visitor,
                        tool_executor=tool_executor,
                        task_handle=task,
                        discovered_skills=discovered_skills,
                        persona_action=persona_action,
                    )
                )
                await task.update_metadata(
                    activated_skills=sorted(tool_executor.activated_skills),
                    stuck_corrections=stuck_corrections,
                )

                # 6. Deliver final response
                if final_response:
                    effective_mode = self._resolve_response_mode(
                        discovered_skills, tool_executor
                    )
                    if effective_mode == "respond":
                        # Route through PersonaAction for persona-enriched response
                        # We pass the final_response as a specialized directive that
                        # explicitly asks the persona to present this verified data.
                        await visitor.add_directive(
                            f"The agentic loop has completed the task with the following verified result:\n\n{final_response}\n\nPresent this result naturally in your persona. Do not strip technical details or evidence, but refine the delivery to be human-like."
                        )
                        await self.respond(visitor)
                    else:
                        # Direct bus delivery
                        await self.publish(
                            visitor,
                            content=final_response,
                            streaming_complete=True,
                        )
                    # Mark directives as executed
                    interaction.set_to_executed()

                # 7. Complete task explicitly with final reason/summary.
                await task.complete(
                    status=termination_reason,
                    summary=final_response[:200] if final_response else None,
                )

        except Exception as e:
            logger.error(
                "SkillInteractAction: Error during agentic loop: %s",
                e,
                exc_info=True,
            )
            await visitor.unrecord_action_execution()
        finally:
            if tool_executor:
                try:
                    await tool_executor.cleanup()
                except Exception as cleanup_error:
                    logger.warning(
                        "SkillInteractAction: tool cleanup failed: %s",
                        cleanup_error,
                    )

    def _resolve_response_mode(
        self,
        discovered_skills: Optional[Dict[str, Dict[str, Any]]] = None,
        tool_executor: Optional[ToolExecutor] = None,
    ) -> str:
        """Resolve the effective response mode for the final response.

        If any activated skill has ``response-mode: respond`` in its frontmatter,
        use ``respond``. Otherwise, fall back to the action's ``response_mode``
        attribute (default: ``publish``).
        """
        if discovered_skills and tool_executor:
            for skill_name in tool_executor.activated_skills:
                skill_data = discovered_skills.get(skill_name, {})
                if skill_data.get("response_mode") == "respond":
                    return "respond"
        return self.response_mode

    def _should_retry_for_skill_first(
        self,
        discovered_skills: Optional[Dict[str, Dict[str, Any]]],
        tool_executor: ToolExecutor,
        utterance: str,
        retries: int,
    ) -> bool:
        """Check if the loop should nudge the model toward skill activation.

        Simplified: relies on model intelligence rather than keyword matching.
        Only nudges when skills are available but none activated and the
        retry limit hasn't been exceeded.
        """
        if not self.prioritize_skills_first:
            return False
        if not discovered_skills:
            return False
        if not tool_executor or tool_executor.activated_skills:
            return False
        if retries >= self.skill_first_retry_limit:
            return False
        return True

    async def _run_agentic_loop(
        self,
        visitor: "InteractWalker",
        tool_executor: ToolExecutor,
        task_handle: "TaskHandle",
        discovered_skills: Optional[Dict[str, Dict[str, Any]]] = None,
        persona_action: Optional[Any] = None,
    ) -> tuple[str, str, int]:
        """Core agentic loop: think → act → observe → repeat."""
        model_kwargs = self._build_model_kwargs()

        # Build initial messages using LoopContext
        loop_ctx = LoopContext(
            LoopContextConfig(
                max_full_tool_results=self.max_full_tool_results,
                max_tool_result_tokens=self.max_tool_result_tokens,
                tool_result_truncation_chars=self.tool_result_truncation_chars,
                history_limit=self.history_limit,
            )
        )

        # Integrate persona into system prompt
        agent_name = "Agent"
        agent_description = "An intelligent skills-based agent."
        if persona_action:
            agent_name = getattr(persona_action, "persona_name", "Agent")
            agent_description = getattr(
                persona_action,
                "persona_description",
                "An intelligent skills-based agent.",
            )

        system_prompt = SKILL_AGENT_SYSTEM_PROMPT.format(
            agent_name=agent_name,
            agent_description=agent_description,
        )

        if not self.plan_first:
            system_prompt += "\n\nOverride: Skip plan-first behavior unless the user explicitly asks for a plan."
        if not self.strict_grounding:
            system_prompt += "\n\nOverride: You may answer with best-effort general reasoning when tool evidence is unavailable."
        skill_index_section = None
        if discovered_skills:
            skill_index_section = SkillCatalog(
                discovered_skills
            ).render_system_prompt_section()

        messages = await loop_ctx.build_initial_messages(
            system_prompt=system_prompt,
            utterance=visitor.utterance,
            conversation=visitor.conversation,
            interaction=visitor.interaction,
            skill_index_section=skill_index_section,
        )

        loop_start = time.monotonic()
        iteration = 0
        final_response = ""
        termination_reason = TerminationReason.COMPLETED.value
        loop_state = LoopState.MODEL
        stuck_detector = StuckDetector(
            StuckDetectorConfig(
                window_size=max(1, int(self.stuck_detection_window or 1)),
                max_corrections=self.max_midcourse_corrections,
            )
        )
        skill_first_retries = 0

        while iteration < self.max_iterations:
            # Check duration limit
            elapsed = time.monotonic() - loop_start
            if elapsed >= self.max_duration_seconds:
                loop_state = LoopState.TERMINATE
                final_response = await self._force_termination(
                    messages,
                    tool_executor.get_tools_list(),
                    visitor,
                    model_kwargs,
                )
                termination_reason = TerminationReason.TIME_CAP.value
                break

            iteration += 1
            loop_state = LoopState.MODEL

            # Re-fetch tools each iteration to include newly activated skill tools.
            tools = tool_executor.get_tools_list()
            model_result = await self._call_model(
                messages, tools, visitor, model_kwargs
            )

            await task_handle.record_step(
                "thinking",
                iteration=iteration,
                details={"tokens": model_result.thinking_tokens or 0},
            )

            if model_result.thinking_content and self.stream_thinking:
                await self.publish_thought(
                    visitor=visitor,
                    content=model_result.thinking_content,
                    thought_type="reasoning",
                    segment_id=f"iter-{iteration}-reasoning",
                    streaming_complete=True,
                    relay_to_adapters=self.relay_thoughts_to_channels,
                    metadata={
                        "action_name": self.get_class_name(),
                        "tokens": model_result.thinking_tokens or 0,
                    },
                )
            if not model_result.tool_calls:
                candidate_response = await model_result.get_response()
                if not candidate_response and model_result.response:
                    candidate_response = model_result.response

                if self._should_retry_for_skill_first(
                    discovered_skills=discovered_skills,
                    tool_executor=tool_executor,
                    utterance=visitor.utterance,
                    retries=skill_first_retries,
                ):
                    messages.append(
                        {
                            "role": "assistant",
                            "content": candidate_response or "",
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": SKILL_FIRST_RETRY_PROMPT,
                        }
                    )
                    skill_first_retries += 1
                    continue

                final_response = candidate_response
                termination_reason = TerminationReason.COMPLETED.value
                loop_state = LoopState.TERMINATE
                break

            tool_calls = model_result.tool_calls
            stuck_result = stuck_detector.record(tool_calls)
            loop_state = LoopState.TOOLS
            tool_names = [
                tc.get("function", {}).get("name", "unknown") for tc in tool_calls
            ]
            tool_summaries = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                args_str = fn.get("arguments", "")
                if len(args_str) > 120:
                    args_str = args_str[:117] + "..."
                tool_summaries.append(
                    {"name": fn.get("name", "unknown"), "arguments": args_str}
                )
            await task_handle.record_step(
                "tool_call",
                iteration=iteration,
                details={
                    "count": len(tool_calls),
                    "tools": tool_names,
                    "tool_summaries": tool_summaries,
                },
            )

            # Surface any mid-loop user-facing commentary the model emitted alongside
            # tool calls. The model already records this text in its internal message
            # list, but without explicit publication it is invisible to the end user
            # and absent from interaction.response (and therefore from conversation
            # history on subsequent turns), causing inconsistencies.
            intermediate_text = (model_result.response or "").strip()
            if self.commit_intermediate_messages and intermediate_text:
                await self.publish(
                    visitor=visitor,
                    content=intermediate_text,
                    streaming_complete=True,
                    metadata={
                        "action_name": self.get_class_name(),
                        "iteration": iteration,
                        "intermediate": True,
                    },
                )

            if self.stream_tool_progress:
                for idx, tc in enumerate(tool_calls):
                    tool_name = tc.get("function", {}).get("name", "unknown")
                    # Use the model's own intermediate response if available to provide a human-like announcement
                    announcement = (
                        intermediate_text
                        if intermediate_text
                        else TOOL_CALL_ANNOUNCE_TEMPLATE.format(tool_name=tool_name)
                    )
                    await self.publish_thought(
                        visitor=visitor,
                        content=announcement,
                        thought_type="tool_call",
                        segment_id=f"iter-{iteration}-call-{tool_name}-{idx}",
                        streaming_complete=True,
                        relay_to_adapters=self.relay_thoughts_to_channels,
                        metadata={
                            "action_name": self.get_class_name(),
                            "tool_name": tool_name,
                        },
                    )

            assistant_msg = LoopContext.build_assistant_content(model_result)
            messages.append(assistant_msg)

            tool_start = time.monotonic()
            tool_result_messages = await tool_executor.dispatch(tool_calls, visitor)
            tool_duration_ms = int((time.monotonic() - tool_start) * 1000)

            if self.stream_tool_progress:
                for tr_msg in tool_result_messages:
                    content = tr_msg.get("content", "")
                    tool_call_id = tr_msg.get("tool_call_id", "")
                    await self.publish_thought(
                        visitor=visitor,
                        content=content[: self.tool_result_truncation_chars],
                        thought_type="tool_result",
                        segment_id=f"iter-{iteration}-result-{tool_call_id or 'unknown'}",
                        streaming_complete=True,
                        relay_to_adapters=self.relay_thoughts_to_channels,
                        metadata={
                            "action_name": self.get_class_name(),
                            "tool_call_id": tool_call_id,
                        },
                    )

            messages.extend(tool_result_messages)
            result_statuses = []
            for tr_msg in tool_result_messages:
                content = tr_msg.get("content", "")
                is_error = tr_msg.get("is_error", False)
                result_statuses.append(
                    {
                        "tool_call_id": tr_msg.get("tool_call_id", ""),
                        "is_error": is_error,
                        "content_preview": content[:200] if content else "",
                    }
                )
            await task_handle.record_step(
                "tool_result",
                iteration=iteration,
                details={
                    "duration_ms": tool_duration_ms,
                    "count": len(tool_result_messages),
                    "results": result_statuses,
                },
            )
            if stuck_result:
                if stuck_result == "FORCE_TERMINATE":
                    loop_state = LoopState.TERMINATE
                    final_response = await self._force_termination(
                        messages,
                        tool_executor.get_tools_list(),
                        visitor,
                        model_kwargs,
                    )
                    termination_reason = TerminationReason.ITER_CAP.value
                    break
                else:
                    messages.append({"role": "user", "content": stuck_result})
            messages = loop_ctx.maybe_truncate(messages)

        if (
            not final_response
            and termination_reason == TerminationReason.COMPLETED.value
            and iteration >= self.max_iterations
        ):
            loop_state = LoopState.TERMINATE
            final_response = await self._force_termination(
                messages,
                tool_executor.get_tools_list(),
                visitor,
                model_kwargs,
            )
            termination_reason = TerminationReason.ITER_CAP.value

        if not final_response:
            final_response = (
                "I was unable to complete the task within the allowed steps."
            )
            if termination_reason == TerminationReason.COMPLETED.value:
                termination_reason = TerminationReason.ITER_CAP.value
        elif self.final_review:
            final_response = await self._final_review_pass(
                messages=messages,
                candidate_response=final_response,
                visitor=visitor,
                model_kwargs=model_kwargs,
            )

        await task_handle.record_step(
            "response",
            iteration=iteration,
            details={
                "length": len(final_response),
                "loop_state": loop_state.value,
                "termination_reason": termination_reason,
                "preview": final_response[:300],
            },
        )
        return final_response, termination_reason, stuck_detector.corrections

    def _build_model_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "temperature": self.model_temperature,
            "max_tokens": self.model_max_tokens,
        }
        if self.thinking_budget_tokens > 0:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget_tokens,
            }
            if kwargs.get("max_tokens", 0) < self.thinking_budget_tokens + 1:
                kwargs["max_tokens"] = self.thinking_budget_tokens + 1
        return kwargs

    async def _call_model(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        visitor: "InteractWalker",
        model_kwargs: Dict[str, Any],
    ) -> Any:
        """Call LanguageModelAction.query_messages() with pre-formatted messages.

        The agentic loop maintains its own message list with tool-call/result
        pairs, so it uses query_messages() to preserve this full context while
        still getting standard observability/profiling tracking.

        Args:
            messages: Current conversation messages (pre-formatted).
            tools: Available tool definitions.
            visitor: The InteractWalker.
            model_kwargs: Model-specific keyword arguments.

        Returns:
            ModelActionResult from the LLM.
        """
        model_action = await self.get_model_action(required=True)
        provider = getattr(model_action, "provider", "") or ""
        final_messages = (
            LoopContext.convert_for_provider(messages, provider)
            if provider == "anthropic"
            else messages
        )

        return await model_action.query_messages(
            messages=final_messages,
            stream=False,
            system=final_messages[0].get("content") if final_messages else None,
            history=final_messages[1:-1] if len(final_messages) > 2 else None,
            tools=tools if tools else None,
            calling_action_name=self.get_class_name(),
            prompt_for_observability=visitor.utterance,
            **model_kwargs,
        )

    async def _force_termination(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        visitor: "InteractWalker",
        model_kwargs: Dict[str, Any],
    ) -> str:
        """Force a final summarization call when limits are reached.

        Appends a system message instructing the model to summarize, then
        makes one final call without tools.

        Args:
            messages: Current conversation messages.
            tools: Tool definitions (will be excluded from this call).
            visitor: The InteractWalker.
            model_kwargs: Model keyword arguments.

        Returns:
            Final response text.
        """
        messages.append({"role": "user", "content": FORCED_TERMINATION_PROMPT})
        # Remove thinking config for final call to avoid token budget issues
        final_kwargs = {k: v for k, v in model_kwargs.items() if k != "thinking"}

        try:
            model_result = await self._call_model(
                messages, None, visitor, final_kwargs  # No tools for final call
            )
            return await model_result.get_response() or model_result.response or ""
        except Exception as e:
            logger.error("SkillInteractAction: forced termination call failed: %s", e)
            return "I was unable to complete the task within the allowed steps."

    async def _final_review_pass(
        self,
        messages: List[Dict[str, Any]],
        candidate_response: str,
        visitor: "InteractWalker",
        model_kwargs: Dict[str, Any],
    ) -> str:
        """Run an optional no-tools final review pass for grounding."""
        final_kwargs = {k: v for k, v in model_kwargs.items() if k != "thinking"}
        review_messages = list(messages)
        review_messages.append({"role": "assistant", "content": candidate_response})
        review_messages.append({"role": "user", "content": FINAL_REVIEW_PROMPT})
        try:
            reviewed = await self._call_model(
                review_messages, None, visitor, final_kwargs
            )
            reviewed_text = await reviewed.get_response()
            if reviewed_text:
                return reviewed_text
            if reviewed.response:
                return reviewed.response
            return candidate_response
        except Exception as exc:
            logger.warning(
                "SkillInteractAction: final review pass failed, using original response: %s",
                exc,
            )
            return candidate_response

    async def healthcheck(self) -> bool:
        """Validate thinking action configuration."""
        if not self.model_action_type:
            return False
        if self.max_iterations < 1:
            return False
        return True
