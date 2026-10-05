"""Opt-in bounded smoke through the real JV model Action and pilot capability.

Set ``JVAGENT_RUN_PILOT_LIVE_SMOKE=1`` only for an explicitly authorized live
run. The test uses the signed-in local Ollama daemon for ``glm-5.3:cloud``; the
ordinary test suite skips it.
"""

import json
import os
from time import perf_counter

import pytest

pytest.importorskip("pydantic_ai")

from jvagent.action.model.language.ollama.ollama import OllamaLanguageModelAction
from jvagent.action.orchestrator.pilot.contracts import (
    PilotCaller,
    PilotRunContext,
    ResearchBrief,
)
from jvagent.action.orchestrator.pilot.runtime import (
    PilotEvidenceCollector,
    build_research_agent,
    run_research_agent,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.tooling.tool import Tool as JVTool


@pytest.mark.asyncio
async def test_bounded_live_model_smoke_returns_typed_pilot_output(record_property):
    if os.environ.get("JVAGENT_RUN_PILOT_LIVE_SMOKE") != "1":
        pytest.skip("set JVAGENT_RUN_PILOT_LIVE_SMOKE=1 to authorize this API call")
    action = OllamaLanguageModelAction()
    action.api_endpoint = "http://127.0.0.1:11434"
    action.model = "glm-5.3:cloud"
    action.max_tokens = 1024
    action.temperature = 0.0
    action.max_retries = 0
    calls = []

    class RecordingAction:
        model = "glm-5.3:cloud"

        async def complete(self, request, *, calling_action_name=None):
            started = perf_counter()
            response = await action.complete(
                request, calling_action_name=calling_action_name
            )
            calls.append(
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                }
            )
            return response

    context = PilotRunContext(
        caller=PilotCaller(
            agent_id="live-smoke-agent",
            user_id="live-smoke-user",
            session_id="live-smoke-session",
        ),
        task_id="live-smoke-task",
        run_id="live-smoke-run",
        skill_id="research",
        skill_digest="live-smoke-skill",
        config_digest="live-smoke-config",
        max_model_requests=5,
        max_tool_calls=5,
        max_total_tokens=4096,
        max_output_tokens=1024,
        max_runtime_seconds=60,
    )

    action_calls = []

    async def fixture_search(query: str) -> list[dict[str, str]]:
        action_calls.append(query)
        return [
            {
                "id": "https://example.test/pilot-evidence",
                "title": "Pilot fixture",
                "link": "https://example.test/pilot-evidence",
                "snippet": "The fixture verifies that the capability pilot uses typed output.",
            }
        ]

    skill = SkillDoc(
        name="research",
        description="Research a question using existing Actions and cite evidence.",
        body="Search for evidence, then cite its source identifier in the brief.",
        requires_tools=("fixture_search__search",),
        requires_actions=("FixtureSearchAction",),
        digest="live-smoke-skill",
    )
    action_tool = JVTool(
        name="fixture_search__search",
        description="Search the fixed smoke-test source fixture.",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        execute=fixture_search,
    )

    async def access_check(*_args):
        return True

    evidence = PilotEvidenceCollector()

    agent = await build_research_agent(
        RecordingAction(),
        [(skill, [("FixtureSearchAction", action_tool)])],
        instructions=(
            "Use the research capability and its fixture search Action. Base the "
            "answer only on the returned source. Return a concise ResearchBrief "
            "whose question is the user's question and whose source_ids contain "
            "the exact source identifier returned by that Action."
        ),
        run_context=context,
        access_check=access_check,
        result_observer=evidence.observe,
        model_id="glm-5.3:cloud",
        model_settings={"temperature": 0.0, "max_tokens": 1024},
    )
    started = perf_counter()
    output = await run_research_agent(
        agent,
        "Use the fixture source to verify whether the capability pilot uses typed output.",
        run_context=context,
        evidence=evidence,
    )
    elapsed_ms = round((perf_counter() - started) * 1000, 2)

    assert isinstance(output, ResearchBrief)
    assert output.source_ids == ("https://example.test/pilot-evidence",)
    assert "typed output" in output.brief.lower()
    evidence.validate(output)
    assert action_calls
    assert len(calls) <= context.max_model_requests
    total_tokens = sum(item["total_tokens"] for item in calls)
    assert total_tokens <= context.max_total_tokens
    # Standard-rate upper estimate from https://ollama.com/pricing, checked
    # 2026-10-05. Treat all input as uncached and ignore off-peak discounts.
    estimated_cost_usd = (
        sum(item["prompt_tokens"] for item in calls) * 1.40
        + sum(item["completion_tokens"] for item in calls) * 4.40
    ) / 1_000_000
    assert estimated_cost_usd <= 0.01
    report = {
        "provider": "Ollama Cloud",
        "model": "glm-5.3:cloud",
        "calls": calls,
        "action_calls": len(action_calls),
        "total_tokens": total_tokens,
        "elapsed_ms": elapsed_ms,
        "estimated_cost_usd": round(estimated_cost_usd, 8),
        "pricing_basis": "Ollama GLM-5.3 standard rates checked 2026-10-05; cached input discount ignored",
    }
    record_property("pilot_live_model_smoke", json.dumps(report, sort_keys=True))
    print(f"PILOT_LIVE_MODEL_SMOKE={json.dumps(report, sort_keys=True)}")
