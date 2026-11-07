"""The app-local Storefront uses the generic native Action adapter."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "examples/jvagent_app/agents/jvagent/leadgen_agent"


def storefront() -> Any:
    path = APP / "actions/contrib/storefront/storefront_action.py"
    spec = importlib.util.spec_from_file_location("_native_storefront", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [False, True])
async def test_storefront_catalog_activation_reveals_each_real_tool(
    persisted: bool,
) -> None:
    from jvagent.harness.step_store import InteractionStepStore

    async def save() -> None:
        pass

    store = (
        InteractionStepStore(SimpleNamespace(native_steps={}, save=save))
        if persisted
        else None
    )
    step = 0

    async def access(name: str) -> bool:
        return True

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        names = {tool.name for tool in info.function_tools}
        if step == 1:
            assert names == {"load_capability"}
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "product-faq"})]
            )
        if step == 2:
            assert "storefront__faq" in names
            assert "storefront__search_products" not in names
            return ModelResponse(
                parts=[ToolCallPart("storefront__faq", {"query": "free trial"})]
            )
        if step == 3:
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "product-search"})]
            )
        if step == 4:
            assert {"storefront__faq", "storefront__search_products"} <= names
            return ModelResponse(
                parts=[ToolCallPart("storefront__search_products", {"query": "churn"})]
            )
        assert "14-day" in str(messages) and "Customer Insights" in str(messages)
        return ModelResponse(parts=[TextPart("done")])

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directories=[APP / "skills/product-faq", APP / "skills/product-search"],
        tools=await storefront().StorefrontAction().get_tools(),
        available_actions=["StorefrontAction"],
        access_check=access,
        instructions="Use the mock storefront skills.",
        step_store=store,
    )
    assert (await agent.run("Trial and churn")).output == "done"


@pytest.mark.asyncio
async def test_gpt41_wire_declares_the_tool_after_skill_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")

    async def access(name: str) -> bool:
        return True

    declared = build_native_agent(
        "openai:gpt-4.1",
        skill_directories=[APP / "skills/product-faq", APP / "skills/product-search"],
        tools=await storefront().StorefrontAction().get_tools(),
        available_actions=["StorefrontAction"],
        access_check=access,
        instructions="Use the mock storefront skills.",
    )
    profile = declared.model.profile
    turn = 0

    async def request(req: httpx.Request) -> httpx.Response:
        nonlocal turn
        turn += 1
        body = json.loads(req.content)
        names = {tool.get("name") for tool in body["tools"]}
        if turn == 1:
            assert names == {"load_capability"}
            name, args = "load_capability", {"id": "product-faq"}
        elif turn == 2:
            # This fails with the unmodified core 2.66 first-party profile.
            assert "storefront__faq" in names
            assert "storefront__search_products" not in names
            assert all(item.get("type") != "additional_tools" for item in body["input"])
            name, args = "storefront__faq", {"query": "free trial"}
        else:
            assert "14-day" in str(body["input"])
            return httpx.Response(
                200,
                json={
                    "id": "resp_done",
                    "object": "response",
                    "created_at": 0,
                    "model": "gpt-4.1",
                    "status": "completed",
                    "output": [
                        {
                            "id": "msg_done",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "done",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": f"resp_{turn}",
                "object": "response",
                "created_at": 0,
                "model": "gpt-4.1",
                "status": "completed",
                "output": [
                    {
                        "id": f"fc_{turn}",
                        "call_id": f"call_{turn}",
                        "type": "function_call",
                        "name": name,
                        "arguments": json.dumps(args),
                        "status": "completed",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
        model = OpenAIResponsesModel(
            "gpt-4.1",
            profile=profile,
            provider=OpenAIProvider(api_key="synthetic-test-key", http_client=client),
        )
        agent = build_native_agent(
            model,
            skill_directories=[
                APP / "skills/product-faq",
                APP / "skills/product-search",
            ],
            tools=await storefront().StorefrontAction().get_tools(),
            available_actions=["StorefrontAction"],
            access_check=access,
            instructions="Use the mock storefront skills.",
        )
        assert (await agent.run("Trial")).output == "done"
    assert turn == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill,name,arguments,marker",
    [
        ("product-faq", "storefront__faq", {"query": "free trial"}, "14-day"),
        (
            "product-search",
            "storefront__search_products",
            {"query": "churn", "limit": 1},
            "Customer Insights",
        ),
    ],
)
@pytest.mark.parametrize("permitted", [True, False])
async def test_existing_sop_calls_real_tool_under_host_permission(
    monkeypatch: pytest.MonkeyPatch,
    skill: str,
    name: str,
    arguments: dict[str, Any],
    marker: str,
    permitted: bool,
) -> None:
    module = storefront()
    body_calls: list[str] = []
    helper = "search_faq" if name == "storefront__faq" else "search_products"
    original = getattr(module, helper)

    def observe(*args: Any, **kwargs: Any) -> Any:
        body_calls.append(name)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, helper, observe)
    action = module.StorefrontAction()
    tools = await action.get_tools()
    assert {tool.name for tool in tools} == {
        "storefront__faq",
        "storefront__search_products",
    }
    assert all(tool.effect_class == "read" for tool in tools)
    turns = 0
    checked: list[str] = []

    async def access(label: str) -> bool:
        raise AssertionError("The host tool checker must own admission")

    async def tool_access(tool: Any) -> bool:
        checked.append(tool.name)
        return permitted

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal turns
        turns += 1
        if turns == 1:
            assert skill in str(messages)
            assert name in {tool.name for tool in info.function_tools}
            return ModelResponse(parts=[ToolCallPart(name, arguments)])
        returned = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert len(returned) == 1
        if permitted:
            value = json.loads(returned[0].content)
            assert value["status"] == "ok"
            assert marker in str(value)
        else:
            assert "Action access denied" in str(returned[0].content)
            assert marker not in str(returned[0].content)
        return ModelResponse(parts=[TextPart("done")])

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=APP / "skills" / skill,
        tools=tools,
        available_actions=["StorefrontAction"],
        access_check=access,
        tool_access_check=tool_access,
        instructions="Use the configured mock storefront skill.",
    )
    assert (await agent.run("Answer from the mock data")).output == "done"
    assert checked == [name]
    assert body_calls == ([name] if permitted else [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments", [{"query": []}, {"query": "trial", "visitor": {"user_id": "other"}}]
)
async def test_invalid_or_authority_arguments_never_enter_storefront(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any]
) -> None:
    module = storefront()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Rejected arguments reached the Action")

    monkeypatch.setattr(module, "search_faq", forbidden)
    turn = 0

    async def access(name: str) -> bool:
        return True

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal turn
        turn += 1
        if turn == 1:
            return ModelResponse(parts=[ToolCallPart("storefront__faq", arguments)])
        assert "14-day" not in str(messages)
        return ModelResponse(parts=[TextPart("rejected")])

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=APP / "skills/product-faq",
        tools=await module.StorefrontAction().get_tools(),
        available_actions=["StorefrontAction"],
        access_check=access,
        instructions="Use the mock FAQ.",
    )
    # Authority rejection may be fatal by design; neither path may invoke the tool.
    if "visitor" in arguments:
        with pytest.raises(ValueError, match="authority|reserved|identity"):
            await agent.run("Answer")
    else:
        assert (await agent.run("Answer")).output == "rejected"
