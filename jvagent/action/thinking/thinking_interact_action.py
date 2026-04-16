"""ThinkingInteractAction: agentic loop implementing think-act-observe.

An InteractAction that runs the think-act-observe loop, enabling long-running
agents that intelligently execute skills, track tasks, and run tools/MCPs
as configured. The LLM decides which tools to call, ToolExecutor dispatches
them, and results feed back into the conversation for the next iteration.
"""

import logging
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.model.language.tools import ToolCall, ToolManager
from jvagent.action.thinking.prompts import (
    ERROR_ANNOUNCE_TEMPLATE,
    FORCED_TERMINATION_PROMPT,
    THINKING_AGENT_SYSTEM_PROMPT,
    TOOL_CALL_ANNOUNCE_TEMPLATE,
    TOOL_RESULT_ANNOUNCE_TEMPLATE,
)
from jvagent.action.thinking.task_tracker import TaskTracker
from jvagent.action.thinking.tool_executor import ToolExecutor

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.skill.skill_action import SkillAction

logger = logging.getLogger(__name__)

# How many full tool results to keep before summarizing older ones
_DEFAULT_MAX_FULL_TOOL_RESULTS = 10


class ThinkingInteractAction(InteractAction):
    """InteractAction implementing a think-act-observe agentic loop.

    When activated by the InteractRouter, this action:
    1. Loads an optional SkillAction for prompt composition and tool filtering
    2. Initializes a ToolExecutor with tools from configured MCP servers
    3. Runs the agentic loop: LLM thinks → calls tools → observes results → repeats
    4. Tracks multi-step progress via TaskTracker on Conversation.active_tasks
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
        skill: Optional name of SkillAction to load.
        tool_servers: Names of MCPAction instances providing tools.
        allow_local_tools: Whether ToolExecutor can register local Python tools.
        stream_thinking: Stream extended thinking content as adhoc.
        stream_tool_progress: Stream tool call status as adhoc.
        max_full_tool_results: Keep last N tool results in full; summarize older.
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
    skill: Optional[str] = attribute(
        default=None,
        description="Name of SkillAction to load (None for free-form)",
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
    max_full_tool_results: int = attribute(
        default=_DEFAULT_MAX_FULL_TOOL_RESULTS,
        description="Keep last N tool results in full; summarize older",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Entry point: load skill, build tool registry, run the agentic loop.

        Args:
            visitor: The InteractWalker visiting this action.
        """
        if not self._ensure_interaction(visitor):
            logger.warning("ThinkingInteractAction: No interaction available")
            await visitor.unrecord_action_execution()
            return

        interaction = visitor.interaction
        conversation = visitor.conversation
        if not conversation:
            logger.warning("ThinkingInteractAction: No conversation available")
            await visitor.unrecord_action_execution()
            return

        try:
            # 1. Load skill (if configured)
            skill_action = await self._load_skill(visitor)

            # 2. Initialize ToolExecutor
            tool_executor = ToolExecutor(
                call_timeout=60.0,
                sanitize_errors=True,
            )
            await tool_executor.initialize(
                visitor=visitor,
                tool_servers=self.tool_servers,
                skill=skill_action,
            )

            if not tool_executor.get_tool_names():
                logger.warning(
                    "ThinkingInteractAction: No tools available, "
                    "proceeding without tools (reasoning-only mode)"
                )

            # 3. Initialize TaskTracker
            task_tracker = TaskTracker(
                conversation=conversation,
                action_name=self.get_class_name(),
            )
            task_description = (
                f"Agentic task: {interaction.utterance[:100]}"
                if interaction.utterance
                else "Agentic task"
            )
            await task_tracker.create_task(
                description=task_description,
                task_type="AGENTIC_LOOP",
                metadata={"skill": self.skill} if self.skill else None,
            )

            # 4. Run the agentic loop
            final_response = await self._run_agentic_loop(
                visitor=visitor,
                tool_executor=tool_executor,
                task_tracker=task_tracker,
                skill_action=skill_action,
            )

            # 5. Publish final response
            if final_response:
                await self.publish(
                    visitor,
                    content=final_response,
                    streaming_complete=True,
                )
                # Mark directives as executed
                interaction.set_to_executed()

            # 6. Complete task
            await task_tracker.complete_task(
                final_status="completed",
                summary=final_response[:200] if final_response else None,
            )

            # 7. Cleanup
            await tool_executor.cleanup()

        except Exception as e:
            logger.error(
                "ThinkingInteractAction: Error during agentic loop: %s",
                e,
                exc_info=True,
            )
            # Try to fail the task gracefully
            try:
                task_tracker_local = TaskTracker(
                    conversation=conversation,
                    action_name=self.get_class_name(),
                )
                await task_tracker_local.fail_task(str(e))
            except Exception:
                pass
            await visitor.unrecord_action_execution()

    async def _load_skill(self, visitor: "InteractWalker") -> Optional["SkillAction"]:
        """Load the configured SkillAction if set.

        Args:
            visitor: The InteractWalker.

        Returns:
            SkillAction instance or None.
        """
        if not self.skill:
            return None

        from jvagent.action.skill.skill_action import SkillAction

        skill = await self.get_action(SkillAction)
        if not skill:
            logger.warning(
                "ThinkingInteractAction: SkillAction not found, "
                "proceeding without skill"
            )
            return None

        # Verify the skill_name matches
        if getattr(skill, "skill_name", None) != self.skill:
            logger.warning(
                "ThinkingInteractAction: Found SkillAction but skill_name "
                "'%s' doesn't match expected '%s'",
                getattr(skill, "skill_name", None),
                self.skill,
            )
            return None

        return skill

    async def _run_agentic_loop(
        self,
        visitor: "InteractWalker",
        tool_executor: ToolExecutor,
        task_tracker: TaskTracker,
        skill_action: Optional["SkillAction"] = None,
    ) -> str:
        """Core agentic loop: think → act → observe → repeat.

        Args:
            visitor: The InteractWalker.
            tool_executor: The ToolExecutor for dispatching tool calls.
            task_tracker: The TaskTracker for recording progress.
            skill_action: Optional SkillAction for prompt and tool filtering.

        Returns:
            Final response text.
        """
        # Resolve model overrides from skill
        model_kwargs = self._build_model_kwargs(skill_action)

        # Build initial messages
        messages = await self._build_initial_messages(visitor, skill_action)

        # Get tool definitions for LLM
        tools = tool_executor.get_tools_list()

        loop_start = time.monotonic()
        iteration = 0
        final_response = ""

        while iteration < self.max_iterations:
            # Check duration limit
            elapsed = time.monotonic() - loop_start
            if elapsed >= self.max_duration_seconds:
                logger.info(
                    "ThinkingInteractAction: duration limit reached (%.1fs)",
                    elapsed,
                )
                # Force a final summarization call
                final_response = await self._force_termination(
                    messages, tools, visitor, model_kwargs
                )
                break

            # Check if forced termination needed at iteration limit
            if iteration == self.max_iterations - 1:
                final_response = await self._force_termination(
                    messages, tools, visitor, model_kwargs
                )
                break

            iteration += 1
            await task_tracker.add_step("thinking", iteration=iteration)

            # Call the LLM
            model_result = await self._call_model(
                messages, tools, visitor, model_kwargs
            )

            # Handle thinking content (extended thinking)
            if model_result.thinking_content and self.stream_thinking:
                await self.publish(
                    visitor,
                    content=model_result.thinking_content,
                    metadata={"thinking": True},
                    transient=True,
                )
                thinking_tokens = model_result.thinking_tokens or 0
                await task_tracker.add_step(
                    "thinking",
                    iteration=iteration,
                    details={"tokens": thinking_tokens},
                )

            # Check for termination: no tool calls, text-only response
            if not model_result.tool_calls:
                final_response = await model_result.get_response()
                if not final_response and model_result.response:
                    final_response = model_result.response
                break

            # Process tool calls
            tool_calls = model_result.tool_calls
            await task_tracker.add_step(
                "tool_call",
                iteration=iteration,
                details={
                    "count": len(tool_calls),
                    "tools": [
                        tc.get("function", {}).get("name", "") for tc in tool_calls
                    ],
                },
            )

            # Stream tool call announcements
            if self.stream_tool_progress:
                for tc in tool_calls:
                    tool_name = tc.get("function", {}).get("name", "unknown")
                    await self.publish(
                        visitor,
                        content=TOOL_CALL_ANNOUNCE_TEMPLATE.format(tool_name=tool_name),
                        metadata={"tool_call": True, "tool_name": tool_name},
                        transient=True,
                    )

            # Append assistant message with tool calls to conversation
            assistant_msg = self._build_assistant_content(model_result)
            messages.append(assistant_msg)

            # Dispatch tool calls
            tool_start = time.monotonic()
            tool_result_messages = await tool_executor.dispatch(tool_calls, visitor)
            tool_duration_ms = int((time.monotonic() - tool_start) * 1000)

            # Stream tool results
            if self.stream_tool_progress:
                for tr_msg in tool_result_messages:
                    content = tr_msg.get("content", "")
                    tool_call_id = tr_msg.get("tool_call_id", "")
                    is_error = content.startswith("Error:")
                    await self.publish(
                        visitor,
                        content=content[:500],  # Truncate long results
                        metadata={
                            "tool_result": True,
                            "tool_call_id": tool_call_id,
                            "is_error": is_error,
                        },
                        transient=True,
                    )

            # Append tool result messages
            messages.extend(tool_result_messages)

            # Record tool results in task tracker
            await task_tracker.add_step(
                "tool_result",
                iteration=iteration,
                details={"duration_ms": tool_duration_ms},
            )

            # Truncate old tool results if message list is too long
            messages = self._maybe_truncate_messages(messages)

        # Handle case where loop exhausted without response
        if not final_response:
            final_response = (
                "I was unable to complete the task within the allowed steps."
            )

        await task_tracker.add_step(
            "response", iteration=iteration, details={"length": len(final_response)}
        )

        return final_response

    def _build_model_kwargs(
        self, skill_action: Optional["SkillAction"] = None
    ) -> Dict[str, Any]:
        """Build model kwargs, applying skill overrides.

        Args:
            skill_action: Optional SkillAction with overrides.

        Returns:
            Dict of model keyword arguments.
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "temperature": self.model_temperature,
            "max_tokens": self.model_max_tokens,
        }

        # Apply skill overrides (skill takes precedence)
        if skill_action:
            overrides = skill_action.get_model_overrides()
            kwargs.update(overrides)

        # Apply extended thinking config
        if self.thinking_budget_tokens > 0:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget_tokens,
            }
            # Ensure max_tokens >= budget_tokens + 1
            if kwargs.get("max_tokens", 0) < self.thinking_budget_tokens + 1:
                kwargs["max_tokens"] = self.thinking_budget_tokens + 1

        return kwargs

    async def _build_initial_messages(
        self,
        visitor: "InteractWalker",
        skill_action: Optional["SkillAction"] = None,
    ) -> List[Dict[str, Any]]:
        """Compose the initial message list from system prompt + history + utterance.

        Args:
            visitor: The InteractWalker.
            skill_action: Optional SkillAction for prompt composition.

        Returns:
            List of message dicts for the LLM.
        """
        # Compose system prompt
        if skill_action:
            system_prompt = skill_action.compose_system_prompt()
        else:
            system_prompt = THINKING_AGENT_SYSTEM_PROMPT

        # Compose user utterance
        if skill_action:
            utterance = skill_action.compose_utterance(visitor.utterance)
        else:
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
                    limit=5,
                    excluded=interaction.id,
                    with_utterance=True,
                    with_response=True,
                    formatted=True,
                )
                if history:
                    messages.extend(history)
            except Exception as e:
                logger.warning(
                    "ThinkingInteractAction: failed to load conversation history: %s", e
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
        """Call LanguageModelAction._query() directly with pre-formatted messages.

        Bypasses query() because query() expects a prompt string and calls
        format_messages() internally. The agentic loop maintains its own
        message list with tool-call/result pairs, so we call _query() directly.

        Args:
            messages: Current conversation messages (pre-formatted).
            tools: Available tool definitions.
            visitor: The InteractWalker.
            model_kwargs: Model-specific keyword arguments.

        Returns:
            ModelActionResult from the LLM.
        """
        model_action = await self.get_model_action(required=True)

        # Convert messages for provider-specific format requirements
        provider = getattr(model_action, "provider", "")
        converted_messages = self._convert_messages_for_provider(messages, provider)

        result = await model_action._query(
            converted_messages,
            tools=tools if tools else None,
            **model_kwargs,
        )
        return result

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
            logger.error(
                "ThinkingInteractAction: forced termination call failed: %s", e
            )
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
