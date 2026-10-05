"""Opt-in live run of the real legacy Orchestrator research loop.

This uses the same fixed evidence manifest and Ollama Cloud model as the pilot
evaluation so the two execution paths can be compared pairwise.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
import yaml

pytest.importorskip("pydantic_ai")

from jvagent.action.model.language.ollama.ollama import OllamaLanguageModelAction
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.tooling.tool import Tool as JVTool
from jvagent.tooling.tool_result import ToolResult

_REAL_LEGACY_RUN_MODEL = OrchestratorInteractAction._run_model
INPUT_PRICE_PER_MILLION = 1.40
OUTPUT_PRICE_PER_MILLION = 4.40
PER_RUN_COST_CEILING_USD = 0.025
TOTAL_COST_CEILING_USD = 0.18
RUNS_PER_CASE = 5


def _estimate_cost(calls: list[dict[str, Any]]) -> float:
    return (
        sum(int(call["prompt_tokens"]) for call in calls) * INPUT_PRICE_PER_MILLION
        + sum(int(call["completion_tokens"]) for call in calls)
        * OUTPUT_PRICE_PER_MILLION
    ) / 1_000_000


@pytest.mark.asyncio
async def test_bounded_live_legacy_research_evaluation(
    make_orchestrator, make_visitor, monkeypatch, publish_log, record_property
):
    if os.environ.get("JVAGENT_RUN_PILOT_LIVE_EVAL") != "1":
        pytest.skip("set JVAGENT_RUN_PILOT_LIVE_EVAL=1 to authorize live model calls")

    manifest_path = Path(__file__).parent / "eval" / "research-cases.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    requested_cases = {
        item.strip()
        for item in os.environ.get("JVAGENT_PILOT_LIVE_EVAL_CASES", "").split(",")
        if item.strip()
    }
    cases = [
        case
        for case in manifest["cases"]
        if not requested_cases or case["id"] in requested_cases
    ]
    if requested_cases and {case["id"] for case in cases} != requested_cases:
        pytest.fail("JVAGENT_PILOT_LIVE_EVAL_CASES contains an unknown case ID")
    replicates = int(
        os.environ.get("JVAGENT_PILOT_LIVE_EVAL_REPEATS", str(RUNS_PER_CASE))
    )
    if not 1 <= replicates <= RUNS_PER_CASE:
        pytest.fail("live evaluation repeats must be between 1 and 5")

    model_action = OllamaLanguageModelAction()
    model_action.api_endpoint = "http://127.0.0.1:11434"
    model_action.model = "glm-5.3:cloud"
    model_action.max_tokens = 1024
    model_action.temperature = 0.0
    model_action.max_retries = 0
    calls: list[dict[str, Any]] = []
    call_attempts = 0
    original_query_messages = OllamaLanguageModelAction.query_messages

    async def record_query_messages(self, *args, **kwargs):
        nonlocal call_attempts
        call_attempts += 1
        started = perf_counter()
        response = await original_query_messages(self, *args, **kwargs)
        usage = response.metrics
        calls.append(
            {
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
                "finish_reason": response.finish_reason,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            }
        )
        return response

    monkeypatch.setattr(
        OllamaLanguageModelAction, "query_messages", record_query_messages
    )

    async def select_model(_self, _gear):
        return model_action, "glm-5.3:cloud", 0.0, 1024, False

    monkeypatch.setattr(OrchestratorInteractAction, "_gear_model", select_model)
    records: list[dict[str, Any]] = []
    cumulative_cost = 0.0

    for case in cases:
        source_by_url = {source["url"]: source for source in case["sources"]}

        async def search(*, query: str, **_kwargs):
            action_calls.append({"tool": "web_search__search", "query": query})
            return ToolResult(
                json.dumps(
                    [
                        {
                            "id": source["id"],
                            "title": source["id"],
                            "link": source["url"],
                            "snippet": source["text"],
                        }
                        for source in case["sources"]
                    ]
                )
            )

        async def fetch(*, url: str, **_kwargs):
            action_calls.append({"tool": "web_fetch__fetch", "url": url})
            source = source_by_url.get(url)
            if source is None:
                raise ValueError("fixture fetch refused an unlisted source URL")
            return ToolResult(source["text"])

        class SerperWebSearchAction:
            def get_class_name(self):
                return "SerperWebSearchAction"

            async def get_version(self):
                return "1.0"

            async def get_tools(self):
                return [
                    JVTool(
                        name="web_search__search",
                        description="Search the fixed evidence sources.",
                        parameters_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                        execute=search,
                    )
                ]

        class WebFetchAction:
            def get_class_name(self):
                return "WebFetchAction"

            async def get_version(self):
                return "1.0"

            async def get_tools(self):
                return [
                    JVTool(
                        name="web_fetch__fetch",
                        description="Fetch full text from a listed source URL.",
                        parameters_schema={
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                            "required": ["url"],
                            "additionalProperties": False,
                        },
                        execute=fetch,
                    )
                ]

        action_calls: list[dict[str, Any]] = []
        actions = [SerperWebSearchAction(), WebFetchAction()]
        skill = SkillDoc(
            name="research",
            description="Investigate a topic with evidence-first synthesis and citations.",
            body=(
                Path(__file__).parents[4]
                / "jvagent"
                / "skills"
                / "research"
                / "SKILL.md"
            )
            .read_text(encoding="utf-8")
            .split("---", 2)[-1]
            .strip(),
            requires_tools=("web_search__search", "web_fetch__fetch"),
            requires_actions=("SerperWebSearchAction", "WebFetchAction"),
            digest="fixed-evidence-research-skill-v2",
        )
        ex = make_orchestrator(actions=actions)
        monkeypatch.setattr(
            OrchestratorInteractAction, "_run_model", _REAL_LEGACY_RUN_MODEL
        )
        assert ex._run_model.__func__ is _REAL_LEGACY_RUN_MODEL
        monkeypatch.setattr(
            OrchestratorInteractAction,
            "_discover_skills",
            lambda _self, _agent: [skill],
        )
        monkeypatch.setattr(
            OrchestratorInteractAction,
            "_enforce_required_actions",
            lambda _self, docs: _return_docs(docs),
        )

        for replicate in range(1, replicates + 1):
            before_calls = len(calls)
            before_attempts = call_attempts
            before_actions = len(action_calls)
            before_published = len(publish_log)
            visitor = make_visitor(utterance=case["prompt"], user_id="legacy-eval")
            visitor.proactive_task = None
            visitor.interaction.observability_metrics = []
            started = perf_counter()
            error = None
            try:
                await ex._run_loop(visitor)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            case_calls = calls[before_calls:]
            case_actions = action_calls[before_actions:]
            run_cost = _estimate_cost(case_calls)
            cumulative_cost += run_cost
            record = {
                "driver": "legacy",
                "case_id": case["id"],
                "replicate": replicate,
                "request_count": len(case_calls),
                "request_attempts": call_attempts - before_attempts,
                "calls": case_calls,
                "action_calls": case_actions,
                "total_tokens": sum(call["total_tokens"] for call in case_calls),
                "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                "estimated_cost_usd": round(run_cost, 8),
                "cumulative_estimated_cost_usd": round(cumulative_cost, 8),
                "error": error,
                "output": visitor.interaction.response,
                "published": [
                    item["content"] for item in publish_log[before_published:]
                ],
                "activation_metrics": [
                    metric
                    for metric in visitor.interaction.observability_metrics
                    if metric.get("event_type") == "orchestrator_activation"
                ],
                "usage_unreported": (call_attempts - before_attempts) > len(case_calls),
            }
            records.append(record)
            if record["usage_unreported"]:
                cumulative_cost += 0.01
                record["cumulative_cost_includes_unknown_request_reserve"] = True
                record["cumulative_estimated_cost_usd"] = round(cumulative_cost, 8)
                break
            assert case_calls, json.dumps(record, sort_keys=True)
            if (
                run_cost > PER_RUN_COST_CEILING_USD
                or cumulative_cost >= TOTAL_COST_CEILING_USD
            ):
                break
        if cumulative_cost >= TOTAL_COST_CEILING_USD or (
            records and records[-1]["estimated_cost_usd"] > PER_RUN_COST_CEILING_USD
        ):
            break

    report = {
        "provider": "Ollama Cloud",
        "model": "glm-5.3:cloud",
        "driver": "legacy Orchestrator",
        "manifest": str(manifest_path.relative_to(Path.cwd())),
        "planned_cases": len(cases),
        "planned_replicates_per_case": replicates,
        "completed_runs": len(records),
        "per_run_cost_ceiling_usd": PER_RUN_COST_CEILING_USD,
        "total_cost_ceiling_usd": TOTAL_COST_CEILING_USD,
        "pricing_basis": "Ollama GLM-5.3 standard rates checked 2026-10-05; cached-input discount ignored",
        "records": records,
    }
    serialized = json.dumps(report, sort_keys=True)
    record_property("legacy_live_research_evaluation", serialized)
    print(f"LEGACY_LIVE_RESEARCH_EVALUATION={serialized}")


async def _return_docs(docs: list[Any]) -> list[Any]:
    return docs
