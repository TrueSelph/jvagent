"""Administrative PageIndex execution persists usage without a chat parent."""

from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from jvagent.action.native_harness import NativeHarnessAction
from jvagent.action.pageindex.native_search import CHANNEL, run_native_search
from jvagent.harness import native_model
from jvagent.harness.completion_tool import CompleteText
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
@pytest.mark.parametrize("max_requests", [1, 2])
async def test_admin_search_shares_limits_and_journals_all_ranks(
    monkeypatch: pytest.MonkeyPatch, max_requests: int
) -> None:
    action = await NativeHarnessAction.create(max_requests=max_requests)
    calls = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        calls.append(messages)
        assert not info.function_tools
        return ModelResponse(
            parts=[TextPart("ranked")],
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        )

    monkeypatch.setattr(
        native_model,
        "qualified_model",
        lambda _: FunctionModel(respond, model_name="gpt-4.1"),
    )

    async def search(complete: CompleteText) -> list[dict[str, Any]]:
        return [{"text": await complete("Rank A")}, {"text": await complete("Rank B")}]

    async def run() -> list[dict[str, Any]]:
        return await run_native_search(
            action, agent_id="agent", actor="operator", query="search", search=search
        )

    if max_requests == 1:
        with pytest.raises(UsageLimitExceeded):
            await run()
    else:
        assert await run() == [{"text": "ranked"}, {"text": "ranked"}]
    assert len(calls) == max_requests
    rows = await Interaction.find({"channel": CHANNEL})
    assert len(rows) == 1
    receipt = rows[0]
    assert receipt.user_id == "operator" and receipt.closed
    assert receipt.usage["total_tokens"] == 15 * max_requests
    runs = receipt.native_steps["runs"]
    assert len(runs) == 2 and runs[1]["parent_run_id"] == runs[0]["run_id"]
    assert receipt.native_run["status"] == (
        "completed" if max_requests == 2 else "failed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_admin_tree_search_refuses_missing_native_admission(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import Request
    from jvspatial.api.exceptions import AuthenticationError, ValidationError

    from jvagent.action.pageindex import endpoints
    from jvagent.action.pageindex_read import PageIndexReadAction
    from jvagent.core import execution_owner

    profile = PageIndexReadAction(enabled=enabled, strategy="tree_search")
    agent = SimpleNamespace(get_action_by_type=AsyncMock(return_value=profile))
    monkeypatch.setattr(endpoints.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        execution_owner, "native_harness_action", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(endpoints, "initialize_pageindex_database", lambda **_: None)
    monkeypatch.setattr(
        endpoints, "_get_app_id_from_node", AsyncMock(return_value="fixture")
    )
    legacy = AsyncMock()
    monkeypatch.setattr(endpoints, "search_documents", legacy)
    with pytest.raises(AuthenticationError if enabled else ValidationError):
        await endpoints.search_documents_endpoint(
            Request({"type": "http"}),
            "agent",
            query="search",
            doc_name=None,
            strategy="tree_search",
            limit=10,
            metadata=None,
            include_references=True,
            only_enabled=True,
            include=None,
        )
    legacy.assert_not_called()
