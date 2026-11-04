"""Research policy through native Pydantic tool and output execution."""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult


@pytest.mark.asyncio
async def test_native_research_uses_observed_receipt_and_typed_output(
    tmp_path: Path,
) -> None:
    tmp_path = tmp_path / "research"
    tmp_path.mkdir()
    (tmp_path / "SKILL.md").write_text(
        "---\nname: research\ndescription: Research\noutput-contract: evidence_required\n"
        "allowed-tools: [web_fetch__fetch]\nrequires-actions: [WebFetchAction]\n---\nResearch carefully.\n"
    )
    url = "https://example.com/page"

    async def fetch(url: str) -> ToolResult:
        return ToolResult(
            content=f"# Source: {url}\n# Title: Example\nThe answer is forty three.",
            metadata={
                "web_fetch_result": {
                    "outcome": "success",
                    "http_status": 200,
                    "requested_url": url,
                    "final_url": url,
                    "content_type": "text/plain",
                }
            },
        )

    async def access(label: str) -> bool:
        return True

    calls = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("web_fetch__fetch", {"url": url})])
        # Read the actual tool return, avoiding repr escape punctuation.
        content = messages[-1].parts[0].content
        source_id = content.split("Observed source ID: ")[-1].strip()
        brief = {
            "question": "What is the answer?",
            "findings": [
                {
                    "claim": "The answer is forty three.",
                    "source_ids": [source_id],
                    "supporting_source_id": source_id,
                    "supporting_quote": "The answer is forty three.",
                }
            ],
        }
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"brief": brief})]
        )

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=tmp_path,
        access_check=access,
        instructions="Use fetch and finish with research.",
        available_actions=["WebFetchAction"],
        tools=[
            Tool(
                "web_fetch__fetch",
                "Fetch page",
                effect_class="read",
                execute=fetch,
                parameters_schema={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            )
        ],
    )
    result = await agent.run("What is the answer?")
    assert "forty three" in result.output
    assert url in result.output
    assert calls == 2


def test_native_research_rejects_unobserved_source() -> None:
    from pydantic_ai import ModelRetry

    from jvagent.skills.research.contracts import ResearchBrief, ResearchFinding
    from jvagent.skills.research.native import ResearchOutputPolicy

    policy = ResearchOutputPolicy()
    brief = ResearchBrief(
        question="Question",
        findings=(
            ResearchFinding(
                claim="Claim",
                source_ids=("forged",),
                supporting_source_id="forged",
                supporting_quote="quote",
            ),
        ),
    )
    with pytest.raises(ModelRetry):
        policy.finish(brief)
