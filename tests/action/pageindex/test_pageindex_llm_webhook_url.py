"""PageIndex LLM webhook URL is /api/pageindex/interact/webhook/{agent_id}."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.pageindex.pageindex_action.pageindex_action import PageIndexAction
from jvagent.action.pageindex.webhook_auth import (
    ALLOWED_WEBHOOK_ENDPOINT_GLOB,
    PAGEINDEX_WEBHOOK_ROUTE_PREFIX,
    WEBHOOK_PERMISSION,
)

_MOD = "jvagent.action.pageindex.pageindex_action.pageindex_action"


def _mint_patches(*, generate_key):
    mock_service = MagicMock()
    mock_service.generate_key = generate_key
    mock_service.get_key = AsyncMock(return_value=None)
    mock_service.revoke_key = AsyncMock()
    return (
        patch.object(
            PageIndexAction,
            "get_agent",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="n.Agent.test123", name="TestAgent"),
        ),
        patch.object(PageIndexAction, "save", new_callable=AsyncMock),
        patch(f"{_MOD}.get_public_base_url", return_value="http://localhost:8000"),
        patch(
            f"{_MOD}.get_or_create_system_user",
            new_callable=AsyncMock,
            return_value="o.User.system123",
        ),
        patch(f"{_MOD}.APIKeyService", return_value=mock_service),
        patch(f"{_MOD}.get_prime_database", return_value=MagicMock()),
        patch(f"{_MOD}.GraphContext", return_value=MagicMock()),
    ), mock_service


def test_pageindex_webhook_path_constants():
    assert PAGEINDEX_WEBHOOK_ROUTE_PREFIX == "pageindex/interact/webhook"
    assert ALLOWED_WEBHOOK_ENDPOINT_GLOB == "/api/pageindex/interact/webhook/*"
    assert WEBHOOK_PERMISSION == "webhook:pageindex"


@pytest.mark.asyncio
async def test_pageindex_llm_webhook_mints_new_path():
    action = PageIndexAction.model_construct(
        webhook_url=None,
        webhook_api_key_id=None,
    )
    mock_key = SimpleNamespace(id="o.APIKey.key123")
    generate_key = AsyncMock(return_value=("test_mock_api_key", mock_key))
    patches, service = _mint_patches(generate_key=generate_key)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
    ):
        url = await action.get_webhook_url()
    assert url.startswith(
        "http://localhost:8000/api/pageindex/interact/webhook/n.Agent.test123"
    )
    assert "?api_key=test_mock_api_key" in url
    kwargs = generate_key.call_args.kwargs
    assert kwargs["allowed_endpoints"] == [ALLOWED_WEBHOOK_ENDPOINT_GLOB]
    assert kwargs["permissions"] == [WEBHOOK_PERMISSION]
    service.generate_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_pageindex_llm_webhook_remints_legacy_prefix():
    action = PageIndexAction.model_construct(
        webhook_url=(
            "http://localhost:8000/api/pageindex_retrieval_interact_action/"
            "interact/webhook/n.Agent.test123?api_key=old"
        ),
        webhook_api_key_id="o.APIKey.old",
    )
    mock_key = SimpleNamespace(id="o.APIKey.new")
    generate_key = AsyncMock(return_value=("new_key", mock_key))
    patches, service = _mint_patches(generate_key=generate_key)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
    ):
        url = await action.get_webhook_url()
    assert "/api/pageindex/interact/webhook/n.Agent.test123" in url
    assert "new_key" in url
    service.generate_key.assert_awaited_once()
    assert "pageindex_retrieval_interact_action" not in url
