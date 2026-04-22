"""ContextCompactor: evidence-aware, policy-driven context window management.

Replaces the bare heuristic truncation in ``LoopContext.maybe_truncate`` with
a compactor that:

1. Never destroys entries in the EvidenceLog (raw truth is preserved there).
2. Never orphans tool-call/result message pairs (Anthropic & OpenAI formats).
3. Reduces the model-facing message list only — the EvidenceLog always
   retains the full raw content.
4. Summarises older tool results with a concise placeholder that includes the
   tool name and iteration number so the model retains provenance awareness.

Usage::

    compactor = ContextCompactor(CompactorConfig(...))
    messages = compactor.compact(messages, evidence_log=log)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from jvagent.action.model.utils.token_estimation import estimate_tokens
from jvagent.action.skill.prompts import (
    COMPACT_DIRECT_RESUME_INSTRUCTION,
    COMPACT_DIRECT_RESUME_SENTINEL,
)

logger = logging.getLogger(__name__)


@dataclass
class CompactorConfig:
    """Configuration for ContextCompactor.

    Attributes:
        max_full_tool_results: Keep the last N tool-result messages in full;
            summarise older ones.
        max_tool_result_tokens: Max estimated token count for an individual
            tool result message before inline truncation.
        tool_result_truncation_chars: Character limit for inline truncation of
            large individual tool results kept in the full window.
    """

    max_full_tool_results: int = 10
    max_tool_result_tokens: int = 400
    tool_result_truncation_chars: int = 500


class ContextCompactor:
    """Evidence-aware context compactor for the agentic loop message list.

    Args:
        config: Compaction parameters.
    """

    def __init__(self, config: CompactorConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def compact(
        self,
        messages: List[Dict[str, Any]],
        *,
        evidence_log: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Return a compacted version of messages, preserving structural integrity.

        Guarantees:
        - System message (index 0) is always retained.
        - The last message is always retained.
        - Tool-call/result pairs are never split across the keep/summarise boundary.
        - Raw content is available in ``evidence_log``; only the derived
          model-facing message is summarised.

        Args:
            messages: Current loop message list.
            evidence_log: Optional EvidenceLog instance used to enrich
                summaries with provenance (tool name, iteration).

        Returns:
            Compacted message list.
        """
        cfg = self._config
        # Fast path: nothing to do yet
        if len(messages) <= cfg.max_full_tool_results * 2 + 4:
            return messages

        tool_result_indices = [
            i for i, m in enumerate(messages) if self._is_tool_result(m)
        ]

        if len(tool_result_indices) <= cfg.max_full_tool_results:
            return messages

        # Determine which tool-result messages to keep in full (most recent N)
        keep_full: Set[int] = set(tool_result_indices[-cfg.max_full_tool_results :])

        # Expand keep_full to protect paired assistant messages (avoid orphans)
        keep_full.update(self._paired_assistant_indices(messages, tool_result_indices))

        # Always keep system message and last message
        keep_full.add(0)
        if messages:
            keep_full.add(len(messages) - 1)

        summarised_count = 0
        compacted: List[Dict[str, Any]] = []
        for i, msg in enumerate(messages):
            if not self._is_tool_result(msg) or i in keep_full:
                # Apply inline size cap even for kept messages
                if self._is_tool_result(msg) and i in keep_full:
                    msg = self._maybe_trim_inline(msg, cfg)
                compacted.append(msg)
            else:
                # Summarise: produce a short placeholder preserving provenance
                compacted.append(
                    self._make_summary_message(msg, evidence_log=evidence_log)
                )
                summarised_count += 1

        # Inject a direct-resume instruction the first time compaction actually
        # summarises content. This mirrors claw-code's post-compaction
        # continuation message: the model should resume the task without
        # recapping, re-announcing, or re-narrating already-completed work.
        if summarised_count > 0 and not self._already_has_resume_instruction(compacted):
            compacted = self._insert_resume_instruction(compacted)

        return compacted

    # ------------------------------------------------------------------
    # Message classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_tool_result(msg: Dict[str, Any]) -> bool:
        """Return True for OpenAI tool messages and Anthropic tool_result blocks."""
        if msg.get("role") == "tool":
            return True
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            return any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in msg["content"]
            )
        return False

    @staticmethod
    def _is_tool_call_message(msg: Dict[str, Any]) -> bool:
        """Return True for assistant messages that include tool calls."""
        if msg.get("role") == "assistant":
            if msg.get("tool_calls"):
                return True
            if isinstance(msg.get("content"), list):
                return any(
                    isinstance(b, dict) and b.get("type") == "tool_use"
                    for b in msg["content"]
                )
        return False

    @staticmethod
    def _paired_assistant_indices(
        messages: List[Dict[str, Any]],
        tool_result_indices: List[int],
    ) -> Set[int]:
        """Return assistant indices whose tool calls have a result in the summarise zone."""
        # We never want to keep an assistant tool-call message while dropping its
        # result (or vice versa), so include any assistant message immediately
        # before a tool-result that is in the "keep" zone.
        protected: Set[int] = set()
        for tr_idx in tool_result_indices:
            # Look back for the matching assistant message
            for j in range(tr_idx - 1, -1, -1):
                if ContextCompactor._is_tool_call_message(messages[j]):
                    protected.add(j)
                    break
                # Stop if we hit another tool result (it has its own pair)
                if ContextCompactor._is_tool_result(messages[j]):
                    break
        return protected

    # ------------------------------------------------------------------
    # Compaction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _already_has_resume_instruction(messages: List[Dict[str, Any]]) -> bool:
        """Detect whether a prior compaction already injected the resume instruction."""
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str) and COMPACT_DIRECT_RESUME_SENTINEL in content:
                return True
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text") or block.get("content") or ""
                        if (
                            isinstance(text, str)
                            and COMPACT_DIRECT_RESUME_SENTINEL in text
                        ):
                            return True
        return False

    @staticmethod
    def _insert_resume_instruction(
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Insert the direct-resume instruction immediately after the last tool result.

        Placing it right after the most recent tool result means the next model
        call reads "continue from here, do not recap" at the point where it is
        about to compose its next response.
        """
        last_tool_result_idx = -1
        for i, msg in enumerate(messages):
            if ContextCompactor._is_tool_result(msg):
                last_tool_result_idx = i

        resume_msg: Dict[str, Any] = {
            "role": "user",
            "content": COMPACT_DIRECT_RESUME_INSTRUCTION,
        }

        if last_tool_result_idx == -1:
            # Fallback: append before the final message if no tool result found
            if not messages:
                return [resume_msg]
            return messages[:-1] + [resume_msg] + [messages[-1]]

        insert_at = last_tool_result_idx + 1
        return messages[:insert_at] + [resume_msg] + messages[insert_at:]

    @staticmethod
    def _maybe_trim_inline(msg: Dict[str, Any], cfg: CompactorConfig) -> Dict[str, Any]:
        """Trim a single kept tool result if it exceeds the per-message token cap."""
        content = msg.get("content")
        if not isinstance(content, str):
            return msg
        if estimate_tokens(content) > cfg.max_tool_result_tokens:
            msg = dict(msg)
            msg["content"] = (
                content[: cfg.tool_result_truncation_chars] + "… (truncated)"
            )
        return msg

    @staticmethod
    def _make_summary_message(
        msg: Dict[str, Any],
        *,
        evidence_log: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Replace a tool result with a concise provenance-aware placeholder.

        Attempts to enrich the placeholder with tool name and iteration from
        the EvidenceLog if available.
        """
        tool_call_id = ""
        if msg.get("role") == "tool":
            tool_call_id = msg.get("tool_call_id", "")
        elif isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_call_id = block.get("tool_use_id", "")
                    break

        # Enrich with EvidenceLog provenance when available
        tool_name = ""
        iteration = ""
        if evidence_log is not None and tool_call_id:
            try:
                entry = evidence_log.by_tool_call_id(tool_call_id)
                if entry:
                    tool_name = entry.tool_name
                    iteration = str(entry.iteration)
            except Exception:
                pass

        label_parts = ["(Earlier result summarised"]
        if tool_name:
            label_parts.append(f"tool={tool_name}")
        if iteration:
            label_parts.append(f"iter={iteration}")
        label = " — ".join(label_parts) + ")"

        if msg.get("role") == "tool":
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": label,
            }
        # Anthropic-format tool_result block in a user message
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": label,
                }
            ],
        }
