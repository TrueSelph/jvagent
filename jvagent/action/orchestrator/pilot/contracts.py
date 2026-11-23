"""Small immutable contracts shared by the opt-in capability pilot."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from jvagent.skills.research.contracts import (
    EvidenceReference,
    ResearchBrief,
)
from jvagent.skills.research.contracts import ResearchFinding as ResearchFinding
from jvagent.skills.research.contracts import evidence_is_fresh as evidence_is_fresh
from jvagent.skills.research.contracts import (
    normalize_evidence_url as normalize_evidence_url,
)
from jvagent.skills.research.contracts import (
    render_research,
)

MAX_PILOT_QUESTION_CHARS = 20_000
MAX_PILOT_CONTEXT_CHARS = 20_000
MAX_PILOT_EVIDENCE_AGE_SECONDS = 86_400
MAX_PILOT_EVIDENCE_CLOCK_SKEW_SECONDS = 300


class PilotModel(BaseModel):
    """Base for persisted or model-produced pilot data."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PilotCaller(PilotModel):
    """Server-resolved graph identity for one caller and conversation."""

    agent_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)


class PilotRunContext(PilotModel):
    """Trusted per-run values; callers must not construct these from model args."""

    caller: PilotCaller
    task_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=128)
    channel: str = Field(default="", max_length=128)
    skill_id: str = Field(min_length=1, max_length=128)
    skill_digest: str = Field(min_length=1, max_length=128)
    config_digest: str = Field(min_length=1, max_length=128)
    max_model_requests: int = Field(default=32, ge=1, le=32)
    max_tool_calls: int = Field(default=48, ge=1, le=64)
    max_tool_result_chars: int = Field(default=4000, ge=256, le=30000)
    max_total_tokens: int = Field(default=100000, ge=1, le=200000)
    max_output_tokens: int = Field(default=20000, ge=1, le=32000)
    max_runtime_seconds: int = Field(default=300, ge=1, le=600)


class ConversationalReply(PilotModel):
    """Short, non-factual answer for a request that needs no external research."""

    answer: str = Field(min_length=1, max_length=4000)


PilotOutput = Union[ResearchBrief, ConversationalReply]


def output_user_text(
    output: PilotOutput, evidence: Sequence[EvidenceReference] = ()
) -> str:
    """Compatibility renderer for persisted pilot outputs."""
    if isinstance(output, ConversationalReply):
        return output.answer
    return render_research(output, evidence)


class PilotSnapshot(PilotModel):
    """Versioned task payload persisted through the existing TaskStore."""

    schema_version: Literal[8] = 8
    driver: Literal["capability_pilot"] = "capability_pilot"
    caller: PilotCaller
    skill_id: str = Field(min_length=1, max_length=128)
    skill_digest: str = Field(min_length=1, max_length=128)
    config_digest: str = Field(min_length=1, max_length=128)
    status: Literal[
        "running",
        "delivery_pending",
        "complete",
        "failed",
        "cancelled",
        "parked",
    ] = "running"
    question: str = Field(default="", max_length=MAX_PILOT_QUESTION_CHARS)
    proactive_task_id: Optional[str] = Field(default=None, max_length=256)
    proactive_context: str = Field(default="", max_length=MAX_PILOT_CONTEXT_CHARS)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=30)
    usage_accounting_complete: bool = True
    model_requests_used: int = Field(default=0, ge=0)
    unsettled_model_requests: int = Field(default=0, ge=0)
    unreported_model_usage_responses: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)
    evidence_overflow_count: int = Field(default=0, ge=0, le=10000)
    reported_input_tokens_used: int = Field(default=0, ge=0)
    reported_output_tokens_used: int = Field(default=0, ge=0)
    estimated_input_tokens_used: int = Field(default=0, ge=0)
    estimated_output_tokens_used: int = Field(default=0, ge=0)
    delivery_attempt_count: int = Field(default=0, ge=0)
    delivery_message_id: Optional[str] = Field(default=None, max_length=256)
    delivery_last_attempt_at: Optional[datetime] = None
    delivery_acknowledged: bool = False
    delivery_acknowledged_at: Optional[datetime] = None
    delivery_reconciled_attempt_count: int = Field(default=0, ge=0)
    delivery_reconciliation_decision: Optional[
        Literal["delivered", "not_delivered"]
    ] = None
    delivery_reconciliation_actor: Optional[str] = Field(default=None, max_length=256)
    delivery_reconciliation_at: Optional[datetime] = None
    delivery_reconciliation_note: Optional[str] = Field(default=None, max_length=1024)
    delivery_retry_authorized: bool = False
    park_reason: Optional[str] = Field(default=None, max_length=512)
    output: Optional[PilotOutput] = None


def validate_snapshot_for_run(
    snapshot: PilotSnapshot,
    *,
    caller: PilotCaller,
    skill_id: str,
    skill_digest: str,
    config_digest: str,
) -> PilotSnapshot:
    """Reject persisted pilot state when its caller or compiled inputs changed."""

    expected = (caller, skill_id, skill_digest, config_digest)
    actual = (
        snapshot.caller,
        snapshot.skill_id,
        snapshot.skill_digest,
        snapshot.config_digest,
    )
    if actual != expected:
        raise ValueError("pilot snapshot identity or configuration has changed")
    return snapshot


__all__ = [
    "EvidenceReference",
    "ResearchBrief",
    "ResearchFinding",
    "evidence_is_fresh",
    "normalize_evidence_url",
    "PilotModel",
    "PilotCaller",
    "PilotRunContext",
    "ConversationalReply",
    "PilotOutput",
    "PilotSnapshot",
    "output_user_text",
    "validate_snapshot_for_run",
    "MAX_PILOT_QUESTION_CHARS",
    "MAX_PILOT_CONTEXT_CHARS",
    "MAX_PILOT_EVIDENCE_AGE_SECONDS",
    "MAX_PILOT_EVIDENCE_CLOCK_SKEW_SECONDS",
]
