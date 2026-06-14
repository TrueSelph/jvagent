"""Structured proactive task envelope for the TaskMonitor queue."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

PROACTIVE_TASK_TYPE = "PROACTIVE"
SPEC_VERSION = 2

TriggerOn = Literal["schedule", "user_message", "keyword", "mood", "any"]


@dataclass
class ProactiveTaskSpec:
    """Canonical payload for ``task_type='PROACTIVE'`` queue entries."""

    directive: str
    context: str = ""
    channel: Optional[str] = None
    skill: Optional[str] = None
    pinned_tools: List[str] = field(default_factory=list)
    priority: int = 0

    not_before: Optional[str] = None
    not_after: Optional[str] = None

    requires_tasks: List[str] = field(default_factory=list)

    trigger_on: TriggerOn = "schedule"
    trigger_keyword: Optional[str] = None
    trigger_mood: Optional[str] = None

    max_attempts: int = 3
    attempt_count: int = 0
    dispatch_lease_id: Optional[str] = None
    dispatch_claimed_at: Optional[str] = None

    def validate(self) -> None:
        directive = (self.directive or "").strip()
        if not directive:
            raise ValueError("ProactiveTaskSpec.directive is required")

    def to_data(self) -> Dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["spec_version"] = SPEC_VERSION
        return payload

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "ProactiveTaskSpec":
        raw = dict(data or {})
        if raw.get("spec_version") != SPEC_VERSION:
            raise ValueError("unsupported proactive task spec version")
        kwargs = {k: raw.get(k) for k in cls.__dataclass_fields__ if k in raw}
        if kwargs.get("pinned_tools") is None:
            kwargs["pinned_tools"] = []
        if kwargs.get("requires_tasks") is None:
            kwargs["requires_tasks"] = []
        spec = cls(**kwargs)  # type: ignore[arg-type]
        spec.validate()
        return spec

    @classmethod
    def from_task_handle(cls, handle: Any) -> "ProactiveTaskSpec":
        task_type = str(getattr(handle, "task_type", "") or "").upper()
        if task_type != PROACTIVE_TASK_TYPE:
            raise ValueError(f"expected task_type PROACTIVE, got {task_type!r}")
        return cls.from_data(getattr(handle, "data", {}) or {})
