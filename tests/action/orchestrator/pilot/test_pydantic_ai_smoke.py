"""Offline contract smoke against the optional Pydantic AI dependency."""

import asyncio

import pytest
from pydantic import ValidationError

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.messages import ModelResponse, ToolCallPart  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

from jvagent.action.orchestrator.pilot.contracts import (  # noqa: E402
    PilotCaller,
    PilotRunContext,
    ResearchBrief,
)
from jvagent.action.orchestrator.pilot.runtime import (  # noqa: E402
    PilotModelAdapterError,
    capability_for_skill,
)
from jvagent.action.orchestrator.skills import SkillDoc  # noqa: E402
from jvagent.tooling.tool import Tool as JVTool  # noqa: E402


def test_compiler_rejects_unimplemented_skill_frontmatter() -> None:
    skill = SkillDoc(
        name="scripted",
        description="Has behavior outside the supported SOP contract.",
        body="Run the bundled script.",
        digest="skill-sha256",
        unsupported_features=("bundled scripts", "lifecycle hooks"),
    )
    context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="scripted",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    with pytest.raises(
        PilotModelAdapterError, match="bundled scripts, lifecycle hooks"
    ):
        asyncio.run(
            capability_for_skill(
                skill,
                [],
                run_context=context,
                access_check=lambda *_args: True,
            )
        )


def test_deferred_skill_calls_existing_named_tool_and_returns_typed_output() -> None:
    tool_calls = []
    requests = []

    async def web_search__search(query: str) -> str:
        tool_calls.append(query)
        return "source-1: evidence from the existing Action package"

    skill = SkillDoc(
        name="research",
        description="Research a question using search and fetch Actions.",
        body="Search, fetch, and ground each finding in source IDs.",
        requires_tools=("web_search__search",),
        requires_actions=("SearchAction",),
        digest="skill-sha256",
    )
    run_context = PilotRunContext(
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )
    research = asyncio.run(
        capability_for_skill(
            skill,
            [
                (
                    "SearchAction",
                    JVTool(
                        name="web_search__search",
                        description="Search the existing JV WebSearch Action operation.",
                        parameters_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                        execute=web_search__search,
                    ),
                )
            ],
            run_context=run_context,
            access_check=lambda *_args: True,
        )
    )
    assert research.id == "research" and research.defer_loading is True

    expected = {
        "question": "pilot architecture",
        "findings": ["The pilot keeps skill instructions and Action tools together."],
        "source_ids": ["source-1"],
        "limitations": [],
        "brief": "Finding supported by source-1.",
    }

    def respond(messages, info):
        tool_names = [tool.name for tool in info.function_tools]
        requests.append(tool_names)
        if len(requests) == 1:
            assert tool_names == ["load_capability"]
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "research"})]
            )
        if len(requests) == 2:
            assert "web_search__search" in tool_names
            return ModelResponse(
                parts=[
                    ToolCallPart("web_search__search", {"query": "pilot architecture"})
                ]
            )
        assert info.output_tools
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, expected)])

    agent = Agent(
        FunctionModel(respond),
        output_type=ResearchBrief,
        capabilities=[research],
        retries=0,
    )
    result = asyncio.run(
        agent.run("Research the pilot architecture.", deps=run_context)
    )

    assert result.output == ResearchBrief.model_validate(expected)
    assert tool_calls == ["pilot architecture"]
    assert len(requests) == 3
    with pytest.raises(ValidationError):
        ResearchBrief.model_validate({**expected, "unexpected": True})
