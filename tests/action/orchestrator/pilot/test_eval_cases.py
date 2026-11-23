"""Guard the paired, fixed-source research evaluation manifest."""

from hashlib import sha256
from pathlib import Path

import yaml

from jvagent.action.orchestrator.pilot.contracts import normalize_evidence_url
from tests.action.orchestrator.pilot.eval.scoring import score_case_output


def test_research_eval_manifest_has_ten_complete_independent_cases() -> None:
    path = Path(__file__).parent / "eval" / "research-cases.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert manifest["schema"] == "jvagent.pilot-eval/v1"
    protocol = path.with_name(manifest["blind_review_protocol"])
    assert protocol.is_file()
    assert "exact quote can still fail" in protocol.read_text(encoding="utf-8")
    assert "independent human review" in protocol.read_text(encoding="utf-8")
    assert set(manifest["rubric"]["dimensions"]) == {
        "evidence_support",
        "citation_coverage",
        "calibration",
        "task_adherence",
    }
    cases = manifest["cases"]
    assert len(cases) == 10
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        source_ids = {source["id"] for source in case["sources"]}
        assert case["prompt"].strip()
        assert source_ids
        assert case["required_claims"]
        assert case["prohibited_claims"]
        assert case["required_source_ids"]
        assert set(case["required_source_ids"]).issubset(source_ids)
        assert all(source["url"].startswith("https://") for source in case["sources"])


def test_surface_evaluator_flags_unsupported_claims_missing_sources_and_reply_escape():
    case = {
        "required_claims": ["the archive sources conflict", "1984", "1986"],
        "prohibited_claims": ["a single verified year"],
        "required_source_ids": ["SRC-A", "SRC-B"],
        "sources": [
            {
                "id": "SRC-A",
                "url": "https://fixtures.example.test/archive/a",
                "text": "The municipal archive lists the bridge opening as 1984.",
            },
            {
                "id": "SRC-B",
                "url": "https://fixtures.example.test/archive/b",
                "text": "The engineering summary lists the bridge opening as 1986.",
            },
        ],
    }

    passed = score_case_output(
        case,
        {
            "question": "opening year",
            "findings": [
                {
                    "claim": "The archive sources conflict: one gives 1984 and the other 1986.",
                    "source_ids": ["SRC-A", "SRC-B"],
                    "supporting_source_id": "SRC-A",
                    "supporting_quote": "The municipal archive lists the bridge opening as 1984.",
                }
            ],
            "limitations": [],
        },
    )
    assert passed["passed"] is True
    assert passed["mechanical_checks_passed"] is True
    assert passed["fully_qualified"] is False
    assert passed["qualification_status"] == "pending_blind_review"
    assert passed["findings_with_unobserved_supporting_quotes"] == []
    assert passed["semantic_entailment_assessed"] is False

    escaped = score_case_output(
        case,
        {"answer": "The sources establish a single verified year: 1984."},
    )
    assert escaped["passed"] is False
    assert "factual_case_used_non_research_output" in escaped["failures"]
    assert "prohibited_claim_phrase_present" in escaped["failures"]
    assert "required_source_id_missing" in escaped["failures"]


def test_exact_quote_with_invalid_causal_inference_still_requires_blind_review():
    case = {
        "required_claims": ["revenue rose after launch"],
        "prohibited_claims": [],
        "required_source_ids": ["SRC-REVENUE"],
        "sources": [
            {
                "id": "SRC-REVENUE",
                "url": "https://fixtures.example.test/revenue/quarterly",
                "text": (
                    "Revenue rose after launch. The record does not establish "
                    "what caused the increase."
                ),
            }
        ],
    }
    output = {
        "findings": [
            {
                "claim": (
                    "Revenue rose after launch; therefore the launch caused "
                    "the rise."
                ),
                "source_ids": ["SRC-REVENUE"],
                "supporting_source_id": "SRC-REVENUE",
                "supporting_quote": "Revenue rose after launch.",
            }
        ]
    }

    result = score_case_output(case, output)

    # Exact quote and phrase checks can pass a non-entailing causal claim.
    # This output must remain explicitly unqualified until blinded semantic
    # reviewers assess it.
    assert result["mechanical_checks_passed"] is True
    assert result["semantic_entailment_assessed"] is False
    assert result["qualification_status"] == "pending_blind_review"
    assert result["fully_qualified"] is False


def test_surface_evaluator_does_not_mistake_missing_claims_for_semantic_validation():
    result = score_case_output(
        {
            "required_claims": ["the vote count is not stated"],
            "prohibited_claims": ["12 votes"],
            "required_source_ids": ["SRC-MINUTES"],
            "sources": [
                {
                    "id": "SRC-MINUTES",
                    "url": "https://fixtures.example.test/minutes/committee",
                    "text": "The committee approved the proposal.",
                }
            ],
        },
        {
            "findings": [
                {
                    "claim": "The meeting approved it, but no vote count was reported.",
                    "source_ids": ["SRC-MINUTES"],
                }
            ]
        },
    )
    assert result["passed"] is False
    assert result["qualification_status"] == "mechanical_failure"
    assert result["missing_required_claim_phrases"] == ["the vote count is not stated"]
    assert result["semantic_entailment_assessed"] is False


def test_surface_evaluator_requires_observed_quotes_and_recognizes_pilot_source_ids():
    case = {
        "required_claims": ["Model A has a 42-litre tank"],
        "prohibited_claims": [],
        "required_source_ids": ["SRC-MODEL-A"],
        "sources": [
            {
                "id": "SRC-MODEL-A",
                "url": "https://fixtures.example.test/models/a",
                "text": "Model A has a 42-litre tank.",
            }
        ],
    }
    source_url = normalize_evidence_url("https://fixtures.example.test/models/a")
    pilot_source_id = (
        "action:SRC-MODEL-A:" + sha256(source_url.encode("utf-8")).hexdigest()[:32]
    )
    output = {
        "findings": [
            {
                "claim": "Model A has a 42-litre tank.",
                "source_ids": [pilot_source_id],
                "supporting_source_id": pilot_source_id,
                "supporting_quote": "Model A has a 42-litre tank.",
            }
        ]
    }

    assert score_case_output(case, output)["passed"] is True

    output["findings"][0]["supporting_quote"] = "Model A has a 52-litre tank."
    result = score_case_output(case, output)
    assert result["passed"] is False
    assert result["findings_with_unobserved_supporting_quotes"] == [0]


def test_surface_evaluator_rejects_missing_or_uncited_supporting_source():
    result = score_case_output(
        {
            "required_claims": [],
            "prohibited_claims": [],
            "required_source_ids": ["SRC-A"],
            "sources": [
                {
                    "id": "SRC-A",
                    "url": "https://fixtures.example.test/a",
                    "text": "Observed text from A.",
                }
            ],
        },
        {
            "findings": [
                {
                    "claim": "A claim",
                    "source_ids": ["SRC-A"],
                    "supporting_source_id": "SRC-B",
                    "supporting_quote": "Observed text from A.",
                }
            ]
        },
    )
    assert result["passed"] is False
    assert result["findings_missing_supporting_sources"] == [0]
