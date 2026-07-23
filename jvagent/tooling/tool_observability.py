import time
from dataclasses import dataclass
from typing import Optional


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
