"""StuckDetector: detects repeated tool-call signatures and semantic loops indicating a stuck loop.

Uses two detection strategies:
1. Exact signature matching: sliding window of consecutive identical tool-call signatures.
2. Semantic intent matching: sliding window of tool-call intents; detects when different
calls serve the same purpose (e.g., search("auth bug") then search("auth error")).

When N consecutive entries match, a stuck condition is detected and a correction prompt
is returned. After exceeding the max correction limit, forced termination is signaled.
"""

import json
import re
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from jvagent.action.skill.loop_context import LoopContext
from jvagent.action.skill.prompts import STUCK_DETECTION_PROMPT


@dataclass
class StuckDetectorConfig:
    """Configuration for stuck detection behavior."""

    window_size: int = 3
    max_corrections: int = 2
    # Jaccard similarity threshold (0.0-1.0) for intent-based loop detection via token-set overlap
    intent_jaccard_threshold: float = 0.7


class StuckDetector:
    """Detects repeated tool-call signatures and semantic loops indicating the loop is stuck.

    Records tool call signatures in two sliding windows:
    - _signature_window: exact signature matches (catches identical repeated calls)
    - _intent_window: semantic intent matches (catches different calls with same purpose)

    When a window fills with matches, a stuck condition is detected. Returns a
    correction prompt if within the correction limit, or signals forced
    termination if the limit is exceeded.
    """

    def __init__(self, config: StuckDetectorConfig):
        self._config = config
        self._signature_window: deque[str] = deque(maxlen=config.window_size)
        self._intent_window: deque[Set[str]] = deque(maxlen=config.window_size)
        self._corrections = 0

    @property
    def corrections(self) -> int:
        """Number of stuck-detection corrections issued so far."""
        return self._corrections

    def record(self, tool_calls: List[Dict[str, Any]]) -> Optional[str]:
        """Record tool calls and check for stuck state.

        Args:
            tool_calls: List of tool call dicts from the model result.

        Returns:
            STUCK_DETECTION_PROMPT if stuck and within correction limit,
            "FORCE_TERMINATE" if max corrections exceeded,
            None if not stuck.
        """
        # Exact signature detection
        signature = self._build_signature(tool_calls)
        self._signature_window.append(signature)

        # Semantic intent detection
        intents = self._extract_intents(tool_calls)
        self._intent_window.append(intents)

        signature_stuck = (
            len(self._signature_window) == self._config.window_size
            and len(set(self._signature_window)) == 1
        )

        intent_stuck = self._check_intent_window()

        if signature_stuck or intent_stuck:
            self._corrections += 1
            if self._corrections <= self._config.max_corrections:
                self._signature_window.clear()
                self._intent_window.clear()
                return STUCK_DETECTION_PROMPT.format(
                    repeat_count=self._config.window_size
                )
            else:
                return "FORCE_TERMINATE"

        return None

    def reset(self) -> None:
        """Reset the signature window and correction count."""
        self._signature_window.clear()
        self._intent_window.clear()
        self._corrections = 0

    def _check_intent_window(self) -> bool:
        """Check if recent intents are semantically similar.

        Compares token overlap between consecutive intent sets.
        Returns True if similarity exceeds threshold for all pairs in window.
        """
        if len(self._intent_window) < self._config.window_size:
            return False

        intents_list = list(self._intent_window)
        threshold = self._config.intent_jaccard_threshold

        for i in range(len(intents_list) - 1):
            set_a = intents_list[i]
            set_b = intents_list[i + 1]
            if not set_a or not set_b:
                return False
            # Jaccard similarity: intersection / union
            intersection = len(set_a & set_b)
            union = len(set_a | set_b)
            if union == 0:
                return False
            similarity = intersection / union
            if similarity < threshold:
                return False

        return True

    @staticmethod
    def _extract_intents(tool_calls: List[Dict[str, Any]]) -> Set[str]:
        """Extract semantic intent tokens from tool calls.

        Returns a set of lowercase intent tokens (tool names, action verbs,
        and key argument values) for semantic comparison.
        """
        intents: Set[str] = set()
        for call in tool_calls:
            function = call.get("function", {}) or {}
            tool_name = str(function.get("name") or "unknown").lower()
            intents.add(tool_name)

            # Add action verbs from common intent frames
            args = function.get("arguments", "")
            parsed = LoopContext.parse_tool_arguments(args)
            for key, value in parsed.items():
                key_lower = key.lower()
                # Include argument keys that indicate intent
                if key_lower in {
                    "query",
                    "search",
                    "command",
                    "action",
                    "operation",
                    "task",
                    "intent",
                }:
                    val_str = str(value).lower()
                    # Tokenize: split on non-alphanumeric, keep tokens >= 3 chars
                    tokens = re.findall(r"[a-z0-9]{3,}", val_str)
                    intents.update(tokens)
                # Include target identifiers (paths, names, IDs)
                if key_lower in {"path", "file_path", "name", "id", "skill_name"}:
                    val_str = str(value).lower()
                    # Extract base name from paths
                    if "/" in val_str:
                        val_str = val_str.split("/")[-1]
                    if "." in val_str:
                        val_str = val_str.split(".")[0]
                    if len(val_str) >= 3:
                        intents.add(val_str)

        return intents

    @staticmethod
    def _build_signature(tool_calls: List[Dict[str, Any]]) -> str:
        """Build a deterministic signature for one iteration's tool calls.

        Uses simple canonicalization and hash instead of cryptographic hashing
        — this is for loop detection, not security.
        """
        signatures: List[str] = []
        for call in tool_calls:
            function = call.get("function", {}) or {}
            tool_name = str(function.get("name") or "unknown")
            args = function.get("arguments", {})
            parsed = LoopContext.parse_tool_arguments(args)
            canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
            signatures.append(
                f"{tool_name}:{len(canonical)}:{hash(canonical) & 0xFFFFFFFF}"
            )
        return "|".join(signatures)
