"""Official skill selection must not grant ambient access to catalog tools."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult


def skill(root: Path, name: str, fields: str = "") -> Path:
    path = root / name
    path.mkdir()
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Select {name}\n{fields}---\n{name} instructions.\n"
    )
    return path


async def allow(name: str) -> bool:
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("permitted", [True, False])
async def test_catalog_tools_require_sdk_activation_and_host_access(
    tmp_path: Path, permitted: bool
) -> None:
    alpha = skill(tmp_path, "alpha", "allowed-tools: [lookup]\n")
    beta = skill(tmp_path, "beta")
    called: list[bool] = []
    checked: list[str] = []
    step = 0

    async def lookup() -> str:
        called.append(True)
        return "record"

    async def access(name: str) -> bool:
        checked.append(name)
        return permitted

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        names = {tool.name for tool in info.function_tools}
        if step == 1:
            assert names == {"load_capability"}
            assert "alpha instructions" not in str(messages)
            # A model can name an unadvertised tool; no host call may follow.
            return ModelResponse(parts=[ToolCallPart("lookup", {})])
        if step == 2:
            assert called == [] and checked == []
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "beta"})]
            )
        if step == 3:
            assert "beta instructions" in str(messages) and "lookup" not in names
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "alpha"})]
            )
        if step == 4:
            assert "alpha instructions" in str(messages) and "lookup" in names
            return ModelResponse(parts=[ToolCallPart("lookup", {})])
        return ModelResponse(parts=[TextPart("complete")])

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directories=[alpha, beta],
        tools=[Tool("lookup", "Read", execute=lookup, effect_class="read")],
        access_check=access,
        instructions="Select the appropriate skill.",
    )
    if permitted:
        assert (await agent.run("lookup")).output == "complete"
    else:
        with pytest.raises(UnexpectedModelBehavior, match="exceeded max retries"):
            await agent.run("lookup")
    assert called == ([True] if permitted else [])
    assert checked == ["lookup"]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_history", [False, True])
async def test_catalog_research_cannot_finish_as_plain_text_or_leak_to_next_run(
    tmp_path: Path,
    with_history: bool,
) -> None:
    plain = skill(tmp_path, "plain")
    research = skill(
        tmp_path,
        "research",
        "allowed-tools: [web_fetch__fetch]\noutput-contract: evidence_required\n",
    )
    step = 0
    source_id = ""
    url = "https://example.com/evidence"

    async def fetch(url: str) -> ToolResult:
        return ToolResult(
            content=f"# Source: {url}\n# Title: Room\nThe room is Cedar.",
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

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step, source_id
        step += 1
        if step in (1, 5):
            assert not info.output_tools
            if step == 5:
                return ModelResponse(parts=[TextPart("ordinary conversation")])
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "research"})]
            )
        assert len(info.output_tools) == 1
        if step == 2:
            return ModelResponse(parts=[ToolCallPart("web_fetch__fetch", {"url": url})])
        if step == 3:
            returned = [
                p
                for m in messages
                for p in m.parts
                if isinstance(p, ToolReturnPart) and p.tool_name == "web_fetch__fetch"
            ]
            source_id = json.loads(returned[-1].content)["evidence"]["source_id"]
            return ModelResponse(parts=[TextPart("An ungrounded plain text escape")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "brief": {
                            "question": "Which room?",
                            "findings": [
                                {
                                    "claim": "The room is Cedar.",
                                    "source_ids": [source_id],
                                    "supporting_source_id": source_id,
                                    "supporting_quote": "The room is Cedar.",
                                }
                            ],
                        }
                    },
                )
            ]
        )

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directories=[plain, research],
        access_check=allow,
        instructions="Use skills.",
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
    result = await agent.run("Research the room")
    assert "Cedar" in result.output and url in result.output
    assert step == 4
    assert (
        await agent.run(
            "Hello", message_history=result.all_messages() if with_history else ()
        )
    ).output == "ordinary conversation"


@pytest.mark.asyncio
async def test_bad_catalog_member_fails_before_any_model_request(
    tmp_path: Path,
) -> None:
    good = skill(tmp_path, "good")
    bad = skill(tmp_path, "bad", "disable-model-invocation: true\n")

    def respond(messages: Any, info: Any) -> ModelResponse:
        raise AssertionError("No paid request before catalog validation")

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directories=[good, bad],
        tools=[],
        access_check=allow,
        instructions="Use skills.",
    )
    with pytest.raises(ValueError, match="not model-invocable"):
        await agent.run("Hello")


def test_catalog_rejects_ambiguous_selection_and_duplicate_names(
    tmp_path: Path,
) -> None:
    good = skill(tmp_path, "good")
    for settings in (
        {},
        {"skill_directory": good, "skill_directories": [good]},
        {"skill_directories": [good, good]},
    ):
        with pytest.raises(ValueError):
            build_native_agent(
                FunctionModel(
                    lambda messages, info: ModelResponse(parts=[TextPart("x")])
                ),
                tools=[],
                access_check=allow,
                instructions="Use skills.",
                **settings,
            )


@pytest.mark.asyncio
async def test_research_obligation_survives_child_hook_task_and_context_compaction(
    tmp_path: Path,
) -> None:
    from jvagent.harness.skill_catalog import NativeSkillCatalog

    research = skill(tmp_path, "research", "output-contract: evidence_required\n")
    catalog = NativeSkillCatalog(
        primary=None,
        directories=[research],
        action_bases=None,
        available_actions=[],
        available_tools=[],
        channel="default",
    )

    async def run(activate: bool) -> None:
        with catalog.run_scope():
            ctx = SimpleNamespace(active_capability_ids=set())
            assert catalog.requires_output_contract() is False

            async def hook() -> None:
                ctx.active_capability_ids = {"research"} if activate else set()
                if activate:
                    catalog.note_tool(ctx, "load_capability", {"id": "research"})
                assert catalog.requires_output_contract() is activate

            await asyncio.create_task(hook())
            # Compaction can remove the loading receipt from the active window.
            # The research obligation must survive that without leaking to a
            # concurrent or subsequent run of the same Agent.
            ctx.active_capability_ids = set()
            await asyncio.sleep(0)
            assert catalog.requires_output_contract() is activate

    await asyncio.gather(run(True), run(False))
    await run(False)


@pytest.mark.asyncio
async def test_catalog_clipped_plain_research_does_not_trigger_another_paid_attempt(
    tmp_path: Path,
) -> None:
    from jvagent.harness.contracts import OutputTokenLimitReached

    research = skill(tmp_path, "research", "output-contract: evidence_required\n")
    requests = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "research"})]
            )
        return ModelResponse(
            parts=[TextPart("Clipped ungrounded text")], finish_reason="length"
        )

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directories=[research],
        tools=[],
        access_check=allow,
        instructions="Use skills.",
    )
    with pytest.raises(OutputTokenLimitReached):
        await agent.run("Research")
    assert requests == 2
