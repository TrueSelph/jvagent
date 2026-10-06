"""Build driver-blind paired research packets for human quality review."""

from __future__ import annotations

import random
import re
from collections.abc import Mapping, Sequence
from typing import Any

from jvagent.action.orchestrator.pilot.contracts import normalize_evidence_url
from jvagent.action.orchestrator.pilot.runtime import PilotEvidenceCollector

BLIND_PACKET_SCHEMA = "jvagent.pilot-blind-review/v1"
ANNOTATION_SCHEMA = "jvagent.pilot-review-annotations/v1"
_URL_PATTERN = re.compile(r"https?://[^\s)>]+", re.IGNORECASE)


def _records_by_pair(
    report: Mapping[str, Any], driver: str
) -> dict[tuple[str, int], Mapping[str, Any]]:
    records = report.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError(f"{driver} report must contain a records list")
    indexed: dict[tuple[str, int], Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError(f"{driver} report contains a malformed record")
        case_id = record.get("case_id")
        replicate = record.get("replicate")
        if (
            not isinstance(case_id, str)
            or not case_id
            or type(replicate) is not int
            or replicate < 1
        ):
            raise ValueError(f"{driver} record has an invalid case/replicate key")
        key = (case_id, replicate)
        if key in indexed:
            raise ValueError(f"{driver} report repeats case/replicate {key!r}")
        indexed[key] = record
    return indexed


def _pilot_claims(output: Any, aliases: Mapping[str, str]) -> str:
    if not isinstance(output, Mapping):
        return "[No substantive answer returned]"
    findings = output.get("findings")
    if isinstance(findings, Sequence) and not isinstance(findings, (str, bytes)):
        lines = []
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            claim = finding.get("claim")
            if not isinstance(claim, str):
                continue
            source_ids = finding.get("source_ids", [])
            labels = (
                [
                    aliases.get(str(source_id), "[Unmatched source reference]")
                    for source_id in source_ids
                ]
                if isinstance(source_ids, Sequence)
                and not isinstance(source_ids, (str, bytes))
                else []
            )
            citation = f" ({'; '.join(labels)})" if labels else " (No source reference)"
            lines.append(claim + citation)
        limitations = output.get("limitations", [])
        if isinstance(limitations, Sequence) and not isinstance(
            limitations, (str, bytes)
        ):
            lines.extend(
                f"Limitation: {item}" for item in limitations if isinstance(item, str)
            )
        return "\n".join(lines) if lines else "[No substantive answer returned]"
    answer = output.get("answer")
    if isinstance(answer, str):
        return answer
    return "[No substantive answer returned]"


def _legacy_answer(record: Mapping[str, Any]) -> str:
    output = record.get("output")
    if isinstance(output, str) and output.strip():
        return output
    published = record.get("published")
    if isinstance(published, Sequence) and not isinstance(published, (str, bytes)):
        return "\n".join(
            item for item in published if isinstance(item, str) and item.strip()
        )
    return "[No substantive answer returned]"


def _mask_urls(text: str, source_aliases: Mapping[str, str]) -> str:
    masked = text
    for url, alias in sorted(
        source_aliases.items(), key=lambda item: len(item[0]), reverse=True
    ):
        masked = masked.replace(url, alias)
    return _URL_PATTERN.sub("[Unmapped URL]", masked)


def prepare_blind_review(
    manifest: Mapping[str, Any],
    pilot_report: Mapping[str, Any],
    legacy_report: Mapping[str, Any],
    *,
    seed: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a reviewer packet and a separate key mapping candidates to drivers.

    Keep the key in a separate file or with the evaluation custodian. It must
    not be distributed with the reviewer packet.
    """

    if manifest.get("schema") != "jvagent.pilot-eval/v1":
        raise ValueError("unsupported research evaluation manifest")
    rubric = manifest.get("rubric")
    cases = manifest.get("cases")
    if not isinstance(rubric, Mapping) or not isinstance(cases, Sequence):
        raise ValueError("manifest requires rubric and cases")
    dimensions = rubric.get("dimensions")
    if not isinstance(dimensions, Mapping) or not dimensions:
        raise ValueError("manifest rubric requires dimensions")
    pilot = _records_by_pair(pilot_report, "pilot")
    legacy = _records_by_pair(legacy_report, "legacy")
    if set(pilot) != set(legacy):
        raise ValueError("pilot and legacy reports must contain identical paired runs")
    case_by_id = {
        case.get("id"): case
        for case in cases
        if isinstance(case, Mapping) and isinstance(case.get("id"), str)
    }
    if not case_by_id:
        raise ValueError("manifest contains no identified cases")
    if any(case_id not in case_by_id for case_id, _ in pilot):
        raise ValueError("evaluation report references a case missing from manifest")

    rng = random.Random(seed)
    packet_pairs: list[dict[str, Any]] = []
    key_pairs: list[dict[str, Any]] = []
    for index, (case_id, replicate) in enumerate(sorted(pilot), start=1):
        case = case_by_id[case_id]
        raw_sources = case.get("sources")
        if not isinstance(raw_sources, Sequence) or isinstance(
            raw_sources, (str, bytes)
        ):
            raise ValueError(f"case {case_id!r} has no source list")
        sources = [source for source in raw_sources if isinstance(source, Mapping)]
        source_labels = [f"Source {chr(ord('A') + i)}" for i in range(len(sources))]
        source_pairs = list(zip(sources, source_labels))
        rng.shuffle(source_pairs)

        id_aliases: dict[str, str] = {}
        url_aliases: dict[str, str] = {}
        reviewer_sources = []
        for source, label in source_pairs:
            source_id = source.get("id")
            url = source.get("url")
            body = source.get("text")
            if (
                not isinstance(source_id, str)
                or not isinstance(url, str)
                or not isinstance(body, str)
            ):
                raise ValueError(f"case {case_id!r} contains an invalid source")
            canonical_url = normalize_evidence_url(url)
            if not canonical_url:
                raise ValueError(f"case {case_id!r} contains an unsafe source URL")
            id_aliases[PilotEvidenceCollector._source_id(source_id, canonical_url)] = (
                label
            )
            id_aliases[source_id] = label
            url_aliases[url] = label
            url_aliases[canonical_url] = label
            reviewer_sources.append({"label": label, "text": body})

        candidates = [
            (
                "pilot",
                _pilot_claims(pilot[(case_id, replicate)].get("output"), id_aliases),
            ),
            ("legacy", _legacy_answer(legacy[(case_id, replicate)])),
        ]
        rng.shuffle(candidates)
        pair_id = f"pair-{index:04d}"
        candidate_items = []
        candidate_key = {}
        for candidate_index, (driver, answer) in enumerate(candidates, start=1):
            candidate_id = f"response-{candidate_index}"
            candidate_items.append(
                {
                    "candidate_id": candidate_id,
                    "answer": _mask_urls(answer, url_aliases),
                }
            )
            candidate_key[candidate_id] = driver
        packet_pairs.append(
            {
                "pair_id": pair_id,
                "case_id": case_id,
                "replicate": replicate,
                "prompt": case["prompt"],
                "sources": reviewer_sources,
                "responses": candidate_items,
            }
        )
        key_pairs.append({"pair_id": pair_id, "drivers": candidate_key})

    rng.shuffle(packet_pairs)
    rubric_dimensions = list(dimensions)
    packet = {
        "schema": BLIND_PACKET_SCHEMA,
        "rubric": rubric,
        "annotation_template": {
            "schema": ANNOTATION_SCHEMA,
            "ratings": [
                {
                    "pair_id": item["pair_id"],
                    "candidate_scores": {
                        candidate["candidate_id"]: dict.fromkeys(rubric_dimensions)
                        for candidate in item["responses"]
                    },
                    "critical_failure": None,
                    "rationale": "",
                }
                for item in packet_pairs
            ],
        },
        "pairs": packet_pairs,
    }
    answer_key = {
        "schema": "jvagent.pilot-blind-review-key/v1",
        "pairs": key_pairs,
    }
    return packet, answer_key


def validate_blind_annotations(
    packet: Mapping[str, Any], annotations: Mapping[str, Any]
) -> None:
    """Reject incomplete, out-of-range, or mis-keyed human ratings."""

    if packet.get("schema") != BLIND_PACKET_SCHEMA:
        raise ValueError("unsupported blind review packet")
    if annotations.get("schema") != ANNOTATION_SCHEMA:
        raise ValueError("unsupported annotation schema")
    template = packet.get("annotation_template")
    expected_ratings = (
        template.get("ratings") if isinstance(template, Mapping) else None
    )
    actual_ratings = annotations.get("ratings")
    if not isinstance(expected_ratings, Sequence) or not isinstance(
        actual_ratings, Sequence
    ):
        raise ValueError("annotations require a ratings list")
    expected = {item["pair_id"]: item for item in expected_ratings}
    actual: dict[str, Mapping[str, Any]] = {}
    for rating in actual_ratings:
        if not isinstance(rating, Mapping) or not isinstance(
            rating.get("pair_id"), str
        ):
            raise ValueError("annotation contains a malformed pair")
        pair_id = rating["pair_id"]
        if pair_id in actual:
            raise ValueError(f"duplicate annotation for {pair_id}")
        actual[pair_id] = rating
    if set(actual) != set(expected):
        raise ValueError("annotations must cover every packet pair exactly once")

    for pair_id, expected_rating in expected.items():
        rating = actual[pair_id]
        scores = rating.get("candidate_scores")
        expected_scores = expected_rating.get("candidate_scores")
        if not isinstance(scores, Mapping) or not isinstance(expected_scores, Mapping):
            raise ValueError(f"{pair_id} requires candidate scores")
        if set(scores) != set(expected_scores):
            raise ValueError(f"{pair_id} candidate set does not match the packet")
        for candidate_id, dimensions in expected_scores.items():
            candidate_scores = scores[candidate_id]
            if not isinstance(candidate_scores, Mapping) or set(
                candidate_scores
            ) != set(dimensions):
                raise ValueError(
                    f"{pair_id}/{candidate_id} must score every rubric dimension"
                )
            for score in candidate_scores.values():
                if type(score) is not int or score not in {0, 1, 2}:
                    raise ValueError(
                        f"{pair_id}/{candidate_id} scores must be integers from 0 to 2"
                    )
        if type(rating.get("critical_failure")) is not bool:
            raise ValueError(f"{pair_id} must record critical_failure as true or false")
        rationale = rating.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"{pair_id} requires a reviewer rationale")
