"""Guard the paired, fixed-source research evaluation manifest."""

from pathlib import Path

import yaml


def test_research_eval_manifest_has_ten_complete_independent_cases() -> None:
    path = Path(__file__).parent / "eval" / "research-cases.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert manifest["schema"] == "jvagent.pilot-eval/v1"
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
