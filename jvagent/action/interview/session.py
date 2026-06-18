"""Interview session — lightweight field-value store persisted in conversation.context.

The LLM decides what to ask next and which tools to call; the session only
stores collected values, skipped fields, status, and a scratch ``context`` dict
for skill hooks.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Set, Union

logger = logging.getLogger(__name__)


class InterviewStatus(str, Enum):
    ACTIVE = "active"
    REVIEW = "review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class InterviewSession:
    interview_type: str
    status: InterviewStatus = InterviewStatus.ACTIVE
    fields: Dict[str, str] = field(default_factory=dict)
    skipped_fields: Set[str] = field(default_factory=set)
    context: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[str] = None

    def __post_init__(self):
        if self.started_at is None:
            self.started_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_type": self.interview_type,
            "status": self.status.value,
            "fields": dict(self.fields),
            "skipped_fields": sorted(self.skipped_fields),
            "context": deepcopy(self.context),
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InterviewSession":
        try:
            status = InterviewStatus(data.get("status", "active"))
        except ValueError:
            status = InterviewStatus.ACTIVE

        skipped = data.get("skipped_fields", [])
        if isinstance(skipped, list):
            skipped = set(skipped)

        return cls(
            interview_type=data.get("interview_type", ""),
            status=status,
            fields=data.get("fields", {}),
            skipped_fields=skipped,
            context=data.get("context", {}),
            started_at=data.get("started_at"),
        )

    def get_value(self, field_name: str) -> Optional[str]:
        return self.fields.get(field_name)

    def set_value(self, field_name: str, value: str):
        self.fields[field_name] = value
        self.skipped_fields.discard(field_name)

    def skip_field(self, field_name: str):
        self.skipped_fields.add(field_name)
        self.fields.pop(field_name, None)

    def is_skipped(self, field_name: str) -> bool:
        return field_name in self.skipped_fields

    def has_field(self, field_name: str) -> bool:
        return field_name in self.fields and bool(self.fields[field_name])

    def missing_required(self, required_fields: List[str]) -> List[str]:
        return [
            f
            for f in required_fields
            if not self.has_field(f) and not self.is_skipped(f)
        ]

    def get_collected_summary(self) -> Dict[str, str]:
        return dict(self.fields)

    def is_active(self) -> bool:
        return self.status in (InterviewStatus.ACTIVE, InterviewStatus.REVIEW)


SESSION_KEY = "interview"

# Conversation.context keys owned by the platform (not interview runtime).
CONVERSATION_CONTEXT_PLATFORM_KEYS: FrozenSet[str] = frozenset({"new_user"})

# Additional platform-durable keys registered by the host app. These survive every
# interview teardown (cancel/complete/reset) exactly like the built-in platform
# keys, so an app can keep its own cross-flow state (e.g. an account session) in
# conversation.context without each skill having to remember a retain list.
_APP_PLATFORM_CONTEXT_KEYS: Set[str] = set()


def register_platform_context_keys(*keys: str) -> None:
    """Register conversation.context keys the host app treats as platform-durable.

    Idempotent. Registered keys are retained by ``clear_interview_context`` on top
    of the built-in platform keys and any per-call ``retain_keys``.
    """
    _APP_PLATFORM_CONTEXT_KEYS.update(k for k in keys if k)


async def save_session(conversation, session: InterviewSession) -> None:
    conversation.context[SESSION_KEY] = session.to_dict()
    await conversation.save()


def load_session(conversation) -> Optional[InterviewSession]:
    data = conversation.context.get(SESSION_KEY)
    if not data or not isinstance(data, dict):
        return None
    return InterviewSession.from_dict(data)


def clear_session(conversation) -> None:
    if SESSION_KEY in conversation.context:
        del conversation.context[SESSION_KEY]


def clear_interview_context(
    conversation,
    *,
    retain_keys: Optional[Union[List[str], Set[str]]] = None,
) -> None:
    """Clear interview scratch from conversation.context; retain platform keys by default."""
    ctx = getattr(conversation, "context", None)
    if not isinstance(ctx, dict):
        return
    retain = (
        CONVERSATION_CONTEXT_PLATFORM_KEYS
        | _APP_PLATFORM_CONTEXT_KEYS
        | frozenset(retain_keys or ())
    )
    preserved = {k: ctx[k] for k in retain if k in ctx}
    ctx.clear()
    ctx.update(preserved)


def has_active_session(conversation) -> bool:
    data = conversation.context.get(SESSION_KEY)
    if not data or not isinstance(data, dict):
        return False
    return data.get("status", "") in (
        InterviewStatus.ACTIVE.value,
        InterviewStatus.REVIEW.value,
    )
