"""Driver-blind annotation packet tests for the fixed-evidence evaluation."""

import json
from copy import deepcopy

import pytest

from jvagent.action.orchestrator.pilot.contracts import normalize_evidence_url
from jvagent.action.orchestrator.pilot.runtime import PilotEvidenceCollector
from tests.action.orchestrator.pilot.eval.blind_review import (
    ANNOTATION_SCHEMA,
    prepare_blind_review,
    validate_blind_annotations,
)


def _inputs():
    source = {
        "id": "SRC-A",
        "url": "HTTPS://EXAMPLE.TEST/report#top",
        "text": "The report records 240 operating hours.",
    }
    source_id = PilotEvidenceCollector._source_id(
        source["id"], normalize_evidence_url(source["url"])
    )
    manifest = {
        "schema": "jvagent.pilot-eval/v1",
        "rubric": {"dimensions": {"evidence_support": "0-2", "calibration": "0-2"}},
        "cases": [
            {
                "id": "interval",
                "prompt": "What interval does the report give?",
                "sources": [source],
            }
        ],
    }
    pilot = {
        "provider": "secret-pilot-provider",
        "records": [
            {
                "driver": "pilot",
                "model": "secret-pilot-model",
                "case_id": "interval",
                "replicate": 1,
                "output": {
                    "findings": [
                        {
                            "claim": "Inspection is recommended every 240 operating hours.",
                            "source_ids": [source_id],
                        }
                    ],
                    "limitations": [],
                },
            }
        ],
    }
    legacy = {
        "provider": "secret-legacy-provider",
        "records": [
            {
                "driver": "legacy",
                "model": "secret-legacy-model",
                "case_id": "interval",
                "replicate": 1,
                "output": "The interval is 240 operating hours. See https://example.test/report#top",
            }
        ],
    }
    return manifest, pilot, legacy


def test_blind_packet_strips_run_metadata_and_maps_both_driver_citations():
    manifest, pilot, legacy = _inputs()
    packet, answer_key = prepare_blind_review(manifest, pilot, legacy, seed=3)

    serialized_packet = json.dumps(packet)
    for secret in (
        "secret-pilot-provider",
        "secret-legacy-provider",
        "secret-pilot-model",
        "secret-legacy-model",
        '"driver"',
    ):
        assert secret not in serialized_packet
    pair = packet["pairs"][0]
    labels = {source["label"] for source in pair["sources"]}
    assert len(labels) == 1
    assert all(
        any(label in response["answer"] for label in labels)
        for response in pair["responses"]
    )
    assert pair["responses"][0]["candidate_id"] in answer_key["pairs"][0]["drivers"]
    assert set(answer_key["pairs"][0]["drivers"].values()) == {"legacy", "pilot"}
    assert "drivers" not in packet
    assert "provider" not in packet
    assert "model" not in packet


def test_blind_packet_rejects_unpaired_or_duplicate_records():
    manifest, pilot, legacy = _inputs()
    legacy["records"].clear()
    with pytest.raises(ValueError, match="identical paired runs"):
        prepare_blind_review(manifest, pilot, legacy, seed=2)

    manifest, pilot, legacy = _inputs()
    pilot["records"].append(deepcopy(pilot["records"][0]))
    with pytest.raises(ValueError, match="repeats case/replicate"):
        prepare_blind_review(manifest, pilot, legacy, seed=2)


def test_blind_annotation_validator_accepts_complete_ratings():
    manifest, pilot, legacy = _inputs()
    packet, _ = prepare_blind_review(manifest, pilot, legacy, seed=2)
    annotations = deepcopy(packet["annotation_template"])
    for rating in annotations["ratings"]:
        rating["candidate_scores"] = {
            candidate_id: dict.fromkeys(dimensions, 2)
            for candidate_id, dimensions in rating["candidate_scores"].items()
        }
        rating["critical_failure"] = False
        rating["rationale"] = "Both responses are supported by the supplied report."
    validate_blind_annotations(packet, annotations)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda rating: rating["candidate_scores"].pop(
                next(iter(rating["candidate_scores"]))
            ),
            "candidate set",
        ),
        (
            lambda rating: next(iter(rating["candidate_scores"].values())).__setitem__(
                "evidence_support", True
            ),
            "integers from 0 to 2",
        ),
        (
            lambda rating: rating.__setitem__("rationale", " "),
            "requires a reviewer rationale",
        ),
    ],
)
def test_blind_annotation_validator_rejects_incomplete_or_invalid_scores(
    mutate, message
):
    manifest, pilot, legacy = _inputs()
    packet, _ = prepare_blind_review(manifest, pilot, legacy, seed=2)
    annotations = deepcopy(packet["annotation_template"])
    rating = annotations["ratings"][0]
    rating["candidate_scores"] = {
        candidate_id: dict.fromkeys(dimensions, 1)
        for candidate_id, dimensions in rating["candidate_scores"].items()
    }
    rating["critical_failure"] = False
    rating["rationale"] = "A complete initial rationale."
    mutate(rating)
    with pytest.raises(ValueError, match=message):
        validate_blind_annotations(packet, annotations)


def test_annotation_schema_is_versioned():
    assert ANNOTATION_SCHEMA == "jvagent.pilot-review-annotations/v1"
