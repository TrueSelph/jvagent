"""Surface-level checks for the fixed-evidence model evaluation.

These checks catch missing required phrases, prohibited phrases, citation-ID
coverage, and factual-case output-mode escapes. They do not judge entailment,
source quality, or calibration; those dimensions still require blinded human
review against the manifest rubric.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from jvagent.action.orchestrator.pilot.contracts import normalize_evidence_url


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def score_case_output(
    case: Mapping[str, Any], output: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return reproducible lexical/citation checks without claiming semantics."""

    if not isinstance(output, Mapping):
        output = {}
    findings = output.get("findings")
    if isinstance(findings, Sequence) and not isinstance(findings, (str, bytes)):
        finding_items = [item for item in findings if isinstance(item, Mapping)]
        claims = [str(item.get("claim", "")) for item in finding_items]
        output_mode = "research_brief"
    else:
        answer = output.get("answer")
        claims = [str(answer)] if isinstance(answer, str) else []
        finding_items = []
        output_mode = "conversational_reply" if isinstance(answer, str) else "invalid"

    source_aliases: dict[str, str] = {}
    source_texts: dict[str, str] = {}
    sources = case.get("sources", [])
    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)):
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            source_id = str(source.get("id") or "").strip()
            source_text = source.get("text")
            if not source_id or not isinstance(source_text, str):
                continue
            source_aliases[source_id] = source_id
            source_texts[source_id] = source_text
            normalized_url = normalize_evidence_url(str(source.get("url") or ""))
            if normalized_url and re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", source_id):
                digest = sha256(normalized_url.encode("utf-8")).hexdigest()[:32]
                source_aliases[f"action:{source_id}:{digest}"] = source_id

    cited_source_ids: set[str] = set()
    missing_supporting_sources: list[int] = []
    unobserved_supporting_quotes: list[int] = []
    for index, item in enumerate(finding_items):
        raw_ids = item.get("source_ids", [])
        finding_source_ids = (
            [str(source_id) for source_id in raw_ids]
            if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes))
            else []
        )
        cited_source_ids.update(
            source_aliases[source_id]
            for source_id in finding_source_ids
            if source_id in source_aliases
        )
        supporting_source = str(item.get("supporting_source_id") or "")
        source_id = source_aliases.get(supporting_source)
        if source_id is None or supporting_source not in finding_source_ids:
            missing_supporting_sources.append(index)
            continue
        quote = item.get("supporting_quote")
        quote_text = _normalize(str(quote)) if isinstance(quote, str) else ""
        if not quote_text or quote_text not in _normalize(source_texts[source_id]):
            unobserved_supporting_quotes.append(index)

    rendered = _normalize(
        " ".join(claims + [str(x) for x in output.get("limitations", [])])
    )
    missing_required = [
        phrase
        for phrase in case.get("required_claims", [])
        if _normalize(str(phrase)) not in rendered
    ]
    present_prohibited = [
        phrase
        for phrase in case.get("prohibited_claims", [])
        if _normalize(str(phrase)) in rendered
    ]
    missing_sources = sorted(
        set(map(str, case.get("required_source_ids", []))) - cited_source_ids
    )
    failures = []
    if output_mode != "research_brief":
        failures.append("factual_case_used_non_research_output")
    if missing_required:
        failures.append("required_claim_phrase_missing")
    if present_prohibited:
        failures.append("prohibited_claim_phrase_present")
    if missing_sources:
        failures.append("required_source_id_missing")
    if missing_supporting_sources:
        failures.append("supporting_source_missing_or_not_cited")
    if unobserved_supporting_quotes:
        failures.append("supporting_quote_not_observed")
    return {
        "passed": not failures,
        "output_mode": output_mode,
        "missing_required_claim_phrases": missing_required,
        "present_prohibited_claim_phrases": present_prohibited,
        "missing_required_source_ids": missing_sources,
        "findings_missing_supporting_sources": missing_supporting_sources,
        "findings_with_unobserved_supporting_quotes": unobserved_supporting_quotes,
        "failures": failures,
        "semantic_entailment_assessed": False,
    }
