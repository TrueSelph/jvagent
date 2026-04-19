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
from jvagent.action.model.utils.token_estimation import estimate_tokens
from jvagent.action.skill.action_resolver import ActionResolver
from jvagent.action.skill.prompts import (
    FORCED_TERMINATION_PROMPT,
    READ_SKILL_RESULT_TEMPLATE,
    SKILL_INDEX_INTRO,
    THINKING_AGENT_SYSTEM_PROMPT,
    TOOL_CALL_ANNOUNCE_TEMPLATE,
)
from jvagent.action.skill.tool_executor import ToolExecutor
from jvagent.core.app_context import get_app_root
from jvagent.scaffold.skill_resolve import (
    apply_skill_selector,
    resolve_agent_skills,
    resolve_builtin_skills,
    resolve_merged_skill_bundles,
)

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

            # 1. Discover Claude-style skill bundles under agents/<ns>/<id>/skills
            discovered_skills = await self._discover_skill_bundles(visitor)

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
            if discovered_skills:
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
                    requires_actions = skill_data.get("requires_actions", [])

                    # Validate required actions are available
                    if requires_actions:
                        if not visitor.action_resolver:
                            return (
                                f"Error: Skill '{skill_name}' cannot be activated. "
                                f"It requires actions {requires_actions} but no agent "
                                f"context is available to resolve them."
                            )
                        errors = await visitor.action_resolver.validate_requirements(
                            requires_actions
                        )
                        if errors:
                            return (
                                f"Error: Skill '{skill_name}' cannot be activated. "
                                f"Required actions unavailable: {', '.join(errors)}"
                            )

                    registered_tools = await tool_executor.activate_skill(skill_name)
                    return READ_SKILL_RESULT_TEMPLATE.format(
                        skill_name=skill_name,
                        tools=(
                            ", ".join(registered_tools)
                            if registered_tools
                            else "(none)"
                        ),
                        content=skill_data["content"],
                    )

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
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "iterations": 0,
                    "tools_called": [],
                    "thinking_tokens_used": 0,
                    "steps": [],
                    "completed_at": None,
                    "total_duration_seconds": None,
                },
            ) as task:
                # 5. Run the agentic loop
                final_response, termination_reason = await self._run_agentic_loop(
                    visitor=visitor,
                    tool_executor=tool_executor,
                    task_handle=task,
                    discovered_skills=discovered_skills,
                )

                # 6. Deliver final response
                if final_response:
                    effective_mode = self._resolve_response_mode(
                        discovered_skills, tool_executor
                    )
                    if effective_mode == "respond":
                        # Route through PersonaAction for persona-enriched response
                        await visitor.add_directive(final_response)
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

    async def _discover_skill_bundles(
        self, visitor: "InteractWalker"
    ) -> Dict[str, Dict[str, Any]]:
        """Resolve skill bundles from configured sources and apply selector filters."""

        agent = getattr(visitor, "_agent", None)
        if not agent:
            return {}

        selector = getattr(self, "skills", None)
        source = str(getattr(self, "skills_source", "both") or "both").strip().lower()

        if source == "none":
            return {}
        if selector is None or selector == [] or selector == "":
            return {}

        try:
            app_root = get_app_root()
            if source == "both":
                discovered_skills = resolve_merged_skill_bundles(
                    app_root=app_root,
                    namespace=agent.namespace,
                    agent_name=agent.name,
                    include_builtin=True,
                )
            elif source == "builtin":
                discovered_skills = resolve_builtin_skills()
            elif source == "app":
                discovered_skills = resolve_agent_skills(
                    app_root=app_root,
                    namespace=agent.namespace,
                    agent_name=agent.name,
                )
            else:
                logger.warning(
                    "SkillInteractAction: invalid skills_source '%s' (expected builtin|app|both|none)",
                    source,
                )
                return {}

            discovered_skills = apply_skill_selector(
                discovered_skills,
                selector=selector,
                denied=getattr(self, "denied_skills", None),
            )
            logger.info(
                "SkillInteractAction resolved %d skill bundles for %s/%s (source=%s)",
                len(discovered_skills),
                agent.namespace,
                agent.name,
                source,
            )
            return discovered_skills
        except Exception as e:
            logger.warning(
                "SkillInteractAction: error resolving skill bundles: %s",
                e,
                exc_info=True,
            )
            return {}

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

    async def _run_agentic_loop(
        self,
        visitor: "InteractWalker",
        tool_executor: ToolExecutor,
        task_handle: "TaskHandle",
        discovered_skills: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> tuple[str, str]:
        """Core agentic loop: think → act → observe → repeat."""
        model_kwargs = self._build_model_kwargs()

        # Build initial messages
        messages = await self._build_initial_messages(visitor, discovered_skills)

        loop_start = time.monotonic()
        iteration = 0
        final_response = ""
        termination_reason = TerminationReason.COMPLETED.value
        loop_state = LoopState.MODEL

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
            await task_handle.record_step("thinking", iteration=iteration)
            loop_state = LoopState.MODEL

            # Re-fetch tools each iteration to include newly activated skill tools.
            tools = tool_executor.get_tools_list()
            model_result = await self._call_model(
                messages, tools, visitor, model_kwargs
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
                final_response = await model_result.get_response()
                if not final_response and model_result.response:
                    final_response = model_result.response
                termination_reason = TerminationReason.COMPLETED.value
                loop_state = LoopState.TERMINATE
                break

            tool_calls = model_result.tool_calls
            loop_state = LoopState.TOOLS
            await task_handle.record_step(
                "tool_call", iteration=iteration, details={"count": len(tool_calls)}
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
                    await self.publish_thought(
                        visitor=visitor,
                        content=TOOL_CALL_ANNOUNCE_TEMPLATE.format(tool_name=tool_name),
                        thought_type="tool_call",
                        segment_id=f"iter-{iteration}-call-{tool_name}-{idx}",
                        streaming_complete=True,
                        relay_to_adapters=self.relay_thoughts_to_channels,
                        metadata={
                            "action_name": self.get_class_name(),
                            "tool_name": tool_name,
                        },
                    )

            assistant_msg = self._build_assistant_content(model_result)
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
            await task_handle.record_step(
                "tool_result",
                iteration=iteration,
                details={"duration_ms": tool_duration_ms},
            )
            messages = self._maybe_truncate_messages(messages)

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

        await task_handle.record_step(
            "response",
            iteration=iteration,
            details={"length": len(final_response), "loop_state": loop_state.value},
        )
        return final_response, termination_reason

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

    async def _build_initial_messages(
        self,
        visitor: "InteractWalker",
        discovered_skills: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        system_prompt = THINKING_AGENT_SYSTEM_PROMPT

        if discovered_skills:
            skill_index = [SKILL_INDEX_INTRO]
            for s_name, s_data in discovered_skills.items():
                skill_index.append(f"- {s_name}: {s_data['description']}")
            system_prompt += "\n\n" + "\n".join(skill_index)

        utterance = visitor.utterance

        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # Include conversation history from prior interactions
        conversation = visitor.conversation
        interaction = visitor.interaction
        if conversation and interaction:
            try:
                history = await conversation.get_interaction_history(
                    limit=self.history_limit,
                    excluded=interaction.id,
                    with_utterance=True,
                    with_response=True,
                    formatted=True,
                )
                if history:
                    messages.extend(history)
            except Exception as e:
                logger.warning(
                    "SkillInteractAction: failed to load conversation history: %s", e
                )

        messages.append({"role": "user", "content": utterance})
        return messages

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

        return await model_action.query_messages(
            messages=messages,
            stream=False,
            system=messages[0].get("content") if messages else None,
            history=messages[1:-1] if len(messages) > 2 else None,
            tools=tools if tools else None,
            calling_action_name=self.get_class_name(),
            prompt_for_observability=visitor.utterance,
            **model_kwargs,
        )

    def _convert_messages_for_provider(
        self,
        messages: List[Dict[str, Any]],
        provider: str,
    ) -> List[Dict[str, Any]]:
        """Convert internal message format to provider-specific format.

        The agentic loop maintains messages in OpenAI-compatible format
        (tool_calls at message level, tool role for results). Anthropic
        requires different formatting:
        - tool_calls become content blocks with type: "tool_use"
        - tool results become user messages with type: "tool_result" content blocks

        Args:
            messages: Messages in internal format.
            provider: Provider name ("openai", "anthropic", etc.).

        Returns:
            Messages formatted for the provider.
        """
        if provider != "anthropic":
            # OpenAI and compatible providers use the format as-is
            return messages

        # Convert for Anthropic
        converted = []
        # Collect consecutive tool results to merge into a single user message
        pending_tool_results: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            # Flush any pending tool results before non-tool messages
            if role != "tool" and pending_tool_results:
                converted.append(
                    {"role": "user", "content": list(pending_tool_results)}
                )
                pending_tool_results = []

            if role == "tool":
                # Convert OpenAI tool result to Anthropic tool_result content block
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": msg.get("content", ""),
                    }
                )
            elif role == "assistant" and msg.get("tool_calls"):
                # Convert OpenAI tool_calls to Anthropic content blocks
                content_blocks = []
                text = msg.get("content")
                if text:
                    content_blocks.append({"type": "text", "text": text})
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "input": self._parse_tool_arguments(
                                func.get("arguments", "{}")
                            ),
                        }
                    )
                converted.append({"role": "assistant", "content": content_blocks})
            else:
                converted.append(msg)

        # Flush any remaining tool results
        if pending_tool_results:
            converted.append({"role": "user", "content": list(pending_tool_results)})

        return converted

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

    def _build_assistant_content(self, model_result: Any) -> Dict[str, Any]:
        """Build the assistant message dict for appending to the conversation.

        Formats the assistant message based on the provider:
        - OpenAI: {"role": "assistant", "content": text, "tool_calls": [...]}
        - Anthropic: {"role": "assistant", "content": [content blocks with tool_use]}

        The format is determined by checking the provider on the model result.

        Args:
            model_result: The ModelActionResult.

        Returns:
            Complete assistant message dict.
        """
        tool_calls = model_result.tool_calls or []
        response_text = model_result.response or ""
        provider = getattr(model_result, "provider", "")

        if not tool_calls:
            return {"role": "assistant", "content": response_text}

        if provider == "anthropic":
            # Anthropic format: content blocks with tool_use
            content_blocks = []
            if response_text:
                content_blocks.append({"type": "text", "text": response_text})

            for tc in tool_calls:
                func = tc.get("function", {})
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": self._parse_tool_arguments(
                            func.get("arguments", "{}")
                        ),
                    }
                )

            return {"role": "assistant", "content": content_blocks or response_text}
        else:
            # OpenAI format: tool_calls at message level
            return {
                "role": "assistant",
                "content": response_text if response_text else None,
                "tool_calls": tool_calls,
            }

    def _parse_tool_arguments(self, arguments: Any) -> Dict[str, Any]:
        """Parse tool call arguments from string or dict.

        Args:
            arguments: Arguments as JSON string or dict.

        Returns:
            Parsed arguments dict.
        """
        import json

        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except json.JSONDecodeError:
                return {}
        return {}

    def _maybe_truncate_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Truncate old tool results to keep context window manageable.

        Keeps the system message, first user message, and last N tool result
        messages in full. Older tool results are replaced with a summary.

        Args:
            messages: Current message list.

        Returns:
            Truncated message list.
        """
        if len(messages) <= self.max_full_tool_results * 2 + 4:
            return messages

        # Find tool result messages
        tool_result_indices = [
            i for i, m in enumerate(messages) if m.get("role") == "tool"
        ]

        if len(tool_result_indices) <= self.max_full_tool_results:
            return messages

        # Keep only the last N tool results in full, summarize older ones
        keep_indices = set(tool_result_indices[-self.max_full_tool_results :])
        # Always keep system, first user, and last assistant messages
        keep_indices.update({0, 1})
        if messages:
            keep_indices.add(len(messages) - 1)

        truncated = []
        for i, msg in enumerate(messages):
            if i in keep_indices or msg.get("role") != "tool":
                if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
                    token_estimate = estimate_tokens(msg["content"])
                    if token_estimate > self.max_tool_result_tokens:
                        msg = dict(msg)
                        msg["content"] = (
                            f"{msg['content'][: self.tool_result_truncation_chars]}... "
                            "(truncated)"
                        )
                truncated.append(msg)
            else:
                # Replace with summary
                truncated.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id", ""),
                        "content": "(Earlier tool result summarized)",
                    }
                )

        return truncated

    async def healthcheck(self) -> bool:
        """Validate thinking action configuration."""
        if not self.model_action_type:
            return False
        if self.max_iterations < 1:
            return False
        return True
