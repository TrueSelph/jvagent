"""Small immutable contracts shared by the opt-in capability pilot."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional, Union
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PILOT_QUESTION_CHARS = 20_000
MAX_PILOT_CONTEXT_CHARS = 20_000
MAX_PILOT_EVIDENCE_AGE_SECONDS = 86_400
MAX_PILOT_EVIDENCE_CLOCK_SKEW_SECONDS = 300


def normalize_evidence_url(value: str) -> str:
    """Return a canonical, credential-free HTTP(S) URL or an empty string."""

    if not isinstance(value, str) or not value or "\\" in value:
        return ""
    if any(character.isspace() or ord(character) < 32 for character in value):
        return ""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    try:
        address = ipaddress.ip_address(hostname)
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    except ValueError:
        try:
            host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError:
            return ""
        labels = host.split(".")
        if (
            not host
            or len(host) > 253
            or any(
                not label
                or len(label) > 63
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                for label in labels
            )
        ):
            return ""
    if port is not None and port != (80 if scheme == "http" else 443):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parsed.path or "/", parsed.query, ""))


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


class EvidenceReference(PilotModel):
    """Bounded reference to a result returned by an existing read Action."""

    source_id: str = Field(min_length=1, max_length=256)
    url: str = Field(default="", max_length=2048)
    title: str = Field(default="", max_length=512)
    excerpt: str = Field(default="", max_length=1600)
    provenance: Literal["search_snippet", "fetched_page"] = "search_snippet"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("observed_at")
    @classmethod
    def require_aware_observation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence observation time must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        if not value:
            return ""
        normalized = normalize_evidence_url(value)
        if not normalized:
            raise ValueError("evidence URL must be a credential-free HTTP(S) URL")
        return normalized


class ResearchFinding(PilotModel):
    """One claim with explicit citations and a quote anchor from a fetched page."""

    claim: str = Field(min_length=1, max_length=4000)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    supporting_source_id: str = Field(default="", max_length=256)
    supporting_quote: str = Field(default="", max_length=1000)

    @field_validator("claim")
    @classmethod
    def reject_inline_citations(cls, value: str) -> str:
        """Prevent model-authored links/citations from bypassing ID rendering."""
        if re.search(r"https?://|www\.|\[[^\]]+\]\(", value, re.IGNORECASE):
            raise ValueError("research claims must use source_ids for citations")
        return value

    @field_validator("source_ids")
    @classmethod
    def require_distinct_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("research claim source_ids must be unique")
        return value


class ResearchBrief(PilotModel):
    """Research claims, each bound to its own observed source identifiers."""

    question: str = Field(min_length=1, max_length=MAX_PILOT_QUESTION_CHARS)
    findings: tuple[ResearchFinding, ...] = Field(min_length=1, max_length=50)
    limitations: tuple[Annotated[str, Field(min_length=1, max_length=1000)], ...] = (
        Field(default=(), max_length=30)
    )

    @field_validator("limitations")
    @classmethod
    def reject_unrenderable_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep caveats bounded and prevent inline links bypassing citations."""
        if any(
            re.search(r"https?://|www\.|\[[^\]]+\]\(", item, re.IGNORECASE)
            for item in value
        ):
            raise ValueError("research limitations must not contain URLs or citations")
        return value


class ConversationalReply(PilotModel):
    """Short, non-factual answer for a request that needs no external research."""

    answer: str = Field(min_length=1, max_length=4000)


PilotOutput = Union[ResearchBrief, ConversationalReply]


def _normalize_support_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def evidence_is_fresh(
    reference: EvidenceReference, *, now: Optional[datetime] = None
) -> bool:
    """Check host observation age; this is not a source publication date."""

    observed_at = reference.observed_at
    current_time = now or datetime.now(timezone.utc)
    age_seconds = (current_time - observed_at).total_seconds()
    return (
        -MAX_PILOT_EVIDENCE_CLOCK_SKEW_SECONDS
        <= age_seconds
        <= MAX_PILOT_EVIDENCE_AGE_SECONDS
    )


def output_user_text(
    output: PilotOutput, evidence: Sequence[EvidenceReference] = ()
) -> str:
    """Render only typed claims and citations resolved from observed results."""

    if isinstance(output, ConversationalReply):
        return output.answer
    references = {item.source_id: item for item in evidence}
    rendered: list[str] = []
    citation_numbers: dict[str, int] = {}
    for finding in output.findings:
        if finding.supporting_source_id not in finding.source_ids:
            raise ValueError("research finding has no cited supporting source")
        supporting_reference = references.get(finding.supporting_source_id)
        if (
            supporting_reference is None
            or supporting_reference.provenance != "fetched_page"
        ):
            raise ValueError("research finding requires a successfully fetched source")
        if not evidence_is_fresh(supporting_reference):
            raise ValueError("research finding supporting source is stale")
        supporting_quote = _normalize_support_text(finding.supporting_quote)
        excerpt = _normalize_support_text(supporting_reference.excerpt)
        if not supporting_quote or supporting_quote not in excerpt:
            raise ValueError(
                "research finding quote must match the fetched source excerpt"
            )
        citations: list[str] = []
        for source_id in finding.source_ids:
            reference = references.get(source_id)
            if (
                reference is None
                or not reference.url
                or reference.provenance != "fetched_page"
                or not evidence_is_fresh(reference)
            ):
                raise ValueError(
                    f"research finding cites unavailable fetched source {source_id!r}"
                )
            number = citation_numbers.setdefault(source_id, len(citation_numbers) + 1)
            url = quote(reference.url, safe=":/?&=#%.-_~")
            observed = reference.observed_at.strftime("%Y-%m-%d %H:%M UTC")
            citations.append(f"[Source {number}, observed {observed}](<{url}>)")
        rendered.append(f"- {finding.claim} {' '.join(citations)}")
    if output.limitations:
        rendered.append("Model-reported limitations (not independently verified):")
        rendered.extend(f"- {limitation}" for limitation in output.limitations)
    return "\n".join(rendered)


class PilotSnapshot(PilotModel):
    """Versioned task payload persisted through the existing TaskStore."""

    schema_version: Literal[6] = 6
    driver: Literal["capability_pilot"] = "capability_pilot"
    caller: PilotCaller
    skill_id: str = Field(min_length=1, max_length=128)
    skill_digest: str = Field(min_length=1, max_length=128)
    config_digest: str = Field(min_length=1, max_length=128)
    status: Literal[
        "running",
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
    reported_input_tokens_used: int = Field(default=0, ge=0)
    reported_output_tokens_used: int = Field(default=0, ge=0)
    estimated_input_tokens_used: int = Field(default=0, ge=0)
    estimated_output_tokens_used: int = Field(default=0, ge=0)
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
