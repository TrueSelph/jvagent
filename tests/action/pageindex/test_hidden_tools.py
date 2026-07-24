"""hidden_tools omits named tools from get_tools() without removing APIs."""

from __future__ import annotations

import pytest

from jvagent.action.pageindex.pageindex_action.pageindex_action import (
    PageIndexAction,
)


@pytest.mark.asyncio
async def test_hidden_tools_omits_assimilate_from_surface():
    inst = PageIndexAction()
    inst.hidden_tools = ["pageindex__assimilate"]

    names = {t.name for t in await inst.get_tools()}
    assert "pageindex__assimilate" not in names
    assert "pageindex__search" in names
    assert "pageindex__list" in names
    assert callable(getattr(inst, "assimilate", None))


@pytest.mark.asyncio
async def test_hidden_tools_default_keeps_assimilate():
    names = {t.name for t in await PageIndexAction().get_tools()}
    assert "pageindex__assimilate" in names
    assert "pageindex__search" in names
