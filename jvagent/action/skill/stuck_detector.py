"""StuckDetector: detects repeated tool-call signatures indicating a stuck loop.

Uses a sliding window of consecutive tool-call signatures. When N consecutive
signatures are identical, a stuck condition is detected and a correction prompt
is returned. After exceeding the max correction limit, forced termination is
signaled.
"""

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from jvagent.action.skill.loop_context import LoopContext
from jvagent.action.skill.prompts import STUCK_DETECTION_PROMPT


@dataclass
class StuckDetectorConfig:
    """Configuration for stuck detection behavior."""

    window_size: int = 3
    max_corrections: int = 2


class StuckDetector:
    """Detects repeated tool-call signatures indicating the loop is stuck.

    Records tool call signatures in a sliding window. When the window fills
    with identical signatures, a stuck condition is detected. Returns a
    correction prompt if within the correction limit, or signals forced
    termination if the limit is exceeded.
    """

    def __init__(self, config: StuckDetectorConfig):
        self._config = config
        self._signature_window: deque[str] = deque(maxlen=config.window_size)
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
        signature = self._build_signature(tool_calls)
        self._signature_window.append(signature)

        if (
            len(self._signature_window) == self._config.window_size
            and len(set(self._signature_window)) == 1
        ):
            self._corrections += 1
            if self._corrections <= self._config.max_corrections:
                self._signature_window.clear()
                return STUCK_DETECTION_PROMPT.format(
                    repeat_count=self._config.window_size
                )
            else:
                return "FORCE_TERMINATE"

        return None

    def reset(self) -> None:
        """Reset the signature window and correction count."""
        self._signature_window.clear()
        self._corrections = 0

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
