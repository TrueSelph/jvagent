"""Shipped SOPs load through official Skills and retain host tool boundaries."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.pageindex_read import PageIndexReadAction
from jvagent.harness.native import build_native_agent
from jvagent.harness.skill_assets import project_skill, projected_workspace


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "directory",
    [
        Path("jvagent/skills/pageindex-search-with-references"),
        Path(
            "examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/web-lookup"
        ),
    ],
)
async def test_migrated_asset_loads_without_legacy_activation(directory: Path) -> None:
    from pydantic_ai_harness import Skills

    metadata, document = project_skill(directory)
    library = await Skills(
        ".",
        include=[metadata["name"]],
        workspace=projected_workspace(directory, document),
    ).load()
    assert [skill.name for skill in library.skills] == [directory.name]
    assert library.skills[0].model_invocable
    assert "use_skill" not in document


@pytest.mark.asyncio
@pytest.mark.parametrize("permitted", [True, False])
async def test_shipped_retrieval_sop_uses_action_tool_and_host_authorization(
    monkeypatch: pytest.MonkeyPatch, permitted: bool
) -> None:
    action = PageIndexReadAction()
    retrieval = AsyncMock(
        return_value=[{"text": "Cedar policy marker", "doc_name": "Handbook"}]
    )

    async def search(self: Any, *args: Any, **kwargs: Any) -> Any:
        return await retrieval(*args, **kwargs)

    monkeypatch.setattr(type(action), "search", search)
    turns = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns == 1:
            assert "pageindex-search-with-references" in str(messages)
            assert "use_skill" not in str(messages)
            tool = next(t for t in info.function_tools if t.name == "pageindex__search")
            assert set(tool.parameters_json_schema["properties"]) == {
                "query",
                "doc_name",
                "limit",
            }
            return ModelResponse(
                parts=[ToolCallPart("pageindex__search", {"query": "Cedar policy"})]
            )
        results = [
            p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        ]
        assert len(results) == 1
        if permitted:
            assert "Cedar policy marker" in str(results[-1].content)
        else:
            assert "Action access denied" in str(results[-1].content)
            assert "Cedar policy marker" not in str(results[-1].content)
        return ModelResponse(parts=[TextPart("done")])

    async def access(name: str) -> bool:
        return permitted and name == "pageindex__search"

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=Path("jvagent/skills/pageindex-search-with-references"),
        tools=await action.get_tools(),
        access_check=access,
        available_actions=["PageIndexAction"],
        instructions="Use the authorized retrieval skill.",
    )
    await agent.run("Find the Cedar policy")
    assert retrieval.await_count == int(permitted)


def test_shipped_retrieval_requires_pageindex_action() -> None:
    with pytest.raises(ValueError, match="Required Actions unavailable"):
        build_native_agent(
            FunctionModel(
                lambda messages, info: ModelResponse(parts=[TextPart("unused")])
            ),
            skill_directory=Path("jvagent/skills/pageindex-search-with-references"),
            tools=[],
            access_check=lambda name: True,
            available_actions=[],
            instructions="Use retrieval.",
        )
