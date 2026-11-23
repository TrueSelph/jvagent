"""Exercise existing retrieval storage through public native tool execution."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from jvspatial.db import unregister_database
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.pageindex.adapter import persist_structure
from jvagent.action.pageindex.config import (
    PAGEINDEX_DB_NAME,
    initialize_pageindex_database,
)
from jvagent.action.pageindex_read import PageIndexReadAction
from jvagent.harness.native import build_native_agent
from jvagent.tooling.context import bind_dispatch_context


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, "outsider", "member"])
@pytest.mark.parametrize("lexical", [False, True])
@pytest.mark.parametrize("document", [None, "restricted_manual"])
async def test_native_retrieval_preserves_document_access(
    tmp_path: Path,
    monkeypatch: Any,
    user: str | None,
    lexical: bool,
    document: str | None,
) -> None:
    try:
        unregister_database(PAGEINDEX_DB_NAME)
    except (ValueError, KeyError):
        pass
    initialize_pageindex_database(
        {"db_type": "json", "db_path": str(tmp_path / "index")}
    )
    try:
        for name, access, collection in (
            ("public_manual", "public", "native-access"),
            ("restricted_manual", "restricted", "native-access"),
            ("other_tenant_manual", "public", "other-tenant"),
        ):
            await persist_structure(
                name,
                [{"title": name, "node_id": "1", "text": f"Orchid {name}"}],
                collection_name=collection,
                metadata={"access": access},
            )
        action = PageIndexReadAction(
            collection="native-access",
            access_control=True,
            metadata={"config_overrides": {"enable_lexical_index": lexical}},
        )
        tools = await action.get_tools()
        from jvagent.action.pageindex.lexical_index import search

        assert await search("Orchid", "native-access")
        monkeypatch.setattr(type(action), "get_app", AsyncMock(return_value=None))
        monkeypatch.setattr(
            type(action), "get_model_action", AsyncMock(return_value=None)
        )
        access = SimpleNamespace(
            user_groups={"PageIndexAction": {"restricted": ["member"]}},
            _resolve_user_groups=lambda name: {"restricted": ["member"]},
        )
        monkeypatch.setattr(type(action), "get_action", AsyncMock(return_value=access))
        returned: list[str] = []

        def respond(messages: Any, info: Any) -> ModelResponse:
            results = [
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart)
                and part.tool_name == "pageindex__search"
            ]
            if not results:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "pageindex__search",
                            {"query": "Orchid", "doc_name": document},
                        )
                    ]
                )
            returned.append(str(results[-1].content))
            return ModelResponse(parts=[TextPart("done")])

        async def permitted(name: str) -> bool:
            return name == "pageindex__search"

        agent = build_native_agent(
            FunctionModel(respond),
            skill_directory=Path("jvagent/skills/answer"),
            tools=tools,
            access_check=permitted,
            available_actions=["PageIndexAction", "SerperWebSearchAction"],
            instructions="Answer from authorized documents.",
        )
        visitor = SimpleNamespace(user_id=user, session_id=None)
        # Context-aware tools must not resurrect the retired dispatcher.
        monkeypatch.setitem(sys.modules, "jvagent.tooling.tool_executor", None)
        with bind_dispatch_context(visitor):
            await agent.run("Find Orchid documents")
        assert len(returned) == 1
        assert ("public_manual" in returned[0]) == (document is None)
        assert ("restricted_manual" in returned[0]) == (user == "member")
        assert "other_tenant_manual" not in returned[0]
    finally:
        unregister_database(PAGEINDEX_DB_NAME)
