#!/usr/bin/env python3
"""Read disposable graph/UI evidence; report an incomplete gate as incomplete.

No model, tool, browser or turn execution is implemented here. Input graph exports
come from the isolated workload apps; they must not include production user data.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def check_case(case: dict[str, Any], context: dict[str, Any]) -> bool | None:
    """Check mechanical conditions; leave semantic judgments to review."""
    text = context.get("response")
    if not isinstance(text, str) or not text or not context.get("emitted"):
        return False
    expected = case["expected"]
    if "exact_text" in expected:
        return text == expected["exact_text"]
    if "json" in expected:
        try:
            return json.loads(text) == expected["json"]
        except ValueError:
            return False  # Markdown fences are a failure, not stripped away.
    if case["id"] == "recall-facts":
        return all(value in text for value in expected["contains"])
    if case["id"] == "remember-facts":
        return bool(context.get("closed") and context.get("conversation_id"))
    if case["id"] == "missing-fact":
        return None  # A missing-information phrase does not prove no fabrication.
    return None  # Other fixtures require their own state/effect checks.


def distribution(values: list[float | int]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(values),
        "median": statistics.median(ordered),
        "p95_nearest_rank": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "total": sum(values),
    }


def build_report(
    manifest: dict[str, Any],
    exports: dict[str, list[dict[str, Any]]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Correlate every UI observation with its exact persisted Interaction."""
    cases = {case["id"]: case for case in manifest["cases"]}
    if len(cases) != len(manifest["cases"]):
        raise ValueError("Duplicate scenario IDs")
    repeats = manifest["replicates"]
    expected = len(cases) * repeats
    if expected != manifest["measured_turns_per_implementation"]:
        raise ValueError("Manifest denominator does not match its scenarios")
    if any(o["implementation"] not in exports for o in observations):
        raise ValueError("Unknown implementation in browser observations")
    result: dict[str, Any] = {
        "schema": "jvagent.harness-workload-report/v1",
        "independent_quality_review": "pending",
        "optimization_gate_evaluable": False,
        "latency_scope": "Persisted backend Interaction duration, not browser end-to-end latency",
        "implementations": {},
    }
    for label, rows in exports.items():
        indexed = {row["id"]: row["context"] for row in rows}
        if len(indexed) != len(rows):
            raise ValueError("Duplicate graph Interaction IDs")
        observed = [o for o in observations if o["implementation"] == label]
        identities: set[tuple[str, int]] = set()
        interaction_ids: set[str] = set()
        records = []
        for observation in observed:
            identity = (observation["case_id"], observation["replicate"])
            if (
                identity in identities
                or observation["interaction_id"] in interaction_ids
            ):
                raise ValueError("Duplicate scenario or Interaction observation")
            case = cases[identity[0]]
            if not 1 <= identity[1] <= repeats:
                raise ValueError("Replicate is outside the frozen workload")
            context = indexed[observation["interaction_id"]]
            if context.get("utterance") != case["prompt"]:
                raise ValueError("UI observation does not match the persisted prompt")
            if not observation.get("visible"):
                raise ValueError("Missing browser observation")
            identities.add(identity)
            interaction_ids.add(observation["interaction_id"])
            start, end = context.get("started_at"), context.get("completed_at")
            elapsed = (
                (
                    datetime.fromisoformat(end) - datetime.fromisoformat(start)
                ).total_seconds()
                if start and end
                else None
            )
            usage = context.get("usage", {})
            receipts = [
                event["data"]
                for event in context.get("observability_metrics", [])
                if event.get("event_type") == "model_call"
            ]
            records.append(
                {
                    "case_id": identity[0],
                    "replicate": identity[1],
                    "interaction_id": observation["interaction_id"],
                    "session_id": context.get("session_id"),
                    "response": context.get("response"),
                    "closed": context.get("closed"),
                    "emitted": context.get("emitted"),
                    "case_check": check_case(case, context),
                    "backend_seconds": elapsed,
                    "usage": usage,
                    "models_recorded": sorted({r["model"] for r in receipts}),
                    "model_receipt_count": len(receipts),
                    "native_run": context.get("native_run"),
                    "native_snapshots": len(
                        context.get("native_steps", {}).get("snapshots", [])
                    ),
                    "ui_observation": observation["visible"],
                }
            )
        if interaction_ids != set(indexed):
            raise ValueError("Uncorrelated persisted turns must not be dropped")
        checks = Counter(r["case_check"] for r in records)
        result["implementations"][label] = {
            "required_measured_turns": expected,
            "measured_turns": len(records),
            "missing_turns": expected - len(records),
            "unmeasured_scenarios": sorted(
                set(cases) - {r["case_id"] for r in records}
            ),
            "case_checks_passed": checks[True],
            "case_checks_failed": checks[False],
            "case_checks_pending": checks[None],
            "full_gate_complete": False,
            "backend_seconds_all_observed": distribution(
                [
                    r["backend_seconds"]
                    for r in records
                    if r["backend_seconds"] is not None
                ]
            ),
            "tokens_all_observed": distribution(
                [
                    r["usage"]["total_tokens"]
                    for r in records
                    if "total_tokens" in r["usage"]
                ]
            ),
            "known_recorded_model_calls": sum(
                r["usage"].get("model_call_count", 0) for r in records
            ),
            "missing_call_count_turns": sum(
                "model_call_count" not in r["usage"] for r in records
            ),
            "known_estimated_usd": sum(
                r["usage"].get("estimated_cost_usd", 0) for r in records
            ),
            "missing_estimated_cost_turns": sum(
                "estimated_cost_usd" not in r["usage"] for r in records
            ),
            "missing_unknown_cost_reporting_turns": sum(
                "unknown_cost_call_count" not in r["usage"] for r in records
            ),
            "reported_unknown_cost_events": (
                sum(r["usage"]["unknown_cost_call_count"] for r in records)
                if all("unknown_cost_call_count" in r["usage"] for r in records)
                else None
            ),
            "cost_provenance": "Estimates, not provider invoices; missing usage remains missing in each record",
            "records": records,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--legacy-export", type=Path, required=True)
    parser.add_argument("--native-export", type=Path, required=True)
    parser.add_argument("--ui-observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        yaml.safe_load(args.manifest.read_text()),
        {
            "legacy": json.loads(args.legacy_export.read_text()),
            "native": json.loads(args.native_export.read_text()),
        },
        json.loads(args.ui_observations.read_text()),
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for label, data in report["implementations"].items():
        print(label, {k: v for k, v in data.items() if k != "records"})


if __name__ == "__main__":
    main()
