"""Native admission of explicitly configured direct graph retrieval."""

import pytest

from jvagent.action.pageindex.pageindex_action import PageIndexAction


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["direct", "tree_search", "walker"])
async def test_only_direct_search_declares_reviewed_read_effect(strategy: str) -> None:
    action = PageIndexAction(strategy=strategy)
    tools = {tool.name: tool for tool in await action.get_tools()}
    assert tools["pageindex__search"].effect_class == (
        "read" if strategy == "direct" else None
    )
    assert tools["pageindex__assimilate"].effect_class != "read"


@pytest.mark.asyncio
async def test_runtime_config_cannot_bypass_strategy_admission() -> None:
    action = PageIndexAction(
        strategy="direct", metadata={"config_overrides": {"strategy": "tree_search"}}
    )
    tools = {tool.name: tool for tool in await action.get_tools()}
    assert tools["pageindex__search"].effect_class is None


@pytest.mark.asyncio
async def test_read_profile_reuses_service_and_hides_write_tools() -> None:
    from jvagent.action.pageindex_read import PageIndexReadAction

    action = PageIndexReadAction()
    assert isinstance(action, PageIndexAction)
    tools = await action.get_tools()
    assert [tool.name for tool in tools] == ["pageindex__search"]
    assert tools[0].effect_class == "read"


@pytest.mark.asyncio
async def test_native_profile_closes_schema_and_pins_strategy(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from jvagent.action.pageindex_read import PageIndexReadAction
    from jvagent.harness.completion_tool import NativeCompletionTool

    search = AsyncMock(return_value=[])
    monkeypatch.setattr(PageIndexAction, "search", search)
    action = PageIndexReadAction(strategy="direct")
    direct = (await action.get_native_tools())[0]
    assert direct.parameters_schema["additionalProperties"] is False
    action.strategy = "tree_search"
    await direct.call(query="induction")
    assert search.await_args.kwargs["strategy"] == "direct"

    tree = (await action.get_native_tools())[0]
    assert isinstance(tree, NativeCompletionTool)
    assert tree.parameters_schema["additionalProperties"] is False
    assert "complete" not in tree.parameters_schema["properties"]
    complete = AsyncMock()
    action.strategy = "direct"
    await tree.call(query="induction", complete=complete)
    assert search.await_args.kwargs["strategy"] == "tree_search"
    assert search.await_args.kwargs["complete"] is complete


@pytest.mark.asyncio
async def test_native_tree_failure_never_calls_legacy_provider_or_search_fallback(
    monkeypatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from jvagent.action.pageindex import retrieval
    from jvagent.action.pageindex.core import utils

    allowed_root = SimpleNamespace(doc_name="Allowed")
    monkeypatch.setattr(retrieval, "_lexical_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        retrieval, "get_document_roots", AsyncMock(return_value=[allowed_root])
    )
    monkeypatch.setattr(
        retrieval, "_graph_to_tree", AsyncMock(return_value=[{"node_id": "one"}])
    )
    direct = AsyncMock()
    legacy = AsyncMock()
    counter = Mock(side_effect=AssertionError("Legacy token counter"))
    monkeypatch.setattr(retrieval, "_search_via_direct", direct)
    monkeypatch.setattr(utils, "llm_acompletion", legacy)
    monkeypatch.setattr(utils, "count_tokens", counter)
    complete = AsyncMock(side_effect=RuntimeError("provider failure"))

    with pytest.raises(RuntimeError, match="provider failure"):
        await retrieval._search_via_tree_search(
            object(), "induction", None, 10, complete=complete
        )
    complete.assert_awaited_once()
    legacy.assert_not_called()
    direct.assert_not_called()
    counter.assert_not_called()
