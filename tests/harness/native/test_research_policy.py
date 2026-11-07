"""Research policy through native Pydantic tool and output execution."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult


def source_identifier(content: str) -> str:
    """Allow mutation checks against the prior plain-text receipt interface."""
    if content.startswith("{"):
        return json.loads(content)["evidence"]["source_id"]
    return content.split("Observed source ID: ")[-1].strip()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_history", [False, True])
async def test_native_research_uses_fresh_receipts_on_reused_agent(
    tmp_path: Path,
    with_history: bool,
) -> None:
    tmp_path = tmp_path / "research"
    tmp_path.mkdir()
    (tmp_path / "SKILL.md").write_text(
        "---\nname: research\ndescription: Research\noutput-contract: evidence_required\n"
        "allowed-tools: [web_fetch__fetch]\nrequires-actions: [WebFetchAction]\n---\nResearch carefully.\n"
    )
    url = "https://example.com/page"
    fetched: list[str] = []
    source_id = ""

    async def fetch(url: str) -> ToolResult:
        fetched.append(url)
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
        nonlocal calls, source_id
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("web_fetch__fetch", {"url": url})])
        # Read the actual tool return, avoiding repr escape punctuation.
        if not source_id:
            content = messages[-1].parts[0].content
            source_id = source_identifier(content)
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
    # A reused specialist must not cite its previous task's evidence, even if
    # that evidence is still visible in the supplied conversation history.
    with pytest.raises(UnexpectedModelBehavior):
        await agent.run(
            "Different objective; cite the old source without retrieving it",
            message_history=result.all_messages() if with_history else (),
        )
    assert fetched == [url]
    assert calls == 4
    calls = 0
    refreshed = await agent.run("Retrieve again for this objective")
    assert url in refreshed.output
    assert fetched == [url, url]


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
    with policy.run_scope():
        with pytest.raises(ModelRetry):
            policy.finish(brief)


def test_native_grounding_fields_are_required_in_model_schema() -> None:
    from jvagent.skills.research.native import GroundedFinding

    schema = GroundedFinding.model_json_schema()
    assert {"supporting_source_id", "supporting_quote"}.issubset(schema["required"])


@pytest.mark.asyncio
async def test_concurrent_research_runs_cannot_cross_cite(tmp_path: Path) -> None:
    directory = tmp_path / "research"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: research\ndescription: Research\noutput-contract: evidence_required\n"
        "allowed-tools: [web_fetch__fetch]\n---\nResearch carefully.\n"
    )
    ready = asyncio.Event()
    observed: dict[str, str] = {}

    async def access(label: str) -> bool:
        return True

    async def fetch(url: str) -> ToolResult:
        return ToolResult(
            content=f"# Source: {url}\n# Title: Example\nA shared quote.",
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

    async def respond(messages: Any, info: Any) -> ModelResponse:
        objective = next(
            p.content
            for m in messages
            for p in m.parts
            if isinstance(p, UserPromptPart)
        )
        returns = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        if not returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "web_fetch__fetch", {"url": f"https://example.com/{objective}"}
                    )
                ]
            )
        observed[objective] = (
            returns[-1].content.split("Observed source ID: ")[-1].strip()
        )
        if len(observed) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=5)
        other = observed["beta" if objective == "alpha" else "alpha"]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "brief": {
                            "question": objective,
                            "findings": [
                                {
                                    "claim": "A shared quote.",
                                    "source_ids": [other],
                                    "supporting_source_id": other,
                                    "supporting_quote": "A shared quote.",
                                }
                            ],
                        }
                    },
                )
            ]
        )

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        access_check=access,
        instructions="Research",
        tools=[
            Tool(
                "web_fetch__fetch",
                "Fetch",
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
    results = await asyncio.gather(
        agent.run("alpha"), agent.run("beta"), return_exceptions=True
    )
    assert len(observed) == 2
    assert all(isinstance(result, UnexpectedModelBehavior) for result in results)


@pytest.mark.asyncio
async def test_fetch_view_exposes_exact_citable_markdown_without_relaxing_validation() -> (
    None
):
    from pydantic_ai import ModelRetry

    from jvagent.skills.research.native import GroundedBrief, ResearchOutputPolicy

    policy = ResearchOutputPolicy()
    url = "https://example.com/page"
    quote = "**Pydantic AI** is a Python SDK."
    with policy.run_scope():
        content = await policy.observe(
            "web_fetch__fetch",
            {"url": url},
            ToolResult(
                content=f"# Source: {url}\n# Title: Example\n{quote}" + "x" * 3000,
                metadata={
                    "web_fetch_result": {
                        "outcome": "success",
                        "http_status": 200,
                        "requested_url": url,
                        "final_url": url,
                        "content_type": "text/plain",
                    }
                },
            ),
        )
        view = json.loads(content)
        assert quote in view["evidence"]["excerpt"]
        assert len(view["evidence"]["excerpt"]) == 1600
        source = view["evidence"]["source_id"]
        brief = GroundedBrief.model_validate(
            {
                "question": "Purpose",
                "findings": [
                    {
                        "claim": "Pydantic AI is a Python SDK.",
                        "source_ids": [source],
                        "supporting_source_id": source,
                        "supporting_quote": quote,
                    }
                ],
            }
        )
        assert "> " + quote in policy.finish(brief)
        changed = brief.findings[0].model_copy(
            update={"supporting_quote": "Pydantic AI is a Python SDK."}
        )
        with pytest.raises(ModelRetry):
            policy.finish(brief.model_copy(update={"findings": (changed,)}))


@pytest.mark.asyncio
async def test_structured_evidence_does_not_reintroduce_source_url_credentials() -> (
    None
):
    from jvagent.skills.research.native import ResearchOutputPolicy

    policy = ResearchOutputPolicy()
    url = "https://example.com/page?api_key=fixture-private-value"
    with policy.run_scope():
        content = await policy.observe(
            "web_fetch__fetch",
            {"url": url},
            ToolResult(
                content=f"# Source: {url}\n# Title: Example\nA fact.",
                metadata={
                    "web_fetch_result": {
                        "outcome": "success",
                        "http_status": 200,
                        "requested_url": url,
                        "final_url": url,
                        "content_type": "text/plain",
                    }
                },
            ),
        )
    assert "fixture-private-value" not in content
    assert json.loads(content)["evidence"]["url"] == "https://example.com/page"


@pytest.mark.asyncio
async def test_html_receipt_grounds_readable_text_without_fuzzy_matching() -> None:
    from pydantic_ai import ModelRetry

    from jvagent.action.web_fetch.web_fetch_action import WebFetchAction
    from jvagent.skills.research.native import GroundedBrief, ResearchOutputPolicy

    url = "https://example.com/page"
    title, markdown, plain = WebFetchAction._html_to_markdown(
        '<main><p><b>Pydantic AI</b> is a <a href="/sdk">Python SDK</a>.</p></main>'
    )
    receipt = WebFetchAction._typed_result(
        f"# Source: {url}\n# Title: {title}\n{markdown}",
        outcome="success",
        requested_url=url,
        final_url=url,
        content_type="text/html",
        http_status=200,
        text_excerpt=plain,
    )
    policy = ResearchOutputPolicy()
    with policy.run_scope():
        view = json.loads(
            await policy.observe(
                "web_fetch__fetch",
                {"url": url},
                ToolResult(
                    content=str(receipt),
                    metadata=receipt.tool_result_metadata,
                ),
            )
        )
        assert view["evidence"]["excerpt"] == "Pydantic AI is a Python SDK."
        source = view["evidence"]["source_id"]
        brief = GroundedBrief.model_validate(
            {
                "question": "Purpose",
                "findings": [
                    {
                        "claim": "Pydantic AI is a Python SDK.",
                        "source_ids": [source],
                        "supporting_source_id": source,
                        "supporting_quote": plain,
                    }
                ],
            }
        )
        assert "> " + plain in policy.finish(brief)
        changed = brief.findings[0].model_copy(
            update={"supporting_quote": "Pydantic AI is a complete operating system."}
        )
        with pytest.raises(ModelRetry):
            policy.finish(brief.model_copy(update={"findings": (changed,)}))
