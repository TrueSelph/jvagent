"""LoopContext: manages the message list for the agentic loop.

Handles message building, truncation, format conversion, and assistant
content construction. Provides language-agnostic operations and fixes
the Anthropic format detection bug.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from jvagent.action.model.utils.token_estimation import estimate_tokens

logger = logging.getLogger(__name__)


@dataclass
class LoopContextConfig:
    """Configuration for message lifecycle management."""

    max_full_tool_results: int = 10
    max_tool_result_tokens: int = 400
    tool_result_truncation_chars: int = 500
    history_limit: int = 5


class LoopContext:
    """Manages the message list for the agentic loop.

    Handles: initial message construction, message truncation,
    provider-specific format conversion, and assistant content formatting.
    """

    def __init__(self, config: LoopContextConfig):
        self._config = config

    def _is_tool_result_message(self, msg: Dict[str, Any]) -> bool:
        """Check if a message is a tool result, handling both OpenAI and Anthropic formats.

        OpenAI format: role == "tool"
        Anthropic format: tool results are content blocks in user messages

        Args:
            msg: Message dictionary to check.

        Returns:
            True if the message is a tool result.
        """
        if msg.get("role") == "tool":
            return True
        # Anthropic format: tool results are content blocks in user messages
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            return any(
                block.get("type") == "tool_result"
                for block in msg["content"]
                if isinstance(block, dict)
            )
        return False

    async def build_initial_messages(
        self,
        system_prompt: str,
        utterance: str,
        conversation: Any,
        interaction: Any,
        skill_index_section: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Build the initial message list.

        Args:
            system_prompt: Base system prompt.
            utterance: User utterance.
            conversation: Conversation object for history loading.
            interaction: Current interaction for history exclusion.
            skill_index_section: Optional skill index to append to system prompt.

        Returns:
            Initial message list with system prompt, history, and user utterance.
        """
        final_system_prompt = system_prompt
        if skill_index_section:
            final_system_prompt += "\n\n" + skill_index_section

        messages = [{"role": "system", "content": final_system_prompt}]

        # Include conversation history from prior interactions
        if conversation and interaction:
            try:
                history = await conversation.get_interaction_history(
                    limit=self._config.history_limit,
                    excluded=interaction.id,
                    with_utterance=True,
                    with_response=True,
                    formatted=True,
                )
                if history:
                    messages.extend(history)
            except Exception as e:
                logger.warning(
                    "LoopContext: failed to load conversation history: %s", e
                )

        messages.append({"role": "user", "content": utterance})
        return messages

    def maybe_truncate(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Truncate old tool results to keep context window manageable.

        Keeps the system message, first user message, and last N tool result
        messages in full. Older tool results are replaced with a summary.
        FIXED: Now handles both OpenAI and Anthropic message formats.

        Args:
            messages: Current message list.

        Returns:
            Truncated message list.
        """
        if len(messages) <= self._config.max_full_tool_results * 2 + 4:
            return messages

        # Find tool result messages using both OpenAI and Anthropic format detection
        tool_result_indices = [
            i for i, m in enumerate(messages) if self._is_tool_result_message(m)
        ]

        if len(tool_result_indices) <= self._config.max_full_tool_results:
            return messages

        # Keep only the last N tool results in full, summarize older ones
        keep_indices = set(tool_result_indices[-self._config.max_full_tool_results :])
        # Always keep system (index 0) and last message
        keep_indices.update({0})
        if messages:
            keep_indices.add(len(messages) - 1)

        truncated = []
        for i, msg in enumerate(messages):
            if i in keep_indices or not self._is_tool_result_message(msg):
                # Check for individual message truncation
                if self._is_tool_result_message(msg):
                    content = msg.get("content")
                    if isinstance(content, str):
                        token_estimate = estimate_tokens(content)
                        if token_estimate > self._config.max_tool_result_tokens:
                            msg = dict(msg)
                            msg["content"] = (
                                f"{content[: self._config.tool_result_truncation_chars]}... "
                                "(truncated)"
                            )
                truncated.append(msg)
            else:
                # Replace with summary - preserve tool_call_id if present
                tool_call_id = (
                    msg.get("tool_call_id", "") if isinstance(msg, dict) else ""
                )
                if isinstance(msg.get("content"), list):
                    # Anthropic format - find tool_use_id
                    for block in msg["content"]:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                        ):
                            tool_call_id = block.get("tool_use_id", tool_call_id)
                            break

                truncated.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": "(Earlier tool result summarized)",
                    }
                )

        return truncated

    @staticmethod
    def convert_for_provider(
        messages: List[Dict[str, Any]], provider: str
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
                            "input": LoopContext.parse_tool_arguments(
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

    @staticmethod
    def build_assistant_content(model_result: Any) -> Dict[str, Any]:
        """Build the assistant message dict for appending to the conversation.

        Formats the assistant message based on the provider:
        - OpenAI: {"role": "assistant", "content": text, "tool_calls": [...]}
        - Anthropic: {"role": "assistant", "content": [content blocks with tool_use]}

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
                        "input": LoopContext.parse_tool_arguments(
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

    @staticmethod
    def parse_tool_arguments(arguments: Any) -> Dict[str, Any]:
        """Parse tool call arguments from string or dict.

        Args:
            arguments: Arguments as JSON string or dict.

        Returns:
            Parsed arguments dict.
        """
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except json.JSONDecodeError:
                return {}
        return {}
