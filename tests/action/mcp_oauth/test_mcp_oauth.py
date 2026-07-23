"""Tests for MCPOAuthAction token crypto + deregister path discovery."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.mcp_oauth.mcp_oauth_action import (
    MCPOAuthAction,
    _deserialize_token_payload,
    _serialize_token_payload,
)
from jvagent.action.oauth.token_crypto import CIPHER_PREFIX_V1


@pytest.fixture
def token_enc_key(monkeypatch):
    """Install a 32-byte AES key so encrypt_token_for_storage ciphers."""
    key = "a" * 32
    monkeypatch.setenv("JVAGENT_TOKEN_ENC_KEY", key)
    yield key


def test_serialize_strips_client_secret_and_encrypts(token_enc_key):
    blob = _serialize_token_payload(
        {
            "type": "authorized_user",
            "client_id": "cid",
            "client_secret": "sekrit",
            "refresh_token": "rtok",
        }
    )
    assert "sekrit" not in blob
    assert blob.startswith(CIPHER_PREFIX_V1)
    parsed = _deserialize_token_payload(blob)
    assert parsed is not None
    assert parsed["refresh_token"] == "rtok"
    assert parsed["client_id"] == "cid"
    assert "client_secret" not in parsed


def test_deserialize_legacy_plaintext_json():
    legacy = json.dumps(
        {
            "type": "authorized_user",
            "client_id": "cid",
            "client_secret": "legacy-secret",
            "refresh_token": "rtok",
        }
    )
    parsed = _deserialize_token_payload(legacy)
    assert parsed is not None
    assert parsed["refresh_token"] == "rtok"


def test_mcp_oauth_extra_prefix_discovered():
    """Deregister must match /mcp/... routes via additional_endpoint_path_prefixes."""
    action = MCPOAuthAction()
    object.__setattr__(action, "id", "n.Action.MCPOauth")
    object.__setattr__(action, "agent_id", "n.Agent.AGENT")

    assert "/mcp/" in MCPOAuthAction.additional_endpoint_path_prefixes

    fake_registry = SimpleNamespace(
        _function_registry={
            object(): SimpleNamespace(path="/mcp/google_workspace/auth"),
            object(): SimpleNamespace(path="/mcp/google_workspace/auth/callback"),
            object(): SimpleNamespace(path="/actions/other/foo"),
        }
    )
    server = SimpleNamespace(_endpoint_registry=fake_registry)
    with patch("jvspatial.api.context.get_current_server", return_value=server):
        funcs = action._discover_action_endpoints()
    assert len(funcs) == 2


@pytest.mark.asyncio
async def test_save_oauth_token_encrypts(monkeypatch, token_enc_key):
    action = MCPOAuthAction()
    saved: dict = {}

    class _Node:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        async def set_context(self, ctx):
            return None

        async def save(self):
            saved["token_json"] = self.token_json

    ctx = MagicMock()
    ctx.find_nodes = AsyncMock(return_value=[])

    monkeypatch.setattr(
        "jvagent.action.mcp_oauth.mcp_oauth_action._get_ctx",
        AsyncMock(return_value=ctx),
    )
    monkeypatch.setattr(
        "jvagent.action.mcp_oauth.mcp_oauth_action.MCPOAuthToken",
        _Node,
    )
    monkeypatch.setattr("jvagent.core.app.App.get", AsyncMock(return_value=None))

    await action.save_oauth_token(
        "google_workspace",
        "integral",
        {
            "type": "authorized_user",
            "client_id": "cid",
            "client_secret": "sekrit",
            "refresh_token": "rtok",
        },
    )
    assert saved["token_json"].startswith(CIPHER_PREFIX_V1)
    assert "sekrit" not in saved["token_json"]
    roundtrip = _deserialize_token_payload(saved["token_json"])
    assert roundtrip is not None
    assert roundtrip["refresh_token"] == "rtok"
    assert "client_secret" not in roundtrip
