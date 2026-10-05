"""Small immutable contracts shared by the opt-in capability pilot."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field


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
    max_model_requests: int = Field(default=8, ge=1, le=32)
    max_tool_calls: int = Field(default=12, ge=1, le=64)
    max_tool_result_chars: int = Field(default=4000, ge=256, le=30000)
    max_total_tokens: int = Field(default=20000, ge=256, le=200000)
    max_output_tokens: int = Field(default=2000, ge=64, le=32000)
    max_runtime_seconds: int = Field(default=120, ge=1, le=600)


class EvidenceReference(PilotModel):
    """Bounded reference to a result returned by an existing read Action."""

    source_id: str = Field(min_length=1, max_length=256)
    url: str = Field(default="", max_length=2048)
    title: str = Field(default="", max_length=512)
    excerpt: str = Field(default="", max_length=1600)


class ResearchBrief(PilotModel):
    """Validated pilot research result; source support is checked separately."""

    question: str = Field(min_length=1, max_length=2000)
    findings: tuple[str, ...] = Field(min_length=1, max_length=50)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    limitations: tuple[str, ...] = Field(default=(), max_length=30)
    brief: str = Field(min_length=1, max_length=12000)


class PilotInvocation(PilotModel):
    """Durable intent/receipt state for one tool invocation."""

    invocation_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    payload_digest: str = Field(min_length=1, max_length=128)
    status: Literal["prepared", "started", "settled"] = "prepared"
    result: Optional[str] = Field(default=None, max_length=16000)


PilotOutput = ResearchBrief


def output_user_text(
    output: PilotOutput, evidence: Sequence[EvidenceReference] = ()
) -> str:
    """Return the validated brief with citations from observed Action results."""

    cited_ids = set(output.source_ids)
    sources: list[str] = []
    seen_urls: set[str] = set()
    for reference in evidence:
        if reference.source_id not in cited_ids or not reference.url:
            continue
        url = quote(reference.url, safe=":/?&=#%.-_~")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        citation = f"[Source {len(sources) + 1}](<{url}>)"
        sources.append(citation)
    if not sources:
        return output.brief
    return f"{output.brief}\n\nSources: " + ", ".join(sources)


class PilotSnapshot(PilotModel):
    """Versioned task payload persisted through the existing TaskStore."""

    schema_version: Literal[1] = 1
    driver: Literal["capability_pilot"] = "capability_pilot"
    caller: PilotCaller
    skill_id: str = Field(min_length=1, max_length=128)
    skill_digest: str = Field(min_length=1, max_length=128)
    config_digest: str = Field(min_length=1, max_length=128)
    status: Literal[
        "running",
        "waiting_approval",
        "reconciliation_required",
        "complete",
        "failed",
        "cancelled",
        "parked",
    ] = "running"
    requires_reconciliation: bool = False
    question: str = Field(default="", max_length=2000)
    proactive_task_id: Optional[str] = Field(default=None, max_length=256)
    proactive_context: str = Field(default="", max_length=2000)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=30)
    approval_id: Optional[str] = Field(default=None, max_length=256)
    approval_payload_digest: Optional[str] = Field(default=None, max_length=128)
    approval_expires_at: Optional[datetime] = None
    approval_invocation_id: Optional[str] = Field(default=None, max_length=256)
    park_reason: Optional[str] = Field(default=None, max_length=512)
    invocations: tuple[PilotInvocation, ...] = Field(default=(), max_length=100)
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
