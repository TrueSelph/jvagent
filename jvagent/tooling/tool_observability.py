import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolExecutionEnvelope:
    attempt_id: str
    tool_name: str
    tool_call_id: str
    input_fingerprint: str
    start_ts: float
    end_ts: float = 0.0
    latency_ms: int = 0
    is_error: bool = False
    error_class: str = ""
    recoverable: bool = True
    content_length: int = 0

    def close(
        self,
        *,
        content: str,
        is_error: bool,
        exc: Optional[Exception] = None,
    ) -> None:
        self.end_ts = time.monotonic()
        self.latency_ms = int((self.end_ts - self.start_ts) * 1000)
        self.is_error = is_error
        self.content_length = len(content)
        if exc:
            self.error_class = type(exc).__name__
            msg = str(exc).lower()
            _perm = ("permission denied", "not found", "invalid api key", "unsupported")
            self.recoverable = not any(m in msg for m in _perm)


@dataclass
class SkillActivationEnvelope:
    skill_name: str
    activated_at_iteration: int = 0
    activated_at_ts: float = 0.0
    closed_at_ts: float = 0.0
    duration_ms: int = 0
    tool_count: int = 0
    tool_success_rate: Optional[float] = None
    total_tool_latency_ms: int = 0
    was_completed: bool = False
    termination_reason: str = "abandoned"
    preflight_warnings: int = 0
