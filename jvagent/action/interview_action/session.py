"""Interview session — lightweight field-value store persisted in conversation.context.

Unlike the v1 SkillInterviewSession which tracks extraction status per field,
branch evaluation state, and a current_question pointer, InterviewSession is minimal:
the LLM decides what to ask next and which tools to call.  The session only
stores the collected values and the interview status.
"""

from __future__ import annotations

import json
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
        status_val = data.get("status", "active")
        try:
            status = InterviewStatus(status_val)
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
        missing = []
        for f in required_fields:
            if not self.has_field(f) and not self.is_skipped(f):
                missing.append(f)
        return missing

    def all_required_collected(self, required_fields: List[str]) -> bool:
        return len(self.missing_required(required_fields)) == 0

    def get_collected_summary(self) -> Dict[str, str]:
        return dict(self.fields)

    def is_active(self) -> bool:
        return self.status in (InterviewStatus.ACTIVE, InterviewStatus.REVIEW)


SESSION_KEY = "interview"

# Scratch keys in InterviewSession.context — runtime flow state, not domain data.
CTX_QUESTION_PRESENTED = "question_presented"

# Conversation.context keys owned by the platform (not interview runtime).
CONVERSATION_CONTEXT_PLATFORM_KEYS: FrozenSet[str] = frozenset({"new_user"})


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
    extra = frozenset(retain_keys or ())
    retain = CONVERSATION_CONTEXT_PLATFORM_KEYS | extra
    preserved = {k: ctx[k] for k in retain if k in ctx}
    ctx.clear()
    ctx.update(preserved)


def has_active_session(conversation) -> bool:
    data = conversation.context.get(SESSION_KEY)
    if not data or not isinstance(data, dict):
        return False
    status = data.get("status", "")
    return status in (
        InterviewStatus.ACTIVE.value,
        InterviewStatus.REVIEW.value,
    )
