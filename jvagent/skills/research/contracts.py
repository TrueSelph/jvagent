"""Typed evidence contracts owned by the research skill."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PILOT_QUESTION_CHARS = 20_000
MAX_PILOT_EVIDENCE_AGE_SECONDS = 86_400
MAX_PILOT_EVIDENCE_CLOCK_SKEW_SECONDS = 300

_CREDENTIAL_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "awsaccesskeyid",
    "client_secret",
    "credential",
    "googleaccessid",
    "key",
    "password",
    "passwd",
    "secret",
    "sig",
    "signature",
    "token",
    "x_amz_algorithm",
    "x_amz_credential",
    "x_amz_date",
    "x_amz_expires",
    "x_amz_security_token",
    "x_amz_signature",
    "x_goog_algorithm",
    "x_goog_credential",
    "x_goog_date",
    "x_goog_expires",
    "x_goog_signature",
}


def normalize_evidence_url(value: str) -> str:
    """Return a canonical HTTP(S) URL without user-info or credential queries."""

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
    safe_query = urlencode(
        [
            (key, query_value)
            for key, query_value in parse_qsl(
                parsed.query, keep_blank_values=True, strict_parsing=False
            )
            if key.casefold().replace("-", "_") not in _CREDENTIAL_QUERY_KEYS
        ]
    )
    return urlunsplit((scheme, host, parsed.path or "/", safe_query, ""))


class ResearchModel(BaseModel):
    """Base for persisted or model-produced research data."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class EvidenceReference(ResearchModel):
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


def _contains_inline_citation(value: str) -> bool:
    """Reject model-authored citation markers reserved for host rendering."""

    return bool(
        re.search(
            r"https?://|www\.|\[[^\]]+\]\(|"
            r"\[\s*(?:source\s*)?#?\d+(?:\s*[,;][^\]]*)?\s*\]|"
            r"\bsource\s*#?\s*\d+\b",
            value,
            re.IGNORECASE,
        )
    )


class ResearchFinding(ResearchModel):
    """One claim with explicit citations and a quote anchor from a fetched page."""

    claim: str = Field(min_length=1, max_length=4000)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    supporting_source_id: str = Field(default="", max_length=256)
    supporting_quote: str = Field(default="", max_length=1000)

    @field_validator("claim")
    @classmethod
    def reject_inline_citations(cls, value: str) -> str:
        """Prevent model-authored links/citations from bypassing ID rendering."""
        if _contains_inline_citation(value):
            raise ValueError("research claims must use source_ids for citations")
        return value

    @field_validator("source_ids")
    @classmethod
    def require_distinct_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("research claim source_ids must be unique")
        return value


class ResearchBrief(ResearchModel):
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
        if any(_contains_inline_citation(item) for item in value):
            raise ValueError("research limitations must not contain URLs or citations")
        return value


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


def render_research(
    output: ResearchBrief, evidence: Sequence[EvidenceReference] = ()
) -> str:
    """Render only typed claims and citations resolved from observed results."""

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
        # Keep every line in quoted voice; source text never becomes a host
        # instruction or an independently validated citation.
        rendered.extend("> " + line for line in finding.supporting_quote.splitlines())
        rendered.append(f"- {finding.claim} {' '.join(citations)}")
    if output.limitations:
        rendered.append("Model-reported limitations (not independently verified):")
        rendered.extend(f"- {limitation}" for limitation in output.limitations)
    return "\n".join(rendered)
