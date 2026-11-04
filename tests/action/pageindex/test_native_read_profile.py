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
