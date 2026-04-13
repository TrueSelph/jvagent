"""Tests for retrieval decision flow (requires full jvagent pageindex deps)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Full PageIndex stack (optional in minimal dev envs).
for _optional in ("pymupdf", "pypdf", "tiktoken", "openai"):
    pytest.importorskip(_optional)

from jvagent.action.long_memory_retrieval.long_memory_retrieval_interact_action import (
    UserLongMemoryRetrievalInteractAction,
)
from jvagent.memory.user_long_memory import UserLongMemory


class _Cat:
    def __init__(self, category: str, keywords: list, empty: bool = False):
        self.category = category
        self.title = category.title()
        self.keywords = keywords
        self._empty = empty

    def is_empty(self) -> bool:
        return self._empty


@pytest.mark.asyncio
async def test_evaluate_search_need_keyword_fast_path_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _gh(_v):
        return "x"

    act = SimpleNamespace(history_limit=1, model=None, _get_recent_history=_gh)

    ulm = MagicMock()
    ulm.get_all_categories = AsyncMock(
        return_value=[_Cat("interests", ["kubernetes"], empty=False)]
    )
    user = MagicMock()
    inter = MagicMock()
    inter.conversation_id = None
    inter.utterance = "help me with kubernetes pods"
    inter.interpretation = None
    inter.get_user = AsyncMock(return_value=user)

    visitor = MagicMock()
    visitor.interaction = inter

    model_action = AsyncMock()

    monkeypatch.setattr(
        UserLongMemory,
        "get_for_user",
        AsyncMock(return_value=ulm),
    )
    out = await UserLongMemoryRetrievalInteractAction._evaluate_search_need(
        act, visitor, model_action
    )
    assert out["decision"] == "SEARCH"
    assert "kubernetes" in (out.get("query") or "").lower()
    model_action.generate.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_search_need_llm_error_returns_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _gh(_v):
        return "x"

    act = SimpleNamespace(history_limit=1, model=None, _get_recent_history=_gh)

    ulm = MagicMock()
    ulm.get_all_categories = AsyncMock(
        return_value=[_Cat("interests", ["obscurexyz"], empty=False)]
    )
    user = MagicMock()
    inter = MagicMock()
    inter.conversation_id = None
    inter.utterance = "hello there"
    inter.interpretation = None
    inter.get_user = AsyncMock(return_value=user)

    visitor = MagicMock()
    visitor.interaction = inter

    model_action = AsyncMock()
    model_action.generate = AsyncMock(side_effect=RuntimeError("llm down"))

    monkeypatch.setattr(
        UserLongMemory,
        "get_for_user",
        AsyncMock(return_value=ulm),
    )
    out = await UserLongMemoryRetrievalInteractAction._evaluate_search_need(
        act, visitor, model_action
    )
    assert out["decision"] == "CONTINUE"


@pytest.mark.asyncio
async def test_evaluate_search_need_no_model_returns_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _gh(_v):
        return "x"

    act = SimpleNamespace(history_limit=1, model=None, _get_recent_history=_gh)

    ulm = MagicMock()
    ulm.get_all_categories = AsyncMock(
        return_value=[_Cat("interests", ["obscurexyz"], empty=False)]
    )
    user = MagicMock()
    inter = MagicMock()
    inter.conversation_id = None
    inter.utterance = "hello there"
    inter.interpretation = None
    inter.get_user = AsyncMock(return_value=user)

    visitor = MagicMock()
    visitor.interaction = inter

    monkeypatch.setattr(
        UserLongMemory,
        "get_for_user",
        AsyncMock(return_value=ulm),
    )
    out = await UserLongMemoryRetrievalInteractAction._evaluate_search_need(
        act, visitor, None
    )
    assert out["decision"] == "CONTINUE"
