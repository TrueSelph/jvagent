"""Modular file tools retain task-local caller binding without an executor."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from jvagent.action.file_interface import FileInterfaceAction, _core
from jvagent.tooling.context import bind_dispatch_context, get_dispatch_context


@pytest.mark.asyncio
async def test_file_tool_preserves_task_local_host_caller(monkeypatch: Any) -> None:
    action = FileInterfaceAction()
    seen = []

    async def exists(visitor: Any, path: str) -> bool:
        await asyncio.sleep(0)
        context = get_dispatch_context()
        assert context is not None
        assert context.user_id == visitor.user_id
        seen.append((context.user_id, path))
        return True

    monkeypatch.setattr(_core, "file_exists", exists)

    async def invoke(user: str) -> dict[str, Any]:
        visitor = SimpleNamespace(user_id=user, session_id="session-" + user)
        with bind_dispatch_context(visitor):
            return json.loads(await action._t_file_exists(user + ".txt"))

    results = await asyncio.gather(invoke("one"), invoke("two"))
    assert [result["path"] for result in results] == ["one.txt", "two.txt"]
    assert sorted(seen) == [("one", "one.txt"), ("two", "two.txt")]
    assert get_dispatch_context() is None
    assert await action._t_file_exists("no-context.txt") == "(no execution context)"
